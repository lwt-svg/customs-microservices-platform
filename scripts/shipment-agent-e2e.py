#!/usr/bin/env python3
"""验证 Shipment Operations Agent 的本机真实编排闭环。

这条脚本覆盖的不是一组彼此孤立的接口，而是一票 ``SEA_FCL`` 发运案件从准备资源到
正式结案的完整路径：

1. 通过 Java BFF ``:8080`` 创建合成装载任务、LINK 草稿和 Shipment Case；
2. 绑定两个业务资源并启动 Shipment 总 Agent；
3. 数据就绪 Agent 发现库存和主数据证据缺失，生成结构化业务待办；
4. 业务人员分两轮补数，证明 Agent 会自动重评、继续追问并从中断点恢复；
5. 人工批准案件激活；
6. 在装载专业工作台对应的 API 中批准候选方案并发布不可变 V1；
7. 由 8200 进程内 Worker 自动发现正式装载 V1，再人工批准 LINK 提交；
8. 通过固定的本机模拟承运人 ``:8100`` 开始处理并确认；
9. Worker 自动发现 LINK 已确认，人工批准结案，断言 Agent、案件和 Trace 均已闭环。

业务系统 API 只允许访问 ``http://127.0.0.1:8080/api/v1/platform/*``。模拟承运人客户端
只允许访问 ``http://127.0.0.1:8100/api/v1/portal/*``，不会调用承运人平台接单入口，更
不会读取环境变量中的外部 URL。所有业务编号均由脚本生成，LINK 编号强制使用 ``DEMO-``
前缀，装载数据来源固定为 ``SYNTHETIC_DEMO``。

完整模式要求先运行 ``./scripts/run-local.sh full``。脚本不会后台死循环：等待承运人
回调和自动唤醒时只在本机 E2E 进程内做有上限的 GET 轮询；Shipment Agent 由跟随 8200
FastAPI 生命周期运行的 Worker 自动恢复，不占用服务端请求线程等待外部系统。显式
``reconcile`` 仍保留为故障排查时的人工兜底，但不再属于正常业务链路。
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

BFF_BASE_URL = "http://127.0.0.1:8080"
CARRIER_SIMULATOR_BASE_URL = "http://127.0.0.1:8100"

LOAD_TASKS_PATH = "/api/v1/platform/load-planning/tasks"
LOAD_AGENT_RUNS_PATH = "/api/v1/platform/load-planning/agent-runs"
LINK_APPLICATIONS_PATH = "/api/v1/platform/link-booking/applications"
SHIPMENT_CASES_PATH = "/api/v1/platform/shipment-cases"
SHIPMENT_AGENT_RUNS_PATH = "/api/v1/platform/shipment-agent-runs"
CARRIER_PORTAL_BOOKINGS_PATH = "/api/v1/portal/bookings"


class E2EError(RuntimeError):
    """用户可读、且不会包含请求体、响应全文或本机环境变量的验收错误。"""


class NetworkOutcomeUnknown(E2EError):
    """写请求可能已经到达服务端，但客户端没有收到完整响应。"""


class HttpStatusError(E2EError):
    """服务端明确返回了非预期 HTTP 状态。"""

    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"{path} 明确返回 HTTP {status}")
        self.status = status
        self.path = path


class ContractError(E2EError):
    """HTTP 成功，但 JSON 契约或业务结果不符合闭环预期。"""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝重定向，防止固定的本机验收请求被带到其他地址。"""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl


class JsonClient:
    """固定 base URL、限制路径前缀的极小 JSON 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        allowed_path_prefix: str,
        bff_envelope: bool,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url
        self.allowed_path_prefix = allowed_path_prefix
        self.bff_envelope = bff_envelope
        self.timeout_seconds = timeout_seconds
        self.opener = urllib.request.build_opener(NoRedirectHandler())

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_statuses: Iterable[int] = (200,),
    ) -> Any:
        """发送 JSON 请求，并按客户端类型解开 Java BFF 的统一响应外壳。"""

        if not path.startswith(self.allowed_path_prefix):
            raise ContractError(f"脚本拒绝访问允许范围以外的本机路径：{path}")

        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        body = None
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # HTTPError 已经给出明确结果。关闭且不读取正文，避免把内部诊断或未来可能
            # 出现的敏感字段打印到终端。
            exc.close()
            raise HttpStatusError(int(exc.code), path) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
            OSError,
        ) as exc:
            raise NetworkOutcomeUnknown(f"{path} 的网络响应未知") from exc

        if status not in set(expected_statuses):
            raise ContractError(f"{path} 返回了未预期的 HTTP {status}")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"{path} 没有返回有效 JSON") from exc

        if not self.bff_envelope:
            return decoded
        if not isinstance(decoded, dict):
            raise ContractError(f"{path} 没有返回 Java BFF JSON 对象")
        if decoded.get("code") != 0 or decoded.get("message") != "success":
            raise ContractError(f"{path} 没有返回成功的 Java BFF 外壳")
        if "data" not in decoded:
            raise ContractError(f"{path} 的 Java BFF 外壳缺少 data")
        return decoded["data"]


class BffClient(JsonClient):
    """只允许访问 Java BFF 平台 API。"""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        super().__init__(
            base_url=BFF_BASE_URL,
            allowed_path_prefix="/api/v1/platform/",
            bff_envelope=True,
            timeout_seconds=timeout_seconds,
        )


class CarrierPortalClient(JsonClient):
    """只允许操作本机模拟承运人的人工门户 API。"""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        super().__init__(
            base_url=CARRIER_SIMULATOR_BASE_URL,
            allowed_path_prefix="/api/v1/portal/",
            bff_envelope=False,
            timeout_seconds=timeout_seconds,
        )


def log(message: str, *, quiet: bool = False) -> None:
    """输出不含请求体和响应全文的短步骤信息。"""

    if not quiet:
        print(f"[shipment-agent-e2e] {message}", flush=True)


def require(condition: bool, message: str) -> None:
    """把普通断言统一转换成用户可读的契约错误。"""

    if not condition:
        raise ContractError(message)


def require_object(value: Any, message: str) -> dict[str, Any]:
    """确认一个 JSON 值是对象。"""

    require(isinstance(value, dict), message)
    return value


def require_positive_int(value: Any, message: str) -> int:
    """读取正整数，并拒绝 Python 把 bool 当成 int 的情况。"""

    require(type(value) is int and value > 0, message)
    return value


def idempotency_key(scope: str) -> str:
    """生成不会与其他本机 E2E 命令碰撞的请求幂等键。"""

    return f"shipment-agent-e2e-{scope}-{uuid.uuid4()}"


def build_load_task_payload(task_number: str) -> dict[str, Any]:
    """构造约束完整、可稳定生成候选方案的脱敏装载任务。"""

    return {
        "taskNumber": task_number,
        "taskName": "Shipment Agent 本机闭环验收",
        "description": "仅用于本机总编排 Agent E2E 的合成装载数据。",
        "cargoItems": [
            {
                "itemCode": "AGENT-E2E-ITEM-A",
                "skuCode": "AGENT-E2E-SKU-A",
                "cargoName": "合成测试纸箱 A",
                "quantity": 24,
                "lengthMm": 300,
                "widthMm": 200,
                "heightMm": 180,
                "grossWeightKg": 8,
                "maxStackLayers": 5,
                "maxTopLoadKg": 160,
                "stackable": True,
                "keepUpright": True,
                "fragile": False,
                "sourceType": "SYNTHETIC_DEMO",
                "sourceReference": None,
                "sourceLocator": None,
                "confidence": 1,
                "verified": False,
            },
            {
                "itemCode": "AGENT-E2E-ITEM-B",
                "skuCode": "AGENT-E2E-SKU-B",
                "cargoName": "合成测试纸箱 B",
                "quantity": 12,
                "lengthMm": 250,
                "widthMm": 250,
                "heightMm": 200,
                "grossWeightKg": 10,
                "maxStackLayers": 4,
                "maxTopLoadKg": 120,
                "stackable": True,
                "keepUpright": True,
                "fragile": False,
                "sourceType": "SYNTHETIC_DEMO",
                "sourceReference": None,
                "sourceLocator": None,
                "confidence": 1,
                "verified": False,
            },
        ],
        "palletSpecs": [
            {
                "palletCode": "AGENT-E2E-PALLET",
                "palletName": "合成测试 1200×1000 托盘",
                "quantity": 12,
                "lengthMm": 1200,
                "widthMm": 1000,
                "heightMm": 150,
                "tareWeightKg": 25,
                "maxLoadKg": 500,
                "maxTotalHeightMm": 1250,
                "sourceType": "SYNTHETIC_DEMO",
                "sourceReference": None,
                "sourceLocator": None,
                "confidence": 1,
                "verified": False,
            }
        ],
    }


def build_link_draft_payload(application_number: str) -> dict[str, Any]:
    """构造只能进入本地模拟承运人链路的 LINK 草稿。"""

    requested_etd = (datetime.now(timezone.utc).date() + timedelta(days=14)).isoformat()
    return {
        "applicationNumber": application_number,
        "originPort": "DEMO-SHA",
        "destinationPort": "DEMO-LAX",
        "transportMode": "SEA",
        "containerType": "40HQ",
        "requestedEtd": requested_etd,
    }


def build_case_payload(case_number: str) -> dict[str, Any]:
    """构造同时需要装载与 LINK 两种能力的 SEA_FCL 案件。"""

    return {
        "caseNumber": case_number,
        "caseName": "上海至洛杉矶 Shipment Agent 合成闭环",
        "transportScenario": "SEA_FCL",
        "description": "仅用于本机 Shipment Operations Agent E2E 验收。",
    }


def readiness_fact(
    *,
    subject_type: str,
    subject_code: str,
    fact_type: str,
    value: int | bool,
    locator: str,
) -> dict[str, Any]:
    """构造一条明确标注为合成数据的发运就绪事实。

    ``CargoItem.quantity`` 是计划装载需求，不是真实库存。E2E 通过独立 readiness
    事实模拟仓库回填，验证总 Agent 不会把装载计划数量直接当作可用库存。
    """

    observed_at = datetime.now(timezone.utc)
    return {
        "subjectType": subject_type,
        "subjectCode": subject_code,
        "factType": fact_type,
        "value": value,
        "sourceType": "SYNTHETIC_DEMO",
        "sourceReference": "shipment-agent-e2e",
        "sourceLocator": locator,
        "observedAt": observed_at.isoformat(),
        "expiresAt": (observed_at + timedelta(hours=1)).isoformat(),
    }


def first_readiness_remediation_facts() -> list[dict[str, Any]]:
    """第一轮故意留下缺货和缺数，让 Agent 再次生成精准待办。"""

    return [
        readiness_fact(
            subject_type="CARGO_ITEM",
            subject_code="AGENT-E2E-ITEM-A",
            fact_type="MASTER_DATA_CONFIRMED",
            value=True,
            locator="generated:master-data-item-a",
        ),
        readiness_fact(
            subject_type="CARGO_ITEM",
            subject_code="AGENT-E2E-ITEM-B",
            fact_type="MASTER_DATA_CONFIRMED",
            value=True,
            locator="generated:master-data-item-b",
        ),
        readiness_fact(
            subject_type="PALLET_SPEC",
            subject_code="AGENT-E2E-PALLET",
            fact_type="MASTER_DATA_CONFIRMED",
            value=True,
            locator="generated:master-data-pallet",
        ),
        readiness_fact(
            subject_type="CARGO_ITEM",
            subject_code="AGENT-E2E-ITEM-A",
            fact_type="AVAILABLE_QUANTITY",
            value=8,
            locator="generated:inventory-item-a-first",
        ),
    ]


def final_readiness_remediation_facts() -> list[dict[str, Any]]:
    """第二轮补齐两条库存事实，使合成案件达到 DEMO_READY。"""

    return [
        readiness_fact(
            subject_type="CARGO_ITEM",
            subject_code="AGENT-E2E-ITEM-A",
            fact_type="AVAILABLE_QUANTITY",
            value=24,
            locator="generated:inventory-item-a-final",
        ),
        readiness_fact(
            subject_type="CARGO_ITEM",
            subject_code="AGENT-E2E-ITEM-B",
            fact_type="AVAILABLE_QUANTITY",
            value=12,
            locator="generated:inventory-item-b-final",
        ),
    ]


def post_idempotent_with_retry(
    client: Any,
    path: str,
    payload: dict[str, Any],
    fixed_key: str,
    *,
    expected_statuses: Iterable[int] = (200,),
    attempts: int = 3,
    quiet: bool = False,
) -> dict[str, Any]:
    """网络结果未知时，使用同一个 Idempotency-Key 重试同一命令。"""

    require(attempts > 0, "幂等命令重试次数必须大于 0")
    for attempt in range(1, attempts + 1):
        try:
            result = client.request(
                "POST",
                path,
                payload=payload,
                headers={"Idempotency-Key": fixed_key},
                expected_statuses=expected_statuses,
            )
            return require_object(result, f"{path} 的 data 必须是对象")
        except NetworkOutcomeUnknown:
            if attempt >= attempts:
                raise
            log(
                "写命令响应未知；将复用原 Idempotency-Key 重试同一命令。",
                quiet=quiet,
            )
            time.sleep(0.5)
    raise AssertionError("unreachable")


def recover_non_idempotent_create(
    client: Any,
    *,
    read_path: str,
    extract: Callable[[Any], dict[str, Any]],
    resource_label: str,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    """前置资源创建响应未知时只 GET 恢复，绝不盲目重发 POST。"""

    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_error: E2EError | None = None
    while True:
        try:
            return extract(client.request("GET", read_path))
        except HttpStatusError as exc:
            last_error = exc
            if exc.status != 404:
                raise
        except NetworkOutcomeUnknown as exc:
            last_error = exc

        if time.monotonic() >= deadline:
            raise NetworkOutcomeUnknown(
                f"{resource_label}创建响应未知，GET 后仍无法确认结果；"
                "为避免重复创建，脚本没有重发 POST"
            ) from last_error
        time.sleep(0.5)


def create_load_task_or_recover(
    client: Any,
    task_number: str,
    *,
    quiet: bool,
) -> dict[str, Any]:
    """通过 BFF 创建装载任务；结果未知时按稳定编号读取恢复。"""

    try:
        task = require_object(
            client.request(
                "POST",
                LOAD_TASKS_PATH,
                payload=build_load_task_payload(task_number),
                expected_statuses=(201,),
            ),
            "创建装载任务 data 必须是对象",
        )
    except NetworkOutcomeUnknown:
        log(
            "装载任务创建响应未知；只按任务编号 GET 恢复，不会重发创建 POST。",
            quiet=quiet,
        )
        task = recover_non_idempotent_create(
            client,
            read_path=f"{LOAD_TASKS_PATH}/{task_number}",
            extract=lambda value: require_object(
                value,
                "恢复的装载任务 data 必须是对象",
            ),
            resource_label="装载任务",
        )
    require(task.get("taskNumber") == task_number, "装载任务编号与请求不一致")
    return task


def create_link_draft_or_recover(
    client: Any,
    application_number: str,
    *,
    quiet: bool,
) -> dict[str, Any]:
    """通过 BFF 创建 LINK 草稿；结果未知时只读取详情恢复。"""

    try:
        application = require_object(
            client.request(
                "POST",
                LINK_APPLICATIONS_PATH,
                payload=build_link_draft_payload(application_number),
                expected_statuses=(201,),
            ),
            "创建 LINK 草稿 data 必须是对象",
        )
    except NetworkOutcomeUnknown:
        log(
            "LINK 草稿创建响应未知；只按申请编号 GET 恢复，不会重发创建 POST。",
            quiet=quiet,
        )

        def extract(value: Any) -> dict[str, Any]:
            detail = require_object(value, "恢复的 LINK 详情 data 必须是对象")
            return require_object(
                detail.get("application"),
                "恢复的 LINK 详情缺少 application",
            )

        application = recover_non_idempotent_create(
            client,
            read_path=f"{LINK_APPLICATIONS_PATH}/{application_number}/detail",
            extract=extract,
            resource_label="LINK 草稿",
        )

    require(
        application.get("applicationNumber") == application_number,
        "LINK 申请编号与请求不一致",
    )
    require(application.get("status") == "DRAFT", "新建 LINK 申请不是 DRAFT")
    return application


def bind_case_resource(
    client: Any,
    *,
    case_number: str,
    revision: int,
    resource_type: str,
    resource_number: str,
    quiet: bool,
) -> dict[str, Any]:
    """使用案件乐观锁和请求幂等键绑定一个已存在的业务资源。"""

    snapshot = post_idempotent_with_retry(
        client,
        f"{SHIPMENT_CASES_PATH}/{case_number}/resources",
        {
            "resourceType": resource_type,
            "resourceNumber": resource_number,
            "expectedRevision": revision,
        },
        idempotency_key(f"bind-{resource_type.lower()}"),
        quiet=quiet,
    )
    require(
        snapshot.get("revision") == revision + 1,
        f"绑定 {resource_type} 没有把案件 revision 精确推进一次",
    )
    return snapshot


def pending_interrupt_type(run: dict[str, Any]) -> str | None:
    """读取结构化人工中断类型。"""

    pending = run.get("pendingInterrupt")
    if pending is None:
        return None
    interrupt_type = require_object(
        pending,
        "pendingInterrupt 必须是对象",
    ).get("type")
    require(
        isinstance(interrupt_type, str) and interrupt_type,
        "pendingInterrupt 缺少 type",
    )
    return interrupt_type


def assert_agent_wait(
    run: dict[str, Any],
    *,
    status: str,
    stage: str,
    interrupt_type: str | None,
) -> None:
    """统一检查总 Agent 是否停在预期的可靠等待点。"""

    require(run.get("status") == status, f"总 Agent 未进入 {status}")
    require(run.get("currentStage") == stage, f"总 Agent 未停在 {stage}")
    require(
        pending_interrupt_type(run) == interrupt_type,
        f"总 Agent 当前人工中断不是 {interrupt_type}",
    )


def resume_agent(
    client: Any,
    run: dict[str, Any],
    *,
    interrupt_type: str,
    action: str = "APPROVE",
    comment: str,
    data_facts: list[dict[str, Any]] | None = None,
    quiet: bool,
) -> dict[str, Any]:
    """按当前运行版本回答一个真实人工中断。"""

    run_id = run.get("runId")
    require(isinstance(run_id, str) and run_id, "总 Agent 响应缺少 runId")
    run_version = require_positive_int(
        run.get("runVersion"),
        "总 Agent 响应缺少有效 runVersion",
    )
    return post_idempotent_with_retry(
        client,
        f"{SHIPMENT_AGENT_RUNS_PATH}/{run_id}/resume",
        {
            "expectedRunVersion": run_version,
            "interruptType": interrupt_type,
            "action": action,
            "comment": comment,
            "selectedResourceNumber": None,
            "requiredCapabilities": None,
            "dataFacts": data_facts,
        },
        idempotency_key(f"resume-{interrupt_type.lower()}"),
        quiet=quiet,
    )


def reconcile_agent(
    client: Any,
    run: dict[str, Any],
    *,
    quiet: bool,
) -> dict[str, Any]:
    """故障兜底：显式要求总 Agent 重新读取装载与 LINK 权威事实。

    正常 E2E 不调用这个入口；保留该客户端是为了验证平台仍有管理员手工恢复能力。
    """

    run_id = run.get("runId")
    require(isinstance(run_id, str) and run_id, "总 Agent 响应缺少 runId")
    run_version = require_positive_int(
        run.get("runVersion"),
        "总 Agent 响应缺少有效 runVersion",
    )
    return post_idempotent_with_retry(
        client,
        f"{SHIPMENT_AGENT_RUNS_PATH}/{run_id}/reconcile",
        {"expectedRunVersion": run_version},
        idempotency_key("reconcile"),
        quiet=quiet,
    )


def assert_automatic_wake_scheduled(
    run: dict[str, Any],
    *,
    expected_reason: str,
) -> None:
    """等待外部/专业系统时必须留下下一次自动检查计划。"""

    next_wake_at = run.get("nextWakeAt")
    require(
        isinstance(next_wake_at, str) and next_wake_at,
        "总 Agent 进入自动等待状态后没有返回 nextWakeAt",
    )
    require(
        run.get("wakeReason") == expected_reason,
        f"总 Agent 自动检查原因不是 {expected_reason}",
    )


def assert_manual_wait_has_no_automatic_wake(run: dict[str, Any]) -> None:
    """人工审批点不应继续后台轮询，避免无意义唤醒。"""

    require(
        run.get("nextWakeAt") is None,
        "总 Agent 已进入人工审批点，但 nextWakeAt 仍未清空",
    )
    require(
        run.get("wakeReason") is None,
        "总 Agent 已进入人工审批点，但 wakeReason 仍未清空",
    )


def wait_for_agent_stage(
    client: Any,
    run_id: str,
    *,
    status: str,
    stage: str,
    interrupt_type: str | None,
    wait_seconds: int,
    quiet: bool,
) -> dict[str, Any]:
    """只用 GET 等待进程内 Worker 把总 Agent 自动推进到目标阶段。"""

    path = f"{SHIPMENT_AGENT_RUNS_PATH}/{run_id}"
    deadline = time.monotonic() + max(0, wait_seconds)
    last_run: dict[str, Any] | None = None
    last_error: E2EError | None = None

    while True:
        try:
            current = require_object(
                client.request("GET", path),
                "自动唤醒后的总 Agent 详情必须是对象",
            )
            last_run = current
            if current.get("status") == status and current.get("currentStage") == stage:
                assert_agent_wait(
                    current,
                    status=status,
                    stage=stage,
                    interrupt_type=interrupt_type,
                )
                projection_is_settled = (
                    current.get("wakeReason") != "REPAIR_CONTROL_PROJECTION"
                    and (
                        status
                        not in {
                            "WAITING_HUMAN",
                            "COMPLETED",
                            "FAILED",
                            "CANCELLED",
                        }
                        or current.get("nextWakeAt") is None
                    )
                )
                if projection_is_settled:
                    return current
        except E2EError as exc:
            last_error = exc

        if time.monotonic() >= deadline:
            if last_run is not None:
                observed = f"{last_run.get('status')}/{last_run.get('currentStage')}"
            else:
                observed = f"读取失败：{last_error or '未知错误'}"
            raise ContractError(
                f"等待 Worker 自动推进到 {status}/{stage} 超时；最后状态为 {observed}。"
                "可检查 8200 日志，必要时再使用“立即检查（故障兜底）”"
            )

        log(
            f"等待 8200 内置 Worker 自动推进到 {stage}。",
            quiet=quiet,
        )
        time.sleep(0.5)


def recover_load_run_status(
    client: Any,
    run_id: str,
    expected_status: str,
    *,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    """装载审批响应未知时只 GET 权威状态，不重复提交人工决定。"""

    path = f"{LOAD_AGENT_RUNS_PATH}/{run_id}"
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        run = require_object(
            client.request("GET", path),
            "恢复的装载子 Agent 必须是对象",
        )
        if run.get("status") == expected_status:
            return run
        if time.monotonic() >= deadline:
            raise NetworkOutcomeUnknown(
                f"装载审批响应未知，且运行尚未进入 {expected_status}；"
                "脚本没有盲目重复提交人工决定"
            )
        time.sleep(0.5)


def approve_load_plan(
    client: Any,
    child_run: dict[str, Any],
    *,
    quiet: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """选择可行方案、人工批准并发布不可变正式版本。"""

    run_id = child_run.get("runId")
    require(isinstance(run_id, str) and run_id, "装载子 Agent 缺少 runId")
    require(
        child_run.get("status") == "READY_FOR_REVIEW",
        "装载子 Agent 未进入 READY_FOR_REVIEW",
    )
    plans = child_run.get("plans")
    require(isinstance(plans, list), "装载子 Agent 缺少 plans 数组")
    selected_plan = next(
        (
            plan
            for plan in plans
            if isinstance(plan, dict)
            and plan.get("feasible") is True
            and isinstance(plan.get("planCode"), str)
            and plan.get("planCode")
        ),
        None,
    )
    require(selected_plan is not None, "装载子 Agent 没有可批准的可行方案")
    plan_code = selected_plan["planCode"]

    decision_path = f"{LOAD_AGENT_RUNS_PATH}/{run_id}/decision"
    decision_payload = {
        "decision": "APPROVE",
        "planCode": plan_code,
        "comment": "Shipment Agent E2E：合成方案人工复核通过。",
        "expectedRunVersion": child_run.get("runVersion"),
    }
    try:
        approved = require_object(
            client.request(
                "POST",
                decision_path,
                payload=decision_payload,
            ),
            "装载审批 data 必须是对象",
        )
    except NetworkOutcomeUnknown:
        approved = recover_load_run_status(client, run_id, "APPROVED")
    require(approved.get("status") == "APPROVED", "装载方案未进入 APPROVED")
    require(
        approved.get("selectedPlanCode") == plan_code,
        "装载审批没有保存选中的方案编号",
    )

    lock_path = f"{LOAD_AGENT_RUNS_PATH}/{run_id}/plan-versions"
    lock_payload = {
        "expectedRunVersion": approved.get("runVersion"),
        "expectedCurrentVersionNumber": 0,
        "lockReason": "Shipment Agent E2E：发布合成任务正式装载 V1。",
    }
    # 正式发布接口没有请求级 Idempotency-Key，但 Python 以 sourceRunId 的业务唯一约束
    # 保证完全相同请求的安全重放。因此只有网络结果未知时才重发相同请求体。
    for attempt in range(1, 4):
        try:
            locked = require_object(
                client.request(
                    "POST",
                    lock_path,
                    payload=lock_payload,
                    expected_statuses=(201,),
                ),
                "正式装载版本 data 必须是对象",
            )
            break
        except NetworkOutcomeUnknown:
            if attempt >= 3:
                raise
            log(
                "装载版本发布响应未知；将重放完全相同的业务幂等请求。",
                quiet=quiet,
            )
            time.sleep(0.5)
    else:  # pragma: no cover - 循环要么 break 要么抛异常
        raise AssertionError("unreachable")

    require(locked.get("status") == "LOCKED", "正式装载版本不是 LOCKED")
    require(locked.get("versionNumber") == 1, "首次发布没有生成装载 V1")
    require(locked.get("sourceRunId") == run_id, "正式版本来源运行不一致")
    require(locked.get("planCode") == plan_code, "正式版本来源方案不一致")
    return approved, locked


def wait_for_carrier_booking(
    carrier: Any,
    application_number: str,
    *,
    wait_seconds: int,
    quiet: bool,
) -> dict[str, Any]:
    """等待 LINK 的可靠投递任务在本机模拟承运人形成一条申请。"""

    query = urllib.parse.urlencode({"keyword": application_number})
    path = f"{CARRIER_PORTAL_BOOKINGS_PATH}?{query}"
    deadline = time.monotonic() + max(0, wait_seconds)
    last_error: E2EError | None = None
    while True:
        try:
            bookings = carrier.request("GET", path)
            require(isinstance(bookings, list), "模拟承运人申请列表必须是数组")
            matches = [
                item
                for item in bookings
                if isinstance(item, dict)
                and item.get("application_number") == application_number
            ]
            if len(matches) == 1:
                return matches[0]
            require(
                len(matches) <= 1,
                "模拟承运人出现多个相同 application_number",
            )
        except E2EError as exc:
            last_error = exc

        if time.monotonic() >= deadline:
            suffix = f"；最后一次读取结果：{last_error}" if last_error else ""
            raise ContractError(
                "模拟承运人没有收到本次 LINK 申请。请确认使用 "
                "`./scripts/run-local.sh full` 启动，且 LINK 的承运人投递已开启"
                + suffix
            )
        log("等待 LINK 把合成申请可靠投递到本机模拟承运人。", quiet=quiet)
        time.sleep(1)


def recover_carrier_booking(
    carrier: Any,
    booking_id: int,
    accepted_statuses: set[str],
    *,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    """模拟门户写请求响应未知时，用详情 GET 恢复，不盲目重发。"""

    path = f"{CARRIER_PORTAL_BOOKINGS_PATH}/{booking_id}"
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        detail = require_object(
            carrier.request("GET", path),
            "模拟承运人详情必须是对象",
        )
        booking = require_object(
            detail.get("booking"),
            "模拟承运人详情缺少 booking",
        )
        if booking.get("status") in accepted_statuses:
            return booking
        if time.monotonic() >= deadline:
            raise NetworkOutcomeUnknown(
                "模拟承运人门户写请求响应未知，GET 后仍不能确认结果；"
                "脚本没有盲目重复人工动作"
            )
        time.sleep(0.5)


def confirm_in_carrier_portal(
    carrier: Any,
    booking: dict[str, Any],
) -> dict[str, Any]:
    """模拟承运人人工受理并确认，只操作固定本机门户。"""

    booking_id = require_positive_int(
        booking.get("id"),
        "模拟承运人申请缺少有效 id",
    )
    current_booking = booking
    status = current_booking.get("status")
    if status == "RECEIVED":
        try:
            processing = require_object(
                carrier.request(
                    "POST",
                    f"{CARRIER_PORTAL_BOOKINGS_PATH}/{booking_id}/process",
                    payload=None,
                ),
                "模拟承运人开始处理响应必须是对象",
            )
        except NetworkOutcomeUnknown:
            processing = recover_carrier_booking(
                carrier,
                booking_id,
                {"PROCESSING", "CONFIRMED"},
            )
        require(
            processing.get("status") in {"PROCESSING", "CONFIRMED"},
            "模拟承运人未进入 PROCESSING",
        )
        current_booking = processing
        status = current_booking.get("status")

    if status == "PROCESSING":
        try:
            confirmed = require_object(
                carrier.request(
                    "POST",
                    f"{CARRIER_PORTAL_BOOKINGS_PATH}/{booking_id}/decision",
                    payload={"decision": "CONFIRMED"},
                ),
                "模拟承运人确认响应必须是对象",
            )
        except NetworkOutcomeUnknown:
            confirmed = recover_carrier_booking(
                carrier,
                booking_id,
                {"CONFIRMED"},
            )
    elif status == "CONFIRMED":
        confirmed = current_booking
    else:
        raise ContractError(f"模拟承运人申请状态 {status!r} 不允许执行成功确认场景")

    require(confirmed.get("status") == "CONFIRMED", "模拟承运人未确认订舱")
    booking_reference = confirmed.get("booking_reference")
    require(
        isinstance(booking_reference, str) and booking_reference,
        "模拟承运人确认后缺少合成 booking_reference",
    )
    require(
        booking_reference.startswith("DEMO-"),
        "模拟承运人返回的参考号不是 DEMO 合成值",
    )
    return confirmed


def wait_for_link_confirmation(
    client: Any,
    application_number: str,
    *,
    wait_seconds: int,
) -> dict[str, Any]:
    """等待模拟承运人回调在 LINK 落库，并读取 BFF 权威详情。"""

    path = f"{LINK_APPLICATIONS_PATH}/{application_number}/detail"
    deadline = time.monotonic() + max(0, wait_seconds)
    last_error: E2EError | None = None
    while True:
        try:
            detail = require_object(
                client.request("GET", path),
                "LINK 详情 data 必须是对象",
            )
            application = require_object(
                detail.get("application"),
                "LINK 详情缺少 application",
            )
            if application.get("status") == "CONFIRMED":
                reference = application.get("bookingReference")
                require(
                    isinstance(reference, str) and reference.startswith("DEMO-"),
                    "LINK 确认后缺少 DEMO bookingReference",
                )
                return detail
        except E2EError as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise ContractError(
                "模拟承运人已确认，但 LINK 尚未收到 CONFIRMED 回调"
                + (f"：{last_error}" if last_error else "")
            )
        time.sleep(0.5)


def probe_platform(
    bff: BffClient,
    carrier: CarrierPortalClient,
    *,
    wait_seconds: int,
    quiet: bool,
) -> bool:
    """等待 BFF 四条读取链路和本机模拟承运人门户均可用。"""

    bff_paths = (
        LOAD_TASKS_PATH,
        LINK_APPLICATIONS_PATH,
        SHIPMENT_CASES_PATH,
        SHIPMENT_AGENT_RUNS_PATH,
    )
    carrier_probe_query = urllib.parse.urlencode(
        {"keyword": "DEMO-SHIPMENT-AGENT-E2E-PROBE-NOT-FOUND"}
    )
    deadline = time.monotonic() + max(0, wait_seconds)
    last_error: E2EError | None = None
    while True:
        try:
            for path in bff_paths:
                data = bff.request("GET", path)
                require(isinstance(data, list), f"{path} 的 data 必须是数组")
            carrier_data = carrier.request(
                "GET",
                f"{CARRIER_PORTAL_BOOKINGS_PATH}?{carrier_probe_query}",
            )
            require(
                isinstance(carrier_data, list),
                "模拟承运人门户探测必须返回数组",
            )
            log(
                "Java BFF、Agent Runtime、LINK 与本机模拟承运人均已就绪。",
                quiet=quiet,
            )
            return True
        except E2EError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(1)

    if not quiet:
        print(
            "[shipment-agent-e2e] error: 完整链路未就绪："
            f"{last_error or '未知错误'}。请先运行 ./scripts/run-local.sh full。",
            file=sys.stderr,
            flush=True,
        )
    return False


def assert_trace(final_run: dict[str, Any]) -> None:
    """复核 Trace 连续、有审计身份，并覆盖关键业务节点。"""

    events = final_run.get("events")
    require(isinstance(events, list) and events, "最终运行缺少 Trace 事件")
    require(
        all(isinstance(event, dict) for event in events),
        "Trace 中存在非对象元素",
    )
    sequences = [event.get("sequence") for event in events]
    require(
        all(type(sequence) is int for sequence in sequences),
        "Trace sequence 必须是整数",
    )
    require(
        sequences == list(range(1, len(events) + 1)),
        "Trace sequence 没有按 1..N 连续排列",
    )
    require(
        all(
            isinstance(event.get("requestId"), str)
            and event.get("requestId")
            and event.get("actorSubject") == "SYSTEM_DEMO"
            and isinstance(event.get("occurredAt"), str)
            and event.get("occurredAt")
            for event in events
        ),
        "Trace 缺少请求号、服务端审计身份或发生时间",
    )

    event_types = {event.get("eventType") for event in events}
    required_event_types = {
        "RUN_STARTED",
        "ONLY_RESOURCE_AUTO_SELECTED",
        "DATA_READINESS_ASSESSMENT_STARTED",
        "DATA_REMEDIATION_REQUIRED",
        "DATA_SUPPLEMENTED",
        "DATA_READINESS_READY",
        "CASE_ACTIVATION_APPROVED",
        "CASE_ACTIVATED",
        "LOAD_COORDINATION_STARTED",
        "LOAD_CHILD_ACTION_REQUIRED",
        "LOAD_PLAN_VERSION_READY",
        "BOOKING_COORDINATION_STARTED",
        "BOOKING_SUBMIT_APPROVED",
        "BOOKING_SUBMITTED",
        "CARRIER_CALLBACK_PENDING",
        "BOOKING_CONFIRMED",
        "COMPLETION_APPROVED",
        "RUN_COMPLETED",
    }
    missing = sorted(required_event_types - event_types)
    require(not missing, f"最终 Trace 缺少关键事件：{', '.join(missing)}")
    require(
        sum(event.get("eventType") == "DATA_SUPPLEMENTED" for event in events) == 2,
        "Trace 应精确记录两轮 DATA_SUPPLEMENTED",
    )


def readiness_issues(run: dict[str, Any]) -> list[dict[str, Any]]:
    """读取数据就绪中断中的结构化问题列表。"""

    pending = require_object(
        run.get("pendingInterrupt"),
        "数据就绪等待缺少 pendingInterrupt",
    )
    details = require_object(
        pending.get("details"),
        "数据就绪等待缺少 details",
    )
    issues = details.get("issues")
    require(isinstance(issues, list), "数据就绪等待缺少 issues 数组")
    require(
        all(isinstance(issue, dict) for issue in issues),
        "数据就绪 issues 中存在非对象元素",
    )
    return issues


def assert_readiness_issue(
    issues: list[dict[str, Any]],
    *,
    issue_code: str,
    subject_code: str,
    actual_value: int | None,
) -> None:
    """断言补数后仍存在一条预期的业务阻塞问题。"""

    # ``dataReadiness.issues`` 是 Java BFF 的稳定平台 DTO，因此字段为 camelCase；
    # ``pendingInterrupt.details`` 则是允许 Python Agent 后续扩展的开放 Map，内部字段
    # 会保留 Python 原始 snake_case。真实页面也同时兼容这两种边界表示，E2E 必须
    # 验证同一份业务事实，而不能把命名风格差异误判成“库存问题丢失”。
    def field(issue: dict[str, Any], camel_name: str, snake_name: str) -> Any:
        return issue.get(camel_name, issue.get(snake_name))

    match = next(
        (
            issue
            for issue in issues
            if field(issue, "issueCode", "issue_code") == issue_code
            and field(issue, "subjectCode", "subject_code") == subject_code
        ),
        None,
    )
    require(
        match is not None,
        f"数据就绪问题中缺少 {issue_code}:{subject_code}",
    )
    require(match.get("severity") == "BLOCKING", "数据就绪问题不是 BLOCKING")
    require(
        field(match, "actualValue", "actual_value") == actual_value,
        f"{issue_code}:{subject_code} 的 actualValue 不正确",
    )


def run_e2e(
    bff: BffClient,
    carrier: CarrierPortalClient,
    *,
    wait_seconds: int,
    quiet: bool,
) -> None:
    """执行一票 SEA_FCL Shipment Agent 的真实本机闭环。"""

    token = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6].upper()}"
    task_number = f"LP-AGENT-E2E-{token}"
    application_number = f"DEMO-AGENT-E2E-{token}"
    case_number = f"SHP-AGENT-E2E-{token}"
    case_path = f"{SHIPMENT_CASES_PATH}/{case_number}"

    log(f"1/17 创建合成装载任务 {task_number}。", quiet=quiet)
    create_load_task_or_recover(bff, task_number, quiet=quiet)

    log(f"2/17 创建 LINK 合成草稿 {application_number}。", quiet=quiet)
    create_link_draft_or_recover(bff, application_number, quiet=quiet)

    log(f"3/17 创建 SEA_FCL 发运案件 {case_number}。", quiet=quiet)
    case_snapshot = post_idempotent_with_retry(
        bff,
        SHIPMENT_CASES_PATH,
        build_case_payload(case_number),
        idempotency_key("create-case"),
        expected_statuses=(201,),
        quiet=quiet,
    )
    require(case_snapshot.get("caseNumber") == case_number, "案件编号不一致")
    require(case_snapshot.get("status") == "DRAFT", "新建案件不是 DRAFT")
    revision = require_positive_int(
        case_snapshot.get("revision"),
        "新建案件缺少有效 revision",
    )
    require(revision == 1, "新建案件 revision 应为 1")

    log("4/17 绑定装载任务与 LINK 草稿。", quiet=quiet)
    case_snapshot = bind_case_resource(
        bff,
        case_number=case_number,
        revision=revision,
        resource_type="LOAD_PLANNING_TASK",
        resource_number=task_number,
        quiet=quiet,
    )
    revision = require_positive_int(
        case_snapshot.get("revision"),
        "绑定装载任务后缺少有效 revision",
    )
    case_snapshot = bind_case_resource(
        bff,
        case_number=case_number,
        revision=revision,
        resource_type="LINK_BOOKING_APPLICATION",
        resource_number=application_number,
        quiet=quiet,
    )
    revision = require_positive_int(
        case_snapshot.get("revision"),
        "绑定 LINK 后缺少有效 revision",
    )
    require(revision == 3, "两个资源绑定后案件 revision 应为 3")

    log("5/17 启动 Shipment 总 Agent，并等待业务补充发运数据。", quiet=quiet)
    run = post_idempotent_with_retry(
        bff,
        f"{case_path}/agent-runs",
        {"expectedCaseRevision": revision},
        idempotency_key("start-agent"),
        expected_statuses=(201,),
        quiet=quiet,
    )
    run_id = run.get("runId")
    require(isinstance(run_id, str) and run_id, "总 Agent 启动响应缺少 runId")
    assert_agent_wait(
        run,
        status="WAITING_HUMAN",
        stage="AWAITING_DATA_REMEDIATION",
        interrupt_type="DATA_READINESS_REMEDIATION",
    )
    require(
        set(run.get("requiredCapabilities") or []) == {"LOAD_PLANNING", "LINK_BOOKING"},
        "SEA_FCL 没有得到装载与 LINK 两项能力",
    )
    require(
        run.get("selectedLoadTaskNumber") == task_number
        and run.get("selectedBookingApplicationNumber") == application_number,
        "总 Agent 没有自动选择案件中唯一的两个绑定资源",
    )
    readiness = require_object(
        run.get("dataReadiness"),
        "总 Agent 缺少 dataReadiness",
    )
    require(
        readiness.get("status") == "ACTION_REQUIRED",
        "首次评估没有进入 ACTION_REQUIRED",
    )
    require(readiness_issues(run), "首次数据就绪评估没有生成业务问题")

    log("6/17 第一轮补数：确认主数据，但故意保留缺货和缺数。", quiet=quiet)
    run = resume_agent(
        bff,
        run,
        interrupt_type="DATA_READINESS_REMEDIATION",
        action="SUPPLY_DATA",
        comment="Shipment Agent E2E：第一轮合成仓库回填。",
        data_facts=first_readiness_remediation_facts(),
        quiet=quiet,
    )
    assert_agent_wait(
        run,
        status="WAITING_HUMAN",
        stage="AWAITING_DATA_REMEDIATION",
        interrupt_type="DATA_READINESS_REMEDIATION",
    )
    first_remaining_issues = readiness_issues(run)
    assert_readiness_issue(
        first_remaining_issues,
        issue_code="INVENTORY_SHORTAGE",
        subject_code="AGENT-E2E-ITEM-A",
        actual_value=8,
    )
    assert_readiness_issue(
        first_remaining_issues,
        issue_code="AVAILABLE_QUANTITY_MISSING",
        subject_code="AGENT-E2E-ITEM-B",
        actual_value=None,
    )
    require(
        len(first_remaining_issues) == 2,
        "第一轮补数后应只剩缺货与缺数两个问题",
    )

    log("7/17 第二轮补齐库存，Agent 应自动重评并进入案件激活审批。", quiet=quiet)
    run = resume_agent(
        bff,
        run,
        interrupt_type="DATA_READINESS_REMEDIATION",
        action="SUPPLY_DATA",
        comment="Shipment Agent E2E：第二轮补齐合成库存。",
        data_facts=final_readiness_remediation_facts(),
        quiet=quiet,
    )
    assert_agent_wait(
        run,
        status="WAITING_HUMAN",
        stage="AWAITING_CASE_ACTIVATION",
        interrupt_type="CASE_ACTIVATION_APPROVAL",
    )
    readiness = require_object(
        run.get("dataReadiness"),
        "补数完成后总 Agent 缺少 dataReadiness",
    )
    require(
        readiness.get("status") == "DEMO_READY",
        "合成数据补齐后没有进入 DEMO_READY",
    )
    require(
        readiness.get("issues") == [],
        "DEMO_READY 仍包含数据就绪问题",
    )

    log("8/17 人工批准案件激活，并等待装载子 Agent 评审。", quiet=quiet)
    run = resume_agent(
        bff,
        run,
        interrupt_type="CASE_ACTIVATION_APPROVAL",
        comment="Shipment Agent E2E：数据就绪与两个合成资源已核对。",
        quiet=quiet,
    )
    assert_agent_wait(
        run,
        status="WAITING_RESOURCE",
        stage="AWAITING_LOAD_REVIEW",
        interrupt_type=None,
    )
    require(run.get("caseStatus") == "ACTIVE", "案件没有进入 ACTIVE")
    child_run_id = run.get("selectedLoadRunId")
    require(
        isinstance(child_run_id, str) and child_run_id,
        "总 Agent 没有保存装载子 Agent runId",
    )
    child_projection = require_object(
        run.get("childAgent"),
        "总 Agent 缺少装载子 Agent 投影",
    )
    require(
        child_projection.get("status") == "READY_FOR_REVIEW",
        "装载子 Agent 投影不是 READY_FOR_REVIEW",
    )
    assert_automatic_wake_scheduled(
        run,
        expected_reason="POLL_LOAD_STATUS",
    )

    log("9/17 在装载专业流程中批准可行方案。", quiet=quiet)
    child_run = require_object(
        bff.request("GET", f"{LOAD_AGENT_RUNS_PATH}/{child_run_id}"),
        "装载子 Agent 详情必须是对象",
    )
    approved_child, locked_version = approve_load_plan(
        bff,
        child_run,
        quiet=quiet,
    )
    require(
        approved_child.get("runId") == child_run_id,
        "装载审批返回了不同的子运行",
    )

    log("10/17 发布 LOCKED V1 后，等待 Worker 自动读取专业结果。", quiet=quiet)
    run = wait_for_agent_stage(
        bff,
        run_id,
        status="WAITING_HUMAN",
        stage="AWAITING_BOOKING_SUBMIT_APPROVAL",
        interrupt_type="BOOKING_SUBMIT_APPROVAL",
        wait_seconds=wait_seconds,
        quiet=quiet,
    )
    assert_manual_wait_has_no_automatic_wake(run)
    require(
        run.get("lockedLoadVersionId") == locked_version.get("versionId")
        and run.get("lockedLoadVersionNumber") == 1,
        "总 Agent 没有记录正式装载 V1",
    )

    log("11/17 人工批准 LINK 提交，HTTP 请求不等待承运人最终结果。", quiet=quiet)
    run = resume_agent(
        bff,
        run,
        interrupt_type="BOOKING_SUBMIT_APPROVAL",
        comment="Shipment Agent E2E：七步字段已由合成数据核对。",
        quiet=quiet,
    )
    assert_agent_wait(
        run,
        status="WAITING_EXTERNAL",
        stage="AWAITING_BOOKING_RESULT",
        interrupt_type=None,
    )
    require(run.get("bookingStatus") == "SUBMITTED", "LINK 申请未进入 SUBMITTED")
    assert_automatic_wake_scheduled(
        run,
        expected_reason="POLL_LINK_STATUS",
    )

    log("12/17 等待本机模拟承运人收到可靠投递。", quiet=quiet)
    carrier_booking = wait_for_carrier_booking(
        carrier,
        application_number,
        wait_seconds=wait_seconds,
        quiet=quiet,
    )
    require(
        carrier_booking.get("application_number") == application_number,
        "模拟承运人收到的不是本次合成 LINK 申请",
    )

    log("13/17 模拟承运人人工受理并确认订舱。", quiet=quiet)
    carrier_confirmed = confirm_in_carrier_portal(carrier, carrier_booking)

    log("14/17 等待 CONFIRMED 回调写回 LINK。", quiet=quiet)
    link_detail = wait_for_link_confirmation(
        bff,
        application_number,
        wait_seconds=wait_seconds,
    )
    link_application = require_object(
        link_detail.get("application"),
        "确认后的 LINK 详情缺少 application",
    )
    require(
        link_application.get("bookingReference")
        == carrier_confirmed.get("booking_reference"),
        "LINK 与模拟承运人的 booking reference 不一致",
    )

    log("15/17 等待 Worker 自动读取 LINK 终态并进入结案审批。", quiet=quiet)
    run = wait_for_agent_stage(
        bff,
        run_id,
        status="WAITING_HUMAN",
        stage="AWAITING_COMPLETION_APPROVAL",
        interrupt_type="COMPLETION_APPROVAL",
        wait_seconds=wait_seconds,
        quiet=quiet,
    )
    assert_manual_wait_has_no_automatic_wake(run)
    require(run.get("bookingStatus") == "CONFIRMED", "总 Agent 未读到 LINK CONFIRMED")
    require(
        run.get("bookingReference") == link_application.get("bookingReference"),
        "总 Agent 没有记录确认后的 booking reference",
    )

    log("16/17 人工批准结案。", quiet=quiet)
    run = resume_agent(
        bff,
        run,
        interrupt_type="COMPLETION_APPROVAL",
        comment="Shipment Agent E2E：装载 V1 与订舱确认均已复核。",
        quiet=quiet,
    )
    require(run.get("status") == "COMPLETED", "总 Agent 最终状态不是 COMPLETED")
    require(run.get("currentStage") == "COMPLETED", "总 Agent 最终阶段不是 COMPLETED")
    require(run.get("caseStatus") == "COMPLETED", "最终案件状态不是 COMPLETED")
    require(run.get("pendingInterrupt") is None, "完成运行仍保留人工中断")
    require(run.get("allowedActions") == [], "完成运行仍暴露可执行动作")
    require(
        isinstance(run.get("completedAt"), str) and run.get("completedAt"),
        "完成运行缺少 completedAt",
    )

    log("17/17 复核案件、运行中心、历史、active=null 与完整 Trace。", quiet=quiet)
    final_case = require_object(
        bff.request("GET", case_path),
        "最终案件详情必须是对象",
    )
    require(final_case.get("status") == "COMPLETED", "最终案件 GET 不是 COMPLETED")
    require(
        final_case.get("revision") == 5,
        "案件应按创建、两个绑定、激活、完成形成 revision 1..5",
    )
    active = bff.request("GET", f"{case_path}/agent-runs/active")
    require(active is None, "完成后案件仍返回 active Shipment Agent")
    history = bff.request("GET", f"{case_path}/agent-runs/history")
    require(isinstance(history, list), "Shipment Agent 历史必须是数组")
    require(
        any(
            isinstance(item, dict)
            and item.get("runId") == run_id
            and item.get("status") == "COMPLETED"
            for item in history
        ),
        "案件运行历史没有保留本次 COMPLETED 运行",
    )
    all_runs = bff.request("GET", SHIPMENT_AGENT_RUNS_PATH)
    require(isinstance(all_runs, list), "全局 Shipment Agent 列表必须是数组")
    require(
        any(
            isinstance(item, dict)
            and item.get("runId") == run_id
            and item.get("caseNumber") == case_number
            for item in all_runs
        ),
        "Agent 运行中心列表中找不到本次运行",
    )
    assert_trace(run)

    log(
        f"PASS：案件 {case_number} 已完成装载 V1、LINK 异步确认、"
        "两轮业务补数自动恢复、人工审批和可审计 COMPLETED 闭环。",
        quiet=quiet,
    )


def run_self_test() -> None:
    """离线检查安全边界、合成数据和幂等重试，不访问任何端口。"""

    task_payload = build_load_task_payload("LP-AGENT-E2E-OFFLINE")
    link_payload = build_link_draft_payload("DEMO-AGENT-E2E-OFFLINE")
    case_payload = build_case_payload("SHP-AGENT-E2E-OFFLINE")
    require(
        all(
            item.get("sourceType") == "SYNTHETIC_DEMO"
            for item in task_payload["cargoItems"]
        ),
        "离线自检发现非合成货物来源",
    )
    require(
        all(
            item.get("sourceType") == "SYNTHETIC_DEMO"
            for item in task_payload["palletSpecs"]
        ),
        "离线自检发现非合成托盘来源",
    )
    require(
        link_payload["applicationNumber"].startswith("DEMO-"),
        "离线自检的 LINK 编号不是 DEMO 前缀",
    )
    require(
        case_payload["transportScenario"] == "SEA_FCL",
        "离线自检案件不是 SEA_FCL",
    )

    class FakeIdempotentClient:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def request(
            self,
            method: str,
            path: str,
            *,
            payload: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            expected_statuses: Iterable[int] = (200,),
        ) -> dict[str, Any]:
            del method, path, payload, expected_statuses
            self.keys.append((headers or {}).get("Idempotency-Key", ""))
            if len(self.keys) == 1:
                raise NetworkOutcomeUnknown("simulated")
            return {"runId": "offline-run"}

    fake = FakeIdempotentClient()
    fixed_key = "shipment-agent-e2e-offline-fixed-key"
    result = post_idempotent_with_retry(
        fake,
        f"{SHIPMENT_CASES_PATH}/SHP-OFFLINE/agent-runs",
        {"expectedCaseRevision": 3},
        fixed_key,
        expected_statuses=(201,),
        attempts=2,
        quiet=True,
    )
    require(result["runId"] == "offline-run", "离线幂等重试没有返回模拟结果")
    require(
        fake.keys == [fixed_key, fixed_key],
        "网络结果未知时没有复用原 Idempotency-Key",
    )

    bff = BffClient()
    carrier = CarrierPortalClient()
    require(
        bff.base_url == BFF_BASE_URL and bff.allowed_path_prefix == "/api/v1/platform/",
        "Java BFF 客户端的本机安全边界发生变化",
    )
    require(
        carrier.base_url == CARRIER_SIMULATOR_BASE_URL
        and carrier.allowed_path_prefix == "/api/v1/portal/",
        "模拟承运人客户端的本机门户边界发生变化",
    )
    print(
        "[shipment-agent-e2e] self-test PASS：合成数据、固定本机边界与"
        " Idempotency-Key 安全重试均通过。"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "通过 Java BFF 与本机模拟承运人验证 Shipment 总 Agent 的真实闭环。"
        )
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="不访问网络，只检查合成数据、安全边界和幂等重试。",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="只读检查 BFF、Agent Runtime、LINK 和模拟承运人，不创建数据。",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=30,
        help="等待服务就绪、可靠投递和回调的最长秒数；默认 30。",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="成功时不输出步骤；失败仍返回非零退出码。",
    )
    args = parser.parse_args()
    if args.wait_seconds < 0:
        parser.error("--wait-seconds 不能小于 0")
    if args.self_test and args.probe_only:
        parser.error("--self-test 与 --probe-only 不能同时使用")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            run_self_test()
            return 0

        bff = BffClient()
        carrier = CarrierPortalClient()
        if not probe_platform(
            bff,
            carrier,
            wait_seconds=args.wait_seconds,
            quiet=args.quiet,
        ):
            return 1
        if args.probe_only:
            return 0
        run_e2e(
            bff,
            carrier,
            wait_seconds=args.wait_seconds,
            quiet=args.quiet,
        )
        return 0
    except E2EError as exc:
        if not args.quiet:
            print(
                f"[shipment-agent-e2e] error: {exc}",
                file=sys.stderr,
                flush=True,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
