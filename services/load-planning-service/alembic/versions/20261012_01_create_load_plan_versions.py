"""create immutable load plan versions and append-only events

Revision ID: 20261012_01
Revises: 20261010_02
Create Date: 2026-10-12 16:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261012_01"
down_revision: str | None = "20261010_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

plan_version_status = sa.Enum(
    "LOCKED",
    name="load_plan_version_status",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    """新增正式版本主表和只追加审计事件表。"""

    op.create_table(
        "load_plan_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", plan_version_status, nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=False),
        sa.Column("source_plan_id", sa.String(length=36), nullable=False),
        sa.Column("plan_code", sa.String(length=64), nullable=False),
        sa.Column("selected_pallet_code", sa.String(length=64), nullable=False),
        sa.Column("supersedes_version_id", sa.String(length=36), nullable=True),
        sa.Column("source_run_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_schema_version", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("constraint_snapshot", sa.JSON(), nullable=False),
        sa.Column("constraint_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False),
        sa.Column("plan_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("approval_actor_subject", sa.String(length=128), nullable=False),
        sa.Column("approval_actor_display_name", sa.String(length=128), nullable=False),
        sa.Column("approval_actor_source", sa.String(length=64), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
        sa.Column("approval_comment", sa.Text(), nullable=True),
        sa.Column("locked_by_subject", sa.String(length=128), nullable=False),
        sa.Column("locked_by_display_name", sa.String(length=128), nullable=False),
        sa.Column("locked_by_source", sa.String(length=64), nullable=False),
        sa.Column("lock_reason", sa.Text(), nullable=False),
        sa.Column(
            "locked_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_load_plan_versions_version_positive",
        ),
        sa.CheckConstraint(
            "source_run_version > 0",
            name="ck_load_plan_versions_run_version_positive",
        ),
        sa.CheckConstraint(
            "supersedes_version_id IS NULL OR supersedes_version_id <> id",
            name="ck_load_plan_versions_not_self_superseding",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["load_planning_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["agent_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"],
            ["load_plan_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "version_number",
            name="uq_load_plan_versions_task_version",
        ),
        sa.UniqueConstraint(
            "source_run_id",
            name="uq_load_plan_versions_source_run",
        ),
        sa.UniqueConstraint(
            "supersedes_version_id",
            name="uq_load_plan_versions_supersedes",
        ),
    )
    op.create_index(
        "ix_load_plan_versions_task_id",
        "load_plan_versions",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_load_plan_versions_source_run_id",
        "load_plan_versions",
        ["source_run_id"],
        unique=False,
    )

    op.create_table(
        "load_plan_version_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_version_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_subject", sa.String(length=128), nullable=False),
        sa.Column("actor_display_name", sa.String(length=128), nullable=False),
        sa.Column("actor_source", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id"],
            ["load_plan_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_version_id",
            "sequence",
            name="uq_load_plan_version_events_sequence",
        ),
    )
    op.create_index(
        "ix_load_plan_version_events_version_id",
        "load_plan_version_events",
        ["plan_version_id"],
        unique=False,
    )


def downgrade() -> None:
    """按外键依赖逆序移除正式版本表。"""

    op.drop_index(
        "ix_load_plan_version_events_version_id",
        table_name="load_plan_version_events",
    )
    op.drop_table("load_plan_version_events")
    op.drop_index(
        "ix_load_plan_versions_source_run_id",
        table_name="load_plan_versions",
    )
    op.drop_index(
        "ix_load_plan_versions_task_id",
        table_name="load_plan_versions",
    )
    op.drop_table("load_plan_versions")
