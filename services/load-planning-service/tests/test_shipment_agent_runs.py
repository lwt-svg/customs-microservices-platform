"""Shipment Coordinator Agent 的聚焦 REST 契约测试。

测试通过真实 FastAPI、SQLAlchemy、LangGraph 和装载子 Agent 走闭环；只有 LINK HTTP 与
案件绑定的远程资源验证使用可控 fake。所有编号均为构造演示值。
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from httpx import AsyncClient

from app.api.shipment_agent_runs import get_link_booking_gateway
from app.api.shipment_cases import get_shipment_resource_verifier
from app.integrations.shipment_agent_resources import LinkBookingSnapshot
from app.integrations.shipment_resources import VerifiedShipmentResource
from app.main import app
from app.models.shipment_case import ShipmentResourceType


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

    reconciled = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/reconcile",
        headers=_headers("reconcile-load-agent-001"),
        json={
            "expected_run_version": waiting_child["run_version"],
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    ready_to_complete = reconciled.json()
    assert ready_to_complete["locked_load_version_id"] == locked.json()["version_id"]
    assert ready_to_complete["locked_load_version_number"] == 1
    assert ready_to_complete["current_stage"] == "AWAITING_COMPLETION_APPROVAL"

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
        confirmed = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/reconcile",
            headers=_headers("reconcile-booking-agent-001"),
            json={"expected_run_version": waiting["run_version"]},
        )
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["booking_status"] == "CONFIRMED"
        assert body["booking_reference"] == "DEMO-BOOKING-REF-001"
        assert body["current_stage"] == "AWAITING_COMPLETION_APPROVAL"
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
