"""Shipment Agent 控制面修复标记的迁移测试。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPAIR_REASON = "REPAIR_CONTROL_PROJECTION"


def _alembic_environment(database_url: str) -> dict[str, str]:
    return {
        **os.environ,
        "LOAD_PLANNING_DATABASE_URL": database_url,
        "LOAD_PLANNING_NACOS_DISCOVERY_ENABLED": "false",
    }


def _run_alembic(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            *arguments,
        ],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_projection_repair_migration_backfills_only_unplanned_non_running_rows(
    tmp_path: Path,
) -> None:
    """03 已执行的数据库可线性升级，且任何既有计划都不会被迁移覆盖。"""

    database_path = tmp_path / "shipment-agent-projection-repair.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    environment = _alembic_environment(async_url)
    upgrade_03 = _run_alembic("upgrade", "20261016_03", environment=environment)
    assert upgrade_03.returncode == 0, upgrade_03.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO shipment_cases (
                        id,
                        case_number,
                        case_name,
                        responsible_organization_code,
                        transport_scenario,
                        status,
                        revision,
                        description
                    ) VALUES (
                        'case-projection-repair',
                        'SHP-MIGRATION-PROJECTION-001',
                        '控制面投影迁移测试',
                        'ORG-DEMO',
                        'LOAD_PLANNING_ONLY',
                        'DRAFT',
                        1,
                        NULL
                    )
                    """
                )
            )
            rows = [
                ("waiting-human", "WAITING_HUMAN", "AWAITING_CASE_ACTIVATION"),
                ("waiting-resource", "WAITING_RESOURCE", "AWAITING_RESOURCE_BINDING"),
                ("waiting-external", "WAITING_EXTERNAL", "AWAITING_BOOKING_RESULT"),
                ("completed", "COMPLETED", "COMPLETED"),
                ("failed", "FAILED", "FAILED"),
                ("cancelled", "CANCELLED", "CANCELLED"),
                ("running", "RUNNING", "SYNCING_CASE"),
                ("existing-plan", "WAITING_HUMAN", "AWAITING_CASE_ACTIVATION"),
            ]
            for index, (run_id, status, stage) in enumerate(rows, start=1):
                connection.execute(
                    text(
                        """
                        INSERT INTO shipment_agent_runs (
                            id,
                            case_id,
                            status,
                            current_stage,
                            run_version,
                            workflow_version,
                            started_case_revision,
                            required_capabilities,
                            scenario_policy_confirmed,
                            booking_submission_uncertain,
                            allowed_actions,
                            start_idempotency_key_hash,
                            start_request_hash,
                            next_wake_at,
                            wake_reason
                        ) VALUES (
                            :id,
                            'case-projection-repair',
                            :status,
                            :stage,
                            1,
                            'SHIPMENT_COORDINATOR_LANGGRAPH_V2',
                            1,
                            '["LOAD_PLANNING"]',
                            1,
                            0,
                            '[]',
                            :key_hash,
                            :request_hash,
                            :next_wake_at,
                            :wake_reason
                        )
                        """
                    ),
                    {
                        "id": run_id,
                        "status": status,
                        "stage": stage,
                        "key_hash": f"{index:064x}",
                        "request_hash": f"{index + 100:064x}",
                        "next_wake_at": (
                            "2099-01-01 00:00:00" if run_id == "existing-plan" else None
                        ),
                        "wake_reason": (
                            "EXISTING_NORMAL_PLAN" if run_id == "existing-plan" else None
                        ),
                    },
                )
    finally:
        engine.dispose()

    upgrade_head = _run_alembic("upgrade", "head", environment=environment)
    assert upgrade_head.returncode == 0, upgrade_head.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            migrated = {
                row["id"]: row
                for row in connection.execute(
                    text(
                        """
                        SELECT id, next_wake_at, wake_reason
                        FROM shipment_agent_runs
                        ORDER BY id
                        """
                    )
                )
                .mappings()
                .all()
            }
    finally:
        engine.dispose()

    for run_id in (
        "waiting-human",
        "waiting-resource",
        "waiting-external",
        "completed",
        "failed",
        "cancelled",
    ):
        assert migrated[run_id]["next_wake_at"] is not None
        assert migrated[run_id]["wake_reason"] == REPAIR_REASON

    assert migrated["running"]["next_wake_at"] is None
    assert migrated["running"]["wake_reason"] is None
    assert str(migrated["existing-plan"]["next_wake_at"]).startswith("2099-01-01")
    assert migrated["existing-plan"]["wake_reason"] == "EXISTING_NORMAL_PLAN"


def test_projection_repair_migration_renders_mysql_offline_sql() -> None:
    """04 可生成无需连接数据库的 MySQL 发布 SQL，并保留精确保护条件。"""

    environment = _alembic_environment(
        "mysql+asyncmy://offline_user:offline_password@127.0.0.1/offline_db"
    )
    rendered = _run_alembic(
        "upgrade",
        "20261016_03:20261016_04",
        "--sql",
        environment=environment,
    )
    assert rendered.returncode == 0, rendered.stderr
    sql = rendered.stdout
    assert "UPDATE shipment_agent_runs" in sql
    assert "REPAIR_CONTROL_PROJECTION" in sql
    assert "next_wake_at IS NULL" in sql
    assert "wake_reason IS NULL" in sql
    assert "wake_lease_token IS NULL" in sql
    assert "wake_lease_until IS NULL" in sql
