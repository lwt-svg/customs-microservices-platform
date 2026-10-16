"""Shipment Coordinator Agent 的 SQLAlchemy 仓储。

仓储只处理查询、加载策略和数据库 flush。跨服务调用、状态机判断和事务分段全部留在
Service：尤其不能在持有 ``SELECT ... FOR UPDATE`` 的事务中等待 LINK HTTP。
"""

from datetime import datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.shipment_agent_run import (
    SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON,
    ShipmentAgentCommand,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.models.shipment_case import ShipmentCase

_TERMINAL_STATUSES = {
    ShipmentAgentRunStatus.COMPLETED,
    ShipmentAgentRunStatus.FAILED,
    ShipmentAgentRunStatus.CANCELLED,
}
_AUTO_WAKE_RESOURCE_STAGES = {
    ShipmentAgentStage.AWAITING_RESOURCE_BINDING,
    ShipmentAgentStage.AWAITING_LOAD_CONSTRAINTS,
    ShipmentAgentStage.AWAITING_LOAD_REVIEW,
    ShipmentAgentStage.AWAITING_LOAD_RELEASE,
    ShipmentAgentStage.AWAITING_LOAD_REPLAN,
    ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT,
}
_CONTROL_PROJECTION_RECOVERY_STATUSES = {
    ShipmentAgentRunStatus.WAITING_HUMAN,
    ShipmentAgentRunStatus.WAITING_RESOURCE,
    ShipmentAgentRunStatus.WAITING_EXTERNAL,
    ShipmentAgentRunStatus.COMPLETED,
    ShipmentAgentRunStatus.FAILED,
    ShipmentAgentRunStatus.CANCELLED,
}


class ShipmentAgentRunRepository:
    """总 Agent 运行、命令和案件上下文的持久化入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _run_options():
        return (
            selectinload(ShipmentAgentRun.shipment_case).selectinload(
                ShipmentCase.resource_bindings
            ),
            selectinload(ShipmentAgentRun.events),
            selectinload(ShipmentAgentRun.notifications),
        )

    async def get(
        self,
        run_id: str,
        *,
        for_update: bool = False,
    ) -> ShipmentAgentRun | None:
        """读取运行、案件资源和完整 Trace；写命令可选择锁住运行主行。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(ShipmentAgentRun.id == run_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_case(
        self,
        case_number: str,
        *,
        for_update: bool = False,
    ) -> ShipmentCase | None:
        """按业务编号读取案件及资源绑定。"""

        statement = (
            select(ShipmentCase)
            .options(
                selectinload(ShipmentCase.resource_bindings),
                selectinload(ShipmentCase.events),
            )
            .where(ShipmentCase.case_number == case_number)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_active_for_case(
        self,
        case_id: str,
        *,
        for_update: bool = False,
    ) -> ShipmentAgentRun | None:
        """返回案件最近一条非终态运行。

        第一版每个案件只允许一个未完成总 Agent。数据库层保留历史运行；Service 在案件
        行锁下检查本查询，避免两个启动请求正常路径下同时创建两条运行。
        """

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(
                ShipmentAgentRun.case_id == case_id,
                ShipmentAgentRun.status.not_in(_TERMINAL_STATUSES),
            )
            .order_by(
                ShipmentAgentRun.updated_at.desc(),
                ShipmentAgentRun.created_at.desc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_for_case(self, case_id: str) -> list[ShipmentAgentRun]:
        """按更新时间倒序返回一票案件的全部运行历史。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(ShipmentAgentRun.case_id == case_id)
            .order_by(
                ShipmentAgentRun.updated_at.desc(),
                ShipmentAgentRun.created_at.desc(),
                ShipmentAgentRun.id.desc(),
            )
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def list_all(self) -> list[ShipmentAgentRun]:
        """Agent Center 使用的全局列表；一次查询避免按案件 N+1。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .order_by(
                ShipmentAgentRun.updated_at.desc(),
                ShipmentAgentRun.created_at.desc(),
                ShipmentAgentRun.id.desc(),
            )
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def get_by_start_key(
        self,
        idempotency_key_hash: str,
    ) -> ShipmentAgentRun | None:
        """恢复并发或网络重试创建的运行。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(ShipmentAgentRun.start_idempotency_key_hash == idempotency_key_hash)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_command(
        self,
        idempotency_key_hash: str,
    ) -> ShipmentAgentCommand | None:
        """按全局幂等键摘要读取成功命令。"""

        return await self.session.scalar(
            select(ShipmentAgentCommand).where(
                ShipmentAgentCommand.idempotency_key_hash == idempotency_key_hash
            )
        )

    @staticmethod
    def _wake_eligible_clause():
        """返回允许后台只读检查的运行范围。

        ``WAITING_HUMAN`` 永远不在这里：自动唤醒只能观察装载、资源绑定和外部系统，
        不能绕过人工审批。``WAITING_RESOURCE`` 也只开放明确可观察的几个专业阶段。
        """

        return or_(
            ShipmentAgentRun.status == ShipmentAgentRunStatus.RUNNING,
            ShipmentAgentRun.status == ShipmentAgentRunStatus.WAITING_EXTERNAL,
            and_(
                ShipmentAgentRun.status == ShipmentAgentRunStatus.WAITING_RESOURCE,
                ShipmentAgentRun.current_stage.in_(_AUTO_WAKE_RESOURCE_STAGES),
            ),
        )

    @classmethod
    def _expired_projection_lease_clause(cls, now: datetime):
        """返回“业务已推进、控制面投影未完成”的过期租约。

        Worker 可能已经把运行推进到 ``WAITING_HUMAN`` 或终态，却在刷新通知、清理租约
        前崩溃。此时当前业务状态不再属于普通自动唤醒范围，但旧租约本身就是一条明确的
        “投影可能未完成”恢复线索，因此必须允许另一个 Worker 在租约过期后接管。
        """

        return and_(
            ShipmentAgentRun.wake_lease_token.is_not(None),
            ShipmentAgentRun.wake_lease_until.is_not(None),
            ShipmentAgentRun.wake_lease_until <= now,
        )

    @staticmethod
    def _due_control_projection_repair_clause(now: datetime):
        """返回无租约 HTTP 崩溃留下的、已到期控制面修复标记。

        跨过 ``WAITING_HUMAN`` 和终态的领取权限必须由精确的 reason 显式授予，不能只凭
        ``updated_at`` 或 NULL 唤醒时间猜测，否则 Worker 可能误把人工节点当成可自动
        推进节点。空闲租约要求 token/到期时间同时为空；已过期租约则要求到期时间明确
        小于当前时间，避免覆盖另一个仍持有有效租约的 Worker。
        """

        lease_is_free_or_expired = or_(
            and_(
                ShipmentAgentRun.wake_lease_token.is_(None),
                ShipmentAgentRun.wake_lease_until.is_(None),
            ),
            and_(
                ShipmentAgentRun.wake_lease_until.is_not(None),
                ShipmentAgentRun.wake_lease_until <= now,
            ),
        )
        return and_(
            ShipmentAgentRun.status.in_(_CONTROL_PROJECTION_RECOVERY_STATUSES),
            ShipmentAgentRun.wake_reason
            == SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON,
            ShipmentAgentRun.next_wake_at.is_not(None),
            ShipmentAgentRun.next_wake_at <= now,
            lease_is_free_or_expired,
        )

    @classmethod
    def _wake_claimable_clause(
        cls,
        *,
        now: datetime,
        stale_before: datetime,
    ):
        """组合正常到期唤醒与崩溃后投影修复两种领取条件。"""

        normally_due = and_(
            cls._wake_eligible_clause(),
            or_(
                ShipmentAgentRun.wake_lease_until.is_(None),
                ShipmentAgentRun.wake_lease_until <= now,
            ),
            or_(
                ShipmentAgentRun.next_wake_at <= now,
                and_(
                    ShipmentAgentRun.next_wake_at.is_(None),
                    ShipmentAgentRun.updated_at <= stale_before,
                ),
            ),
        )
        return or_(
            normally_due,
            cls._expired_projection_lease_clause(now),
            cls._due_control_projection_repair_clause(now),
        )

    async def list_due_wake_ids(
        self,
        *,
        now: datetime,
        stale_before: datetime,
        limit: int,
    ) -> list[str]:
        """读取可能到期的运行编号；真正排他性由后续条件 UPDATE 保证。

        NULL ``next_wake_at`` 仅在记录已经超过恢复宽限期时视为到期。这能修复“业务事务
        已提交、进程却在写入唤醒投影前崩溃”的运行，同时避免抢走仍在请求内推进的新运行。
        """

        statement = (
            select(ShipmentAgentRun.id)
            .where(
                self._wake_claimable_clause(
                    now=now,
                    stale_before=stale_before,
                ),
            )
            .order_by(
                ShipmentAgentRun.next_wake_at.asc(),
                ShipmentAgentRun.updated_at.asc(),
                ShipmentAgentRun.id.asc(),
            )
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def claim_wake(
        self,
        run_id: str,
        *,
        lease_token: str,
        now: datetime,
        stale_before: datetime,
        lease_until: datetime,
    ) -> bool:
        """用单条条件 UPDATE 领取租约，避免正常租期内多个实例同时取得调度权。"""

        result = await self.session.execute(
            update(ShipmentAgentRun)
            .where(
                ShipmentAgentRun.id == run_id,
                self._wake_claimable_clause(
                    now=now,
                    stale_before=stale_before,
                ),
            )
            .values(
                wake_lease_token=lease_token,
                wake_lease_until=lease_until,
                last_wake_at=now,
                next_wake_at=None,
                wake_attempt_count=ShipmentAgentRun.wake_attempt_count + 1,
                # 唤醒控制面更新不能把业务“最近更新时间”刷新成每几秒一次。
                updated_at=ShipmentAgentRun.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def renew_wake_lease(
        self,
        run_id: str,
        *,
        lease_token: str,
        now: datetime,
        lease_until: datetime,
    ) -> bool:
        """只为仍由当前 token 持有、且尚未过期的租约续期。

        过期租约可能已经被另一个实例接管。续租条件必须同时校验 token 和原到期时间，
        不能让迟到 Worker 把已失去的所有权“续回来”。
        """

        result = await self.session.execute(
            update(ShipmentAgentRun)
            .where(
                ShipmentAgentRun.id == run_id,
                ShipmentAgentRun.wake_lease_token == lease_token,
                ShipmentAgentRun.wake_lease_until.is_not(None),
                ShipmentAgentRun.wake_lease_until > now,
            )
            .values(
                wake_lease_until=lease_until,
                updated_at=ShipmentAgentRun.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    def add(self, value: object) -> None:
        self.session.add(value)

    async def flush(self) -> None:
        await self.session.flush()
