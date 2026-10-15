"""create load planning task and master data tables

Revision ID: 20261009_01
Revises:
Create Date: 2026-10-09 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_01"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

task_status = sa.Enum(
    "DRAFT",
    name="load_planning_task_status",
    native_enum=False,
    length=32,
)
source_type = sa.Enum(
    "MANUAL",
    "ENTERPRISE_HISTORICAL",
    "DOCUMENT_IMPORT",
    "SYNTHETIC_DEMO",
    name="load_planning_source_type",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    """创建装载任务、货物箱规和托盘规格三张表。"""

    op.create_table(
        "load_planning_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_number", sa.String(length=64), nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", task_status, nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_number"),
    )
    op.create_index(
        "ix_load_planning_tasks_task_number",
        "load_planning_tasks",
        ["task_number"],
        unique=False,
    )

    op.create_table(
        "cargo_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("sku_code", sa.String(length=128), nullable=True),
        sa.Column("cargo_name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("length_mm", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("width_mm", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("height_mm", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("gross_weight_kg", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("max_stack_layers", sa.Integer(), nullable=True),
        sa.Column("max_top_load_kg", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("stackable", sa.Boolean(), nullable=True),
        sa.Column("keep_upright", sa.Boolean(), nullable=True),
        sa.Column("fragile", sa.Boolean(), nullable=True),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("source_locator", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["load_planning_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_cargo_items_quantity_positive"),
        sa.CheckConstraint("length_mm > 0", name="ck_cargo_items_length_positive"),
        sa.CheckConstraint("width_mm > 0", name="ck_cargo_items_width_positive"),
        sa.CheckConstraint("height_mm > 0", name="ck_cargo_items_height_positive"),
        sa.CheckConstraint(
            "gross_weight_kg > 0",
            name="ck_cargo_items_gross_weight_positive",
        ),
        sa.CheckConstraint(
            "max_stack_layers IS NULL OR max_stack_layers > 0",
            name="ck_cargo_items_stack_layers_positive",
        ),
        sa.CheckConstraint(
            "max_top_load_kg IS NULL OR max_top_load_kg > 0",
            name="ck_cargo_items_top_load_positive",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_cargo_items_confidence_range",
        ),
        sa.CheckConstraint(
            "source_type <> 'SYNTHETIC_DEMO' OR verified = 0",
            name="ck_cargo_items_demo_not_verified",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "item_code",
            name="uq_cargo_items_task_item_code",
        ),
    )
    op.create_index("ix_cargo_items_task_id", "cargo_items", ["task_id"], unique=False)

    op.create_table(
        "pallet_specs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("pallet_code", sa.String(length=64), nullable=False),
        sa.Column("pallet_name", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("length_mm", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("width_mm", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("height_mm", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("tare_weight_kg", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("max_load_kg", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column(
            "max_total_height_mm",
            sa.Numeric(precision=12, scale=3),
            nullable=True,
        ),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("source_locator", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["load_planning_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_pallet_specs_quantity_positive"),
        sa.CheckConstraint("length_mm > 0", name="ck_pallet_specs_length_positive"),
        sa.CheckConstraint("width_mm > 0", name="ck_pallet_specs_width_positive"),
        sa.CheckConstraint("height_mm > 0", name="ck_pallet_specs_height_positive"),
        sa.CheckConstraint(
            "tare_weight_kg > 0",
            name="ck_pallet_specs_tare_weight_positive",
        ),
        sa.CheckConstraint(
            "max_load_kg IS NULL OR max_load_kg > 0",
            name="ck_pallet_specs_max_load_positive",
        ),
        sa.CheckConstraint(
            "max_total_height_mm IS NULL OR max_total_height_mm > 0",
            name="ck_pallet_specs_total_height_positive",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_pallet_specs_confidence_range",
        ),
        sa.CheckConstraint(
            "source_type <> 'SYNTHETIC_DEMO' OR verified = 0",
            name="ck_pallet_specs_demo_not_verified",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "pallet_code",
            name="uq_pallet_specs_task_pallet_code",
        ),
    )
    op.create_index("ix_pallet_specs_task_id", "pallet_specs", ["task_id"], unique=False)


def downgrade() -> None:
    """按外键依赖逆序删除三张表。"""

    op.drop_index("ix_pallet_specs_task_id", table_name="pallet_specs")
    op.drop_table("pallet_specs")
    op.drop_index("ix_cargo_items_task_id", table_name="cargo_items")
    op.drop_table("cargo_items")
    op.drop_index(
        "ix_load_planning_tasks_task_number",
        table_name="load_planning_tasks",
    )
    op.drop_table("load_planning_tasks")
