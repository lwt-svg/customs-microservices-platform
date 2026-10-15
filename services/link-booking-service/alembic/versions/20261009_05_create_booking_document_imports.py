"""create booking document imports

Revision ID: 20261009_05
Revises: 20261009_04
Create Date: 2026-10-09 10:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_05"
down_revision: str | None = "20261009_04"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """创建 PDF 导入记录，并把申请与来源文档建立可选一对一关系。"""

    document_status = sa.Enum(
        "PARSED",
        "CONFIRMED",
        name="booking_document_import_status",
    )
    document_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "booking_document_imports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", document_status, nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("candidate_payload", sa.JSON(), nullable=False),
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
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_booking_document_imports_sha256",
        "booking_document_imports",
        ["sha256"],
    )
    op.add_column(
        "booking_applications",
        sa.Column("source_document_import_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_booking_applications_source_document_import_id",
        "booking_applications",
        ["source_document_import_id"],
    )
    op.create_foreign_key(
        "fk_booking_applications_source_document_import_id",
        "booking_applications",
        "booking_document_imports",
        ["source_document_import_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """回滚来源关系和 PDF 导入表。"""

    op.drop_constraint(
        "fk_booking_applications_source_document_import_id",
        "booking_applications",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_booking_applications_source_document_import_id",
        "booking_applications",
        type_="unique",
    )
    op.drop_column("booking_applications", "source_document_import_id")
    op.drop_index(
        "ix_booking_document_imports_sha256",
        table_name="booking_document_imports",
    )
    op.drop_table("booking_document_imports")
    sa.Enum(name="booking_document_import_status").drop(op.get_bind(), checkfirst=True)
