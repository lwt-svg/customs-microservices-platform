"""无 LLM 的 LangGraph 编排层测试。

测试使用真实 SQLAlchemy Repository、现有业务 Service 和内存 Checkpointer。重点不是再测一遍
装托几何算法，而是证明 Graph：

* 会在正确节点 interrupt；
* 恢复后调用原有 Service；
* 不制造第二套 AgentRun/正式版本；
* checkpoint 丢失后能从业务数据库恢复；
* 网络重试继续复用原有幂等规则。

建议按下面顺序阅读测试：

1. ``test_workflow_interrupt_resume_approve_and_lock``：主成功链路；
2. ``test_rejected_workflow_replans_once_and_bootstraps_child_thread``：父子 thread；
3. 恢复类测试：区分“从 MySQL 重建”“已发布终态重建”和“从 SQLite 原地续跑”；
4. disabled/small-state 测试：验证功能开关和 checkpoint 数据边界。
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.main import app
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.load_planning_tasks import LoadPlanningTaskRepository
from app.repositories.plan_versions import LoadPlanVersionRepository
from app.schemas.agent_run import (
    AgentAnswer,
    AgentDecisionCode,
    AgentRunCreate,
    AgentRunDecision,
    AgentRunReplan,
    AgentRunResume,
)
from app.schemas.load_planning import LoadPlanningTaskCreate
from app.schemas.plan_version import LoadPlanLockRequest
from app.services.agent_runs import StatefulPalletAgentService
from app.services.load_planning_tasks import LoadPlanningTaskService
from app.services.plan_versions import LoadPlanVersionService
from app.workflows.load_planning import (
    LoadPlanningWorkflowOrchestrator,
    LoadPlanningWorkflowStage,
)


@dataclass(slots=True)
class WorkflowTestContext:
    """一个测试内共享同一事务会话的真实领域服务。

    这里只把重复装配收进容器，不使用 mock。Graph 节点实际调用 Repository 和 Service，
    因而可以发现事务、状态转换和 DTO 串联问题。
    """

    session: AsyncSession
    task_service: LoadPlanningTaskService
    agent_service: StatefulPalletAgentService
    plan_version_service: LoadPlanVersionService
    orchestrator: LoadPlanningWorkflowOrchestrator


@pytest_asyncio.fixture
async def workflow_context() -> AsyncIterator[WorkflowTestContext]:
    """建立独立内存数据库；Graph checkpoint 也按测试隔离。

    ``StaticPool`` 让异步 Session 复用同一个 SQLite 内存连接，否则每条新连接会看到一座
    空数据库。``expire_on_commit=False`` 则与服务读取已提交对象的方式保持一致。
    """

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            task_service = LoadPlanningTaskService(LoadPlanningTaskRepository(session))
            agent_service = StatefulPalletAgentService(AgentRunRepository(session))
            plan_version_service = LoadPlanVersionService(LoadPlanVersionRepository(session))
            yield WorkflowTestContext(
                session=session,
                task_service=task_service,
                agent_service=agent_service,
                plan_version_service=plan_version_service,
                orchestrator=LoadPlanningWorkflowOrchestrator(
                    agent_service=agent_service,
                    plan_version_service=plan_version_service,
                    checkpointer=InMemorySaver(),
                ),
            )
    finally:
        await engine.dispose()


def _task_payload(
    task_number: str,
    *,
    complete_constraints: bool,
) -> LoadPlanningTaskCreate:
    """构造一个可在“追问”和“直接评审”两条路径间切换的最小任务。"""

    cargo: dict[str, object] = {
        "item_code": "ITEM-WORKFLOW",
        "sku_code": "SKU-WORKFLOW",
        "cargo_name": "工作流测试箱",
        "quantity": 12,
        "length_mm": 300,
        "width_mm": 200,
        "height_mm": 180,
        "gross_weight_kg": 8,
        "keep_upright": True,
        "fragile": False,
        "source_type": "MANUAL",
        "verified": True,
    }
    pallet: dict[str, object] = {
        "pallet_code": "PALLET-WORKFLOW",
        "pallet_name": "工作流测试托盘",
        "quantity": 10,
        "length_mm": 1200,
        "width_mm": 1000,
        "height_mm": 150,
        "tare_weight_kg": 25,
        "source_type": "MANUAL",
        "verified": True,
    }
    if complete_constraints:
        cargo.update(stackable=True, max_stack_layers=4)
        pallet.update(max_load_kg=500, max_total_height_mm=1250)

    return LoadPlanningTaskCreate.model_validate(
        {
            "task_number": task_number,
            "task_name": "LangGraph 编排测试",
            "cargo_items": [cargo],
            "pallet_specs": [pallet],
        }
    )


async def _api_checkpoint_stage(run_id: str) -> str:
    """直接读取应用 Saver，证明 REST 写入口确实推进了对应 Graph thread。"""

    checkpoint = await app.state.load_planning_checkpointer.aget_tuple(
        {"configurable": {"thread_id": run_id}}
    )
    assert checkpoint is not None
    channel_values = checkpoint.checkpoint["channel_values"]
    stage = channel_values.get("current_stage")
    assert isinstance(stage, str)
    return stage


async def test_all_api_write_endpoints_advance_langgraph_threads(
    client: AsyncClient,
) -> None:
    """从真实 FastAPI 路由走完五类写入口，并逐步核对 checkpoint。

    领域 Service 与 Graph 对外返回相同 DTO，仅断言 HTTP 响应无法发现路由是否绕过 Graph。
    因此本测试在 create/resume/decision/replan/lock 后直接读取应用级 Saver：只要任一路由
    改回直调 Service，对应 thread 就会停在旧阶段，测试会立即失败。
    """

    task_number = "LP-WORKFLOW-API-ROUTES"
    created_task = await client.post(
        "/api/v1/load-planning-tasks",
        json=_task_payload(task_number, complete_constraints=False).model_dump(mode="json"),
    )
    assert created_task.status_code == 201, created_task.text

    started_response = await client.post(
        f"/api/v1/load-planning/tasks/{task_number}/agent-runs",
        headers={"Idempotency-Key": "workflow-api-create"},
        json={},
    )
    assert started_response.status_code == 201, started_response.text
    started = started_response.json()
    parent_run_id = started["run_id"]
    assert await _api_checkpoint_stage(parent_run_id) == "AWAITING_CONSTRAINTS"

    resumed_response = await client.post(
        f"/api/v1/load-planning/agent-runs/{parent_run_id}/resume",
        json={
            "answers": [
                {"key": "pallet.PALLET-WORKFLOW.max_load_kg", "value": 500},
                {
                    "key": "pallet.PALLET-WORKFLOW.max_total_height_mm",
                    "value": 1250,
                },
                {"key": "cargo.ITEM-WORKFLOW.stackable", "value": False},
            ],
            "expected_run_version": started["run_version"],
        },
    )
    assert resumed_response.status_code == 200, resumed_response.text
    ready = resumed_response.json()
    assert await _api_checkpoint_stage(parent_run_id) == "AWAITING_REVIEW"

    rejected_response = await client.post(
        f"/api/v1/load-planning/agent-runs/{parent_run_id}/decision",
        json={
            "decision": "REJECT",
            "comment": "验证 decision REST 入口推进 Graph",
            "expected_run_version": ready["run_version"],
        },
    )
    assert rejected_response.status_code == 200, rejected_response.text
    assert await _api_checkpoint_stage(parent_run_id) == "AWAITING_REPLAN"

    replanned_response = await client.post(
        f"/api/v1/load-planning/agent-runs/{parent_run_id}/replan",
        headers={"Idempotency-Key": "workflow-api-replan"},
        json={"reason": "验证 replan REST 入口创建独立 child thread"},
    )
    assert replanned_response.status_code == 200, replanned_response.text
    child = replanned_response.json()
    child_run_id = child["run_id"]
    assert child["parent_run_id"] == parent_run_id
    assert await _api_checkpoint_stage(child_run_id) == "AWAITING_REVIEW"

    approved_response = await client.post(
        f"/api/v1/load-planning/agent-runs/{child_run_id}/decision",
        json={
            "decision": "APPROVE",
            "plan_code": child["plans"][0]["plan_code"],
            "comment": "验证 child thread 进入正式发布",
            "expected_run_version": child["run_version"],
        },
    )
    assert approved_response.status_code == 200, approved_response.text
    approved = approved_response.json()
    assert await _api_checkpoint_stage(child_run_id) == "AWAITING_RELEASE"

    locked_response = await client.post(
        f"/api/v1/load-planning/agent-runs/{child_run_id}/plan-versions",
        json={
            "expected_run_version": approved["run_version"],
            "expected_current_version_number": 0,
            "lock_reason": "验证 lock REST 入口把 Graph 推进到 LOCKED",
        },
    )
    assert locked_response.status_code == 201, locked_response.text
    assert locked_response.json()["version_number"] == 1
    assert await _api_checkpoint_stage(child_run_id) == "LOCKED"


async def test_workflow_interrupt_resume_approve_and_lock(
    workflow_context: WorkflowTestContext,
) -> None:
    """完整走通约束补充、人工评审和正式发布三个 interrupt。

    业务阶段依次为：
    ``AWAITING_INPUT -> READY_FOR_REVIEW -> APPROVED -> LOCKED``。
    每一段都同时断言业务状态和 Graph 暂停点，证明两者相互对应但没有混成一套状态。
    """

    context = workflow_context
    task_number = "LP-WORKFLOW-CLOSED-LOOP"
    await context.task_service.create(_task_payload(task_number, complete_constraints=False))

    # 第一次启动：任务缺少三个约束，Graph 应停在 MISSING_CONSTRAINTS。
    started = await context.orchestrator.create(
        task_number,
        AgentRunCreate(),
        idempotency_key="workflow-create-closed-loop",
    )
    first = await context.orchestrator.get_snapshot(started.run_id)

    assert started.status.value == "AWAITING_INPUT"
    assert first.thread_id == started.run_id
    assert first.stage is LoadPlanningWorkflowStage.AWAITING_CONSTRAINTS
    assert first.next_nodes == ["wait_for_constraints"]
    assert first.interrupt is not None
    assert first.interrupt["interrupt_type"] == "MISSING_CONSTRAINTS"
    assert {question["key"] for question in first.interrupt["questions"]} == {
        "pallet.PALLET-WORKFLOW.max_load_kg",
        "pallet.PALLET-WORKFLOW.max_total_height_mm",
        "cargo.ITEM-WORKFLOW.stackable",
    }

    # 提交结构化答案恢复第一个 interrupt；算法运行后进入人工方案评审。
    ready = await context.orchestrator.resume(
        started.run_id,
        AgentRunResume(
            answers=[
                AgentAnswer(
                    key="pallet.PALLET-WORKFLOW.max_load_kg",
                    value=500,
                ),
                AgentAnswer(
                    key="pallet.PALLET-WORKFLOW.max_total_height_mm",
                    value=1250,
                ),
                AgentAnswer(
                    key="cargo.ITEM-WORKFLOW.stackable",
                    value=False,
                ),
            ],
            expected_run_version=started.run_version,
        ),
    )
    review = await context.orchestrator.get_snapshot(started.run_id)

    assert ready.status.value == "READY_FOR_REVIEW"
    assert review.stage is LoadPlanningWorkflowStage.AWAITING_REVIEW
    assert review.interrupt is not None
    assert review.interrupt["interrupt_type"] == "PLAN_REVIEW"
    # Checkpoint 只保留摘要，不复制 3D placements。
    assert "pallets" not in review.interrupt["plans"][0]

    # 人工批准只改变 AgentRun，不直接产生不可变正式版本。
    approved = await context.orchestrator.decide(
        started.run_id,
        AgentRunDecision(
            decision=AgentDecisionCode.APPROVE,
            plan_code=ready.plans[0].plan_code,
            comment="工作流测试批准",
            expected_run_version=ready.run_version,
        ),
    )
    release = await context.orchestrator.get_snapshot(started.run_id)

    assert approved.status.value == "APPROVED"
    assert release.stage is LoadPlanningWorkflowStage.AWAITING_RELEASE
    assert release.interrupt is not None
    assert release.interrupt["interrupt_type"] == "FORMAL_RELEASE"
    assert release.current_formal_version_number == 0

    lock_request = LoadPlanLockRequest(
        expected_run_version=approved.run_version,
        expected_current_version_number=0,
        lock_reason="工作流测试正式发布",
    )
    # 第二次人工动作正式发布：这时才产生 V1 快照并使 Graph 结束。
    locked = await context.orchestrator.lock(
        started.run_id,
        lock_request,
    )
    completed = await context.orchestrator.get_snapshot(started.run_id)

    assert locked.version_number == 1
    assert completed.stage is LoadPlanningWorkflowStage.LOCKED
    assert completed.next_nodes == []
    assert completed.interrupt is None
    assert completed.locked_version_id == locked.version_id

    # 网络重试继续调用现有 lock() 的幂等校验，不会生成 V2 或第二条事件。
    retried = await context.orchestrator.lock(
        started.run_id,
        lock_request,
    )
    history = await context.plan_version_service.list(task_number)
    assert retried.version_id == locked.version_id
    assert len(history) == 1
    assert len(history[0].events) == 1


async def test_rejected_workflow_replans_once_and_bootstraps_child_thread(
    workflow_context: WorkflowTestContext,
) -> None:
    """驳回后由 Graph 调用现有 replan，并让子 AgentRun 使用自己的 thread。

    父运行是不可篡改的审计记录，不能把 REJECTED 原地改回规划中。因此 replan 创建新的
    AgentRun，child 的 ``parent_run_id`` 指向父运行，同时 child run_id 成为新 thread_id。
    """

    context = workflow_context
    task_number = "LP-WORKFLOW-REPLAN"
    await context.task_service.create(_task_payload(task_number, complete_constraints=True))
    started = await context.orchestrator.create(
        task_number,
        AgentRunCreate(),
        idempotency_key="workflow-create-replan",
    )
    assert started.status.value == "READY_FOR_REVIEW"

    rejected = await context.orchestrator.decide(
        started.run_id,
        AgentRunDecision(
            decision=AgentDecisionCode.REJECT,
            comment="需要重新规划验证",
            expected_run_version=started.run_version,
        ),
    )
    parent_wait = await context.orchestrator.get_snapshot(started.run_id)
    assert rejected.status.value == "REJECTED"
    assert parent_wait.stage is LoadPlanningWorkflowStage.AWAITING_REPLAN
    assert parent_wait.interrupt is not None
    assert parent_wait.interrupt["interrupt_type"] == "REPLAN_REQUIRED"

    request = AgentRunReplan(reason="换一条可审计子运行重新计算")
    child = await context.orchestrator.replan(
        started.run_id,
        request,
        idempotency_key="workflow-replan-stable-key",
    )
    child_snapshot = await context.orchestrator.get_snapshot(child.run_id)

    assert child.parent_run_id == started.run_id
    assert child.run_id != started.run_id
    assert child.status.value == "READY_FOR_REVIEW"
    assert child_snapshot.thread_id == child.run_id
    assert child_snapshot.stage is LoadPlanningWorkflowStage.AWAITING_REVIEW

    # 父 thread 可以因网络重试再次进入 replan interrupt；稳定 key 仍由原 Service
    # 的数据库唯一约束裁决，最终返回同一个 child。
    retried = await context.orchestrator.replan(
        started.run_id,
        request,
        idempotency_key="workflow-replan-stable-key",
    )
    history = await context.agent_service.list_history(task_number)
    assert retried.run_id == child.run_id
    assert len(history) == 2


async def test_empty_checkpointer_restores_latest_run_from_business_history(
    workflow_context: WorkflowTestContext,
) -> None:
    """清空 checkpoint 后仍能用 AgentRun 历史重建正确 interrupt。

    这是“灾后重建”路径：新 Saver 没有任何暂停游标，``restore_latest`` 先从业务历史找到
    run_id，再执行 ``synchronize``，根据 MySQL 状态生成一份新 checkpoint。
    """

    context = workflow_context
    task_number = "LP-WORKFLOW-RESTORE"
    await context.task_service.create(_task_payload(task_number, complete_constraints=True))
    started = await context.orchestrator.create(
        task_number,
        AgentRunCreate(),
        idempotency_key="workflow-create-restore",
    )

    # 模拟本地 checkpoint 文件丢失或迁移到另一实例，但业务 MySQL 完整保留。
    rebuilt = LoadPlanningWorkflowOrchestrator(
        agent_service=context.agent_service,
        plan_version_service=context.plan_version_service,
        checkpointer=InMemorySaver(),
    )
    restored = await rebuilt.restore_latest(task_number)

    assert restored is not None
    assert restored.run_id == started.run_id
    snapshot = await rebuilt.get_snapshot(started.run_id)
    assert snapshot.stage is LoadPlanningWorkflowStage.AWAITING_REVIEW
    assert snapshot.interrupt is not None
    assert snapshot.interrupt["interrupt_type"] == "PLAN_REVIEW"


async def test_empty_checkpointer_restores_locked_run_without_release_interrupt(
    workflow_context: WorkflowTestContext,
) -> None:
    """正式版本已经落库时，新 checkpoint 必须重建为 LOCKED，而不是再次等待发布。

    AgentRun 在正式发布后仍保持 ``APPROVED``，因为“审批通过”和“固化正式版本”是两个
    聚合。若同步节点只读取 AgentRun，它会错误地再次产生 ``FORMAL_RELEASE`` interrupt。
    这条测试故意丢弃旧 checkpoint，验证 Graph 会同时查询 LoadPlanVersion，并把已发布
    运行直接恢复到无后续节点的终态。
    """

    context = workflow_context
    task_number = "LP-WORKFLOW-RESTORE-LOCKED"
    await context.task_service.create(_task_payload(task_number, complete_constraints=True))
    started = await context.orchestrator.create(
        task_number,
        AgentRunCreate(),
        idempotency_key="workflow-create-restore-locked",
    )
    approved = await context.orchestrator.decide(
        started.run_id,
        AgentRunDecision(
            decision=AgentDecisionCode.APPROVE,
            plan_code=started.plans[0].plan_code,
            comment="验证已发布状态可以从业务事实重建",
            expected_run_version=started.run_version,
        ),
    )
    lock_request = LoadPlanLockRequest(
        expected_run_version=approved.run_version,
        expected_current_version_number=0,
        lock_reason="验证新 checkpoint 不会重复要求正式发布",
    )
    locked = await context.orchestrator.lock(started.run_id, lock_request)

    # 使用完全空白的新 Saver，模拟 checkpoint 文件丢失或实例迁移。
    rebuilt = LoadPlanningWorkflowOrchestrator(
        agent_service=context.agent_service,
        plan_version_service=context.plan_version_service,
        checkpointer=InMemorySaver(),
    )
    restored = await rebuilt.restore_latest(task_number)
    snapshot = await rebuilt.get_snapshot(started.run_id)

    assert restored is not None
    assert restored.run_id == started.run_id
    assert restored.status.value == "APPROVED"
    assert snapshot.stage is LoadPlanningWorkflowStage.LOCKED
    assert snapshot.next_nodes == []
    assert snapshot.interrupt is None
    assert snapshot.locked_version_id == locked.version_id
    assert snapshot.locked_version_number == 1

    # 即使客户端因网络超时再次提交原请求，仍返回 V1，不产生第二个正式版本。
    retried = await rebuilt.lock(started.run_id, lock_request)
    history = await context.plan_version_service.list(task_number)
    assert retried.version_id == locked.version_id
    assert len(history) == 1


async def test_disabled_orchestrator_delegates_without_changing_business_contract(
    workflow_context: WorkflowTestContext,
) -> None:
    """功能开关关闭时仍使用相同请求/响应，只是不执行 Graph checkpoint。

    这个回退测试保证上线 LangGraph 不是不可逆迁移：关闭开关后，Facade 直接委托已有
    Service，HTTP 层不需要切换另一套 DTO 或 URL。
    """

    context = workflow_context
    task_number = "LP-WORKFLOW-DISABLED"
    await context.task_service.create(_task_payload(task_number, complete_constraints=False))
    legacy_checkpointer = InMemorySaver()
    disabled = LoadPlanningWorkflowOrchestrator(
        agent_service=context.agent_service,
        plan_version_service=context.plan_version_service,
        checkpointer=legacy_checkpointer,
        enabled=False,
    )

    awaiting_input = await disabled.create(
        task_number,
        AgentRunCreate(),
        idempotency_key="workflow-create-disabled",
    )
    ready = await disabled.resume(
        awaiting_input.run_id,
        AgentRunResume(
            answers=[
                AgentAnswer(
                    key="pallet.PALLET-WORKFLOW.max_load_kg",
                    value=500,
                ),
                AgentAnswer(
                    key="pallet.PALLET-WORKFLOW.max_total_height_mm",
                    value=1250,
                ),
                AgentAnswer(
                    key="cargo.ITEM-WORKFLOW.stackable",
                    value=False,
                ),
            ],
            expected_run_version=awaiting_input.run_version,
        ),
    )
    rejected = await disabled.decide(
        ready.run_id,
        AgentRunDecision(
            decision=AgentDecisionCode.REJECT,
            comment="关闭 Graph 时验证重新规划回退路径",
            expected_run_version=ready.run_version,
        ),
    )
    child = await disabled.replan(
        rejected.run_id,
        AgentRunReplan(reason="关闭 Graph 时仍由原 Service 创建子运行"),
        idempotency_key="workflow-replan-disabled",
    )
    approved = await disabled.decide(
        child.run_id,
        AgentRunDecision(
            decision=AgentDecisionCode.APPROVE,
            plan_code=child.plans[0].plan_code,
            comment="关闭 Graph 时沿用原业务 Service",
            expected_run_version=child.run_version,
        ),
    )
    locked = await disabled.lock(
        child.run_id,
        LoadPlanLockRequest(
            expected_run_version=approved.run_version,
            expected_current_version_number=0,
            lock_reason="验证功能开关不改变业务契约",
        ),
    )

    assert awaiting_input.status.value == "AWAITING_INPUT"
    assert ready.status.value == "READY_FOR_REVIEW"
    assert rejected.status.value == "REJECTED"
    assert child.parent_run_id == rejected.run_id
    assert approved.status.value == "APPROVED"
    assert locked.source_run_id == child.run_id
    assert locked.version_number == 1

    # 五个写方法都走完后 Saver 仍为空，证明关闭开关是真正的原 Service 回退，而不是
    # “Graph 仍执行、只是页面不显示”。
    checkpoint = await disabled.graph.aget_state({"configurable": {"thread_id": child.run_id}})
    assert not checkpoint.values


async def test_sqlite_checkpoint_survives_saver_reopen_and_resumes_interrupt(
    workflow_context: WorkflowTestContext,
    tmp_path: Path,
) -> None:
    """关闭并重开 SQLite Saver 后，直接读取原 interrupt 并从原节点继续。

    这个测试与 ``restore_latest`` 不同：它没有先调用 ``synchronize()`` 从业务数据库
    重建 Graph，而是直接证明文件 checkpoint 本身可以跨进程生命周期恢复。
    """

    context = workflow_context
    task_number = "LP-WORKFLOW-SQLITE-REOPEN"
    await context.task_service.create(_task_payload(task_number, complete_constraints=False))
    checkpoint_path = tmp_path / "load-planning-checkpoints.sqlite3"

    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as first_saver:
        await first_saver.setup()
        first_orchestrator = LoadPlanningWorkflowOrchestrator(
            agent_service=context.agent_service,
            plan_version_service=context.plan_version_service,
            checkpointer=first_saver,
        )
        started = await first_orchestrator.create(
            task_number,
            AgentRunCreate(),
            idempotency_key="workflow-sqlite-reopen",
        )
        before_close = await first_orchestrator.get_snapshot(started.run_id)
        assert before_close.stage is LoadPlanningWorkflowStage.AWAITING_CONSTRAINTS
        assert before_close.interrupt is not None
        assert before_close.interrupt["interrupt_type"] == "MISSING_CONSTRAINTS"

    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as reopened_saver:
        await reopened_saver.setup()
        reopened = LoadPlanningWorkflowOrchestrator(
            agent_service=context.agent_service,
            plan_version_service=context.plan_version_service,
            checkpointer=reopened_saver,
        )

        # 不调用 synchronize：直接按同一个 run_id/thread_id 读取文件中的原 checkpoint。
        restored = await reopened.get_snapshot(started.run_id)
        assert restored.stage is LoadPlanningWorkflowStage.AWAITING_CONSTRAINTS
        assert restored.interrupt == before_close.interrupt

        resume_payload = AgentRunResume(
            answers=[
                AgentAnswer(
                    key="pallet.PALLET-WORKFLOW.max_load_kg",
                    value=500,
                ),
                AgentAnswer(
                    key="pallet.PALLET-WORKFLOW.max_total_height_mm",
                    value=1250,
                ),
                AgentAnswer(
                    key="cargo.ITEM-WORKFLOW.stackable",
                    value=False,
                ),
            ],
            expected_run_version=started.run_version,
        )
        # 直接向恢复出来的 interrupt 发送 Command，不先启动一轮新的 DB reconcile。
        await reopened.graph.ainvoke(
            Command(resume=resume_payload.model_dump(mode="json")),
            config={"configurable": {"thread_id": started.run_id}},
            durability="sync",
        )

        ready = await context.agent_service.get(started.run_id)
        review = await reopened.get_snapshot(started.run_id)
        assert ready.status.value == "READY_FOR_REVIEW"
        assert review.stage is LoadPlanningWorkflowStage.AWAITING_REVIEW
        assert review.interrupt is not None
        assert review.interrupt["interrupt_type"] == "PLAN_REVIEW"


async def test_checkpoint_values_exclude_large_business_facts(
    workflow_context: WorkflowTestContext,
) -> None:
    """State 只保存流程引用，不复制任务快照、方案或三维坐标。

    除了逐键检查，也序列化整份 State 搜索禁用词，防止未来把大对象嵌在更深层字段中而
    逃过表层断言。interrupt 中允许有候选方案摘要，但 ``checkpoint.values`` 不应保存它。
    """

    context = workflow_context
    task_number = "LP-WORKFLOW-SMALL-STATE"
    await context.task_service.create(_task_payload(task_number, complete_constraints=True))
    ready = await context.orchestrator.create(
        task_number,
        AgentRunCreate(),
        idempotency_key="workflow-small-state",
    )

    checkpoint = await context.orchestrator.graph.aget_state(
        {"configurable": {"thread_id": ready.run_id}}
    )
    values = checkpoint.values
    serialized_values = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower()

    assert values["run_id"] == ready.run_id
    assert values["run_version"] == ready.run_version
    assert values["run_status"] == "READY_FOR_REVIEW"
    for forbidden_fact in (
        "input_snapshot",
        "cargo_items",
        "plans",
        "pallets",
        "placements",
        "pdf",
    ):
        assert forbidden_fact not in values
        assert forbidden_fact not in serialized_values
