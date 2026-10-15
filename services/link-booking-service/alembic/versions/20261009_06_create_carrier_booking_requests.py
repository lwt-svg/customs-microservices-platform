"""create carrier booking requests and callback receipts

Revision ID: 20261009_06
Revises: 20261009_05
Create Date: 2026-10-09 15:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_06"
down_revision: str | None = "20261009_05"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """扩展申请终态，并创建 LINK 侧可靠投递和回调幂等表。"""

    # MySQL 的 ENUM 定义保存在列上。这里需要同时扩展申请快照、事件前后状态以及
    # 事件类型；否则 Python 新枚举虽然能启动，数据库仍会拒绝 REJECTED。
    old_booking_status = sa.Enum(
        "DRAFT",
        "SUBMITTED",
        "CONFIRMED",
        "CANCELLED",
        name="booking_status",
    )
    new_booking_status = sa.Enum(
        "DRAFT",
        "SUBMITTED",
        "CONFIRMED",
        "REJECTED",
        "CANCELLED",
        name="booking_status",
    )
    op.alter_column(
        "booking_applications",
        "status",
        existing_type=old_booking_status,
        type_=new_booking_status,
        existing_nullable=False,
    )
    op.alter_column(
        "booking_application_events",
        "previous_status",
        existing_type=old_booking_status,
        type_=new_booking_status,
        existing_nullable=True,
    )
    op.alter_column(
        "booking_application_events",
        "current_status",
        existing_type=old_booking_status,
        type_=new_booking_status,
        existing_nullable=False,
    )
    old_event_type = sa.Enum(
        "DRAFT_CREATED",
        "SUBMITTED",
        "CONFIRMED",
        "CANCELLED",
        name="booking_event_type",
    )
    new_event_type = sa.Enum(
        "DRAFT_CREATED",
        "SUBMITTED",
        "CARRIER_PROCESSING",
        "CONFIRMED",
        "REJECTED",
        "CANCELLED",
        name="booking_event_type",
    )
    op.alter_column(
        "booking_application_events",
        "event_type",
        existing_type=old_event_type,
        type_=new_event_type,
        existing_nullable=False,
    )

    request_status = sa.Enum(
        "PENDING",
        "SENDING",
        "RETRY_WAIT",
        "ACCEPTED",
        "PROCESSING",
        "CONFIRMED",
        "REJECTED",
        "DEAD",
        "CANCELLED",
        name="carrier_booking_request_status",
    )
    request_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "carrier_booking_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("carrier_request_id", sa.String(length=64), nullable=True),
        sa.Column("status", request_status, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("manual_retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
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
            ["application_id"],
            ["booking_applications.id"],
            name="fk_carrier_booking_requests_application_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id"),
        sa.UniqueConstraint("carrier_request_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_carrier_booking_requests_status_next_attempt_at",
        "carrier_booking_requests",
        ["status", "next_attempt_at"],
    )

    op.create_table(
        "carrier_callback_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("callback_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("carrier_status", request_status, nullable=False),
        sa.Column("ignored", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["carrier_booking_requests.id"],
            name="fk_carrier_callback_receipts_request_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("callback_id"),
    )
    op.create_index(
        "ix_carrier_callback_receipts_request_id",
        "carrier_callback_receipts",
        ["request_id"],
    )


def downgrade() -> None:
    """删除承运人集成表，并把申请枚举恢复到第 21 章定义。"""

    op.drop_index(
        "ix_carrier_callback_receipts_request_id",
        table_name="carrier_callback_receipts",
    )
    op.drop_table("carrier_callback_receipts")
    op.drop_index(
        "ix_carrier_booking_requests_status_next_attempt_at",
        table_name="carrier_booking_requests",
    )
    op.drop_table("carrier_booking_requests")
    sa.Enum(name="carrier_booking_request_status").drop(op.get_bind(), checkfirst=True)

    expanded_booking_status = sa.Enum(
        "DRAFT",
        "SUBMITTED",
        "CONFIRMED",
        "REJECTED",
        "CANCELLED",
        name="booking_status",
    )
    old_booking_status = sa.Enum(
        "DRAFT",
        "SUBMITTED",
        "CONFIRMED",
        "CANCELLED",
        name="booking_status",
    )
    op.alter_column(
        "booking_application_events",
        "previous_status",
        existing_type=expanded_booking_status,
        type_=old_booking_status,
        existing_nullable=True,
    )
    op.alter_column(
        "booking_application_events",
        "current_status",
        existing_type=expanded_booking_status,
        type_=old_booking_status,
        existing_nullable=False,
    )
    op.alter_column(
        "booking_applications",
        "status",
        existing_type=expanded_booking_status,
        type_=old_booking_status,
        existing_nullable=False,
    )
    expanded_event_type = sa.Enum(
        "DRAFT_CREATED",
        "SUBMITTED",
        "CARRIER_PROCESSING",
        "CONFIRMED",
        "REJECTED",
        "CANCELLED",
        name="booking_event_type",
    )
    old_event_type = sa.Enum(
        "DRAFT_CREATED",
        "SUBMITTED",
        "CONFIRMED",
        "CANCELLED",
        name="booking_event_type",
    )
    op.alter_column(
        "booking_application_events",
        "event_type",
        existing_type=expanded_event_type,
        type_=old_event_type,
        existing_nullable=False,
    )
