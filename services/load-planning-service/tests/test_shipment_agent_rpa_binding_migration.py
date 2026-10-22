"""Shipment Agent 与统一 RPA Job 绑定表的迁移测试。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.models.shipment_agent_run import ShipmentAgentRun, ShipmentAgentStage

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(
    *arguments: str,
    database_url: str,
) -> subprocess.CompletedProcess[str]:
    """在隔离数据库上执行 Alembic，不读取开发者本机的服务发现配置。"""

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


def test_binding_model_registers_new_stages_and_relationships() -> None:
    """ORM 必须能从 Run 有序加载绑定，并从绑定继续加载 Job。"""

    assert {
        ShipmentAgentStage.DISPATCHING_RPA_JOB,
        ShipmentAgentStage.AWAITING_RPA_RESULT,
        ShipmentAgentStage.AWAITING_RPA_ATTENTION,
    }.issubset(set(ShipmentAgentStage))

    table = Base.metadata.tables["shipment_agent_rpa_job_bindings"]
    assert set(table.c) == {
        table.c.id,
        table.c.run_id,
        table.c.job_id,
        table.c.sequence,
        table.c.operation,
        table.c.created_at,
    }
    assert ShipmentAgentRun.rpa_job_bindings.property.lazy == "selectin"
    assert table.c.job_id in {foreign_key.parent for foreign_key in table.foreign_keys}


def test_binding_migration_creates_constraints_and_foreign_keys(
    tmp_path: Path,
) -> None:
    """从空库升级到 head，确认顺序、去重和引用边界真正落到数据库。"""

    database_path = tmp_path / "shipment-rpa-binding.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    completed = _run_alembic("upgrade", "head", database_url=async_url)
    assert completed.returncode == 0, completed.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        table_name = "shipment_agent_rpa_job_bindings"
        assert table_name in inspector.get_table_names()
        assert {column["name"] for column in inspector.get_columns(table_name)} == {
            "id",
            "run_id",
            "job_id",
            "sequence",
            "operation",
            "created_at",
        }

        unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        assert ("run_id", "sequence") in unique_constraints
        assert ("run_id", "job_id") in unique_constraints

        check_constraints = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspector.get_check_constraints(table_name)
        }
        assert "ck_shipment_agent_rpa_bindings_sequence_positive" in check_constraints
        assert "sequence > 0" in check_constraints[
            "ck_shipment_agent_rpa_bindings_sequence_positive"
        ]

        foreign_keys = {
            (
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
                foreign_key["options"].get("ondelete"),
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        }
        assert foreign_keys == {
            (("run_id",), "shipment_agent_runs", "CASCADE"),
            (("job_id",), "rpa_jobs", "RESTRICT"),
        }

        indexes = {
            (index["name"], tuple(index["column_names"]))
            for index in inspector.get_indexes(table_name)
        }
        assert (
            "ix_shipment_agent_rpa_job_bindings_job_id",
            ("job_id",),
        ) in indexes
    finally:
        engine.dispose()


def test_binding_migration_renders_mysql_offline_sql() -> None:
    """发布前可生成 MySQL SQL，无需连接企业数据库。"""

    rendered = _run_alembic(
        "upgrade",
        "20261022_01:20261022_02",
        "--sql",
        database_url=("mysql+asyncmy://offline_user:offline_password@127.0.0.1/offline_db"),
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "CREATE TABLE shipment_agent_rpa_job_bindings" in rendered.stdout
    assert "ck_shipment_agent_rpa_bindings_sequence_positive" in rendered.stdout
    assert "uq_shipment_agent_rpa_bindings_run_sequence" in rendered.stdout
    assert "uq_shipment_agent_rpa_bindings_run_job" in rendered.stdout
    assert "ix_shipment_agent_rpa_job_bindings_job_id" in rendered.stdout
