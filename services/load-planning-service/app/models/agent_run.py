"""状态化装托智能体的持久化模型。

这里的“智能体”不是聊天记录，而是一条可恢复、可审计的业务状态机。一次运行会保存：

1. 启动时的任务输入快照哈希，证明本次计算针对哪一版输入；
2. 人工补充的约束答案和当前状态；
3. 每一次状态迁移事件；
4. 由确定性算法生成的候选方案。

托盘与箱子坐标数量可能很大，因此方案明细使用 JSON 保存；运行和事件仍使用独立关系表，
这样可以按运行查询、审计顺序并实施外键约束。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
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


class AgentRunStatus(StrEnum):
    """对调用方稳定公开的运行状态。"""

    AWAITING_INPUT = "AWAITING_INPUT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class AgentRun(Base):
    """一次装托智能体运行。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_task_id", "task_id"),
        Index("ix_agent_runs_status", "status"),
        Index("ix_agent_runs_parent_run_id", "parent_run_id"),
        # 幂等键只在同一任务内唯一；不同任务可以使用相同的客户端请求编号。
        # NULL 表示旧客户端没有发送 Idempotency-Key，MySQL/SQLite 都允许多条 NULL。
        UniqueConstraint(
            "task_id",
            "idempotency_key",
            name="uq_agent_runs_task_idempotency_key",
        ),
        # 重新规划的幂等作用域是“同一父运行”。保存请求头的 SHA-256，而不是原始值，
        # 可避免 MySQL 默认大小写不敏感排序规则与 SQLite 比较语义不一致。
        UniqueConstraint(
            "parent_run_id",
            "replan_idempotency_key_hash",
            name="uq_agent_runs_parent_replan_idempotency_key",
        ),
    )

    # UUID 由应用生成；相比自增 ID，更适合前端把运行编号保存在路由中。
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("load_planning_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(
            AgentRunStatus,
            name="load_planning_agent_run_status",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    current_step: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_pallet_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 保存快照本体可让后续审计解释哈希，也避免只剩一个无法复原的摘要值。
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    answers: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 创建请求的幂等键和规范化请求指纹。它们只用于防止网络重试重复创建运行，
    # 不属于装载任务事实，也不能被人工答案覆盖。
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 每次成功状态迁移递增。前端可在请求中回传它，避免两个浏览器窗口
    # 同时修改同一条运行时后写入覆盖先写入的答案或审批。
    run_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 驳回后重新规划会创建一条新运行；父子关系让审计人员能沿着重规划链追溯。
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    replan_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    replan_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    events: Mapped[list["AgentRunEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AgentRunEvent.sequence",
    )
    plans: Mapped[list["PackingPlan"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PackingPlan.id",
    )


class AgentRunEvent(Base):
    """运行轨迹中的一条只追加事件。"""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_events_run_sequence"),
        Index("ix_agent_run_events_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    run: Mapped[AgentRun] = relationship(back_populates="events")


class PackingPlan(Base):
    """一套候选方案及其完整算法输出。"""

    __tablename__ = "packing_plans"
    __table_args__ = (
        UniqueConstraint("run_id", "plan_code", name="uq_packing_plans_run_plan_code"),
        Index("ix_packing_plans_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_code: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 方案必须记录“由哪一版算法和评分公式生成”，否则未来算法升级后，
    # 历史审批结果无法复现和解释。
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feasible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    pallets: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    run: Mapped[AgentRun] = relationship(back_populates="plans")
