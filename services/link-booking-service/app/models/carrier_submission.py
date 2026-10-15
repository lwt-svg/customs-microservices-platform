"""LINK 侧的承运人提交任务与回调收件记录。

这两张表分别回答两个问题：

* ``carrier_booking_requests``：LINK 是否已经把申请可靠地送到模拟承运人；
* ``carrier_callback_receipts``：某个回调是否收过、是否真正改变了申请。

回调原文不会入库，只保存稳定哈希和最小审计字段，避免今后把未知外部内容误当日志。
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.booking_application import BookingApplication


class CarrierBookingRequestStatus(StrEnum):
    """LINK 观察到的提交任务与承运人处理状态。"""

    PENDING = "PENDING"
    SENDING = "SENDING"
    RETRY_WAIT = "RETRY_WAIT"
    ACCEPTED = "ACCEPTED"
    PROCESSING = "PROCESSING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    DEAD = "DEAD"
    CANCELLED = "CANCELLED"


class CarrierBookingRequest(Base):
    """一份订舱申请唯一对应的一条可靠投递任务。"""

    __tablename__ = "carrier_booking_requests"
    __table_args__ = (
        Index(
            "ix_carrier_booking_requests_status_next_attempt_at",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("booking_applications.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 对应模拟承运人的 carrier_request_id，不能与 LINK 数据库自增主键混为一谈。
    carrier_request_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
    )
    status: Mapped[CarrierBookingRequestStatus] = mapped_column(
        Enum(CarrierBookingRequestStatus, name="carrier_booking_request_status"),
        nullable=False,
        default=CarrierBookingRequestStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    application: Mapped["BookingApplication"] = relationship(back_populates="carrier_request")
    callback_receipts: Mapped[list["CarrierCallbackReceipt"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="CarrierCallbackReceipt.id.desc()",
    )

    @property
    def can_retry(self) -> bool:
        """只有失败等待或 DEAD 任务允许页面明确触发人工重试。"""

        return self.status in {
            CarrierBookingRequestStatus.RETRY_WAIT,
            CarrierBookingRequestStatus.DEAD,
        }

    @property
    def can_cancel(self) -> bool:
        """只有尚未被模拟承运人受理的任务可随申请一起取消。"""

        return self.status in {
            CarrierBookingRequestStatus.PENDING,
            CarrierBookingRequestStatus.RETRY_WAIT,
            CarrierBookingRequestStatus.DEAD,
        }


class CarrierCallbackReceipt(Base):
    """已接收回调的幂等凭证；相同 callback_id 只能对应同一份 JSON 事实。"""

    __tablename__ = "carrier_callback_receipts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    callback_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("carrier_booking_requests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    carrier_status: Mapped[CarrierBookingRequestStatus] = mapped_column(
        Enum(CarrierBookingRequestStatus, name="carrier_booking_request_status"),
        nullable=False,
    )
    ignored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    request: Mapped[CarrierBookingRequest] = relationship(back_populates="callback_receipts")
