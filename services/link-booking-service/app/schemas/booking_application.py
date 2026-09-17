"""订舱申请 API 的输入输出约束。"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.booking_application import BookingStatus


class BookingApplicationCreate(BaseModel):
    """创建申请所需的最小字段；示例和测试只可使用构造值。"""

    application_number: str = Field(min_length=1, max_length=64)
    origin_port: str = Field(min_length=1, max_length=64)
    destination_port: str = Field(min_length=1, max_length=64)
    container_type: str = Field(min_length=1, max_length=32)
    requested_etd: date | None = None


class BookingStatusChange(BaseModel):
    """状态变更请求；业务服务会校验是否满足状态机规则。"""

    target_status: BookingStatus


class BookingApplicationRead(BaseModel):
    """对外返回的申请视图，不包含文件原文或任何敏感解析内容。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    application_number: str
    booking_reference: str | None
    origin_port: str
    destination_port: str
    container_type: str
    requested_etd: date | None
    status: BookingStatus
    created_at: datetime
    updated_at: datetime
