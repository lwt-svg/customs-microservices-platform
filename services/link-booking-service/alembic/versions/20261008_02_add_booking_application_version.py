"""add optimistic lock version to booking applications

Revision ID: 20261008_02
Revises: 20260825_01
Create Date: 2026-10-08 17:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261008_02"
down_revision: str | None = "20260825_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """为已有订舱申请增加乐观锁版本列。

    server_default 只用于让旧记录在新增非空列时安全补成 1；之后立刻移除数据库默认值，
    新记录的初始版本由 SQLAlchemy 模型明确控制。
    """

    op.add_column(
        "booking_applications",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.alter_column("booking_applications", "version", server_default=None)


def downgrade() -> None:
    """回滚乐观锁列。"""

    op.drop_column("booking_applications", "version")
