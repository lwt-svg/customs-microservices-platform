"""create shipment agent to RPA job bindings

Revision ID: 20261022_02
Revises: 20261022_01
Create Date: 2026-10-22 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261022_02"
down_revision: str | None = "20261022_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

rpa_operation = sa.Enum(
    "LINK_BOOKING_STAGE",
    "COO_STAGE",
    "CIQ_STAGE",
    name="rpa_operation",
    native_enum=False,
    length=40,
)


def upgrade() -> None:
    """创建 Shipment Run 与统一 RPA Job 的有序多对多绑定。

    Shipment Agent 的三个新阶段保存在既有 ``VARCHAR(48)`` 列中，因此不需要修改
    ``shipment_agent_runs`` 或事件表。这里只新增独立绑定表，避免把可复用 Job 的
    来源字段误当成当前运行关系。
    """

    op.create_table(
        "shipment_agent_rpa_job_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("operation", rpa_operation, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["shipment_agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_shipment_agent_rpa_bindings_sequence_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_shipment_agent_rpa_bindings_run_sequence",
        ),
        sa.UniqueConstraint(
            "run_id",
            "job_id",
            name="uq_shipment_agent_rpa_bindings_run_job",
        ),
    )
    op.create_index(
        "ix_shipment_agent_rpa_job_bindings_job_id",
        "shipment_agent_rpa_job_bindings",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    """先移除索引，再删除绑定表。"""

    op.drop_index(
        "ix_shipment_agent_rpa_job_bindings_job_id",
        table_name="shipment_agent_rpa_job_bindings",
    )
    op.drop_table("shipment_agent_rpa_job_bindings")
