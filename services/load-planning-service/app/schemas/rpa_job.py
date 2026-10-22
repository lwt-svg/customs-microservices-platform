"""统一 RPA Job 的 HTTP 请求与只读视图。

HTTP 模型与纯领域契约分开：

* :mod:`app.rpa.contracts` 定义所有运行域共同遵守的业务语义；
* 本模块只描述 API 如何派发、领取、汇报进度和回传结果。

Worker 只能通过一次领取得到的 ``attempt_token`` 写回状态。普通查询不会返回这个 token，
避免页面或日志误把 Worker 凭据当成业务数据展示。
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.rpa.contracts import (
    RpaBusinessOutcome,
    RpaDomain,
    RpaEnvironment,
    RpaJobStatus,
    RpaOperation,
    RpaSideEffectBoundary,
    RpaSideEffectPhase,
)


class RpaJobDispatch(BaseModel):
    """创建并立即进入队列的一条 RPA Job。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    originating_run_id: UUID | None = None
    domain: RpaDomain
    operation: RpaOperation
    environment: RpaEnvironment = RpaEnvironment.SANDBOX
    business_reference: str = Field(min_length=1, max_length=128)
    side_effect_boundary: RpaSideEffectBoundary = RpaSideEffectBoundary.DRAFT_ONLY
    adapter_key: str = Field(min_length=1, max_length=64)
    source_profile: str | None = Field(default=None, min_length=1, max_length=96)
    flow_profile: str | None = Field(default=None, min_length=1, max_length=96)
    selector_pack_version: str = Field(min_length=1, max_length=96)
    payload_schema_version: str = Field(default="1", min_length=1, max_length=32)
    max_attempts: int = Field(default=3, ge=1, le=10)
    business_payload: dict[str, JsonValue]


class RpaJobClaim(BaseModel):
    """Worker 领取自己运行域内的一条排队任务。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain: RpaDomain
    environment: RpaEnvironment = RpaEnvironment.SANDBOX
    worker_id: str = Field(min_length=1, max_length=128)
    supported_operations: list[RpaOperation] = Field(min_length=1, max_length=3)

    @field_validator("supported_operations")
    @classmethod
    def require_unique_operations(
        cls,
        value: list[RpaOperation],
    ) -> list[RpaOperation]:
        if len(value) != len(set(value)):
            raise ValueError("supported_operations 不能重复")
        return value

    @model_validator(mode="after")
    def require_operations_from_same_domain(self) -> "RpaJobClaim":
        allowed = {
            RpaDomain.LINK: {RpaOperation.LINK_BOOKING_STAGE},
            RpaDomain.SINGLE_WINDOW: {
                RpaOperation.COO_STAGE,
                RpaOperation.CIQ_STAGE,
            },
        }[self.domain]
        unsupported = set(self.supported_operations) - allowed
        if unsupported:
            rendered = "、".join(sorted(item.value for item in unsupported))
            raise ValueError(f"{self.domain.value} Worker 不能领取：{rendered}")
        return self


class RpaJobProgress(BaseModel):
    """Worker 在同一次领取期间报告关键步骤和副作用进度。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    attempt_token: UUID
    current_step: str = Field(min_length=1, max_length=128)
    side_effect_phase: RpaSideEffectPhase
    message: str = Field(min_length=1, max_length=2_000)
    page_fingerprint: str | None = Field(default=None, min_length=1, max_length=128)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)


class RpaJobResultReport(BaseModel):
    """Worker 的一次结果回报。

    ``SUCCEEDED`` 必须携带与 Operation 对应的业务结果；错误状态必须携带稳定错误码。
    更细的组合规则会复用 :class:`app.rpa.contracts.RpaResult` 再校验一次。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    callback_id: UUID
    attempt_token: UUID
    status: RpaJobStatus
    side_effect_phase: RpaSideEffectPhase
    business_outcome: RpaBusinessOutcome | None = None
    message: str = Field(min_length=1, max_length=2_000)
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    external_reference: str | None = Field(default=None, min_length=1, max_length=256)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)


class RpaReconciliationDecision(StrEnum):
    """对不确定结果或未知页面版本的人工/查询对账结论。"""

    CONFIRMED_SUCCEEDED = "CONFIRMED_SUCCEEDED"
    CONFIRMED_NO_EFFECT = "CONFIRMED_NO_EFFECT"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    CONFIRMED_FAILED = "CONFIRMED_FAILED"


class RpaJobReconcile(BaseModel):
    """把未知状态解析成明确事实；本接口本身不会再次点击外部网站。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=1)
    decision: RpaReconciliationDecision
    message: str = Field(min_length=1, max_length=2_000)
    external_reference: str | None = Field(default=None, min_length=1, max_length=256)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)


class RpaJobRequeue(BaseModel):
    """把已经明确可重试的任务重新放入队列。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)
    confirmed_safe_to_retry: bool = Field(
        default=False,
        description="WAITING_HUMAN 且曾开始外部写操作时，必须由人工明确确认不会重复",
    )


class RpaJobEventRead(BaseModel):
    """页面可展示的一条 RPA Trace。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    event_type: str
    previous_status: RpaJobStatus | None
    current_status: RpaJobStatus
    current_step: str
    message: str
    data: dict[str, object]
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def normalize_event_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class RpaJobRead(BaseModel):
    """RPA 控制台、Shipment Agent 和 Worker 共用的任务快照。"""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    contract_version: str
    case_number: str | None
    originating_run_id: str | None
    business_reference: str
    domain: RpaDomain
    operation: RpaOperation
    environment: RpaEnvironment
    side_effect_boundary: RpaSideEffectBoundary
    side_effect_phase: RpaSideEffectPhase
    adapter_key: str
    source_profile: str | None
    flow_profile: str | None
    selector_pack_version: str
    payload_schema_version: str
    business_payload_hash: str
    status: RpaJobStatus
    current_step: str
    revision: int
    attempt_count: int
    max_attempts: int
    claimed_by: str | None
    lease_expires_at: datetime | None
    next_attempt_at: datetime | None
    page_fingerprint: str | None
    business_outcome: RpaBusinessOutcome | None
    external_reference: str | None
    error_code: str | None
    error_message: str | None
    evidence: dict[str, object]
    can_auto_retry: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    events: list[RpaJobEventRead]

    @field_validator(
        "lease_expires_at",
        "next_attempt_at",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    )
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class RpaJobClaimRead(BaseModel):
    """Worker 领取响应；只有这里会返回本次 ``attempt_token``。"""

    model_config = ConfigDict(extra="forbid")

    job: RpaJobRead
    attempt_token: UUID
    idempotency_key: str
    business_payload: dict[str, JsonValue]
