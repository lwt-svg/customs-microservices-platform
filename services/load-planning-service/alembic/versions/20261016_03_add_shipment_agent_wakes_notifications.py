"""add shipment agent automatic wakes and business notifications

Revision ID: 20261016_03
Revises: 20261016_02
Create Date: 2026-10-16 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261016_03"
down_revision: str | None = "20261016_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

notification_type = sa.Enum(
    "ACTION_REQUIRED",
    "RESOURCE_ATTENTION",
    "EXTERNAL_WAIT",
    "RUN_FAILED",
    "AUTO_WAKE_FAILED",
    name="shipment_agent_notification_type",
    native_enum=False,
    length=32,
)
notification_severity = sa.Enum(
    "INFO",
    "WARNING",
    "ERROR",
    name="shipment_agent_notification_severity",
    native_enum=False,
    length=16,
)
notification_status = sa.Enum(
    "OPEN",
    "RESOLVED",
    name="shipment_agent_notification_status",
    native_enum=False,
    length=16,
)


def upgrade() -> None:
    """加入可恢复的数据库租约和站内通知权威记录。

    所有新列都有安全默认值，因此已有 ``.env.local`` 和历史运行不需要人工补配置。
    历史活动运行的 ``next_wake_at`` 保持 NULL；Worker 会把超过恢复宽限期、且处于可自动
    检查状态的 NULL 记录视为需要修复的遗留运行。
    """

    with op.batch_alter_table("shipment_agent_runs") as batch_op:
        batch_op.add_column(sa.Column("next_wake_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("wake_reason", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "wake_attempt_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("wake_lease_token", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("wake_lease_until", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("last_wake_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("wake_last_error", sa.String(length=500), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_shipment_agent_runs_wake_attempt_non_negative",
            "wake_attempt_count >= 0",
        )
        batch_op.create_index(
            "ix_shipment_agent_runs_status_next_wake",
            ["status", "next_wake_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_shipment_agent_runs_wake_lease_until",
            ["wake_lease_until"],
            unique=False,
        )

    op.create_table(
        "shipment_agent_notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("source_event_sequence", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("notification_type", notification_type, nullable=False),
        sa.Column("severity", notification_severity, nullable=False),
        sa.Column("status", notification_status, nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("owner_role", sa.String(length=64), nullable=False),
        sa.Column("action_context", sa.JSON(), nullable=False),
        sa.Column(
            "occurrence_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "occurrence_count > 0",
            name="ck_shipment_agent_notifications_occurrence_positive",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["shipment_agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "dedupe_key",
            name="uq_shipment_agent_notifications_run_dedupe",
        ),
    )
    op.create_index(
        "ix_shipment_agent_notifications_run_id",
        "shipment_agent_notifications",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_shipment_agent_notifications_status_created",
        "shipment_agent_notifications",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """移除通知表与自动唤醒控制面字段。"""

    op.drop_index(
        "ix_shipment_agent_notifications_status_created",
        table_name="shipment_agent_notifications",
    )
    op.drop_index(
        "ix_shipment_agent_notifications_run_id",
        table_name="shipment_agent_notifications",
    )
    op.drop_table("shipment_agent_notifications")

    with op.batch_alter_table("shipment_agent_runs") as batch_op:
        batch_op.drop_index("ix_shipment_agent_runs_wake_lease_until")
        batch_op.drop_index("ix_shipment_agent_runs_status_next_wake")
        batch_op.drop_constraint(
            "ck_shipment_agent_runs_wake_attempt_non_negative",
            type_="check",
        )
        batch_op.drop_column("wake_last_error")
        batch_op.drop_column("last_wake_at")
        batch_op.drop_column("wake_lease_until")
        batch_op.drop_column("wake_lease_token")
        batch_op.drop_column("wake_attempt_count")
        batch_op.drop_column("wake_reason")
        batch_op.drop_column("next_wake_at")
