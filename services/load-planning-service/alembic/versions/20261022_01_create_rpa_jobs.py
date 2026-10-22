"""create unified RPA job control plane

Revision ID: 20261022_01
Revises: 20261016_04
Create Date: 2026-10-22 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261022_01"
down_revision: str | None = "20261016_04"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

rpa_domain = sa.Enum(
    "LINK",
    "SINGLE_WINDOW",
    name="rpa_domain",
    native_enum=False,
    length=32,
)
rpa_operation = sa.Enum(
    "LINK_BOOKING_STAGE",
    "COO_STAGE",
    "CIQ_STAGE",
    name="rpa_operation",
    native_enum=False,
    length=40,
)
rpa_environment = sa.Enum(
    "SANDBOX",
    "PRODUCTION",
    name="rpa_environment",
    native_enum=False,
    length=16,
)
rpa_side_effect_boundary = sa.Enum(
    "READ_ONLY",
    "DRAFT_ONLY",
    "FINAL_SUBMISSION",
    name="rpa_side_effect_boundary",
    native_enum=False,
    length=24,
)
rpa_effect_phase = sa.Enum(
    "READ_ONLY",
    "LOCAL_PREPARED",
    "EXTERNAL_MUTATION_STARTED",
    "EXTERNAL_EFFECT_CONFIRMED",
    name="rpa_effect_phase",
    native_enum=False,
    length=40,
)
rpa_job_status = sa.Enum(
    "CREATED",
    "QUEUED",
    "RUNNING",
    "WAITING_HUMAN",
    "SUCCEEDED",
    "RETRYABLE_FAILED",
    "FAILED",
    "RESULT_UNKNOWN",
    "SITE_VERSION_UNKNOWN",
    "CANCELLED",
    name="rpa_job_status",
    native_enum=False,
    length=32,
)
rpa_business_outcome = sa.Enum(
    "LINK_DRAFT_SAVED",
    "COO_STAGED",
    "CIQ_STAGED",
    name="rpa_business_outcome",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    """创建 RPA 当前快照、只追加事件和回调幂等收据三张表。

    业务载荷保存在控制面数据库中，仅在 Worker 成功领取后返回；普通页面查询永远不会
    返回载荷、幂等键或 attempt token。``originating_run_id`` 只是来源追踪，后续
    Shipment LangGraph 会通过专门绑定表关联可复用 Job。
    """

    op.create_table(
        "rpa_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("case_number", sa.String(length=64), nullable=True),
        sa.Column("originating_run_id", sa.String(length=36), nullable=True),
        sa.Column("business_reference", sa.String(length=128), nullable=False),
        sa.Column("business_payload", sa.JSON(), nullable=False),
        sa.Column("business_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("domain", rpa_domain, nullable=False),
        sa.Column("operation", rpa_operation, nullable=False),
        sa.Column("environment", rpa_environment, nullable=False),
        sa.Column(
            "side_effect_boundary",
            rpa_side_effect_boundary,
            nullable=False,
        ),
        sa.Column(
            "effect_phase",
            rpa_effect_phase,
            nullable=False,
        ),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("source_profile", sa.String(length=96), nullable=True),
        sa.Column("flow_profile", sa.String(length=96), nullable=True),
        sa.Column(
            "selector_pack_version",
            sa.String(length=96),
            nullable=False,
        ),
        sa.Column(
            "payload_schema_version",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("status", rpa_job_status, nullable=False),
        sa.Column("current_step", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_token", sa.String(length=36), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("page_fingerprint", sa.String(length=128), nullable=True),
        sa.Column(
            "business_outcome",
            rpa_business_outcome,
            nullable=True,
        ),
        sa.Column("external_reference", sa.String(length=256), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
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
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_rpa_jobs_revision_positive",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_rpa_jobs_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_rpa_jobs_max_attempts_positive",
        ),
        sa.ForeignKeyConstraint(
            ["originating_run_id"],
            ["shipment_agent_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_rpa_jobs_idempotency_key",
        ),
    )
    op.create_index(
        "ix_rpa_jobs_status_created",
        "rpa_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_rpa_jobs_domain_status_created",
        "rpa_jobs",
        ["domain", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_rpa_jobs_business_reference",
        "rpa_jobs",
        ["business_reference"],
        unique=False,
    )
    op.create_index(
        "ix_rpa_jobs_case_number",
        "rpa_jobs",
        ["case_number"],
        unique=False,
    )
    op.create_index(
        "ix_rpa_jobs_originating_run_id",
        "rpa_jobs",
        ["originating_run_id"],
        unique=False,
    )

    op.create_table(
        "rpa_job_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("previous_status", rpa_job_status, nullable=True),
        sa.Column("current_status", rpa_job_status, nullable=False),
        sa.Column("current_step", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "sequence",
            name="uq_rpa_job_events_sequence",
        ),
    )
    op.create_index(
        "ix_rpa_job_events_job_id",
        "rpa_job_events",
        ["job_id"],
        unique=False,
    )

    op.create_table(
        "rpa_job_callback_receipts",
        sa.Column("callback_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("result_status", rpa_job_status, nullable=False),
        sa.Column(
            "occurrence_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "occurrence_count > 0",
            name="ck_rpa_job_callback_receipts_occurrence_positive",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("callback_id"),
    )
    op.create_index(
        "ix_rpa_job_callback_receipts_job_id",
        "rpa_job_callback_receipts",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    """按外键依赖逆序移除统一 RPA 控制面。"""

    op.drop_index(
        "ix_rpa_job_callback_receipts_job_id",
        table_name="rpa_job_callback_receipts",
    )
    op.drop_table("rpa_job_callback_receipts")
    op.drop_index(
        "ix_rpa_job_events_job_id",
        table_name="rpa_job_events",
    )
    op.drop_table("rpa_job_events")
    op.drop_index(
        "ix_rpa_jobs_originating_run_id",
        table_name="rpa_jobs",
    )
    op.drop_index("ix_rpa_jobs_case_number", table_name="rpa_jobs")
    op.drop_index(
        "ix_rpa_jobs_business_reference",
        table_name="rpa_jobs",
    )
    op.drop_index(
        "ix_rpa_jobs_domain_status_created",
        table_name="rpa_jobs",
    )
    op.drop_index("ix_rpa_jobs_status_created", table_name="rpa_jobs")
    op.drop_table("rpa_jobs")
