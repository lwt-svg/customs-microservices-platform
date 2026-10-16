"""create shipment coordinator agent runs, events and commands

Revision ID: 20261016_01
Revises: 20261015_02
Create Date: 2026-10-16 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261016_01"
down_revision: str | None = "20261015_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

shipment_agent_run_status = sa.Enum(
    "RUNNING",
    "WAITING_HUMAN",
    "WAITING_RESOURCE",
    "WAITING_EXTERNAL",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="shipment_agent_run_status",
    native_enum=False,
    length=32,
)
shipment_agent_stage = sa.Enum(
    "SYNCING_CASE",
    "AWAITING_SCENARIO_POLICY",
    "AWAITING_RESOURCE_BINDING",
    "AWAITING_RESOURCE_SELECTION",
    "AWAITING_CASE_ACTIVATION",
    "STARTING_LOAD_AGENT",
    "AWAITING_LOAD_CONSTRAINTS",
    "AWAITING_LOAD_REVIEW",
    "AWAITING_LOAD_RELEASE",
    "AWAITING_LOAD_REPLAN",
    "AWAITING_BOOKING_SUBMIT_APPROVAL",
    "SUBMITTING_BOOKING",
    "AWAITING_BOOKING_RESULT",
    "AWAITING_BOOKING_RETRY_APPROVAL",
    "AWAITING_BOOKING_REPLACEMENT",
    "AWAITING_COMPLETION_APPROVAL",
    "COMPLETING_CASE",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="shipment_agent_stage",
    native_enum=False,
    length=48,
)
shipment_agent_command_type = sa.Enum(
    "START",
    "RESUME",
    "RECONCILE",
    name="shipment_agent_command_type",
    native_enum=False,
    length=24,
)
shipment_agent_command_result = sa.Enum(
    "APPLIED",
    "NO_OP",
    name="shipment_agent_command_result",
    native_enum=False,
    length=16,
)


def upgrade() -> None:
    """创建 Shipment Coordinator Agent 的三张权威业务表。"""

    op.create_table(
        "shipment_agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("status", shipment_agent_run_status, nullable=False),
        sa.Column("current_stage", shipment_agent_stage, nullable=False),
        sa.Column("run_version", sa.Integer(), nullable=False),
        sa.Column("workflow_version", sa.String(length=64), nullable=False),
        sa.Column("started_case_revision", sa.Integer(), nullable=False),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("scenario_policy_confirmed", sa.Boolean(), nullable=False),
        sa.Column("selected_load_task_number", sa.String(length=64), nullable=True),
        sa.Column("selected_load_run_id", sa.String(length=36), nullable=True),
        sa.Column("locked_load_version_id", sa.String(length=36), nullable=True),
        sa.Column("locked_load_version_number", sa.Integer(), nullable=True),
        sa.Column(
            "selected_booking_application_number",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("booking_status", sa.String(length=32), nullable=True),
        sa.Column(
            "carrier_submission_status",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column("booking_reference", sa.String(length=64), nullable=True),
        sa.Column(
            "booking_submission_uncertain",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("pending_interrupt", sa.JSON(), nullable=True),
        sa.Column("allowed_actions", sa.JSON(), nullable=False),
        sa.Column("child_agent_snapshot", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("start_idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("start_request_hash", sa.String(length=64), nullable=False),
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
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "run_version > 0",
            name="ck_shipment_agent_runs_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["shipment_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "start_idempotency_key_hash",
            name="uq_shipment_agent_runs_start_idempotency_key",
        ),
    )
    op.create_index(
        "ix_shipment_agent_runs_case_id",
        "shipment_agent_runs",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        "ix_shipment_agent_runs_status",
        "shipment_agent_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_shipment_agent_runs_updated_at",
        "shipment_agent_runs",
        ["updated_at"],
        unique=False,
    )

    op.create_table(
        "shipment_agent_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("stage", shipment_agent_stage, nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("actor_subject", sa.String(length=128), nullable=False),
        sa.Column("actor_display_name", sa.String(length=128), nullable=False),
        sa.Column("actor_source", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["shipment_agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_shipment_agent_events_run_sequence",
        ),
    )
    op.create_index(
        "ix_shipment_agent_events_run_id",
        "shipment_agent_events",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "shipment_agent_commands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("command_type", shipment_agent_command_type, nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("result_kind", shipment_agent_command_result, nullable=False),
        sa.Column("result_run_version", sa.Integer(), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result_run_version > 0",
            name="ck_shipment_agent_commands_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["shipment_agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_shipment_agent_commands_idempotency_key",
        ),
    )
    op.create_index(
        "ix_shipment_agent_commands_run_id",
        "shipment_agent_commands",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    """按外键依赖逆序删除总 Agent 表。"""

    op.drop_index(
        "ix_shipment_agent_commands_run_id",
        table_name="shipment_agent_commands",
    )
    op.drop_table("shipment_agent_commands")
    op.drop_index(
        "ix_shipment_agent_events_run_id",
        table_name="shipment_agent_events",
    )
    op.drop_table("shipment_agent_events")
    op.drop_index(
        "ix_shipment_agent_runs_updated_at",
        table_name="shipment_agent_runs",
    )
    op.drop_index(
        "ix_shipment_agent_runs_status",
        table_name="shipment_agent_runs",
    )
    op.drop_index(
        "ix_shipment_agent_runs_case_id",
        table_name="shipment_agent_runs",
    )
    op.drop_table("shipment_agent_runs")
