"""发运案件、资源绑定、命令台账和追加式审计事件的持久化模型。

``ShipmentCase`` 是跨装载规划与 LINK 订舱能力的业务关联骨架。它只保存稳定的业务
编号，不复制装载任务或订舱申请的明细，也不建立跨服务数据库外键。这样既能形成一票
发运的统一时间线，又不会破坏各服务独立拥有业务事实的边界。

案件上的 ``revision`` 用于乐观并发控制；绑定表记录资源在绑定时经过了什么方式验证；
命令台账保存每一个已经成功返回的幂等命令，包括没有改变业务事实的 ``NO_OP``；事件表
只追加真正发生的业务事实。命令结果与业务事件分开后，重复绑定不会为了登记幂等键而
伪造第二条 ``RESOURCE_BOUND`` 事件。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
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


class ShipmentCaseStatus(StrEnum):
    """发运案件的最小生命周期。

    子任务等待、失败或审批等细节属于装载任务、订舱申请和案件事件，不继续膨胀案件
    主状态。``COMPLETED`` 与 ``CANCELLED`` 是终态，具体流转约束由 Service 层执行。
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ShipmentTransportScenario(StrEnum):
    """第一阶段支持的运输与能力使用场景。"""

    # 单证冲突等案件可以先进入 DRAFT，再由人工或后续 Agent 确认真实运输场景。
    UNDECIDED = "UNDECIDED"
    SEA_FCL = "SEA_FCL"
    SEA_LCL = "SEA_LCL"
    AIR = "AIR"
    EXPRESS = "EXPRESS"
    LOAD_PLANNING_ONLY = "LOAD_PLANNING_ONLY"
    BOOKING_ONLY = "BOOKING_ONLY"


class ShipmentResourceType(StrEnum):
    """案件当前可以关联的下游业务资源类型。"""

    LOAD_PLANNING_TASK = "LOAD_PLANNING_TASK"
    LINK_BOOKING_APPLICATION = "LINK_BOOKING_APPLICATION"


class ShipmentCaseCommandType(StrEnum):
    """Shipment Case 当前支持的三类写命令。"""

    CREATE_CASE = "CREATE_CASE"
    BIND_RESOURCE = "BIND_RESOURCE"
    CHANGE_STATUS = "CHANGE_STATUS"


class ShipmentCaseCommandResult(StrEnum):
    """成功命令对案件业务事实产生的结果。"""

    # APPLIED 表示命令真正改变了案件，并且对应一条业务事件。
    APPLIED = "APPLIED"
    # NO_OP 表示命令成功，但目标事实原本已经成立，因此不推进 revision、不追加事件。
    NO_OP = "NO_OP"


class ShipmentCase(Base):
    """一票跨服务发运案件。

    UUID 主键只用于数据库关系；``case_number`` 才是页面、日志和跨服务交互使用的稳定
    业务编号。幂等键与请求指纹统一存放在 ``ShipmentCaseCommand``，主表不承担命令
    去重职责。
    """

    __tablename__ = "shipment_cases"
    __table_args__ = (
        CheckConstraint(
            "revision > 0",
            name="ck_shipment_cases_revision_positive",
        ),
        UniqueConstraint(
            "case_number",
            name="uq_shipment_cases_case_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_number: Mapped[str] = mapped_column(String(64), nullable=False)
    case_name: Mapped[str] = mapped_column(String(128), nullable=False)
    responsible_organization_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    transport_scenario: Mapped[ShipmentTransportScenario] = mapped_column(
        Enum(
            ShipmentTransportScenario,
            name="shipment_transport_scenario",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    status: Mapped[ShipmentCaseStatus] = mapped_column(
        Enum(
            ShipmentCaseStatus,
            name="shipment_case_status",
            native_enum=False,
            length=32,
        ),
        nullable=False,
        default=ShipmentCaseStatus.DRAFT,
    )
    # 只有改变案件事实的成功命令才递增；NO_OP 只进入命令台账。
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    # selectin 可在异步 API 序列化前批量取回两个集合，避免触发隐式异步懒加载。
    resource_bindings: Mapped[list["ShipmentCaseResourceBinding"]] = relationship(
        back_populates="shipment_case",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ShipmentCaseResourceBinding.id",
    )
    events: Mapped[list["ShipmentCaseEvent"]] = relationship(
        back_populates="shipment_case",
        # 事件是只追加事实，不能因为从 Python 集合中移除就被 ORM 当成 orphan 删除。
        # 新事件仍会通过默认的 save-update cascade 随案件一起持久化。
        lazy="selectin",
        order_by="ShipmentCaseEvent.sequence",
    )


class ShipmentCaseResourceBinding(Base):
    """案件与外部业务资源之间经过验证的稳定引用。

    ``resource_number`` 分别保存装载任务编号或 LINK 申请编号。这里故意没有指向对应表的
    外键：装载能力即使当前与案件共用一个 Python 进程，领域边界仍不应依赖物理部署；
    LINK 数据更位于独立服务和独立数据库中。
    """

    __tablename__ = "shipment_case_resource_bindings"
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_number",
            name="uq_shipment_case_bindings_resource",
        ),
        Index("ix_shipment_case_bindings_case_id", "case_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_type: Mapped[ShipmentResourceType] = mapped_column(
        Enum(
            ShipmentResourceType,
            name="shipment_resource_type",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    resource_number: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_status_at_verification: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    verification_source: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    shipment_case: Mapped[ShipmentCase] = relationship(back_populates="resource_bindings")


class ShipmentCaseEvent(Base):
    """案件时间线中的一条只追加审计事件。

    状态和资源字段允许为空，因为同一张时间线既记录案件状态迁移，也记录资源绑定等
    非状态事件。actor 三字段不能直接信任请求体；当前由服务端写入演示占位值，后续再
    替换为经过认证的用户上下文。

    这里故意没有幂等键和请求摘要。事件回答“发生了什么业务事实”，命令台账回答“哪个
    HTTP 命令已经成功以及它是否真正改变事实”。一个成功 ``NO_OP`` 命令只进台账，
    不应伪装成业务事件。
    """

    __tablename__ = "shipment_case_events"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "sequence",
            name="uq_shipment_case_events_case_sequence",
        ),
        Index("ix_shipment_case_events_case_id", "case_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[ShipmentCaseStatus | None] = mapped_column(
        Enum(
            ShipmentCaseStatus,
            name="shipment_case_status",
            native_enum=False,
            length=32,
        ),
        nullable=True,
    )
    current_status: Mapped[ShipmentCaseStatus | None] = mapped_column(
        Enum(
            ShipmentCaseStatus,
            name="shipment_case_status",
            native_enum=False,
            length=32,
        ),
        nullable=True,
    )
    resource_type: Mapped[ShipmentResourceType | None] = mapped_column(
        Enum(
            ShipmentResourceType,
            name="shipment_resource_type",
            native_enum=False,
            length=32,
        ),
        nullable=True,
    )
    resource_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
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

    shipment_case: Mapped[ShipmentCase] = relationship(back_populates="events")


class ShipmentCaseCommand(Base):
    """每一个已经成功返回的 Shipment Case 写命令。

    ``idempotency_key_hash`` 在整张表全局唯一，因此同一个 key 不能换案件、换动作或换
    payload 再使用。原始 key 不落库；``request_hash`` 用于判断重试是否仍是同一请求。

    ``result_event_id`` 只有 ``APPLIED`` 命令才有值。``NO_OP`` 仍必须登记命令结果，但
    不会增加案件 revision，也不会创建虚假的业务事件。
    """

    __tablename__ = "shipment_case_commands"
    __table_args__ = (
        CheckConstraint(
            "result_revision > 0",
            name="ck_shipment_case_commands_result_revision_positive",
        ),
        CheckConstraint(
            "("
            "result_kind = 'APPLIED' AND result_event_id IS NOT NULL"
            ") OR ("
            "result_kind = 'NO_OP' AND result_event_id IS NULL"
            ")",
            name="ck_shipment_case_commands_result_event",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_shipment_case_commands_idempotency_key_hash",
        ),
        UniqueConstraint(
            "result_event_id",
            name="uq_shipment_case_commands_result_event_id",
        ),
        Index("ix_shipment_case_commands_case_id", "case_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    command_type: Mapped[ShipmentCaseCommandType] = mapped_column(
        Enum(
            ShipmentCaseCommandType,
            name="shipment_case_command_type",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    result_kind: Mapped[ShipmentCaseCommandResult] = mapped_column(
        Enum(
            ShipmentCaseCommandResult,
            name="shipment_case_command_result",
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_event_id: Mapped[int | None] = mapped_column(
        # APPLIED 命令依赖对应业务事件；禁止单独删除事件后让已使用的幂等键重新可用。
        # 删除整个案件时，MySQL 仍可沿 case_id 的两个 CASCADE 同时清理事件和命令。
        ForeignKey("shipment_case_events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
