"""数据就绪度补数请求和只读视图。

HTTP 只暴露当前评估与问题列表，不返回完整事实历史。事实历史包含库存证据和来源定位，
只留在服务端审计表中；Agent Trace 也只记录 fact key、来源类型和快照哈希。
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.shipment_data_readiness import (
    ShipmentDataFactSourceType,
    ShipmentDataFactType,
    ShipmentDataReadinessStatus,
    ShipmentDataSourceMode,
    ShipmentDataSubjectType,
)

_HUMAN_SUPPLY_DATA_SOURCE_TYPES = {
    ShipmentDataFactSourceType.MANUAL_CONFIRMATION,
    ShipmentDataFactSourceType.CSV_IMPORT,
    ShipmentDataFactSourceType.SYNTHETIC_DEMO,
}
# 浏览器和服务端时钟可能存在少量误差，因此只拒绝超过 5 分钟的“明显未来”观察时间。
# 这不是证据时效策略；证据是否过期仍由确定性 DATA_READINESS 评估器判断。
_OBSERVED_AT_FUTURE_TOLERANCE = timedelta(minutes=5)


class ShipmentDataReadinessIssueCode(StrEnum):
    """确定性门禁当前可能产生的固定问题代码。"""

    AVAILABLE_QUANTITY_MISSING = "AVAILABLE_QUANTITY_MISSING"
    INVENTORY_SHORTAGE = "INVENTORY_SHORTAGE"
    MASTER_DATA_UNCONFIRMED = "MASTER_DATA_UNCONFIRMED"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    SYNTHETIC_DATA_NOT_ALLOWED = "SYNTHETIC_DATA_NOT_ALLOWED"


class ShipmentDataIssueSeverity(StrEnum):
    """第一版所有就绪度问题都会阻止案件继续。"""

    BLOCKING = "BLOCKING"


class ShipmentDataResolution(StrEnum):
    """问题对应的结构化解决动作。"""

    SUPPLY_DATA = "SUPPLY_DATA"


class ShipmentDataOwnerRole(StrEnum):
    """第一版缺数待办的业务责任角色。"""

    WAREHOUSE_OPERATOR = "WAREHOUSE_OPERATOR"


class ShipmentDataFactInput(BaseModel):
    """一次人工恢复命令中提交的一条库存或主数据确认事实。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subject_type: ShipmentDataSubjectType
    subject_code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    fact_type: ShipmentDataFactType
    # 使用 object 保留 JSON 原始类型，再按 fact_type 做严格校验。这样 Python 的 bool
    # 不会因为 ``bool`` 是 ``int`` 的子类而被库存数量规则误接收。
    value: object
    source_type: ShipmentDataFactSourceType
    source_reference: str | None = Field(default=None, max_length=255)
    source_locator: str | None = Field(default=None, max_length=255)
    observed_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_fact_value_and_time(self) -> Self:
        if self.source_type not in _HUMAN_SUPPLY_DATA_SOURCE_TYPES:
            raise ValueError(
                "人工 SUPPLY_DATA 不能声明 ERP/WMS 来源；请使用人工确认、CSV 导入或脱敏演示来源"
            )
        if self.source_type is ShipmentDataFactSourceType.CSV_IMPORT and not self.source_reference:
            raise ValueError("CSV_IMPORT 必须提供 source_reference（文件名或导入批次号）")

        if self.fact_type is ShipmentDataFactType.AVAILABLE_QUANTITY:
            if type(self.value) is not int:
                raise ValueError("AVAILABLE_QUANTITY 的 value 必须是非负整数")
            if self.value < 0:
                raise ValueError("AVAILABLE_QUANTITY 的 value 不能为负数")
        elif type(self.value) is not bool:
            raise ValueError("MASTER_DATA_CONFIRMED 的 value 必须是布尔值")

        for field_name, value in (
            ("observed_at", self.observed_at),
            ("expires_at", self.expires_at),
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field_name} 必须包含时区")

        now = datetime.now(UTC)
        if self.observed_at is not None and self.observed_at > now + _OBSERVED_AT_FUTURE_TOLERANCE:
            raise ValueError("observed_at 不能明显晚于当前时间")

        # expires_at 可以为空，也允许提交“已经过期”的历史证据，让评估器明确产生
        # EVIDENCE_STALE；但它在时间线上必须晚于该证据自己的观察时间。
        effective_observed_at = self.observed_at or now
        if self.expires_at is not None and self.expires_at <= effective_observed_at:
            raise ValueError("expires_at 必须晚于 observed_at")
        return self

    @field_validator("observed_at", "expires_at")
    @classmethod
    def normalize_datetime_to_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        # 上面的 model validator 会拒绝 naive datetime；这里统一输出 UTC。
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC)


class ShipmentDataRequestedFactRead(BaseModel):
    """当前待办允许回答的一个事实键。"""

    subject_type: ShipmentDataSubjectType
    subject_code: str
    fact_type: ShipmentDataFactType


class ShipmentDataReadinessIssueRead(BaseModel):
    """页面可直接渲染的一条阻断问题。"""

    issue_id: str
    issue_code: ShipmentDataReadinessIssueCode
    severity: ShipmentDataIssueSeverity = ShipmentDataIssueSeverity.BLOCKING
    subject_type: ShipmentDataSubjectType
    subject_code: str
    fact_type: ShipmentDataFactType
    required_value: int | bool | str | None
    actual_value: int | bool | str | None
    unit: str
    message: str
    resolution: ShipmentDataResolution = ShipmentDataResolution.SUPPLY_DATA
    owner_role: ShipmentDataOwnerRole = ShipmentDataOwnerRole.WAREHOUSE_OPERATOR


class ShipmentDataReadinessRead(BaseModel):
    """Shipment Agent 运行响应中的当前数据门禁投影。"""

    status: ShipmentDataReadinessStatus
    # 0 只用于“尚未评估”和迁移前运行；真正案件级记录从 1 开始。
    revision: int = Field(ge=0)
    policy_version: str
    source_mode: ShipmentDataSourceMode
    snapshot_hash: str | None
    assessed_at: datetime | None
    ready_at: datetime | None
    issue_count: int = Field(ge=0)
    blocking_issue_count: int = Field(ge=0)
    issues: list[ShipmentDataReadinessIssueRead] = Field(default_factory=list)

    @field_validator("assessed_at", "ready_at")
    @classmethod
    def normalize_timestamps(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
