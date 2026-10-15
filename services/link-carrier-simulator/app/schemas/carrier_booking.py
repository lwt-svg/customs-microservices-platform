"""模拟承运人 API 的请求、响应模型。"""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.carrier_booking import (
    CallbackDeliveryStatus,
    CallbackEventType,
    CarrierBookingEventType,
    CarrierBookingStatus,
)


class CarrierBookingCreate(BaseModel):
    """LINK 发送给模拟承运人的最小构造申请内容。"""

    application_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^DEMO-",
        examples=["DEMO-BOOKING-2201"],
        description="仅限 DEMO 构造申请编号",
    )
    origin_port: str = Field(min_length=1, max_length=64, examples=["DEMO-SHA"])
    destination_port: str = Field(min_length=1, max_length=64, examples=["DEMO-LAX"])
    transport_mode: str = Field(default="OCEAN", min_length=1, max_length=32, examples=["OCEAN"])
    container_type: str = Field(min_length=1, max_length=32, examples=["40HQ"])
    requested_etd: date | None = Field(default=None, examples=["2026-10-20"])


class CarrierBookingRead(BaseModel):
    """门户列表和平台查询共用的当前快照。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    carrier_request_id: str
    application_number: str
    origin_port: str
    destination_port: str
    transport_mode: str
    container_type: str
    requested_etd: date | None
    status: CarrierBookingStatus
    booking_reference: str | None
    rejection_reason_code: str | None
    created_at: datetime
    updated_at: datetime


class CarrierBookingEventRead(BaseModel):
    """门户详情中的不可变操作时间线。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: CarrierBookingEventType
    previous_status: CarrierBookingStatus | None
    current_status: CarrierBookingStatus
    actor_label: str
    occurred_at: datetime


class CallbackDeliveryRead(BaseModel):
    """回调投递的状态，不输出回调 Token。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    callback_id: str
    booking_id: int
    event_type: CallbackEventType
    status: CallbackDeliveryStatus
    attempt_count: int
    next_attempt_at: datetime | None
    lease_until: datetime | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CarrierBookingDetailRead(BaseModel):
    """门户详情把当前快照、业务时间线和回调任务分开呈现。"""

    booking: CarrierBookingRead
    events: list[CarrierBookingEventRead]
    callback_deliveries: list[CallbackDeliveryRead]


class CarrierDecision(StrEnum):
    """门户最终决定；不能直接传入任意承运人状态。"""

    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class CarrierBookingDecision(BaseModel):
    """确认或拒绝处理中的申请。"""

    decision: CarrierDecision
    rejection_reason_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        examples=["DEMO-NO-SPACE"],
    )

    @model_validator(mode="after")
    def validate_rejection_reason_code(self) -> "CarrierBookingDecision":
        """拒绝必须有构造原因代码；确认时不能携带误导性的拒绝字段。"""

        if self.decision is CarrierDecision.REJECTED and not self.rejection_reason_code:
            raise ValueError("REJECTED 决策必须提供 rejection_reason_code")
        if self.decision is CarrierDecision.CONFIRMED and self.rejection_reason_code:
            raise ValueError("CONFIRMED 决策不能提供 rejection_reason_code")
        return self
