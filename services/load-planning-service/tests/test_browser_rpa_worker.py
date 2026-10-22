"""Playwright Worker 的纯映射与安全分流测试。

浏览器本身不在这里启动；测试重点是 Job 不会被错误 Handler 接管，以及 LINK/
单一窗口字段如何从稳定业务载荷映射到语义 DOM。
"""

from pathlib import Path
from uuid import uuid4

import pytest

from app.rpa.browser_worker import (
    BusinessPayloadValidationError,
    ClaimedJob,
    RpaControlPlaneClient,
    SandboxBrowserHandler,
    SiteVersionUnknownError,
    WorkerConfig,
)
from app.rpa.contracts import (
    RpaDomain,
    RpaEnvironment,
    RpaOperation,
    RpaSideEffectPhase,
)


def _config(domain: RpaDomain) -> WorkerConfig:
    return WorkerConfig(
        domain=domain,
        control_base_url="http://127.0.0.1:8200",
        sandbox_base_url="http://127.0.0.1:8200",
        worker_id="unit-test-worker",
        headless=True,
        browser_channel=None,
        poll_seconds=1,
        request_timeout_seconds=1,
        browser_timeout_ms=1_000,
        ukey_present=False,
        evidence_directory=Path("runtime/test-rpa-evidence"),
    )


def _job(
    *,
    operation: RpaOperation,
    adapter_key: str,
    selector_pack_version: str,
    payload: dict[str, object],
) -> ClaimedJob:
    return ClaimedJob(
        job_id=str(uuid4()),
        attempt_token=uuid4(),
        idempotency_key="RPA-V1-UNIT-TEST",
        operation=operation,
        environment=RpaEnvironment.SANDBOX,
        adapter_key=adapter_key,
        selector_pack_version=selector_pack_version,
        business_reference="DEMO-UNIT-001",
        side_effect_phase=RpaSideEffectPhase.READ_ONLY,
        business_payload=payload,
    )


def test_link_worker_flattens_validated_booking_payload() -> None:
    config = _config(RpaDomain.LINK)
    handler = SandboxBrowserHandler(config, RpaControlPlaneClient(config))
    job = _job(
        operation=RpaOperation.LINK_BOOKING_STAGE,
        adapter_key="link-playwright",
        selector_pack_version="link-sandbox-v1",
        payload={
            "application_number": "DEMO-UNIT-001",
            "origin_port": "CNSHA",
            "destination_port": "FRLEH",
            "transport_mode": "SEA",
            "container_type": "40HQ",
            "requested_etd": "2026-11-15",
            "booking_details": {
                "invoice_number": "DEMO-INV-001",
                "total_cartons": 20,
                "marks": "DEMO MARKS",
            },
        },
    )

    fields = handler._business_fields(job)

    assert fields["application_number"] == "DEMO-UNIT-001"
    assert fields["destination_port"] == "FRLEH"
    assert fields["invoice_number"] == "DEMO-INV-001"
    assert fields["total_cartons"] == 20
    assert fields["marks"] == "DEMO MARKS"


def test_worker_rejects_job_for_another_adapter() -> None:
    config = _config(RpaDomain.LINK)
    handler = SandboxBrowserHandler(config, RpaControlPlaneClient(config))
    job = _job(
        operation=RpaOperation.LINK_BOOKING_STAGE,
        adapter_key="single-window-playwright",
        selector_pack_version="link-sandbox-v1",
        payload={},
    )

    with pytest.raises(SiteVersionUnknownError, match="adapter_key"):
        handler._validate_job(job)


def test_single_window_worker_uses_flat_execution_payload() -> None:
    config = _config(RpaDomain.SINGLE_WINDOW)
    handler = SandboxBrowserHandler(config, RpaControlPlaneClient(config))
    job = _job(
        operation=RpaOperation.COO_STAGE,
        adapter_key="single-window-playwright",
        selector_pack_version="single-window-sandbox-v1",
        payload={
            "order_number": "DEMO-ORDER-001",
            "destination_country_code": "FR",
            "party_name": "DEMO CONSIGNEE",
            "invoice_number": "DEMO-INV-001",
            "source_profile": "wudi_form_e",
            "flow_profile": "STANDARD",
        },
    )

    fields = handler._business_fields(job)

    assert fields["order_number"] == "DEMO-ORDER-001"
    assert fields["destination_country_code"] == "FR"
    assert fields["source_profile"] == "wudi_form_e"
    assert fields["flow_profile"] == "STANDARD"


def test_link_worker_rejects_missing_required_business_fields() -> None:
    config = _config(RpaDomain.LINK)
    handler = SandboxBrowserHandler(config, RpaControlPlaneClient(config))
    job = _job(
        operation=RpaOperation.LINK_BOOKING_STAGE,
        adapter_key="link-playwright",
        selector_pack_version="link-sandbox-v1",
        payload={
            "application_number": "DEMO-UNIT-001",
            "origin_port": "CNSHA",
            "destination_port": "",
            "transport_mode": "SEA",
            "container_type": None,
            "booking_details": {},
        },
    )

    fields = handler._business_fields(job)

    with pytest.raises(
        BusinessPayloadValidationError,
        match="destination_port, container_type",
    ):
        handler._validate_required_fields(job, fields)


def test_coo_worker_requires_versioned_source_and_flow_profiles() -> None:
    config = _config(RpaDomain.SINGLE_WINDOW)
    handler = SandboxBrowserHandler(config, RpaControlPlaneClient(config))
    job = _job(
        operation=RpaOperation.COO_STAGE,
        adapter_key="single-window-playwright",
        selector_pack_version="single-window-sandbox-v1",
        payload={
            "order_number": "DEMO-ORDER-001",
            "destination_country_code": "FR",
            "party_name": "DEMO CONSIGNEE",
        },
    )

    fields = handler._business_fields(job)

    with pytest.raises(
        BusinessPayloadValidationError,
        match="source_profile, flow_profile",
    ):
        handler._validate_required_fields(job, fields)
