"""Shipment Coordinator Agent 的业务运行、事件和命令台账。

总 Agent 不复制装载任务、装载候选方案或 LINK 申请明细。它只保存跨能力编排需要的
稳定引用、当前等待原因和人工命令结果：

* ``ShipmentAgentRun``：一票案件当前走到哪一步，以及选中了哪些真实资源；
* ``ShipmentAgentEvent``：按 sequence 追加的可审计运行轨迹；
* ``ShipmentAgentCommand``：START/RESUME/RECONCILE 的幂等命令台账。

LangGraph checkpoint 只保存 ``run_id``、``run_version`` 和路由游标。即使 checkpoint
文件丢失，也可以根据本模块中的 MySQL 事实和下游服务当前状态重建流程位置。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
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
from app.models.rpa_job import RpaJobRecord
from app.models.shipment_case import ShipmentCase
from app.models.shipment_data_readiness import ShipmentDataReadinessStatus
from app.rpa.contracts import RpaOperation


class ShipmentAgentCapability(StrEnum):
    """总 Agent 当前能够编排的两个真实能力。"""

    LOAD_PLANNING = "LOAD_PLANNING"
    LINK_BOOKING = "LINK_BOOKING"


class ShipmentAgentRunStatus(StrEnum):
    """运行面向页面公开的粗粒度状态。"""

    RUNNING = "RUNNING"
    WAITING_HUMAN = "WAITING_HUMAN"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# 这不是业务状态，也不是新的工作流阶段。它只是一条短期控制面恢复标记：
# 业务事务已经提交后，正常请求会立刻调用通知/唤醒投影并覆盖或清除它；若进程恰好在
# 两者之间崩溃，后台 Worker 才会在宽限期后领取该运行，仅修复控制面投影。
SHIPMENT_AGENT_REPAIR_PROJECTION_WAKE_REASON = "REPAIR_CONTROL_PROJECTION"
# RPA Job 与 Shipment Run 分属两张业务表。Worker 回调只更新 Job，不直接恢复
# LangGraph；它会把所有绑定运行标记为立即可唤醒，再由现有 Wake Worker 读取最新事实。
SHIPMENT_AGENT_RPA_JOB_STATE_CHANGED_WAKE_REASON = "RPA_JOB_STATE_CHANGED"


class ShipmentAgentStage(StrEnum):
    """Shipment Agent 的可审计阶段。

    阶段比 ``status`` 更细：两个运行都可能是 ``WAITING_HUMAN``，但一个在等待案件激活，
    另一个在等待 LINK 提交审批。前端应依据 stage 和结构化 interrupt 展示下一动作，
    不解析事件中文案。
    """

    SYNCING_CASE = "SYNCING_CASE"
    AWAITING_SCENARIO_POLICY = "AWAITING_SCENARIO_POLICY"
    AWAITING_RESOURCE_BINDING = "AWAITING_RESOURCE_BINDING"
    AWAITING_RESOURCE_SELECTION = "AWAITING_RESOURCE_SELECTION"
    ASSESSING_DATA_READINESS = "ASSESSING_DATA_READINESS"
    AWAITING_DATA_REMEDIATION = "AWAITING_DATA_REMEDIATION"
    AWAITING_CASE_ACTIVATION = "AWAITING_CASE_ACTIVATION"
    STARTING_LOAD_AGENT = "STARTING_LOAD_AGENT"
    AWAITING_LOAD_CONSTRAINTS = "AWAITING_LOAD_CONSTRAINTS"
    AWAITING_LOAD_REVIEW = "AWAITING_LOAD_REVIEW"
    AWAITING_LOAD_RELEASE = "AWAITING_LOAD_RELEASE"
    AWAITING_LOAD_REPLAN = "AWAITING_LOAD_REPLAN"
    AWAITING_BOOKING_SUBMIT_APPROVAL = "AWAITING_BOOKING_SUBMIT_APPROVAL"
    SUBMITTING_BOOKING = "SUBMITTING_BOOKING"
    AWAITING_BOOKING_RESULT = "AWAITING_BOOKING_RESULT"
    AWAITING_BOOKING_RETRY_APPROVAL = "AWAITING_BOOKING_RETRY_APPROVAL"
    AWAITING_BOOKING_REPLACEMENT = "AWAITING_BOOKING_REPLACEMENT"
    DISPATCHING_RPA_JOB = "DISPATCHING_RPA_JOB"
    AWAITING_RPA_RESULT = "AWAITING_RPA_RESULT"
    AWAITING_RPA_ATTENTION = "AWAITING_RPA_ATTENTION"
    AWAITING_COMPLETION_APPROVAL = "AWAITING_COMPLETION_APPROVAL"
    COMPLETING_CASE = "COMPLETING_CASE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ShipmentAgentInterruptType(StrEnum):
    """总 Agent 自己拥有的结构化人工中断。

    装载子 Agent 的约束、评审、发布和重规划继续由装载工作台处理。总 Agent 会显示
    ``WAITING_EXTERNAL`` 和子运行摘要，但不会复制一套子 Agent 表单。
    """

    SCENARIO_POLICY_CONFIRMATION = "SCENARIO_POLICY_CONFIRMATION"
    RESOURCE_SELECTION = "RESOURCE_SELECTION"
    DATA_READINESS_REMEDIATION = "DATA_READINESS_REMEDIATION"
    CASE_ACTIVATION_APPROVAL = "CASE_ACTIVATION_APPROVAL"
    BOOKING_SUBMIT_APPROVAL = "BOOKING_SUBMIT_APPROVAL"
    BOOKING_RETRY_APPROVAL = "BOOKING_RETRY_APPROVAL"
    COMPLETION_APPROVAL = "COMPLETION_APPROVAL"


class ShipmentAgentAction(StrEnum):
    """resume 端点接受的结构化人工动作。"""

    CONFIRM = "CONFIRM"
    SELECT = "SELECT"
    SUPPLY_DATA = "SUPPLY_DATA"
    APPROVE = "APPROVE"
    STOP = "STOP"
    RECHECK = "RECHECK"


class ShipmentAgentCommandType(StrEnum):
    """写命令的幂等作用域。"""

    START = "START"
    RESUME = "RESUME"
    RECONCILE = "RECONCILE"


class ShipmentAgentCommandResult(StrEnum):
    """命令是否改变了总 Agent 业务事实。"""

    APPLIED = "APPLIED"
    NO_OP = "NO_OP"


class ShipmentAgentNotificationType(StrEnum):
    """站内业务通知的稳定分类。

    通知不是另一套工作流状态机。它只把运行当前已经形成的业务事实投影成“谁需要关注
    什么”的待办，因此飞书、邮件等外部渠道以后只能消费通知，不能反向决定 Agent 状态。
    """

    ACTION_REQUIRED = "ACTION_REQUIRED"
    RESOURCE_ATTENTION = "RESOURCE_ATTENTION"
    EXTERNAL_WAIT = "EXTERNAL_WAIT"
    RUN_FAILED = "RUN_FAILED"
    AUTO_WAKE_FAILED = "AUTO_WAKE_FAILED"


class ShipmentAgentNotificationSeverity(StrEnum):
    """通知严重度；用于页面排序和将来的渠道升级策略。"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ShipmentAgentNotificationStatus(StrEnum):
    """通知是否仍然需要业务关注。"""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ShipmentAgentRun(Base):
    """一条 Shipment Coordinator Agent 运行。"""

    __tablename__ = "shipment_agent_runs"
    __table_args__ = (
        CheckConstraint(
            "run_version > 0",
            name="ck_shipment_agent_runs_version_positive",
        ),
        CheckConstraint(
            "data_readiness_revision >= 0",
            name="ck_shipment_agent_runs_readiness_revision_non_negative",
        ),
        CheckConstraint(
            "wake_attempt_count >= 0",
            name="ck_shipment_agent_runs_wake_attempt_non_negative",
        ),
        UniqueConstraint(
            "start_idempotency_key_hash",
            name="uq_shipment_agent_runs_start_idempotency_key",
        ),
        Index("ix_shipment_agent_runs_case_id", "case_id"),
        Index("ix_shipment_agent_runs_status", "status"),
        Index(
            "ix_shipment_agent_runs_status_next_wake",
            "status",
            "next_wake_at",
        ),
        Index("ix_shipment_agent_runs_wake_lease_until", "wake_lease_until"),
        Index("ix_shipment_agent_runs_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ShipmentAgentRunStatus] = mapped_column(
        Enum(
            ShipmentAgentRunStatus,
            name="shipment_agent_run_status",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    current_stage: Mapped[ShipmentAgentStage] = mapped_column(
        Enum(
            ShipmentAgentStage,
            name="shipment_agent_stage",
            native_enum=False,
            length=48,
        ),
        nullable=False,
    )
    run_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_case_revision: Mapped[int] = mapped_column(Integer, nullable=False)

    # 未知运输场景必须经过人工确认。已支持的三个场景则由服务端确定能力集合。
    required_capabilities: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    scenario_policy_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # 这里只保存稳定编号/UUID，不复制下游资源业务明细。
    selected_load_task_number: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    selected_load_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    locked_load_version_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    locked_load_version_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    selected_booking_application_number: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    booking_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    carrier_submission_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    booking_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    booking_submission_uncertain: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # interrupt 是页面可直接渲染的结构化等待合同；完整业务事实仍留在源系统。
    pending_interrupt: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    allowed_actions: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    child_agent_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # 案件级完整事实保存在 shipment_case_data_readiness；运行表只保存当前投影，
    # 便于 Agent Center 列表无需逐票展开库存证据。revision=0 表示尚未完成首次评估。
    data_readiness_status: Mapped[ShipmentDataReadinessStatus] = mapped_column(
        Enum(
            ShipmentDataReadinessStatus,
            name="shipment_data_readiness_status",
            native_enum=False,
            length=32,
        ),
        nullable=False,
        default=ShipmentDataReadinessStatus.NOT_EVALUATED,
    )
    data_readiness_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    data_readiness_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    data_readiness_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # 自动唤醒字段属于运行控制面，不改变业务 run_version。后台 Worker 先用条件 UPDATE
    # 领取短租约，再只读检查装载/LINK 权威状态；没有真实变化时不会追加 Trace。
    next_wake_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wake_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wake_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    wake_lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    wake_lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_wake_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wake_last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    start_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
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
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    shipment_case: Mapped[ShipmentCase] = relationship(lazy="selectin")
    events: Mapped[list["ShipmentAgentEvent"]] = relationship(
        back_populates="run",
        # 事件是只追加事实，不能因为 Python 集合变化而被 ORM 物理删除。
        lazy="selectin",
        order_by="ShipmentAgentEvent.sequence",
    )
    notifications: Mapped[list["ShipmentAgentNotification"]] = relationship(
        back_populates="run",
        # 历史通知用于审计“何时提醒过谁”；状态变化只把旧通知标记为 RESOLVED。
        lazy="selectin",
        order_by="ShipmentAgentNotification.created_at",
    )
    rpa_job_bindings: Mapped[list["ShipmentAgentRpaJobBinding"]] = relationship(
        back_populates="run",
        # 绑定属于某次 Shipment Run 的编排事实；删除测试运行时可以随运行一起清理，
        # 但 RPA Job 自身仍由统一控制面独立保存。
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ShipmentAgentRpaJobBinding.sequence",
    )


class ShipmentAgentRpaJobBinding(Base):
    """Shipment Agent 运行与可复用 RPA Job 之间的有序绑定。

    ``originating_run_id`` 只能说明 Job 最初由谁创建，不能表达后续运行复用了同一个
    幂等 Job。专门的绑定表同时保存运行内顺序和业务操作，让 LangGraph 恢复时无需
    依赖事件文案，也不会因为 Job 被复用而丢失当前运行的上下文。
    """

    __tablename__ = "shipment_agent_rpa_job_bindings"
    __table_args__ = (
        CheckConstraint(
            "sequence > 0",
            name="ck_shipment_agent_rpa_bindings_sequence_positive",
        ),
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_shipment_agent_rpa_bindings_run_sequence",
        ),
        UniqueConstraint(
            "run_id",
            "job_id",
            name="uq_shipment_agent_rpa_bindings_run_job",
        ),
        Index("ix_shipment_agent_rpa_job_bindings_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("rpa_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[RpaOperation] = mapped_column(
        Enum(
            RpaOperation,
            name="rpa_operation",
            native_enum=False,
            length=40,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[ShipmentAgentRun] = relationship(
        back_populates="rpa_job_bindings",
    )
    # Job 是控制面的权威快照；绑定只引用它，不复制状态、结果或浏览器证据。
    job: Mapped[RpaJobRecord] = relationship(lazy="selectin")


class ShipmentAgentNotification(Base):
    """由 Agent 运行事实投影出的站内通知。

    ``dedupe_key`` 在同一运行内稳定。例如装载方案连续多次轮询仍停在评审节点时，只更新
    同一条通知，不会制造通知风暴。离开该节点后通知改为 RESOLVED；未来重新进入时再
    打开同一业务通知并增加 occurrence_count。
    """

    __tablename__ = "shipment_agent_notifications"
    __table_args__ = (
        CheckConstraint(
            "occurrence_count > 0",
            name="ck_shipment_agent_notifications_occurrence_positive",
        ),
        UniqueConstraint(
            "run_id",
            "dedupe_key",
            name="uq_shipment_agent_notifications_run_dedupe",
        ),
        Index(
            "ix_shipment_agent_notifications_status_created",
            "status",
            "created_at",
        ),
        Index("ix_shipment_agent_notifications_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_event_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    notification_type: Mapped[ShipmentAgentNotificationType] = mapped_column(
        Enum(
            ShipmentAgentNotificationType,
            name="shipment_agent_notification_type",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    severity: Mapped[ShipmentAgentNotificationSeverity] = mapped_column(
        Enum(
            ShipmentAgentNotificationSeverity,
            name="shipment_agent_notification_severity",
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    status: Mapped[ShipmentAgentNotificationStatus] = mapped_column(
        Enum(
            ShipmentAgentNotificationStatus,
            name="shipment_agent_notification_status",
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    owner_role: Mapped[str] = mapped_column(String(64), nullable=False)
    action_context: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped[ShipmentAgentRun] = relationship(back_populates="notifications")


class ShipmentAgentEvent(Base):
    """总 Agent Trace 中的一条只追加事件。"""

    __tablename__ = "shipment_agent_events"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_shipment_agent_events_run_sequence",
        ),
        Index("ix_shipment_agent_events_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[ShipmentAgentStage] = mapped_column(
        Enum(
            ShipmentAgentStage,
            name="shipment_agent_stage",
            native_enum=False,
            length=48,
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    actor_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_source: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[ShipmentAgentRun] = relationship(back_populates="events")


class ShipmentAgentCommand(Base):
    """成功接收的总 Agent 写命令。

    原始 ``Idempotency-Key`` 不落库，只保存 SHA-256。相同 key、相同请求会直接返回
    当前运行；相同 key 换动作或 payload 会被拒绝。
    """

    __tablename__ = "shipment_agent_commands"
    __table_args__ = (
        CheckConstraint(
            "result_run_version > 0",
            name="ck_shipment_agent_commands_version_positive",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_shipment_agent_commands_idempotency_key",
        ),
        Index("ix_shipment_agent_commands_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    command_type: Mapped[ShipmentAgentCommandType] = mapped_column(
        Enum(
            ShipmentAgentCommandType,
            name="shipment_agent_command_type",
            native_enum=False,
            length=24,
        ),
        nullable=False,
    )
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    result_kind: Mapped[ShipmentAgentCommandResult] = mapped_column(
        Enum(
            ShipmentAgentCommandResult,
            name="shipment_agent_command_result",
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    result_run_version: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
