"""backfill shipment agent control projection repairs

Revision ID: 20261016_04
Revises: 20261016_03
Create Date: 2026-10-16 20:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261016_04"
down_revision: str | None = "20261016_03"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_REPAIR_REASON = "REPAIR_CONTROL_PROJECTION"


def upgrade() -> None:
    """安排一次历史控制面投影修复，但不覆盖任何既有唤醒计划。

    ``20261016_03`` 创建通知表时，没有把升级前已存在的人工待办、失败和等待运行投影
    成通知。新代码会在每次业务事务中写修复标记，但历史行不会再自动经历一次业务
    事务，因此这里给“完全没有唤醒计划且没有租约”的非 RUNNING 行安排一次立即修复。

    两个字段都必须为 NULL 才会回填：任何已经具有 ``next_wake_at`` 或 ``wake_reason``
    的行都由原计划负责，迁移绝不猜测或覆盖。活跃/残留租约同样保持原样。
    """

    op.execute(
        sa.text(
            """
            UPDATE shipment_agent_runs
            SET
                next_wake_at = CURRENT_TIMESTAMP,
                wake_reason = :repair_reason
            WHERE status IN (
                'WAITING_HUMAN',
                'WAITING_RESOURCE',
                'WAITING_EXTERNAL',
                'COMPLETED',
                'FAILED',
                'CANCELLED'
            )
              AND next_wake_at IS NULL
              AND wake_reason IS NULL
              AND wake_lease_token IS NULL
              AND wake_lease_until IS NULL
            """
        ).bindparams(repair_reason=_REPAIR_REASON)
    )


def downgrade() -> None:
    """移除仍未被 Worker 消费的本迁移修复标记。"""

    op.execute(
        sa.text(
            """
            UPDATE shipment_agent_runs
            SET
                next_wake_at = NULL,
                wake_reason = NULL
            WHERE wake_reason = :repair_reason
            """
        ).bindparams(repair_reason=_REPAIR_REASON)
    )
