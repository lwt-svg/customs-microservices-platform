"""Shipment Coordinator Agent 的 LangGraph 编排。

Graph 只保存 ``run_id``、版本和路由游标；所有案件、资源、人工命令和 Trace 都由 MySQL
表保存。每次进入 ``sync_run`` 都重新读取业务事实，因此 checkpoint 丢失或落后时可以
安全重建。

人工节点使用 ``interrupt``；自动节点分别协调案件状态、装载子 Agent 和 LINK。外部
HTTP 永远由 Service 在数据库事务之外执行，Graph 重放也受运行阶段与命令幂等保护。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from app.models.shipment_agent_run import (
    SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.schemas.shipment_agent_run import (
    ShipmentAgentReconcile,
    ShipmentAgentResume,
    ShipmentAgentRunRead,
    ShipmentAgentStart,
)
from app.services.shipment_agent_automation import ShipmentAgentAutomationService
from app.services.shipment_agent_runs import (
    SHIPMENT_AGENT_WORKFLOW_VERSION,
    ShipmentAgentService,
)


class ShipmentWorkflowState(TypedDict, total=False):
    """checkpoint 中允许保存的最小路由状态。"""

    workflow_version: str
    run_id: str
    run_version: int
    run_status: str
    current_stage: str
    pending_command: dict[str, object] | None


class ShipmentWorkflowStateError(RuntimeError):
    """checkpoint 或当前阶段无法匹配请求动作。"""


class _WorkflowResumeCommand(BaseModel):
    """LangGraph ``Command(resume=...)`` 的内部信封。

    HTTP 请求体只包含 :class:`ShipmentAgentResume` 的业务字段，但工作流恢复时还必须把
    ``Idempotency-Key`` 一起交给 Service。两者因此包装成这个仅供 Graph 使用的对象；
    明确建模可以让 ``interrupt`` 在恢复节点运行前就拒绝缺字段或多字段的 checkpoint。
    """

    model_config = ConfigDict(extra="forbid")

    payload: ShipmentAgentResume
    idempotency_key: str = Field(min_length=1, max_length=128)


def _run_update(run: ShipmentAgentRunRead) -> dict[str, object]:
    return {
        # 迁移前的 V1 运行继续携带自己创建时的版本，不能在一次普通读取后被伪装成 V2。
        "workflow_version": run.workflow_version,
        "run_id": run.run_id,
        "run_version": run.run_version,
        "run_status": run.status.value,
        "current_stage": run.current_stage.value,
        "pending_command": None,
    }


class _ShipmentWorkflowNodes:
    """把 Graph 节点绑定到权威领域 Service。"""

    def __init__(self, service: ShipmentAgentService) -> None:
        self.service = service

    async def sync_run(self, state: ShipmentWorkflowState) -> dict[str, object]:
        run_id = state.get("run_id")
        if not run_id:
            raise ShipmentWorkflowStateError("Shipment 工作流缺少 run_id")
        return _run_update(await self.service.get(run_id))

    async def wait_for_human(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        run = await self.service.get(state["run_id"])
        if run.pending_interrupt is None:
            raise ShipmentWorkflowStateError("运行没有可恢复的人工中断")
        command = interrupt(
            {
                "run_id": run.run_id,
                "run_version": run.run_version,
                **run.pending_interrupt.model_dump(mode="json"),
            },
            response_schema=_WorkflowResumeCommand,
        )
        # LangGraph 会按 response_schema 返回已经验证过的 Pydantic 对象。checkpoint 只存
        # 普通 JSON，避免把应用内模型类写进 msgpack 并依赖不稳定的反序列化白名单。
        resume_command = _WorkflowResumeCommand.model_validate(command)
        return {"pending_command": resume_command.model_dump(mode="json")}

    async def apply_human_command(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        command = state.get("pending_command")
        if not isinstance(command, dict):
            raise ShipmentWorkflowStateError("人工恢复命令不是 JSON 对象")
        resume_command = _WorkflowResumeCommand.model_validate(command)
        run = await self.service.resume(
            state["run_id"],
            resume_command.payload,
            resume_command.idempotency_key,
        )
        return _run_update(run)

    async def evaluate(self, state: ShipmentWorkflowState) -> dict[str, object]:
        return _run_update(await self.service.evaluate(state["run_id"]))

    async def assess_data_readiness(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        return _run_update(await self.service.assess_data_readiness(state["run_id"]))

    async def coordinate_load(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        return _run_update(await self.service.coordinate_load(state["run_id"]))

    async def coordinate_booking(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        return _run_update(await self.service.coordinate_booking(state["run_id"]))

    async def activate_case(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        return _run_update(await self.service.activate_case(state["run_id"]))

    async def submit_booking(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        return _run_update(await self.service.submit_booking(state["run_id"]))

    async def retry_booking(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        return _run_update(await self.service.retry_booking(state["run_id"]))

    async def complete_case(
        self,
        state: ShipmentWorkflowState,
    ) -> dict[str, object]:
        return _run_update(await self.service.complete_case(state["run_id"]))


def _route(state: ShipmentWorkflowState) -> str:
    """根据 MySQL 投影选择下一节点；等待外部/子 Agent 时结束本次请求。"""

    status = ShipmentAgentRunStatus(str(state["run_status"]))
    if status in {
        ShipmentAgentRunStatus.COMPLETED,
        ShipmentAgentRunStatus.FAILED,
        ShipmentAgentRunStatus.CANCELLED,
        ShipmentAgentRunStatus.WAITING_RESOURCE,
        ShipmentAgentRunStatus.WAITING_EXTERNAL,
    }:
        return "end"
    if status is ShipmentAgentRunStatus.WAITING_HUMAN:
        return "human"

    stage = ShipmentAgentStage(str(state["current_stage"]))
    routes = {
        ShipmentAgentStage.SYNCING_CASE: "evaluate",
        ShipmentAgentStage.ASSESSING_DATA_READINESS: "data_readiness",
        ShipmentAgentStage.STARTING_LOAD_AGENT: "load",
        ShipmentAgentStage.AWAITING_BOOKING_RESULT: "booking",
        ShipmentAgentStage.AWAITING_CASE_ACTIVATION: "activate",
        ShipmentAgentStage.SUBMITTING_BOOKING: "submit",
        ShipmentAgentStage.AWAITING_BOOKING_RETRY_APPROVAL: "retry",
        ShipmentAgentStage.COMPLETING_CASE: "complete",
    }
    try:
        return routes[stage]
    except KeyError as error:
        raise ShipmentWorkflowStateError(f"RUNNING 状态无法路由阶段 {stage.value}") from error


def build_shipment_workflow(
    *,
    service: ShipmentAgentService,
    checkpointer: BaseCheckpointSaver[Any],
):
    """构建总 Agent 图；不创建第二套业务状态机。"""

    nodes = _ShipmentWorkflowNodes(service)
    builder = StateGraph(ShipmentWorkflowState)
    builder.add_node("sync_run", nodes.sync_run)
    builder.add_node("wait_for_human", nodes.wait_for_human)
    builder.add_node("apply_human_command", nodes.apply_human_command)
    builder.add_node("evaluate", nodes.evaluate)
    builder.add_node("assess_data_readiness", nodes.assess_data_readiness)
    builder.add_node("coordinate_load", nodes.coordinate_load)
    builder.add_node("coordinate_booking", nodes.coordinate_booking)
    builder.add_node("activate_case", nodes.activate_case)
    builder.add_node("submit_booking", nodes.submit_booking)
    builder.add_node("retry_booking", nodes.retry_booking)
    builder.add_node("complete_case", nodes.complete_case)

    builder.add_edge(START, "sync_run")
    builder.add_conditional_edges(
        "sync_run",
        _route,
        {
            "human": "wait_for_human",
            "evaluate": "evaluate",
            "data_readiness": "assess_data_readiness",
            "load": "coordinate_load",
            "booking": "coordinate_booking",
            "activate": "activate_case",
            "submit": "submit_booking",
            "retry": "retry_booking",
            "complete": "complete_case",
            "end": END,
        },
    )
    builder.add_edge("wait_for_human", "apply_human_command")
    for node_name in (
        "apply_human_command",
        "evaluate",
        "assess_data_readiness",
        "coordinate_load",
        "coordinate_booking",
        "activate_case",
        "submit_booking",
        "retry_booking",
        "complete_case",
    ):
        builder.add_edge(node_name, "sync_run")
    return builder.compile(
        checkpointer=checkpointer,
        name="shipment-coordinator-workflow-v2",
    )


class ShipmentWorkflowOrchestrator:
    """FastAPI 使用的总 Agent Facade。"""

    def __init__(
        self,
        *,
        service: ShipmentAgentService,
        checkpointer: BaseCheckpointSaver[Any],
        automation: ShipmentAgentAutomationService | None = None,
    ) -> None:
        self.service = service
        self.automation = automation or ShipmentAgentAutomationService(service.session_factory)
        self.graph = build_shipment_workflow(
            service=service,
            checkpointer=checkpointer,
        )

    @staticmethod
    def _config(run_id: str) -> dict[str, object]:
        # 前缀防止总 Agent UUID 与装载 Agent UUID 极端碰撞时共用一条 checkpoint thread。
        return {
            "configurable": {"thread_id": f"shipment:{run_id}"},
            "recursion_limit": 64,
        }

    async def start(
        self,
        case_number: str,
        payload: ShipmentAgentStart,
        idempotency_key: str,
    ) -> ShipmentAgentRunRead:
        run = await self.service.start(case_number, payload, idempotency_key)
        await self.synchronize(run.run_id)
        await self.automation.project(run.run_id)
        return await self.service.get(run.run_id)

    async def synchronize(self, run_id: str) -> None:
        """从业务数据库重建/刷新 Graph 并推进到下一个等待点。"""

        await self.graph.ainvoke(
            {
                "workflow_version": SHIPMENT_AGENT_WORKFLOW_VERSION,
                "run_id": run_id,
                "pending_command": None,
            },
            config=self._config(run_id),
            durability="sync",
        )

    async def resume(
        self,
        run_id: str,
        payload: ShipmentAgentResume,
        idempotency_key: str,
    ) -> ShipmentAgentRunRead:
        current = await self.service.get(run_id)
        if current.status is ShipmentAgentRunStatus.WAITING_HUMAN:
            await self.synchronize(run_id)
            await self.graph.ainvoke(
                Command(
                    resume={
                        "payload": payload.model_dump(mode="json"),
                        "idempotency_key": idempotency_key,
                    }
                ),
                config=self._config(run_id),
                durability="sync",
            )
        else:
            # 非 interrupt 状态也先交给 Service：同一 Idempotency-Key 的审批网络重试
            # 必须返回已经成功的命令；全新的非法动作仍会被 allowed_actions 拒绝。
            await self.service.resume(run_id, payload, idempotency_key)
            await self.synchronize(run_id)
        await self.automation.project(run_id)
        return await self.service.get(run_id)

    async def reconcile(
        self,
        run_id: str,
        payload: ShipmentAgentReconcile,
        idempotency_key: str,
    ) -> ShipmentAgentRunRead:
        await self.service.reconcile(run_id, payload, idempotency_key)
        await self.synchronize(run_id)
        await self.automation.project(run_id)
        return await self.service.get(run_id)

    async def automatic_wake(self, run_id: str) -> ShipmentAgentRunRead:
        """由数据库租约 Worker 观察等待状态，并在真实变化后继续 LangGraph。

        与人工 ``reconcile`` 不同，本入口不写命令台账、不把等待状态先强行改成 RUNNING，
        因而普通轮询没有发现变化时不会增加 run_version 或 Trace。提交 LINK 和重试 DEAD
        仍只能通过人工 interrupt 批准。
        """

        current = await self.service.get(run_id)
        changed = current

        if current.wake_reason == SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON:
            # 该 reason 只表示“业务事实已提交，但通知/下次唤醒投影可能未提交”。尤其当
            # 当前状态是 WAITING_HUMAN 或终态时，Worker 绝不能据此恢复业务 Graph；
            # 返回当前事实后，调用方只会执行 automation.project 并清除修复标记。
            return current

        if current.status is ShipmentAgentRunStatus.RUNNING:
            await self.synchronize(run_id)
            return await self.service.get(run_id)

        if (
            current.status is ShipmentAgentRunStatus.WAITING_EXTERNAL
            and current.current_stage is ShipmentAgentStage.AWAITING_BOOKING_RESULT
        ):
            changed = await self.service.coordinate_booking(
                run_id,
                allow_waiting_poll=True,
            )
        elif (
            current.status is ShipmentAgentRunStatus.WAITING_RESOURCE
            and current.current_stage
            in {
                ShipmentAgentStage.AWAITING_LOAD_CONSTRAINTS,
                ShipmentAgentStage.AWAITING_LOAD_REVIEW,
                ShipmentAgentStage.AWAITING_LOAD_RELEASE,
                ShipmentAgentStage.AWAITING_LOAD_REPLAN,
            }
        ):
            changed = await self.service.coordinate_load(
                run_id,
                allow_waiting_poll=True,
            )
        elif (
            current.status is ShipmentAgentRunStatus.WAITING_RESOURCE
            and current.current_stage
            in {
                ShipmentAgentStage.AWAITING_RESOURCE_BINDING,
                ShipmentAgentStage.AWAITING_BOOKING_REPLACEMENT,
            }
        ):
            changed = await self.service.poll_waiting_resource(run_id)

        if changed.status is ShipmentAgentRunStatus.RUNNING:
            await self.synchronize(run_id)
        return await self.service.get(run_id)
