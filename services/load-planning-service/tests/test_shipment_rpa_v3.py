"""Shipment Coordinator V3 与统一 RPA Job 的闭环契约测试。

本文件只验证 V3 新增的 LINK 草稿 RPA 分支。既有 V2 流程继续由
``test_shipment_agent_runs.py`` 锁定，二者分开后可以清楚看出：

* V2 直接读取已经存在的 LINK 草稿，再等待人工批准提交；
* V3 先让 RPA 自动完成七步填单并保存草稿，再回到同一条人工提交链。

测试使用内存 SQLite、真实 LangGraph、真实 RPA Job Service 和可控的 LINK fake，
不会访问企业网站，也不会写入真实订单。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.api.shipment_cases import get_shipment_resource_verifier
from app.db.session import get_session_factory
from app.integrations.shipment_agent_resources import (
    LinkBookingSnapshot,
    LinkRpaBookingDetails,
    LinkRpaDraftPayload,
)
from app.integrations.shipment_resources import VerifiedShipmentResource
from app.main import app
from app.models.rpa_job import RpaJobRecord
from app.models.shipment_agent_run import (
    ShipmentAgentCommand,
    ShipmentAgentRpaJobBinding,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.models.shipment_case import ShipmentResourceType
from app.rpa.contracts import RpaEnvironment
from app.schemas.shipment_agent_run import (
    ShipmentAgentResume,
    ShipmentAgentRunRead,
    ShipmentAgentStart,
)
from app.services.shipment_agent_automation import ShipmentAgentAutomationService
from app.services.shipment_agent_runs import ShipmentAgentService
from app.workers.shipment_agent_wake_worker import ShipmentAgentWakeWorker
from app.workflows.shipment_case import ShipmentWorkflowOrchestrator

RPA_WAKE_REASON = "RPA_JOB_STATE_CHANGED"
SECRET_PAYLOAD_MARKS = "SECRET-RPA-PAYLOAD-MARKS-DO-NOT-EXPOSE"


@dataclass(slots=True)
class FakeBookingResourceVerifier:
    """只证明演示 LINK 申请存在，不替 RPA 生成业务数据。"""

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
            verification_source="SHIPMENT_RPA_V3_TEST_FAKE",
            verified_at=datetime.now(UTC),
        )


@dataclass(slots=True)
class FakeLinkGateway:
    """同时提供旧 LINK 状态快照和 V3 RPA 所需的强类型草稿载荷。"""

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
                "DEMO-CARRIER-RPA-V3-001" if self.carrier_status is not None else None
            ),
            rpa_payload=LinkRpaDraftPayload(
                application_number=application_number,
                origin_port="DEMO-CNSHA",
                destination_port="DEMO-FRLEH",
                transport_mode="SEA",
                container_type="40HQ",
                requested_etd=date(2026, 11, 15),
                booking_details=LinkRpaBookingDetails(
                    invoice_number="DEMO-INV-RPA-V3-001",
                    marks=SECRET_PAYLOAD_MARKS,
                    total_pallets=2,
                    total_cartons=20,
                    total_quantity=200,
                    total_cbm=18.5,
                    total_net_weight_kg=1800.0,
                    total_gross_weight_kg=2000.0,
                ),
            ),
        )

    async def submit(self, application_number: str) -> None:
        assert application_number == self.application_number
        self.submit_calls += 1
        self.application_status = "SUBMITTED"
        self.carrier_status = "PROCESSING"

    async def retry(self, application_number: str) -> None:
        assert application_number == self.application_number
        self.retry_calls += 1
        self.carrier_status = "PROCESSING"


@dataclass(slots=True)
class V3Stack:
    """同一测试数据库上的 V3 Service、Graph 和自动唤醒 Worker。"""

    service: ShipmentAgentService
    workflow: ShipmentWorkflowOrchestrator
    worker: ShipmentAgentWakeWorker
    gateway: FakeLinkGateway


def _headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


def _build_v3_stack(gateway: FakeLinkGateway) -> V3Stack:
    """显式开启 V3，避免依赖 conftest 为 V2 回归测试保留的关闭开关。"""

    session_factory = app.dependency_overrides[get_session_factory]()
    service = ShipmentAgentService(
        session_factory,
        gateway,
        app.state.load_planning_checkpointer,
        running_recovery_seconds=1,
        rpa_enabled=True,
        rpa_environment=RpaEnvironment.SANDBOX,
        rpa_adapter_key="link-playwright",
        rpa_selector_pack_version="link-sandbox-v1",
    )
    automation = ShipmentAgentAutomationService(
        session_factory,
        wait_seconds=1,
        resource_binding_seconds=1,
        lease_seconds=30,
        recovery_grace_seconds=1,
        retry_base_seconds=1,
        retry_max_seconds=8,
    )
    workflow = ShipmentWorkflowOrchestrator(
        service=service,
        checkpointer=app.state.load_planning_checkpointer,
        automation=automation,
    )
    worker = ShipmentAgentWakeWorker(
        automation=automation,
        orchestrator=workflow,
        poll_seconds=1,
        batch_size=20,
    )
    return V3Stack(
        service=service,
        workflow=workflow,
        worker=worker,
        gateway=gateway,
    )


async def _create_bound_booking_case(
    client: AsyncClient,
    *,
    case_number: str,
    application_number: str,
) -> int:
    """创建 BOOKING_ONLY 案件并绑定一条可被 fake 验证的 LINK 申请。"""

    verifier = FakeBookingResourceVerifier(application_number)
    app.dependency_overrides[get_shipment_resource_verifier] = lambda: verifier

    created = await client.post(
        "/api/v1/shipment-cases",
        headers=_headers(f"create-{case_number}"),
        json={
            "case_number": case_number,
            "case_name": "Shipment RPA V3 构造测试案件",
            "transport_scenario": "BOOKING_ONLY",
            "description": "只使用合成数据验证 LINK RPA 草稿闭环。",
        },
    )
    assert created.status_code == 201, created.text

    bound = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        headers=_headers(f"bind-{case_number}"),
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": application_number,
            "expected_revision": created.json()["revision"],
        },
    )
    assert bound.status_code == 200, bound.text
    return int(bound.json()["revision"])


async def _start_v3_until_rpa_wait(
    client: AsyncClient,
    *,
    case_number: str,
    application_number: str,
) -> tuple[V3Stack, ShipmentAgentRunRead]:
    """启动 V3、批准案件激活，并推进到等待 LINK RPA 结果。"""

    revision = await _create_bound_booking_case(
        client,
        case_number=case_number,
        application_number=application_number,
    )
    stack = _build_v3_stack(FakeLinkGateway(application_number))
    activation = await stack.workflow.start(
        case_number,
        ShipmentAgentStart(expected_case_revision=revision),
        f"start-{case_number}",
    )
    assert activation.status is ShipmentAgentRunStatus.WAITING_HUMAN
    assert activation.current_stage is ShipmentAgentStage.AWAITING_CASE_ACTIVATION

    waiting = await stack.workflow.resume(
        activation.run_id,
        ShipmentAgentResume(
            expected_run_version=activation.run_version,
            interrupt_type="CASE_ACTIVATION_APPROVAL",
            action="APPROVE",
            comment="V3 测试批准激活案件",
        ),
        f"activate-{case_number}",
    )
    assert waiting.status is ShipmentAgentRunStatus.WAITING_EXTERNAL
    assert waiting.current_stage is ShipmentAgentStage.AWAITING_RPA_RESULT
    assert waiting.rpa_jobs
    assert waiting.rpa_jobs[-1].operation.value == "LINK_BOOKING_STAGE"
    return stack, waiting


async def _claim_link_job(client: AsyncClient) -> dict[str, object]:
    response = await client.post(
        "/api/v1/rpa/worker/jobs/claim",
        json={
            "domain": "LINK",
            "environment": "SANDBOX",
            "worker_id": "link-rpa-v3-test-worker",
            "supported_operations": ["LINK_BOOKING_STAGE"],
        },
    )
    assert response.status_code == 200, response.text
    claimed = response.json()
    assert claimed is not None
    return claimed


async def _report_result(
    client: AsyncClient,
    claimed: dict[str, object],
    *,
    status: str,
) -> dict[str, object]:
    """按目标状态构造一份符合 RPA v1 契约的 Worker 回调。"""

    job = claimed["job"]
    assert isinstance(job, dict)
    payload: dict[str, object] = {
        "callback_id": str(uuid4()),
        "attempt_token": claimed["attempt_token"],
        "status": status,
        "side_effect_phase": "READ_ONLY",
        "message": f"构造的 {status} 回调。",
    }
    if status == "SUCCEEDED":
        payload.update(
            {
                "side_effect_phase": "EXTERNAL_EFFECT_CONFIRMED",
                "business_outcome": "LINK_DRAFT_SAVED",
                "external_reference": "DEMO-LINK-DRAFT-RPA-V3-001",
            }
        )
    elif status == "RESULT_UNKNOWN":
        payload.update(
            {
                "side_effect_phase": "EXTERNAL_MUTATION_STARTED",
                "error_code": "SAVE_RESPONSE_LOST",
            }
        )
    elif status == "SITE_VERSION_UNKNOWN":
        payload["error_code"] = "SELECTOR_PACK_MISMATCH"

    response = await client.post(
        f"/api/v1/rpa/worker/jobs/{job['job_id']}/result",
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _run_due_wakes(stack: V3Stack) -> int:
    """把测试时钟推进一分钟，让刚刚由 RPA 回调登记的运行立即被领取。"""

    return await stack.worker.run_once(now=datetime.now(UTC) + timedelta(minutes=1))


async def _count_rows(model: type[object]) -> int:
    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session:
        value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def _shipment_command_count(run_id: str) -> int:
    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session:
        value = await session.scalar(
            select(func.count(ShipmentAgentCommand.id)).where(
                ShipmentAgentCommand.run_id == run_id
            )
        )
    return int(value or 0)


async def test_graph_replay_reuses_one_rpa_job_and_one_binding(
    client: AsyncClient,
) -> None:
    """Job 已提交但绑定事务丢失时，Graph 重放只补绑定而不重复建 Job。"""

    stack, waiting = await _start_v3_until_rpa_wait(
        client,
        case_number="SHP-DEMO-RPA-V3-REPLAY-001",
        application_number="DEMO-LINK-RPA-V3-REPLAY-001",
    )

    # 人为构造最危险的事务缝隙：统一 RPA Job 已经提交，但进程在 Shipment 绑定提交
    # 前退出。恢复时数据库仍停在 DISPATCHING_RPA_JOB，Graph 必须按稳定幂等键取回
    # 原 Job、只补一条绑定。第二次重放也不得增加 Job 或绑定。
    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session, session.begin():
        binding = await session.scalar(
            select(ShipmentAgentRpaJobBinding).where(
                ShipmentAgentRpaJobBinding.run_id == waiting.run_id
            )
        )
        assert binding is not None
        await session.delete(binding)

        stored = await session.get(ShipmentAgentRun, waiting.run_id)
        assert stored is not None
        stored.status = ShipmentAgentRunStatus.RUNNING
        stored.current_stage = ShipmentAgentStage.DISPATCHING_RPA_JOB
        stored.next_wake_at = None
        stored.wake_reason = None

    assert await _count_rows(RpaJobRecord) == 1
    assert await _count_rows(ShipmentAgentRpaJobBinding) == 0

    await stack.workflow.synchronize(waiting.run_id)
    await stack.workflow.synchronize(waiting.run_id)

    assert await _count_rows(RpaJobRecord) == 1
    assert await _count_rows(ShipmentAgentRpaJobBinding) == 1
    replayed = await stack.service.get(waiting.run_id)
    assert len(replayed.rpa_jobs) == 1
    assert replayed.rpa_jobs[0].job_id == waiting.rpa_jobs[0].job_id


async def test_success_callback_wakes_without_human_command_and_returns_to_submit_approval(
    client: AsyncClient,
) -> None:
    """RPA 只保存 LINK 草稿；成功后仍必须经过原有人工提交审批。"""

    stack, waiting = await _start_v3_until_rpa_wait(
        client,
        case_number="SHP-DEMO-RPA-V3-SUCCESS-001",
        application_number="DEMO-LINK-RPA-V3-SUCCESS-001",
    )
    command_count_before = await _shipment_command_count(waiting.run_id)
    claimed = await _claim_link_job(client)

    await _report_result(client, claimed, status="SUCCEEDED")

    # RPA callback 只能登记 Job 事实并安排自动唤醒，不能伪造一条 RESUME/RECONCILE
    # 人工命令，也不能在回调请求内直接恢复 LangGraph。
    callback_projection = await stack.service.get(waiting.run_id)
    assert callback_projection.status is ShipmentAgentRunStatus.WAITING_EXTERNAL
    assert callback_projection.current_stage is ShipmentAgentStage.AWAITING_RPA_RESULT
    assert callback_projection.next_wake_at is not None
    assert callback_projection.wake_reason == RPA_WAKE_REASON
    assert await _shipment_command_count(waiting.run_id) == command_count_before

    assert await _run_due_wakes(stack) == 1
    approval = await stack.service.get(waiting.run_id)
    assert approval.status is ShipmentAgentRunStatus.WAITING_HUMAN
    assert approval.current_stage is ShipmentAgentStage.AWAITING_BOOKING_SUBMIT_APPROVAL
    assert approval.pending_interrupt is not None
    assert approval.pending_interrupt.type.value == "BOOKING_SUBMIT_APPROVAL"
    assert approval.case_status == "ACTIVE"
    assert approval.completed_at is None
    assert approval.rpa_jobs[-1].business_outcome.value == "LINK_DRAFT_SAVED"
    assert stack.gateway.submit_calls == 0
    assert await _shipment_command_count(waiting.run_id) == command_count_before


@pytest.mark.parametrize(
    ("rpa_status", "case_suffix"),
    [
        ("RESULT_UNKNOWN", "UNKNOWN"),
        ("SITE_VERSION_UNKNOWN", "SITE"),
        ("WAITING_HUMAN", "HUMAN"),
    ],
)
async def test_non_final_rpa_states_enter_attention_instead_of_blind_retry(
    client: AsyncClient,
    rpa_status: str,
    case_suffix: str,
) -> None:
    """未知结果、未知页面和 Worker 人工等待都必须停在显式处理节点。"""

    stack, waiting = await _start_v3_until_rpa_wait(
        client,
        case_number=f"SHP-DEMO-RPA-V3-{case_suffix}-001",
        application_number=f"DEMO-LINK-RPA-V3-{case_suffix}-001",
    )
    claimed = await _claim_link_job(client)
    await _report_result(client, claimed, status=rpa_status)

    assert await _run_due_wakes(stack) == 1
    attention = await stack.service.get(waiting.run_id)
    assert attention.status is ShipmentAgentRunStatus.WAITING_RESOURCE
    assert attention.current_stage is ShipmentAgentStage.AWAITING_RPA_ATTENTION
    assert attention.pending_interrupt is None
    assert attention.rpa_jobs[-1].status.value == rpa_status
    assert attention.current_stage is not ShipmentAgentStage.DISPATCHING_RPA_JOB


async def test_result_unknown_reconciliation_success_automatically_resumes_v3(
    client: AsyncClient,
) -> None:
    """对账确认草稿已保存后，自动唤醒回到人工提交链，无需人工恢复 Graph。"""

    stack, waiting = await _start_v3_until_rpa_wait(
        client,
        case_number="SHP-DEMO-RPA-V3-RECONCILE-001",
        application_number="DEMO-LINK-RPA-V3-RECONCILE-001",
    )
    claimed = await _claim_link_job(client)
    unknown = await _report_result(client, claimed, status="RESULT_UNKNOWN")
    assert await _run_due_wakes(stack) == 1

    attention = await stack.service.get(waiting.run_id)
    assert attention.current_stage is ShipmentAgentStage.AWAITING_RPA_ATTENTION
    command_count_before = await _shipment_command_count(waiting.run_id)

    job = claimed["job"]
    assert isinstance(job, dict)
    reconciled = await client.post(
        f"/api/v1/rpa/jobs/{job['job_id']}/reconcile",
        json={
            "expected_revision": unknown["revision"],
            "decision": "CONFIRMED_SUCCEEDED",
            "message": "已在 LINK Sandbox 查询到唯一草稿，确认没有重复创建。",
            "external_reference": "DEMO-LINK-DRAFT-RECONCILED-001",
            "evidence": {"artifact_ref": "sandbox://link/reconcile/001"},
        },
    )
    assert reconciled.status_code == 200, reconciled.text

    scheduled = await stack.service.get(waiting.run_id)
    assert scheduled.status is ShipmentAgentRunStatus.WAITING_RESOURCE
    assert scheduled.current_stage is ShipmentAgentStage.AWAITING_RPA_ATTENTION
    assert scheduled.next_wake_at is not None
    assert scheduled.wake_reason == RPA_WAKE_REASON
    assert await _shipment_command_count(waiting.run_id) == command_count_before

    assert await _run_due_wakes(stack) == 1
    resumed = await stack.service.get(waiting.run_id)
    assert resumed.status is ShipmentAgentRunStatus.WAITING_HUMAN
    assert resumed.current_stage is ShipmentAgentStage.AWAITING_BOOKING_SUBMIT_APPROVAL
    assert resumed.rpa_jobs[-1].status.value == "SUCCEEDED"
    assert resumed.rpa_jobs[-1].business_outcome.value == "LINK_DRAFT_SAVED"
    assert await _shipment_command_count(waiting.run_id) == command_count_before


async def test_same_idempotent_job_can_bind_and_wake_multiple_runs(
    client: AsyncClient,
) -> None:
    """Job 首次创建来源不等于唯一消费者；所有显式绑定的运行都应收到状态变化。"""

    case_number = "SHP-DEMO-RPA-V3-FANOUT-001"
    application_number = "DEMO-LINK-RPA-V3-FANOUT-001"
    stack, first = await _start_v3_until_rpa_wait(
        client,
        case_number=case_number,
        application_number=application_number,
    )
    session_factory = app.dependency_overrides[get_session_factory]()

    # 正常产品规则同一案件只保留一个未完成 Run。这里先把旧 Run 暂时标为终态以创建
    # 第二条恢复运行，随后再还原为等待态，专门验证“同一个幂等 Job 的多绑定广播”。
    async with session_factory() as session, session.begin():
        stored_first = await session.get(ShipmentAgentRun, first.run_id)
        assert stored_first is not None
        stored_first.status = ShipmentAgentRunStatus.CANCELLED
        stored_first.current_stage = ShipmentAgentStage.CANCELLED
        stored_first.completed_at = datetime.now(UTC).replace(tzinfo=None)
        stored_first.next_wake_at = None
        stored_first.wake_reason = None

    second = await stack.workflow.start(
        case_number,
        ShipmentAgentStart(expected_case_revision=first.last_observed_case_revision),
        f"restart-{case_number}",
    )
    assert second.status is ShipmentAgentRunStatus.WAITING_EXTERNAL
    assert second.current_stage is ShipmentAgentStage.AWAITING_RPA_RESULT
    assert first.rpa_jobs[0].job_id == second.rpa_jobs[0].job_id
    assert await _count_rows(RpaJobRecord) == 1
    assert await _count_rows(ShipmentAgentRpaJobBinding) == 2

    # 清空两个控制面计划，确保下面观察到的时间与 reason 确实来自 RPA callback 广播。
    async with session_factory() as session, session.begin():
        for run_id in (first.run_id, second.run_id):
            stored = await session.get(ShipmentAgentRun, run_id)
            assert stored is not None
            stored.status = ShipmentAgentRunStatus.WAITING_EXTERNAL
            stored.current_stage = ShipmentAgentStage.AWAITING_RPA_RESULT
            stored.completed_at = None
            stored.next_wake_at = None
            stored.wake_reason = None

    claimed = await _claim_link_job(client)
    await _report_result(client, claimed, status="SUCCEEDED")

    first_scheduled = await stack.service.get(first.run_id)
    second_scheduled = await stack.service.get(second.run_id)
    for scheduled in (first_scheduled, second_scheduled):
        assert scheduled.next_wake_at is not None
        assert scheduled.wake_reason == RPA_WAKE_REASON


async def test_shipment_dto_exposes_rpa_summary_but_redacts_worker_secrets(
    client: AsyncClient,
) -> None:
    """Shipment 页面只能看到安全摘要，不能得到 payload、幂等键或 attempt token。"""

    stack, waiting = await _start_v3_until_rpa_wait(
        client,
        case_number="SHP-DEMO-RPA-V3-REDACTION-001",
        application_number="DEMO-LINK-RPA-V3-REDACTION-001",
    )
    claimed = await _claim_link_job(client)
    run = await stack.service.get(waiting.run_id)
    document = run.model_dump(mode="json")
    summaries = document["rpa_jobs"]
    assert isinstance(summaries, list)
    assert len(summaries) == 1
    summary = summaries[0]
    assert set(summary) == {
        "job_id",
        "sequence",
        "domain",
        "operation",
        "environment",
        "status",
        "current_step",
        "side_effect_phase",
        "business_outcome",
        "external_reference",
        "error_code",
        "updated_at",
    }

    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True)
    assert str(claimed["attempt_token"]) not in serialized
    assert str(claimed["idempotency_key"]) not in serialized
    assert SECRET_PAYLOAD_MARKS not in serialized

    # 数据库中的敏感字段确实有值，避免测试因“根本没生成秘密”而出现假阳性。
    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session:
        stored_job = await session.scalar(select(RpaJobRecord))
        assert stored_job is not None
        assert stored_job.attempt_token == str(claimed["attempt_token"])
        assert stored_job.idempotency_key == claimed["idempotency_key"]
        assert (
            stored_job.business_payload["booking_details"]["marks"]
            == SECRET_PAYLOAD_MARKS
        )
