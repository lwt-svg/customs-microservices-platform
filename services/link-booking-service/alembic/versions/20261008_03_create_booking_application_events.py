"""create booking application audit events

Revision ID: 20261008_03
Revises: 20261008_02
Create Date: 2026-10-08 18:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261008_03"
down_revision: str | None = "20261008_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """为已有订舱申请增加不可变操作流水表。

    旧申请不会被补造“创建事件”：迁移不能根据猜测伪造历史。只有迁移后发生的新创建、
    提交、确认、取消操作才会留下完整流水。
    """

    event_type = sa.Enum(
        "DRAFT_CREATED",
        "SUBMITTED",
        "CONFIRMED",
        "CANCELLED",
        name="booking_event_type",
    )
    event_type.create(op.get_bind(), checkfirst=True)
    # MySQL 的 ENUM 值随列定义保存，不像 PostgreSQL 那样只引用独立类型名。因此即使
    # booking_status 已由首个迁移创建，这里仍要重复列出合法值，不能只写 name。
    booking_status = sa.Enum(
        "DRAFT",
        "SUBMITTED",
        "CONFIRMED",
        "CANCELLED",
        name="booking_status",
    )

    op.create_table(
        "booking_application_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("previous_status", booking_status, nullable=True),
        sa.Column("current_status", booking_status, nullable=False),
        sa.Column("booking_reference", sa.String(length=64), nullable=True),
        sa.Column(
            "actor_label",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'SYSTEM_DEMO'"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["booking_applications.id"],
            name="fk_booking_application_events_application_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_booking_application_events_application_id",
        "booking_application_events",
        ["application_id"],
    )


def downgrade() -> None:
    """只删除本迁移创建的事件表和事件枚举。"""

    op.drop_index(
        "ix_booking_application_events_application_id",
        table_name="booking_application_events",
    )
    op.drop_table("booking_application_events")
    sa.Enum(name="booking_event_type").drop(op.get_bind(), checkfirst=True)
