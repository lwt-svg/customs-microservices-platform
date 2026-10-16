"""Shipment DATA_READINESS V2 的聚焦闭环测试。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from alembic import command
from app.db.session import get_session_factory
from app.main import app
from app.models.shipment_agent_run import (
    ShipmentAgentCommand,
    ShipmentAgentCommandResult,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.models.shipment_case import ShipmentCase
from app.models.shipment_data_readiness import ShipmentDataReadinessStatus
from app.schemas.shipment_agent_run import ShipmentAgentResume
from app.services.shipment_agent_runs import ShipmentAgentService
from app.workflows.shipment_case import ShipmentWorkflowOrchestrator


def _headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


def _task_payload(task_number: str) -> dict[str, object]:
    """两箱规、一托盘的构造任务：初次评估应产生五个问题。"""

    cargo_common = {
        "length_mm": 400,
        "width_mm": 300,
        "height_mm": 250,
        "gross_weight_kg": 10,
        "stackable": True,
        "max_stack_layers": 4,
        "keep_upright": True,
        "fragile": False,
        "source_type": "SYNTHETIC_DEMO",
        "source_reference": "DATA_READINESS_TEST",
        "verified": False,
    }
    return {
        "task_number": task_number,
        "task_name": "DATA_READINESS 两箱规测试任务",
        "cargo_items": [
            {
                **cargo_common,
                "item_code": "ITEM-A",
                "sku_code": "SKU-A",
                "cargo_name": "演示箱 A",
                "quantity": 24,
            },
            {
                **cargo_common,
                "item_code": "ITEM-B",
                "sku_code": "SKU-B",
                "cargo_name": "演示箱 B",
                "quantity": 12,
            },
        ],
        "pallet_specs": [
            {
                "pallet_code": "PALLET-A",
                "pallet_name": "演示托盘 A",
                "quantity": 3,
                "length_mm": 1200,
                "width_mm": 1000,
                "height_mm": 150,
                "tare_weight_kg": 25,
                "max_load_kg": 800,
                "max_total_height_mm": 1400,
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "DATA_READINESS_TEST",
                "verified": False,
            }
        ],
    }


async def _create_run(
    client: AsyncClient,
    suffix: str,
) -> tuple[dict[str, object], dict[str, object]]:
    task_number = f"LP-DEMO-DATA-{suffix}"
    case_number = f"SHP-DEMO-DATA-{suffix}"
    task = await client.post(
        "/api/v1/load-planning-tasks",
        json=_task_payload(task_number),
    )
    assert task.status_code == 201, task.text
    case = await client.post(
        "/api/v1/shipment-cases",
        headers=_headers(f"create-data-{suffix}"),
        json={
            "case_number": case_number,
            "case_name": f"DATA_READINESS {suffix}",
            "transport_scenario": "LOAD_PLANNING_ONLY",
        },
    )
    assert case.status_code == 201, case.text
    bound = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        headers=_headers(f"bind-data-{suffix}"),
        json={
            "resource_type": "LOAD_PLANNING_TASK",
            "resource_number": task_number,
            "expected_revision": case.json()["revision"],
        },
    )
    assert bound.status_code == 200, bound.text
    started = await client.post(
        f"/api/v1/shipment-cases/{case_number}/agent-runs",
        headers=_headers(f"start-data-{suffix}"),
        json={"expected_case_revision": bound.json()["revision"]},
    )
    assert started.status_code == 201, started.text
    return started.json(), {
        "case_number": case_number,
        "task_number": task_number,
        "start_payload": {"expected_case_revision": bound.json()["revision"]},
        "start_key": f"start-data-{suffix}",
    }


def _master_confirmation(
    subject_type: str,
    subject_code: str,
) -> dict[str, object]:
    return {
        "subject_type": subject_type,
        "subject_code": subject_code,
        "fact_type": "MASTER_DATA_CONFIRMED",
        "value": True,
        "source_type": "MANUAL_CONFIRMATION",
        "source_reference": "WH-MASTER-CHECK-20261016",
    }


def _available(
    subject_code: str,
    quantity: object,
    **extra: object,
) -> dict[str, object]:
    return {
        "subject_type": "CARGO_ITEM",
        "subject_code": subject_code,
        "fact_type": "AVAILABLE_QUANTITY",
        "value": quantity,
        "source_type": "MANUAL_CONFIRMATION",
        "source_reference": "WH-STOCK-CHECK-20261016",
        **extra,
    }


async def test_partial_supply_is_idempotent_and_automatically_reassessed(
    client: AsyncClient,
) -> None:
    run, context = await _create_run(client, "PARTIAL-001")
    assert run["workflow_version"] == "SHIPMENT_COORDINATOR_LANGGRAPH_V2"
    assert run["current_stage"] == "AWAITING_DATA_REMEDIATION"
    assert run["data_readiness"]["status"] == "ACTION_REQUIRED"
    assert run["data_readiness"]["issue_count"] == 5
    assert {issue["issue_code"] for issue in run["data_readiness"]["issues"]} == {
        "AVAILABLE_QUANTITY_MISSING",
        "MASTER_DATA_UNCONFIRMED",
    }
    assert len(run["pending_interrupt"]["details"]["requested_facts"]) == 5

    # 丢失 checkpoint 后，用相同 START 幂等命令重建图，MySQL 中的待办保持完全一致。
    old_checkpointer = app.state.load_planning_checkpointer
    app.state.load_planning_checkpointer = InMemorySaver()
    try:
        rebuilt = await client.post(
            (f"/api/v1/shipment-cases/{context['case_number']}/agent-runs"),
            headers=_headers(str(context["start_key"])),
            json=context["start_payload"],
        )
        assert rebuilt.status_code == 201, rebuilt.text
        assert rebuilt.json()["run_id"] == run["run_id"]
        assert rebuilt.json()["run_version"] == run["run_version"]
        assert rebuilt.json()["pending_interrupt"] == run["pending_interrupt"]

        first_payload = {
            "expected_run_version": run["run_version"],
            "interrupt_type": "DATA_READINESS_REMEDIATION",
            "action": "SUPPLY_DATA",
            "comment": "第一次只完成主数据确认，并发现 ITEM-A 库存不足",
            "data_facts": [
                _master_confirmation("CARGO_ITEM", "ITEM-A"),
                _master_confirmation("CARGO_ITEM", "ITEM-B"),
                _master_confirmation("PALLET_SPEC", "PALLET-A"),
                _available("ITEM-A", 8),
            ],
        }
        first = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("supply-data-partial-001"),
            json=first_payload,
        )
        replayed = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("supply-data-partial-001"),
            json=first_payload,
        )
        assert first.status_code == replayed.status_code == 200
        partial = first.json()
        assert partial["current_stage"] == "AWAITING_DATA_REMEDIATION"
        assert partial["data_readiness"]["status"] == "ACTION_REQUIRED"
        assert partial["data_readiness"]["issue_count"] == 2
        assert {issue["issue_code"] for issue in partial["data_readiness"]["issues"]} == {
            "INVENTORY_SHORTAGE",
            "AVAILABLE_QUANTITY_MISSING",
        }
        assert replayed.json()["run_version"] == partial["run_version"]
        assert (
            sum(event["event_type"] == "DATA_SUPPLEMENTED" for event in replayed.json()["events"])
            == 1
        )

        changed_same_key = {
            **first_payload,
            "data_facts": [
                *first_payload["data_facts"][:-1],
                _available("ITEM-A", 9),
            ],
        }
        conflict = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("supply-data-partial-001"),
            json=changed_same_key,
        )
        assert conflict.status_code == 409

        stale_version = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("supply-data-stale-version-001"),
            json={
                "expected_run_version": run["run_version"],
                "interrupt_type": "DATA_READINESS_REMEDIATION",
                "action": "SUPPLY_DATA",
                "data_facts": [
                    _available("ITEM-A", 24),
                    _available("ITEM-B", 12),
                ],
            },
        )
        assert stale_version.status_code == 409

        completed_supply = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers("supply-data-complete-001"),
            json={
                "expected_run_version": partial["run_version"],
                "interrupt_type": "DATA_READINESS_REMEDIATION",
                "action": "SUPPLY_DATA",
                "data_facts": [
                    _available("ITEM-A", 24),
                    _available("ITEM-B", 12),
                ],
            },
        )
        assert completed_supply.status_code == 200, completed_supply.text
        ready = completed_supply.json()
        assert ready["data_readiness"]["status"] == "DEMO_READY"
        assert ready["data_readiness"]["source_mode"] == "DEMO"
        assert ready["data_readiness"]["issue_count"] == 0
        assert ready["current_stage"] == "AWAITING_CASE_ACTIVATION"
        assert ready["case_status"] == "DRAFT"
        assert ready["last_observed_case_revision"] == 2
        event_types = [event["event_type"] for event in ready["events"]]
        assert event_types.count("DATA_REMEDIATION_REQUIRED") == 2
        assert event_types.count("DATA_SUPPLEMENTED") == 2
        assert event_types.count("DATA_READINESS_READY") == 1

        trace_keys = {
            "fact_keys",
            "issue_codes",
            "source_types",
            "issue_count",
            "blocking_issue_count",
            "snapshot_hash",
        }
        for event in ready["events"]:
            if event["event_type"] in {
                "DATA_REMEDIATION_REQUIRED",
                "DATA_SUPPLEMENTED",
                "DATA_READINESS_READY",
            }:
                assert set(event["data"]) <= trace_keys
                assert "actual_value" not in json.dumps(event["data"])
                assert "required_value" not in json.dumps(event["data"])
    finally:
        app.state.load_planning_checkpointer = old_checkpointer


async def test_supply_data_rejects_invalid_or_unrequested_facts(
    client: AsyncClient,
) -> None:
    run, _ = await _create_run(client, "VALIDATION-001")
    base = {
        "expected_run_version": run["run_version"],
        "interrupt_type": "DATA_READINESS_REMEDIATION",
        "action": "SUPPLY_DATA",
    }

    for index, fact in enumerate(
        (
            _available("ITEM-A", -1),
            _available("ITEM-A", True),
            {
                **_master_confirmation("CARGO_ITEM", "ITEM-A"),
                "value": 1,
            },
        ),
        start=1,
    ):
        response = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers(f"invalid-data-value-{index}"),
            json={**base, "data_facts": [fact]},
        )
        assert response.status_code == 422

    provenance_cases = (
        {
            **_available("ITEM-A", 24),
            "source_type": "ERP",
        },
        {
            **_available("ITEM-A", 24),
            "source_type": "WMS",
        },
        {
            **_available("ITEM-A", 24),
            "source_type": "CSV_IMPORT",
            "source_reference": "  ",
        },
        _available(
            "ITEM-A",
            24,
            observed_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        ),
        _available(
            "ITEM-A",
            24,
            observed_at="2026-10-16T08:00:00Z",
            expires_at="2026-10-16T07:59:59Z",
        ),
    )
    for index, fact in enumerate(provenance_cases, start=1):
        response = await client.post(
            f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
            headers=_headers(f"invalid-data-provenance-{index}"),
            json={**base, "data_facts": [fact]},
        )
        assert response.status_code == 422, response.text

    missing_facts = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("invalid-data-empty"),
        json=base,
    )
    assert missing_facts.status_code == 422

    unrequested = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("invalid-data-unrequested"),
        json={
            **base,
            "data_facts": [_available("ITEM-NOT-IN-TASK", 10)],
        },
    )
    assert unrequested.status_code == 422

    data_on_other_action = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("invalid-data-stop"),
        json={
            **base,
            "action": "STOP",
            "data_facts": [_available("ITEM-A", 24)],
        },
    )
    assert data_on_other_action.status_code == 422

    unchanged = await client.get(f"/api/v1/shipment-agent-runs/{run['run_id']}")
    assert unchanged.status_code == 200
    assert unchanged.json()["run_version"] == run["run_version"]
    assert unchanged.json()["data_readiness"]["issue_count"] == 5


async def test_reconcile_recovers_after_supply_commit_before_graph_continuation(
    client: AsyncClient,
) -> None:
    """补数事务已提交但 Graph 尚未续跑时，RECONCILE 可幂等恢复同一运行。"""

    run, _ = await _create_run(client, "CRASH-WINDOW-001")
    session_factory = app.dependency_overrides[get_session_factory]()
    service = ShipmentAgentService(
        session_factory,
        _UnusedLinkGateway(),
        app.state.load_planning_checkpointer,
    )
    resume_payload = ShipmentAgentResume.model_validate(
        {
            "expected_run_version": run["run_version"],
            "interrupt_type": "DATA_READINESS_REMEDIATION",
            "action": "SUPPLY_DATA",
            "data_facts": [
                _master_confirmation("CARGO_ITEM", "ITEM-A"),
                _master_confirmation("CARGO_ITEM", "ITEM-B"),
                _master_confirmation("PALLET_SPEC", "PALLET-A"),
                _available("ITEM-A", 24),
                _available("ITEM-B", 12),
            ],
        }
    )

    # 直接调用领域 Service，刻意模拟 HTTP 进程在事务提交后、LangGraph 继续前崩溃。
    committed = await service.resume(
        str(run["run_id"]),
        resume_payload,
        "supply-before-crash-001",
    )
    assert committed.status is ShipmentAgentRunStatus.RUNNING
    assert committed.current_stage is ShipmentAgentStage.ASSESSING_DATA_READINESS
    assert committed.pending_interrupt is None
    assert committed.allowed_actions == []
    committed_version = committed.run_version

    recovered = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/reconcile",
        headers=_headers("recover-crash-window-001"),
        json={"expected_run_version": committed_version},
    )
    replayed = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/reconcile",
        headers=_headers("recover-crash-window-001"),
        # 同一请求的网络重试保留第一次看到的旧版本和同一个幂等键。
        json={"expected_run_version": committed_version},
    )
    assert recovered.status_code == replayed.status_code == 200
    restored = recovered.json()
    assert restored["current_stage"] == "AWAITING_CASE_ACTIVATION"
    assert restored["data_readiness"]["status"] == "DEMO_READY"
    assert replayed.json()["run_version"] == restored["run_version"]
    event_types = [event["event_type"] for event in restored["events"]]
    assert event_types.count("DATA_SUPPLEMENTED") == 1
    assert event_types.count("DATA_READINESS_READY") == 1
    assert "RECONCILIATION_REQUESTED" not in event_types

    async with session_factory() as session:
        commands = (
            await session.scalars(
                select(ShipmentAgentCommand).where(ShipmentAgentCommand.run_id == run["run_id"])
            )
        ).all()
    recovery_commands = [
        command for command in commands if command.result_kind is ShipmentAgentCommandResult.NO_OP
    ]
    assert len(recovery_commands) == 1
    assert recovery_commands[0].result_run_version == committed_version


async def test_expired_evidence_stays_blocked_until_latest_fact_is_valid(
    client: AsyncClient,
) -> None:
    run, _ = await _create_run(client, "STALE-001")
    supplied = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("supply-expired-data-001"),
        json={
            "expected_run_version": run["run_version"],
            "interrupt_type": "DATA_READINESS_REMEDIATION",
            "action": "SUPPLY_DATA",
            "data_facts": [
                _master_confirmation("CARGO_ITEM", "ITEM-A"),
                _master_confirmation("CARGO_ITEM", "ITEM-B"),
                _master_confirmation("PALLET_SPEC", "PALLET-A"),
                _available(
                    "ITEM-A",
                    24,
                    observed_at="1999-01-01T00:00:00Z",
                    expires_at="2000-01-01T00:00:00Z",
                ),
                _available("ITEM-B", 12),
            ],
        },
    )
    assert supplied.status_code == 200, supplied.text
    stale = supplied.json()
    assert stale["data_readiness"]["issue_count"] == 1
    assert stale["data_readiness"]["issues"][0]["issue_code"] == "EVIDENCE_STALE"
    assert stale["pending_interrupt"]["details"]["requested_facts"] == [
        {
            "subject_type": "CARGO_ITEM",
            "subject_code": "ITEM-A",
            "fact_type": "AVAILABLE_QUANTITY",
        }
    ]

    refreshed = await client.post(
        f"/api/v1/shipment-agent-runs/{run['run_id']}/resume",
        headers=_headers("refresh-expired-data-001"),
        json={
            "expected_run_version": stale["run_version"],
            "interrupt_type": "DATA_READINESS_REMEDIATION",
            "action": "SUPPLY_DATA",
            "data_facts": [_available("ITEM-A", 24)],
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["data_readiness"]["status"] == "DEMO_READY"


class _UnusedLinkGateway:
    async def get_detail(self, application_number: str):  # pragma: no cover
        raise AssertionError(application_number)

    async def submit(self, application_number: str):  # pragma: no cover
        raise AssertionError(application_number)

    async def retry(self, application_number: str):  # pragma: no cover
        raise AssertionError(application_number)


async def test_legacy_v1_run_bypasses_new_gate(
    client: AsyncClient,
) -> None:
    """迁移前运行保留 V1 语义，但不会把 workflow_version 改写成 V2。"""

    task_number = "LP-DEMO-DATA-LEGACY-001"
    case_number = "SHP-DEMO-DATA-LEGACY-001"
    task = await client.post(
        "/api/v1/load-planning-tasks",
        json=_task_payload(task_number),
    )
    assert task.status_code == 201
    case = await client.post(
        "/api/v1/shipment-cases",
        headers=_headers("create-data-legacy-001"),
        json={
            "case_number": case_number,
            "case_name": "迁移前 V1 运行",
            "transport_scenario": "LOAD_PLANNING_ONLY",
        },
    )
    bound = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        headers=_headers("bind-data-legacy-001"),
        json={
            "resource_type": "LOAD_PLANNING_TASK",
            "resource_number": task_number,
            "expected_revision": case.json()["revision"],
        },
    )
    assert bound.status_code == 200

    session_factory = app.dependency_overrides[get_session_factory]()
    run_id = str(uuid4())
    async with session_factory() as session:
        shipment_case = await session.scalar(
            select(ShipmentCase).where(ShipmentCase.case_number == case_number)
        )
        assert shipment_case is not None
        session.add(
            ShipmentAgentRun(
                id=run_id,
                case_id=shipment_case.id,
                status=ShipmentAgentRunStatus.RUNNING,
                current_stage=ShipmentAgentStage.SYNCING_CASE,
                run_version=1,
                workflow_version="SHIPMENT_COORDINATOR_LANGGRAPH_V1",
                started_case_revision=shipment_case.revision,
                required_capabilities=["LOAD_PLANNING"],
                scenario_policy_confirmed=True,
                selected_load_task_number=task_number,
                booking_submission_uncertain=False,
                pending_interrupt=None,
                allowed_actions=[],
                child_agent_snapshot=None,
                data_readiness_status=(ShipmentDataReadinessStatus.LEGACY_BYPASSED),
                data_readiness_revision=0,
                data_readiness_snapshot=None,
                data_readiness_checked_at=None,
                start_idempotency_key_hash="a" * 64,
                start_request_hash="b" * 64,
            )
        )
        await session.commit()

    service = ShipmentAgentService(
        session_factory,
        _UnusedLinkGateway(),
        InMemorySaver(),
    )
    workflow = ShipmentWorkflowOrchestrator(
        service=service,
        checkpointer=service.checkpointer,
    )
    await workflow.synchronize(run_id)
    legacy = await service.get(run_id)
    assert legacy.workflow_version == "SHIPMENT_COORDINATOR_LANGGRAPH_V1"
    assert legacy.data_readiness.status is ShipmentDataReadinessStatus.LEGACY_BYPASSED
    assert legacy.data_readiness.policy_version == "LEGACY_BYPASSED"
    assert legacy.current_stage is ShipmentAgentStage.AWAITING_CASE_ACTIVATION


def test_alembic_upgrade_marks_existing_runs_as_legacy_bypassed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """从 20261016_01 带一条既有运行升级到新 head。"""

    from app.core.config import settings

    database_path = tmp_path / "readiness-migration.sqlite3"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    monkeypatch.setattr(
        settings,
        "load_planning_database_url",
        database_url,
    )
    service_root = Path(__file__).resolve().parents[1]
    config = Config(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "alembic"))
    command.upgrade(config, "20261016_01")

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO shipment_cases (
                id, case_number, case_name, responsible_organization_code,
                transport_scenario, status, revision, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "case-before-readiness",
                "SHP-MIGRATION-001",
                "迁移前案件",
                "ORG-DEMO",
                "LOAD_PLANNING_ONLY",
                "DRAFT",
                1,
                None,
            ),
        )
        connection.execute(
            """
            INSERT INTO shipment_agent_runs (
                id, case_id, status, current_stage, run_version,
                workflow_version, started_case_revision,
                required_capabilities, scenario_policy_confirmed,
                selected_load_task_number, selected_load_run_id,
                locked_load_version_id, locked_load_version_number,
                selected_booking_application_number, booking_status,
                carrier_submission_status, booking_reference,
                booking_submission_uncertain, pending_interrupt,
                allowed_actions, child_agent_snapshot,
                failure_code, failure_message,
                start_idempotency_key_hash, start_request_hash,
                completed_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "run-before-readiness",
                "case-before-readiness",
                "RUNNING",
                "SYNCING_CASE",
                1,
                "SHIPMENT_COORDINATOR_LANGGRAPH_V1",
                1,
                '["LOAD_PLANNING"]',
                1,
                "LP-MIGRATION-001",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                0,
                None,
                "[]",
                None,
                None,
                None,
                "c" * 64,
                "d" * 64,
                None,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")
    connection = sqlite3.connect(database_path)
    try:
        migrated = connection.execute(
            """
            SELECT data_readiness_status, data_readiness_revision,
                   data_readiness_snapshot, data_readiness_checked_at
            FROM shipment_agent_runs
            WHERE id = 'run-before-readiness'
            """
        ).fetchone()
        assert migrated == ("LEGACY_BYPASSED", 0, None, None)
        table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'shipment_case_data_readiness'
            """
        ).fetchone()
        assert table == ("shipment_case_data_readiness",)
    finally:
        connection.close()
