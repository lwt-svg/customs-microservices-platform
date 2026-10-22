"""统一 RPA 控制面的持久化模型。

领域契约位于 :mod:`app.rpa.contracts`，本模块只负责把同一份契约可靠地保存到数据库：

* ``RpaJobRecord`` 保存当前快照、Worker 租约和最后一次结果；
* ``RpaJobEvent`` 只追加状态变化与关键进度，供页面和故障排查使用。

这里把 ``attempt_token`` 与随机 ``job_id`` 分开。``job_id`` 是一票业务任务的稳定身份，
而 ``attempt_token`` 只属于某一次 Worker 领取；旧 Worker 即使晚到，也不能覆盖新尝试。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.rpa.contracts import (
    RpaBusinessOutcome,
    RpaDomain,
    RpaEffectPhase,
    RpaEnvironment,
    RpaJobStatus,
    RpaOperation,
    RpaSideEffectBoundary,
)


class RpaJobRecord(Base):
    """一条可由 LINK 或单一窗口 Worker 领取的统一 RPA Job。"""

    __tablename__ = "rpa_jobs"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_rpa_jobs_revision_positive"),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_rpa_jobs_attempt_count_non_negative",
        ),
        CheckConstraint("max_attempts > 0", name="ck_rpa_jobs_max_attempts_positive"),
        UniqueConstraint("idempotency_key", name="uq_rpa_jobs_idempotency_key"),
        Index("ix_rpa_jobs_status_created", "status", "created_at"),
        Index("ix_rpa_jobs_domain_status_created", "domain", "status", "created_at"),
        Index("ix_rpa_jobs_business_reference", "business_reference"),
        Index("ix_rpa_jobs_case_number", "case_number"),
        Index("ix_rpa_jobs_originating_run_id", "originating_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    case_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    originating_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("shipment_agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    business_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    business_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    domain: Mapped[RpaDomain] = mapped_column(
        Enum(RpaDomain, name="rpa_domain", native_enum=False, length=32),
        nullable=False,
    )
    operation: Mapped[RpaOperation] = mapped_column(
        Enum(RpaOperation, name="rpa_operation", native_enum=False, length=40),
        nullable=False,
    )
    environment: Mapped[RpaEnvironment] = mapped_column(
        Enum(
            RpaEnvironment,
            name="rpa_environment",
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    side_effect_boundary: Mapped[RpaSideEffectBoundary] = mapped_column(
        Enum(
            RpaSideEffectBoundary,
            name="rpa_side_effect_boundary",
            native_enum=False,
            length=24,
        ),
        nullable=False,
    )
    effect_phase: Mapped[RpaEffectPhase] = mapped_column(
        Enum(
            RpaEffectPhase,
            name="rpa_effect_phase",
            native_enum=False,
            length=40,
        ),
        nullable=False,
        default=RpaEffectPhase.READ_ONLY,
    )

    # Adapter/Profile 描述“谁来执行、按哪套规则执行”；XPath/CSS 不进入业务载荷。
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_profile: Mapped[str | None] = mapped_column(String(96), nullable=True)
    flow_profile: Mapped[str | None] = mapped_column(String(96), nullable=True)
    selector_pack_version: Mapped[str] = mapped_column(String(96), nullable=False)
    payload_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[RpaJobStatus] = mapped_column(
        Enum(RpaJobStatus, name="rpa_job_status", native_enum=False, length=32),
        nullable=False,
    )
    current_step: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # 当前/最后一次领取凭据。终态后保留 token，允许 Worker 因响应丢失而幂等重放同一结果。
    attempt_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    page_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)

    business_outcome: Mapped[RpaBusinessOutcome | None] = mapped_column(
        Enum(
            RpaBusinessOutcome,
            name="rpa_business_outcome",
            native_enum=False,
            length=32,
        ),
        nullable=True,
    )
    external_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    events: Mapped[list["RpaJobEvent"]] = relationship(
        back_populates="job",
        lazy="selectin",
        order_by="RpaJobEvent.sequence",
    )
    callback_receipts: Mapped[list["RpaJobCallbackReceipt"]] = relationship(
        back_populates="job",
        lazy="selectin",
        order_by="RpaJobCallbackReceipt.received_at",
    )


class RpaJobEvent(Base):
    """RPA Job 的只追加审计事件。"""

    __tablename__ = "rpa_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_rpa_job_events_sequence"),
        Index("ix_rpa_job_events_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("rpa_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[RpaJobStatus | None] = mapped_column(
        Enum(RpaJobStatus, name="rpa_job_status", native_enum=False, length=32),
        nullable=True,
    )
    current_status: Mapped[RpaJobStatus] = mapped_column(
        Enum(RpaJobStatus, name="rpa_job_status", native_enum=False, length=32),
        nullable=False,
    )
    current_step: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    job: Mapped[RpaJobRecord] = relationship(back_populates="events")


class RpaJobCallbackReceipt(Base):
    """Worker 结果回调收据。

    Worker 在网络超时后必须重发同一个 ``callback_id``。相同 ID、相同 payload 是幂等
    重放；相同 ID 换内容则是协议冲突，不能靠当前 Job 状态猜测。
    """

    __tablename__ = "rpa_job_callback_receipts"
    __table_args__ = (
        CheckConstraint(
            "occurrence_count > 0",
            name="ck_rpa_job_callback_receipts_occurrence_positive",
        ),
        Index("ix_rpa_job_callback_receipts_job_id", "job_id"),
    )

    callback_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("rpa_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_status: Mapped[RpaJobStatus] = mapped_column(
        Enum(RpaJobStatus, name="rpa_job_status", native_enum=False, length=32),
        nullable=False,
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    received_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    job: Mapped[RpaJobRecord] = relationship(back_populates="callback_receipts")
