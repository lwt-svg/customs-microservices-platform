"""正式装载方案版本锁定闭环测试。"""

from collections.abc import AsyncIterator

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.main import app
from app.models.agent_run import PackingPlan
from app.models.load_planning import CargoItem, LoadPlanningTask


def build_task(task_number: str) -> dict[str, object]:
    """构造约束完整、可直接生成可行方案的最小任务。"""

    return {
        "task_number": task_number,
        "task_name": "正式版本锁定测试",
        "cargo_items": [
            {
                "item_code": "ITEM-LOCK",
                "sku_code": "SKU-LOCK",
                "cargo_name": "锁定测试箱",
                "quantity": 12,
                "length_mm": 300,
                "width_mm": 200,
                "height_mm": 180,
                "gross_weight_kg": 8,
                "stackable": True,
                "max_stack_layers": 4,
                "keep_upright": True,
                "fragile": False,
                "source_type": "MANUAL",
                "verified": True,
            }
        ],
        "pallet_specs": [
            {
                "pallet_code": "PALLET-LOCK",
                "pallet_name": "锁定测试托盘",
                "quantity": 10,
                "length_mm": 1200,
                "width_mm": 1000,
                "height_mm": 150,
                "tare_weight_kg": 25,
                "max_load_kg": 500,
                "max_total_height_mm": 1250,
                "source_type": "MANUAL",
                "verified": True,
            }
        ],
    }


async def create_task(client: AsyncClient, task_number: str) -> None:
    response = await client.post(
        "/api/v1/load-planning-tasks",
        json=build_task(task_number),
    )
    assert response.status_code == 201, response.text


async def create_and_approve_run(
    client: AsyncClient,
    task_number: str,
    *,
    strategy: str = "STABLE",
) -> dict[str, object]:
    """创建候选方案并走完 APPROVED，但不把它冒充正式锁定。"""

    started = await client.post(
        f"/api/v1/load-planning/tasks/{task_number}/agent-runs",
        json={"strategy_codes": [strategy]},
    )
    assert started.status_code == 201, started.text
    started_body = started.json()
    approved = await client.post(
        f"/api/v1/load-planning/agent-runs/{started_body['run_id']}/decision",
        json={
            "decision": "APPROVE",
            "plan_code": started_body["plans"][0]["plan_code"],
            "comment": "演示审批通过",
            "expected_run_version": started_body["run_version"],
        },
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


async def open_database_session() -> tuple[AsyncSession, AsyncIterator[AsyncSession]]:
    """复用 API 测试夹具的覆盖会话，只用于构造篡改/主数据变化场景。"""

    override = app.dependency_overrides[get_session]
    generator = override()
    session = await anext(generator)
    return session, generator


async def test_lock_creates_immutable_version_and_all_read_endpoints(
    client: AsyncClient,
) -> None:
    """批准与锁定分离；锁定后四个端点返回同一份可校验正式版本。"""

    task_number = "LP-PLAN-VERSION-LOCK"
    await create_task(client, task_number)

    before_current = await client.get(
        f"/api/v1/load-planning/tasks/{task_number}/plan-versions/current"
    )
    before_list = await client.get(f"/api/v1/load-planning/tasks/{task_number}/plan-versions")
    assert before_current.status_code == 404
    assert before_list.status_code == 200
    assert before_list.json() == []

    approved = await create_and_approve_run(client, task_number)
    run_id = str(approved["run_id"])
    payload = {
        "expected_run_version": approved["run_version"],
        "expected_current_version_number": 0,
        "lock_reason": "仓库准备按该坐标执行装托",
    }
    locked = await client.post(
        f"/api/v1/load-planning/agent-runs/{run_id}/plan-versions",
        json=payload,
    )

    assert locked.status_code == 201, locked.text
    body = locked.json()
    assert body["status"] == "LOCKED"
    assert body["task_number"] == task_number
    assert body["version_number"] == 1
    assert body["source_run_id"] == run_id
    assert body["source_plan_id"] == approved["plans"][0]["plan_id"]
    assert body["plan_code"] == approved["selected_plan_code"]
    assert body["selected_pallet_code"] == "PALLET-LOCK"
    assert body["supersedes_version_id"] is None
    assert body["source_run_version"] == approved["run_version"]
    assert body["snapshot_schema_version"] == "LOAD_PLAN_VERSION_SNAPSHOT_V1"
    assert body["algorithm_version"] == "DETERMINISTIC_GRID_STRIP_V1"
    assert body["score_version"] == "PALLET_PLAN_SCORE_V1"
    assert len(body["input_snapshot_hash"]) == 64
    assert len(body["constraint_snapshot_hash"]) == 64
    assert len(body["plan_snapshot_hash"]) == 64
    assert body["approval_actor_subject"] == "SYSTEM_DEMO"
    assert body["approval_actor_display_name"] == "SYSTEM_DEMO"
    assert body["approval_actor_source"] == "SYSTEM_DEMO"
    assert body["locked_by_subject"] == "SYSTEM_DEMO"
    assert body["locked_by_display_name"] == "SYSTEM_DEMO"
    assert body["locked_by_source"] == "SYSTEM_DEMO"
    assert body["approval_comment"] == "演示审批通过"
    assert body["lock_reason"] == payload["lock_reason"]
    assert body["approved_at"].endswith("Z")
    assert body["locked_at"].endswith("Z")
    assert body["input_snapshot"]["task_number"] == task_number
    assert body["constraint_snapshot"] == {
        "selected_pallet_code": "PALLET-LOCK",
        "strategy_codes": ["STABLE"],
        "answers": {},
    }
    assert body["plan_snapshot"]["plan_id"] == body["source_plan_id"]
    assert body["plan_snapshot"]["feasible"] is True
    assert body["plan_snapshot"]["pallets"]
    assert len(body["events"]) == 1
    assert body["events"][0]["sequence"] == 1
    assert body["events"][0]["event_type"] == "PLAN_VERSION_LOCKED"
    assert body["events"][0]["actor_subject"] == "SYSTEM_DEMO"
    assert body["events"][0]["actor_display_name"] == "SYSTEM_DEMO"
    assert body["events"][0]["actor_source"] == "SYSTEM_DEMO"
    assert len(body["events"][0]["request_id"]) == 36
    assert body["events"][0]["occurred_at"].endswith("Z")

    # 锁定不会篡改来源运行的 APPROVED 语义。
    restored_run = await client.get(f"/api/v1/load-planning/agent-runs/{run_id}")
    assert restored_run.status_code == 200
    assert restored_run.json()["status"] == "APPROVED"

    detail = await client.get(f"/api/v1/load-planning/plan-versions/{body['version_id']}")
    current = await client.get(f"/api/v1/load-planning/tasks/{task_number}/plan-versions/current")
    history = await client.get(f"/api/v1/load-planning/tasks/{task_number}/plan-versions")
    assert detail.status_code == current.status_code == history.status_code == 200
    assert detail.json() == current.json() == body
    assert history.json() == [body]


async def test_same_source_run_lock_is_idempotent_but_cannot_change_reason(
    client: AsyncClient,
) -> None:
    """网络重试不增加版本/事件；换锁定原因则不能覆盖原正式事实。"""

    task_number = "LP-PLAN-VERSION-IDEMPOTENT"
    await create_task(client, task_number)
    approved = await create_and_approve_run(client, task_number)
    path = f"/api/v1/load-planning/agent-runs/{approved['run_id']}/plan-versions"
    payload = {
        "expected_run_version": approved["run_version"],
        "expected_current_version_number": 0,
        "lock_reason": "第一次正式锁定",
    }
    first = await client.post(path, json=payload)
    retried = await client.post(path, json=payload)
    changed_reason = await client.post(
        path,
        json={**payload, "lock_reason": "试图覆盖锁定原因"},
    )
    history = await client.get(f"/api/v1/load-planning/tasks/{task_number}/plan-versions")

    assert first.status_code == retried.status_code == 201
    assert first.json() == retried.json()
    assert changed_reason.status_code == 409
    assert len(history.json()) == 1
    assert len(history.json()[0]["events"]) == 1


async def test_second_approved_run_creates_linear_version_chain(
    client: AsyncClient,
) -> None:
    """expected_current_version_number 和 supersedes 形成线性正式版本历史。"""

    task_number = "LP-PLAN-VERSION-CHAIN"
    await create_task(client, task_number)
    first_run = await create_and_approve_run(client, task_number, strategy="STABLE")
    first = await client.post(
        f"/api/v1/load-planning/agent-runs/{first_run['run_id']}/plan-versions",
        json={
            "expected_run_version": first_run["run_version"],
            "expected_current_version_number": 0,
            "lock_reason": "首版",
        },
    )
    assert first.status_code == 201

    second_run = await create_and_approve_run(
        client,
        task_number,
        strategy="UTILIZATION",
    )
    stale = await client.post(
        f"/api/v1/load-planning/agent-runs/{second_run['run_id']}/plan-versions",
        json={
            "expected_run_version": second_run["run_version"],
            "expected_current_version_number": 0,
            "lock_reason": "旧页面尝试锁定第二版",
        },
    )
    assert stale.status_code == 409
    assert "当前版本为 1" in stale.json()["detail"]

    second = await client.post(
        f"/api/v1/load-planning/agent-runs/{second_run['run_id']}/plan-versions",
        json={
            "expected_run_version": second_run["run_version"],
            "expected_current_version_number": 1,
            "lock_reason": "利用率方案成为第二版",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_number"] == 2
    assert second.json()["supersedes_version_id"] == first.json()["version_id"]

    history = await client.get(f"/api/v1/load-planning/tasks/{task_number}/plan-versions")
    assert [version["version_number"] for version in history.json()] == [2, 1]


async def test_lock_rejects_unapproved_stale_or_changed_input(
    client: AsyncClient,
) -> None:
    """正式锁定必须同时通过状态、运行版本和任务输入哈希三道检查。"""

    task_number = "LP-PLAN-VERSION-GUARDS"
    await create_task(client, task_number)
    started = await client.post(
        f"/api/v1/load-planning/tasks/{task_number}/agent-runs",
        json={"strategy_codes": ["STABLE"]},
    )
    assert started.status_code == 201
    started_body = started.json()
    path = f"/api/v1/load-planning/agent-runs/{started_body['run_id']}/plan-versions"
    base_payload = {
        "expected_run_version": started_body["run_version"],
        "expected_current_version_number": 0,
        "lock_reason": "守卫测试",
    }
    unapproved = await client.post(path, json=base_payload)
    assert unapproved.status_code == 409
    assert "APPROVED" in unapproved.json()["detail"]

    approved = await client.post(
        f"/api/v1/load-planning/agent-runs/{started_body['run_id']}/decision",
        json={
            "decision": "APPROVE",
            "plan_code": "STABLE-001",
            "expected_run_version": started_body["run_version"],
        },
    )
    assert approved.status_code == 200
    stale_version = await client.post(path, json=base_payload)
    assert stale_version.status_code == 409
    assert "运行版本已变化" in stale_version.json()["detail"]

    # 模拟批准后有人绕过业务 API 改了任务主数据；当前任务哈希与运行快照不再一致。
    session, generator = await open_database_session()
    try:
        task = await session.scalar(
            select(LoadPlanningTask).where(LoadPlanningTask.task_number == task_number)
        )
        assert task is not None
        cargo = await session.scalar(select(CargoItem).where(CargoItem.task_id == task.id))
        assert cargo is not None
        cargo.quantity += 1
        await session.commit()
    finally:
        await generator.aclose()

    changed_input = await client.post(
        path,
        json={
            **base_payload,
            "expected_run_version": approved.json()["run_version"],
        },
    )
    assert changed_input.status_code == 409
    assert "任务输入" in changed_input.json()["detail"]


async def test_locked_snapshot_survives_source_plan_change_and_rejects_actor_input(
    client: AsyncClient,
) -> None:
    """正式版本是深复制快照；请求体也不能注入伪造 actor。"""

    task_number = "LP-PLAN-VERSION-IMMUTABLE"
    await create_task(client, task_number)
    approved = await create_and_approve_run(client, task_number)
    path = f"/api/v1/load-planning/agent-runs/{approved['run_id']}/plan-versions"
    injected_actor = await client.post(
        path,
        json={
            "expected_run_version": approved["run_version"],
            "expected_current_version_number": 0,
            "lock_reason": "不应接受伪造身份",
            "locked_by_subject": "fake-user",
        },
    )
    assert injected_actor.status_code == 422

    locked = await client.post(
        path,
        json={
            "expected_run_version": approved["run_version"],
            "expected_current_version_number": 0,
            "lock_reason": "保存不可变快照",
        },
    )
    assert locked.status_code == 201, locked.text
    original_summary = locked.json()["plan_snapshot"]["summary"]

    # 直接篡改来源候选表，用于证明正式版本没有继续引用可变 ORM 行。
    session, generator = await open_database_session()
    try:
        source_plan = await session.scalar(
            select(PackingPlan).where(PackingPlan.plan_id == locked.json()["source_plan_id"])
        )
        assert source_plan is not None
        source_plan.summary = "来源候选方案已被外部篡改"
        await session.commit()
    finally:
        await generator.aclose()

    restored = await client.get(
        f"/api/v1/load-planning/plan-versions/{locked.json()['version_id']}"
    )
    assert restored.status_code == 200
    assert restored.json()["plan_snapshot"]["summary"] == original_summary


async def test_plan_version_not_found_errors_are_explicit(
    client: AsyncClient,
) -> None:
    """任务、运行、版本不存在时均返回 404。"""

    missing_run = await client.post(
        "/api/v1/load-planning/agent-runs/00000000-0000-0000-0000-000000000000/plan-versions",
        json={
            "expected_run_version": 1,
            "expected_current_version_number": 0,
            "lock_reason": "不存在",
        },
    )
    missing_task = await client.get("/api/v1/load-planning/tasks/LP-MISSING/plan-versions")
    missing_version = await client.get(
        "/api/v1/load-planning/plan-versions/00000000-0000-0000-0000-000000000000"
    )
    assert missing_run.status_code == 404
    assert missing_task.status_code == 404
    assert missing_version.status_code == 404
