"""统一 RPA Job 持久化控制面的行为测试。

这些测试故意经过真实 FastAPI、SQLAlchemy 和内存 SQLite，而不是只测纯函数。这样可以
尽早发现异步 ORM 刷新、attempt token、回调幂等和租约恢复这类“代码能读懂但运行会坏”
的问题。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import get_session_factory
from app.main import app
from app.models.rpa_job import RpaJobCallbackReceipt, RpaJobRecord
from app.rpa.contracts import (
    RpaEnvironment,
    RpaJobStatus,
    RpaSideEffectPhase,
)
from app.schemas.rpa_job import RpaJobDispatch
from app.services.rpa_jobs import RpaJobService, RpaJobValidationError
from app.workers.rpa_lease_recovery_worker import RpaLeaseRecoveryWorker


def _link_dispatch_payload(
    *,
    business_reference: str = "ORDER-RPA-001",
    environment: str = "SANDBOX",
    max_attempts: int = 3,
) -> dict[str, object]:
    """返回一份最小 LINK 草稿 RPA 任务。

    ``business_payload`` 当前仍是版本化 JSON 信封；后续 Shipment V3 会放入经过校验的
    CanonicalTradeOrder 快照，而不是让页面随意拼字段。
    """

    return {
        "case_number": "SHP-RPA-001",
        "domain": "LINK",
        "operation": "LINK_BOOKING_STAGE",
        "environment": environment,
        "business_reference": business_reference,
        "side_effect_boundary": "DRAFT_ONLY",
        "adapter_key": "link-playwright",
        "selector_pack_version": "link-sandbox-v1",
        "payload_schema_version": "1",
        "max_attempts": max_attempts,
        "business_payload": {
            "data_source_label": "SYNTHETIC_DEMO",
            "order_number": business_reference,
        },
    }


def _link_claim_payload(
    *,
    worker_id: str = "link-worker-01",
    environment: str = "SANDBOX",
) -> dict[str, object]:
    return {
        "domain": "LINK",
        "environment": environment,
        "worker_id": worker_id,
        "supported_operations": ["LINK_BOOKING_STAGE"],
    }


async def _dispatch(
    client: AsyncClient,
    *,
    business_reference: str = "ORDER-RPA-001",
    max_attempts: int = 3,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/rpa/jobs",
        json=_link_dispatch_payload(
            business_reference=business_reference,
            max_attempts=max_attempts,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _claim(
    client: AsyncClient,
    *,
    worker_id: str = "link-worker-01",
    environment: str = "SANDBOX",
) -> dict[str, object] | None:
    response = await client.post(
        "/api/v1/rpa/worker/jobs/claim",
        json=_link_claim_payload(
            worker_id=worker_id,
            environment=environment,
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _service_from_test_app(
    *,
    worker_lease_seconds: int = 120,
    production_dispatch_enabled: bool = False,
) -> RpaJobService:
    session_factory = app.dependency_overrides[get_session_factory]()
    return RpaJobService(
        session_factory,
        worker_lease_seconds=worker_lease_seconds,
        production_dispatch_enabled=production_dispatch_enabled,
    )


@pytest.mark.asyncio
async def test_same_dispatch_reuses_one_job_and_public_view_redacts_worker_secrets(
    client: AsyncClient,
) -> None:
    """同一业务命令重放不会生成第二票任务，普通查询也看不到执行凭据。"""

    first = await _dispatch(client)
    replayed = await _dispatch(client)

    assert replayed["job_id"] == first["job_id"]
    listed = await client.get("/api/v1/rpa/jobs")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    public_job = listed.json()[0]
    assert "business_payload" not in public_job
    assert "idempotency_key" not in public_job
    assert "attempt_token" not in public_job

    claimed = await _claim(client)
    assert claimed is not None
    assert claimed["business_payload"]["order_number"] == "ORDER-RPA-001"
    assert claimed["idempotency_key"].startswith("RPA-V1-LINK-")
    assert claimed["attempt_token"]
    assert "business_payload" not in claimed["job"]
    assert "attempt_token" not in claimed["job"]


@pytest.mark.asyncio
async def test_worker_claim_isolated_by_environment_and_only_one_claims_job(
    client: AsyncClient,
) -> None:
    """Production Worker 不能串领 Sandbox，任务被领取后第二个 Worker 得到 null。"""

    await _dispatch(client)

    wrong_environment = await _claim(
        client,
        worker_id="link-production-worker",
        environment="PRODUCTION",
    )
    assert wrong_environment is None

    first = await _claim(client, worker_id="link-sandbox-worker-a")
    second = await _claim(client, worker_id="link-sandbox-worker-b")
    assert first is not None
    assert second is None
    assert first["job"]["status"] == "RUNNING"
    assert first["job"]["attempt_count"] == 1


@pytest.mark.asyncio
async def test_progress_renews_lease_and_success_callback_is_idempotent(
    client: AsyncClient,
) -> None:
    """长流程靠进度心跳续租；相同 callback 重放不重复推进 revision。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    token = claimed["attempt_token"]
    old_lease = claimed["job"]["lease_expires_at"]

    progress = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/progress",
        json={
            "attempt_token": token,
            "current_step": "FORM_FILLED",
            "side_effect_phase": "LOCAL_PREPARED",
            "message": "已在本地准备好七步填单数据。",
            "page_fingerprint": "link-page-v1",
            "evidence": {"artifact_ref": "sandbox://link/form-preview/001"},
        },
    )
    assert progress.status_code == 200, progress.text
    assert progress.json()["lease_expires_at"] >= old_lease

    callback_id = str(uuid4())
    result_payload = {
        "callback_id": callback_id,
        "attempt_token": token,
        "status": "SUCCEEDED",
        "side_effect_phase": "EXTERNAL_EFFECT_CONFIRMED",
        "business_outcome": "LINK_DRAFT_SAVED",
        "message": "LINK Sandbox 草稿已保存。",
        "external_reference": "LINK-DRAFT-001",
        "evidence": {"artifact_ref": "sandbox://link/result/001"},
    }
    first_result = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json=result_payload,
    )
    replayed = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json=result_payload,
    )
    assert first_result.status_code == 200, first_result.text
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["revision"] == first_result.json()["revision"]
    assert replayed.json()["business_outcome"] == "LINK_DRAFT_SAVED"

    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session:
        receipt = await session.get(RpaJobCallbackReceipt, callback_id)
        assert receipt is not None
        assert receipt.occurrence_count == 2


@pytest.mark.asyncio
async def test_callback_id_cannot_be_reused_with_different_content(
    client: AsyncClient,
) -> None:
    """callback ID 是协议身份，不允许同一个 ID 在重试时偷偷换结果。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    callback_id = str(uuid4())
    base = {
        "callback_id": callback_id,
        "attempt_token": claimed["attempt_token"],
        "status": "RESULT_UNKNOWN",
        "side_effect_phase": "EXTERNAL_MUTATION_STARTED",
        "message": "点击保存后连接中断，结果未知。",
        "error_code": "SAVE_RESPONSE_LOST",
    }
    first = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json=base,
    )
    changed = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={**base, "message": "换成了另一份结果内容。"},
    )
    assert first.status_code == 200, first.text
    assert changed.status_code == 409
    assert "callback_id" in changed.json()["detail"]


@pytest.mark.asyncio
async def test_same_result_with_new_callback_id_does_not_duplicate_trace(
    client: AsyncClient,
) -> None:
    """Worker 即使误换 callback ID，完全相同的结果也只补收据、不重复写事件。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    body = {
        "attempt_token": claimed["attempt_token"],
        "status": "RESULT_UNKNOWN",
        "side_effect_phase": "EXTERNAL_MUTATION_STARTED",
        "message": "点击保存后连接中断，结果未知。",
        "error_code": "SAVE_RESPONSE_LOST",
    }
    first = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={"callback_id": str(uuid4()), **body},
    )
    second = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={"callback_id": str(uuid4()), **body},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == first.json()["revision"]
    assert len(second.json()["events"]) == len(first.json()["events"])


@pytest.mark.asyncio
async def test_old_attempt_token_cannot_overwrite_new_attempt(
    client: AsyncClient,
) -> None:
    """第一次 Worker 的迟到消息不能覆盖已经开始的第二次尝试。"""

    dispatched = await _dispatch(client)
    first_claim = await _claim(client, worker_id="link-worker-old")
    assert first_claim is not None
    retryable = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={
            "callback_id": str(uuid4()),
            "attempt_token": first_claim["attempt_token"],
            "status": "RETRYABLE_FAILED",
            "side_effect_phase": "READ_ONLY",
            "message": "打开 Sandbox 页面前网络不可用。",
            "error_code": "TRANSIENT_NETWORK",
            "retry_after_seconds": 1,
        },
    )
    assert retryable.status_code == 200, retryable.text

    requeued = await client.post(
        f"/api/v1/rpa/jobs/{dispatched['job_id']}/requeue",
        json={
            "expected_revision": retryable.json()["revision"],
            "reason": "确认尚未访问外部写页面，立即重试。",
        },
    )
    assert requeued.status_code == 200, requeued.text
    second_claim = await _claim(client, worker_id="link-worker-new")
    assert second_claim is not None

    stale = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/progress",
        json={
            "attempt_token": first_claim["attempt_token"],
            "current_step": "LATE_OLD_WORKER",
            "side_effect_phase": "READ_ONLY",
            "message": "旧 Worker 迟到。",
        },
    )
    assert stale.status_code == 409
    assert "attempt_token 已失效" in stale.json()["detail"]


@pytest.mark.asyncio
async def test_result_unknown_cannot_be_directly_requeued(
    client: AsyncClient,
) -> None:
    """保存响应丢失时必须先查外部事实，不能直接再点一次保存。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    unknown = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={
            "callback_id": str(uuid4()),
            "attempt_token": claimed["attempt_token"],
            "status": "RESULT_UNKNOWN",
            "side_effect_phase": "EXTERNAL_MUTATION_STARTED",
            "message": "保存响应丢失。",
            "error_code": "SAVE_RESPONSE_LOST",
        },
    )
    assert unknown.status_code == 200, unknown.text

    requeue = await client.post(
        f"/api/v1/rpa/jobs/{dispatched['job_id']}/requeue",
        json={
            "expected_revision": unknown.json()["revision"],
            "reason": "错误地尝试直接重跑。",
            "confirmed_safe_to_retry": True,
        },
    )
    assert requeue.status_code == 409
    assert "RESULT_UNKNOWN" in requeue.json()["detail"]


@pytest.mark.asyncio
async def test_retryable_failed_after_external_write_is_rejected(
    client: AsyncClient,
) -> None:
    """外部写入后的网络异常只能标记 RESULT_UNKNOWN，不能伪装成普通可重试。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    response = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={
            "callback_id": str(uuid4()),
            "attempt_token": claimed["attempt_token"],
            "status": "RETRYABLE_FAILED",
            "side_effect_phase": "EXTERNAL_MUTATION_STARTED",
            "message": "保存后网络断开。",
            "error_code": "TRANSIENT_NETWORK",
            "retry_after_seconds": 1,
        },
    )
    assert response.status_code == 422
    assert "必须报告 RESULT_UNKNOWN" in response.json()["detail"]


@pytest.mark.asyncio
async def test_expired_lease_after_external_write_becomes_result_unknown(
    client: AsyncClient,
) -> None:
    """浏览器开始写外部网站后崩溃，恢复器只转未知结果，绝不自动重跑。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    progressed = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/progress",
        json={
            "attempt_token": claimed["attempt_token"],
            "current_step": "SAVE_CLICKED",
            "side_effect_phase": "EXTERNAL_MUTATION_STARTED",
            "message": "已经点击保存，等待网站响应。",
        },
    )
    assert progressed.status_code == 200, progressed.text

    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session, session.begin():
        job = await session.scalar(
            select(RpaJobRecord).where(RpaJobRecord.id == dispatched["job_id"])
        )
        assert job is not None
        job.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

    recovered = await _service_from_test_app().recover_expired_leases()
    assert len(recovered) == 1
    assert recovered[0].status is RpaJobStatus.RESULT_UNKNOWN
    assert recovered[0].can_auto_retry is False
    assert recovered[0].error_code == "WORKER_LEASE_EXPIRED_AFTER_MUTATION"


@pytest.mark.asyncio
async def test_recovery_worker_requeues_only_safe_retryable_jobs(
    client: AsyncClient,
) -> None:
    """进程内 Worker 构成受控 Agent Loop，但只循环外部写入前的明确失败。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    failed = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={
            "callback_id": str(uuid4()),
            "attempt_token": claimed["attempt_token"],
            "status": "RETRYABLE_FAILED",
            "side_effect_phase": "LOCAL_PREPARED",
            "message": "Sandbox 登录页临时不可用。",
            "error_code": "LOGIN_PAGE_UNAVAILABLE",
            "retry_after_seconds": 1,
        },
    )
    assert failed.status_code == 200, failed.text

    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session, session.begin():
        job = await session.scalar(
            select(RpaJobRecord).where(RpaJobRecord.id == dispatched["job_id"])
        )
        assert job is not None
        job.next_attempt_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

    worker = RpaLeaseRecoveryWorker(
        service=_service_from_test_app(),
        poll_seconds=1,
        batch_size=10,
    )
    assert await worker.run_once() == 1

    requeued = await client.get(f"/api/v1/rpa/jobs/{dispatched['job_id']}")
    assert requeued.status_code == 200
    assert requeued.json()["status"] == "QUEUED"
    assert requeued.json()["current_step"] == "WAITING_WORKER"


@pytest.mark.asyncio
async def test_max_attempts_converges_retryable_result_to_failed(
    client: AsyncClient,
) -> None:
    """Worker 认为可重试也不能突破服务端最大尝试次数。"""

    dispatched = await _dispatch(client, max_attempts=1)
    claimed = await _claim(client)
    assert claimed is not None
    result = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={
            "callback_id": str(uuid4()),
            "attempt_token": claimed["attempt_token"],
            "status": "RETRYABLE_FAILED",
            "side_effect_phase": "READ_ONLY",
            "message": "网络仍不可用。",
            "error_code": "TRANSIENT_NETWORK",
            "retry_after_seconds": 1,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "FAILED"
    assert result.json()["error_code"] == "MAX_ATTEMPTS_EXHAUSTED"
    assert result.json()["can_auto_retry"] is False


@pytest.mark.asyncio
async def test_production_is_disabled_and_synthetic_data_never_enters_it(
    client: AsyncClient,
) -> None:
    """默认开关拒绝 Production；即使测试显式打开，也拒绝合成数据或缺失来源标签。"""

    disabled = await client.post(
        "/api/v1/rpa/jobs",
        json=_link_dispatch_payload(
            business_reference="ORDER-PROD-DISABLED",
            environment="PRODUCTION",
        ),
    )
    assert disabled.status_code == 422
    assert "未开启 Production" in disabled.json()["detail"]

    service = _service_from_test_app(production_dispatch_enabled=True)
    with pytest.raises(RpaJobValidationError, match="ENTERPRISE_REAL"):
        await service.dispatch(
            RpaJobDispatch.model_validate(
                _link_dispatch_payload(
                    business_reference="ORDER-PROD-SYNTHETIC",
                    environment="PRODUCTION",
                )
            )
        )

    missing_label = _link_dispatch_payload(
        business_reference="ORDER-PROD-NO-LABEL",
        environment="PRODUCTION",
    )
    missing_label["business_payload"] = {"order_number": "ORDER-PROD-NO-LABEL"}
    with pytest.raises(RpaJobValidationError, match="ENTERPRISE_REAL"):
        await service.dispatch(RpaJobDispatch.model_validate(missing_label))


@pytest.mark.asyncio
async def test_certificate_profile_generates_server_side_flow_profile(
    client: AsyncClient,
) -> None:
    """调用方只提供已知来源版式，网页执行策略由服务端目录确定。"""

    response = await client.post(
        "/api/v1/rpa/jobs",
        json={
            "case_number": "SHP-COO-001",
            "domain": "SINGLE_WINDOW",
            "operation": "COO_STAGE",
            "environment": "SANDBOX",
            "business_reference": "COO-ORDER-001",
            "side_effect_boundary": "DRAFT_ONLY",
            "adapter_key": "single-window-selenium",
            "source_profile": "heying_rcep_pti",
            "selector_pack_version": "single-window-sandbox-v1",
            "payload_schema_version": "1",
            "business_payload": {
                "data_source_label": "SYNTHETIC_DEMO",
                "order_number": "COO-ORDER-001",
            },
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["source_profile"] == "heying_rcep_pti"
    assert response.json()["flow_profile"] == "RCEP_WITH_INVOICE"


@pytest.mark.asyncio
async def test_expired_worker_cannot_renew_progress_after_lease_loss(
    client: AsyncClient,
) -> None:
    """租约过期后旧浏览器必须停手，不能靠迟到进度把自己重新变成 owner。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    session_factory = app.dependency_overrides[get_session_factory]()
    async with session_factory() as session, session.begin():
        job = await session.scalar(
            select(RpaJobRecord).where(RpaJobRecord.id == dispatched["job_id"])
        )
        assert job is not None
        job.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)

    late_progress = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/progress",
        json={
            "attempt_token": claimed["attempt_token"],
            "current_step": "TOO_LATE",
            "side_effect_phase": RpaSideEffectPhase.READ_ONLY.value,
            "message": "迟到心跳。",
        },
    )
    assert late_progress.status_code == 409
    assert "租约已经过期" in late_progress.json()["detail"]


@pytest.mark.asyncio
async def test_new_callback_id_with_changed_terminal_result_conflicts(
    client: AsyncClient,
) -> None:
    """终态事实只能幂等重放，不能由第二条回调改写。"""

    dispatched = await _dispatch(client)
    claimed = await _claim(client)
    assert claimed is not None
    succeeded = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={
            "callback_id": str(uuid4()),
            "attempt_token": claimed["attempt_token"],
            "status": "SUCCEEDED",
            "side_effect_phase": "EXTERNAL_EFFECT_CONFIRMED",
            "business_outcome": "LINK_DRAFT_SAVED",
            "message": "草稿已保存。",
            "external_reference": "LINK-DRAFT-001",
        },
    )
    assert succeeded.status_code == 200, succeeded.text

    changed = await client.post(
        f"/api/v1/rpa/worker/jobs/{dispatched['job_id']}/result",
        json={
            "callback_id": str(uuid4()),
            "attempt_token": claimed["attempt_token"],
            "status": "FAILED",
            "side_effect_phase": "EXTERNAL_EFFECT_CONFIRMED",
            "message": "试图覆盖为失败。",
            "error_code": "LATE_CONFLICT",
        },
    )
    assert changed.status_code == 409
    assert "不能接受新的 Worker 结果" in changed.json()["detail"]


@pytest.mark.asyncio
async def test_rpa_environment_enum_is_kept_in_public_snapshot(
    client: AsyncClient,
) -> None:
    """控制台必须醒目显示 Sandbox，避免演示数据看起来像真实网站结果。"""

    dispatched = await _dispatch(client)
    assert dispatched["environment"] == RpaEnvironment.SANDBOX.value
