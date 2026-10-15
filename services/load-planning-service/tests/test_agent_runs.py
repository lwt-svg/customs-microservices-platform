"""状态化装托智能体端到端测试。"""

from collections.abc import AsyncIterator, Sequence
from itertools import combinations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.load_planning import PalletSpec
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.load_planning_tasks import LoadPlanningTaskRepository
from app.schemas.agent_run import (
    AgentDecisionCode,
    AgentRunCreate,
    AgentRunDecision,
    AgentRunReplan,
    AgentRunResume,
)
from app.schemas.load_planning import LoadPlanningTaskCreate
from app.services.agent_runs import AgentRunConflictError, StatefulPalletAgentService
from app.services.load_planning_tasks import LoadPlanningTaskService
from app.services.pallet_packing import (
    CargoSkuInput,
    PackingStrategy,
    PalletPackingService,
    PalletSpecInput,
)


@pytest_asyncio.fixture
async def agent_session() -> AsyncIterator[AsyncSession]:
    """为需要直接验证事务/回滚语义的测试提供独立真实 AsyncSession。"""

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
            yield session
    finally:
        await engine.dispose()


def build_agent_task(
    task_number: str,
    *,
    complete: bool = True,
    pallet_quantity: int = 20,
    box_quantity: int = 36,
) -> dict[str, object]:
    """构造既能触发追问、又能在回答后进入确定性算法的任务。"""

    first_cargo: dict[str, object] = {
        "item_code": "ITEM-A",
        "sku_code": "SKU-A",
        "cargo_name": "A 箱",
        "quantity": box_quantity,
        "length_mm": 300,
        "width_mm": 200,
        "height_mm": 180,
        "gross_weight_kg": 8,
        "keep_upright": True,
        "fragile": False,
        "source_type": "MANUAL",
        "verified": True,
    }
    second_cargo: dict[str, object] = {
        "item_code": "ITEM-B",
        "sku_code": "SKU-B",
        "cargo_name": "B 箱",
        "quantity": 12,
        "length_mm": 250,
        "width_mm": 250,
        "height_mm": 200,
        "gross_weight_kg": 10,
        "stackable": True,
        "keep_upright": True,
        "fragile": False,
        "source_type": "MANUAL",
        "verified": True,
    }
    if complete:
        first_cargo.update(stackable=True, max_stack_layers=5)
        second_cargo["max_stack_layers"] = 4

    pallet: dict[str, object] = {
        "pallet_code": "PALLET-A",
        "pallet_name": "1200×1000 托盘",
        "quantity": pallet_quantity,
        "length_mm": 1200,
        "width_mm": 1000,
        "height_mm": 150,
        "tare_weight_kg": 25,
        "source_type": "MANUAL",
        "verified": True,
    }
    if complete:
        pallet.update(max_load_kg=500, max_total_height_mm=1250)

    pallets = [pallet]
    if not complete:
        pallets.append(
            {
                "pallet_code": "PALLET-B",
                "pallet_name": "完整候选托盘",
                "quantity": pallet_quantity,
                "length_mm": 1200,
                "width_mm": 1000,
                "height_mm": 150,
                "tare_weight_kg": 25,
                "max_load_kg": 500,
                "max_total_height_mm": 1250,
                "source_type": "MANUAL",
                "verified": True,
            }
        )
    return {
        "task_number": task_number,
        "task_name": "智能体测试任务",
        "cargo_items": [first_cargo, second_cargo],
        "pallet_specs": pallets,
    }


async def create_task(client: AsyncClient, payload: dict[str, object]) -> None:
    response = await client.post("/api/v1/load-planning-tasks", json=payload)
    assert response.status_code == 201, response.text


async def test_agent_should_pause_ask_and_resume_with_three_plans(
    client: AsyncClient,
) -> None:
    """缺约束时暂停；答案齐全后从同一 run 恢复并生成三套方案。"""

    await create_task(client, build_agent_task("LP-AGENT-ASK", complete=False))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-ASK/agent-runs",
        json={},
    )

    assert started.status_code == 201
    body = started.json()
    assert body["status"] == "AWAITING_INPUT"
    assert body["current_step"] == "AWAITING_CONSTRAINTS"
    assert {question["key"] for question in body["questions"]} == {
        "selected_pallet_code",
        "cargo.ITEM-A.stackable",
        "cargo.ITEM-B.max_stack_layers",
    }
    run_id = body["run_id"]

    resumed = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json={
            "answers": [
                {"key": "selected_pallet_code", "value": "PALLET-B"},
                {"key": "cargo.ITEM-A.stackable", "value": False},
                {"key": "cargo.ITEM-B.max_stack_layers", "value": 4},
            ]
        },
    )

    assert resumed.status_code == 200, resumed.text
    result = resumed.json()
    assert result["run_id"] == run_id
    assert result["status"] == "READY_FOR_REVIEW"
    assert result["questions"] == []
    assert result["selected_pallet_code"] == "PALLET-B"
    assert result["run_version"] == 2
    assert result["parent_run_id"] is None
    assert [plan["strategy_code"] for plan in result["plans"]] == [
        "STABLE",
        "BALANCED",
        "UTILIZATION",
    ]
    assert all(
        plan["algorithm_version"] == "DETERMINISTIC_GRID_STRIP_V1"
        and plan["score_version"] == "PALLET_PLAN_SCORE_V1"
        for plan in result["plans"]
    )
    assert [event["sequence"] for event in result["events"]] == list(
        range(1, len(result["events"]) + 1)
    )
    assert all(event["created_at"].endswith("Z") for event in result["events"])


def test_strategy_layer_tradeoff_and_determinism() -> None:
    """三策略应有真实堆高取舍，同一输入重复调用必须逐字段一致。"""

    pallet = PalletSpecInput(
        pallet_code="P",
        quantity=10,
        length_mm=1200,
        width_mm=1000,
        height_mm=150,
        tare_weight_kg=25,
        max_load_kg=1000,
        max_total_height_mm=1250,
    )
    cargo = CargoSkuInput(
        sku_code="A",
        item_code="A",
        quantity=200,
        length_mm=300,
        width_mm=200,
        height_mm=180,
        gross_weight_kg=8,
        max_stack_layers=4,
    )
    service = PalletPackingService()
    results = {strategy: service.plan([pallet], [cargo], strategy) for strategy in PackingStrategy}

    assert results[PackingStrategy.STABLE] == service.plan(
        [pallet], [cargo], PackingStrategy.STABLE
    )
    max_layers = {
        strategy: max(value["layer_count"] for value in result["pallets"])
        for strategy, result in results.items()
    }
    used_pallets = {
        strategy: result["metrics"]["used_pallet_count"] for strategy, result in results.items()
    }
    assert max_layers[PackingStrategy.STABLE] < max_layers[PackingStrategy.BALANCED]
    assert max_layers[PackingStrategy.BALANCED] < max_layers[PackingStrategy.UTILIZATION]
    assert used_pallets[PackingStrategy.STABLE] > used_pallets[PackingStrategy.BALANCED]
    assert used_pallets[PackingStrategy.BALANCED] > used_pallets[PackingStrategy.UTILIZATION]


async def test_generated_coordinates_respect_boundaries_collision_height_and_weight(
    client: AsyncClient,
) -> None:
    """每个输出箱体都必须满足确定性几何与安全上限。"""

    await create_task(client, build_agent_task("LP-AGENT-GEOMETRY"))
    response = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-GEOMETRY/agent-runs",
        json={},
    )

    assert response.status_code == 201, response.text
    for plan in response.json()["plans"]:
        assert plan["metrics"]["unplaced_boxes"] == 0
        assert plan["feasible"] is True
        assert 0 <= plan["score"] <= 100
        for pallet in plan["pallets"]:
            assert pallet["loaded_height_mm"] <= 1250
            # max_load_kg 是货物净载上限；gross_weight 还包含 25kg 托盘皮重。
            assert pallet["gross_weight_kg"] <= 525
            item_codes = {placement["item_code"] for placement in pallet["placements"]}
            assert len(item_codes) == 1
            for placement in pallet["placements"]:
                assert placement["orientation"] in {"LENGTH_ALONG_X", "WIDTH_ALONG_X"}
                assert placement["x_mm"] >= 0
                assert placement["y_mm"] >= 0
                assert placement["z_mm"] >= 150
                assert placement["x_mm"] + placement["length_mm"] <= 1200
                assert placement["y_mm"] + placement["width_mm"] <= 1000
                assert placement["z_mm"] + placement["height_mm"] <= 1250
            for left, right in combinations(pallet["placements"], 2):
                overlap_x = (
                    left["x_mm"] < right["x_mm"] + right["length_mm"]
                    and right["x_mm"] < left["x_mm"] + left["length_mm"]
                )
                overlap_y = (
                    left["y_mm"] < right["y_mm"] + right["width_mm"]
                    and right["y_mm"] < left["y_mm"] + left["width_mm"]
                )
                overlap_z = (
                    left["z_mm"] < right["z_mm"] + right["height_mm"]
                    and right["z_mm"] < left["z_mm"] + left["height_mm"]
                )
                assert not (overlap_x and overlap_y and overlap_z)


async def test_insufficient_pallets_returns_infeasible_candidates(
    client: AsyncClient,
) -> None:
    """可用托盘是硬约束；不足时保留方案用于解释，但不能声称可行。"""

    payload = build_agent_task(
        "LP-AGENT-SHORTAGE",
        pallet_quantity=1,
        box_quantity=500,
    )
    await create_task(client, payload)
    response = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-SHORTAGE/agent-runs",
        json={},
    )

    assert response.status_code == 201
    for plan in response.json()["plans"]:
        assert plan["feasible"] is False
        assert plan["metrics"]["unplaced_boxes"] > 0
        assert plan["warnings"]


async def test_approve_feasible_plan_and_reject_another_run(client: AsyncClient) -> None:
    """审批只接受当前运行的可行方案；驳回必须保留原因。"""

    await create_task(client, build_agent_task("LP-AGENT-DECISION"))
    first = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-DECISION/agent-runs",
        json={},
    )
    first_body = first.json()
    approved = await client.post(
        f"/api/v1/load-planning/agent-runs/{first_body['run_id']}/decision",
        json={
            "decision": "APPROVE",
            "plan_code": first_body["plans"][0]["plan_code"],
            "comment": "仓库确认",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["selected_plan_code"] == "STABLE-001"
    assert approved.json()["run_version"] == 2

    second = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-DECISION/agent-runs",
        json={"strategy_codes": ["BALANCED"]},
    )
    rejected = await client.post(
        f"/api/v1/load-planning/agent-runs/{second.json()['run_id']}/decision",
        json={"decision": "REJECT", "plan_code": None, "comment": "现场改用其他托盘"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert "现场改用其他托盘" in rejected.json()["events"][-1]["detail"]

    replanned = await client.post(
        f"/api/v1/load-planning/agent-runs/{second.json()['run_id']}/replan",
        json={
            "reason": "业务人员要求重新比较利用率方案",
            "strategy_codes": ["UTILIZATION"],
        },
    )
    assert replanned.status_code == 200, replanned.text
    replanned_body = replanned.json()
    assert replanned_body["status"] == "READY_FOR_REVIEW"
    assert replanned_body["parent_run_id"] == second.json()["run_id"]
    assert [plan["strategy_code"] for plan in replanned_body["plans"]] == ["UTILIZATION"]
    assert replanned_body["events"][0]["event_type"] == "RUN_REPLANNED"


async def test_replan_without_strategy_override_inherits_parent_strategies(
    client: AsyncClient,
) -> None:
    """未传 strategy_codes 时应复制数据库中的字符串，而不是对 str 访问 .value。"""

    await create_task(client, build_agent_task("LP-AGENT-REPLAN-INHERIT"))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-REPLAN-INHERIT/agent-runs",
        json={"strategy_codes": ["BALANCED"]},
    )
    run_id = started.json()["run_id"]
    rejected = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/decision",
        json={"decision": "REJECT", "comment": "保持策略重新计算"},
    )
    assert rejected.status_code == 200

    replanned = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/replan",
        json={"reason": "沿用父运行策略"},
    )

    assert replanned.status_code == 200, replanned.text
    assert [plan["strategy_code"] for plan in replanned.json()["plans"]] == ["BALANCED"]


async def test_replan_retry_is_idempotent_and_key_cannot_change_request(
    client: AsyncClient,
) -> None:
    """重规划超时重试返回同一子运行；同 key 换参数必须冲突。"""

    await create_task(client, build_agent_task("LP-AGENT-REPLAN-IDEMPOTENT"))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-REPLAN-IDEMPOTENT/agent-runs",
        json={"strategy_codes": ["STABLE"]},
    )
    parent_id = started.json()["run_id"]
    rejected = await client.post(
        f"/api/v1/load-planning/agent-runs/{parent_id}/decision",
        json={"decision": "REJECT", "comment": "需要重新规划"},
    )
    assert rejected.status_code == 200

    path = f"/api/v1/load-planning/agent-runs/{parent_id}/replan"
    headers = {"Idempotency-Key": " replan-request-001 "}
    payload = {
        "reason": "改为利用率优先",
        "strategy_codes": ["UTILIZATION"],
    }
    first = await client.post(path, headers=headers, json=payload)
    retried = await client.post(path, headers=headers, json=payload)
    conflict = await client.post(
        path,
        headers=headers,
        json={
            "reason": "换一个原因复用旧 key",
            "strategy_codes": ["UTILIZATION"],
        },
    )
    history = await client.get(
        "/api/v1/load-planning/tasks/LP-AGENT-REPLAN-IDEMPOTENT/agent-runs/history"
    )

    assert first.status_code == retried.status_code == 200
    assert first.json() == retried.json()
    assert first.json()["parent_run_id"] == parent_id
    assert conflict.status_code == 409
    assert "另一份重新规划请求" in conflict.json()["detail"]
    assert history.status_code == 200
    assert {item["run_id"] for item in history.json()} == {
        parent_id,
        first.json()["run_id"],
    }


async def test_replan_idempotent_replay_precedes_changed_task_snapshot(
    agent_session: AsyncSession,
) -> None:
    """首次成功后主数据变化，同 key 重试仍返回原 child；新请求则必须拒绝。"""

    task = await LoadPlanningTaskService(LoadPlanningTaskRepository(agent_session)).create(
        LoadPlanningTaskCreate.model_validate(build_agent_task("LP-AGENT-REPLAN-SNAPSHOT"))
    )
    service = StatefulPalletAgentService(AgentRunRepository(agent_session))
    parent = await service.create(task.task_number, AgentRunCreate())
    rejected = await service.decide(
        parent.run_id,
        AgentRunDecision(
            decision=AgentDecisionCode.REJECT,
            comment="先建立可重放的重规划请求",
            expected_run_version=parent.run_version,
        ),
    )
    payload = AgentRunReplan(reason="沿用原策略重新规划")
    child = await service.replan(rejected.run_id, payload, "snapshot-replay-key")

    await agent_session.execute(
        update(PalletSpec)
        .where(
            PalletSpec.task_id == task.id,
            PalletSpec.pallet_code == "PALLET-A",
        )
        .values(max_load_kg=501)
    )
    await agent_session.commit()

    replayed = await service.replan(rejected.run_id, payload, "snapshot-replay-key")
    assert replayed == child

    with pytest.raises(AgentRunConflictError, match="任务输入"):
        await service.replan(rejected.run_id, payload, "new-request-after-change")


async def test_replan_pallet_override_survives_later_resume(client: AsyncClient) -> None:
    """改选托盘后，父运行 answers 中的旧选择不能在下一次 resume 时复活。"""

    await create_task(
        client,
        build_agent_task("LP-AGENT-REPLAN-PALLET", complete=False),
    )
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-REPLAN-PALLET/agent-runs",
        json={},
    )
    parent_id = started.json()["run_id"]
    ready = await client.post(
        f"/api/v1/load-planning/agent-runs/{parent_id}/resume",
        json={
            "answers": [
                {"key": "selected_pallet_code", "value": "PALLET-B"},
                {"key": "cargo.ITEM-A.stackable", "value": False},
                {"key": "cargo.ITEM-B.max_stack_layers", "value": 4},
            ]
        },
    )
    assert ready.status_code == 200
    rejected = await client.post(
        f"/api/v1/load-planning/agent-runs/{parent_id}/decision",
        json={"decision": "REJECT", "comment": "改用 PALLET-A"},
    )
    assert rejected.status_code == 200

    replanned = await client.post(
        f"/api/v1/load-planning/agent-runs/{parent_id}/replan",
        json={
            "reason": "现场改用 PALLET-A",
            "selected_pallet_code": "PALLET-A",
            "strategy_codes": ["STABLE"],
        },
    )
    assert replanned.status_code == 200, replanned.text
    child = replanned.json()
    assert child["selected_pallet_code"] == "PALLET-A"
    assert {question["key"] for question in child["questions"]} == {
        "pallet.PALLET-A.max_load_kg",
        "pallet.PALLET-A.max_total_height_mm",
    }

    resumed = await client.post(
        f"/api/v1/load-planning/agent-runs/{child['run_id']}/resume",
        json={
            "answers": [
                {"key": "pallet.PALLET-A.max_load_kg", "value": 500},
                {"key": "pallet.PALLET-A.max_total_height_mm", "value": 1250},
            ],
            "expected_run_version": child["run_version"],
        },
    )

    assert resumed.status_code == 200, resumed.text
    result = resumed.json()
    assert result["selected_pallet_code"] == "PALLET-A"
    assert {pallet["pallet_code"] for plan in result["plans"] for pallet in plan["pallets"]} == {
        "PALLET-A"
    }


async def test_resume_retry_is_idempotent_and_get_restores_state(
    client: AsyncClient,
) -> None:
    """成功 resume 的网络重试不重复生成方案或事件。"""

    await create_task(client, build_agent_task("LP-AGENT-IDEMPOTENT", complete=False))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-IDEMPOTENT/agent-runs",
        json={},
    )
    run_id = started.json()["run_id"]
    answers = {
        "answers": [
            {"key": "selected_pallet_code", "value": "PALLET-B"},
            {"key": "cargo.ITEM-A.stackable", "value": False},
            # 字符串数字也按问题类型归一化，网络重试仍应识别为同一个答案。
            {"key": "cargo.ITEM-B.max_stack_layers", "value": "4"},
        ]
    }
    first = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json=answers,
    )
    retried = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json=answers,
    )
    restored = await client.get(f"/api/v1/load-planning/agent-runs/{run_id}")

    assert first.status_code == retried.status_code == restored.status_code == 200
    assert len(first.json()["events"]) == len(retried.json()["events"])
    assert [plan["plan_id"] for plan in first.json()["plans"]] == [
        plan["plan_id"] for plan in retried.json()["plans"]
    ]
    assert restored.json() == retried.json()


async def test_partial_resume_retry_is_idempotent_before_version_check(
    client: AsyncClient,
) -> None:
    """仍在等待其他答案时，重复提交相同 partial answers 不应 bump 版本。"""

    await create_task(client, build_agent_task("LP-AGENT-PARTIAL-RETRY", complete=False))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-PARTIAL-RETRY/agent-runs",
        json={},
    )
    run_id = started.json()["run_id"]
    payload = {
        "answers": [{"key": "cargo.ITEM-A.stackable", "value": False}],
        "expected_run_version": started.json()["run_version"],
    }
    first = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json=payload,
    )
    retried_with_stale_version = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json=payload,
    )
    retried_without_version = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json={"answers": payload["answers"]},
    )

    assert first.status_code == 200
    assert first.json()["status"] == "AWAITING_INPUT"
    assert first.json()["run_version"] == started.json()["run_version"] + 1
    assert retried_with_stale_version.status_code == 200
    assert retried_without_version.status_code == 200
    assert retried_with_stale_version.json() == first.json()
    assert retried_without_version.json() == first.json()


async def test_create_retry_with_same_idempotency_key_returns_same_run(
    client: AsyncClient,
) -> None:
    """创建请求超时后重试不能产生第二条运行。"""

    await create_task(client, build_agent_task("LP-AGENT-CREATE-IDEMPOTENT", complete=False))
    path = "/api/v1/load-planning/tasks/LP-AGENT-CREATE-IDEMPOTENT/agent-runs"
    headers = {"Idempotency-Key": " browser-request-001 "}
    first = await client.post(path, headers=headers, json={"strategy_codes": ["STABLE"]})
    retried = await client.post(path, headers=headers, json={"strategy_codes": ["STABLE"]})
    active = await client.get("/api/v1/load-planning/tasks/LP-AGENT-CREATE-IDEMPOTENT/agent-runs")

    assert first.status_code == retried.status_code == 201
    assert first.json() == retried.json()
    assert [item["run_id"] for item in active.json()] == [first.json()["run_id"]]


async def test_create_idempotency_key_cannot_change_request(
    client: AsyncClient,
) -> None:
    """同一个幂等键换策略时返回 409，而不是悄悄创建另一条业务运行。"""

    await create_task(client, build_agent_task("LP-AGENT-CREATE-IDEMPOTENCY-CONFLICT"))
    path = "/api/v1/load-planning/tasks/LP-AGENT-CREATE-IDEMPOTENCY-CONFLICT/agent-runs"
    first = await client.post(
        path,
        headers={"Idempotency-Key": "browser-request-002"},
        json={"strategy_codes": ["STABLE"]},
    )
    conflict = await client.post(
        path,
        headers={"Idempotency-Key": "browser-request-002"},
        json={"strategy_codes": ["UTILIZATION"]},
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "另一份创建请求" in conflict.json()["detail"]


async def test_create_idempotency_integrity_conflict_recovers_after_rollback(
    agent_session: AsyncSession,
) -> None:
    """唯一键竞争回滚后不能再访问已过期 task ORM，否则 AsyncSession 会 MissingGreenlet。"""

    task_payload = LoadPlanningTaskCreate.model_validate(
        build_agent_task("LP-AGENT-IDEMPOTENCY-RACE")
    )
    task = await LoadPlanningTaskService(LoadPlanningTaskRepository(agent_session)).create(
        task_payload
    )
    task_id = task.id
    snapshot = {
        "task_number": task.task_number,
        "cargo_items": [],
        "pallet_specs": [],
    }

    def build_run(run_id: str) -> AgentRun:
        return AgentRun(
            id=run_id,
            task_id=task_id,
            status=AgentRunStatus.AWAITING_INPUT,
            current_step="AWAITING_CONSTRAINTS",
            selected_pallet_code=None,
            selected_plan_code=None,
            strategy_codes=["STABLE"],
            input_snapshot_hash="snapshot-hash",
            input_snapshot=snapshot,
            answers={},
            idempotency_key="race-key",
            idempotency_request_hash="request-hash",
        )

    repository = AgentRunRepository(agent_session)
    winner = await repository.save(build_run("00000000-0000-0000-0000-000000000001"))
    service = StatefulPalletAgentService(repository)

    recovered = await service._save_created_run(
        build_run("00000000-0000-0000-0000-000000000002"),
        task_id,
        "race-key",
        "request-hash",
    )

    assert recovered.id == winner.id
    assert service._to_read(recovered).run_id == winner.id


async def test_expected_run_version_rejects_stale_resume(client: AsyncClient) -> None:
    """两个页面同时操作时，旧页面不能覆盖新页面已经提交的答案。"""

    await create_task(client, build_agent_task("LP-AGENT-VERSION", complete=False))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-VERSION/agent-runs",
        json={},
    )
    run_id = started.json()["run_id"]
    current_version = started.json()["run_version"]
    answers = {
        "answers": [
            {"key": "selected_pallet_code", "value": "PALLET-B"},
            {"key": "cargo.ITEM-A.stackable", "value": False},
            {"key": "cargo.ITEM-B.max_stack_layers", "value": 4},
        ],
        "expected_run_version": current_version,
    }
    accepted = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json=answers,
    )
    assert accepted.status_code == 200
    stale = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json=answers,
    )
    # 相同答案的重复请求仍保持幂等，即使调用方保存的是旧版本。
    assert stale.status_code == 200

    different = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json={
            "answers": [{"key": "cargo.ITEM-A.stackable", "value": True}],
            "expected_run_version": current_version,
        },
    )
    assert different.status_code == 409


async def test_active_run_list_supports_cross_device_recovery(client: AsyncClient) -> None:
    """未完成运行可按任务查询；终态运行不再出现在恢复列表。"""

    await create_task(client, build_agent_task("LP-AGENT-ACTIVE-LIST", complete=False))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-ACTIVE-LIST/agent-runs",
        json={},
    )
    assert started.status_code == 201
    run_id = started.json()["run_id"]

    active = await client.get("/api/v1/load-planning/tasks/LP-AGENT-ACTIVE-LIST/agent-runs")
    assert active.status_code == 200
    assert [item["run_id"] for item in active.json()] == [run_id]

    resumed = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json={
            "answers": [
                {"key": "selected_pallet_code", "value": "PALLET-B"},
                {"key": "cargo.ITEM-A.stackable", "value": False},
                {"key": "cargo.ITEM-B.max_stack_layers", "value": 4},
            ]
        },
    )
    assert resumed.status_code == 200
    approved = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/decision",
        json={"decision": "APPROVE", "plan_code": "STABLE-001"},
    )
    assert approved.status_code == 200

    after_terminal = await client.get("/api/v1/load-planning/tasks/LP-AGENT-ACTIVE-LIST/agent-runs")
    assert after_terminal.status_code == 200
    assert after_terminal.json() == []


async def test_get_and_active_list_explain_run_from_immutable_snapshot(
    agent_session: AsyncSession,
) -> None:
    """主数据变化后，旧运行的读取视图仍应展示创建时缺失的问题。"""

    payload = build_agent_task("LP-AGENT-SNAPSHOT-READ", complete=False)
    payload["pallet_specs"] = payload["pallet_specs"][:1]
    task = await LoadPlanningTaskService(LoadPlanningTaskRepository(agent_session)).create(
        LoadPlanningTaskCreate.model_validate(payload)
    )
    service = StatefulPalletAgentService(AgentRunRepository(agent_session))
    started = await service.create(task.task_number, AgentRunCreate())
    original_question_keys = {question.key for question in started.questions}
    assert "pallet.PALLET-A.max_load_kg" in original_question_keys

    await agent_session.execute(
        update(PalletSpec)
        .where(PalletSpec.task_id == task.id, PalletSpec.pallet_code == "PALLET-A")
        .values(max_load_kg=500)
    )
    await agent_session.commit()

    restored = await service.get(started.run_id)
    active = await service.list_active(task.task_number)
    assert {question.key for question in restored.questions} == original_question_keys
    assert len(active) == 1
    assert {question.key for question in active[0].questions} == original_question_keys

    with pytest.raises(AgentRunConflictError, match="任务输入"):
        await service.resume(
            started.run_id,
            AgentRunResume(
                answers=[
                    {
                        "key": "cargo.ITEM-A.stackable",
                        "value": False,
                    }
                ],
                expected_run_version=started.run_version,
            ),
        )


async def test_failed_generation_is_discoverable_without_partial_plans(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAILED 必须可追溯且无半成品，并能用固定幂等键安全发起新运行。"""

    await create_task(client, build_agent_task("LP-AGENT-FAILED-HISTORY"))
    original_plan = PalletPackingService.plan
    call_count = 0

    def fail_on_second_strategy(
        service: PalletPackingService,
        pallet_specs: Sequence[PalletSpecInput],
        cargo_skus: Sequence[CargoSkuInput],
        strategy: PackingStrategy | str,
    ) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("模拟第二个策略计算失败")
        return original_plan(service, pallet_specs, cargo_skus, strategy)

    monkeypatch.setattr(PalletPackingService, "plan", fail_on_second_strategy)
    with pytest.raises(RuntimeError, match="模拟第二个策略计算失败"):
        await client.post(
            "/api/v1/load-planning/tasks/LP-AGENT-FAILED-HISTORY/agent-runs",
            json={},
        )

    history = await client.get(
        "/api/v1/load-planning/tasks/LP-AGENT-FAILED-HISTORY/agent-runs/history"
    )
    active = await client.get("/api/v1/load-planning/tasks/LP-AGENT-FAILED-HISTORY/agent-runs")

    assert history.status_code == active.status_code == 200
    assert len(history.json()) == 1
    failed = history.json()[0]
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "RuntimeError"
    assert "模拟第二个策略计算失败" in failed["failure_message"]
    assert failed["plans"] == []
    assert failed["events"][-1]["event_type"] == "RUN_FAILED"
    assert active.json() == []

    # 故障解除后，业务人员不是“恢复”这条已失败运行，而是从它创建新的子运行。
    # 固定 Idempotency-Key 让浏览器即使没有收到第一次响应，也能安全重放同一请求。
    monkeypatch.setattr(PalletPackingService, "plan", original_plan)
    replan_path = f"/api/v1/load-planning/agent-runs/{failed['run_id']}/replan"
    replan_payload = {
        "reason": "算法服务恢复后重新规划",
        "strategy_codes": ["STABLE"],
    }
    replan_headers = {"Idempotency-Key": "failed-run-replan-001"}
    replanned = await client.post(
        replan_path,
        headers=replan_headers,
        json=replan_payload,
    )
    replayed = await client.post(
        replan_path,
        headers=replan_headers,
        json=replan_payload,
    )
    parent = await client.get(f"/api/v1/load-planning/agent-runs/{failed['run_id']}")
    recovered_history = await client.get(
        "/api/v1/load-planning/tasks/LP-AGENT-FAILED-HISTORY/agent-runs/history"
    )

    assert replanned.status_code == replayed.status_code == 200
    assert replanned.json() == replayed.json()
    assert replanned.json()["status"] == "READY_FOR_REVIEW"
    assert replanned.json()["parent_run_id"] == failed["run_id"]
    assert [plan["strategy_code"] for plan in replanned.json()["plans"]] == ["STABLE"]
    assert parent.status_code == 200
    assert parent.json()["status"] == "FAILED"
    assert recovered_history.status_code == 200
    assert {item["run_id"] for item in recovered_history.json()} == {
        failed["run_id"],
        replanned.json()["run_id"],
    }


async def test_agent_errors_are_mapped_to_404_409_and_422(client: AsyncClient) -> None:
    """领域错误应让调用方明确区分不存在、状态冲突和输入错误。"""

    missing_task = await client.post(
        "/api/v1/load-planning/tasks/LP-NOT-FOUND/agent-runs",
        json={},
    )
    missing_run = await client.get(
        "/api/v1/load-planning/agent-runs/00000000-0000-0000-0000-000000000000"
    )
    assert missing_task.status_code == missing_run.status_code == 404

    await create_task(client, build_agent_task("LP-AGENT-ERROR"))
    started = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-ERROR/agent-runs",
        json={},
    )
    run_id = started.json()["run_id"]
    bad_answer = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/resume",
        json={"answers": [{"key": "cargo.UNKNOWN.stackable", "value": True}]},
    )
    early_approval = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/decision",
        json={"decision": "APPROVE", "plan_code": "STABLE-001"},
    )
    assert bad_answer.status_code == 422
    assert early_approval.status_code == 200

    shortage_payload = build_agent_task(
        "LP-AGENT-INFEASIBLE",
        pallet_quantity=1,
        box_quantity=500,
    )
    await create_task(client, shortage_payload)
    shortage = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-INFEASIBLE/agent-runs",
        json={"strategy_codes": ["UTILIZATION"]},
    )
    invalid_approval = await client.post(
        f"/api/v1/load-planning/agent-runs/{shortage.json()['run_id']}/decision",
        json={"decision": "APPROVE", "plan_code": "UTILIZATION-001"},
    )
    assert invalid_approval.status_code == 409

    invalid_pallet = await client.post(
        "/api/v1/load-planning/tasks/LP-AGENT-ERROR/agent-runs",
        json={"selected_pallet_code": "DOES-NOT-EXIST"},
    )
    assert invalid_pallet.status_code == 422
