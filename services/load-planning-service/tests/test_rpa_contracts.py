"""统一 RPA Job v1 纯领域契约测试。

这些测试不启动数据库和浏览器，专门保护最容易造成重复填报的底层规则：稳定哈希、
幂等键、合法状态迁移、结果关联，以及 ``RESULT_UNKNOWN`` 禁止自动重试。
"""

from datetime import UTC

import pytest
from pydantic import ValidationError

from app.rpa.contracts import (
    RpaBusinessOutcome,
    RpaContractMismatchError,
    RpaDomain,
    RpaEnvironment,
    RpaJob,
    RpaJobStatus,
    RpaOperation,
    RpaResult,
    RpaSideEffectBoundary,
    RpaSideEffectPhase,
    RpaSideEffectPhaseError,
    RpaStateTransitionError,
    can_automatically_retry,
    hash_business_payload,
)


def _link_job(**updates: object) -> RpaJob:
    values: dict[str, object] = {
        "domain": RpaDomain.LINK,
        "operation": RpaOperation.LINK_BOOKING_STAGE,
        "environment": RpaEnvironment.SANDBOX,
        "business_reference": "SHP-RPA-001",
        "business_payload": {
            "booking": {
                "port_of_loading": "CNSHA",
                "port_of_discharge": "FRLEH",
            },
            "cargo_lines": [
                {"sku": "SKU-001", "quantity": 10},
                {"sku": "SKU-002", "quantity": 20},
            ],
        },
    }
    values.update(updates)
    return RpaJob.model_validate(values)


def _result(
    job: RpaJob,
    *,
    status: RpaJobStatus,
    business_outcome: RpaBusinessOutcome | None = None,
    error_code: str | None = None,
    retry_after_seconds: int | None = None,
) -> RpaResult:
    return RpaResult(
        job_id=job.job_id,
        operation=job.operation,
        idempotency_key=job.idempotency_key,
        business_payload_hash=job.business_payload_hash,
        status=status,
        business_outcome=business_outcome,
        message=f"构造的 {status.value} 测试结果",
        error_code=error_code,
        retry_after_seconds=retry_after_seconds,
    )


def test_business_payload_hash_is_independent_of_dictionary_key_order() -> None:
    first = {
        "shipment": {"number": "SHP-001", "revision": 3},
        "lines": [{"sku": "A", "quantity": 2}],
    }
    second = {
        "lines": [{"quantity": 2, "sku": "A"}],
        "shipment": {"revision": 3, "number": "SHP-001"},
    }

    assert hash_business_payload(first) == hash_business_payload(second)


def test_same_business_command_has_same_idempotency_key_but_different_job_id() -> None:
    first = _link_job()
    replay = _link_job()

    assert first.job_id != replay.job_id
    assert first.business_payload_hash == replay.business_payload_hash
    assert first.idempotency_key == replay.idempotency_key
    assert len(first.idempotency_key) <= 128


def test_business_change_or_environment_change_changes_idempotency_key() -> None:
    original = _link_job()
    changed_payload = _link_job(
        business_payload={
            "booking": {
                "port_of_loading": "CNSHA",
                "port_of_discharge": "USLAX",
            }
        }
    )
    production = _link_job(environment=RpaEnvironment.PRODUCTION)

    assert changed_payload.idempotency_key != original.idempotency_key
    assert production.idempotency_key != original.idempotency_key


@pytest.mark.parametrize(
    ("operation", "domain", "expected_outcome"),
    [
        (
            RpaOperation.LINK_BOOKING_STAGE,
            RpaDomain.LINK,
            RpaBusinessOutcome.LINK_DRAFT_SAVED,
        ),
        (
            RpaOperation.COO_STAGE,
            RpaDomain.SINGLE_WINDOW,
            RpaBusinessOutcome.COO_STAGED,
        ),
        (
            RpaOperation.CIQ_STAGE,
            RpaDomain.SINGLE_WINDOW,
            RpaBusinessOutcome.CIQ_STAGED,
        ),
    ],
)
def test_each_operation_has_one_domain_and_one_business_outcome(
    operation: RpaOperation,
    domain: RpaDomain,
    expected_outcome: RpaBusinessOutcome,
) -> None:
    job = RpaJob(
        domain=domain,
        operation=operation,
        business_reference=f"REF-{operation.value}",
        business_payload={"synthetic": True},
    )

    assert job.expected_business_outcome is expected_outcome


def test_job_rejects_wrong_domain_and_final_submission_boundary() -> None:
    with pytest.raises(ValidationError, match="必须由 SINGLE_WINDOW 域执行"):
        RpaJob(
            domain=RpaDomain.LINK,
            operation=RpaOperation.COO_STAGE,
            business_reference="COO-001",
            business_payload={"synthetic": True},
        )

    with pytest.raises(ValidationError, match="只允许 DRAFT_ONLY"):
        _link_job(side_effect_boundary=RpaSideEffectBoundary.FINAL_SUBMISSION)


def test_job_rejects_tampered_payload_hash_or_idempotency_key() -> None:
    with pytest.raises(ValidationError, match="business_payload_hash"):
        _link_job(business_payload_hash="0" * 64)

    with pytest.raises(ValidationError, match="idempotency_key"):
        _link_job(idempotency_key="RPA-V1-TAMPERED")


def test_happy_path_uses_explicit_legal_transitions_and_keeps_old_snapshots() -> None:
    created = _link_job()
    queued = created.transition_to(RpaJobStatus.QUEUED)
    running = queued.transition_to(RpaJobStatus.RUNNING)
    succeeded = running.transition_to(RpaJobStatus.SUCCEEDED)

    assert created.status is RpaJobStatus.CREATED
    assert queued.status is RpaJobStatus.QUEUED
    assert running.status is RpaJobStatus.RUNNING
    assert succeeded.status is RpaJobStatus.SUCCEEDED
    # 重放同一个状态是幂等 no-op，不创建一条伪造的新进度。
    assert succeeded.transition_to(RpaJobStatus.SUCCEEDED) is succeeded


def test_illegal_transition_cannot_skip_queue_or_reopen_terminal_job() -> None:
    with pytest.raises(RpaStateTransitionError, match="CREATED.*SUCCEEDED"):
        _link_job().transition_to(RpaJobStatus.SUCCEEDED)

    succeeded = (
        _link_job()
        .transition_to(RpaJobStatus.QUEUED)
        .transition_to(RpaJobStatus.RUNNING)
        .transition_to(RpaJobStatus.SUCCEEDED)
    )
    with pytest.raises(RpaStateTransitionError, match="SUCCEEDED.*RUNNING"):
        succeeded.transition_to(RpaJobStatus.RUNNING)


def test_only_explicit_retryable_failure_can_be_automatically_retried() -> None:
    assert can_automatically_retry(RpaJobStatus.RETRYABLE_FAILED) is True
    assert can_automatically_retry(RpaJobStatus.RESULT_UNKNOWN) is False
    assert can_automatically_retry(RpaJobStatus.SITE_VERSION_UNKNOWN) is False
    assert can_automatically_retry(RpaJobStatus.FAILED) is False


def test_result_unknown_cannot_return_directly_to_queue() -> None:
    unknown = (
        _link_job()
        .transition_to(RpaJobStatus.QUEUED)
        .transition_to(RpaJobStatus.RUNNING)
        .transition_to(RpaJobStatus.RESULT_UNKNOWN)
    )

    assert unknown.can_auto_retry is False
    with pytest.raises(RpaStateTransitionError, match="RESULT_UNKNOWN.*QUEUED"):
        unknown.transition_to(RpaJobStatus.QUEUED)

    # 对账明确“外部没有保存成功且现在可重试”后，先把未知解析成 RETRYABLE_FAILED。
    retryable = unknown.transition_to(RpaJobStatus.RETRYABLE_FAILED)
    assert retryable.can_auto_retry is True
    assert retryable.transition_to(RpaJobStatus.QUEUED).status is RpaJobStatus.QUEUED


def test_side_effect_phase_is_monotonic_and_separate_from_authorization() -> None:
    created = _link_job()
    prepared = created.advance_side_effect_phase(RpaSideEffectPhase.LOCAL_PREPARED)
    started = prepared.advance_side_effect_phase(RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED)
    confirmed = started.advance_side_effect_phase(RpaSideEffectPhase.EXTERNAL_EFFECT_CONFIRMED)

    assert created.side_effect_boundary is RpaSideEffectBoundary.DRAFT_ONLY
    assert created.side_effect_phase is RpaSideEffectPhase.READ_ONLY
    assert confirmed.side_effect_phase is RpaSideEffectPhase.EXTERNAL_EFFECT_CONFIRMED
    with pytest.raises(RpaSideEffectPhaseError, match="不允许.*倒退"):
        confirmed.advance_side_effect_phase(RpaSideEffectPhase.LOCAL_PREPARED)


def test_success_result_requires_operation_specific_business_outcome() -> None:
    job = _link_job()

    with pytest.raises(ValidationError, match="必须返回 LINK_DRAFT_SAVED"):
        _result(job, status=RpaJobStatus.SUCCEEDED)

    with pytest.raises(ValidationError, match="必须返回 LINK_DRAFT_SAVED"):
        _result(
            job,
            status=RpaJobStatus.SUCCEEDED,
            business_outcome=RpaBusinessOutcome.COO_STAGED,
        )

    succeeded = _result(
        job,
        status=RpaJobStatus.SUCCEEDED,
        business_outcome=RpaBusinessOutcome.LINK_DRAFT_SAVED,
    )
    assert succeeded.business_outcome is RpaBusinessOutcome.LINK_DRAFT_SAVED


def test_unknown_result_cannot_claim_business_success_or_retry_delay() -> None:
    job = _link_job()

    with pytest.raises(ValidationError, match="只有 SUCCEEDED"):
        _result(
            job,
            status=RpaJobStatus.RESULT_UNKNOWN,
            business_outcome=RpaBusinessOutcome.LINK_DRAFT_SAVED,
            error_code="SAVE_RESPONSE_LOST",
        )

    with pytest.raises(ValidationError, match="只有 RETRYABLE_FAILED"):
        _result(
            job,
            status=RpaJobStatus.RESULT_UNKNOWN,
            error_code="SAVE_RESPONSE_LOST",
            retry_after_seconds=30,
        )

    unknown = _result(
        job,
        status=RpaJobStatus.RESULT_UNKNOWN,
        error_code="SAVE_RESPONSE_LOST",
    )
    assert unknown.business_outcome is None
    assert unknown.can_auto_retry is False


def test_failure_result_requires_machine_readable_error_code() -> None:
    job = _link_job()

    with pytest.raises(ValidationError, match="必须提供 error_code"):
        _result(job, status=RpaJobStatus.RETRYABLE_FAILED)

    retryable = _result(
        job,
        status=RpaJobStatus.RETRYABLE_FAILED,
        error_code="TEMPORARY_NETWORK_ERROR",
        retry_after_seconds=30,
    )
    assert retryable.can_auto_retry is True


def test_job_applies_only_a_correlated_worker_result() -> None:
    running = _link_job().transition_to(RpaJobStatus.QUEUED).transition_to(RpaJobStatus.RUNNING)
    result = _result(
        running,
        status=RpaJobStatus.SUCCEEDED,
        business_outcome=RpaBusinessOutcome.LINK_DRAFT_SAVED,
    )

    completed = running.apply_result(result)
    assert completed.status is RpaJobStatus.SUCCEEDED

    other_job = _link_job(business_reference="SHP-RPA-OTHER")
    with pytest.raises(RpaContractMismatchError, match="job_id.*idempotency_key"):
        other_job.apply_result(result)


def test_contract_rejects_unknown_fields_and_normalizes_timestamp_to_utc() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _link_job(actor_subject="browser-supplied-user")

    job = _link_job(created_at="2026-10-22T08:30:00")
    assert job.created_at.tzinfo is UTC
