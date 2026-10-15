"""add booking transport mode

Revision ID: 20261009_04
Revises: 20261008_03
Create Date: 2026-10-09 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_04"
down_revision: str | None = "20261008_03"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """增加人工确认后的运输方式，并为旧学习数据回填 SEA。"""

    op.add_column(
        "booking_applications",
        sa.Column(
            "transport_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'SEA'"),
        ),
    )
    op.alter_column("booking_applications", "transport_mode", server_default=None)


def downgrade() -> None:
    """回滚运输方式字段。"""

    op.drop_column("booking_applications", "transport_mode")
