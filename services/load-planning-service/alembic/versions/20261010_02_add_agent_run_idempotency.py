"""add idempotency key to agent runs

Revision ID: 20261010_02
Revises: 20261010_01
Create Date: 2026-10-10 17:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261010_02"
down_revision: str | None = "20261010_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """为创建运行请求增加数据库级幂等约束。"""

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("idempotency_request_hash", sa.String(length=64), nullable=True))
        # 组合唯一约束把作用域限定在 task_id 内；多个旧客户端的 NULL key 不会互相冲突。
        batch.create_unique_constraint(
            "uq_agent_runs_task_idempotency_key",
            ["task_id", "idempotency_key"],
        )


def downgrade() -> None:
    """移除创建幂等字段；不会删除运行记录本身。"""

    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("uq_agent_runs_task_idempotency_key", type_="unique")
        batch.drop_column("idempotency_request_hash")
        batch.drop_column("idempotency_key")
