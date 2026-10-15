"""create shipment cases, resource bindings and append-only events

Revision ID: 20261015_01
Revises: 20261012_02
Create Date: 2026-10-15 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261015_01"
down_revision: str | None = "20261012_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

shipment_case_status = sa.Enum(
    "DRAFT",
    "ACTIVE",
    "COMPLETED",
    "CANCELLED",
    name="shipment_case_status",
    native_enum=False,
    length=32,
)
shipment_transport_scenario = sa.Enum(
    "UNDECIDED",
    "SEA_FCL",
    "SEA_LCL",
    "AIR",
    "EXPRESS",
    "LOAD_PLANNING_ONLY",
    "BOOKING_ONLY",
    name="shipment_transport_scenario",
    native_enum=False,
    length=32,
)
shipment_resource_type = sa.Enum(
    "LOAD_PLANNING_TASK",
    "LINK_BOOKING_APPLICATION",
    name="shipment_resource_type",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    """创建案件主表、跨服务资源绑定表和只追加事件表。"""

    op.create_table(
        "shipment_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_number", sa.String(length=64), nullable=False),
        sa.Column("case_name", sa.String(length=128), nullable=False),
        sa.Column(
            "responsible_organization_code",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "transport_scenario",
            shipment_transport_scenario,
            nullable=False,
        ),
        sa.Column("status", shipment_case_status, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "create_idempotency_key_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("create_request_hash", sa.String(length=64), nullable=False),
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
        sa.CheckConstraint(
            "revision > 0",
            name="ck_shipment_cases_revision_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_number",
            name="uq_shipment_cases_case_number",
        ),
        sa.UniqueConstraint(
            "create_idempotency_key_hash",
            name="uq_shipment_cases_create_idempotency_key_hash",
        ),
    )

    op.create_table(
        "shipment_case_resource_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", shipment_resource_type, nullable=False),
        sa.Column("resource_number", sa.String(length=64), nullable=False),
        sa.Column(
            "resource_status_at_verification",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("verification_source", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.Column(
            "bound_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["shipment_cases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_type",
            "resource_number",
            name="uq_shipment_case_bindings_resource",
        ),
    )
    op.create_index(
        "ix_shipment_case_bindings_case_id",
        "shipment_case_resource_bindings",
        ["case_id"],
        unique=False,
    )

    op.create_table(
        "shipment_case_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("previous_status", shipment_case_status, nullable=True),
        sa.Column("current_status", shipment_case_status, nullable=True),
        sa.Column("resource_type", shipment_resource_type, nullable=True),
        sa.Column("resource_number", sa.String(length=64), nullable=True),
        sa.Column("actor_subject", sa.String(length=128), nullable=False),
        sa.Column("actor_display_name", sa.String(length=128), nullable=False),
        sa.Column("actor_source", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["shipment_cases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "sequence",
            name="uq_shipment_case_events_case_sequence",
        ),
        sa.UniqueConstraint(
            "case_id",
            "idempotency_key_hash",
            name="uq_shipment_case_events_case_idempotency_key_hash",
        ),
    )
    op.create_index(
        "ix_shipment_case_events_case_id",
        "shipment_case_events",
        ["case_id"],
        unique=False,
    )


def downgrade() -> None:
    """按外键依赖逆序删除发运案件的三张表。"""

    op.drop_index(
        "ix_shipment_case_events_case_id",
        table_name="shipment_case_events",
    )
    op.drop_table("shipment_case_events")
    op.drop_index(
        "ix_shipment_case_bindings_case_id",
        table_name="shipment_case_resource_bindings",
    )
    op.drop_table("shipment_case_resource_bindings")
    op.drop_table("shipment_cases")
