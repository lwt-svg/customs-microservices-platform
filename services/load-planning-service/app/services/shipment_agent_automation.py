"""Shipment Agent 自动唤醒计划与站内通知投影。

本模块只管理运行控制面：

* ``next_wake_at`` 告诉后台 Worker 何时再次观察专业系统；
* 数据库短租约协调正常租期内的单实例领取，并拒绝失租实例的迟到控制面写入；
* 站内通知把当前业务状态投影成“谁需要关注什么”。

它不会修改 Shipment Agent 的业务阶段，也不会调用 LINK、装载算法或 LangGraph。真正的
业务判断仍由 :mod:`app.services.shipment_agent_runs` 和工作流负责，因此 Worker 重试
不会绕过人工审批，也不会因为轮询而制造新的业务 Trace。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.shipment_agent_run import (
    ShipmentAgentNotification,
    ShipmentAgentNotificationSeverity,
    ShipmentAgentNotificationStatus,
    ShipmentAgentNotificationType,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.repositories.shipment_agent_runs import ShipmentAgentRunRepository

_LOAD_WAIT_STAGES = {
    ShipmentAgentStage.AWAITING_LOAD_CONSTRAINTS,
    ShipmentAgentStage.AWAITING_LOAD_REVIEW,
    ShipmentAgentStage.AWAITING_LOAD_RELEASE,
    ShipmentAgentStage.AWAITING_LOAD_REPLAN,
}
_RESOURCE_BINDING_STAGES = {
    ShipmentAgentStage.AWAITING_RESOURCE_BINDING,
    ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT,
}

_OWNER_BY_STAGE: dict[ShipmentAgentStage, str] = {
    ShipmentAgentStage.AWAITING_SCENARIO_POLICY: "SHIPMENT_COORDINATOR",
    ShipmentAgentStage.AWAITING_RESOURCE_BINDING: "SHIPMENT_COORDINATOR",
    ShipmentAgentStage.AWAITING_RESOURCE_SELECTION: "SHIPMENT_COORDINATOR",
    ShipmentAgentStage.ASSESSING_DATA_READINESS: "SYSTEM",
    ShipmentAgentStage.AWAITING_DATA_REMEDIATION: "WAREHOUSE_OPERATOR",
    ShipmentAgentStage.AWAITING_CASE_ACTIVATION: "SHIPMENT_MANAGER",
    ShipmentAgentStage.AWAITING_LOAD_CONSTRAINTS: "LOAD_PLANNER",
    ShipmentAgentStage.AWAITING_LOAD_REVIEW: "LOAD_REVIEWER",
    ShipmentAgentStage.AWAITING_LOAD_RELEASE: "WAREHOUSE_SUPERVISOR",
    ShipmentAgentStage.AWAITING_LOAD_REPLAN: "LOAD_PLANNER",
    ShipmentAgentStage.AWAITING_BOOKING_SUBMIT_APPROVAL: "BOOKING_OPERATOR",
    ShipmentAgentStage.AWAITING_BOOKING_RESULT: "BOOKING_OPERATOR",
    ShipmentAgentStage.AWAITING_BOOKING_RETRY_APPROVAL: "BOOKING_OPERATOR",
    ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT: "BOOKING_OPERATOR",
    ShipmentAgentStage.AWAITING_COMPLETION_APPROVAL: "SHIPMENT_MANAGER",
    ShipmentAgentStage.FAILED: "SHIPMENT_COORDINATOR",
}


def _database_now(value: datetime | None = None) -> datetime:
    """把测试/Worker 的 aware 时间统一转为数据库 UTC naive DATETIME。"""

    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current
    return current.astimezone(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class ShipmentAgentWakeLease:
    """一次成功领取的运行租约。"""

    run_id: str
    lease_token: str


@dataclass(frozen=True, slots=True)
class _NotificationSpec:
    dedupe_key: str
    notification_type: ShipmentAgentNotificationType
    severity: ShipmentAgentNotificationSeverity
    title: str
    message: str
    owner_role: str


class ShipmentAgentAutomationService:
    """自动唤醒控制面与通知投影服务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        wait_seconds: float | None = None,
        resource_binding_seconds: float | None = None,
        lease_seconds: float | None = None,
        recovery_grace_seconds: float | None = None,
        retry_base_seconds: float | None = None,
        retry_max_seconds: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.wait_seconds = (
            wait_seconds if wait_seconds is not None else settings.shipment_agent_wake_wait_seconds
        )
        self.resource_binding_seconds = (
            resource_binding_seconds
            if resource_binding_seconds is not None
            else settings.shipment_agent_wake_resource_binding_seconds
        )
        self.lease_seconds = (
            lease_seconds
            if lease_seconds is not None
            else settings.shipment_agent_wake_lease_seconds
        )
        self.recovery_grace_seconds = (
            recovery_grace_seconds
            if recovery_grace_seconds is not None
            else settings.shipment_agent_wake_recovery_grace_seconds
        )
        self.retry_base_seconds = (
            retry_base_seconds
            if retry_base_seconds is not None
            else settings.shipment_agent_wake_retry_base_seconds
        )
        self.retry_max_seconds = (
            retry_max_seconds
            if retry_max_seconds is not None
            else settings.shipment_agent_wake_retry_max_seconds
        )

    async def project(
        self,
        run_id: str,
        *,
        lease_token: str | None = None,
        successful_wake: bool = False,
        now: datetime | None = None,
    ) -> bool:
        """根据最终业务状态刷新下一次唤醒和通知。

        返回 ``False`` 表示带租约调用已经失去所有权。迟到 Worker 不能清除新实例领取的
        租约，也不能覆盖新实例计算出的下一次执行时间。
        """

        now_database = _database_now(now)
        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            async with session.begin():
                run = await repository.get(run_id, for_update=True)
                if run is None:
                    return False
                if lease_token is not None:
                    lease_is_owned_and_live = (
                        run.wake_lease_token == lease_token
                        and run.wake_lease_until is not None
                        and run.wake_lease_until > now_database
                    )
                    if not lease_is_owned_and_live:
                        return False

                next_wake_at, wake_reason = self._next_wake(run, now_database)
                values: dict[str, object | None] = {
                    "next_wake_at": next_wake_at,
                    "wake_reason": wake_reason,
                    # 控制面写入不应改变业务列表的更新时间排序。
                    "updated_at": ShipmentAgentRun.updated_at,
                }

                lease_is_live = (
                    run.wake_lease_until is not None and run.wake_lease_until > now_database
                )
                if lease_token is not None:
                    values.update(
                        {
                            "wake_lease_token": None,
                            "wake_lease_until": None,
                            "wake_attempt_count": 0,
                            "wake_last_error": None,
                        }
                    )
                elif not lease_is_live:
                    # 普通 HTTP 命令成功推进后，可以安全清理过期租约和旧错误。
                    values.update(
                        {
                            "wake_lease_token": None,
                            "wake_lease_until": None,
                            "wake_attempt_count": 0,
                            "wake_last_error": None,
                        }
                    )

                await session.execute(
                    update(ShipmentAgentRun)
                    .where(ShipmentAgentRun.id == run.id)
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                self._sync_business_notification(
                    session,
                    run,
                    now_database,
                )
                if successful_wake or not lease_is_live:
                    self._resolve_operational_failure(run, now_database)
        return True

    async def claim_due(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> list[ShipmentAgentWakeLease]:
        """领取一批到期运行；多个 Worker 看到相同候选也只有一个能 UPDATE 成功。"""

        now_database = _database_now(now)
        stale_before = now_database - timedelta(seconds=self.recovery_grace_seconds)
        lease_until = now_database + timedelta(seconds=self.lease_seconds)
        claimed: list[ShipmentAgentWakeLease] = []

        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            async with session.begin():
                run_ids = await repository.list_due_wake_ids(
                    now=now_database,
                    stale_before=stale_before,
                    limit=limit,
                )
                for run_id in run_ids:
                    token = str(uuid4())
                    if await repository.claim_wake(
                        run_id,
                        lease_token=token,
                        now=now_database,
                        stale_before=stale_before,
                        lease_until=lease_until,
                    ):
                        claimed.append(
                            ShipmentAgentWakeLease(
                                run_id=run_id,
                                lease_token=token,
                            )
                        )
        return claimed

    async def renew_lease(
        self,
        lease: ShipmentAgentWakeLease,
        *,
        now: datetime | None = None,
    ) -> bool:
        """延长一条仍有效的租约；返回 ``False`` 表示 Worker 已失去所有权。"""

        now_database = _database_now(now)
        lease_until = now_database + timedelta(seconds=self.lease_seconds)
        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            async with session.begin():
                return await repository.renew_wake_lease(
                    lease.run_id,
                    lease_token=lease.lease_token,
                    now=now_database,
                    lease_until=lease_until,
                )

    async def mark_wake_failed(
        self,
        lease: ShipmentAgentWakeLease,
        error: Exception,
        *,
        now: datetime | None = None,
    ) -> bool:
        """释放失败租约、指数退避，并生成一条去重的运维通知。"""

        now_database = _database_now(now)
        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            async with session.begin():
                run = await repository.get(lease.run_id, for_update=True)
                if (
                    run is None
                    or run.wake_lease_token != lease.lease_token
                    or run.wake_lease_until is None
                    or run.wake_lease_until <= now_database
                ):
                    return False

                # 业务步骤可能已经成功提交，只是在通知投影前抛错。若当前状态已是人工节点
                # 或终态，就不能再伪造一条永远不会执行的自动重试计划；这里直接完成投影
                # 修复、清理控制面，并把先前的运维故障通知关闭。
                retry_wake_at, _ = self._next_wake(run, now_database)
                if retry_wake_at is None:
                    await session.execute(
                        update(ShipmentAgentRun)
                        .where(
                            ShipmentAgentRun.id == run.id,
                            ShipmentAgentRun.wake_lease_token == lease.lease_token,
                        )
                        .values(
                            next_wake_at=None,
                            wake_reason=None,
                            wake_lease_token=None,
                            wake_lease_until=None,
                            wake_attempt_count=0,
                            wake_last_error=None,
                            updated_at=ShipmentAgentRun.updated_at,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    self._sync_business_notification(
                        session,
                        run,
                        now_database,
                    )
                    self._resolve_operational_failure(run, now_database)
                    return True

                attempt = max(run.wake_attempt_count, 1)
                multiplier = 2 ** min(attempt - 1, 20)
                retry_seconds = min(
                    self.retry_base_seconds * multiplier,
                    self.retry_max_seconds,
                )
                next_wake_at = now_database + timedelta(seconds=retry_seconds)
                error_code = type(error).__name__[:128]
                await session.execute(
                    update(ShipmentAgentRun)
                    .where(
                        ShipmentAgentRun.id == run.id,
                        ShipmentAgentRun.wake_lease_token == lease.lease_token,
                    )
                    .values(
                        next_wake_at=next_wake_at,
                        wake_reason="RETRY_AFTER_AUTO_WAKE_FAILURE",
                        wake_lease_token=None,
                        wake_lease_until=None,
                        wake_last_error=error_code,
                        updated_at=ShipmentAgentRun.updated_at,
                    )
                    .execution_options(synchronize_session=False)
                )
                self._upsert_operational_failure(
                    session,
                    run,
                    now_database,
                    attempt=attempt,
                    retry_seconds=retry_seconds,
                    error_code=error_code,
                )
        return True

    def _next_wake(
        self,
        run: ShipmentAgentRun,
        now_database: datetime,
    ) -> tuple[datetime | None, str | None]:
        if run.status is ShipmentAgentRunStatus.RUNNING:
            return (
                now_database + timedelta(seconds=self.recovery_grace_seconds),
                "RECOVER_RUNNING_STEP",
            )
        if run.status is ShipmentAgentRunStatus.WAITING_EXTERNAL:
            return (
                now_database + timedelta(seconds=self.wait_seconds),
                "POLL_LINK_STATUS",
            )
        if run.status is ShipmentAgentRunStatus.WAITING_RESOURCE:
            if run.current_stage in _LOAD_WAIT_STAGES:
                return (
                    now_database + timedelta(seconds=self.wait_seconds),
                    "POLL_LOAD_STATUS",
                )
            if run.current_stage in _RESOURCE_BINDING_STAGES:
                return (
                    now_database + timedelta(seconds=self.resource_binding_seconds),
                    "POLL_RESOURCE_BINDING",
                )
        return None, None

    @staticmethod
    def _notification_spec(run: ShipmentAgentRun) -> _NotificationSpec | None:
        owner_role = _OWNER_BY_STAGE.get(
            run.current_stage,
            "SHIPMENT_COORDINATOR",
        )
        dedupe_key = f"BUSINESS:{run.current_stage.value}"

        if run.status is ShipmentAgentRunStatus.FAILED:
            return _NotificationSpec(
                dedupe_key=dedupe_key,
                notification_type=ShipmentAgentNotificationType.RUN_FAILED,
                severity=ShipmentAgentNotificationSeverity.ERROR,
                title="Shipment Agent 运行失败",
                message=run.failure_message or "自动处理已经停止，请查看运行轨迹。",
                owner_role=owner_role,
            )

        if run.status is ShipmentAgentRunStatus.WAITING_HUMAN:
            pending = run.pending_interrupt or {}
            return _NotificationSpec(
                dedupe_key=dedupe_key,
                notification_type=ShipmentAgentNotificationType.ACTION_REQUIRED,
                severity=ShipmentAgentNotificationSeverity.WARNING,
                title=str(pending.get("title") or "案件需要人工处理"),
                message=str(pending.get("description") or "请进入案件工作台处理当前人工节点。"),
                owner_role=owner_role,
            )

        if run.status is ShipmentAgentRunStatus.WAITING_RESOURCE:
            child = run.child_agent_snapshot or {}
            message = str(
                child.get("next_action")
                or run.failure_message
                or "请完成当前专业作业；系统会自动检查最新结果。"
            )
            return _NotificationSpec(
                dedupe_key=dedupe_key,
                notification_type=(ShipmentAgentNotificationType.RESOURCE_ATTENTION),
                severity=ShipmentAgentNotificationSeverity.WARNING,
                title="专业作业等待处理",
                message=message,
                owner_role=owner_role,
            )

        if run.status is ShipmentAgentRunStatus.WAITING_EXTERNAL:
            return _NotificationSpec(
                dedupe_key=dedupe_key,
                notification_type=ShipmentAgentNotificationType.EXTERNAL_WAIT,
                severity=ShipmentAgentNotificationSeverity.INFO,
                title="等待承运人异步结果",
                message=("系统会按计划自动检查 LINK 状态；请勿重复提交同一订舱申请。"),
                owner_role=owner_role,
            )
        return None

    def _sync_business_notification(
        self,
        session: AsyncSession,
        run: ShipmentAgentRun,
        now_database: datetime,
    ) -> None:
        spec = self._notification_spec(run)
        current_key = spec.dedupe_key if spec is not None else None

        for notification in run.notifications:
            if (
                notification.dedupe_key.startswith("BUSINESS:")
                and notification.status is ShipmentAgentNotificationStatus.OPEN
                and notification.dedupe_key != current_key
            ):
                notification.status = ShipmentAgentNotificationStatus.RESOLVED
                notification.resolved_at = now_database

        if spec is None:
            return

        existing = next(
            (
                notification
                for notification in run.notifications
                if notification.dedupe_key == spec.dedupe_key
            ),
            None,
        )
        last_sequence = run.events[-1].sequence if run.events else None
        action_context = {
            "run_id": run.id,
            "case_number": run.shipment_case.case_number,
            "stage": run.current_stage.value,
        }
        if existing is None:
            notification = ShipmentAgentNotification(
                id=str(uuid4()),
                run_id=run.id,
                source_event_sequence=last_sequence,
                dedupe_key=spec.dedupe_key,
                notification_type=spec.notification_type,
                severity=spec.severity,
                status=ShipmentAgentNotificationStatus.OPEN,
                title=spec.title,
                message=spec.message,
                owner_role=spec.owner_role,
                action_context=action_context,
                occurrence_count=1,
                last_seen_at=now_database,
                resolved_at=None,
            )
            session.add(notification)
            run.notifications.append(notification)
            return

        reopened = existing.status is ShipmentAgentNotificationStatus.RESOLVED
        changed = (
            existing.title != spec.title
            or existing.message != spec.message
            or existing.owner_role != spec.owner_role
            or existing.severity is not spec.severity
            or existing.notification_type is not spec.notification_type
        )
        if reopened:
            existing.occurrence_count += 1
        if reopened or changed:
            existing.source_event_sequence = last_sequence
            existing.notification_type = spec.notification_type
            existing.severity = spec.severity
            existing.status = ShipmentAgentNotificationStatus.OPEN
            existing.title = spec.title
            existing.message = spec.message
            existing.owner_role = spec.owner_role
            existing.action_context = action_context
            existing.last_seen_at = now_database
            existing.resolved_at = None

    @staticmethod
    def _resolve_operational_failure(
        run: ShipmentAgentRun,
        now_database: datetime,
    ) -> None:
        for notification in run.notifications:
            if (
                notification.dedupe_key == "OPERATIONS:AUTO_WAKE_FAILED"
                and notification.status is ShipmentAgentNotificationStatus.OPEN
            ):
                notification.status = ShipmentAgentNotificationStatus.RESOLVED
                notification.resolved_at = now_database

    @staticmethod
    def _upsert_operational_failure(
        session: AsyncSession,
        run: ShipmentAgentRun,
        now_database: datetime,
        *,
        attempt: int,
        retry_seconds: float,
        error_code: str,
    ) -> None:
        dedupe_key = "OPERATIONS:AUTO_WAKE_FAILED"
        existing = next(
            (
                notification
                for notification in run.notifications
                if notification.dedupe_key == dedupe_key
            ),
            None,
        )
        severity = (
            ShipmentAgentNotificationSeverity.ERROR
            if attempt >= 3
            else ShipmentAgentNotificationSeverity.WARNING
        )
        title = "自动跟踪暂时失败"
        message = (
            f"后台检查未完成（{error_code}），系统将在约 "
            f"{retry_seconds:g} 秒后自动重试。业务人员无需重复提交订单。"
        )
        action_context = {
            "run_id": run.id,
            "case_number": run.shipment_case.case_number,
            "stage": run.current_stage.value,
            "attempt": attempt,
        }
        if existing is None:
            notification = ShipmentAgentNotification(
                id=str(uuid4()),
                run_id=run.id,
                source_event_sequence=(run.events[-1].sequence if run.events else None),
                dedupe_key=dedupe_key,
                notification_type=ShipmentAgentNotificationType.AUTO_WAKE_FAILED,
                severity=severity,
                status=ShipmentAgentNotificationStatus.OPEN,
                title=title,
                message=message,
                owner_role="PLATFORM_OPERATOR",
                action_context=action_context,
                occurrence_count=1,
                last_seen_at=now_database,
                resolved_at=None,
            )
            session.add(notification)
            run.notifications.append(notification)
            return

        existing.notification_type = ShipmentAgentNotificationType.AUTO_WAKE_FAILED
        existing.severity = severity
        existing.status = ShipmentAgentNotificationStatus.OPEN
        existing.title = title
        existing.message = message
        existing.owner_role = "PLATFORM_OPERATOR"
        existing.action_context = action_context
        existing.occurrence_count += 1
        existing.last_seen_at = now_database
        existing.resolved_at = None
