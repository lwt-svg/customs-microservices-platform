"""add idempotency metadata for agent replan requests

Revision ID: 20261012_02
Revises: 20261012_01
Create Date: 2026-10-12 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261012_02"
down_revision: str | None = "20261012_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """让同一父运行的重新规划请求可以安全重放。"""

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(
            sa.Column(
                "replan_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "replan_request_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.create_unique_constraint(
            "uq_agent_runs_parent_replan_idempotency_key",
            ["parent_run_id", "replan_idempotency_key_hash"],
        )


def downgrade() -> None:
    """移除重新规划幂等字段，不删除任何智能体运行。"""

    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint(
            "uq_agent_runs_parent_replan_idempotency_key",
            type_="unique",
        )
        batch.drop_column("replan_request_hash")
        batch.drop_column("replan_idempotency_key_hash")
