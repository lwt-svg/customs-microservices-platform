"""统一 RPA Job v1 领域契约。

这一层只回答四个问题：

1. 要让哪个运行域执行哪一种业务操作；
2. 这次任务最多允许产生什么外部副作用；
3. 机器人在技术上执行到了什么状态；
4. 外部网站上是否已经形成可确认的业务结果。

第 3、4 点必须分开。例如浏览器点击“保存”后网络断开，技术状态应是
``RESULT_UNKNOWN``，不能武断地写成 ``FAILED``，也不能自动再点一次“保存”。只有重新
查询外部网站并完成对账后，才能确认是 ``SUCCEEDED`` 还是可安全重试。

本模块故意不包含 SQLAlchemy、FastAPI、Playwright、Selenium 或 LangGraph 代码。它是
上述所有层都可以共同依赖的稳定词汇表和安全规则。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

RPA_CONTRACT_VERSION: Final = "RPA_JOB_V1"


class RpaDomain(StrEnum):
    """真正运行浏览器自动化的执行域。

    ``SINGLE_WINDOW`` 内部可以有产地证和商检两个 Handler，但它们共用 Windows 会话、
    UKey、登录恢复与任务领取机制，因此不是两套独立 RPA 平台。
    """

    LINK = "LINK"
    SINGLE_WINDOW = "SINGLE_WINDOW"


class RpaOperation(StrEnum):
    """当前第一版支持的三种业务操作。"""

    LINK_BOOKING_STAGE = "LINK_BOOKING_STAGE"
    COO_STAGE = "COO_STAGE"
    CIQ_STAGE = "CIQ_STAGE"


class RpaEnvironment(StrEnum):
    """RPA 连接的目标环境。"""

    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class RpaJobStatus(StrEnum):
    """机器人执行状态，不代表外部网站已经形成哪一种业务单据。"""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_HUMAN = "WAITING_HUMAN"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FAILED = "FAILED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"
    SITE_VERSION_UNKNOWN = "SITE_VERSION_UNKNOWN"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        """这些状态不会再由普通 Worker 自动推进。"""

        return self in {
            RpaJobStatus.SUCCEEDED,
            RpaJobStatus.FAILED,
            RpaJobStatus.CANCELLED,
        }


class RpaBusinessOutcome(StrEnum):
    """外部网站已经形成并经过确认的业务结果。

    没有 Outcome 不等于业务失败，也可能是等人工登录、网站结构变化，或保存响应丢失
    后尚未完成对账。反过来，外部草稿已经保存后，即使本地截图归档失败，Outcome 也
    不能被技术 ``FAILED`` 覆盖掉；这就是业务结果与技术状态分开的意义。
    """

    LINK_DRAFT_SAVED = "LINK_DRAFT_SAVED"
    COO_STAGED = "COO_STAGED"
    CIQ_STAGED = "CIQ_STAGED"


class RpaSideEffectBoundary(StrEnum):
    """单个 Job 被授权触达的最高副作用边界。

    当前三个 ``*_STAGE`` 操作一律只能保存草稿/暂存，不能代表业务人员完成正式订舱、
    申报或签章。枚举保留另外两种边界，是为了让未来新增只读核验或正式提交操作时仍需
    显式声明权限，而不是让浏览器脚本自行决定。
    """

    READ_ONLY = "READ_ONLY"
    DRAFT_ONLY = "DRAFT_ONLY"
    FINAL_SUBMISSION = "FINAL_SUBMISSION"


class RpaSideEffectPhase(StrEnum):
    """本次执行实际已经抵达的副作用阶段。

    ``boundary`` 是授权上限，``phase`` 是运行进度，两者不能混用。比如 Job 被授权
    ``DRAFT_ONLY``，刚领取时 phase 仍是 ``READ_ONLY``；只有填写并点击保存后才进入
    ``EXTERNAL_MUTATION_STARTED``，完成外部查询对账后才是
    ``EXTERNAL_EFFECT_CONFIRMED``。
    """

    READ_ONLY = "READ_ONLY"
    LOCAL_PREPARED = "LOCAL_PREPARED"
    EXTERNAL_MUTATION_STARTED = "EXTERNAL_MUTATION_STARTED"
    EXTERNAL_EFFECT_CONFIRMED = "EXTERNAL_EFFECT_CONFIRMED"


# 持久化层使用更短的列类型名；两者是同一个枚举，不会产生第二套状态含义。
RpaEffectPhase = RpaSideEffectPhase


class RpaStateTransitionError(ValueError):
    """RPA Job 试图越过状态机时抛出。"""


class RpaSideEffectPhaseError(ValueError):
    """RPA Worker 试图把已经发生的副作用进度倒退时抛出。"""


class RpaContractMismatchError(ValueError):
    """Worker 回传结果与原 Job 身份或数据版本不一致时抛出。"""


_OPERATION_DOMAINS: Final[dict[RpaOperation, RpaDomain]] = {
    RpaOperation.LINK_BOOKING_STAGE: RpaDomain.LINK,
    RpaOperation.COO_STAGE: RpaDomain.SINGLE_WINDOW,
    RpaOperation.CIQ_STAGE: RpaDomain.SINGLE_WINDOW,
}

_OPERATION_OUTCOMES: Final[dict[RpaOperation, RpaBusinessOutcome]] = {
    RpaOperation.LINK_BOOKING_STAGE: RpaBusinessOutcome.LINK_DRAFT_SAVED,
    RpaOperation.COO_STAGE: RpaBusinessOutcome.COO_STAGED,
    RpaOperation.CIQ_STAGE: RpaBusinessOutcome.CIQ_STAGED,
}

_SIDE_EFFECT_PHASE_ORDER: Final[dict[RpaSideEffectPhase, int]] = {
    RpaSideEffectPhase.READ_ONLY: 0,
    RpaSideEffectPhase.LOCAL_PREPARED: 1,
    RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED: 2,
    RpaSideEffectPhase.EXTERNAL_EFFECT_CONFIRMED: 3,
}

# 状态表是唯一真相来源。Service、Worker 和 LangGraph 不应各写一套 if/else。
_ALLOWED_TRANSITIONS: Final[dict[RpaJobStatus, frozenset[RpaJobStatus]]] = {
    RpaJobStatus.CREATED: frozenset(
        {
            RpaJobStatus.QUEUED,
            RpaJobStatus.CANCELLED,
        }
    ),
    RpaJobStatus.QUEUED: frozenset(
        {
            RpaJobStatus.RUNNING,
            RpaJobStatus.RETRYABLE_FAILED,
            RpaJobStatus.FAILED,
            RpaJobStatus.SITE_VERSION_UNKNOWN,
            RpaJobStatus.CANCELLED,
        }
    ),
    RpaJobStatus.RUNNING: frozenset(
        {
            RpaJobStatus.WAITING_HUMAN,
            RpaJobStatus.SUCCEEDED,
            RpaJobStatus.RETRYABLE_FAILED,
            RpaJobStatus.FAILED,
            RpaJobStatus.RESULT_UNKNOWN,
            RpaJobStatus.SITE_VERSION_UNKNOWN,
            RpaJobStatus.CANCELLED,
        }
    ),
    RpaJobStatus.WAITING_HUMAN: frozenset(
        {
            RpaJobStatus.QUEUED,
            RpaJobStatus.FAILED,
            RpaJobStatus.CANCELLED,
        }
    ),
    RpaJobStatus.RETRYABLE_FAILED: frozenset(
        {
            RpaJobStatus.QUEUED,
            RpaJobStatus.WAITING_HUMAN,
            RpaJobStatus.FAILED,
            RpaJobStatus.CANCELLED,
        }
    ),
    # 响应未知时绝不能直接回到队列。必须先通过外部查询/人工对账，把它解析成明确成功、
    # 明确失败，或 RETRYABLE_FAILED，之后才可能再次执行写操作。
    RpaJobStatus.RESULT_UNKNOWN: frozenset(
        {
            RpaJobStatus.WAITING_HUMAN,
            RpaJobStatus.SUCCEEDED,
            RpaJobStatus.RETRYABLE_FAILED,
            RpaJobStatus.FAILED,
            RpaJobStatus.CANCELLED,
        }
    ),
    # 网站 DOM 版本未知同样不自动重试；先暂停给人工确认或升级 SelectorPack。
    RpaJobStatus.SITE_VERSION_UNKNOWN: frozenset(
        {
            RpaJobStatus.WAITING_HUMAN,
            RpaJobStatus.FAILED,
            RpaJobStatus.CANCELLED,
        }
    ),
    RpaJobStatus.SUCCEEDED: frozenset(),
    RpaJobStatus.FAILED: frozenset(),
    RpaJobStatus.CANCELLED: frozenset(),
}

_REPORTABLE_RESULT_STATUSES: Final[frozenset[RpaJobStatus]] = frozenset(
    {
        RpaJobStatus.WAITING_HUMAN,
        RpaJobStatus.SUCCEEDED,
        RpaJobStatus.RETRYABLE_FAILED,
        RpaJobStatus.FAILED,
        RpaJobStatus.RESULT_UNKNOWN,
        RpaJobStatus.SITE_VERSION_UNKNOWN,
        RpaJobStatus.CANCELLED,
    }
)

_ERROR_RESULT_STATUSES: Final[frozenset[RpaJobStatus]] = frozenset(
    {
        RpaJobStatus.RETRYABLE_FAILED,
        RpaJobStatus.FAILED,
        RpaJobStatus.RESULT_UNKNOWN,
        RpaJobStatus.SITE_VERSION_UNKNOWN,
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_to_utc(value: datetime) -> datetime:
    """兼容数据库常见的 naive UTC 时间，同时保证新对象始终输出带时区时间。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_business_payload(
    payload: Mapping[str, JsonValue] | BaseModel,
) -> str:
    """生成与字典键顺序无关的业务数据 SHA-256。

    哈希对象只应包含“这次外部操作的业务事实”，不应包含 ``job_id``、创建时间或
    Worker 领取时间。这样同一业务命令即使因客户端重放生成了不同 ``job_id``，仍会
    得到同一个幂等标识。
    """

    raw_payload: object
    if isinstance(payload, BaseModel):
        raw_payload = payload.model_dump(mode="json")
    else:
        raw_payload = dict(payload)

    try:
        canonical_json = json.dumps(
            raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("business_payload 必须是可稳定序列化的 JSON 对象") from error
    return _sha256_text(canonical_json)


def build_rpa_idempotency_key(
    *,
    domain: RpaDomain,
    operation: RpaOperation,
    environment: RpaEnvironment,
    business_reference: str,
    side_effect_boundary: RpaSideEffectBoundary,
    business_payload_hash: str,
) -> str:
    """根据业务身份生成稳定幂等键，而不是使用随机 Job ID。

    明文前缀方便排查运行域和操作类型；具体业务编号进入摘要，避免把客户编号直接带进
    Worker 日志或外部请求 Header。
    """

    material = (
        f"{RPA_CONTRACT_VERSION}\n"
        f"{domain.value}\n"
        f"{operation.value}\n"
        f"{environment.value}\n"
        f"{business_reference}\n"
        f"{side_effect_boundary.value}\n"
        f"{business_payload_hash}"
    )
    digest = _sha256_text(material)
    key = f"RPA-V1-{domain.value}-{operation.value}-{digest}"
    if len(key) > 128:  # 当前枚举不会触发；保留防线，避免未来扩展悄悄破坏存储契约。
        raise ValueError("生成的 RPA 幂等键超过 128 个字符")
    return key


def can_automatically_retry(status: RpaJobStatus) -> bool:
    """只有明确标记为可重试的失败才允许自动重新入队。"""

    return status is RpaJobStatus.RETRYABLE_FAILED


def assert_rpa_job_transition(
    current: RpaJobStatus,
    target: RpaJobStatus,
) -> None:
    """验证一次状态迁移。

    同状态回放视为幂等 no-op。例如 Worker 的成功回调因网络原因发送两次时，不应把
    第二次回调误判成非法业务操作。
    """

    if current is target:
        return
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise RpaStateTransitionError(f"RPA Job 不允许从 {current.value} 迁移到 {target.value}")


def assert_rpa_side_effect_phase_transition(
    current: RpaSideEffectPhase,
    target: RpaSideEffectPhase,
) -> None:
    """副作用阶段可跳过丢失的心跳向前推进，但不能倒退。"""

    if _SIDE_EFFECT_PHASE_ORDER[target] < _SIDE_EFFECT_PHASE_ORDER[current]:
        raise RpaSideEffectPhaseError(
            f"RPA 副作用阶段不允许从 {current.value} 倒退到 {target.value}"
        )


class _StrictContract(BaseModel):
    """所有 RPA 消息共用的边界规则。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class RpaJob(_StrictContract):
    """发送给 RPA Worker 的稳定命令信封。

    ``business_payload_hash`` 和 ``idempotency_key`` 是派生字段。调用方可以在反序列化
    已持久化消息时携带它们，但只要值与业务数据不一致，模型就会拒绝加载，防止数据库
    快照、消息正文和幂等键各说各话。
    """

    contract_version: Literal["RPA_JOB_V1"] = RPA_CONTRACT_VERSION
    job_id: UUID = Field(default_factory=uuid4)
    domain: RpaDomain
    operation: RpaOperation
    environment: RpaEnvironment = RpaEnvironment.SANDBOX
    business_reference: str = Field(min_length=1, max_length=128)
    side_effect_boundary: RpaSideEffectBoundary = RpaSideEffectBoundary.DRAFT_ONLY
    business_payload: dict[str, JsonValue]
    business_payload_hash: str = Field(default="", max_length=64)
    idempotency_key: str = Field(default="", max_length=128)
    status: RpaJobStatus = RpaJobStatus.CREATED
    side_effect_phase: RpaSideEffectPhase = RpaSideEffectPhase.READ_ONLY
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _normalize_to_utc(value)

    @model_validator(mode="after")
    def validate_operation_and_derived_identity(self) -> Self:
        """校验路由、安全边界，并生成不可由调用方伪造的派生身份。"""

        expected_domain = _OPERATION_DOMAINS[self.operation]
        if self.domain is not expected_domain:
            raise ValueError(f"{self.operation.value} 必须由 {expected_domain.value} 域执行")

        # v1 的三个动作都只做到“草稿/暂存”，最终提交必须由业务人员确认。若以后新增
        # 正式提交动作，应增加新的 Operation 并单独评审，而不是放宽这里的规则。
        if self.side_effect_boundary is not RpaSideEffectBoundary.DRAFT_ONLY:
            raise ValueError(f"{self.operation.value} 只允许 DRAFT_ONLY 副作用边界")

        actual_payload_hash = hash_business_payload(self.business_payload)
        if self.business_payload_hash and self.business_payload_hash != actual_payload_hash:
            raise ValueError("business_payload_hash 与 business_payload 不一致")

        actual_idempotency_key = build_rpa_idempotency_key(
            domain=self.domain,
            operation=self.operation,
            environment=self.environment,
            business_reference=self.business_reference,
            side_effect_boundary=self.side_effect_boundary,
            business_payload_hash=actual_payload_hash,
        )
        if self.idempotency_key and self.idempotency_key != actual_idempotency_key:
            raise ValueError("idempotency_key 与当前 RPA 业务命令不一致")

        # frozen=True 禁止模型创建完成后的普通赋值；校验阶段只写入两个确定性派生字段。
        object.__setattr__(self, "business_payload_hash", actual_payload_hash)
        object.__setattr__(self, "idempotency_key", actual_idempotency_key)
        return self

    @property
    def expected_business_outcome(self) -> RpaBusinessOutcome:
        """当前操作成功后唯一允许报告的业务结果。"""

        return _OPERATION_OUTCOMES[self.operation]

    @property
    def can_auto_retry(self) -> bool:
        return can_automatically_retry(self.status)

    def transition_to(self, target: RpaJobStatus) -> Self:
        """返回迁移后的新快照；原对象保持不变。"""

        assert_rpa_job_transition(self.status, target)
        if target is self.status:
            return self
        return self.model_copy(update={"status": target})

    def advance_side_effect_phase(self, target: RpaSideEffectPhase) -> Self:
        """返回副作用进度向前推进后的新快照；原对象保持不变。"""

        assert_rpa_side_effect_phase_transition(self.side_effect_phase, target)
        if target is self.side_effect_phase:
            return self
        return self.model_copy(update={"side_effect_phase": target})

    def apply_result(self, result: RpaResult) -> Self:
        """校验 Worker 结果确实属于本 Job，再返回新的状态快照。"""

        mismatches: list[str] = []
        if result.job_id != self.job_id:
            mismatches.append("job_id")
        if result.operation is not self.operation:
            mismatches.append("operation")
        if result.idempotency_key != self.idempotency_key:
            mismatches.append("idempotency_key")
        if result.business_payload_hash != self.business_payload_hash:
            mismatches.append("business_payload_hash")
        if mismatches:
            raise RpaContractMismatchError("RPA 结果与 Job 不匹配: " + ", ".join(mismatches))
        assert_rpa_job_transition(self.status, result.status)
        assert_rpa_side_effect_phase_transition(
            self.side_effect_phase,
            result.side_effect_phase,
        )
        return self.model_copy(
            update={
                "status": result.status,
                "side_effect_phase": result.side_effect_phase,
            }
        )


class RpaResult(_StrictContract):
    """RPA Worker 回传的执行结果。

    ``status`` 是技术执行状态，``business_outcome`` 是已确认的外部业务事实。只有
    ``SUCCEEDED`` 才能携带 Outcome；特别是 ``RESULT_UNKNOWN`` 绝不能声称草稿已保存。
    """

    contract_version: Literal["RPA_JOB_V1"] = RPA_CONTRACT_VERSION
    job_id: UUID
    operation: RpaOperation
    idempotency_key: str = Field(min_length=1, max_length=128)
    business_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RpaJobStatus
    side_effect_phase: RpaSideEffectPhase = RpaSideEffectPhase.READ_ONLY
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
    reported_at: datetime = Field(default_factory=_utc_now)

    @field_validator("reported_at")
    @classmethod
    def normalize_reported_at(cls, value: datetime) -> datetime:
        return _normalize_to_utc(value)

    @model_validator(mode="after")
    def validate_status_and_business_outcome(self) -> Self:
        """禁止把技术异常包装成已完成的业务结果。"""

        if self.status not in _REPORTABLE_RESULT_STATUSES:
            raise ValueError(f"{self.status.value} 不是 Worker 可回传的结果状态")

        expected_outcome = _OPERATION_OUTCOMES[self.operation]
        if self.status is RpaJobStatus.SUCCEEDED:
            if self.business_outcome is not expected_outcome:
                raise ValueError(f"{self.operation.value} 成功时必须返回 {expected_outcome.value}")
            if self.error_code is not None:
                raise ValueError("SUCCEEDED 结果不能携带 error_code")
        elif self.business_outcome is not None:
            raise ValueError("只有 SUCCEEDED 结果可以携带 business_outcome")

        if self.status in _ERROR_RESULT_STATUSES and self.error_code is None:
            raise ValueError(f"{self.status.value} 结果必须提供 error_code")

        if (
            self.retry_after_seconds is not None
            and self.status is not RpaJobStatus.RETRYABLE_FAILED
        ):
            raise ValueError("只有 RETRYABLE_FAILED 可以提供 retry_after_seconds")
        return self

    @property
    def can_auto_retry(self) -> bool:
        return can_automatically_retry(self.status)
