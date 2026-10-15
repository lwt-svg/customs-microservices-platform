"""模拟承运人订舱、时间线和回调投递模型。

这三个表刻意分离：
* ``carrier_bookings`` 记录当前快照；
* ``carrier_booking_events`` 记录承运人门户的操作时间线；
* ``callback_deliveries`` 记录对 LINK 的可靠回调尝试。

因此即使回调失败，也不会丢失已经完成的“承运人确认/拒绝”业务事实。
"""

from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import uuid4

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CarrierBookingStatus(StrEnum):
    """承运人侧可见的处理状态。"""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class CarrierBookingEventType(StrEnum):
    """门户时间线的稳定事件类型。"""

    RECEIVED = "RECEIVED"
    PROCESSING_STARTED = "PROCESSING_STARTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class CallbackDeliveryStatus(StrEnum):
    """回调投递的状态，而非承运人订舱状态。"""

    PENDING = "PENDING"
    # 已领取、正在调用 LINK 的短暂状态。它让门户能区分“尚未开始”与“正在投递”。
    SENDING = "SENDING"
    RETRY_WAIT = "RETRY_WAIT"
    DELIVERED = "DELIVERED"
    DEAD = "DEAD"


class CallbackEventType(StrEnum):
    """发送给 LINK 的终态通知类型。"""

    BOOKING_PROCESSING = "BOOKING_PROCESSING"
    BOOKING_CONFIRMED = "BOOKING_CONFIRMED"
    BOOKING_REJECTED = "BOOKING_REJECTED"


class CarrierBooking(Base):
    """独立模拟承运人收到的一份构造订舱申请。"""

    __tablename__ = "carrier_bookings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 同一调用方和同一幂等键只能产生一份申请。它是请求语义，不是外部订舱号。
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    application_number: Mapped[str] = mapped_column(String(64), index=True)
    # 承运人请求号一旦生成就不变化，帮助门户操作员将页面与 LINK 请求对应起来。
    carrier_request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    origin_port: Mapped[str] = mapped_column(String(64))
    destination_port: Mapped[str] = mapped_column(String(64))
    transport_mode: Mapped[str] = mapped_column(String(32), default="OCEAN")
    container_type: Mapped[str] = mapped_column(String(32))
    requested_etd: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[CarrierBookingStatus] = mapped_column(
        Enum(CarrierBookingStatus, name="carrier_booking_status"),
        default=CarrierBookingStatus.RECEIVED,
    )
    booking_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 本地枚举式原因代码，而不是自由文本，避免演示页看起来像录入真实业务说明。
    rejection_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}

    events: Mapped[list["CarrierBookingEvent"]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="CarrierBookingEvent.id.desc()",
    )
    callback_deliveries: Mapped[list["CallbackDelivery"]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="CallbackDelivery.id.desc()",
    )


class CarrierBookingEvent(Base):
    """承运人操作门户的不可变时间线。"""

    __tablename__ = "carrier_booking_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("carrier_bookings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[CarrierBookingEventType] = mapped_column(
        Enum(CarrierBookingEventType, name="carrier_booking_event_type"),
        nullable=False,
    )
    previous_status: Mapped[CarrierBookingStatus | None] = mapped_column(
        Enum(CarrierBookingStatus, name="carrier_booking_status"),
        nullable=True,
    )
    current_status: Mapped[CarrierBookingStatus] = mapped_column(
        Enum(CarrierBookingStatus, name="carrier_booking_status"),
        nullable=False,
    )
    actor_label: Mapped[str] = mapped_column(String(64), nullable=False, default="CARRIER_PORTAL_DEMO")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    booking: Mapped[CarrierBooking] = relationship(back_populates="events")


class CallbackDelivery(Base):
    """一条可独立重试、可审计的回调投递任务。"""

    __tablename__ = "callback_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("carrier_bookings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # 这个 UUID 会进入请求 JSON。LINK 应把它作为回调幂等业务键。
    callback_id: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
    )
    event_type: Mapped[CallbackEventType] = mapped_column(
        Enum(CallbackEventType, name="callback_event_type"),
        nullable=False,
    )
    status: Mapped[CallbackDeliveryStatus] = mapped_column(
        Enum(CallbackDeliveryStatus, name="callback_delivery_status"),
        default=CallbackDeliveryStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 领取任务后先写入租约。进程在 HTTP 调用时崩溃，租约过期后 worker 会重新领取。
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 仅记录截断后的技术错误，避免把 Token 或未知响应体写入数据库。
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    booking: Mapped[CarrierBooking] = relationship(back_populates="callback_deliveries")
