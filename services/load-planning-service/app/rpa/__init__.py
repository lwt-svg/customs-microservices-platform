"""RPA 控制面的纯领域契约。

这里不依赖数据库、HTTP 或具体浏览器驱动。后续 LINK Playwright Worker、单一窗口
Windows Worker 和 Shipment LangGraph 都应复用同一份契约，避免各自发明状态含义。
"""

from app.rpa.contracts import (
    RPA_CONTRACT_VERSION,
    RpaBusinessOutcome,
    RpaContractMismatchError,
    RpaDomain,
    RpaEffectPhase,
    RpaEnvironment,
    RpaJob,
    RpaJobStatus,
    RpaOperation,
    RpaResult,
    RpaSideEffectBoundary,
    RpaSideEffectPhase,
    RpaSideEffectPhaseError,
    RpaStateTransitionError,
    assert_rpa_job_transition,
    assert_rpa_side_effect_phase_transition,
    build_rpa_idempotency_key,
    can_automatically_retry,
    hash_business_payload,
)

__all__ = [
    "RPA_CONTRACT_VERSION",
    "RpaBusinessOutcome",
    "RpaContractMismatchError",
    "RpaDomain",
    "RpaEffectPhase",
    "RpaEnvironment",
    "RpaJob",
    "RpaJobStatus",
    "RpaOperation",
    "RpaResult",
    "RpaSideEffectBoundary",
    "RpaSideEffectPhase",
    "RpaSideEffectPhaseError",
    "RpaStateTransitionError",
    "assert_rpa_job_transition",
    "assert_rpa_side_effect_phase_transition",
    "build_rpa_idempotency_key",
    "can_automatically_retry",
    "hash_business_payload",
]
