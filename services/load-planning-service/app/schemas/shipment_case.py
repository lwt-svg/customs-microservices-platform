"""Shipment Case HTTP 边界的 Pydantic 模型。

Shipment Case 是一票发运工作的稳定业务外壳。请求模型只允许调用方提交业务命令需要
的数据；组织、操作人和审计来源不能让浏览器自行声明。当前由服务端写入明确的演示占位
值，后续安全章节再替换为经过认证的用户与组织上下文。

本模块统一使用 snake_case。Java BFF 如需 camelCase，应在自己的 DTO 边界转换，而
不是让 Python 领域模型同时兼容两套字段名。
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.shipment_case import (
    ShipmentCaseStatus,
    ShipmentResourceType,
    ShipmentTransportScenario,
)


class ShipmentCaseCreate(BaseModel):
    """创建一份轻量发运案件。

    ``responsible_organization_code``、actor 和幂等键都不属于请求正文：前两者当前由
    服务端写入演示占位值，后续再来自认证上下文；幂等键来自 HTTP Header。这样至少能
    阻止浏览器直接在业务 JSON 中声明组织或审计身份，但不代表真实认证已经完成。
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    case_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        examples=["SHP-DEMO-FCL-001"],
        description="稳定案件编号；只能使用字母、数字、下划线和连字符",
    )
    case_name: str = Field(
        min_length=1,
        max_length=128,
        examples=["上海至洛杉矶演示发运"],
    )
    transport_scenario: ShipmentTransportScenario = Field(
        description="当前已知的运输场景；尚未确认时使用 UNDECIDED"
    )
    description: str | None = Field(default=None, max_length=2_000)


class ShipmentCaseResourceBind(BaseModel):
    """把一个已有业务资源绑定到案件，而不是复制资源明细。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_type: ShipmentResourceType
    resource_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        examples=["LP-DEMO-0001"],
    )
    expected_revision: int = Field(
        ge=1,
        description="调用方读取案件时看到的 revision，用于阻止旧页面覆盖新数据",
    )


class ShipmentCaseStatusChange(BaseModel):
    """请求案件状态流转；是否允许流转由领域服务判断。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    target_status: ShipmentCaseStatus
    expected_revision: int = Field(
        ge=1,
        description="乐观并发版本；与数据库当前 revision 不一致时应拒绝命令",
    )
    reason: str = Field(
        min_length=1,
        max_length=2_000,
        description="状态变化原因，会进入不可变事件流水",
    )


class _UtcReadModel(BaseModel):
    """把数据库返回的时间统一补成 UTC aware datetime。

    MySQL/SQLite 的 ``DateTime`` 可能返回不带时区的 ``datetime``。系统约定这些值都是
    UTC，因此响应前补上 UTC；如果值本身带时区，则转换为 UTC。这样 JSON 中会稳定输出
    ``Z``，前端不会把服务器时间误当成本地时间。
    """

    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="after")
    @classmethod
    def normalize_datetime_to_utc(cls, value: object) -> object:
        """仅处理 datetime，其他字段原样返回。"""

        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ShipmentCaseResourceBindingRead(_UtcReadModel):
    """案件中的一个稳定资源引用及其绑定时验证快照。"""

    id: int
    resource_type: ShipmentResourceType
    resource_number: str
    resource_status_at_verification: str
    verification_source: str
    verified_at: datetime
    bound_at: datetime


# 教程和调用方有时会简称为 BindingRead；保留明确的规范名，同时提供无行为差异的别名。
ShipmentCaseBindingRead = ShipmentCaseResourceBindingRead


class ShipmentCaseEventRead(_UtcReadModel):
    """不可变案件事件；不向 API 暴露幂等键或请求摘要的哈希内部字段。"""

    id: int
    sequence: int
    event_type: str
    previous_status: ShipmentCaseStatus | None
    current_status: ShipmentCaseStatus | None
    resource_type: ShipmentResourceType | None
    resource_number: str | None
    actor_subject: str
    actor_display_name: str
    actor_source: str
    request_id: str
    detail: str
    occurred_at: datetime


class ShipmentCaseSummaryRead(_UtcReadModel):
    """列表页所需的案件摘要，不强制加载资源和事件集合。"""

    id: str
    case_number: str
    case_name: str
    responsible_organization_code: str
    transport_scenario: ShipmentTransportScenario
    status: ShipmentCaseStatus
    revision: int
    description: str | None
    created_at: datetime
    updated_at: datetime


class ShipmentCaseRead(ShipmentCaseSummaryRead):
    """案件详情：当前快照、资源引用和按 sequence 排列的事件流水。"""

    resource_bindings: list[ShipmentCaseResourceBindingRead] = Field(default_factory=list)
    events: list[ShipmentCaseEventRead] = Field(default_factory=list)
