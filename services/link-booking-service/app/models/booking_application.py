"""订舱申请的持久化模型。

除首个 API 闭环的元数据外，只允许保存人工确认后的结构化 Packing List 明细。
原始 PDF 全文仍不得写入数据库或日志。
"""

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.booking_document_import import BookingDocumentImport
    from app.models.carrier_submission import CarrierBookingRequest


class BookingStatus(StrEnum):
    """申请在首期允许出现的状态，转换规则由领域服务统一约束。"""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class BookingEventType(StrEnum):
    """审计流水中的业务动作类型。

    事件类型不是页面显示文案，而是稳定的领域枚举。Vue 可以把它翻译成中文；数据库、
    Python 和未来的消息消费者则依据这个值判断发生了什么。
    """

    DRAFT_CREATED = "DRAFT_CREATED"
    SUBMITTED = "SUBMITTED"
    CARRIER_PROCESSING = "CARRIER_PROCESSING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class BookingApplication(Base):
    """LINK 订舱申请表；该表只存在于 ``link_booking_service`` 独立 schema。"""

    __tablename__ = "booking_applications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 申请编号由调用方提供，作为幂等业务键，不能使用真实客户单号进行演示。
    application_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    booking_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin_port: Mapped[str] = mapped_column(String(64))
    destination_port: Mapped[str] = mapped_column(String(64))
    # 当前只保存人工确认后的运输方式。解析器给出的值始终只是候选，不能直接写入此列。
    transport_mode: Mapped[str] = mapped_column(String(32), default="SEA")
    container_type: Mapped[str] = mapped_column(String(32))
    requested_etd: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 仅保存人工确认后的结构化字段，不保存 PDF 全文。字段可能包含参与方地址，
    # 所以应用日志不能直接输出该 JSON；历史申请允许为空。
    booking_details: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    source_document_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("booking_document_imports.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status"), default=BookingStatus.DRAFT
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    # 乐观锁版本不返回给浏览器，只在同一申请被并发修改时保护数据库写入。
    # SQLAlchemy 会把它拼进 UPDATE 的 WHERE 条件，并在每次成功修改后递增。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # 两个请求同时读取同一行时，只有第一个提交者能匹配旧 version。第二个请求会收到
    # StaleDataError，而不是悄悄覆盖先完成操作的状态。
    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}

    # 申请是聚合根，操作流水只能从属于一份申请。cascade 只影响 ORM 中删除申请对象的
    # 场景；正常业务不会物理删除申请，审计记录必须长期保留。
    events: Mapped[list["BookingApplicationEvent"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="BookingApplicationEvent.id.desc()",
    )
    source_document: Mapped["BookingDocumentImport | None"] = relationship(
        back_populates="application"
    )
    carrier_request: Mapped["CarrierBookingRequest | None"] = relationship(
        back_populates="application",
        uselist=False,
    )


class BookingApplicationEvent(Base):
    """订舱申请的不可变操作流水。

    首期尚未实现登录，所以 ``actor_label`` 固定记录为 ``SYSTEM_DEMO``，明确表示它不是
    真实用户身份。等认证章节完成后，才由 Java BFF 传入已认证的平台用户，而不是让浏览器
    自报名字。
    """

    __tablename__ = "booking_application_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("booking_applications.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[BookingEventType] = mapped_column(
        Enum(BookingEventType, name="booking_event_type"),
        nullable=False,
    )
    previous_status: Mapped[BookingStatus | None] = mapped_column(
        Enum(BookingStatus, name="booking_status"),
        nullable=True,
    )
    current_status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status"),
        nullable=False,
    )
    # 对确认事件记录模拟承运人返回的构造参考号，能让以后排查“参考号由哪次回调产生”
    # 时不依赖当前申请行的最新状态。
    booking_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(64), nullable=False, default="SYSTEM_DEMO")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    application: Mapped[BookingApplication] = relationship(back_populates="events")
