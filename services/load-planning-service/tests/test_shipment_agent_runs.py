"""Shipment Coordinator Agent 的聚焦 REST 契约测试。

测试通过真实 FastAPI、SQLAlchemy、LangGraph 和装载子 Agent 走闭环；只有 LINK HTTP 与
案件绑定的远程资源验证使用可控 fake。所有编号均为构造演示值。
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update

from app.api.shipment_agent_runs import get_link_booking_gateway
from app.api.shipment_cases import get_shipment_resource_verifier
from app.db.session import get_session_factory
from app.integrations.shipment_agent_resources import LinkBookingSnapshot
from app.integrations.shipment_resources import VerifiedShipmentResource
from app.main import app
from app.models.shipment_agent_run import (
    SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.models.shipment_case import ShipmentResourceType
from app.repositories.shipment_agent_runs import ShipmentAgentRunRepository
from app.schemas.shipment_agent_run import ShipmentAgentStart
from app.services.shipment_agent_automation import ShipmentAgentAutomationService
from app.services.shipment_agent_runs import ShipmentAgentService
from app.workers.shipment_agent_wake_worker import ShipmentAgentWakeWorker
from app.workflows.shipment_case import ShipmentWorkflowOrchestrator


def _headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


def _case_payload(case_number: str, scenario: str) -> dict[str, object]:
    return {
        "case_number": case_number,
        "case_name": f"{scenario} Shipment Agent 测试",
        "transport_scenario": scenario,
        "description": "构造的 Shipment Coordinator Agent 测试案件。",
    }


def _task_payload(task_number: str) -> dict[str, object]:
    return {
        "task_number": task_number,
        "task_name": "总 Agent 装载子任务",
        "cargo_items": [
            {
                "item_code": "ITEM-SHIPMENT-AGENT",
                "sku_code": "SKU-SHIPMENT-AGENT",
                "cargo_name": "构造测试箱",
                "quantity": 8,
                "length_mm": 300,
                "width_mm": 200,
                "height_mm": 180,
                "gross_weight_kg": 8,
                "stackable": True,
                "max_stack_layers": 4,
                "keep_upright": True,
                "fragile": False,
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "SHIPMENT_AGENT_TEST",
                "verified": False,
            }
        ],
        "pallet_specs": [
            {
                "pallet_code": "PALLET-SHIPMENT-AGENT",
                "pallet_name": "构造测试托盘",
                "quantity": 2,
                "length_mm": 1200,
                "width_mm": 1000,
                "height_mm": 150,
                "tare_weight_kg": 25,
                "max_load_kg": 500,
                "max_total_height_mm": 1250,
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "SHIPMENT_AGENT_TEST",
                "verified": False,
            }
        ],
    }


@dataclass(slots=True)
class FakeBookingResourceVerifier:
    application_number: str

    async def verify(
        self,
        resource_type: ShipmentResourceType,
        resource_number: str,
    ) -> VerifiedShipmentResource:
        assert resource_type is ShipmentResourceType.LINK_BOOKING_APPLICATION
        assert resource_number == self.application_number
        return VerifiedShipmentResource(
            resource_type=resource_type,
            resource_number=resource_number,
            resource_status="DRAFT",
            verification_source="SHIPMENT_AGENT_TEST_FAKE",
            verified_at=datetime.now(UTC),
        )


@dataclass(slots=True)
class FakeLinkGateway:
    application_number: str
    application_status: str = "DRAFT"
    carrier_status: str | None = None
    booking_reference: str | None = None
    submit_calls: int = 0
    retry_calls: int = 0

    async def get_detail(self, application_number: str) -> LinkBookingSnapshot:
        assert application_number == self.application_number
        return LinkBookingSnapshot(
            application_number=application_number,
            application_status=self.application_status,
            booking_reference=self.booking_reference,
            carrier_status=self.carrier_status,
            carrier_can_retry=self.carrier_status in {"DEAD", "RETRY_WAIT"},
            carrier_request_id=(
                "DEMO-CARRIER-SHIPMENT-001" if self.carrier_status is not None else None
            ),
        )

    async def submit(self, application_number: str) -> None:
        assert application_number == self.application_number
        self.submit_calls += 1
        self.application_status = "SUBMITTED"
        self.carrier_status = "DEAD"

    async def retry(self, application_number: str) -> None:
        assert application_number == self.application_number
        self.retry_calls += 1
        self.carrier_status = "PROCESSING"


def _wake_worker(
    gateway: FakeLinkGateway,
    *,
    lease_seconds: float = 30,
    batch_size: int = 20,
    recovery_grace_seconds: float = 1,
) -> ShipmentAgentWakeWorker:
    """使用当前测试数据库和同一个 checkpoint 构建单轮 Worker。"""

    session_factory = app.dependency_overrides[get_session_factory]()
    automation = ShipmentAgentAutomationService(
        session_factory,
        wait_seconds=1,
        resource_binding_seconds=1,
        lease_seconds=lease_seconds,
        recovery_grace_seconds=recovery_grace_seconds,
        retry_base_seconds=1,
        retry_max_seconds=8,
    )
    service = ShipmentAgentService(
        session_factory,
        gateway,
        app.state.load_planning_checkpointer,
        running_recovery_seconds=recovery_grace_seconds,
    )
    workflow = ShipmentWorkflowOrchestrator(
        service=service,
        checkpointer=app.state.load_planning_checkpointer,
        automation=automation,
    )
    return ShipmentAgentWakeWorker(
        automation=automation,
        orchestrator=workflow,
        poll_seconds=1,
        batch_size=batch_size,
    )


async def _run_due_wakes(gateway: FakeLinkGateway) -> int:
    """把测试时钟推进一分钟，避免真实等待 next_wake_at。"""

    return await _wake_worker(gateway).run_once(now=datetime.now(UTC) + timedelta(minutes=1))


async def _create_case(
    client: AsyncClient,
    case_number: str,
    scenario: str,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/shipment-cases",
        json=_case_payload(case_number, scenario),
        headers=_headers(f"create-{case_number}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _supply_default_demo_readiness(
    client: AsyncClient,
    run: dict[str, object],
    *,
    key: str,
) -> dict[str, object]:
    """为本文件的一箱规/一托盘演示任务补齐门禁事实。"""

    assert run["current_stage"] == "AWAITING_DATA_REMEDIATION"
    assert run["pending_interrupt"]["type"] == "DATA_READINESS_REMEDIATION"
    payload = {
        "expected_run_version": run["run_version"],
        "interrupt_type": "DATA_READINESS_REMEDIATION",
        "action": "SUPPLY_DATA",
        "comment": "仓库完成演示盘点与主数据核对",
        "data_facts": [
            {
                "subject_type": "CARGO_ITEM",
                "subject_code": "ITEM-SHIPMENT-AGENT",
                "fact_type": "AVAILABLE_QUANTITY",
                "value": 8,
                "source_type": "MANUAL_CONFIRMATION",
                "source_reference": "SHIPMENT_AGENT_TEST_STOCK_CHECK",
            },
            {
                "subject_type": "CARGO_ITEM",
                "subject_code": "ITEM-SHIPMENT-AGENT",
                "fact_type": "MASTER_DATA_CONFIRMED",
                "value": True,
                "source_type": "MANUAL_CONFIRMATION",
                "source_reference": "SHIPMENT_AGENT_TEST_MASTER_CHECK",
            },
            {
                "subject_type": "PALLET_SPEC",
                "subject_code": "PALLET-SHIPMENT-AGENT",
                "fact_type": "MASTER_DATA_CONFIRMED",
                "value": True,
                "source_type": "MANUAL_CONFIRMATION",
                "source_reference": "SHIPMENT_AGENT_TEST_PALLET_CHECK",
            },
        ],
    }
    supplied = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers(key),
        json=payload,
    )
    replayed = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers(key),
        json=payload,
    )
    assert supplied.status_code == replayed.status_code == 200
    body = supplied.json()
    assert body["data_readiness"]["status"] == "DEMO_READY"
    assert body["data_readiness"]["source_mode"] == "DEMO"
    assert body["data_readiness"]["issue_count"] == 0
    assert body["current_stage"] == "AWAITING_CASE_ACTIVATION"
    assert (
        sum(event["event_type"] == "DATA_SUPPLEMENTED" for event in replayed.json()["events"]) == 1
    )
    return body


async def test_load_only_coordinator_closes_loop_and_keeps_commands_idempotent(
    client: AsyncClient,
) -> None:
    """自动启动装载子 Agent，人工在子工作台发布后由总 Agent 结案。"""

    case_number = "SHP-DEMO-AGENT-LOAD-001"
    task_number = "LP-DEMO-AGENT-LOAD-001"
    empty_active = await client.get(f"/api/v1/shipment-cases/{case_number}/agent-runs/active")
    assert empty_active.status_code == 404

    created_task = await client.post(
        "/api/v1/load-planning-tasks",
        json=_task_payload(task_number),
    )
    assert created_task.status_code == 201, created_task.text
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    assert case["revision"] == 1

    bound = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        headers=_headers("bind-load-agent-001"),
        json={
            "resource_type": "LOAD_PLANNING_TASK",
            "resource_number": task_number,
            "expected_revision": 1,
        },
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["revision"] == 2

    start_payload = {"expected_case_revision": 2}
    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-load-agent-001"),
        json=start_payload,
    )
    replayed_start = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-load-agent-001"),
        json=start_payload,
    )
    assert started.status_code == replayed_start.status_code == 201
    run = started.json()
    assert replayed_start.json()["run_id"] == run["run_id"]
    assert run["started_case_revision"] == 2
    assert run["last_observed_case_revision"] == 2
    assert run["status"] == "WAITING_HUMAN"
    assert run["current_stage"] == "AWAITING_DATA_REMEDIATION"
    assert run["pending_interrupt"]["type"] == "DATA_READINESS_REMEDIATION"
    assert run["data_readiness"]["status"] == "ACTION_REQUIRED"
    assert run["data_readiness"]["issue_count"] == 3
    assert run["next_wake_at"] is None
    assert run["notifications"][0]["status"] == "OPEN"

    activation = await _supply_default_demo_readiness(
        client,
        run,
        key="supply-load-readiness-001",
    )

    activation_payload = {
        "expected_run_version": activation["run_version"],
        "interrupt_type": "CASE_ACTIVATION_APPROVAL",
        "action": "APPROVE",
        "comment": "测试批准激活",
    }
    activated = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("approve-load-case-001"),
        json=activation_payload,
    )
    replayed_activation = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("approve-load-case-001"),
        json=activation_payload,
    )
    assert activated.status_code == replayed_activation.status_code == 200
    waiting_child = activated.json()
    assert waiting_child["status"] == "WAITING_RESOURCE"
    assert waiting_child["current_stage"] == "AWAITING_LOAD_REVIEW"
    assert waiting_child["selected_load_run_id"]
    assert replayed_activation.json()["run_version"] == waiting_child["run_version"]

    child_run_id = waiting_child["selected_load_run_id"]
    child = await client.get(f"/api/v1/load-planning/agent-runs/{child_run_id}")
    assert child.status_code == 200
    child_body = child.json()
    approved = await client.post(
        f"/api/v1/load-planning/agent-runs/{child_run_id}/decision",
        json={
            "decision": "APPROVE",
            "plan_code": child_body["plans"][0]["plan_code"],
            "comment": "总 Agent 测试批准装载方案",
            "expected_run_version": child_body["run_version"],
        },
    )
    assert approved.status_code == 200, approved.text
    locked = await client.post(
        f"/api/v1/load-planning/agent-runs/{child_run_id}/plan-versions",
        json={
            "expected_run_version": approved.json()["run_version"],
            "expected_current_version_number": 0,
            "lock_reason": "总 Agent 测试发布正式版本",
        },
    )
    assert locked.status_code == 201, locked.text

    assert waiting_child["next_wake_at"] is not None
    before_event_count = len(waiting_child["events"])
    processed = await _run_due_wakes(FakeLinkGateway("UNUSED-LINK-APPLICATION"))
    assert processed == 1
    reconciled = await client.get(f"/api/v1/shipment-agent-runs/{run['run_id']}")
    assert reconciled.status_code == 200, reconciled.text
    ready_to_complete = reconciled.json()
    assert ready_to_complete["locked_load_version_id"] == locked.json()["version_id"]
    assert ready_to_complete["locked_load_version_number"] == 1
    assert ready_to_complete["current_stage"] == "AWAITING_COMPLETION_APPROVAL"
    assert ready_to_complete["next_wake_at"] is None
    assert len(ready_to_complete["events"]) > before_event_count

    completed = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("complete-load-agent-001"),
        json={
            "expected_run_version": ready_to_complete["run_version"],
            "interrupt_type": "COMPLETION_APPROVAL",
            "action": "APPROVE",
            "comment": "人工确认结案",
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["case_status"] == "COMPLETED"
    assert completed.json()["events"][-1]["actor_display_name"] == "SYSTEM_DEMO"

    active = await client.get(f"/api/v1/shipment-cases/{case_number}/agent-runs/active")
    global_runs = await client.get("/api/v1/shipment-agent-runs")
    history = await client.get(f"/api/v1/shipment-cases/{case_number}/agent-runs/history")
    assert active.status_code == 200
    assert active.json() is None
    assert global_runs.status_code == history.status_code == 200
    assert global_runs.json()[0]["run_id"] == run["run_id"]
    assert history.json()[0]["run_id"] == run["run_id"]


async def test_booking_only_requires_submit_and_dead_retry_approvals(
    client: AsyncClient,
) -> None:
    """LINK 提交和 DEAD 重试分别停在两个明确审批节点。"""

    case_number = "SHP-DEMO-AGENT-BOOKING-001"
    application_number = "DEMO-BOOKING-SHIPMENT-001"
    verifier = FakeBookingResourceVerifier(application_number)
    gateway = FakeLinkGateway(application_number)
    app.dependency_overrides[get_shipment_resource_verifier] = lambda: verifier
    app.dependency_overrides[get_link_booking_gateway] = lambda: gateway
    try:
        case = await _create_case(client, case_number, "BOOKING_ONLY")
        bound = await client.post(
            f"/api/v1/shipment-cases/{case_number}/resources",
            headers=_headers("bind-booking-agent-001"),
            json={
                "resource_type": "LINK_BOOKING_APPLICATION",
                "resource_number": application_number,
                "expected_revision": case["revision"],
            },
        )
        assert bound.status_code == 200, bound.text

        started = await client.post(
            f"/api/v1/shipment-cases/{case_number}/agent-runs",
            headers=_headers("start-booking-agent-001"),
            json={"expected_case_revision": bound.json()["revision"]},
        )
        assert started.status_code == 201, started.text
        run = started.json()
        assert run["data_readiness"]["status"] == "READY"
        assert run["data_readiness"]["source_mode"] == "NOT_APPLICABLE"
        assert run["data_readiness"]["issue_count"] == 0

        activated = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("activate-booking-agent-001"),
            json={
                "expected_run_version": run["run_version"],
                "interrupt_type": "CASE_ACTIVATION_APPROVAL",
                "action": "APPROVE",
            },
        )
        assert activated.status_code == 200, activated.text
        draft = activated.json()
        assert draft["current_stage"] == "AWAITING_BOOKING_SUBMIT_APPROVAL"

        submitted = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("submit-booking-agent-001"),
            json={
                "expected_run_version": draft["run_version"],
                "interrupt_type": "BOOKING_SUBMIT_APPROVAL",
                "action": "APPROVE",
            },
        )
        assert submitted.status_code == 200, submitted.text
        dead = submitted.json()
        assert gateway.submit_calls == 1
        assert dead["current_stage"] == "AWAITING_BOOKING_RETRY_APPROVAL"
        assert dead["carrier_submission_status"] == "DEAD"

        retried = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("retry-booking-agent-001"),
            json={
                "expected_run_version": dead["run_version"],
                "interrupt_type": "BOOKING_RETRY_APPROVAL",
                "action": "APPROVE",
            },
        )
        assert retried.status_code == 200, retried.text
        waiting = retried.json()
        assert gateway.retry_calls == 1
        assert waiting["status"] == "WAITING_EXTERNAL"
        assert waiting["current_stage"] == "AWAITING_BOOKING_RESULT"

        gateway.application_status = "CONFIRMED"
        gateway.carrier_status = "CONFIRMED"
        gateway.booking_reference = "DEMO-BOOKING-REF-001"
        assert waiting["next_wake_at"] is not None
        processed = await _run_due_wakes(gateway)
        assert processed == 1
        confirmed = await client.get(f"/api/v1/shipment-agent-runs/{run['run_id']}")
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["booking_status"] == "CONFIRMED"
        assert body["booking_reference"] == "DEMO-BOOKING-REF-001"
        assert body["current_stage"] == "AWAITING_COMPLETION_APPROVAL"
        assert body["next_wake_at"] is None
    finally:
        app.dependency_overrides.pop(get_shipment_resource_verifier, None)
        app.dependency_overrides.pop(get_link_booking_gateway, None)


async def test_booking_poll_records_snapshot_cleanup_in_version_and_trace(
    client: AsyncClient,
) -> None:
    """LINK 状态未变但快照字段被修复时，不能发生无版本、无 Trace 的静默写入。"""

    case_number = "SHP-DEMO-AGENT-BOOKING-SNAPSHOT-001"
    application_number = "DEMO-BOOKING-SNAPSHOT-001"
    verifier = FakeBookingResourceVerifier(application_number)
    gateway = FakeLinkGateway(application_number)
    app.dependency_overrides[get_shipment_resource_verifier] = lambda: verifier
    app.dependency_overrides[get_link_booking_gateway] = lambda: gateway
    try:
        case = await _create_case(client, case_number, "BOOKING_ONLY")
        bound = await client.post(
            f"/api/v1/shipment-cases/{case_number}/resources",
            headers=_headers("bind-booking-snapshot-001"),
            json={
                "resource_type": "LINK_BOOKING_APPLICATION",
                "resource_number": application_number,
                "expected_revision": case["revision"],
            },
        )
        assert bound.status_code == 200, bound.text

        started = await client.post(
            f"/api/v1/shipment-cases/{case_number}/agent-runs",
            headers=_headers("start-booking-snapshot-001"),
            json={"expected_case_revision": bound.json()["revision"]},
        )
        assert started.status_code == 201, started.text
        run = started.json()

        activated = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("activate-booking-snapshot-001"),
            json={
                "expected_run_version": run["run_version"],
                "interrupt_type": "CASE_ACTIVATION_APPROVAL",
                "action": "APPROVE",
            },
        )
        assert activated.status_code == 200, activated.text

        submitted = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("submit-booking-snapshot-001"),
            json={
                "expected_run_version": activated.json()["run_version"],
                "interrupt_type": "BOOKING_SUBMIT_APPROVAL",
                "action": "APPROVE",
            },
        )
        assert submitted.status_code == 200, submitted.text

        retried = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("retry-booking-snapshot-001"),
            json={
                "expected_run_version": submitted.json()["run_version"],
                "interrupt_type": "BOOKING_RETRY_APPROVAL",
                "action": "APPROVE",
            },
        )
        assert retried.status_code == 200, retried.text
        waiting = retried.json()
        assert waiting["status"] == "WAITING_EXTERNAL"
        assert waiting["booking_status"] == "SUBMITTED"
        assert waiting["carrier_submission_status"] == "PROCESSING"

        # 模拟旧版本留下的脏投影：LINK/承运人主状态与远端完全相同，只有附属字段
        # 需要由下一次只读轮询清理。修复前这些值会被提交，但 run_version/Trace 不变。
        session_factory = app.dependency_overrides[get_session_factory]()
        async with session_factory() as session, session.begin():
            stored = await session.get(ShipmentAgentRun, run["run_id"])
            assert stored is not None
            stored.booking_submission_uncertain = True
            stored.booking_reference = "STALE-BOOKING-REFERENCE"
            stored.failure_code = "STALE_LINK_FAILURE"
            stored.failure_message = "旧的 LINK 故障信息"

        before = await client.get(f"/api/v1/shipment-agent-runs/{run['run_id']}")
        assert before.status_code == 200, before.text
        before_body = before.json()

        assert await _run_due_wakes(gateway) == 1

        after = await client.get(f"/api/v1/shipment-agent-runs/{run['run_id']}")
        assert after.status_code == 200, after.text
        after_body = after.json()
        assert after_body["booking_status"] == "SUBMITTED"
        assert after_body["carrier_submission_status"] == "PROCESSING"
        assert after_body["booking_submission_uncertain"] is False
        assert after_body["booking_reference"] is None
        assert after_body["failure_code"] is None
        assert after_body["failure_message"] is None
        assert after_body["run_version"] == before_body["run_version"] + 1
        assert len(after_body["events"]) == len(before_body["events"]) + 1
        assert after_body["events"][-1]["event_type"] == "CARRIER_CALLBACK_PENDING"
    finally:
        app.dependency_overrides.pop(get_shipment_resource_verifier, None)
        app.dependency_overrides.pop(get_link_booking_gateway, None)


async def test_missing_required_binding_waits_without_fake_human_interrupt(
    client: AsyncClient,
) -> None:
    """缺少业务资源时结束本次 Graph 调用，页面只显示绑定后重新检查。"""

    case_number = "SHP-DEMO-AGENT-MISSING-RESOURCE-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")

    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-missing-resource-agent-001"),
        json={"expected_case_revision": case["revision"]},
    )
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["status"] == "WAITING_RESOURCE"
    assert run["current_stage"] == "AWAITING_RESOURCE_BINDING"
    assert run["pending_interrupt"] is None
    assert run["allowed_actions"] == ["RECHECK", "STOP"]
    assert run["events"][-1]["event_type"] == "RESOURCE_BINDING_REQUIRED"


async def test_multiple_bound_resources_require_explicit_selection(
    client: AsyncClient,
) -> None:
    """多个候选不能自动猜测；选中后继续到案件激活审批。"""

    case_number = "SHP-DEMO-AGENT-MULTI-RESOURCE-001"
    task_numbers = [
        "LP-DEMO-AGENT-MULTI-001",
        "LP-DEMO-AGENT-MULTI-002",
    ]
    for task_number in task_numbers:
        created_task = await client.post(
            "/api/v1/load-planning-tasks",
            json=_task_payload(task_number),
        )
        assert created_task.status_code == 201, created_task.text

    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    revision = case["revision"]
    for index, task_number in enumerate(task_numbers, start=1):
        bound = await client.post(
            f"/api/v1/shipment-cases/{case_number}/resources",
            headers=_headers(f"bind-multi-load-agent-{index:03d}"),
            json={
                "resource_type": "LOAD_PLANNING_TASK",
                "resource_number": task_number,
                "expected_revision": revision,
            },
        )
        assert bound.status_code == 200, bound.text
        revision = bound.json()["revision"]

    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-multi-resource-agent-001"),
        json={"expected_case_revision": revision},
    )
    assert started.status_code == 201, started.text
    selecting = started.json()
    assert selecting["status"] == "WAITING_HUMAN"
    assert selecting["current_stage"] == "AWAITING_RESOURCE_SELECTION"
    assert selecting["pending_interrupt"]["type"] == "RESOURCE_SELECTION"
    assert selecting["pending_interrupt"]["details"] == {
        "resource_type": "LOAD_PLANNING_TASK",
        "candidates": sorted(task_numbers),
    }

    selection_payload = {
        "expected_run_version": selecting["run_version"],
        "interrupt_type": "RESOURCE_SELECTION",
        "action": "SELECT",
        "selected_resource_number": task_numbers[1],
    }
    selected = await client.post(
        f"/api/v1/shipment-agent-runs/{selecting['run_id']}/resume",
        headers=_headers("select-multi-resource-agent-001"),
        json=selection_payload,
    )
    replayed_selection = await client.post(
        f"/api/v1/shipment-agent-runs/{selecting['run_id']}/resume",
        headers=_headers("select-multi-resource-agent-001"),
        json=selection_payload,
    )
    assert selected.status_code == replayed_selection.status_code == 200
    remediation = selected.json()
    assert remediation["selected_load_task_number"] == task_numbers[1]
    assert remediation["status"] == "WAITING_HUMAN"
    assert remediation["current_stage"] == "AWAITING_DATA_REMEDIATION"
    assert remediation["pending_interrupt"]["type"] == "DATA_READINESS_REMEDIATION"
    assert replayed_selection.json()["run_version"] == remediation["run_version"]

    activation = await _supply_default_demo_readiness(
        client,
        remediation,
        key="supply-multi-readiness-001",
    )
    assert activation["selected_load_task_number"] == task_numbers[1]


async def test_wake_lease_prevents_duplicate_claim_and_noop_poll_is_quiet(
    client: AsyncClient,
) -> None:
    """两个 Worker 看到同一到期运行时只允许一个领取；空轮询不污染 Trace。"""

    case_number = "SHP-DEMO-WAKE-LEASE-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-wake-lease-001"),
        json={"expected_case_revision": case["revision"]},
    )
    assert started.status_code == 201, started.text
    before = started.json()
    assert before["status"] == "WAITING_RESOURCE"
    assert before["next_wake_at"] is not None
    assert len(before["notifications"]) == 1

    worker = _wake_worker(FakeLinkGateway("UNUSED-WAKE-LEASE"))
    future = datetime.now(UTC) + timedelta(minutes=1)
    first = await worker.automation.claim_due(limit=20, now=future)
    second = await worker.automation.claim_due(limit=20, now=future)
    assert len(first) == 1
    assert second == []

    await worker.orchestrator.automatic_wake(first[0].run_id)
    projected = await worker.automation.project(
        first[0].run_id,
        lease_token=first[0].lease_token,
        successful_wake=True,
        now=future,
    )
    assert projected is True

    response = await client.get(f"/api/v1/shipment-agent-runs/{before['run_id']}")
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["run_version"] == before["run_version"]
    assert len(after["events"]) == len(before["events"])
    assert len(after["notifications"]) == len(before["notifications"])
    assert after["next_wake_at"] is not None


async def test_expired_lease_takeover_rejects_late_owner_control_writes(
    client: AsyncClient,
) -> None:
    """新 Worker 接管过期租约后，旧 token 不能续租、投影或写入失败退避。"""

    case_number = "SHP-DEMO-WAKE-TAKEOVER-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-wake-takeover-001"),
        json={"expected_case_revision": case["revision"]},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    first_worker = _wake_worker(
        FakeLinkGateway("UNUSED-WAKE-TAKEOVER-A"),
        lease_seconds=1,
    )
    second_worker = _wake_worker(
        FakeLinkGateway("UNUSED-WAKE-TAKEOVER-B"),
        lease_seconds=30,
    )
    first_claim_at = datetime.now(UTC) + timedelta(minutes=1)
    first_leases = await first_worker.automation.claim_due(
        limit=1,
        now=first_claim_at,
    )
    assert len(first_leases) == 1
    first_lease = first_leases[0]

    takeover_at = first_claim_at + timedelta(seconds=2)
    second_leases = await second_worker.automation.claim_due(
        limit=1,
        now=takeover_at,
    )
    assert len(second_leases) == 1
    second_lease = second_leases[0]
    assert second_lease.run_id == run_id
    assert second_lease.lease_token != first_lease.lease_token

    assert (
        await first_worker.automation.renew_lease(
            first_lease,
            now=takeover_at,
        )
        is False
    )
    assert (
        await first_worker.automation.project(
            run_id,
            lease_token=first_lease.lease_token,
            successful_wake=True,
            now=takeover_at,
        )
        is False
    )
    assert (
        await first_worker.automation.mark_wake_failed(
            first_lease,
            RuntimeError("late owner"),
            now=takeover_at,
        )
        is False
    )

    assert (
        await second_worker.automation.project(
            run_id,
            lease_token=second_lease.lease_token,
            successful_wake=True,
            now=takeover_at,
        )
        is True
    )
    response = await client.get(f"/api/v1/shipment-agent-runs/{run_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_wake_at"] is not None
    assert body["wake_reason"] == "POLL_RESOURCE_BINDING"
    assert all(
        notification["notification_type"] != "AUTO_WAKE_FAILED"
        for notification in body["notifications"]
    )

    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session:
        run = await ShipmentAgentRunRepository(session).get(run_id)
        assert run is not None
        assert run.wake_lease_token is None
        assert run.wake_lease_until is None
        assert run.wake_attempt_count == 0


async def test_wake_failure_uses_backoff_and_deduplicated_notification(
    client: AsyncClient,
    monkeypatch,
) -> None:
    """单轮故障不杀死服务；恢复后运维通知自动解决。"""

    case_number = "SHP-DEMO-WAKE-FAILURE-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-wake-failure-001"),
        json={"expected_case_revision": case["revision"]},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    worker = _wake_worker(FakeLinkGateway("UNUSED-WAKE-FAILURE"))

    async def fail_wake(_run_id: str):
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(worker.orchestrator, "automatic_wake", fail_wake)
    future = datetime.now(UTC) + timedelta(minutes=1)
    assert await worker.run_once(now=future) == 1

    failed = await client.get(f"/api/v1/shipment-agent-runs/{run_id}")
    assert failed.status_code == 200, failed.text
    failed_body = failed.json()
    operations = [
        value
        for value in failed_body["notifications"]
        if value["notification_type"] == "AUTO_WAKE_FAILED"
    ]
    assert len(operations) == 1
    assert operations[0]["status"] == "OPEN"
    assert operations[0]["occurrence_count"] == 1
    assert failed_body["next_wake_at"] is not None
    assert failed_body["wake_reason"] == "RETRY_AFTER_AUTO_WAKE_FAILURE"
    first_retry_at = datetime.fromisoformat(failed_body["next_wake_at"])
    assert 0.9 <= (first_retry_at - future).total_seconds() <= 1.1

    second_failure_at = future + timedelta(minutes=1)
    assert await worker.run_once(now=second_failure_at) == 1
    failed_again = await client.get(f"/api/v1/shipment-agent-runs/{run_id}")
    assert failed_again.status_code == 200, failed_again.text
    failed_again_body = failed_again.json()
    repeated_operations = [
        value
        for value in failed_again_body["notifications"]
        if value["notification_type"] == "AUTO_WAKE_FAILED"
    ]
    assert len(repeated_operations) == 1
    assert repeated_operations[0]["status"] == "OPEN"
    assert repeated_operations[0]["occurrence_count"] == 2
    second_retry_at = datetime.fromisoformat(failed_again_body["next_wake_at"])
    assert 1.9 <= (second_retry_at - second_failure_at).total_seconds() <= 2.1

    recovered_worker = _wake_worker(FakeLinkGateway("UNUSED-WAKE-RECOVERY"))
    assert (
        await recovered_worker.run_once(
            now=second_failure_at + timedelta(minutes=1),
        )
        == 1
    )
    recovered = await client.get(f"/api/v1/shipment-agent-runs/{run_id}")
    recovered_body = recovered.json()
    recovered_operations = [
        value
        for value in recovered_body["notifications"]
        if value["notification_type"] == "AUTO_WAKE_FAILED"
    ]
    assert len(recovered_operations) == 1
    assert recovered_operations[0]["status"] == "RESOLVED"


async def test_expired_residual_lease_repairs_human_notification_projection(
    client: AsyncClient,
) -> None:
    """业务已进入人工节点但投影前崩溃时，过期旧租约仍可被接管修复。"""

    case_number = "SHP-DEMO-WAKE-PROJECTION-RECOVERY-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-wake-projection-recovery-001"),
        json={"expected_case_revision": case["revision"]},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    worker = _wake_worker(FakeLinkGateway("UNUSED-PROJECTION-RECOVERY"))
    first_wake_at = datetime.now(UTC) + timedelta(minutes=1)
    leases = await worker.automation.claim_due(limit=1, now=first_wake_at)
    assert len(leases) == 1
    original_lease = leases[0]

    # 刻意只提交“业务推进到人工节点”，不调用 automation.project，模拟进程在两者之间
    # 崩溃。旧租约、旧业务通知和空 next_wake_at 都原样留在数据库中。
    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session:
        repository = ShipmentAgentRunRepository(session)
        async with session.begin():
            run = await repository.get(run_id, for_update=True)
            assert run is not None
            run.status = ShipmentAgentRunStatus.WAITING_HUMAN
            run.current_stage = ShipmentAgentStage.AWAITING_DATA_REMEDIATION
            run.pending_interrupt = {
                "type": "DATA_READINESS_REMEDIATION",
                "title": "补充装载数据",
                "description": "请补充缺失的库存和主数据事实。",
                "allowed_actions": ["SUPPLY_DATA", "STOP"],
                "details": {},
            }
            run.allowed_actions = ["SUPPLY_DATA", "STOP"]

    expired_at = first_wake_at + timedelta(seconds=31)
    # 已过期 token 不能通过续租重新获得所有权。
    assert (
        await worker.automation.renew_lease(
            original_lease,
            now=expired_at,
        )
        is False
    )
    assert await worker.run_once(now=expired_at) == 1

    recovered = await client.get(f"/api/v1/shipment-agent-runs/{run_id}")
    assert recovered.status_code == 200, recovered.text
    body = recovered.json()
    assert body["status"] == "WAITING_HUMAN"
    assert body["next_wake_at"] is None
    assert body["wake_reason"] is None
    open_notifications = [value for value in body["notifications"] if value["status"] == "OPEN"]
    assert len(open_notifications) == 1
    assert open_notifications[0]["notification_type"] == "ACTION_REQUIRED"
    assert open_notifications[0]["title"] == "补充装载数据"

    async with session_factory() as session:
        run = await ShipmentAgentRunRepository(session).get(run_id)
        assert run is not None
        assert run.wake_lease_token is None
        assert run.wake_lease_until is None
        assert run.wake_attempt_count == 0


async def test_http_commit_without_lease_repairs_human_projection_once(
    client: AsyncClient,
) -> None:
    """HTTP 业务事务提交后若投影前崩溃，Worker 只补通知且不会反复领取。"""

    case_number = "SHP-DEMO-HTTP-PROJECTION-RECOVERY-001"
    task_number = "LP-DEMO-HTTP-PROJECTION-RECOVERY-001"
    created_task = await client.post(
        "/api/v1/load-planning-tasks",
        json=_task_payload(task_number),
    )
    assert created_task.status_code == 201, created_task.text
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    bound = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        headers=_headers("bind-http-projection-recovery-001"),
        json={
            "resource_type": "LOAD_PLANNING_TASK",
            "resource_number": task_number,
            "expected_revision": case["revision"],
        },
    )
    assert bound.status_code == 200, bound.text

    # 直接调用领域服务，刻意不经过 Orchestrator 的 automation.project，等价于 HTTP
    # 进程在业务事务 COMMIT 成功后、控制面投影事务开始前崩溃。
    session_factory = app.dependency_overrides[get_session_factory]()
    service = ShipmentAgentService(
        session_factory,
        FakeLinkGateway("UNUSED-HTTP-PROJECTION-RECOVERY"),
        app.state.load_planning_checkpointer,
        running_recovery_seconds=1,
    )
    started = await service.start(
        case_number,
        ShipmentAgentStart(expected_case_revision=bound.json()["revision"]),
        "start-http-projection-recovery-001",
    )
    selected = await service.evaluate(started.run_id)
    assert selected.status is ShipmentAgentRunStatus.RUNNING
    assessing = await service.evaluate(started.run_id)
    assert assessing.current_stage is ShipmentAgentStage.ASSESSING_DATA_READINESS
    waiting_human = await service.assess_data_readiness(started.run_id)
    assert waiting_human.status is ShipmentAgentRunStatus.WAITING_HUMAN
    assert waiting_human.current_stage is ShipmentAgentStage.AWAITING_DATA_REMEDIATION
    assert waiting_human.wake_reason == SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON
    assert waiting_human.next_wake_at is not None
    assert waiting_human.notifications == []
    version_before_repair = waiting_human.run_version
    event_count_before_repair = len(waiting_human.events)

    worker = _wake_worker(
        FakeLinkGateway("UNUSED-HTTP-PROJECTION-RECOVERY"),
        recovery_grace_seconds=1,
    )
    due_at = waiting_human.next_wake_at + timedelta(seconds=1)
    assert await worker.run_once(now=due_at) == 1

    recovered = await service.get(started.run_id)
    assert recovered.status is ShipmentAgentRunStatus.WAITING_HUMAN
    assert recovered.current_stage is ShipmentAgentStage.AWAITING_DATA_REMEDIATION
    assert recovered.run_version == version_before_repair
    assert len(recovered.events) == event_count_before_repair
    assert recovered.next_wake_at is None
    assert recovered.wake_reason is None
    assert len(recovered.notifications) == 1
    assert recovered.notifications[0].notification_type.value == "ACTION_REQUIRED"
    assert recovered.notifications[0].status.value == "OPEN"

    # 成功投影已清除修复 reason；人工节点没有正常轮询计划，下一轮不能再次领取。
    assert await worker.run_once(now=due_at + timedelta(minutes=1)) == 0


async def test_http_commit_without_lease_repairs_terminal_projection_once(
    client: AsyncClient,
) -> None:
    """直接失败事务也会留下修复标记，Worker 只生成失败通知、不恢复业务 Graph。"""

    case_number = "SHP-DEMO-HTTP-TERMINAL-RECOVERY-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    session_factory = app.dependency_overrides[get_session_factory]()
    service = ShipmentAgentService(
        session_factory,
        FakeLinkGateway("UNUSED-HTTP-TERMINAL-RECOVERY"),
        app.state.load_planning_checkpointer,
        running_recovery_seconds=1,
    )
    started = await service.start(
        case_number,
        ShipmentAgentStart(expected_case_revision=case["revision"]),
        "start-http-terminal-recovery-001",
    )

    # 构造 assess_data_readiness 的 LOAD_RESOURCE_MISSING 失败前置状态，覆盖这条不经
    # _mutate 的直接事务路径；该方法提交 FAILED 后不调用 automation.project。
    async with session_factory() as session:
        repository = ShipmentAgentRunRepository(session)
        async with session.begin():
            run = await repository.get(started.run_id, for_update=True)
            assert run is not None
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.ASSESSING_DATA_READINESS
            run.selected_load_task_number = None

    failed = await service.assess_data_readiness(started.run_id)
    assert failed.status is ShipmentAgentRunStatus.FAILED
    assert failed.failure_code == "LOAD_RESOURCE_MISSING"
    assert failed.wake_reason == SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON
    assert failed.next_wake_at is not None
    assert failed.notifications == []
    version_before_repair = failed.run_version
    event_count_before_repair = len(failed.events)

    worker = _wake_worker(
        FakeLinkGateway("UNUSED-HTTP-TERMINAL-RECOVERY"),
        recovery_grace_seconds=1,
    )
    due_at = failed.next_wake_at + timedelta(seconds=1)
    assert await worker.run_once(now=due_at) == 1
    recovered = await service.get(started.run_id)
    assert recovered.status is ShipmentAgentRunStatus.FAILED
    assert recovered.run_version == version_before_repair
    assert len(recovered.events) == event_count_before_repair
    assert recovered.next_wake_at is None
    assert recovered.wake_reason is None
    assert len(recovered.notifications) == 1
    assert recovered.notifications[0].notification_type.value == "RUN_FAILED"
    assert recovered.notifications[0].status.value == "OPEN"
    assert await worker.run_once(now=due_at + timedelta(minutes=1)) == 0


async def test_data_readiness_missing_task_failure_returns_after_commit(
    client: AsyncClient,
) -> None:
    """LOAD_TASK_NOT_FOUND 早退也在提交后重读，不访问 flush 后过期的 ORM 属性。"""

    case_number = "SHP-DEMO-MISSING-READINESS-TASK-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    session_factory = app.dependency_overrides[get_session_factory]()
    service = ShipmentAgentService(
        session_factory,
        FakeLinkGateway("UNUSED-MISSING-READINESS-TASK"),
        app.state.load_planning_checkpointer,
        running_recovery_seconds=1,
    )
    started = await service.start(
        case_number,
        ShipmentAgentStart(expected_case_revision=case["revision"]),
        "start-missing-readiness-task-001",
    )
    async with session_factory() as session:
        repository = ShipmentAgentRunRepository(session)
        async with session.begin():
            run = await repository.get(started.run_id, for_update=True)
            assert run is not None
            run.status = ShipmentAgentRunStatus.RUNNING
            run.current_stage = ShipmentAgentStage.ASSESSING_DATA_READINESS
            run.selected_load_task_number = "LP-NOT-FOUND-READINESS-001"

    failed = await service.assess_data_readiness(started.run_id)
    assert failed.status is ShipmentAgentRunStatus.FAILED
    assert failed.current_stage is ShipmentAgentStage.FAILED
    assert failed.failure_code == "LOAD_TASK_NOT_FOUND"
    assert failed.wake_reason == SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON
    assert failed.next_wake_at is not None


async def test_wake_failure_after_business_reaches_human_clears_false_retry(
    client: AsyncClient,
) -> None:
    """业务已成功到人工节点时，随后投影异常不能留下永远不会执行的重试。"""

    case_number = "SHP-DEMO-WAKE-HUMAN-FAILURE-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers("start-wake-human-failure-001"),
        json={"expected_case_revision": case["revision"]},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    worker = _wake_worker(FakeLinkGateway("UNUSED-HUMAN-FAILURE"))
    wake_at = datetime.now(UTC) + timedelta(minutes=1)
    leases = await worker.automation.claim_due(limit=1, now=wake_at)
    assert len(leases) == 1

    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session:
        repository = ShipmentAgentRunRepository(session)
        async with session.begin():
            run = await repository.get(run_id, for_update=True)
            assert run is not None
            run.status = ShipmentAgentRunStatus.WAITING_HUMAN
            run.current_stage = ShipmentAgentStage.AWAITING_LOAD_REVIEW
            run.pending_interrupt = {
                "type": "DATA_READINESS_REMEDIATION",
                "title": "人工复核装载方案",
                "description": "装载方案已经生成，请业务人员复核。",
                "allowed_actions": ["STOP"],
                "details": {},
            }
            run.allowed_actions = ["STOP"]

    marked = await worker.automation.mark_wake_failed(
        leases[0],
        RuntimeError("projection crashed"),
        now=wake_at + timedelta(seconds=1),
    )
    assert marked is True
    response = await client.get(f"/api/v1/shipment-agent-runs/{run_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_wake_at"] is None
    assert body["wake_reason"] is None
    assert all(value["notification_type"] != "AUTO_WAKE_FAILED" for value in body["notifications"])
    open_notifications = [value for value in body["notifications"] if value["status"] == "OPEN"]
    assert len(open_notifications) == 1
    assert open_notifications[0]["notification_type"] == "ACTION_REQUIRED"

    async with session_factory() as session:
        run = await ShipmentAgentRunRepository(session).get(run_id)
        assert run is not None
        assert run.wake_lease_token is None
        assert run.wake_lease_until is None
        assert run.wake_attempt_count == 0
        assert run.wake_last_error is None


async def test_worker_claims_one_at_a_time_and_renews_slow_lease(
    client: AsyncClient,
    monkeypatch,
) -> None:
    """短租约慢任务不会预占整批；处理中的唯一租约由 heartbeat 保持有效。"""

    run_ids: list[str] = []
    for index in range(2):
        case_number = f"SHP-DEMO-WAKE-SLOW-{index + 1:03d}"
        case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
        started = await client.post(
            f"/api/v1/shipment-cases/{case_number}/agent-runs",
            headers=_headers(f"start-wake-slow-{index + 1:03d}"),
            json={"expected_case_revision": case["revision"]},
        )
        assert started.status_code == 201, started.text
        run_ids.append(started.json()["run_id"])

    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session, session.begin():
        await session.execute(
            update(ShipmentAgentRun)
            .where(ShipmentAgentRun.id.in_(run_ids))
            .values(
                next_wake_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
                wake_reason="POLL_RESOURCE_BINDING",
                updated_at=ShipmentAgentRun.updated_at,
            )
            .execution_options(synchronize_session=False)
        )

    worker = _wake_worker(
        FakeLinkGateway("UNUSED-SLOW-LEASE"),
        lease_seconds=0.15,
        batch_size=2,
        recovery_grace_seconds=0.1,
    )
    original_wake = worker.orchestrator.automatic_wake
    started_ids: list[str] = []
    first_finished = asyncio.Event()

    async def slow_wake(run_id: str):
        started_ids.append(run_id)
        if len(started_ids) == 1:
            async with session_factory() as session:
                leased_runs = [
                    await ShipmentAgentRunRepository(session).get(value) for value in run_ids
                ]
            assert (
                sum(
                    value is not None and value.wake_lease_token is not None
                    for value in leased_runs
                )
                == 1
            )

            # 处理时间显著超过 150ms 租期；heartbeat 应至少续租数次。
            await asyncio.sleep(0.35)
            async with session_factory() as session:
                current = await ShipmentAgentRunRepository(session).get(run_id)
            assert current is not None
            assert current.wake_lease_until is not None
            assert current.wake_lease_until > datetime.now(UTC).replace(tzinfo=None)
            result = await original_wake(run_id)
            first_finished.set()
            return result

        assert first_finished.is_set()
        return await original_wake(run_id)

    monkeypatch.setattr(worker.orchestrator, "automatic_wake", slow_wake)
    assert await worker.run_once() == 2
    assert len(started_ids) == 2
    assert set(started_ids) == set(run_ids)


async def test_new_running_run_has_explicit_recovery_schedule(
    client: AsyncClient,
) -> None:
    """RUNNING 事务显式落恢复时间，不再只靠 updated_at 猜测是否卡死。"""

    case_number = "SHP-DEMO-RUNNING-RECOVERY-001"
    case = await _create_case(client, case_number, "LOAD_PLANNING_ONLY")
    session_factory = app.dependency_overrides[get_session_factory]()
    service = ShipmentAgentService(
        session_factory,
        FakeLinkGateway("UNUSED-RUNNING-RECOVERY"),
        app.state.load_planning_checkpointer,
        running_recovery_seconds=120,
    )
    before = datetime.now(UTC)
    run = await service.start(
        case_number,
        ShipmentAgentStart(expected_case_revision=case["revision"]),
        "start-running-recovery-001",
    )
    assert run.status is ShipmentAgentRunStatus.RUNNING
    assert run.wake_reason == "RECOVER_RUNNING_STEP"
    assert run.next_wake_at is not None
    assert 115 <= (run.next_wake_at - before).total_seconds() <= 125
