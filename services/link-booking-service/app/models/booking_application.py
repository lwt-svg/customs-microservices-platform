"""订舱申请的持久化模型。

字段刻意只保留首个 API 闭环所需的最小元数据。未来解析器不能把原始 PDF/XLSX
文本、客户地址、金额或商品明细写入日志；是否入库要另行做数据分级设计。
"""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BookingStatus(StrEnum):
    """申请在首期允许出现的状态，转换规则由领域服务统一约束。"""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
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
    container_type: Mapped[str] = mapped_column(String(32))
    requested_etd: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status"), default=BookingStatus.DRAFT
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
