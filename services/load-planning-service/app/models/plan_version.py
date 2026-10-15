"""正式装载方案版本与追加式审计事件。

``AgentRun.APPROVED`` 只表示候选方案通过了业务评审；它仍属于一次智能体运行。
``LoadPlanVersion.LOCKED`` 则是从已批准运行复制出的独立、不可变执行基线。二者必须分表，
否则后续重规划、算法升级或运行清理很容易改写已经交付现场的方案事实。

本模块没有提供 update/delete 业务入口。正式生产还应让数据库账号只拥有 INSERT/SELECT
权限，或增加数据库触发器，进一步防止绕过应用直接修改锁定版本。
"""

from datetime import datetime
from enum import StrEnum

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
from app.models.agent_run import AgentRun
from app.models.load_planning import LoadPlanningTask


class LoadPlanVersionStatus(StrEnum):
    """正式方案版本当前支持的唯一生命周期状态。"""

    LOCKED = "LOCKED"


class LoadPlanVersion(Base):
    """由一条已批准运行固化出的正式装载方案版本。"""

    __tablename__ = "load_plan_versions"
    __table_args__ = (
        CheckConstraint(
            "version_number > 0",
            name="ck_load_plan_versions_version_positive",
        ),
        CheckConstraint(
            "source_run_version > 0",
            name="ck_load_plan_versions_run_version_positive",
        ),
        CheckConstraint(
            "supersedes_version_id IS NULL OR supersedes_version_id <> id",
            name="ck_load_plan_versions_not_self_superseding",
        ),
        UniqueConstraint(
            "task_id",
            "version_number",
            name="uq_load_plan_versions_task_version",
        ),
        # 一条批准运行最多形成一个正式版本；这是锁定请求幂等性的数据库最终防线。
        UniqueConstraint(
            "source_run_id",
            name="uq_load_plan_versions_source_run",
        ),
        # 除首版的 NULL 外，每个正式版本最多只能被一个后继版本替代，禁止形成分叉链。
        UniqueConstraint(
            "supersedes_version_id",
            name="uq_load_plan_versions_supersedes",
        ),
        Index("ix_load_plan_versions_task_id", "task_id"),
        Index("ix_load_plan_versions_source_run_id", "source_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("load_planning_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[LoadPlanVersionStatus] = mapped_column(
        Enum(
            LoadPlanVersionStatus,
            name="load_plan_version_status",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    source_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_pallet_code: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("load_plan_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_run_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)

    # 三份快照各自带规范 JSON 的 SHA-256。读取方既能解释内容，也能检查内容是否被改写。
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    constraint_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    constraint_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    plan_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)

    # 当前项目还没有真实认证上下文，因此审批人与锁定人均使用明确的演示占位身份。
    # 接入 SSO/JWT 后，应在 Service 边界从可信认证上下文填充，绝不能改为读取请求体字段。
    approval_actor_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_actor_display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_actor_source: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_by_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    locked_by_display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    locked_by_source: Mapped[str] = mapped_column(String(64), nullable=False)
    lock_reason: Mapped[str] = mapped_column(Text, nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    task: Mapped[LoadPlanningTask] = relationship(lazy="selectin")
    source_run: Mapped[AgentRun] = relationship(lazy="raise")
    events: Mapped[list["LoadPlanVersionEvent"]] = relationship(
        back_populates="plan_version",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="LoadPlanVersionEvent.sequence",
    )


class LoadPlanVersionEvent(Base):
    """正式版本生命周期中的只追加事件。"""

    __tablename__ = "load_plan_version_events"
    __table_args__ = (
        UniqueConstraint(
            "plan_version_id",
            "sequence",
            name="uq_load_plan_version_events_sequence",
        ),
        Index("ix_load_plan_version_events_version_id", "plan_version_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_version_id: Mapped[str] = mapped_column(
        ForeignKey("load_plan_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_source: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    plan_version: Mapped[LoadPlanVersion] = relationship(back_populates="events")
