"""LINK 与本地模拟承运人之间的请求、回调及只读视图。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.booking_application import BookingStatus
from app.models.carrier_submission import CarrierBookingRequestStatus


class CarrierSubmissionRead(BaseModel):
    """平台详情和手工重试接口共用的最小提交状态。"""

    model_config = ConfigDict(from_attributes=True)

    status: CarrierBookingRequestStatus
    attempt_count: int
    last_error_message: str | None
    can_retry: bool
    can_cancel: bool
    carrier_request_id: str | None


class CarrierCallbackPayload(BaseModel):
    """模拟承运人回调给 LINK 的处理中或终态事实。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    callback_id: str = Field(min_length=1, max_length=64)
    carrier_request_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    application_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^DEMO-[A-Za-z0-9_-]+$",
    )
    status: CarrierBookingRequestStatus
    booking_reference: str | None = Field(default=None, max_length=64)
    reason_code: str | None = Field(default=None, max_length=64)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_terminal_fact(self) -> "CarrierCallbackPayload":
        """终态与附加字段必须互相一致。"""

        if self.status is CarrierBookingRequestStatus.PROCESSING:
            if self.booking_reference or self.reason_code:
                raise ValueError("PROCESSING 回调不能提供终态附加字段")
        elif self.status is CarrierBookingRequestStatus.CONFIRMED:
            if not self.booking_reference:
                raise ValueError("CONFIRMED 回调必须提供 booking_reference")
            if self.reason_code:
                raise ValueError("CONFIRMED 回调不能提供 reason_code")
        elif self.status is CarrierBookingRequestStatus.REJECTED:
            if self.booking_reference:
                raise ValueError("REJECTED 回调不能提供 booking_reference")
            if not self.reason_code:
                raise ValueError("REJECTED 回调必须提供 reason_code")
        else:
            raise ValueError("承运人回调只接受 PROCESSING、CONFIRMED 或 REJECTED")
        return self


class CarrierCallbackResult(BaseModel):
    """明确告诉投递方本次回调是首次处理、幂等重放还是被乱序保护忽略。"""

    callback_id: str
    duplicate: bool
    ignored: bool
    application_status: BookingStatus
