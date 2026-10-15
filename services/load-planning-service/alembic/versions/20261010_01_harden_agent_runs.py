"""add versioning, failure and replan metadata to agent runs

Revision ID: 20261010_01
Revises: 20261009_02
Create Date: 2026-10-10 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261010_01"
down_revision: str | None = "20261009_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """为运行和候选方案补充可复现、可恢复和可审计字段。"""

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("run_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("parent_run_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("failure_code", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("failure_message", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_agent_runs_parent_run_id",
            "agent_runs",
            ["parent_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_agent_runs_parent_run_id", ["parent_run_id"], unique=False)

    with op.batch_alter_table("packing_plans") as batch:
        # 旧数据来自 DETERMINISTIC_GRID_STRIP_V1 + PALLET_PLAN_SCORE_V1；
        # 迁移给它们补上明确版本，而不是留下不可解释的 NULL。
        batch.add_column(
            sa.Column(
                "algorithm_version",
                sa.String(length=64),
                nullable=False,
                server_default="DETERMINISTIC_GRID_STRIP_V1",
            )
        )
        batch.add_column(
            sa.Column(
                "score_version",
                sa.String(length=64),
                nullable=False,
                server_default="PALLET_PLAN_SCORE_V1",
            )
        )


def downgrade() -> None:
    """按依赖逆序移除本次版本化字段。"""

    with op.batch_alter_table("packing_plans") as batch:
        batch.drop_column("score_version")
        batch.drop_column("algorithm_version")

    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_parent_run_id")
        batch.drop_constraint("fk_agent_runs_parent_run_id", type_="foreignkey")
        batch.drop_column("failure_message")
        batch.drop_column("failure_code")
        batch.drop_column("parent_run_id")
        batch.drop_column("run_version")
