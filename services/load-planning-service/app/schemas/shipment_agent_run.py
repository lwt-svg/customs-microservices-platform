"""Shipment Coordinator Agent 的 HTTP 请求与只读视图。

总 Agent 使用结构化 ``pending_interrupt``，而不是让前端解析聊天文本。页面可以直接
根据 ``type``、``allowed_actions`` 和 ``details`` 渲染审批、资源选择或重新检查按钮。
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.shipment_agent_run import (
    ShipmentAgentAction,
    ShipmentAgentCapability,
    ShipmentAgentInterruptType,
    ShipmentAgentRunStatus,
    ShipmentAgentStage,
)
from app.models.shipment_case import ShipmentResourceType
from app.schemas.shipment_data_readiness import (
    ShipmentDataFactInput,
    ShipmentDataReadinessRead,
)


class ShipmentAgentStart(BaseModel):
    """启动总 Agent。

    当前启动命令没有可由浏览器决定的业务参数。保留显式空对象模型，能让未来扩展继续
    使用同一路由，同时拒绝调用方偷偷传入 actor、组织或下游状态。
    """

    model_config = ConfigDict(extra="forbid")

    expected_case_revision: int = Field(
        ge=1,
        description="调用方读取案件时看到的 revision，避免从旧案件页面启动总 Agent",
    )


class ShipmentAgentResume(BaseModel):
    """恢复一次人工中断，或对外部/子 Agent 等待状态执行显式重新检查。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: ShipmentAgentAction
    expected_run_version: int = Field(
        ge=1,
        description="调用方读取运行时看到的 run_version；用于阻止旧页面覆盖新状态",
    )
    interrupt_type: ShipmentAgentInterruptType = Field(
        description="调用方实际看到并确认的 interrupt 类型；用于阻止旧弹窗误操作"
    )
    comment: str | None = Field(default=None, max_length=2_000)
    required_capabilities: list[ShipmentAgentCapability] | None = Field(
        default=None,
        min_length=1,
        max_length=2,
        description="仅 SCENARIO_POLICY_CONFIRMATION + CONFIRM 使用",
    )
    selected_resource_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    data_facts: list[ShipmentDataFactInput] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="仅 DATA_READINESS_REMEDIATION + SUPPLY_DATA 使用",
    )

    @model_validator(mode="after")
    def validate_action_fields(self) -> "ShipmentAgentResume":
        """校验动作自身需要的字段；是否匹配当前 interrupt 由 Service 再判断。"""

        if self.action is ShipmentAgentAction.CONFIRM:
            if not self.required_capabilities:
                raise ValueError("CONFIRM 必须提供 required_capabilities")
        elif self.required_capabilities is not None:
            raise ValueError("只有 CONFIRM 可以提供 required_capabilities")

        if self.action is ShipmentAgentAction.SELECT:
            if self.selected_resource_number is None:
                raise ValueError("SELECT 必须提供 selected_resource_number")
        elif self.selected_resource_number is not None:
            raise ValueError("只有 SELECT 可以提供 selected_resource_number")

        if self.action is ShipmentAgentAction.SUPPLY_DATA:
            if not self.data_facts:
                raise ValueError("SUPPLY_DATA 必须提供 data_facts")
        elif self.data_facts is not None:
            raise ValueError("只有 SUPPLY_DATA 可以提供 data_facts")
        return self

    @field_validator("required_capabilities")
    @classmethod
    def require_unique_capabilities(
        cls,
        value: list[ShipmentAgentCapability] | None,
    ) -> list[ShipmentAgentCapability] | None:
        """同一能力重复两次没有业务意义。"""

        if value is not None and len(value) != len(set(value)):
            raise ValueError("required_capabilities 不能重复")
        return value

    @field_validator("data_facts")
    @classmethod
    def require_unique_data_fact_keys(
        cls,
        value: list[ShipmentDataFactInput] | None,
    ) -> list[ShipmentDataFactInput] | None:
        """同一命令不能对同一个事实键给出两个互相矛盾的值。"""

        if value is None:
            return None
        keys = [(fact.subject_type, fact.subject_code, fact.fact_type) for fact in value]
        if len(keys) != len(set(keys)):
            raise ValueError("data_facts 中的事实键不能重复")
        return value


class ShipmentAgentReconcile(BaseModel):
    """显式重新读取案件、装载子 Agent 和 LINK 当前事实。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    expected_run_version: int = Field(ge=1)


class ShipmentAgentInterruptRead(BaseModel):
    """页面可直接渲染的一项人工等待合同。"""

    type: ShipmentAgentInterruptType
    title: str
    description: str
    allowed_actions: list[ShipmentAgentAction]
    details: dict[str, object] = Field(default_factory=dict)


class ShipmentAgentChildRead(BaseModel):
    """装载子 Agent 的最小状态投影，不复制候选方案和 3D 坐标。"""

    run_id: str
    task_number: str
    status: str
    current_step: str
    run_version: int
    next_action: str


class ShipmentAgentEventRead(BaseModel):
    """Agent Trace 中的一条审计事件。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    event_type: str
    stage: ShipmentAgentStage
    title: str
    detail: str
    data: dict[str, object]
    actor_subject: str
    actor_display_name: str
    actor_source: str
    request_id: str
    resource_type: ShipmentResourceType | None = None
    resource_number: str | None = None
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def normalize_datetime_to_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ShipmentAgentRunRead(BaseModel):
    """总 Agent 页面、Agent Center 和 BFF 共用的完整运行视图。"""

    run_id: str
    case_number: str
    case_name: str
    transport_scenario: str
    case_status: str
    status: ShipmentAgentRunStatus
    current_stage: ShipmentAgentStage
    run_version: int
    workflow_version: str
    policy_version: str
    started_case_revision: int
    last_observed_case_revision: int
    required_capabilities: list[ShipmentAgentCapability]
    scenario_policy_confirmed: bool
    selected_load_task_number: str | None
    selected_load_run_id: str | None
    locked_load_version_id: str | None
    locked_load_version_number: int | None
    selected_booking_application_number: str | None
    booking_status: str | None
    carrier_submission_status: str | None
    booking_reference: str | None
    booking_submission_uncertain: bool
    data_readiness: ShipmentDataReadinessRead
    pending_interrupt: ShipmentAgentInterruptRead | None
    allowed_actions: list[ShipmentAgentAction]
    child_agent: ShipmentAgentChildRead | None
    next_wake_at: datetime | None = None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    events: list[ShipmentAgentEventRead]

    @field_validator("created_at", "updated_at", "completed_at")
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
