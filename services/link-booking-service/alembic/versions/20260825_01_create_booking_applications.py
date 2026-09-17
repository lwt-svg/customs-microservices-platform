"""create booking applications

Revision ID: 20260825_01
Revises:
Create Date: 2026-08-25 17:20:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_01"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """创建只属于 LINK 服务的订舱申请表和独立状态枚举。"""

    booking_status = sa.Enum("DRAFT", "SUBMITTED", "CONFIRMED", "CANCELLED", name="booking_status")
    booking_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "booking_applications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_number", sa.String(length=64), nullable=False),
        sa.Column("booking_reference", sa.String(length=64), nullable=True),
        sa.Column("origin_port", sa.String(length=64), nullable=False),
        sa.Column("destination_port", sa.String(length=64), nullable=False),
        sa.Column("container_type", sa.String(length=32), nullable=False),
        sa.Column("requested_etd", sa.Date(), nullable=True),
        sa.Column("status", booking_status, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_number"),
    )
    op.create_index("ix_booking_applications_application_number", "booking_applications", ["application_number"])


def downgrade() -> None:
    """回滚首期 LINK 申请表及枚举。"""

    op.drop_index("ix_booking_applications_application_number", table_name="booking_applications")
    op.drop_table("booking_applications")
    sa.Enum(name="booking_status").drop(op.get_bind(), checkfirst=True)
