"""创建独立模拟承运人、时间线和回调投递表。

Revision ID: 20261009_01
Revises:
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

carrier_booking_status = sa.Enum(
    "RECEIVED",
    "PROCESSING",
    "CONFIRMED",
    "REJECTED",
    name="carrier_booking_status",
)
carrier_booking_event_type = sa.Enum(
    "RECEIVED",
    "PROCESSING_STARTED",
    "CONFIRMED",
    "REJECTED",
    name="carrier_booking_event_type",
)
callback_delivery_status = sa.Enum(
    "PENDING",
    "SENDING",
    "RETRY_WAIT",
    "DELIVERED",
    "DEAD",
    name="callback_delivery_status",
)
callback_event_type = sa.Enum(
    "BOOKING_PROCESSING",
    "BOOKING_CONFIRMED",
    "BOOKING_REJECTED",
    name="callback_event_type",
)


def upgrade() -> None:
    """新建不与 LINK 共用的本地模拟 schema 表。"""

    op.create_table(
        "carrier_bookings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("application_number", sa.String(length=64), nullable=False),
        sa.Column("carrier_request_id", sa.String(length=64), nullable=False),
        sa.Column("origin_port", sa.String(length=64), nullable=False),
        sa.Column("destination_port", sa.String(length=64), nullable=False),
        sa.Column("transport_mode", sa.String(length=32), nullable=False),
        sa.Column("container_type", sa.String(length=32), nullable=False),
        sa.Column("requested_etd", sa.Date(), nullable=True),
        sa.Column("status", carrier_booking_status, nullable=False),
        sa.Column("booking_reference", sa.String(length=64), nullable=True),
        sa.Column("rejection_reason_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("carrier_request_id"),
    )
    op.create_index(
        "ix_carrier_bookings_application_number",
        "carrier_bookings",
        ["application_number"],
        unique=False,
    )
    op.create_index(
        "ix_carrier_bookings_idempotency_key",
        "carrier_bookings",
        ["idempotency_key"],
        unique=False,
    )
    op.create_index(
        "ix_carrier_bookings_carrier_request_id",
        "carrier_bookings",
        ["carrier_request_id"],
        unique=False,
    )

    op.create_table(
        "carrier_booking_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("event_type", carrier_booking_event_type, nullable=False),
        sa.Column("previous_status", carrier_booking_status, nullable=True),
        sa.Column("current_status", carrier_booking_status, nullable=False),
        sa.Column("actor_label", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["booking_id"], ["carrier_bookings.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_carrier_booking_events_booking_id",
        "carrier_booking_events",
        ["booking_id"],
        unique=False,
    )

    op.create_table(
        "callback_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("callback_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", callback_event_type, nullable=False),
        sa.Column("status", callback_delivery_status, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["booking_id"], ["carrier_bookings.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("callback_id"),
    )
    op.create_index(
        "ix_callback_deliveries_booking_id",
        "callback_deliveries",
        ["booking_id"],
        unique=False,
    )


def downgrade() -> None:
    """仅用于本地学习环境回滚此服务自己的三张表。"""

    op.drop_index("ix_callback_deliveries_booking_id", table_name="callback_deliveries")
    op.drop_table("callback_deliveries")
    op.drop_index("ix_carrier_booking_events_booking_id", table_name="carrier_booking_events")
    op.drop_table("carrier_booking_events")
    op.drop_index(
        "ix_carrier_bookings_carrier_request_id",
        table_name="carrier_bookings",
    )
    op.drop_index("ix_carrier_bookings_idempotency_key", table_name="carrier_bookings")
    op.drop_index("ix_carrier_bookings_application_number", table_name="carrier_bookings")
    op.drop_table("carrier_bookings")
