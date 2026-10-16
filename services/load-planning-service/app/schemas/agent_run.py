"""状态化装托智能体 API 契约。

这些模型有意把“问题、答案、事件、候选方案”都显式化。前端不需要解析自然语言聊天，
而是可以根据 ``value_type`` 渲染表单，并在恢复后继续同一条运行。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.agent_run import AgentRunStatus


class PackingStrategyCode(StrEnum):
    """确定性算法支持的三种取舍。"""

    STABLE = "STABLE"
    BALANCED = "BALANCED"
    UTILIZATION = "UTILIZATION"


class QuestionValueType(StrEnum):
    """前端用于选择输入控件的值类型。"""

    NUMBER = "NUMBER"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    TEXT = "TEXT"


class AgentRunCreate(BaseModel):
    """为某个现有装载任务启动一次运行。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    selected_pallet_code: str | None = Field(default=None, min_length=1, max_length=64)
    strategy_codes: list[PackingStrategyCode] = Field(
        default_factory=lambda: [
            PackingStrategyCode.STABLE,
            PackingStrategyCode.BALANCED,
            PackingStrategyCode.UTILIZATION,
        ],
        min_length=1,
        max_length=3,
    )

    @model_validator(mode="after")
    def require_unique_strategies(self) -> Self:
        """相同策略重复计算没有业务意义，也会导致 plan_code 冲突。"""

        if len(self.strategy_codes) != len(set(self.strategy_codes)):
            raise ValueError("strategy_codes 不能重复")
        return self


class AgentRunReplan(BaseModel):
    """从一条已驳回运行复制约束并重新生成候选方案。

    这不是偷偷改写旧运行：旧运行仍保持 REJECTED，新运行通过 parent_run_id
    建立审计链。若业务人员要修改尺寸、重量或安全参数，应先创建新的任务版本，
    而不是在这里覆盖旧快照。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=2_000)
    selected_pallet_code: str | None = Field(default=None, min_length=1, max_length=64)
    strategy_codes: list[PackingStrategyCode] | None = Field(
        default=None, min_length=1, max_length=3
    )

    @model_validator(mode="after")
    def require_unique_strategies(self) -> Self:
        if self.strategy_codes is not None and len(self.strategy_codes) != len(
            set(self.strategy_codes)
        ):
            raise ValueError("strategy_codes 不能重复")
        return self


class AgentAnswer(BaseModel):
    """一个结构化人工答案。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=255)
    value: str | int | float | bool


class AgentRunResume(BaseModel):
    """补充约束后恢复原运行。"""

    answers: list[AgentAnswer] = Field(min_length=1, max_length=2_000)
    # 兼容旧客户端：没有传时仍按原行为工作；新客户端可以用它做乐观并发控制。
    expected_run_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_unique_keys(self) -> Self:
        """同一次提交不能对同一个问题给出两个相互竞争的值。"""

        keys = [answer.key for answer in self.answers]
        if len(keys) != len(set(keys)):
            raise ValueError("同一次 resume 中 answer key 不能重复")
        return self


class AgentDecisionCode(StrEnum):
    """人工评审动作。"""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class AgentRunDecision(BaseModel):
    """批准一个可行方案，或驳回本次运行。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    decision: AgentDecisionCode
    plan_code: str | None = Field(default=None, min_length=1, max_length=64)
    comment: str | None = Field(default=None, max_length=2_000)
    expected_run_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_decision_fields(self) -> Self:
        """批准必须指出方案；驳回必须留下可审计原因。"""

        if self.decision is AgentDecisionCode.APPROVE and not self.plan_code:
            raise ValueError("APPROVE 必须提供 plan_code")
        if self.decision is AgentDecisionCode.REJECT and not self.comment:
            raise ValueError("REJECT 必须提供 comment")
        return self


class AgentQuestionRead(BaseModel):
    """当前状态仍需人工补充的一项约束。"""

    key: str
    label: str
    value_type: QuestionValueType
    unit: str | None
    reason: str
    required: bool
    min_value: float | None
    max_value: float | None


class AgentEventRead(BaseModel):
    """按 sequence 排序的状态迁移事件。"""

    model_config = ConfigDict(from_attributes=True)

    sequence: int
    event_type: str
    title: str
    detail: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def mark_database_timestamp_as_utc(cls, value: datetime) -> datetime:
        """MySQL DATETIME 不携带时区；本地容器按 UTC 写入，API 必须补出明确的 ``Z``。

        如果直接返回 ``2026-10-09T09:46:24``，浏览器会把它误当成本地 09:46；
        补成 UTC 后，上海浏览器才能正确显示为 17:46。将来若数据库驱动已经返回带时区
        的时间，则保留驱动给出的真实时区，不再重复覆盖。
        """

        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class PlacementRead(BaseModel):
    """单箱在托盘坐标系中的轴对齐长方体。"""

    # 下面三个字段是在 Pallet Digital Twin V2 中补充的。它们提供算法结果的稳定定位、
    # 分层查看和旋转角度；默认值用于兼容数据库中已经保存的旧版方案快照。
    placement_id: str | None = None
    item_code: str
    box_index: int
    layer: int | None = Field(default=None, ge=1)
    x_mm: float
    y_mm: float
    z_mm: float
    length_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: int | None = None
    gross_weight_kg: float | None = Field(default=None, ge=0)
    orientation: str


class PalletLoadRead(BaseModel):
    """一块实际使用托盘及其箱子坐标。"""

    pallet_index: int
    pallet_code: str
    loaded_height_mm: float
    payload_weight_kg: float | None = Field(default=None, ge=0)
    gross_weight_kg: float
    box_count: int
    layer_count: int | None = Field(default=None, ge=0)
    capacity_box_count: int | None = Field(default=None, ge=0)
    layer_area_utilization: float | None = Field(default=None, ge=0)
    payload_weight_utilization: float | None = Field(default=None, ge=0)
    height_utilization: float | None = Field(default=None, ge=0)
    balance_offset: float | None = Field(default=None, ge=0)
    pattern_type: str | None = None
    boxes_per_full_layer: int | None = Field(default=None, ge=0)
    placements: list[PlacementRead]


class PackingMetricsRead(BaseModel):
    """确定性评分和页面对比所需的核心指标。"""

    total_pallets: int
    total_boxes: int
    unplaced_boxes: int
    # 正确业务含义是“已使用层的平均平面占用率”。volume_utilization 暂时保留，
    # 只为兼容旧版本和已经持久化的方案 JSON，前端新页面优先使用本字段。
    layer_area_utilization: float | None = Field(default=None, ge=0)
    volume_utilization: float
    weight_utilization: float
    average_loaded_height_mm: float
    max_loaded_height_mm: float
    total_gross_weight_kg: float


class PackingPlanRead(BaseModel):
    """一套候选装托方案。"""

    model_config = ConfigDict(from_attributes=True)

    plan_id: str
    plan_code: str
    strategy_code: PackingStrategyCode
    strategy_name: str
    algorithm_version: str
    score_version: str
    feasible: bool
    score: float
    summary: str
    metrics: PackingMetricsRead
    warnings: list[str]
    pallets: list[PalletLoadRead]


class AgentRunRead(BaseModel):
    """每个运行端点统一返回的完整状态视图。"""

    run_id: str
    parent_run_id: str | None
    task_number: str
    status: AgentRunStatus
    current_step: str
    run_version: int
    selected_pallet_code: str | None
    selected_plan_code: str | None
    failure_code: str | None
    failure_message: str | None
    questions: list[AgentQuestionRead]
    events: list[AgentEventRead]
    plans: list[PackingPlanRead]
