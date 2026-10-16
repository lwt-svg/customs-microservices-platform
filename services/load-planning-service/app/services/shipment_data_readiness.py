"""Shipment DATA_READINESS 的纯确定性评估规则。

本模块不调用 LLM，也不写数据库。它把装载任务主数据和案件级补充事实转换成稳定的
问题列表；相同输入一定得到相同 ``snapshot_hash``。领域 Service 负责事务、Trace、
人工中断和把结果投影到 Shipment Agent 运行。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.load_planning import LoadPlanningTask, SourceType
from app.models.shipment_data_readiness import (
    ShipmentDataFactSourceType,
    ShipmentDataFactType,
    ShipmentDataReadinessStatus,
    ShipmentDataSourceMode,
    ShipmentDataSubjectType,
)
from app.schemas.shipment_data_readiness import (
    ShipmentDataIssueSeverity,
    ShipmentDataOwnerRole,
    ShipmentDataReadinessIssueCode,
    ShipmentDataReadinessIssueRead,
    ShipmentDataRequestedFactRead,
    ShipmentDataResolution,
)

SHIPMENT_DATA_READINESS_POLICY_VERSION = "SHIPMENT_DATA_READINESS_POLICY_V1"


@dataclass(frozen=True, slots=True)
class ShipmentDataReadinessAssessment:
    """一次纯规则评估的完整结果。"""

    status: ShipmentDataReadinessStatus
    source_mode: ShipmentDataSourceMode
    snapshot_hash: str
    issues: tuple[ShipmentDataReadinessIssueRead, ...]
    requested_facts: tuple[ShipmentDataRequestedFactRead, ...]
    fact_keys: tuple[str, ...]
    source_types: tuple[str, ...]


def _utc_datetime(value: object) -> datetime | None:
    """把 JSON 中的 ISO 时间安全解析成 UTC；坏历史数据视为没有时间证据。"""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _fact_key(
    subject_type: ShipmentDataSubjectType,
    subject_code: str,
    fact_type: ShipmentDataFactType,
) -> tuple[str, str, str]:
    return subject_type.value, subject_code, fact_type.value


def _fact_key_text(key: tuple[str, str, str]) -> str:
    return ":".join(key)


def _latest_facts(
    facts: list[dict[str, object]],
) -> dict[tuple[str, str, str], dict[str, object]]:
    """按追加顺序选出每个事实键的最新记录。

    服务端只会追加记录，但升级前后或手工恢复数据库时仍可能遇到格式不完整的 JSON。
    评估器跳过无法识别的行，不让一条坏审计记录导致整个 Agent 500。
    """

    latest: dict[tuple[str, str, str], dict[str, object]] = {}
    for fact in facts:
        try:
            subject_type = ShipmentDataSubjectType(str(fact["subject_type"]))
            subject_code = str(fact["subject_code"])
            fact_type = ShipmentDataFactType(str(fact["fact_type"]))
            ShipmentDataFactSourceType(str(fact["source_type"]))
        except (KeyError, TypeError, ValueError):
            continue
        latest[_fact_key(subject_type, subject_code, fact_type)] = fact
    return latest


def _is_stale(fact: dict[str, object], now: datetime) -> bool:
    expires_at = _utc_datetime(fact.get("expires_at"))
    return expires_at is not None and expires_at <= now


def _issue_id(
    issue_code: ShipmentDataReadinessIssueCode,
    subject_type: ShipmentDataSubjectType,
    subject_code: str,
    fact_type: ShipmentDataFactType,
) -> str:
    # EVIDENCE_STALE 可能同时发生在同一 Cargo 的库存和主数据确认上，需要 fact_type
    # 才能保持 issue_id 唯一；其余代码本身已经唯一指向一种事实。
    base = f"{issue_code.value}:{subject_type.value}:{subject_code}"
    if issue_code is ShipmentDataReadinessIssueCode.EVIDENCE_STALE:
        return f"{base}:{fact_type.value}"
    return base


def _issue(
    *,
    issue_code: ShipmentDataReadinessIssueCode,
    subject_type: ShipmentDataSubjectType,
    subject_code: str,
    fact_type: ShipmentDataFactType,
    required_value: int | bool | str | None,
    actual_value: int | bool | str | None,
    unit: str,
    message: str,
) -> ShipmentDataReadinessIssueRead:
    return ShipmentDataReadinessIssueRead(
        issue_id=_issue_id(issue_code, subject_type, subject_code, fact_type),
        issue_code=issue_code,
        severity=ShipmentDataIssueSeverity.BLOCKING,
        subject_type=subject_type,
        subject_code=subject_code,
        fact_type=fact_type,
        required_value=required_value,
        actual_value=actual_value,
        unit=unit,
        message=message,
        resolution=ShipmentDataResolution.SUPPLY_DATA,
        owner_role=ShipmentDataOwnerRole.WAREHOUSE_OPERATOR,
    )


def _master_confirmation_issue(
    *,
    subject_type: ShipmentDataSubjectType,
    subject_code: str,
    display_name: str,
    fact: dict[str, object] | None,
    now: datetime,
) -> ShipmentDataReadinessIssueRead | None:
    fact_type = ShipmentDataFactType.MASTER_DATA_CONFIRMED
    if fact is not None and _is_stale(fact, now):
        return _issue(
            issue_code=ShipmentDataReadinessIssueCode.EVIDENCE_STALE,
            subject_type=subject_type,
            subject_code=subject_code,
            fact_type=fact_type,
            required_value=True,
            actual_value=(fact.get("value") if type(fact.get("value")) is bool else None),
            unit="BOOLEAN",
            message=f"{display_name} 的主数据确认凭证已过期，请重新核对。",
        )
    if fact is None or fact.get("value") is not True:
        return _issue(
            issue_code=ShipmentDataReadinessIssueCode.MASTER_DATA_UNCONFIRMED,
            subject_type=subject_type,
            subject_code=subject_code,
            fact_type=fact_type,
            required_value=True,
            actual_value=(
                fact.get("value") if fact is not None and type(fact.get("value")) is bool else None
            ),
            unit="BOOLEAN",
            message=f"{display_name} 尚未由业务人员确认主数据。",
        )
    return None


def _source_mode(
    task: LoadPlanningTask | None,
    latest: dict[tuple[str, str, str], dict[str, object]],
) -> ShipmentDataSourceMode:
    if task is None:
        return ShipmentDataSourceMode.NOT_APPLICABLE
    master_has_demo = any(
        value.source_type is SourceType.SYNTHETIC_DEMO
        for value in [*task.cargo_items, *task.pallet_specs]
    )
    facts_have_demo = any(
        fact.get("source_type") == ShipmentDataFactSourceType.SYNTHETIC_DEMO.value
        for fact in latest.values()
    )
    return (
        ShipmentDataSourceMode.DEMO
        if master_has_demo or facts_have_demo
        else ShipmentDataSourceMode.OPERATIONAL
    )


def _canonical_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_shipment_data_readiness(
    *,
    task: LoadPlanningTask | None,
    facts: list[dict[str, object]],
    now: datetime,
) -> ShipmentDataReadinessAssessment:
    """评估库存、主数据确认和证据有效期。

    ``task=None`` 表示本票不需要装载能力（例如 BOOKING_ONLY），因此直接返回
    ``READY / NOT_APPLICABLE``。承重、层数、堆码方向等安全约束仍由装载子 Agent
    负责，本门禁不会重复询问。
    """

    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)

    latest = _latest_facts(facts)
    source_mode = _source_mode(task, latest)
    issues: list[ShipmentDataReadinessIssueRead] = []

    if task is not None:
        for cargo in task.cargo_items:
            subject_type = ShipmentDataSubjectType.CARGO_ITEM
            quantity_key = _fact_key(
                subject_type,
                cargo.item_code,
                ShipmentDataFactType.AVAILABLE_QUANTITY,
            )
            quantity_fact = latest.get(quantity_key)
            if quantity_fact is None:
                issues.append(
                    _issue(
                        issue_code=(ShipmentDataReadinessIssueCode.AVAILABLE_QUANTITY_MISSING),
                        subject_type=subject_type,
                        subject_code=cargo.item_code,
                        fact_type=ShipmentDataFactType.AVAILABLE_QUANTITY,
                        required_value=cargo.quantity,
                        actual_value=None,
                        unit="CARTON",
                        message=(
                            f"{cargo.item_code} 计划装载 {cargo.quantity} 箱，"
                            "尚未提供可用库存数量。"
                        ),
                    )
                )
            elif _is_stale(quantity_fact, now):
                raw_value = quantity_fact.get("value")
                issues.append(
                    _issue(
                        issue_code=ShipmentDataReadinessIssueCode.EVIDENCE_STALE,
                        subject_type=subject_type,
                        subject_code=cargo.item_code,
                        fact_type=ShipmentDataFactType.AVAILABLE_QUANTITY,
                        required_value=cargo.quantity,
                        actual_value=raw_value if type(raw_value) is int else None,
                        unit="CARTON",
                        message=f"{cargo.item_code} 的库存凭证已过期，请重新盘点。",
                    )
                )
            else:
                raw_value = quantity_fact.get("value")
                if type(raw_value) is not int or raw_value < cargo.quantity:
                    issues.append(
                        _issue(
                            issue_code=(ShipmentDataReadinessIssueCode.INVENTORY_SHORTAGE),
                            subject_type=subject_type,
                            subject_code=cargo.item_code,
                            fact_type=ShipmentDataFactType.AVAILABLE_QUANTITY,
                            required_value=cargo.quantity,
                            actual_value=raw_value if type(raw_value) is int else None,
                            unit="CARTON",
                            message=(
                                f"{cargo.item_code} 计划装载 {cargo.quantity} 箱，"
                                f"当前仅确认 {raw_value if type(raw_value) is int else 0} 箱。"
                            ),
                        )
                    )

            if not cargo.verified:
                confirmation = latest.get(
                    _fact_key(
                        subject_type,
                        cargo.item_code,
                        ShipmentDataFactType.MASTER_DATA_CONFIRMED,
                    )
                )
                issue = _master_confirmation_issue(
                    subject_type=subject_type,
                    subject_code=cargo.item_code,
                    display_name=f"货物 {cargo.item_code}",
                    fact=confirmation,
                    now=now,
                )
                if issue is not None:
                    issues.append(issue)

        for pallet in task.pallet_specs:
            if pallet.verified:
                continue
            subject_type = ShipmentDataSubjectType.PALLET_SPEC
            confirmation = latest.get(
                _fact_key(
                    subject_type,
                    pallet.pallet_code,
                    ShipmentDataFactType.MASTER_DATA_CONFIRMED,
                )
            )
            issue = _master_confirmation_issue(
                subject_type=subject_type,
                subject_code=pallet.pallet_code,
                display_name=f"托盘 {pallet.pallet_code}",
                fact=confirmation,
                now=now,
            )
            if issue is not None:
                issues.append(issue)

    requested: list[ShipmentDataRequestedFactRead] = []
    seen_requested: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = _fact_key(
            issue.subject_type,
            issue.subject_code,
            issue.fact_type,
        )
        if key in seen_requested:
            continue
        seen_requested.add(key)
        requested.append(
            ShipmentDataRequestedFactRead(
                subject_type=issue.subject_type,
                subject_code=issue.subject_code,
                fact_type=issue.fact_type,
            )
        )

    status = (
        ShipmentDataReadinessStatus.ACTION_REQUIRED
        if issues
        else (
            ShipmentDataReadinessStatus.DEMO_READY
            if source_mode is ShipmentDataSourceMode.DEMO
            else ShipmentDataReadinessStatus.READY
        )
    )

    # 哈希包含规则真正使用的值，但 API 和 Trace 只暴露哈希本身。
    hash_payload = {
        "policy_version": SHIPMENT_DATA_READINESS_POLICY_VERSION,
        "task_number": task.task_number if task is not None else None,
        "task_inputs": (
            {
                "cargo_items": [
                    {
                        "item_code": item.item_code,
                        "quantity": item.quantity,
                        "verified": item.verified,
                        "source_type": item.source_type.value,
                    }
                    for item in task.cargo_items
                ],
                "pallet_specs": [
                    {
                        "pallet_code": pallet.pallet_code,
                        "quantity": pallet.quantity,
                        "verified": pallet.verified,
                        "source_type": pallet.source_type.value,
                    }
                    for pallet in task.pallet_specs
                ],
            }
            if task is not None
            else None
        ),
        "latest_facts": [
            {
                "key": _fact_key_text(key),
                "value": fact.get("value"),
                "source_type": fact.get("source_type"),
                "observed_at": fact.get("observed_at"),
                "expires_at": fact.get("expires_at"),
            }
            for key, fact in sorted(latest.items())
        ],
        "issue_ids": [issue.issue_id for issue in issues],
        "source_mode": source_mode.value,
        "status": status.value,
    }
    return ShipmentDataReadinessAssessment(
        status=status,
        source_mode=source_mode,
        snapshot_hash=_canonical_hash(hash_payload),
        issues=tuple(issues),
        requested_facts=tuple(requested),
        fact_keys=tuple(_fact_key_text(key) for key in sorted(latest)),
        source_types=tuple(
            sorted(
                {
                    str(fact["source_type"])
                    for fact in latest.values()
                    if fact.get("source_type") is not None
                }
            )
        ),
    )
