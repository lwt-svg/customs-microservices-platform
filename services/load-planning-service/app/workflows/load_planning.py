"""无大模型的 LangGraph 装载规划编排层。

本模块刻意不实现第二套业务状态机：

* ``StatefulPalletAgentService`` 仍是 AgentRun、问题、候选方案和审批状态的事实来源；
* ``LoadPlanVersionService`` 仍是正式版本、快照、哈希和版本号的事实来源；
* LangGraph 只负责根据上述权威状态选择下一个人工中断点，并在恢复后调用原有 Service。

这样做有两个直接好处：

1. 即使 checkpoint 丢失，也能从 MySQL 中的 AgentRun/LoadPlanVersion 重建流程位置；
2. Graph 节点重放时，仍受原有幂等键、乐观版本和数据库行锁保护。

第一阶段没有 LLM 节点。自然语言解释以后可以作为可关闭的只读增强加入，但几何坐标、
承重校验、审批和正式发布永远不能由模型自行决定。

初学者可以先把整张图理解成四组“等待节点 + 执行节点”：

.. code-block:: text

    START
      -> sync_agent_run（先读 MySQL，判断现在走哪条路）
          -> wait_for_constraints --人工补充约束--> resume_agent_run --+
          -> wait_for_review ------人工批准/驳回--> decide_agent_run -----+--> sync_agent_run
          -> wait_for_release -----人工正式发布--> lock_plan_version ----+
          -> wait_for_replan ------人工要求重算--> replan_agent_run -> END
          -> END（已经存在正式版本）

``interrupt(...)`` 可以理解成一个可持久化的“暂停并等待输入”。第一次执行到它时，
LangGraph 把暂停位置和返回给调用方的数据写入 checkpoint；以后收到
``Command(resume=...)`` 时，会从这个节点重新执行，且本次 ``interrupt(...)`` 表达式
返回 ``resume`` 中携带的值。因此 wait 节点在 interrupt 之前只能做可安全重放的读取，
真正的数据库写操作统一放在它后面的执行节点。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_run import AgentRunStatus
from app.schemas.agent_run import (
    AgentRunCreate,
    AgentRunDecision,
    AgentRunRead,
    AgentRunReplan,
    AgentRunResume,
)
from app.schemas.plan_version import LoadPlanLockRequest, LoadPlanVersionRead
from app.services.agent_runs import StatefulPalletAgentService
from app.services.plan_versions import LoadPlanVersionService

WORKFLOW_VERSION = "LOAD_PLANNING_LANGGRAPH_V1"


class LoadPlanningWorkflowStage(StrEnum):
    """对 API/测试稳定的编排阶段。

    这些阶段不是新的业务状态。它们只是把 AgentRun 的权威状态翻译成“页面下一步应该
    等待哪一种人工动作”。例如 ``AWAITING_REVIEW`` 对应 AgentRun 的
    ``READY_FOR_REVIEW``，而 ``LOCKED`` 来自独立的 LoadPlanVersion。
    """

    AWAITING_CONSTRAINTS = "AWAITING_CONSTRAINTS"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    AWAITING_RELEASE = "AWAITING_RELEASE"
    AWAITING_REPLAN = "AWAITING_REPLAN"
    LOCKED = "LOCKED"


class LoadingWorkflowState(TypedDict, total=False):
    """LangGraph checkpoint 中允许保存的最小状态。

    State 只保存稳定业务 ID、版本号和路由所需的少量摘要。完整箱子坐标、方案、任务快照、
    PDF/Excel 原文都已经有自己的权威存储，不能复制进 checkpoint 形成第二份事实。

    ``total=False`` 表示 Graph 初始化时不要求一次传入所有键。节点会随着流程推进逐步补齐
    字段；真正读取关键字段时仍会在代码中显式校验，不能把“类型上可缺省”理解成
    “业务上永远可缺省”。
    """

    # 用于定位业务事实的稳定引用。run_id 同时也是 LangGraph thread_id。
    workflow_version: str
    task_number: str
    run_id: str

    # 从 AgentRunRead 投影出来的轻量状态。每次进入 sync_agent_run 都会用 MySQL 刷新。
    run_version: int
    run_status: str
    current_step: str
    current_stage: str
    pending_question_keys: list[str]
    selected_plan_code: str | None
    failure_code: str | None
    failure_message: str | None

    # 只有 APPROVED 阶段才需要查询正式版本链；其他阶段保持 None，避免为每次同步加载
    # 可能很大的正式方案快照。
    current_formal_version_number: int | None
    locked_version_id: str | None
    locked_version_number: int | None

    # replan 会创建新的 AgentRun。父 Graph 只保存 child ID 后结束；子运行使用自己的
    # run_id 作为 thread_id，从而继续保持“一条 AgentRun 对应一个 Graph thread”。
    child_run_id: str | None

    # interrupt 恢复后短暂保存本次命令。写节点消费后立即清空，避免下一次新 Graph run
    # 错把历史命令当成新的人工动作。
    pending_command: dict[str, object] | None


class LoadPlanningWorkflowSnapshot(BaseModel):
    """给上层 Facade、测试和未来 API 使用的轻量工作流视图。

    它不是业务详情 DTO。页面若要查看全部候选方案和 3D 坐标，仍应读取
    ``AgentRunRead``；这里主要回答“Graph 停在哪里、下一步等什么动作”。
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    workflow_version: str
    stage: LoadPlanningWorkflowStage
    run_id: str
    run_version: int
    run_status: AgentRunStatus
    current_step: str
    next_nodes: list[str]
    interrupt: dict[str, object] | None
    selected_plan_code: str | None
    current_formal_version_number: int | None
    locked_version_id: str | None
    locked_version_number: int | None
    child_run_id: str | None


class LoadPlanningWorkflowStateError(RuntimeError):
    """checkpoint 与当前请求动作不匹配或内部状态不完整。"""


class _WorkflowReplanCommand(BaseModel):
    """Graph 恢复时同时传递业务请求和 HTTP 幂等键。

    ``Idempotency-Key`` 不是装载业务字段，所以它不进入 ``AgentRunReplan``。Graph 仍需
    将它和请求一起送到现有 ``replan()``，以保证 checkpoint 重放不会创建多个子运行。
    """

    model_config = ConfigDict(extra="forbid")

    request: AgentRunReplan
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


def _run_reference_update(run: AgentRunRead) -> dict[str, object]:
    """把完整 AgentRunRead 压缩成 checkpoint 所需的稳定引用。

    单独保留这个转换函数有两个目的：

    * 所有写节点都用同一份投影规则，避免某个分支漏更新 ``run_version``；
    * 明确限制 checkpoint 边界，防止以后顺手把完整 plans/placements 写进去。
    """

    return {
        "task_number": run.task_number,
        "run_id": run.run_id,
        "run_version": run.run_version,
        "run_status": run.status.value,
        "current_step": run.current_step,
        "pending_question_keys": [question.key for question in run.questions],
        "selected_plan_code": run.selected_plan_code,
        "failure_code": run.failure_code,
        "failure_message": run.failure_message,
    }


class _LoadingWorkflowNodes:
    """绑定现有领域 Service 的 LangGraph Node 集合。

    Service 通过普通 Python 引用注入 Node，但永远不会进入 State，因此 AsyncSession、
    数据库连接等不可序列化对象不会写入 checkpoint。

    每项人工动作都拆成两个节点：

    * ``wait_for_*``：组织提示数据并调用 ``interrupt``，本身不修改业务数据；
    * 动词节点（resume/decide/replan/lock）：验证恢复值，再调用原领域 Service 写库。

    这种拆分让 checkpoint 重放边界清楚，也方便测试断言流程究竟停在哪一步。
    """

    def __init__(
        self,
        agent_service: StatefulPalletAgentService,
        plan_version_service: LoadPlanVersionService,
    ) -> None:
        self.agent_service = agent_service
        self.plan_version_service = plan_version_service

    async def sync_agent_run(self, state: LoadingWorkflowState) -> dict[str, object]:
        """每次路由前都从业务数据库重新读取权威状态。

        checkpoint 可能比 MySQL 旧，例如业务事务已经提交、进程却在写 checkpoint 前退出。
        因此 Graph 绝不能仅凭旧 checkpoint 猜测当前业务阶段。
        """

        run_id = state.get("run_id")
        if not run_id:
            raise LoadPlanningWorkflowStateError("工作流缺少 run_id，无法同步 AgentRun")

        run = await self.agent_service.get(run_id)
        update = _run_reference_update(run)
        update.update(
            {
                "workflow_version": WORKFLOW_VERSION,
                "pending_command": None,
                "child_run_id": None,
                "current_formal_version_number": None,
                "locked_version_id": None,
                "locked_version_number": None,
            }
        )

        if run.status is AgentRunStatus.APPROVED:
            # AgentRun 在正式发布后仍保持 APPROVED。必须另外查询 LoadPlanVersion，
            # 否则服务重启后会把已经发布的运行再次路由到“等待正式发布”。
            versions = await self.plan_version_service.list(run.task_number)
            current_version_number = max(
                (version.version_number for version in versions),
                default=0,
            )
            locked = next(
                (version for version in versions if version.source_run_id == run.run_id),
                None,
            )
            update["current_formal_version_number"] = current_version_number
            if locked is not None:
                update.update(
                    {
                        "current_stage": LoadPlanningWorkflowStage.LOCKED.value,
                        "locked_version_id": locked.version_id,
                        "locked_version_number": locked.version_number,
                    }
                )
                return update

        stage_by_status = {
            AgentRunStatus.AWAITING_INPUT: (LoadPlanningWorkflowStage.AWAITING_CONSTRAINTS),
            AgentRunStatus.READY_FOR_REVIEW: (LoadPlanningWorkflowStage.AWAITING_REVIEW),
            AgentRunStatus.APPROVED: LoadPlanningWorkflowStage.AWAITING_RELEASE,
            AgentRunStatus.REJECTED: LoadPlanningWorkflowStage.AWAITING_REPLAN,
            AgentRunStatus.FAILED: LoadPlanningWorkflowStage.AWAITING_REPLAN,
        }
        try:
            update["current_stage"] = stage_by_status[run.status].value
        except KeyError as error:  # pragma: no cover - 枚举新增时的防御性保护
            raise LoadPlanningWorkflowStateError(f"不支持的 AgentRun 状态：{run.status}") from error
        return update

    async def wait_for_constraints(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """暂停并返回结构化缺失约束；恢复值沿用 AgentRunResume 契约。

        注意 ``interrupt`` 第一次执行时会暂停，不会立即执行下面的 ``return``。收到
        ``Command(resume=<AgentRunResume JSON>)`` 后，本节点从头重放，``interrupt``
        才把该 JSON 作为 ``command`` 返回，随后交给 ``resume_agent_run`` 消费。
        """

        run = await self.agent_service.get(state["run_id"])
        command = interrupt(
            {
                "interrupt_type": "MISSING_CONSTRAINTS",
                "task_number": run.task_number,
                "run_id": run.run_id,
                "run_version": run.run_version,
                "questions": [question.model_dump(mode="json") for question in run.questions],
            },
            response_schema=AgentRunResume,
        )
        return {"pending_command": command}

    async def resume_agent_run(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """消费恢复命令并调用已有 resume。

        Pydantic 在这里再次校验 checkpoint 中的恢复值。问题是否完整、乐观版本是否匹配、
        何时运行装托算法以及如何写审计事件，仍全部由领域 Service 完成。
        """

        payload = AgentRunResume.model_validate(state.get("pending_command"))
        run = await self.agent_service.resume(state["run_id"], payload)
        return {
            **_run_reference_update(run),
            "pending_command": None,
        }

    async def wait_for_review(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """暂停等待人工评审，只把方案摘要写入 interrupt。

        完整 XYZ 坐标继续由现有 AgentRun API 提供给 3D 页面，避免每个 checkpoint
        重复保存大体积坐标。这里的摘要只用于告诉审批人“有哪些候选项可选”，不是正式
        方案快照。
        """

        run = await self.agent_service.get(state["run_id"])
        plan_summaries = [
            {
                "plan_code": plan.plan_code,
                "strategy_code": plan.strategy_code.value,
                "strategy_name": plan.strategy_name,
                "feasible": plan.feasible,
                "score": plan.score,
                "summary": plan.summary,
                "metrics": plan.metrics.model_dump(mode="json"),
                "warnings": list(plan.warnings),
                "algorithm_version": plan.algorithm_version,
                "score_version": plan.score_version,
            }
            for plan in run.plans
        ]
        command = interrupt(
            {
                "interrupt_type": "PLAN_REVIEW",
                "task_number": run.task_number,
                "run_id": run.run_id,
                "run_version": run.run_version,
                "plans": plan_summaries,
            },
            response_schema=AgentRunDecision,
        )
        return {"pending_command": command}

    async def decide_agent_run(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """消费人工评审命令并调用已有 decide。

        Graph 只负责把人工提交的 ``AgentRunDecision`` 送到 Service，不能根据分数自行
        批准，也不能在这里绕过 Service 的版本冲突和方案可行性校验。
        """

        payload = AgentRunDecision.model_validate(state.get("pending_command"))
        run = await self.agent_service.decide(state["run_id"], payload)
        return {
            **_run_reference_update(run),
            "pending_command": None,
        }

    async def wait_for_replan(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """暂停等待人工决定重新规划。

        这里不自动创建子运行。只有收到明确请求后，下一 Node 才调用现有 replan()。
        """

        run = await self.agent_service.get(state["run_id"])
        command = interrupt(
            {
                "interrupt_type": "REPLAN_REQUIRED",
                "task_number": run.task_number,
                "run_id": run.run_id,
                "run_version": run.run_version,
                "run_status": run.status.value,
                "failure_code": run.failure_code,
                "failure_message": run.failure_message,
            },
            response_schema=_WorkflowReplanCommand,
        )
        return {"pending_command": command}

    async def replan_agent_run(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """调用已有 replan，保留父子运行和原有幂等语义。

        这里故意只把 ``child_run_id`` 写回父 checkpoint。子运行的业务状态已由 Service
        写入 MySQL，稍后 Facade 会用 child run_id 初始化另一条 Graph thread；父子流程
        因而不会共用一份可变 State。
        """

        command = _WorkflowReplanCommand.model_validate(state.get("pending_command"))
        child = await self.agent_service.replan(
            state["run_id"],
            command.request,
            command.idempotency_key,
        )
        return {
            "child_run_id": child.run_id,
            "pending_command": None,
        }

    async def wait_for_release(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """批准与正式发布分离；恢复值沿用 LoadPlanLockRequest。

        APPROVED 只表示人工选中了候选方案。正式版本号、不可变快照和哈希必须由第二个
        明确动作生成，所以审批后还要在此处再次暂停。
        """

        run = await self.agent_service.get(state["run_id"])
        command = interrupt(
            {
                "interrupt_type": "FORMAL_RELEASE",
                "task_number": run.task_number,
                "run_id": run.run_id,
                "run_version": run.run_version,
                "selected_plan_code": run.selected_plan_code,
                "current_formal_version_number": (state.get("current_formal_version_number") or 0),
            },
            response_schema=LoadPlanLockRequest,
        )
        return {"pending_command": command}

    async def lock_plan_version(
        self,
        state: LoadingWorkflowState,
    ) -> dict[str, object]:
        """调用已有 lock；快照、哈希、版本号和锁定审计不在 Graph 中重写。"""

        payload = LoadPlanLockRequest.model_validate(state.get("pending_command"))
        version = await self.plan_version_service.lock(state["run_id"], payload)
        return {
            "locked_version_id": version.version_id,
            "locked_version_number": version.version_number,
            "pending_command": None,
        }


def _route_synced_state(state: LoadingWorkflowState) -> str:
    """把同步后的业务阶段转换成条件边名称。

    返回值不是节点名，而是 ``add_conditional_edges`` 映射表的键。路由仅依赖枚举值，
    不解析错误文案、审批备注等自然语言，也不调用 LLM。
    """

    stage = state.get("current_stage")
    routes = {
        LoadPlanningWorkflowStage.AWAITING_CONSTRAINTS.value: "constraints",
        LoadPlanningWorkflowStage.AWAITING_REVIEW.value: "review",
        LoadPlanningWorkflowStage.AWAITING_RELEASE.value: "release",
        LoadPlanningWorkflowStage.AWAITING_REPLAN.value: "replan",
        LoadPlanningWorkflowStage.LOCKED.value: "locked",
    }
    try:
        return routes[stage]
    except KeyError as error:
        raise LoadPlanningWorkflowStateError(f"无法根据工作流阶段路由：{stage!r}") from error


def build_load_planning_workflow(
    *,
    agent_service: StatefulPalletAgentService,
    plan_version_service: LoadPlanVersionService,
    checkpointer: BaseCheckpointSaver[Any],
):
    """组装无 LLM 的第一版装载规划 StateGraph。

    函数不自行创建 checkpointer，便于：

    * 单元测试注入 ``InMemorySaver``；
    * 本地应用注入 SQLite Saver；
    * 生产以后替换为适合多实例的持久化 Saver。

    图中三个动作完成后会回到 ``sync_agent_run``，因为写库后的状态仍应由 MySQL 决定：
    补约束后可能继续追问，也可能进入评审；批准后进入发布，驳回后进入重规划；发布后
    通过正式版本记录识别为 ``LOCKED``。只有 replan 例外——父 thread 到此结束，随后由
    Facade 为新 child run 建立自己的 thread。
    """

    nodes = _LoadingWorkflowNodes(agent_service, plan_version_service)
    builder = StateGraph(LoadingWorkflowState)
    builder.add_node("sync_agent_run", nodes.sync_agent_run)
    builder.add_node("wait_for_constraints", nodes.wait_for_constraints)
    builder.add_node("resume_agent_run", nodes.resume_agent_run)
    builder.add_node("wait_for_review", nodes.wait_for_review)
    builder.add_node("decide_agent_run", nodes.decide_agent_run)
    builder.add_node("wait_for_replan", nodes.wait_for_replan)
    builder.add_node("replan_agent_run", nodes.replan_agent_run)
    builder.add_node("wait_for_release", nodes.wait_for_release)
    builder.add_node("lock_plan_version", nodes.lock_plan_version)

    builder.add_edge(START, "sync_agent_run")
    builder.add_conditional_edges(
        "sync_agent_run",
        _route_synced_state,
        {
            "constraints": "wait_for_constraints",
            "review": "wait_for_review",
            "release": "wait_for_release",
            "replan": "wait_for_replan",
            "locked": END,
        },
    )
    builder.add_edge("wait_for_constraints", "resume_agent_run")
    builder.add_edge("resume_agent_run", "sync_agent_run")
    builder.add_edge("wait_for_review", "decide_agent_run")
    builder.add_edge("decide_agent_run", "sync_agent_run")
    builder.add_edge("wait_for_replan", "replan_agent_run")
    builder.add_edge("replan_agent_run", END)
    builder.add_edge("wait_for_release", "lock_plan_version")
    builder.add_edge("lock_plan_version", "sync_agent_run")

    return builder.compile(
        checkpointer=checkpointer,
        name="load-planning-workflow-v1",
    )


class LoadPlanningWorkflowOrchestrator:
    """现有 API 当前实际使用的 LangGraph Facade。

    所有公开方法仍接收、返回现有 Pydantic 契约。Java BFF 与 Vue 不需要认识 thread_id，
    因为一条 AgentRun 的 ``run_id`` 就是它的 Graph ``thread_id``。

    可以把这个类看成“API 与 Graph 之间的适配器”：

    1. API 仍调用熟悉的 ``create/resume/decide/replan/lock``；
    2. Facade 先让 Graph 对齐 MySQL，再用 ``Command(resume=...)`` 唤醒正确 interrupt；
    3. Graph 的写节点调用原领域 Service；
    4. Facade 最终仍返回原有业务 DTO，而不是把 LangGraph 内部对象暴露给上层。
    """

    def __init__(
        self,
        *,
        agent_service: StatefulPalletAgentService,
        plan_version_service: LoadPlanVersionService,
        checkpointer: BaseCheckpointSaver[Any],
        enabled: bool = True,
    ) -> None:
        self.agent_service = agent_service
        self.plan_version_service = plan_version_service
        self.enabled = enabled
        self.graph = build_load_planning_workflow(
            agent_service=agent_service,
            plan_version_service=plan_version_service,
            checkpointer=checkpointer,
        )

    @staticmethod
    def _config(run_id: str) -> dict[str, dict[str, str]]:
        """run_id 是稳定、非敏感且可从业务历史恢复的 thread_id。"""

        return {"configurable": {"thread_id": run_id}}

    async def create(
        self,
        task_number: str,
        payload: AgentRunCreate,
        idempotency_key: str | None = None,
    ) -> AgentRunRead:
        """创建权威 AgentRun，再为它建立可恢复的 Graph checkpoint。

        创建必须先发生，因为现有 Service 才负责产生 run_id 和处理创建幂等竞争。若
        checkpoint 写入失败，业务运行仍可在下次 ``synchronize`` 时重建，不能反向回滚
        已经提交的 AgentRun。
        """

        run = await self.agent_service.create(task_number, payload, idempotency_key)
        if self.enabled:
            await self.synchronize(run.run_id)
        return run

    async def restore_latest(self, task_number: str) -> AgentRunRead | None:
        """从业务历史恢复最新运行，不依赖浏览器缓存或 checkpoint 映射表。"""

        history = await self.agent_service.list_history(task_number)
        if not history:
            return None
        latest = history[0]
        if self.enabled:
            await self.synchronize(latest.run_id)
        return latest

    async def synchronize(self, run_id: str) -> LoadPlanningWorkflowSnapshot:
        """以 MySQL 权威状态重新开始一轮同步并停在正确 interrupt。

        同一个 thread 的新输入会与旧 checkpoint State 合并，所以这里显式清空上轮的
        临时命令和结果字段。``sync_agent_run`` 随后重读数据库，条件路由最终停在当前
        应等待的人工节点。``durability="sync"`` 要求 checkpoint 持久化完成后才返回，
        避免 HTTP 已成功但暂停位置尚未落盘。
        """

        await self.graph.ainvoke(
            {
                "workflow_version": WORKFLOW_VERSION,
                "run_id": run_id,
                # 显式清理上一轮命令结果；LangGraph 同一 thread 的新输入会合并旧 State。
                "pending_command": None,
                "child_run_id": None,
                "locked_version_id": None,
                "locked_version_number": None,
            },
            config=self._config(run_id),
            durability="sync",
        )
        return await self.get_snapshot(run_id)

    async def get_snapshot(self, run_id: str) -> LoadPlanningWorkflowSnapshot:
        """读取 checkpoint 视图；业务详情仍应通过原 AgentRun API 查询。

        ``checkpoint.next`` 表示下次将执行（或恢复）的节点；``checkpoint.interrupts``
        保存当前暂停节点公开给调用方的结构化提示。这里只取第一个 interrupt，因为本图
        是串行人工流程，同一 thread 在任一时刻最多等待一个动作。
        """

        checkpoint = await self.graph.aget_state(self._config(run_id))
        values = checkpoint.values
        if not values or values.get("run_id") != run_id:
            raise LoadPlanningWorkflowStateError(f"运行 {run_id} 尚未初始化 LangGraph checkpoint")
        raw_stage = values.get("current_stage")
        if not isinstance(raw_stage, str):
            raise LoadPlanningWorkflowStateError("checkpoint 缺少 current_stage")
        interrupts = checkpoint.interrupts
        raw_interrupt = interrupts[0].value if interrupts else None
        if raw_interrupt is not None and not isinstance(raw_interrupt, dict):
            raise LoadPlanningWorkflowStateError("checkpoint interrupt 不是结构化对象")

        return LoadPlanningWorkflowSnapshot(
            thread_id=run_id,
            workflow_version=str(values.get("workflow_version") or WORKFLOW_VERSION),
            stage=LoadPlanningWorkflowStage(raw_stage),
            run_id=run_id,
            run_version=int(values["run_version"]),
            run_status=AgentRunStatus(str(values["run_status"])),
            current_step=str(values["current_step"]),
            next_nodes=list(checkpoint.next),
            interrupt=raw_interrupt,
            selected_plan_code=(
                str(values["selected_plan_code"])
                if values.get("selected_plan_code") is not None
                else None
            ),
            current_formal_version_number=(
                int(values["current_formal_version_number"])
                if values.get("current_formal_version_number") is not None
                else None
            ),
            locked_version_id=(
                str(values["locked_version_id"])
                if values.get("locked_version_id") is not None
                else None
            ),
            locked_version_number=(
                int(values["locked_version_number"])
                if values.get("locked_version_number") is not None
                else None
            ),
            child_run_id=(
                str(values["child_run_id"]) if values.get("child_run_id") is not None else None
            ),
        )

    async def resume(
        self,
        run_id: str,
        payload: AgentRunResume,
    ) -> AgentRunRead:
        """从缺失约束 interrupt 恢复；完成后可能再次追问或进入方案评审。

        先 ``synchronize`` 是为了处理“业务事务已提交、HTTP 响应丢失后客户端重试”。
        如果仍在等待约束，就恢复 Graph；如果数据库已经前进，则把请求交给原 Service
        做幂等/冲突判断，避免在编排层复制一套重试规则。
        """

        if not self.enabled:
            return await self.agent_service.resume(run_id, payload)

        snapshot = await self.synchronize(run_id)
        if snapshot.stage is LoadPlanningWorkflowStage.AWAITING_CONSTRAINTS:
            await self.graph.ainvoke(
                Command(resume=payload.model_dump(mode="json")),
                config=self._config(run_id),
                durability="sync",
            )
        else:
            # 网络重试可能发生在业务成功、响应丢失以后。此时 Graph 已路由到下一阶段，
            # 仍让原 Service 判断“相同请求幂等返回”或“不同请求冲突”，不在此复制规则。
            await self.agent_service.resume(run_id, payload)
            await self.synchronize(run_id)
        return await self.agent_service.get(run_id)

    async def decide(
        self,
        run_id: str,
        payload: AgentRunDecision,
    ) -> AgentRunRead:
        """从方案评审 interrupt 恢复并调用现有审批 Service。

        与 ``resume`` 一样，Graph 只处理“确实停在评审节点”的正常路径；迟到或重复的
        请求仍交给业务 Service 按乐观版本和历史事件判断。
        """

        if not self.enabled:
            return await self.agent_service.decide(run_id, payload)

        snapshot = await self.synchronize(run_id)
        if snapshot.stage is LoadPlanningWorkflowStage.AWAITING_REVIEW:
            await self.graph.ainvoke(
                Command(resume=payload.model_dump(mode="json")),
                config=self._config(run_id),
                durability="sync",
            )
        else:
            await self.agent_service.decide(run_id, payload)
            await self.synchronize(run_id)
        return await self.agent_service.get(run_id)

    async def replan(
        self,
        run_id: str,
        payload: AgentRunReplan,
        idempotency_key: str | None = None,
    ) -> AgentRunRead:
        """从驳回/失败 interrupt 创建子运行，并为子运行初始化独立 thread。

        父 thread 的最终产物是 ``child_run_id``。创建成功后必须再调用
        ``synchronize(child_run_id)``，让新运行依据自身状态停在“补约束”或“方案评审”，
        这也是为什么一次 HTTP replan 会涉及前后两条 thread。
        """

        if not self.enabled:
            return await self.agent_service.replan(
                run_id,
                payload,
                idempotency_key,
            )

        snapshot = await self.synchronize(run_id)
        if snapshot.stage is LoadPlanningWorkflowStage.AWAITING_REPLAN:
            await self.graph.ainvoke(
                Command(
                    resume=_WorkflowReplanCommand(
                        request=payload,
                        idempotency_key=idempotency_key,
                    ).model_dump(mode="json")
                ),
                config=self._config(run_id),
                durability="sync",
            )
            completed = await self.get_snapshot(run_id)
            child_run_id = completed.child_run_id
            if not child_run_id:
                raise LoadPlanningWorkflowStateError(
                    "重新规划 Graph 已结束，但 checkpoint 缺少 child_run_id"
                )
        else:
            # 正常情况下 REJECTED/FAILED 必然路由到 AWAITING_REPLAN；保留这个分支让
            # 现有 Service 产生准确的 409，而不是让编排层自创业务错误。
            child = await self.agent_service.replan(
                run_id,
                payload,
                idempotency_key,
            )
            child_run_id = child.run_id

        await self.synchronize(child_run_id)
        return await self.agent_service.get(child_run_id)

    async def lock(
        self,
        run_id: str,
        payload: LoadPlanLockRequest,
    ) -> LoadPlanVersionRead:
        """从正式发布 interrupt 恢复，并返回现有不可变版本响应。

        Graph State 只保存锁定后的 ``version_id``，完整快照仍从正式版本 Service 读取。
        对已经锁定的请求重试时，``synchronize`` 会直接识别 ``LOCKED``，随后调用原
        ``lock`` 复用其幂等规则，而不会由 Graph 猜测是否该返回旧版本。
        """

        if not self.enabled:
            return await self.plan_version_service.lock(run_id, payload)

        snapshot = await self.synchronize(run_id)
        if snapshot.stage is LoadPlanningWorkflowStage.AWAITING_RELEASE:
            await self.graph.ainvoke(
                Command(resume=payload.model_dump(mode="json")),
                config=self._config(run_id),
                durability="sync",
            )
            completed = await self.get_snapshot(run_id)
            version_id = completed.locked_version_id
            if not version_id:
                raise LoadPlanningWorkflowStateError(
                    "正式发布 Graph 已结束，但 checkpoint 缺少 locked_version_id"
                )
            return await self.plan_version_service.get(version_id)

        # AgentRun 发布后仍是 APPROVED，重试时 synchronize 会直接恢复为 LOCKED。
        # 再调用现有 lock() 可以复用其“相同请求返回原版本、换原因则冲突”的完整规则。
        version = await self.plan_version_service.lock(run_id, payload)
        await self.synchronize(run_id)
        return version
