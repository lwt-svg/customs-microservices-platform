"""订舱申请 API 的输入输出约束。"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.booking_application import BookingEventType, BookingStatus
from app.schemas.booking_document_import import BookingDetails
from app.schemas.carrier_submission import CarrierSubmissionRead


class BookingApplicationCreate(BaseModel):
    """创建申请所需的最小字段；示例和测试只可使用构造值。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Field 的约束不仅会校验请求，还会进入 FastAPI 自动生成的 Swagger 文档。
    application_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^DEMO-[A-Za-z0-9_-]+$",
        description="调用方提供的构造业务编号；本地实验使用 DEMO- 前缀",
        examples=["DEMO-BOOKING-0212"],
    )
    origin_port: str = Field(
        min_length=1,
        max_length=64,
        description="构造的起运港标识",
        examples=["DEMO-SHA"],
    )
    destination_port: str = Field(
        min_length=1,
        max_length=64,
        description="构造的目的港标识",
        examples=["DEMO-LAX"],
    )
    transport_mode: str = Field(
        default="SEA",
        min_length=1,
        max_length=32,
        description="人工确认后的运输方式；当前手工创建接口默认使用 SEA",
        examples=["SEA"],
    )
    container_type: str = Field(
        min_length=1,
        max_length=32,
        description="箱型，例如 20GP、40GP 或 40HQ",
        examples=["40HQ"],
    )
    requested_etd: date | None = Field(
        default=None,
        description="可选的计划离港日期，不包含时区和时分秒",
        examples=["2026-10-20"],
    )
    booking_details: BookingDetails | None = Field(
        default=None,
        description="人工确认后的 Packing List 明细；手工创建或历史申请可以为空",
    )


class BookingStatusChange(BaseModel):
    """状态变更请求；业务服务会校验是否满足状态机规则。"""

    target_status: BookingStatus = Field(
        description="目标状态；是否允许从当前状态转入该状态由 LINK 领域服务决定",
        examples=["SUBMITTED"],
    )


class BookingApplicationRead(BaseModel):
    """对外返回申请及已确认的结构化字段，不包含 PDF 原文。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    application_number: str
    booking_reference: str | None = Field(
        description="模拟承运人确认后通过回调返回的构造参考号；草稿和待处理申请为空"
    )
    origin_port: str
    destination_port: str
    transport_mode: str
    container_type: str
    requested_etd: date | None
    booking_details: BookingDetails | None = None
    status: BookingStatus
    created_at: datetime
    updated_at: datetime


class BookingApplicationEventRead(BaseModel):
    """只读审计事件；不暴露任何文件原文或未来的敏感操作参数。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: BookingEventType
    previous_status: BookingStatus | None
    current_status: BookingStatus
    booking_reference: str | None
    actor_label: str
    occurred_at: datetime


class BookingApplicationDetailRead(BaseModel):
    """申请详情由当前快照和倒序操作流水组成。"""

    application: BookingApplicationRead
    events: list[BookingApplicationEventRead]
    carrier_submission: CarrierSubmissionRead | None = Field(
        default=None,
        description="提交到模拟承运人的当前快照；草稿尚未提交时为空",
    )
