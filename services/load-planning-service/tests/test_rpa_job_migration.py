"""统一 RPA Job 数据库迁移测试。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(
    *arguments: str,
    database_url: str,
) -> subprocess.CompletedProcess[str]:
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
        env={
            **os.environ,
            "LOAD_PLANNING_DATABASE_URL": database_url,
            "LOAD_PLANNING_NACOS_DISCOVERY_ENABLED": "false",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_rpa_job_migration_creates_control_plane_tables(tmp_path: Path) -> None:
    """从空数据库升级到 head，确认 ORM 所依赖的表、外键和唯一约束真实存在。"""

    database_path = tmp_path / "rpa-control-plane.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    completed = _run_alembic("upgrade", "head", database_url=async_url)
    assert completed.returncode == 0, completed.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert {
            "rpa_jobs",
            "rpa_job_events",
            "rpa_job_callback_receipts",
        }.issubset(inspector.get_table_names())

        job_unique = {
            tuple(item["column_names"]) for item in inspector.get_unique_constraints("rpa_jobs")
        }
        event_unique = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("rpa_job_events")
        }
        assert ("idempotency_key",) in job_unique
        assert ("job_id", "sequence") in event_unique

        job_foreign_keys = inspector.get_foreign_keys("rpa_jobs")
        assert any(
            key["constrained_columns"] == ["originating_run_id"]
            and key["referred_table"] == "shipment_agent_runs"
            and key["options"].get("ondelete") == "SET NULL"
            for key in job_foreign_keys
        )
        for table_name in ("rpa_job_events", "rpa_job_callback_receipts"):
            foreign_keys = inspector.get_foreign_keys(table_name)
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["referred_table"] == "rpa_jobs"
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"
    finally:
        engine.dispose()


def test_rpa_job_migration_renders_mysql_offline_sql() -> None:
    """发布前可生成 MySQL SQL，不需要连接任何企业数据库。"""

    rendered = _run_alembic(
        "upgrade",
        "20261016_04:20261022_01",
        "--sql",
        database_url=("mysql+asyncmy://offline_user:offline_password@127.0.0.1/offline_db"),
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "CREATE TABLE rpa_jobs" in rendered.stdout
    assert "CREATE TABLE rpa_job_events" in rendered.stdout
    assert "CREATE TABLE rpa_job_callback_receipts" in rendered.stdout
    assert "uq_rpa_jobs_idempotency_key" in rendered.stdout
