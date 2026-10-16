"""Shipment Coordinator Agent 的领域服务。

本服务把一票 Shipment Case 串成一个可恢复闭环，但不把两个下游模块重写一遍：

* 装载规划仍由 ``StatefulPalletAgentService``、正式版本服务和其 LangGraph 负责；
* LINK 申请及承运人投递状态仍由 LINK 服务负责；
* 本服务只决定下一项能力、人工审批和异常恢复动作。

所有远程 HTTP、装载子 Agent 调用和 Shipment Case 状态命令都在总 Agent 数据库事务之外
执行。写入外部意图前先把人工命令和阶段落库；若响应丢失，后续只重新读取源系统事实，
不会因为 Graph 节点重放而盲目重复提交订单。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.integrations.shipment_agent_resources import (
    LinkBookingGateway,
    LinkBookingNotFoundError,
    LinkBookingProtocolError,
    LinkBookingUnavailableError,
    LinkCommandConflictError,
    LinkCommandResultUnknownError,
)
from app.integrations.shipment_resources import ShipmentResourceVerifier
from app.models.agent_run import AgentRunStatus
from app.models.shipment_agent_run import (
    SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON,
    ShipmentAgentAction,
    ShipmentAgentCapability,
    ShipmentAgentCommand,
    ShipmentAgentCommandResult,
    ShipmentAgentCommandType,
    ShipmentAgentEvent,
    ShipmentAgentInterruptType,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.models.shipment_case import (
    ShipmentCaseStatus,
    ShipmentResourceType,
    ShipmentTransportScenario,
)
from app.models.shipment_data_readiness import (
    ShipmentCaseDataReadiness,
    ShipmentDataFactSourceType,
    ShipmentDataReadinessStatus,
    ShipmentDataSourceMode,
)
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.plan_versions import LoadPlanVersionRepository
from app.repositories.shipment_agent_runs import ShipmentAgentRunRepository
from app.repositories.shipment_data_readiness import (
    ShipmentDataReadinessRepository,
)
from app.schemas.agent_run import AgentRunCreate
from app.schemas.shipment_agent_run import (
    ShipmentAgentChildRead,
    ShipmentAgentEventRead,
    ShipmentAgentInterruptRead,
    ShipmentAgentNotificationRead,
    ShipmentAgentReconcile,
    ShipmentAgentResume,
    ShipmentAgentRunRead,
    ShipmentAgentStart,
)
from app.schemas.shipment_case import ShipmentCaseStatusChange
from app.schemas.shipment_data_readiness import (
    ShipmentDataReadinessRead,
    ShipmentDataRequestedFactRead,
)
from app.services.agent_runs import StatefulPalletAgentService
from app.services.plan_versions import LoadPlanVersionService
from app.services.shipment_cases import ShipmentCaseService
from app.services.shipment_data_readiness import (
    SHIPMENT_DATA_READINESS_POLICY_VERSION,
    assess_shipment_data_readiness,
)
from app.workflows.load_planning import LoadPlanningWorkflowOrchestrator

SHIPMENT_AGENT_WORKFLOW_VERSION = "SHIPMENT_COORDINATOR_LANGGRAPH_V2"
SYSTEM_DEMO_ACTOR_SUBJECT = "SYSTEM_DEMO"
SYSTEM_DEMO_ACTOR_SOURCE = "SHIPMENT_COORDINATOR_AGENT"

_TERMINAL_STATUSES = {
    ShipmentAgentRunStatus.COMPLETED,
    ShipmentAgentRunStatus.FAILED,
    ShipmentAgentRunStatus.CANCELLED,
}
# 这些阶段全部由 LangGraph 自动节点处理，并且每个可能产生外部副作用的节点都先检查
# 数据库/下游权威状态和固定幂等键。若人工命令已经提交、HTTP 进程却在 Graph 续跑前
# 中断，RECONCILE 可以只登记一次 NO_OP kick，再从当前阶段恢复，而不是把阶段改写或
# 重放一条新的人工动作。
_AUTOMATICALLY_ROUTABLE_RUNNING_STAGES = {
    ShipmentAgentStage.SYNCING_CASE,
    ShipmentAgentStage.ASSESSING_DATA_READINESS,
    ShipmentAgentStage.STARTING_LOAD_AGENT,
    ShipmentAgentStage.AWAITING_BOOKING_RESULT,
    ShipmentAgentStage.AWAITING_CASE_ACTIVATION,
    ShipmentAgentStage.SUBMITTING_BOOKING,
    ShipmentAgentStage.AWAITING_BOOKING_RETRY_APPROVAL,
    ShipmentAgentStage.COMPLETING_CASE,
}
_HUMAN_SUPPLY_DATA_SOURCE_TYPES = {
    ShipmentDataFactSourceType.MANUAL_CONFIRMATION,
    ShipmentDataFactSourceType.CSV_IMPORT,
    ShipmentDataFactSourceType.SYNTHETIC_DEMO,
}
_SUPPORTED_SCENARIOS: dict[
    ShipmentTransportScenario,
    list[ShipmentAgentCapability],
] = {
    ShipmentTransportScenario.SEA_FCL: [
        ShipmentAgentCapability.LOAD_PLANNING,
        ShipmentAgentCapability.LINK_BOOKING,
    ],
    ShipmentTransportScenario.LOAD_PLANNING_ONLY: [ShipmentAgentCapability.LOAD_PLANNING],
    ShipmentTransportScenario.BOOKING_ONLY: [ShipmentAgentCapability.LINK_BOOKING],
}


class ShipmentAgentRunNotFoundError(Exception):
    """总 Agent 运行不存在。"""


class ShipmentAgentCaseNotFoundError(Exception):
    """Shipment Case 不存在。"""


class ShipmentAgentConflictError(Exception):
    """幂等键、运行版本、人工动作或案件状态发生冲突。"""


class ShipmentAgentValidationError(Exception):
    """JSON 类型有效，但不满足当前 interrupt 的业务合同。"""


class ShipmentAgentTransientDependencyError(Exception):
    """自动唤醒只读检查遇到的暂时性依赖故障。

    人工请求首次遇到 LINK 故障时仍会把运行投影为 ``WAITING_EXTERNAL``；后台自动检查
    再遇到同类故障则抛出本异常，由 Worker 做指数退避，避免固定频率持续打满依赖服务。
    """


def _canonical_hash(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ShipmentAgentValidationError("Idempotency-Key 不能为空")
    if len(normalized) > 128 or any(ord(character) < 32 for character in normalized):
        raise ShipmentAgentValidationError("Idempotency-Key 必须是 1 到 128 个可见字符")
    return normalized


def _idempotency_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now_for_database() -> datetime:
    """项目 MySQL/SQLite 的 DATETIME 统一保存 UTC naive 值。"""

    return datetime.now(UTC).replace(tzinfo=None)


class ShipmentAgentService:
    """总 Agent 的业务事实、人工命令和跨能力协调服务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        link_gateway: LinkBookingGateway,
        checkpointer: BaseCheckpointSaver[Any],
        *,
        running_recovery_seconds: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.link_gateway = link_gateway
        self.checkpointer = checkpointer
        self.running_recovery_seconds = (
            running_recovery_seconds
            if running_recovery_seconds is not None
            else settings.shipment_agent_wake_recovery_grace_seconds
        )
        # ShipmentCaseService 的状态变更路径不会调用资源验证器。这里仍显式传入空派发器，
        # 复用案件既有的幂等、revision 和审计实现，而不是绕过它直接 UPDATE 主表。
        self.case_service = ShipmentCaseService(
            session_factory,
            ShipmentResourceVerifier([]),
        )

    async def start(
        self,
        case_number: str,
        payload: ShipmentAgentStart,
        idempotency_key: str,
    ) -> ShipmentAgentRunRead:
        """为案件创建唯一未完成运行；Graph 稍后从 SYNCING_CASE 推进。"""

        normalized_key = _normalize_idempotency_key(idempotency_key)
        key_hash = _idempotency_hash(normalized_key)
        request_hash = _canonical_hash(
            {
                "action": "START",
                "case_number": case_number,
                "expected_case_revision": payload.expected_case_revision,
            }
        )
        run_id = str(uuid4())
        request_id = str(uuid4())

        try:
            async with self.session_factory() as session:
                repository = ShipmentAgentRunRepository(session)
                async with session.begin():
                    existing_command = await repository.get_command(key_hash)
                    if existing_command is not None:
                        self._ensure_same_command(
                            existing_command,
                            ShipmentAgentCommandType.START,
                            request_hash,
                        )
                        existing = await repository.get(existing_command.run_id)
                        if existing is None:  # pragma: no cover - FK 应保证存在
                            raise RuntimeError("幂等命令对应的 Shipment Agent 运行不存在")
                        if existing.shipment_case.case_number != case_number:
                            raise ShipmentAgentConflictError("Idempotency-Key 已用于另一案件")
                        return self._to_read(existing)

                    shipment_case = await repository.get_case(
                        case_number,
                        for_update=True,
                    )
                    if shipment_case is None:
                        raise ShipmentAgentCaseNotFoundError("发运案件不存在")
                    if shipment_case.revision != payload.expected_case_revision:
                        raise ShipmentAgentConflictError(
                            f"案件修订号已变化，当前为 {shipment_case.revision}，请刷新后重新启动"
                        )
                    if shipment_case.status in {
                        ShipmentCaseStatus.COMPLETED,
                        ShipmentCaseStatus.CANCELLED,
                    }:
                        raise ShipmentAgentConflictError(
                            f"{shipment_case.status.value} 案件不能启动新的总 Agent"
                        )
                    active = await repository.get_active_for_case(
                        shipment_case.id,
                        for_update=True,
                    )
                    if active is not None:
                        raise ShipmentAgentConflictError(
                            f"案件已有未完成运行 {active.id}，请先继续该运行"
                        )

                    capabilities = _SUPPORTED_SCENARIOS.get(shipment_case.transport_scenario)
                    run = ShipmentAgentRun(
                        id=run_id,
                        case_id=shipment_case.id,
                        status=ShipmentAgentRunStatus.RUNNING,
                        current_stage=ShipmentAgentStage.SYNCING_CASE,
                        run_version=1,
                        workflow_version=SHIPMENT_AGENT_WORKFLOW_VERSION,
                        started_case_revision=shipment_case.revision,
                        required_capabilities=[
                            capability.value for capability in (capabilities or [])
                        ],
                        scenario_policy_confirmed=capabilities is not None,
                        selected_load_task_number=None,
                        selected_load_run_id=None,
                        locked_load_version_id=None,
                        locked_load_version_number=None,
                        selected_booking_application_number=None,
                        booking_status=None,
                        carrier_submission_status=None,
                        booking_reference=None,
                        booking_submission_uncertain=False,
                        pending_interrupt=None,
                        allowed_actions=[],
                        child_agent_snapshot=None,
                        data_readiness_status=(ShipmentDataReadinessStatus.NOT_EVALUATED),
                        data_readiness_revision=0,
                        data_readiness_snapshot=(self._empty_data_readiness_snapshot()),
                        data_readiness_checked_at=None,
                        start_idempotency_key_hash=key_hash,
                        start_request_hash=request_hash,
                    )
                    self._refresh_automatic_wake_marker(run)
                    run.events.append(
                        self._event(
                            run,
                            request_id=request_id,
                            event_type="RUN_STARTED",
                            title="Shipment Agent 已启动",
                            detail=(
                                f"开始协调案件 {case_number}；运输场景为 "
                                f"{shipment_case.transport_scenario.value}。"
                            ),
                            data={
                                "case_number": case_number,
                                "transport_scenario": (shipment_case.transport_scenario.value),
                            },
                        )
                    )
                    repository.add(run)
                    await repository.flush()
                    repository.add(
                        ShipmentAgentCommand(
                            run_id=run.id,
                            command_type=ShipmentAgentCommandType.START,
                            idempotency_key_hash=key_hash,
                            request_hash=request_hash,
                            request_id=request_id,
                            result_kind=ShipmentAgentCommandResult.APPLIED,
                            result_run_version=run.run_version,
                        )
                    )
                    await repository.flush()
        except IntegrityError:
            return await self._recover_start(
                case_number,
                key_hash,
                request_hash,
            )

        return await self.get(run_id)

    async def get(self, run_id: str) -> ShipmentAgentRunRead:
        async with self.session_factory() as session:
            run = await ShipmentAgentRunRepository(session).get(run_id)
            if run is None:
                raise ShipmentAgentRunNotFoundError("Shipment Agent 运行不存在")
            return self._to_read(run)

    async def list_all(self) -> list[ShipmentAgentRunRead]:
        """一次查询返回 Agent Center 所需全部运行，避免按案件 N+1。"""

        async with self.session_factory() as session:
            runs = await ShipmentAgentRunRepository(session).list_all()
            return [self._to_read(run) for run in runs]

    async def active(self, case_number: str) -> ShipmentAgentRunRead | None:
        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            shipment_case = await repository.get_case(case_number)
            if shipment_case is None:
                raise ShipmentAgentCaseNotFoundError("发运案件不存在")
            run = await repository.get_active_for_case(shipment_case.id)
            return self._to_read(run) if run is not None else None

    async def history(self, case_number: str) -> list[ShipmentAgentRunRead]:
        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            shipment_case = await repository.get_case(case_number)
            if shipment_case is None:
                raise ShipmentAgentCaseNotFoundError("发运案件不存在")
            runs = await repository.list_for_case(shipment_case.id)
            return [self._to_read(run) for run in runs]

    async def resume(
        self,
        run_id: str,
        payload: ShipmentAgentResume,
        idempotency_key: str,
    ) -> ShipmentAgentRunRead:
        """原子登记一次人工命令，再由 LangGraph 执行相应阶段。

        对 LINK 提交/重试和案件状态命令，本方法只落库“人工已批准”的意图并切换阶段；
        真正外部调用在事务提交后由独立 Graph 节点完成。
        """

        normalized_key = _normalize_idempotency_key(idempotency_key)
        key_hash = _idempotency_hash(normalized_key)
        request_hash = _canonical_hash(
            {
                "action": payload.action.value,
                "expected_run_version": payload.expected_run_version,
                "comment": payload.comment,
                "interrupt_type": payload.interrupt_type.value,
                "required_capabilities": (
                    [value.value for value in payload.required_capabilities]
                    if payload.required_capabilities is not None
                    else None
                ),
                "selected_resource_number": payload.selected_resource_number,
                "data_facts": (
                    [fact.model_dump(mode="json") for fact in payload.data_facts]
                    if payload.data_facts is not None
                    else None
                ),
            }
        )
        return await self._apply_resume_command(
            run_id,
            payload,
            key_hash,
            request_hash,
            ShipmentAgentCommandType.RESUME,
        )

    async def reconcile(
        self,
        run_id: str,
        payload: ShipmentAgentReconcile,
        idempotency_key: str,
    ) -> ShipmentAgentRunRead:
        """显式重新检查源系统，不在后台死循环轮询。"""

        normalized_key = _normalize_idempotency_key(idempotency_key)
        key_hash = _idempotency_hash(normalized_key)
        request_hash = _canonical_hash(
            {
                "action": "RECHECK",
                "expected_run_version": payload.expected_run_version,
            }
        )
        resume = ShipmentAgentResume(
            action=ShipmentAgentAction.RECHECK,
            expected_run_version=payload.expected_run_version,
            # RECONCILE 没有人工 interrupt；占位值不会参与 RECHECK 校验，也不会落库。
            interrupt_type=ShipmentAgentInterruptType.RESOURCE_SELECTION,
        )
        return await self._apply_resume_command(
            run_id,
            resume,
            key_hash,
            request_hash,
            ShipmentAgentCommandType.RECONCILE,
        )

    async def _apply_resume_command(
        self,
        run_id: str,
        payload: ShipmentAgentResume,
        key_hash: str,
        request_hash: str,
        command_type: ShipmentAgentCommandType,
    ) -> ShipmentAgentRunRead:
        request_id = str(uuid4())
        try:
            async with self.session_factory() as session:
                repository = ShipmentAgentRunRepository(session)
                async with session.begin():
                    existing_command = await repository.get_command(key_hash)
                    if existing_command is not None:
                        self._ensure_same_command(
                            existing_command,
                            command_type,
                            request_hash,
                            expected_run_id=run_id,
                        )
                        existing = await repository.get(run_id)
                        if existing is None:
                            raise ShipmentAgentRunNotFoundError("Shipment Agent 运行不存在")
                        return self._to_read(existing)

                    run = await repository.get(run_id, for_update=True)
                    if run is None:
                        raise ShipmentAgentRunNotFoundError("Shipment Agent 运行不存在")
                    if run.status in _TERMINAL_STATUSES:
                        raise ShipmentAgentConflictError("该运行已经结束")
                    if run.run_version != payload.expected_run_version:
                        raise ShipmentAgentConflictError(
                            f"运行版本已变化，当前为 {run.run_version}，请刷新后重试"
                        )
                    automatic_recovery_kick = (
                        command_type is ShipmentAgentCommandType.RECONCILE
                        and payload.action is ShipmentAgentAction.RECHECK
                        and run.status is ShipmentAgentRunStatus.RUNNING
                        and run.pending_interrupt is None
                        and run.current_stage in _AUTOMATICALLY_ROUTABLE_RUNNING_STAGES
                    )
                    if (
                        not automatic_recovery_kick
                        and payload.action.value not in run.allowed_actions
                    ):
                        raise ShipmentAgentConflictError(
                            f"当前阶段不允许动作 {payload.action.value}"
                        )

                    if automatic_recovery_kick:
                        # 不修改业务阶段、不增加 run_version，也不追加一条容易误导业务的
                        # “重新检查”事件。事务只持久化幂等命令；提交后 Orchestrator 的
                        # synchronize 会根据当前 RUNNING 阶段恢复自动执行。
                        self._refresh_automatic_wake_marker(run)
                        repository.add(
                            ShipmentAgentCommand(
                                run_id=run.id,
                                command_type=command_type,
                                idempotency_key_hash=key_hash,
                                request_hash=request_hash,
                                request_id=request_id,
                                result_kind=ShipmentAgentCommandResult.NO_OP,
                                result_run_version=run.run_version,
                            )
                        )
                    else:
                        if payload.action is ShipmentAgentAction.SUPPLY_DATA:
                            readiness = await ShipmentDataReadinessRepository(session).get_for_case(
                                run.case_id,
                                for_update=True,
                            )
                            if readiness is None:
                                raise ShipmentAgentConflictError(
                                    "当前案件尚未形成可补充的数据就绪度记录"
                                )
                            event_type, title, detail, data = self._apply_data_supply(
                                run,
                                readiness,
                                payload,
                                request_id=request_id,
                            )
                        else:
                            event_type, title, detail, data = self._apply_action(
                                run,
                                payload,
                            )
                        self._refresh_automatic_wake_marker(run)
                        run.run_version += 1
                        event = self._event(
                            run,
                            request_id=request_id,
                            event_type=event_type,
                            title=title,
                            detail=detail,
                            data=data,
                        )
                        run.events.append(event)
                        repository.add(
                            ShipmentAgentCommand(
                                run_id=run.id,
                                command_type=command_type,
                                idempotency_key_hash=key_hash,
                                request_hash=request_hash,
                                request_id=request_id,
                                result_kind=ShipmentAgentCommandResult.APPLIED,
                                result_run_version=run.run_version,
                            )
                        )
                    await repository.flush()
        except IntegrityError:
            return await self._recover_command(
                run_id,
                key_hash,
                command_type,
                request_hash,
            )
        return await self.get(run_id)

    def _apply_data_supply(
        self,
        run: ShipmentAgentRun,
        readiness: ShipmentCaseDataReadiness,
        payload: ShipmentAgentResume,
        *,
        request_id: str,
    ) -> tuple[str, str, str, dict[str, object]]:
        """在命令事务内追加补数事实，并切回自动评估阶段。

        事实和幂等命令在同一个数据库事务中提交：如果响应在网络中丢失，调用方使用
        相同 ``Idempotency-Key`` 重试只会读取既有命令，不会把库存证据追加两遍。
        """

        pending = run.pending_interrupt
        if (
            pending is None
            or pending.get("type") != ShipmentAgentInterruptType.DATA_READINESS_REMEDIATION.value
        ):
            raise ShipmentAgentConflictError("当前运行没有数据补充待办")
        if payload.interrupt_type is not ShipmentAgentInterruptType.DATA_READINESS_REMEDIATION:
            raise ShipmentAgentConflictError("人工中断已变化，请刷新页面后重新确认")
        if not payload.data_facts:
            # Pydantic 已在 HTTP 边界保证；保留领域层防线供内部直接调用。
            raise ShipmentAgentValidationError("SUPPLY_DATA 必须提供 data_facts")

        details = pending.get("details")
        requested_raw = details.get("requested_facts", []) if isinstance(details, dict) else []
        try:
            requested = {
                (
                    value.subject_type.value,
                    value.subject_code,
                    value.fact_type.value,
                )
                for value in (
                    ShipmentDataRequestedFactRead.model_validate(item) for item in requested_raw
                )
            }
        except (TypeError, ValueError) as error:
            raise ShipmentAgentConflictError("当前数据补充待办缺少有效 requested_facts") from error

        supplied_keys = {
            (
                fact.subject_type.value,
                fact.subject_code,
                fact.fact_type.value,
            )
            for fact in payload.data_facts
        }
        unexpected = supplied_keys - requested
        if unexpected:
            rendered = "、".join(":".join(value) for value in sorted(unexpected))
            raise ShipmentAgentValidationError(
                f"只能回答当前 requested_facts，以下事实不在待办中：{rendered}"
            )

        now = datetime.now(UTC)
        recorded_at = now.isoformat().replace("+00:00", "Z")
        records: list[dict[str, object]] = []
        for fact in payload.data_facts:
            # ShipmentDataFactInput 已在 HTTP 边界校验；领域层仍保留同一条权威防线，
            # 防止未来内部调用绕过请求模型，把人工录入伪装成 ERP/WMS 真值。
            if fact.source_type not in _HUMAN_SUPPLY_DATA_SOURCE_TYPES:
                raise ShipmentAgentValidationError("人工 SUPPLY_DATA 不能声明 ERP/WMS 来源")
            if (
                fact.source_type is ShipmentDataFactSourceType.CSV_IMPORT
                and not fact.source_reference
            ):
                raise ShipmentAgentValidationError("CSV_IMPORT 必须提供 source_reference")
            observed_at = fact.observed_at or now
            records.append(
                {
                    "fact_id": str(uuid4()),
                    "subject_type": fact.subject_type.value,
                    "subject_code": fact.subject_code,
                    "fact_type": fact.fact_type.value,
                    "value": fact.value,
                    "source_type": fact.source_type.value,
                    "source_reference": fact.source_reference,
                    "source_locator": fact.source_locator,
                    "observed_at": observed_at.isoformat().replace(
                        "+00:00",
                        "Z",
                    ),
                    "expires_at": (
                        fact.expires_at.isoformat().replace("+00:00", "Z")
                        if fact.expires_at is not None
                        else None
                    ),
                    "actor_subject": SYSTEM_DEMO_ACTOR_SUBJECT,
                    "actor_display_name": SYSTEM_DEMO_ACTOR_SUBJECT,
                    "actor_source": SYSTEM_DEMO_ACTOR_SOURCE,
                    "request_id": request_id,
                    "recorded_at": recorded_at,
                }
            )

        readiness.facts = [*(readiness.facts or []), *records]
        readiness.revision += 1
        readiness.status = ShipmentDataReadinessStatus.NOT_EVALUATED
        readiness.ready_at = None

        run.status = ShipmentAgentRunStatus.RUNNING
        run.current_stage = ShipmentAgentStage.ASSESSING_DATA_READINESS
        run.pending_interrupt = None
        run.allowed_actions = []
        run.data_readiness_status = ShipmentDataReadinessStatus.NOT_EVALUATED
        run.data_readiness_revision = readiness.revision
        run.data_readiness_snapshot = self._empty_data_readiness_snapshot(
            source_mode=readiness.source_mode,
        )
        run.data_readiness_checked_at = None

        fact_keys = sorted(":".join(value) for value in supplied_keys)
        source_types = sorted({fact.source_type.value for fact in payload.data_facts})
        return (
            "DATA_SUPPLEMENTED",
            "数据补充已记录",
            (
                f"已追加 {len(records)} 条可追溯事实；"
                "Shipment Agent 将自动重新评估，不需要额外点击重新检查。"
            ),
            {
                "fact_keys": fact_keys,
                "source_types": source_types,
            },
        )

    def _apply_action(
        self,
        run: ShipmentAgentRun,
        payload: ShipmentAgentResume,
    ) -> tuple[str, str, str, dict[str, object]]:
        """把结构化人工动作翻译成下一业务阶段；不执行外部 I/O。"""

        action = payload.action
        interrupt_type = (
            str(run.pending_interrupt.get("type")) if run.pending_interrupt is not None else None
        )
        # 浏览器必须把自己真正看到的 interrupt_type 原样回传。即使动作名相同，
        # 旧的“批准提交”弹窗也不能误批准后来出现的“批准结案”节点。
        if (
            action not in {ShipmentAgentAction.RECHECK, ShipmentAgentAction.STOP}
            and interrupt_type != payload.interrupt_type.value
        ):
            raise ShipmentAgentConflictError("人工中断已变化，请刷新页面后重新确认")

        if action is ShipmentAgentAction.STOP:
            run.status = ShipmentAgentRunStatus.CANCELLED
            run.current_stage = ShipmentAgentStage.CANCELLED
            run.pending_interrupt = None
            run.allowed_actions = []
            run.completed_at = _utc_now_for_database()
            return (
                "RUN_CANCELLED",
                "Shipment Agent 已人工取消",
                payload.comment or "人工取消了本次总 Agent 运行。",
                {},
            )

        if action is ShipmentAgentAction.RECHECK:
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.SYNCING_CASE
            run.pending_interrupt = None
            run.allowed_actions = []
            return (
                "RECONCILIATION_REQUESTED",
                "已请求重新检查",
                payload.comment or "将重新读取案件、装载子 Agent 与 LINK 当前事实。",
                {},
            )

        if interrupt_type == ShipmentAgentInterruptType.SCENARIO_POLICY_CONFIRMATION.value:
            if action is not ShipmentAgentAction.CONFIRM:
                raise ShipmentAgentValidationError("运输场景策略必须使用 CONFIRM")
            capabilities = payload.required_capabilities or []
            run.required_capabilities = [value.value for value in capabilities]
            run.scenario_policy_confirmed = True
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.SYNCING_CASE
            run.pending_interrupt = None
            run.allowed_actions = []
            return (
                "SCENARIO_POLICY_CONFIRMED",
                "运输场景策略已确认",
                "本案件需要的能力为：" + "、".join(value.value for value in capabilities),
                {"required_capabilities": [value.value for value in capabilities]},
            )

        if interrupt_type == ShipmentAgentInterruptType.RESOURCE_SELECTION.value:
            if action is not ShipmentAgentAction.SELECT:
                raise ShipmentAgentValidationError("资源选择必须使用 SELECT")
            assert payload.selected_resource_number is not None
            details = run.pending_interrupt.get("details", {})
            expected_type = details.get("resource_type") if isinstance(details, dict) else None
            candidates = details.get("candidates", []) if isinstance(details, dict) else []
            try:
                resource_type = ShipmentResourceType(str(expected_type))
            except ValueError as error:
                raise ShipmentAgentValidationError(
                    "当前资源选择中断缺少有效 resource_type"
                ) from error
            if payload.selected_resource_number not in candidates:
                raise ShipmentAgentValidationError(
                    "selected_resource_number 不在当前已绑定候选资源中"
                )
            if resource_type is ShipmentResourceType.LOAD_PLANNING_TASK:
                run.selected_load_task_number = payload.selected_resource_number
                run.selected_load_run_id = None
                run.locked_load_version_id = None
                run.locked_load_version_number = None
                run.child_agent_snapshot = None
                self._reset_data_readiness_projection(run)
            else:
                run.selected_booking_application_number = payload.selected_resource_number
                run.booking_status = None
                run.carrier_submission_status = None
                run.booking_reference = None
                run.booking_submission_uncertain = False
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.SYNCING_CASE
            run.pending_interrupt = None
            run.allowed_actions = []
            return (
                "RESOURCE_SELECTED",
                "业务资源已选择",
                (f"已选择 {resource_type.value}：{payload.selected_resource_number}。"),
                {
                    "resource_type": resource_type.value,
                    "resource_number": payload.selected_resource_number,
                },
            )

        if action is not ShipmentAgentAction.APPROVE:
            raise ShipmentAgentValidationError("当前审批节点必须使用 APPROVE")

        if interrupt_type == ShipmentAgentInterruptType.CASE_ACTIVATION_APPROVAL.value:
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.AWAITING_CASE_ACTIVATION
            message = "已批准把 DRAFT 案件激活为 ACTIVE。"
            event_type = "CASE_ACTIVATION_APPROVED"
            title = "案件激活已批准"
        elif interrupt_type == ShipmentAgentInterruptType.BOOKING_SUBMIT_APPROVAL.value:
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.SUBMITTING_BOOKING
            run.booking_submission_uncertain = False
            message = "已批准把当前 LINK 草稿提交给承运人链路。"
            event_type = "BOOKING_SUBMIT_APPROVED"
            title = "LINK 提交已批准"
        elif interrupt_type == ShipmentAgentInterruptType.BOOKING_RETRY_APPROVAL.value:
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.AWAITING_BOOKING_RETRY_APPROVAL
            message = "已批准人工重试当前 DEAD 承运人投递任务。"
            event_type = "BOOKING_RETRY_APPROVED"
            title = "LINK 重试已批准"
        elif interrupt_type == ShipmentAgentInterruptType.COMPLETION_APPROVAL.value:
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.COMPLETING_CASE
            message = "已批准结案；总 Agent 将通过案件状态机完成案件。"
            event_type = "COMPLETION_APPROVED"
            title = "案件结案已批准"
        else:
            raise ShipmentAgentConflictError("当前运行没有可批准的人工中断")

        run.pending_interrupt = None
        run.allowed_actions = []
        if payload.comment:
            message += f" 备注：{payload.comment}"
        return event_type, title, message, {}

    async def evaluate(self, run_id: str) -> ShipmentAgentRunRead:
        """只根据权威业务事实选择下一节点，不调用远程 HTTP。"""

        current = await self._get_orm_snapshot(run_id)
        if current.status is not ShipmentAgentRunStatus.RUNNING:
            return self._to_read(current)

        if not current.scenario_policy_confirmed:
            return await self._set_pending(
                run_id,
                current.run_version,
                stage=ShipmentAgentStage.AWAITING_SCENARIO_POLICY,
                interrupt_type=(ShipmentAgentInterruptType.SCENARIO_POLICY_CONFIRMATION),
                title="确认运输场景需要的能力",
                message=(
                    f"场景 {current.shipment_case.transport_scenario.value} "
                    "尚无自动编排策略，请人工确认本票需要装载规划、LINK 订舱或两者。"
                ),
                details={
                    "transport_scenario": (current.shipment_case.transport_scenario.value),
                    "available_capabilities": [value.value for value in ShipmentAgentCapability],
                },
                allowed=[
                    ShipmentAgentAction.CONFIRM,
                    ShipmentAgentAction.STOP,
                ],
            )

        resource_requirement = self._first_unselected_resource(current)
        if resource_requirement is not None:
            resource_type, candidates = resource_requirement
            if len(candidates) == 1:
                return await self._auto_select_only_resource(
                    run_id,
                    current.run_version,
                    resource_type,
                    candidates[0],
                )
            if not candidates:
                return await self._wait_for_resource_binding(
                    run_id,
                    current.run_version,
                    resource_type,
                    "案件尚未绑定所需业务资源；请先在案件工作台完成绑定。",
                )
            return await self._set_pending(
                run_id,
                current.run_version,
                stage=ShipmentAgentStage.AWAITING_RESOURCE_SELECTION,
                interrupt_type=ShipmentAgentInterruptType.RESOURCE_SELECTION,
                title="选择业务资源",
                message="存在多个已绑定资源，总 Agent 不能替业务人员猜测。",
                details={
                    "resource_type": resource_type.value,
                    "candidates": candidates,
                },
                allowed=[
                    ShipmentAgentAction.SELECT,
                    ShipmentAgentAction.RECHECK,
                    ShipmentAgentAction.STOP,
                ],
            )

        # DATA_READINESS 是新 V2 运行的必经门禁。迁移前已经存在的 V1 运行使用
        # LEGACY_BYPASSED 保持原流程；BOOKING_ONLY 也会经过一次评估并得到
        # READY / NOT_APPLICABLE，而不是伪造装载库存要求。
        if current.data_readiness_status not in {
            ShipmentDataReadinessStatus.LEGACY_BYPASSED,
            ShipmentDataReadinessStatus.READY,
            ShipmentDataReadinessStatus.DEMO_READY,
        }:
            return await self._set_running_stage(
                run_id,
                current.run_version,
                ShipmentAgentStage.ASSESSING_DATA_READINESS,
                "DATA_READINESS_ASSESSMENT_STARTED",
                "开始评估业务数据",
                "将以确定性规则检查库存数量、主数据确认和证据有效期。",
            )

        case_status = current.shipment_case.status
        if case_status is ShipmentCaseStatus.DRAFT:
            return await self._set_pending(
                run_id,
                current.run_version,
                stage=ShipmentAgentStage.AWAITING_CASE_ACTIVATION,
                interrupt_type=(ShipmentAgentInterruptType.CASE_ACTIVATION_APPROVAL),
                title="批准激活案件",
                message="案件仍为 DRAFT；总 Agent 不会在无人确认时偷偷激活。",
                details={
                    "case_number": current.shipment_case.case_number,
                    "case_revision": current.shipment_case.revision,
                },
                allowed=[
                    ShipmentAgentAction.APPROVE,
                    ShipmentAgentAction.STOP,
                ],
            )
        if case_status is ShipmentCaseStatus.CANCELLED:
            return await self._mark_failed(
                run_id,
                current.run_version,
                "CASE_CANCELLED",
                "发运案件已取消，不能继续编排。",
            )
        if case_status is ShipmentCaseStatus.COMPLETED:
            return await self._mark_completed_from_existing_case(
                run_id,
                current.run_version,
            )

        capabilities = set(current.required_capabilities)
        if (
            ShipmentAgentCapability.LOAD_PLANNING.value in capabilities
            and current.locked_load_version_id is None
        ):
            return await self._set_running_stage(
                run_id,
                current.run_version,
                ShipmentAgentStage.STARTING_LOAD_AGENT,
                "LOAD_COORDINATION_STARTED",
                "开始协调装载子 Agent",
                "将读取正式装载版本；若尚无运行，则幂等启动装载子 Agent。",
            )
        if (
            ShipmentAgentCapability.LINK_BOOKING.value in capabilities
            and current.booking_status != "CONFIRMED"
        ):
            return await self._set_running_stage(
                run_id,
                current.run_version,
                ShipmentAgentStage.AWAITING_BOOKING_RESULT,
                "BOOKING_COORDINATION_STARTED",
                "开始协调 LINK 订舱",
                "将读取 LINK 申请与承运人投递当前状态。",
            )

        return await self._set_pending(
            run_id,
            current.run_version,
            stage=ShipmentAgentStage.AWAITING_COMPLETION_APPROVAL,
            interrupt_type=ShipmentAgentInterruptType.COMPLETION_APPROVAL,
            title="批准案件结案",
            message="所需分支已经具备正式结果；仍需人工确认后才能把案件标记为 COMPLETED。",
            details={
                "locked_load_version_id": current.locked_load_version_id,
                "locked_load_version_number": (current.locked_load_version_number),
                "booking_reference": current.booking_reference,
            },
            allowed=[
                ShipmentAgentAction.APPROVE,
                ShipmentAgentAction.STOP,
            ],
        )

    async def assess_data_readiness(
        self,
        run_id: str,
    ) -> ShipmentAgentRunRead:
        """评估选中装载任务，并生成补数中断或放行到案件激活。

        规则计算和两张本地表的更新位于同一个短事务中，不调用 LINK、LLM 或其他网络
        服务。补数后 LangGraph 会直接再次进入本节点，因此业务人员不需要再点击一次
        ``RECHECK``。
        """

        await self._commit_data_readiness_assessment(run_id)
        # 统一在事务完整提交后重新读取。两个 FAILED 早退分支也会更新数据库维护的
        # updated_at；若在 flush 后同步序列化 ORM 对象，会触发 AsyncSession 无法执行的
        # 隐式 I/O（MissingGreenlet）。
        return await self.get(run_id)

    async def _commit_data_readiness_assessment(
        self,
        run_id: str,
    ) -> None:
        """在一个短事务中提交数据就绪度业务事实，不在事务内构造返回 DTO。"""

        async with self.session_factory() as session:
            run_repository = ShipmentAgentRunRepository(session)
            readiness_repository = ShipmentDataReadinessRepository(session)
            async with session.begin():
                run = await run_repository.get(run_id, for_update=True)
                if run is None:
                    raise ShipmentAgentRunNotFoundError("Shipment Agent 运行不存在")
                if (
                    run.status is not ShipmentAgentRunStatus.RUNNING
                    or run.current_stage is not ShipmentAgentStage.ASSESSING_DATA_READINESS
                ):
                    return

                capabilities = set(run.required_capabilities)
                needs_load = ShipmentAgentCapability.LOAD_PLANNING.value in capabilities
                task = None
                if needs_load:
                    if not run.selected_load_task_number:
                        run.status = ShipmentAgentRunStatus.FAILED
                        run.current_stage = ShipmentAgentStage.FAILED
                        run.failure_code = "LOAD_RESOURCE_MISSING"
                        run.failure_message = "数据就绪度评估前找不到选中的装载任务。"
                        run.pending_interrupt = None
                        run.allowed_actions = []
                        run.completed_at = _utc_now_for_database()
                        run.run_version += 1
                        run.events.append(
                            self._event(
                                run,
                                request_id=str(uuid4()),
                                event_type="RUN_FAILED",
                                title="Shipment Agent 运行失败",
                                detail=(
                                    "LOAD_RESOURCE_MISSING：数据就绪度评估前找不到选中的装载任务。"
                                ),
                                data={"failure_code": "LOAD_RESOURCE_MISSING"},
                            )
                        )
                        self._refresh_automatic_wake_marker(run)
                        await run_repository.flush()
                        return
                    task = await readiness_repository.get_load_task(run.selected_load_task_number)
                    if task is None:
                        run.status = ShipmentAgentRunStatus.FAILED
                        run.current_stage = ShipmentAgentStage.FAILED
                        run.failure_code = "LOAD_TASK_NOT_FOUND"
                        run.failure_message = "案件绑定的装载任务已不存在。"
                        run.pending_interrupt = None
                        run.allowed_actions = []
                        run.completed_at = _utc_now_for_database()
                        run.run_version += 1
                        run.events.append(
                            self._event(
                                run,
                                request_id=str(uuid4()),
                                event_type="RUN_FAILED",
                                title="Shipment Agent 运行失败",
                                detail=("LOAD_TASK_NOT_FOUND：案件绑定的装载任务已不存在。"),
                                data={"failure_code": "LOAD_TASK_NOT_FOUND"},
                            )
                        )
                        self._refresh_automatic_wake_marker(run)
                        await run_repository.flush()
                        return

                readiness = await readiness_repository.get_for_case(
                    run.case_id,
                    for_update=True,
                )
                facts = readiness.facts if readiness is not None else []
                now_aware = datetime.now(UTC)
                result = assess_shipment_data_readiness(
                    task=task,
                    facts=facts,
                    now=now_aware,
                )
                issues = [issue.model_dump(mode="json") for issue in result.issues]
                requested_facts = [
                    value.model_dump(mode="json") for value in result.requested_facts
                ]
                ready = result.status in {
                    ShipmentDataReadinessStatus.READY,
                    ShipmentDataReadinessStatus.DEMO_READY,
                }
                ready_at_aware = now_aware if ready else None
                now_database = now_aware.replace(tzinfo=None)

                if readiness is None:
                    readiness = ShipmentCaseDataReadiness(
                        case_id=run.case_id,
                        revision=1,
                        status=result.status,
                        policy_version=(SHIPMENT_DATA_READINESS_POLICY_VERSION),
                        source_mode=result.source_mode,
                        facts=[],
                        assessment={},
                        snapshot_hash=result.snapshot_hash,
                        assessed_at=now_database,
                        ready_at=(
                            ready_at_aware.replace(tzinfo=None)
                            if ready_at_aware is not None
                            else None
                        ),
                    )
                    readiness_repository.add(readiness)
                else:
                    readiness.revision += 1
                    readiness.status = result.status
                    readiness.policy_version = SHIPMENT_DATA_READINESS_POLICY_VERSION
                    readiness.source_mode = result.source_mode
                    readiness.snapshot_hash = result.snapshot_hash
                    readiness.assessed_at = now_database
                    readiness.ready_at = (
                        ready_at_aware.replace(tzinfo=None) if ready_at_aware is not None else None
                    )

                assessment = {
                    "status": result.status.value,
                    "policy_version": SHIPMENT_DATA_READINESS_POLICY_VERSION,
                    "source_mode": result.source_mode.value,
                    "selected_load_task_number": (
                        run.selected_load_task_number if needs_load else None
                    ),
                    "snapshot_hash": result.snapshot_hash,
                    "assessed_at": now_aware.isoformat().replace(
                        "+00:00",
                        "Z",
                    ),
                    "ready_at": (
                        ready_at_aware.isoformat().replace("+00:00", "Z")
                        if ready_at_aware is not None
                        else None
                    ),
                    "issue_count": len(issues),
                    "blocking_issue_count": len(issues),
                    "issues": issues,
                    "requested_facts": requested_facts,
                }
                readiness.assessment = assessment

                run.data_readiness_status = result.status
                run.data_readiness_revision = readiness.revision
                run.data_readiness_snapshot = {
                    key: value
                    for key, value in assessment.items()
                    if key
                    not in {
                        "status",
                        "selected_load_task_number",
                        "requested_facts",
                    }
                }
                run.data_readiness_checked_at = now_database
                run.failure_code = None
                run.failure_message = None

                if result.status is ShipmentDataReadinessStatus.ACTION_REQUIRED:
                    title = "业务数据需要补充"
                    detail = (
                        f"发现 {len(issues)} 个阻断问题；请补充当前待办列出的库存或主数据确认事实。"
                    )
                    run.status = ShipmentAgentRunStatus.WAITING_HUMAN
                    run.current_stage = ShipmentAgentStage.AWAITING_DATA_REMEDIATION
                    run.pending_interrupt = {
                        "type": (ShipmentAgentInterruptType.DATA_READINESS_REMEDIATION.value),
                        "title": title,
                        "description": detail,
                        "allowed_actions": [
                            ShipmentAgentAction.SUPPLY_DATA.value,
                            ShipmentAgentAction.STOP.value,
                        ],
                        "details": {
                            "readiness_revision": readiness.revision,
                            "policy_version": (SHIPMENT_DATA_READINESS_POLICY_VERSION),
                            "readiness_status": result.status.value,
                            "issues": issues,
                            "requested_facts": requested_facts,
                        },
                    }
                    run.allowed_actions = [
                        ShipmentAgentAction.SUPPLY_DATA.value,
                        ShipmentAgentAction.STOP.value,
                    ]
                    event_type = "DATA_REMEDIATION_REQUIRED"
                else:
                    title = "业务数据已就绪"
                    detail = (
                        "演示数据已满足当前规则，已明确标记为 DEMO_READY。"
                        if result.status is ShipmentDataReadinessStatus.DEMO_READY
                        else "库存与主数据门禁已满足，可以继续后续流程。"
                    )
                    run.status = ShipmentAgentRunStatus.RUNNING
                    run.current_stage = ShipmentAgentStage.SYNCING_CASE
                    run.pending_interrupt = None
                    run.allowed_actions = []
                    event_type = "DATA_READINESS_READY"

                run.run_version += 1
                run.events.append(
                    self._event(
                        run,
                        request_id=str(uuid4()),
                        event_type=event_type,
                        title=title,
                        detail=detail,
                        data={
                            "fact_keys": list(result.fact_keys),
                            "issue_codes": sorted(
                                {issue.issue_code.value for issue in result.issues}
                            ),
                            "source_types": list(result.source_types),
                            "issue_count": len(issues),
                            "blocking_issue_count": len(issues),
                            "snapshot_hash": result.snapshot_hash,
                        },
                    )
                )
                self._refresh_automatic_wake_marker(run)
                await run_repository.flush()

    async def coordinate_load(
        self,
        run_id: str,
        *,
        allow_waiting_poll: bool = False,
    ) -> ShipmentAgentRunRead:
        """读取正式版本；必要时幂等启动或恢复装载子 Agent。

        普通 LangGraph 节点只处理 ``RUNNING + STARTING_LOAD_AGENT``。后台 Worker 可以在
        ``allow_waiting_poll`` 下观察装载人工节点是否已经产生正式版本；若仍无变化，
        ``_mutate`` 返回 False，不增加 run_version、Trace 或通知。
        """

        current = await self._get_orm_snapshot(run_id)
        expected_version = current.run_version
        task_number = current.selected_load_task_number
        running_start = (
            current.status is ShipmentAgentRunStatus.RUNNING
            and current.current_stage is ShipmentAgentStage.STARTING_LOAD_AGENT
        )
        waiting_poll = (
            allow_waiting_poll
            and current.status is ShipmentAgentRunStatus.WAITING_RESOURCE
            and current.current_stage
            in {
                ShipmentAgentStage.AWAITING_LOAD_CONSTRAINTS,
                ShipmentAgentStage.AWAITING_LOAD_REVIEW,
                ShipmentAgentStage.AWAITING_LOAD_RELEASE,
                ShipmentAgentStage.AWAITING_LOAD_REPLAN,
            }
        )
        if not running_start and not waiting_poll:
            return self._to_read(current)
        if not task_number:
            return await self._set_running_stage(
                run_id,
                expected_version,
                ShipmentAgentStage.SYNCING_CASE,
                "LOAD_RESOURCE_MISSING",
                "装载资源需要重新检查",
                "运行没有选中的装载任务，将重新检查案件绑定。",
            )

        async with self.session_factory() as session:
            version_repository = LoadPlanVersionRepository(session)
            task = await version_repository.get_task_by_number(task_number)
            current_version = (
                await version_repository.get_current_for_task(task.id) if task is not None else None
            )
        if current_version is not None:
            return await self._record_load_version(
                run_id,
                expected_version,
                current_version.id,
                current_version.version_number,
            )
        if task is None:
            return await self._mark_failed(
                run_id,
                expected_version,
                "LOAD_TASK_NOT_FOUND",
                "案件绑定的装载任务已不存在。",
            )

        # 子 Agent 使用自己的业务表和 LangGraph thread。这里没有打开总 Agent 写事务。
        async with self.session_factory() as session:
            agent_service = StatefulPalletAgentService(AgentRunRepository(session))
            version_service = LoadPlanVersionService(LoadPlanVersionRepository(session))
            child_workflow = LoadPlanningWorkflowOrchestrator(
                agent_service=agent_service,
                plan_version_service=version_service,
                checkpointer=self.checkpointer,
            )
            history = await agent_service.list_history(task_number)
            if history:
                child = history[0]
                await child_workflow.synchronize(child.run_id)
            else:
                try:
                    child = await child_workflow.create(
                        task_number,
                        AgentRunCreate(),
                        idempotency_key=f"shipment-agent-{run_id}-load-start",
                    )
                except Exception:  # noqa: BLE001 - 子 Agent 已自行持久化安全失败摘要
                    # 装托算法异常会先把 FAILED 运行持久化。重新读取后把真实 child 暴露给
                    # 页面，而不是只返回一个丢失 run_id 的 500。
                    history = await agent_service.list_history(task_number)
                    if not history:
                        return await self._mark_failed(
                            run_id,
                            expected_version,
                            "LOAD_AGENT_START_FAILED",
                            "装载子 Agent 启动失败，且没有可恢复运行。",
                        )
                    child = history[0]

        stage_by_status = {
            AgentRunStatus.AWAITING_INPUT: (
                ShipmentAgentStage.AWAITING_LOAD_CONSTRAINTS,
                "请在装载工作台补充缺失约束",
            ),
            AgentRunStatus.READY_FOR_REVIEW: (
                ShipmentAgentStage.AWAITING_LOAD_REVIEW,
                "请在装载工作台评审候选方案",
            ),
            AgentRunStatus.APPROVED: (
                ShipmentAgentStage.AWAITING_LOAD_RELEASE,
                "请在装载工作台发布正式版本",
            ),
            AgentRunStatus.REJECTED: (
                ShipmentAgentStage.AWAITING_LOAD_REPLAN,
                "请在装载工作台发起重新规划",
            ),
            AgentRunStatus.FAILED: (
                ShipmentAgentStage.AWAITING_LOAD_REPLAN,
                "请查看失败原因并在装载工作台重新规划",
            ),
        }
        stage, next_action = stage_by_status[child.status]
        child_snapshot = {
            "run_id": child.run_id,
            "task_number": child.task_number,
            "status": child.status.value,
            "current_step": child.current_step,
            "run_version": child.run_version,
            "next_action": next_action,
        }

        def mutate(run: ShipmentAgentRun) -> bool:
            changed = (
                run.selected_load_run_id != child.run_id
                or run.current_stage is not stage
                or run.status is not ShipmentAgentRunStatus.WAITING_RESOURCE
                or run.child_agent_snapshot != child_snapshot
            )
            run.selected_load_run_id = child.run_id
            run.child_agent_snapshot = child_snapshot
            run.status = ShipmentAgentRunStatus.WAITING_RESOURCE
            run.current_stage = stage
            run.pending_interrupt = None
            run.allowed_actions = [
                ShipmentAgentAction.RECHECK.value,
                ShipmentAgentAction.STOP.value,
            ]
            return changed

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="LOAD_CHILD_ACTION_REQUIRED",
            title="装载子 Agent 等待处理",
            detail=next_action,
            data={"child_agent": child_snapshot},
        )

    async def coordinate_booking(
        self,
        run_id: str,
        *,
        allow_waiting_poll: bool = False,
    ) -> ShipmentAgentRunRead:
        """读取 LINK 权威状态，并选择提交、等待、重试或替换分支。

        后台 Worker 只允许从 ``WAITING_EXTERNAL`` 做 GET 核对，绝不会自动调用提交或
        DEAD 重试。状态未变化时不产生新的业务事件。
        """

        current = await self._get_orm_snapshot(run_id)
        expected_version = current.run_version
        application_number = current.selected_booking_application_number
        running_check = (
            current.status is ShipmentAgentRunStatus.RUNNING
            and current.current_stage is ShipmentAgentStage.AWAITING_BOOKING_RESULT
        )
        waiting_poll = (
            allow_waiting_poll
            and current.status is ShipmentAgentRunStatus.WAITING_EXTERNAL
            and current.current_stage is ShipmentAgentStage.AWAITING_BOOKING_RESULT
        )
        if not running_check and not waiting_poll:
            return self._to_read(current)
        if not application_number:
            return await self._set_running_stage(
                run_id,
                expected_version,
                ShipmentAgentStage.SYNCING_CASE,
                "BOOKING_RESOURCE_MISSING",
                "LINK 资源需要重新检查",
                "运行没有选中的 LINK 申请，将重新检查案件绑定。",
            )

        try:
            snapshot = await self.link_gateway.get_detail(application_number)
        except LinkBookingNotFoundError:
            return await self._request_booking_replacement(
                current,
                "当前 LINK 申请已不存在，请绑定并选择替代申请。",
            )
        except (LinkBookingUnavailableError, LinkBookingProtocolError) as error:
            if allow_waiting_poll:
                raise ShipmentAgentTransientDependencyError("LINK 状态暂时无法读取") from error
            return await self._wait_for_booking_reconciliation(
                run_id,
                expected_version,
                type(error).__name__,
                str(error),
            )

        if snapshot.application_status == "CONFIRMED":

            def record_confirmed(run: ShipmentAgentRun) -> bool:
                snapshot_changed = self._copy_link_snapshot(run, snapshot)
                changed = (
                    snapshot_changed
                    or run.booking_submission_uncertain
                    or run.status is not ShipmentAgentRunStatus.RUNNING
                    or run.current_stage is not ShipmentAgentStage.SYNCING_CASE
                    or run.pending_interrupt is not None
                    or run.allowed_actions != []
                )
                run.booking_submission_uncertain = False
                run.status = ShipmentAgentRunStatus.RUNNING
                run.current_stage = ShipmentAgentStage.SYNCING_CASE
                run.pending_interrupt = None
                run.allowed_actions = []
                return changed

            return await self._mutate(
                run_id,
                expected_version,
                record_confirmed,
                event_type="BOOKING_CONFIRMED",
                title="LINK 订舱已确认",
                detail=(
                    "已取得构造订舱参考号，订舱分支完成。"
                    if snapshot.booking_reference
                    else "LINK 申请已确认，订舱分支完成。"
                ),
                data={
                    "application_number": application_number,
                    "booking_reference": snapshot.booking_reference,
                },
            )

        if snapshot.application_status in {"REJECTED", "CANCELLED"}:
            return await self._request_booking_replacement(
                current,
                f"当前 LINK 申请为 {snapshot.application_status}，需要选择替代申请。",
                link_status=snapshot.application_status,
                carrier_status=snapshot.carrier_status,
            )

        if snapshot.application_status == "DRAFT":
            return await self._set_booking_pending(
                current,
                snapshot,
                stage=(ShipmentAgentStage.AWAITING_BOOKING_SUBMIT_APPROVAL),
                interrupt_type=ShipmentAgentInterruptType.BOOKING_SUBMIT_APPROVAL,
                title="批准提交 LINK 订舱",
                message=(
                    "LINK 申请仍为 DRAFT；总 Agent 只在人工批准后执行提交。"
                    + (
                        " 上一次提交结果未知，请先核对后再明确批准一次。"
                        if current.booking_submission_uncertain
                        else ""
                    )
                ),
                allowed=[
                    ShipmentAgentAction.APPROVE,
                    ShipmentAgentAction.STOP,
                ],
            )

        if snapshot.application_status == "SUBMITTED":
            if snapshot.carrier_status == "DEAD":
                return await self._set_booking_pending(
                    current,
                    snapshot,
                    stage=(ShipmentAgentStage.AWAITING_BOOKING_RETRY_APPROVAL),
                    interrupt_type=(ShipmentAgentInterruptType.BOOKING_RETRY_APPROVAL),
                    title="批准重试承运人投递",
                    message=("LINK 投递任务已进入 DEAD；不会自动重复发送，需人工批准重试。"),
                    allowed=[
                        ShipmentAgentAction.APPROVE,
                        ShipmentAgentAction.STOP,
                    ],
                )
            return await self._set_booking_waiting_external(current, snapshot)

        return await self._wait_for_booking_reconciliation(
            run_id,
            expected_version,
            "UNSUPPORTED_LINK_STATUS",
            f"暂不识别 LINK 状态 {snapshot.application_status}，请人工核对。",
            snapshot=snapshot,
        )

    async def poll_waiting_resource(self, run_id: str) -> ShipmentAgentRunRead:
        """观察资源绑定类等待；没有新候选时保持完全无副作用。

        该入口只用于后台 Worker。它不会自动替业务人员选择多个候选：初次缺少资源时，
        发现绑定后回到 ``SYNCING_CASE``，随后仍由既有策略执行“唯一候选自动选择 / 多候选
        人工选择”；替代 LINK 申请出现后则进入明确的人工选择节点。
        """

        current = await self._get_orm_snapshot(run_id)
        if current.status is not ShipmentAgentRunStatus.WAITING_RESOURCE:
            return self._to_read(current)

        if current.current_stage is ShipmentAgentStage.AWAITING_RESOURCE_BINDING:
            requirement = self._first_unselected_resource(current)
            if requirement is None or not requirement[1]:
                return self._to_read(current)
            resource_type, candidates = requirement
            return await self._set_running_stage(
                run_id,
                current.run_version,
                ShipmentAgentStage.SYNCING_CASE,
                "RESOURCE_BINDING_DETECTED",
                "检测到新的业务资源",
                (f"已发现 {len(candidates)} 个 {resource_type.value} 候选，将按既有选择策略继续。"),
            )

        if current.current_stage is ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT:
            candidates = [
                binding.resource_number
                for binding in current.shipment_case.resource_bindings
                if (
                    binding.resource_type is ShipmentResourceType.LINK_BOOKING_APPLICATION
                    and binding.resource_number != current.selected_booking_application_number
                )
            ]
            if not candidates:
                return self._to_read(current)
            return await self._request_booking_replacement(
                current,
                "已检测到新的 LINK 申请，请确认本票实际使用的替代申请。",
                link_status=current.booking_status,
                carrier_status=current.carrier_submission_status,
            )

        return self._to_read(current)

    async def activate_case(self, run_id: str) -> ShipmentAgentRunRead:
        """在人工批准后通过既有 Shipment Case 状态机激活案件。"""

        current = await self._get_orm_snapshot(run_id)
        expected_version = current.run_version
        if (
            current.current_stage is not ShipmentAgentStage.AWAITING_CASE_ACTIVATION
            or current.status is not ShipmentAgentRunStatus.RUNNING
        ):
            return self._to_read(current)
        shipment_case = current.shipment_case
        if shipment_case.status is ShipmentCaseStatus.DRAFT:
            await self.case_service.change_status(
                shipment_case.case_number,
                ShipmentCaseStatusChange(
                    target_status=ShipmentCaseStatus.ACTIVE,
                    expected_revision=shipment_case.revision,
                    reason=f"Shipment Agent {run_id} 经人工批准后激活案件",
                ),
                idempotency_key=f"shipment-agent-{run_id}-activate-case",
            )
        elif shipment_case.status is not ShipmentCaseStatus.ACTIVE:
            return await self._mark_failed(
                run_id,
                expected_version,
                "CASE_ACTIVATION_CONFLICT",
                f"案件当前为 {shipment_case.status.value}，无法激活。",
            )
        return await self._set_running_stage(
            run_id,
            expected_version,
            ShipmentAgentStage.SYNCING_CASE,
            "CASE_ACTIVATED",
            "案件已激活",
            "案件已通过既有状态机进入 ACTIVE。",
        )

    async def submit_booking(self, run_id: str) -> ShipmentAgentRunRead:
        """在审批后执行一次 LINK 提交；未知结果绝不自动重试。"""

        current = await self._get_orm_snapshot(run_id)
        expected_version = current.run_version
        if current.current_stage is not ShipmentAgentStage.SUBMITTING_BOOKING:
            return self._to_read(current)
        application_number = current.selected_booking_application_number
        if not application_number:
            return await self._mark_failed(
                run_id,
                expected_version,
                "BOOKING_RESOURCE_MISSING",
                "提交前找不到选中的 LINK 申请。",
            )

        try:
            snapshot = await self.link_gateway.get_detail(application_number)
            if snapshot.application_status == "DRAFT":
                await self.link_gateway.submit(application_number)
        except LinkCommandResultUnknownError as error:
            return await self._record_unknown_booking_command(
                run_id,
                expected_version,
                "BOOKING_SUBMIT_RESULT_UNKNOWN",
                str(error),
            )
        except (
            LinkBookingUnavailableError,
            LinkBookingProtocolError,
            LinkCommandConflictError,
        ) as error:
            return await self._wait_for_booking_reconciliation(
                run_id,
                expected_version,
                type(error).__name__,
                str(error),
            )
        except LinkBookingNotFoundError:
            return await self._request_booking_replacement(
                current,
                "准备提交时发现 LINK 申请不存在，请选择替代申请。",
            )

        return await self._set_running_stage(
            run_id,
            expected_version,
            ShipmentAgentStage.AWAITING_BOOKING_RESULT,
            "BOOKING_SUBMITTED",
            "LINK 提交命令已接受",
            "将重新读取 LINK 与承运人投递状态；请求线程不会等待最终回调。",
        )

    async def retry_booking(self, run_id: str) -> ShipmentAgentRunRead:
        """在人工批准后执行一次 LINK 承运人任务重试。"""

        current = await self._get_orm_snapshot(run_id)
        expected_version = current.run_version
        if (
            current.current_stage is not ShipmentAgentStage.AWAITING_BOOKING_RETRY_APPROVAL
            or current.status is not ShipmentAgentRunStatus.RUNNING
        ):
            return self._to_read(current)
        application_number = current.selected_booking_application_number
        if not application_number:
            return await self._mark_failed(
                run_id,
                expected_version,
                "BOOKING_RESOURCE_MISSING",
                "重试前找不到选中的 LINK 申请。",
            )

        try:
            snapshot = await self.link_gateway.get_detail(application_number)
            if snapshot.carrier_status in {"DEAD", "RETRY_WAIT"}:
                await self.link_gateway.retry(application_number)
        except LinkCommandResultUnknownError as error:
            return await self._record_unknown_booking_command(
                run_id,
                expected_version,
                "BOOKING_RETRY_RESULT_UNKNOWN",
                str(error),
            )
        except (
            LinkBookingUnavailableError,
            LinkBookingProtocolError,
            LinkCommandConflictError,
        ) as error:
            return await self._wait_for_booking_reconciliation(
                run_id,
                expected_version,
                type(error).__name__,
                str(error),
            )
        except LinkBookingNotFoundError:
            return await self._request_booking_replacement(
                current,
                "重试时发现 LINK 申请不存在，请选择替代申请。",
            )

        return await self._set_running_stage(
            run_id,
            expected_version,
            ShipmentAgentStage.AWAITING_BOOKING_RESULT,
            "BOOKING_RETRY_REQUESTED",
            "承运人投递已重新触发",
            "将通过重新检查观察投递状态，不在当前请求中阻塞等待承运人回调。",
        )

    async def complete_case(self, run_id: str) -> ShipmentAgentRunRead:
        """在人工批准后完成案件，再把总 Agent 标记为 COMPLETED。"""

        current = await self._get_orm_snapshot(run_id)
        expected_version = current.run_version
        if current.current_stage is not ShipmentAgentStage.COMPLETING_CASE:
            return self._to_read(current)
        shipment_case = current.shipment_case
        if shipment_case.status is ShipmentCaseStatus.ACTIVE:
            await self.case_service.change_status(
                shipment_case.case_number,
                ShipmentCaseStatusChange(
                    target_status=ShipmentCaseStatus.COMPLETED,
                    expected_revision=shipment_case.revision,
                    reason=f"Shipment Agent {run_id} 的所需分支已完成并经人工批准结案",
                ),
                idempotency_key=f"shipment-agent-{run_id}-complete-case",
            )
        elif shipment_case.status is not ShipmentCaseStatus.COMPLETED:
            return await self._mark_failed(
                run_id,
                expected_version,
                "CASE_COMPLETION_CONFLICT",
                f"案件当前为 {shipment_case.status.value}，无法结案。",
            )

        def mutate(run: ShipmentAgentRun) -> bool:
            run.status = ShipmentAgentRunStatus.COMPLETED
            run.current_stage = ShipmentAgentStage.COMPLETED
            run.pending_interrupt = None
            run.allowed_actions = []
            run.completed_at = _utc_now_for_database()
            return True

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="RUN_COMPLETED",
            title="Shipment Agent 已完成",
            detail="装载与/或订舱所需分支完成，案件已由人工批准结案。",
            data={},
        )

    async def _set_booking_pending(
        self,
        current: ShipmentAgentRun,
        snapshot,
        *,
        stage: ShipmentAgentStage,
        interrupt_type: ShipmentAgentInterruptType,
        title: str,
        message: str,
        allowed: list[ShipmentAgentAction],
    ) -> ShipmentAgentRunRead:
        details = {
            "application_number": snapshot.application_number,
            "application_status": snapshot.application_status,
            "carrier_status": snapshot.carrier_status,
            "carrier_can_retry": snapshot.carrier_can_retry,
        }
        pending = {
            "type": interrupt_type.value,
            "title": title,
            "description": message,
            "allowed_actions": [value.value for value in allowed],
            "details": details,
        }

        def mutate(run: ShipmentAgentRun) -> bool:
            snapshot_changed = self._copy_link_snapshot(run, snapshot)
            changed = (
                snapshot_changed
                or run.pending_interrupt != pending
                or run.current_stage is not stage
                or run.status is not ShipmentAgentRunStatus.WAITING_HUMAN
                or run.allowed_actions != [value.value for value in allowed]
            )
            run.status = ShipmentAgentRunStatus.WAITING_HUMAN
            run.current_stage = stage
            run.pending_interrupt = pending
            run.allowed_actions = [value.value for value in allowed]
            return changed

        return await self._mutate(
            current.id,
            current.run_version,
            mutate,
            event_type="HUMAN_ACTION_REQUIRED",
            title=title,
            detail=message,
            data={"interrupt_type": interrupt_type.value, **details},
        )

    async def _set_booking_waiting_external(
        self,
        current: ShipmentAgentRun,
        snapshot,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            snapshot_changed = self._copy_link_snapshot(run, snapshot)
            changed = (
                snapshot_changed
                or run.booking_submission_uncertain
                or run.status is not ShipmentAgentRunStatus.WAITING_EXTERNAL
                or run.current_stage is not ShipmentAgentStage.AWAITING_BOOKING_RESULT
                or run.pending_interrupt is not None
                or run.allowed_actions
                != [
                    ShipmentAgentAction.RECHECK.value,
                    ShipmentAgentAction.STOP.value,
                ]
            )
            run.booking_submission_uncertain = False
            run.status = ShipmentAgentRunStatus.WAITING_EXTERNAL
            run.current_stage = ShipmentAgentStage.AWAITING_BOOKING_RESULT
            run.pending_interrupt = None
            run.allowed_actions = [
                ShipmentAgentAction.RECHECK.value,
                ShipmentAgentAction.STOP.value,
            ]
            return changed

        return await self._mutate(
            current.id,
            current.run_version,
            mutate,
            event_type="CARRIER_CALLBACK_PENDING",
            title="等待承运人异步结果",
            detail=(
                f"LINK={snapshot.application_status}，"
                f"carrier={snapshot.carrier_status or '尚未建立投递任务'}。"
            ),
            data={
                "application_status": snapshot.application_status,
                "carrier_status": snapshot.carrier_status,
            },
        )

    async def _request_booking_replacement(
        self,
        current: ShipmentAgentRun,
        message: str,
        *,
        link_status: str | None = None,
        carrier_status: str | None = None,
    ) -> ShipmentAgentRunRead:
        candidates = [
            binding.resource_number
            for binding in current.shipment_case.resource_bindings
            if (
                binding.resource_type is ShipmentResourceType.LINK_BOOKING_APPLICATION
                and binding.resource_number != current.selected_booking_application_number
            )
        ]
        if not candidates:
            allowed_actions = [
                ShipmentAgentAction.RECHECK.value,
                ShipmentAgentAction.STOP.value,
            ]

            def wait_for_binding(run: ShipmentAgentRun) -> bool:
                changed = (
                    run.status is not ShipmentAgentRunStatus.WAITING_RESOURCE
                    or run.current_stage is not ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT
                    or run.booking_status != link_status
                    or run.carrier_submission_status != carrier_status
                    or run.pending_interrupt is not None
                    or run.allowed_actions != allowed_actions
                )
                run.booking_status = link_status
                run.carrier_submission_status = carrier_status
                run.status = ShipmentAgentRunStatus.WAITING_RESOURCE
                run.current_stage = ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT
                run.pending_interrupt = None
                run.allowed_actions = allowed_actions
                return changed

            return await self._mutate(
                current.id,
                current.run_version,
                wait_for_binding,
                event_type="BOOKING_REPLACEMENT_BINDING_REQUIRED",
                title="需要绑定替代 LINK 申请",
                detail=message,
                data={
                    "resource_type": (ShipmentResourceType.LINK_BOOKING_APPLICATION.value),
                    "rejected_resource": (current.selected_booking_application_number),
                },
            )

        pending = {
            "type": ShipmentAgentInterruptType.RESOURCE_SELECTION.value,
            "title": "选择替代 LINK 申请",
            "description": message,
            "allowed_actions": [
                *([ShipmentAgentAction.SELECT.value] if candidates else []),
                ShipmentAgentAction.RECHECK.value,
                ShipmentAgentAction.STOP.value,
            ],
            "details": {
                "resource_type": (ShipmentResourceType.LINK_BOOKING_APPLICATION.value),
                "candidates": sorted(candidates),
                "rejected_resource": (current.selected_booking_application_number),
            },
        }
        allowed_actions = [
            ShipmentAgentAction.SELECT.value,
            ShipmentAgentAction.RECHECK.value,
            ShipmentAgentAction.STOP.value,
        ]

        def mutate(run: ShipmentAgentRun) -> bool:
            changed = (
                run.booking_status != link_status
                or run.carrier_submission_status != carrier_status
                or run.status is not ShipmentAgentRunStatus.WAITING_HUMAN
                or run.current_stage is not ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT
                or run.pending_interrupt != pending
                or run.allowed_actions != allowed_actions
            )
            run.booking_status = link_status
            run.carrier_submission_status = carrier_status
            run.status = ShipmentAgentRunStatus.WAITING_HUMAN
            run.current_stage = ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT
            run.pending_interrupt = pending
            run.allowed_actions = allowed_actions
            return changed

        return await self._mutate(
            current.id,
            current.run_version,
            mutate,
            event_type="BOOKING_REPLACEMENT_REQUIRED",
            title="LINK 申请需要替换",
            detail=message,
            data={
                "candidates": sorted(candidates),
                "rejected_resource": current.selected_booking_application_number,
            },
        )

    async def _wait_for_resource_binding(
        self,
        run_id: str,
        expected_version: int,
        resource_type: ShipmentResourceType,
        message: str,
    ) -> ShipmentAgentRunRead:
        """没有候选时只等待绑定，不伪造一个无法 SELECT 的人工中断。"""

        def mutate(run: ShipmentAgentRun) -> bool:
            changed = (
                run.status is not ShipmentAgentRunStatus.WAITING_RESOURCE
                or run.current_stage is not ShipmentAgentStage.AWAITING_RESOURCE_BINDING
                or run.pending_interrupt is not None
            )
            run.status = ShipmentAgentRunStatus.WAITING_RESOURCE
            run.current_stage = ShipmentAgentStage.AWAITING_RESOURCE_BINDING
            run.pending_interrupt = None
            run.allowed_actions = [
                ShipmentAgentAction.RECHECK.value,
                ShipmentAgentAction.STOP.value,
            ]
            return changed

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="RESOURCE_BINDING_REQUIRED",
            title="等待案件绑定业务资源",
            detail=message,
            data={
                "resource_type": resource_type.value,
                "candidates": [],
            },
        )

    async def _wait_for_booking_reconciliation(
        self,
        run_id: str,
        expected_version: int,
        error_code: str,
        message: str,
        *,
        snapshot=None,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            snapshot_changed = False
            if snapshot is not None:
                # 本分支最终要保留当前 reconciliation 错误，不能先把旧错误清空再据此
                # 判断变化，否则完全相同的轮询也会被误记为一次新事件。
                snapshot_changed = self._copy_link_snapshot(
                    run,
                    snapshot,
                    clear_failure=False,
                )
            failure_message = message[:2_000]
            changed = (
                snapshot_changed
                or run.status is not ShipmentAgentRunStatus.WAITING_EXTERNAL
                or run.current_stage is not ShipmentAgentStage.AWAITING_BOOKING_RESULT
                or run.pending_interrupt is not None
                or run.allowed_actions
                != [
                    ShipmentAgentAction.RECHECK.value,
                    ShipmentAgentAction.STOP.value,
                ]
                or run.failure_code != error_code
                or run.failure_message != failure_message
            )
            run.status = ShipmentAgentRunStatus.WAITING_EXTERNAL
            run.current_stage = ShipmentAgentStage.AWAITING_BOOKING_RESULT
            run.pending_interrupt = None
            run.allowed_actions = [
                ShipmentAgentAction.RECHECK.value,
                ShipmentAgentAction.STOP.value,
            ]
            run.failure_code = error_code
            run.failure_message = failure_message
            return changed

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="BOOKING_RECONCILIATION_REQUIRED",
            title="LINK 状态需要重新检查",
            detail=message,
            data={"error_code": error_code},
        )

    async def _record_unknown_booking_command(
        self,
        run_id: str,
        expected_version: int,
        error_code: str,
        message: str,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            run.booking_submission_uncertain = True
            run.status = ShipmentAgentRunStatus.WAITING_EXTERNAL
            run.current_stage = ShipmentAgentStage.AWAITING_BOOKING_RESULT
            run.pending_interrupt = None
            run.allowed_actions = [
                ShipmentAgentAction.RECHECK.value,
                ShipmentAgentAction.STOP.value,
            ]
            run.failure_code = error_code
            run.failure_message = message[:2_000]
            return True

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type=error_code,
            title="外部写命令结果未知",
            detail=(f"{message}。系统不会盲目重复发送；请先重新检查 LINK 当前状态。"),
            data={"result_unknown": True},
        )

    async def _record_load_version(
        self,
        run_id: str,
        expected_version: int,
        version_id: str,
        version_number: int,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            changed = (
                run.locked_load_version_id != version_id
                or run.locked_load_version_number != version_number
            )
            run.locked_load_version_id = version_id
            run.locked_load_version_number = version_number
            run.child_agent_snapshot = None
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.SYNCING_CASE
            run.pending_interrupt = None
            run.allowed_actions = []
            return changed or True

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="LOAD_PLAN_VERSION_READY",
            title="正式装载方案已就绪",
            detail=f"已读取正式装载版本 V{version_number}。",
            data={
                "locked_load_version_id": version_id,
                "locked_load_version_number": version_number,
                "version_number": version_number,
            },
        )

    async def _auto_select_only_resource(
        self,
        run_id: str,
        expected_version: int,
        resource_type: ShipmentResourceType,
        resource_number: str,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            if resource_type is ShipmentResourceType.LOAD_PLANNING_TASK:
                run.selected_load_task_number = resource_number
                self._reset_data_readiness_projection(run)
            else:
                run.selected_booking_application_number = resource_number
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.SYNCING_CASE
            run.pending_interrupt = None
            run.allowed_actions = []
            return True

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="ONLY_RESOURCE_AUTO_SELECTED",
            title="已选择唯一绑定资源",
            detail=f"{resource_type.value} 只有一个候选：{resource_number}。",
            data={
                "resource_type": resource_type.value,
                "resource_number": resource_number,
            },
        )

    async def _set_pending(
        self,
        run_id: str,
        expected_version: int,
        *,
        stage: ShipmentAgentStage,
        interrupt_type: ShipmentAgentInterruptType,
        title: str,
        message: str,
        details: dict[str, object],
        allowed: list[ShipmentAgentAction],
        waiting_status: ShipmentAgentRunStatus = ShipmentAgentRunStatus.WAITING_HUMAN,
    ) -> ShipmentAgentRunRead:
        pending = {
            "type": interrupt_type.value,
            "title": title,
            "description": message,
            "allowed_actions": [value.value for value in allowed],
            "details": details,
        }

        def mutate(run: ShipmentAgentRun) -> bool:
            changed = (
                run.pending_interrupt != pending
                or run.status is not waiting_status
                or run.current_stage is not stage
            )
            run.status = waiting_status
            run.current_stage = stage
            run.pending_interrupt = pending
            run.allowed_actions = [value.value for value in allowed]
            return changed

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="HUMAN_ACTION_REQUIRED",
            title=title,
            detail=message,
            data={"interrupt_type": interrupt_type.value, **details},
        )

    async def _set_running_stage(
        self,
        run_id: str,
        expected_version: int,
        stage: ShipmentAgentStage,
        event_type: str,
        title: str,
        detail: str,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            changed = (
                run.status is not ShipmentAgentRunStatus.RUNNING
                or run.current_stage is not stage
                or run.pending_interrupt is not None
            )
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = stage
            run.pending_interrupt = None
            run.allowed_actions = []
            run.failure_code = None
            run.failure_message = None
            return changed

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type=event_type,
            title=title,
            detail=detail,
            data={},
        )

    async def _mark_failed(
        self,
        run_id: str,
        expected_version: int,
        failure_code: str,
        failure_message: str,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            run.status = ShipmentAgentRunStatus.FAILED
            run.current_stage = ShipmentAgentStage.FAILED
            run.failure_code = failure_code
            run.failure_message = failure_message[:2_000]
            run.pending_interrupt = None
            run.allowed_actions = []
            run.completed_at = _utc_now_for_database()
            return True

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="RUN_FAILED",
            title="Shipment Agent 运行失败",
            detail=f"{failure_code}：{failure_message}",
            data={"failure_code": failure_code},
        )

    async def _mark_completed_from_existing_case(
        self,
        run_id: str,
        expected_version: int,
    ) -> ShipmentAgentRunRead:
        def mutate(run: ShipmentAgentRun) -> bool:
            run.status = ShipmentAgentRunStatus.COMPLETED
            run.current_stage = ShipmentAgentStage.COMPLETED
            run.pending_interrupt = None
            run.allowed_actions = []
            run.completed_at = _utc_now_for_database()
            return True

        return await self._mutate(
            run_id,
            expected_version,
            mutate,
            event_type="CASE_ALREADY_COMPLETED",
            title="检测到案件已经结案",
            detail="案件已由其他受控流程标记为 COMPLETED，总 Agent 同步结束。",
            data={},
        )

    async def _mutate(
        self,
        run_id: str,
        expected_version: int,
        mutate: Callable[[ShipmentAgentRun], bool],
        *,
        event_type: str,
        title: str,
        detail: str,
        data: dict[str, object],
    ) -> ShipmentAgentRunRead:
        """短事务更新运行；外部调用返回后若版本已变化，就保留更新后的当前事实。

        例如用户在 LINK HTTP 等待期间取消了运行，迟到的 HTTP 响应不能把 CANCELLED
        覆盖回 RUNNING。后续人工重新检查仍能从 LINK 读取真实结果。
        """

        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            async with session.begin():
                run = await repository.get(run_id, for_update=True)
                if run is None:
                    raise ShipmentAgentRunNotFoundError("Shipment Agent 运行不存在")
                if run.run_version != expected_version:
                    return self._to_read(run)
                changed = mutate(run)
                if changed:
                    self._refresh_automatic_wake_marker(run)
                    run.run_version += 1
                    run.events.append(
                        self._event(
                            run,
                            request_id=str(uuid4()),
                            event_type=event_type,
                            title=title,
                            detail=detail,
                            data=data,
                        )
                    )
                    await repository.flush()
        return await self.get(run_id)

    def _refresh_automatic_wake_marker(
        self,
        run: ShipmentAgentRun,
    ) -> None:
        """给每次业务事实提交写入一条有期限的控制面恢复标记。

        ``RUNNING`` 使用 ``RECOVER_RUNNING_STEP``，允许 Graph 在 HTTP 进程崩溃后从
        当前阶段幂等继续。其他状态使用 ``REPAIR_CONTROL_PROJECTION``：正常
        ``automation.project`` 会立即把它清除，或改成真正的 POLL 计划；只有业务事务
        已提交、进程却在投影前崩溃时，Worker 才会在宽限期后领取它，并且只补通知和
        下一次唤醒计划，不执行人工动作。
        """

        next_recovery_at = _utc_now_for_database() + timedelta(
            seconds=self.running_recovery_seconds
        )
        if run.status is ShipmentAgentRunStatus.RUNNING:
            run.next_wake_at = next_recovery_at
            run.wake_reason = "RECOVER_RUNNING_STEP"
            return
        run.next_wake_at = next_recovery_at
        run.wake_reason = SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON

    async def _get_orm_snapshot(self, run_id: str) -> ShipmentAgentRun:
        async with self.session_factory() as session:
            run = await ShipmentAgentRunRepository(session).get(run_id)
            if run is None:
                raise ShipmentAgentRunNotFoundError("Shipment Agent 运行不存在")
            # expunge 后对象及已经 selectin 加载的案件、绑定、事件仍可安全只读；关闭会话
            # 前不再触发隐式 lazy load。
            session.expunge(run)
            return run

    @staticmethod
    def _empty_data_readiness_snapshot(
        *,
        source_mode: ShipmentDataSourceMode = (ShipmentDataSourceMode.NOT_APPLICABLE),
    ) -> dict[str, object]:
        """构造尚未评估时仍可被前端稳定渲染的空投影。"""

        return {
            "policy_version": SHIPMENT_DATA_READINESS_POLICY_VERSION,
            "source_mode": source_mode.value,
            "snapshot_hash": None,
            "assessed_at": None,
            "ready_at": None,
            "issue_count": 0,
            "blocking_issue_count": 0,
            "issues": [],
        }

    @classmethod
    def _reset_data_readiness_projection(
        cls,
        run: ShipmentAgentRun,
    ) -> None:
        """装载资源变化后强制新一轮评估，禁止复用旧任务的 READY。"""

        run.data_readiness_status = ShipmentDataReadinessStatus.NOT_EVALUATED
        run.data_readiness_revision = 0
        run.data_readiness_snapshot = cls._empty_data_readiness_snapshot()
        run.data_readiness_checked_at = None

    @classmethod
    def _data_readiness_read(
        cls,
        run: ShipmentAgentRun,
    ) -> ShipmentDataReadinessRead:
        snapshot = (
            dict(run.data_readiness_snapshot)
            if isinstance(run.data_readiness_snapshot, dict)
            else cls._empty_data_readiness_snapshot()
        )
        if run.data_readiness_status is ShipmentDataReadinessStatus.LEGACY_BYPASSED:
            # _empty_data_readiness_snapshot 已带当前 V2 策略号，setdefault 无法覆盖，
            # 会把迁移前运行误标成已执行 V2 规则。历史兼容状态必须强制使用自己的标识。
            snapshot["policy_version"] = "LEGACY_BYPASSED"
        else:
            snapshot.setdefault(
                "policy_version",
                SHIPMENT_DATA_READINESS_POLICY_VERSION,
            )
        snapshot.setdefault(
            "source_mode",
            ShipmentDataSourceMode.NOT_APPLICABLE.value,
        )
        snapshot.setdefault("snapshot_hash", None)
        snapshot.setdefault(
            "assessed_at",
            run.data_readiness_checked_at,
        )
        snapshot.setdefault("ready_at", None)
        issues = snapshot.get("issues")
        if not isinstance(issues, list):
            issues = []
            snapshot["issues"] = issues
        snapshot.setdefault("issue_count", len(issues))
        snapshot.setdefault("blocking_issue_count", len(issues))
        return ShipmentDataReadinessRead.model_validate(
            {
                "status": run.data_readiness_status,
                "revision": run.data_readiness_revision,
                **snapshot,
            }
        )

    @staticmethod
    def _first_unselected_resource(
        run: ShipmentAgentRun,
    ) -> tuple[ShipmentResourceType, list[str]] | None:
        capabilities = set(run.required_capabilities)
        requirements = []
        if ShipmentAgentCapability.LOAD_PLANNING.value in capabilities:
            requirements.append(
                (
                    ShipmentResourceType.LOAD_PLANNING_TASK,
                    run.selected_load_task_number,
                )
            )
        if ShipmentAgentCapability.LINK_BOOKING.value in capabilities:
            requirements.append(
                (
                    ShipmentResourceType.LINK_BOOKING_APPLICATION,
                    run.selected_booking_application_number,
                )
            )
        for resource_type, selected in requirements:
            candidates = sorted(
                binding.resource_number
                for binding in run.shipment_case.resource_bindings
                if binding.resource_type is resource_type
            )
            if selected is None:
                return resource_type, candidates
            if selected not in candidates:
                return resource_type, candidates
        return None

    @staticmethod
    def _copy_link_snapshot(
        run: ShipmentAgentRun,
        snapshot,
        *,
        clear_failure: bool = True,
    ) -> bool:
        """复制 LINK 权威快照，并返回是否真的改变了持久化业务字段。

        ``_mutate`` 在回调返回 ``False`` 时不会增加 run_version 和 Trace，但 SQLAlchemy
        事务仍可能自动 flush 已赋的新值。因此调用方必须把本方法的返回值并入自己的
        ``changed`` 判定，避免“数据库字段已经变了，版本和审计轨迹却没变”的静默写入。
        reconciliation 分支可关闭错误清理，因为它会在同一事务写入新的错误投影。
        """

        values = {
            "booking_status": snapshot.application_status,
            "carrier_submission_status": snapshot.carrier_status,
            "booking_reference": snapshot.booking_reference,
        }
        if clear_failure:
            values.update(
                {
                    "failure_code": None,
                    "failure_message": None,
                }
            )
        changed = any(getattr(run, field) != value for field, value in values.items())
        for field, value in values.items():
            setattr(run, field, value)
        return changed

    async def _recover_start(
        self,
        case_number: str,
        key_hash: str,
        request_hash: str,
    ) -> ShipmentAgentRunRead:
        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            run = await repository.get_by_start_key(key_hash)
            if run is None:
                raise ShipmentAgentConflictError("Shipment Agent 并发启动冲突，请刷新后重试")
            if (
                run.start_request_hash != request_hash
                or run.shipment_case.case_number != case_number
            ):
                raise ShipmentAgentConflictError("Idempotency-Key 已用于另一份启动请求")
            return self._to_read(run)

    async def _recover_command(
        self,
        run_id: str,
        key_hash: str,
        command_type: ShipmentAgentCommandType,
        request_hash: str,
    ) -> ShipmentAgentRunRead:
        async with self.session_factory() as session:
            repository = ShipmentAgentRunRepository(session)
            command = await repository.get_command(key_hash)
            if command is None:
                raise ShipmentAgentConflictError("Shipment Agent 命令发生并发冲突，请刷新后重试")
            self._ensure_same_command(
                command,
                command_type,
                request_hash,
                expected_run_id=run_id,
            )
            run = await repository.get(run_id)
            if run is None:
                raise ShipmentAgentRunNotFoundError("Shipment Agent 运行不存在")
            return self._to_read(run)

    @staticmethod
    def _ensure_same_command(
        command: ShipmentAgentCommand,
        command_type: ShipmentAgentCommandType,
        request_hash: str,
        *,
        expected_run_id: str | None = None,
    ) -> None:
        if (
            command.command_type is not command_type
            or command.request_hash != request_hash
            or (expected_run_id is not None and command.run_id != expected_run_id)
        ):
            raise ShipmentAgentConflictError("Idempotency-Key 已用于另一份 Shipment Agent 命令")

    @staticmethod
    def _event(
        run: ShipmentAgentRun,
        *,
        request_id: str,
        event_type: str,
        title: str,
        detail: str,
        data: dict[str, object],
    ) -> ShipmentAgentEvent:
        return ShipmentAgentEvent(
            run_id=run.id,
            sequence=len(run.events) + 1,
            event_type=event_type,
            stage=run.current_stage,
            title=title,
            detail=detail,
            data=data,
            actor_subject=SYSTEM_DEMO_ACTOR_SUBJECT,
            actor_display_name=SYSTEM_DEMO_ACTOR_SUBJECT,
            actor_source=SYSTEM_DEMO_ACTOR_SOURCE,
            request_id=request_id,
        )

    @staticmethod
    def _to_read(run: ShipmentAgentRun) -> ShipmentAgentRunRead:
        pending = (
            ShipmentAgentInterruptRead.model_validate(run.pending_interrupt)
            if run.pending_interrupt is not None
            else None
        )
        child = (
            ShipmentAgentChildRead.model_validate(run.child_agent_snapshot)
            if run.child_agent_snapshot is not None
            else None
        )
        return ShipmentAgentRunRead(
            run_id=run.id,
            case_number=run.shipment_case.case_number,
            case_name=run.shipment_case.case_name,
            transport_scenario=run.shipment_case.transport_scenario.value,
            case_status=run.shipment_case.status.value,
            status=run.status,
            current_stage=run.current_stage,
            run_version=run.run_version,
            workflow_version=run.workflow_version,
            policy_version="SHIPMENT_COORDINATOR_POLICY_V1",
            started_case_revision=run.started_case_revision,
            last_observed_case_revision=run.shipment_case.revision,
            required_capabilities=[
                ShipmentAgentCapability(value) for value in run.required_capabilities
            ],
            scenario_policy_confirmed=run.scenario_policy_confirmed,
            selected_load_task_number=run.selected_load_task_number,
            selected_load_run_id=run.selected_load_run_id,
            locked_load_version_id=run.locked_load_version_id,
            locked_load_version_number=run.locked_load_version_number,
            selected_booking_application_number=(run.selected_booking_application_number),
            booking_status=run.booking_status,
            carrier_submission_status=run.carrier_submission_status,
            booking_reference=run.booking_reference,
            booking_submission_uncertain=run.booking_submission_uncertain,
            data_readiness=ShipmentAgentService._data_readiness_read(run),
            pending_interrupt=pending,
            allowed_actions=[ShipmentAgentAction(value) for value in run.allowed_actions],
            child_agent=child,
            next_wake_at=run.next_wake_at,
            wake_reason=run.wake_reason,
            failure_code=run.failure_code,
            failure_message=run.failure_message,
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            events=[
                ShipmentAgentEventRead(
                    id=event.id,
                    sequence=event.sequence,
                    event_type=event.event_type,
                    stage=event.stage,
                    title=event.title,
                    detail=event.detail,
                    data=event.data,
                    actor_subject=event.actor_subject,
                    actor_display_name=event.actor_display_name,
                    actor_source=event.actor_source,
                    request_id=event.request_id,
                    resource_type=(
                        ShipmentResourceType(str(event.data["resource_type"]))
                        if event.data.get("resource_type") is not None
                        else None
                    ),
                    resource_number=(
                        str(event.data["resource_number"])
                        if event.data.get("resource_number") is not None
                        else None
                    ),
                    occurred_at=event.occurred_at,
                )
                for event in run.events
            ],
            notifications=[
                ShipmentAgentNotificationRead.model_validate(notification)
                for notification in run.notifications
            ],
        )
