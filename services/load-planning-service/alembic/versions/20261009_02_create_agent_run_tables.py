"""create stateful pallet agent run tables

Revision ID: 20261009_02
Revises: 20261009_01
Create Date: 2026-10-09 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_02"
down_revision: str | None = "20261009_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

run_status = sa.Enum(
    "AWAITING_INPUT",
    "READY_FOR_REVIEW",
    "APPROVED",
    "REJECTED",
    name="load_planning_agent_run_status",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    """创建运行、事件和候选方案表，不修改已经执行过的首版迁移。"""

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=False),
        sa.Column("selected_pallet_code", sa.String(length=64), nullable=True),
        sa.Column("selected_plan_code", sa.String(length=64), nullable=True),
        sa.Column("strategy_codes", sa.JSON(), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["load_planning_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"], unique=False)
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"], unique=False)

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_agent_run_events_run_sequence",
        ),
    )
    op.create_index(
        "ix_agent_run_events_run_id",
        "agent_run_events",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "packing_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("plan_code", sa.String(length=64), nullable=False),
        sa.Column("strategy_code", sa.String(length=32), nullable=False),
        sa.Column("strategy_name", sa.String(length=64), nullable=False),
        sa.Column("feasible", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("pallets", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "plan_code",
            name="uq_packing_plans_run_plan_code",
        ),
    )
    op.create_index("ix_packing_plans_run_id", "packing_plans", ["run_id"], unique=False)


def downgrade() -> None:
    """按外键依赖逆序删除新增表。"""

    op.drop_index("ix_packing_plans_run_id", table_name="packing_plans")
    op.drop_table("packing_plans")
    op.drop_index("ix_agent_run_events_run_id", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_task_id", table_name="agent_runs")
    op.drop_table("agent_runs")
