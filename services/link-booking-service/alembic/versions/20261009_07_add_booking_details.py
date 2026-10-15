"""add confirmed packing-list details to booking applications

Revision ID: 20261009_07
Revises: 20261009_06
Create Date: 2026-10-09 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_07"
down_revision: str | None = "20261009_06"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """增加可空 JSON，兼容迁移前已经存在的申请。"""

    op.add_column(
        "booking_applications",
        sa.Column("booking_details", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """移除结构化 Packing List 明细。"""

    op.drop_column("booking_applications", "booking_details")
