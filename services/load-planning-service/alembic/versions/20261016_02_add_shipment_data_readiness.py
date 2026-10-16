"""add shipment case data readiness gate

Revision ID: 20261016_02
Revises: 20261016_01
Create Date: 2026-10-16 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261016_02"
down_revision: str | None = "20261016_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

shipment_data_readiness_status = sa.Enum(
    "LEGACY_BYPASSED",
    "NOT_EVALUATED",
    "ACTION_REQUIRED",
    "READY",
    "DEMO_READY",
    name="shipment_data_readiness_status",
    native_enum=False,
    length=32,
)
shipment_data_source_mode = sa.Enum(
    "NOT_APPLICABLE",
    "OPERATIONAL",
    "DEMO",
    name="shipment_data_source_mode",
    native_enum=False,
    length=24,
)


def upgrade() -> None:
    """新增案件级事实表，并为运行保存轻量就绪度投影。

    升级前已经存在的运行统一标记为 ``LEGACY_BYPASSED``。它们继续按 V1 语义完成，
    不会因为部署新代码突然倒退到补数节点；新运行由应用显式写 ``NOT_EVALUATED``。
    """

    op.create_table(
        "shipment_case_data_readiness",
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", shipment_data_readiness_status, nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("source_mode", shipment_data_source_mode, nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("assessed_at", sa.DateTime(), nullable=False),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
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
            name="ck_shipment_case_data_readiness_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["shipment_cases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )

    # snapshot 对迁移前运行允许为 NULL；应用序列化时会构造只读空投影。
    with op.batch_alter_table("shipment_agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "data_readiness_status",
                shipment_data_readiness_status,
                server_default="LEGACY_BYPASSED",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "data_readiness_revision",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("data_readiness_snapshot", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "data_readiness_checked_at",
                sa.DateTime(),
                nullable=True,
            )
        )
        batch_op.create_check_constraint(
            "ck_shipment_agent_runs_readiness_revision_non_negative",
            "data_readiness_revision >= 0",
        )


def downgrade() -> None:
    """删除 V2 投影和案件级事实表。"""

    with op.batch_alter_table("shipment_agent_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_shipment_agent_runs_readiness_revision_non_negative",
            type_="check",
        )
        batch_op.drop_column("data_readiness_checked_at")
        batch_op.drop_column("data_readiness_snapshot")
        batch_op.drop_column("data_readiness_revision")
        batch_op.drop_column("data_readiness_status")

    op.drop_table("shipment_case_data_readiness")
