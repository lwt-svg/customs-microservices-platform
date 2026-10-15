#!/usr/bin/env python3
"""只通过 Java BFF 验证 Shipment Case 的本机真实闭环。

脚本固定访问 ``http://127.0.0.1:8080/api/v1/platform/*``，不会直连 Python、
Nacos 或 MySQL。一次完整运行会创建全新的合成装载任务、LINK 订舱草稿和发运案件，
再验证资源绑定、乐观锁冲突、状态流转、HTTP 幂等重放和最终审计时间线。

这里刻意区分两类写请求：

* 装载任务和 LINK 草稿创建接口当前没有 ``Idempotency-Key``。若响应未知，脚本只按
  唯一业务编号 GET 恢复，不会盲目重发 POST；
* Shipment Case 的创建、资源绑定和状态流转都要求 ``Idempotency-Key``。若响应未知，
  脚本可以重发完全相同的命令，但每次必须复用原来的 key。

输出只包含合成业务编号、步骤和状态，不打印请求体、响应全文或环境变量。
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

BASE_URL = "http://127.0.0.1:8080"
LOAD_TASKS_PATH = "/api/v1/platform/load-planning/tasks"
LINK_APPLICATIONS_PATH = "/api/v1/platform/link-booking/applications"
SHIPMENT_CASES_PATH = "/api/v1/platform/shipment-cases"


class E2EError(RuntimeError):
    """用户可读、且不会包含请求体、响应全文或环境变量的验收错误。"""


class NetworkOutcomeUnknown(E2EError):
    """请求可能已到达服务端，但客户端没有收到完整响应。"""


class HttpStatusError(E2EError):
    """服务端明确返回了非 2xx 状态。"""

    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"{path} 明确返回 HTTP {status}")
        self.status = status
        self.path = path


class ContractError(E2EError):
    """BFF 返回成功 HTTP，但 JSON 契约或业务结果不符合预期。"""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝重定向，保证脚本不会离开固定的本机 8080。"""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class BffClient:
    """只允许访问 Java 平台 API 的极小 JSON 客户端。"""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
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
        """发送请求并只返回统一 BFF 外壳中的 ``data``。

        异常只保留路径和状态码，不读取或输出错误响应正文。这样真实链路失败时，脚本
        仍不会把上游诊断、SQL 信息或未来可能出现的敏感字段打印到终端。
        """

        if not path.startswith("/api/v1/platform/"):
            raise ContractError("脚本拒绝访问 Java BFF 平台 API 以外的路径")

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
            f"{BASE_URL}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # HTTPError 表示服务端已经给出明确 HTTP 结果。关闭响应且不读取正文。
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

        expected = set(expected_statuses)
        if status not in expected:
            raise ContractError(f"{path} 返回了未预期的 HTTP {status}")

        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"{path} 没有返回有效 JSON") from exc
        if not isinstance(envelope, dict):
            raise ContractError(f"{path} 没有返回 BFF JSON 对象")
        if envelope.get("code") != 0 or envelope.get("message") != "success":
            raise ContractError(f"{path} 没有返回成功的 BFF 外壳")
        if "data" not in envelope:
            raise ContractError(f"{path} 的 BFF 外壳缺少 data")
        return envelope["data"]


def log(message: str, *, quiet: bool = False) -> None:
    """输出不含请求体和响应全文的短步骤信息。"""

    if not quiet:
        print(f"[shipment-case-e2e] {message}", flush=True)


def require(condition: bool, message: str) -> None:
    """把契约断言统一转为可读的 E2E 错误。"""

    if not condition:
        raise ContractError(message)


def require_object(value: Any, message: str) -> dict[str, Any]:
    """确认 BFF ``data`` 是 JSON 对象。"""

    require(isinstance(value, dict), message)
    return value


def require_revision(snapshot: dict[str, Any], message: str) -> int:
    """读取正整数 revision，并拒绝把布尔值误当成整数。"""

    revision = snapshot.get("revision")
    require(type(revision) is int and revision > 0, message)
    return revision


def probe_platform(
    client: BffClient,
    *,
    wait_seconds: int,
    quiet: bool,
) -> bool:
    """等待三条平台读取链路全部就绪。

    这三次 GET 分别覆盖 Java→装载 Python、Java→LINK Python，以及
    Java→Shipment Case Python。这里只轮询安全读取，不会自动创建任何资源。
    """

    probe_paths = (
        LOAD_TASKS_PATH,
        LINK_APPLICATIONS_PATH,
        SHIPMENT_CASES_PATH,
    )
    deadline = time.monotonic() + max(0, wait_seconds)
    last_error: E2EError | None = None

    while True:
        try:
            for path in probe_paths:
                data = client.request("GET", path)
                require(isinstance(data, list), f"{path} 的 data 必须是数组")
            log("Java BFF、两个 Python 业务域和案件接口均已就绪。", quiet=quiet)
            return True
        except E2EError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(1)

    if not quiet:
        print(
            f"[shipment-case-e2e] error: 平台链路未就绪："
            f"{last_error or '未知错误'}。",
            file=sys.stderr,
            flush=True,
        )
    return False


def build_load_task_payload(task_number: str) -> dict[str, Any]:
    """生成完全合成的装载任务，不读取企业文件或环境变量。"""

    return {
        "taskNumber": task_number,
        "taskName": "Shipment Case 本机闭环验收",
        "description": "仅用于本机 Java BFF 真实链路验收的合成数据。",
        "cargoItems": [
            {
                "itemCode": "SHP-E2E-ITEM-A",
                "skuCode": "SHP-E2E-SKU-A",
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
            }
        ],
        "palletSpecs": [
            {
                "palletCode": "SHP-E2E-PALLET",
                "palletName": "合成测试 1200×1000 托盘",
                "quantity": 10,
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
    """生成 LINK 手工草稿使用的合成航线。"""

    requested_etd = (
        datetime.now(timezone.utc).date() + timedelta(days=14)
    ).isoformat()
    return {
        "applicationNumber": application_number,
        "originPort": "DEMO-SHA",
        "destinationPort": "DEMO-LAX",
        "transportMode": "SEA",
        "containerType": "40HQ",
        "requestedEtd": requested_etd,
    }


def recover_non_idempotent_create(
    client: Any,
    *,
    read_path: str,
    extract: Callable[[Any], dict[str, Any]],
    resource_label: str,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    """POST 响应未知时，只使用 GET 按稳定编号恢复服务端结果。

    装载任务和 LINK 草稿创建接口没有请求级幂等键。即使第一次 POST 可能已经落库，
    这里也绝不重发 POST；读不到权威结果时宁可停止并交给人工检查。
    """

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
            # GET 是安全读取，可以等待后再次读取；仍然不会再次发送创建 POST。
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
    quiet: bool = False,
) -> dict[str, Any]:
    """创建装载任务；响应未知时按 taskNumber GET，不重发 POST。"""

    try:
        result = client.request(
            "POST",
            LOAD_TASKS_PATH,
            payload=build_load_task_payload(task_number),
            expected_statuses=(201,),
        )
        task = require_object(result, "创建装载任务 data 必须是对象")
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
    require(
        isinstance(task.get("status"), str) and task.get("status"),
        "装载任务响应缺少状态",
    )
    return task


def create_link_draft_or_recover(
    client: Any,
    application_number: str,
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    """创建 LINK 草稿；响应未知时读取详情，不重发非幂等 POST。"""

    try:
        result = client.request(
            "POST",
            LINK_APPLICATIONS_PATH,
            payload=build_link_draft_payload(application_number),
            expected_statuses=(201,),
        )
        application = require_object(result, "创建 LINK 草稿 data 必须是对象")
    except NetworkOutcomeUnknown:
        log(
            "LINK 草稿创建响应未知；只按申请编号 GET 恢复，不会重发创建 POST。",
            quiet=quiet,
        )

        def extract_application(value: Any) -> dict[str, Any]:
            detail = require_object(value, "恢复的 LINK 详情 data 必须是对象")
            return require_object(
                detail.get("application"),
                "恢复的 LINK 详情缺少 application",
            )

        application = recover_non_idempotent_create(
            client,
            read_path=f"{LINK_APPLICATIONS_PATH}/{application_number}/detail",
            extract=extract_application,
            resource_label="LINK 草稿",
        )

    require(
        application.get("applicationNumber") == application_number,
        "LINK 申请编号与请求不一致",
    )
    require(application.get("status") == "DRAFT", "新建 LINK 申请不是 DRAFT")
    return application


def post_idempotent_case_command(
    client: Any,
    path: str,
    payload: dict[str, Any],
    idempotency_key: str,
    *,
    expected_statuses: Iterable[int] = (200,),
    attempts: int = 3,
    quiet: bool = False,
) -> dict[str, Any]:
    """发送 Shipment Case 命令；网络结果未知时固定复用原 key。

    只有 ``NetworkOutcomeUnknown`` 才会自动重试。明确的 400/404/409/5xx 会立即交给
    调用方判断，避免把业务冲突误当作瞬时网络问题。
    """

    require(attempts > 0, "幂等命令重试次数必须大于 0")
    headers = {"Idempotency-Key": idempotency_key}
    for attempt in range(1, attempts + 1):
        try:
            result = client.request(
                "POST",
                path,
                payload=payload,
                headers=headers,
                expected_statuses=expected_statuses,
            )
            return require_object(result, "Shipment Case 命令 data 必须是对象")
        except NetworkOutcomeUnknown:
            if attempt >= attempts:
                raise
            log(
                "Shipment Case 命令响应未知；将复用原 Idempotency-Key "
                "重试同一命令。",
                quiet=quiet,
            )
            time.sleep(0.5)
    raise AssertionError("unreachable")


def require_case_identity(
    snapshot: dict[str, Any],
    *,
    case_id: str,
    case_number: str,
    message: str,
) -> None:
    """确认一次命令返回的仍是同一个案件。"""

    require(
        snapshot.get("id") == case_id
        and snapshot.get("caseNumber") == case_number,
        message,
    )


def race_resource_bindings(
    case_path: str,
    *,
    expected_revision: int,
    task_number: str,
    application_number: str,
    quiet: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """并发绑定两个资源，并返回成功命令、409 命令和成功后的案件快照。

    两个请求各自创建客户端，避免在线程间共享 ``urllib`` opener。它们携带相同
    ``expectedRevision`` 和不同幂等键；正确结果必须是一条成功、一条 409。
    """

    commands = (
        {
            "label": "装载任务",
            "resourceType": "LOAD_PLANNING_TASK",
            "resourceNumber": task_number,
        },
        {
            "label": "LINK 草稿",
            "resourceType": "LINK_BOOKING_APPLICATION",
            "resourceNumber": application_number,
        },
    )

    def submit(command: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "resourceType": command["resourceType"],
            "resourceNumber": command["resourceNumber"],
            "expectedRevision": expected_revision,
        }
        try:
            snapshot = post_idempotent_case_command(
                BffClient(),
                f"{case_path}/resources",
                payload,
                f"shipment-case-e2e-race-{uuid.uuid4()}",
                quiet=quiet,
            )
        except HttpStatusError as exc:
            require(
                exc.status == 409,
                f"{command['label']} 并发绑定没有返回预期的 HTTP 409",
            )
            return {
                "outcome": "CONFLICT",
                "payload": payload,
                "label": command["label"],
            }
        return {
            "outcome": "SUCCESS",
            "payload": payload,
            "label": command["label"],
            "snapshot": snapshot,
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, commands))

    successes = [result for result in results if result["outcome"] == "SUCCESS"]
    conflicts = [result for result in results if result["outcome"] == "CONFLICT"]
    require(
        len(successes) == 1 and len(conflicts) == 1,
        "相同 revision 的两个并发绑定必须恰好一条成功、一条返回 409",
    )

    success = successes[0]
    conflict = conflicts[0]
    snapshot = require_object(
        success.get("snapshot"),
        "并发绑定成功结果缺少案件快照",
    )
    return success, conflict, snapshot


def run_e2e(client: BffClient, *, quiet: bool = False) -> None:
    """执行 Shipment Case 从创建到完成的真实平台闭环。"""

    token = (
        f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-"
        f"{uuid.uuid4().hex[:6].upper()}"
    )
    task_number = f"LP-SHIP-E2E-{token}"
    application_number = f"DEMO-SHIP-E2E-{token}"
    case_number = f"SHP-E2E-{token}"
    case_path = f"{SHIPMENT_CASES_PATH}/{case_number}"

    log(f"1/11 创建合成装载任务 {task_number}。", quiet=quiet)
    load_task = create_load_task_or_recover(
        client,
        task_number,
        quiet=quiet,
    )
    load_status_at_binding = load_task["status"]

    log(f"2/11 创建 LINK 合成草稿 {application_number}。", quiet=quiet)
    link_application = create_link_draft_or_recover(
        client,
        application_number,
        quiet=quiet,
    )
    link_status_at_binding = link_application["status"]

    log(f"3/11 使用固定幂等键创建发运案件 {case_number}。", quiet=quiet)
    create_payload = {
        "caseNumber": case_number,
        "caseName": "上海至洛杉矶合成发运闭环",
        "transportScenario": "SEA_FCL",
        "description": "仅用于本机 Shipment Case 真实 E2E 验收。",
    }
    create_key = f"shipment-case-e2e-create-{uuid.uuid4()}"
    created = post_idempotent_case_command(
        client,
        SHIPMENT_CASES_PATH,
        create_payload,
        create_key,
        expected_statuses=(201,),
        quiet=quiet,
    )
    case_id = created.get("id")
    require(isinstance(case_id, str) and case_id, "创建案件响应缺少 id")
    require(created.get("caseNumber") == case_number, "创建案件编号不一致")
    require(created.get("status") == "DRAFT", "新建案件不是 DRAFT")
    created_revision = require_revision(created, "新建案件缺少有效 revision")
    require(created_revision == 1, "新建案件 revision 应为 1")

    log("4/11 显式重放创建命令，确认案件 id 和 revision 不变。", quiet=quiet)
    replayed_create = post_idempotent_case_command(
        client,
        SHIPMENT_CASES_PATH,
        create_payload,
        create_key,
        expected_statuses=(201,),
        quiet=quiet,
    )
    require_case_identity(
        replayed_create,
        case_id=case_id,
        case_number=case_number,
        message="同一创建幂等键返回了不同案件",
    )
    require(
        require_revision(replayed_create, "创建重放缺少有效 revision")
        == created_revision,
        "创建重放错误地推进了 revision",
    )

    log(
        "5/11 并发提交装载与 LINK 绑定，必须一条成功、一条返回 409。",
        quiet=quiet,
    )
    first_binding, losing_binding, first_bound_snapshot = race_resource_bindings(
        case_path,
        expected_revision=created_revision,
        task_number=task_number,
        application_number=application_number,
        quiet=quiet,
    )
    first_binding_revision = require_revision(
        first_bound_snapshot,
        "并发绑定成功后缺少有效 revision",
    )
    require(
        first_binding_revision == created_revision + 1,
        "并发绑定的成功请求没有把 revision 精确推进一次",
    )

    after_race = require_object(
        client.request("GET", case_path),
        "并发绑定后案件详情 data 必须是对象",
    )
    require(
        require_revision(after_race, "并发绑定后案件缺少有效 revision")
        == first_binding_revision,
        "并发绑定后案件 revision 与成功响应不一致",
    )
    require(
        isinstance(after_race.get("resourceBindings"), list)
        and len(after_race["resourceBindings"]) == 1,
        "并发绑定后必须只有一个资源真正写入",
    )
    require(
        isinstance(after_race.get("events"), list)
        and len(after_race["events"]) == 2,
        "并发绑定中的 409 不应新增案件事件",
    )

    log(
        "6/11 用最新 revision 绑定另一资源，并验证 no-op 幂等键不能改作其他请求。",
        quiet=quiet,
    )
    losing_payload = dict(losing_binding["payload"])
    losing_payload["expectedRevision"] = first_binding_revision
    all_bound = post_idempotent_case_command(
        client,
        f"{case_path}/resources",
        losing_payload,
        f"shipment-case-e2e-bind-loser-{uuid.uuid4()}",
        quiet=quiet,
    )
    all_bindings_revision = require_revision(
        all_bound,
        "完成第二个资源绑定后缺少有效 revision",
    )
    require(
        all_bindings_revision == first_binding_revision + 1,
        "第二个资源绑定没有把 revision 精确推进一次",
    )

    noop_key = f"shipment-case-e2e-bind-noop-{uuid.uuid4()}"
    noop_payload = dict(first_binding["payload"])
    noop_payload["expectedRevision"] = all_bindings_revision
    noop_result = post_idempotent_case_command(
        client,
        f"{case_path}/resources",
        noop_payload,
        noop_key,
        quiet=quiet,
    )
    require(
        require_revision(noop_result, "重复资源 no-op 响应缺少有效 revision")
        == all_bindings_revision,
        "重复绑定同一资源的 no-op 不应推进 revision",
    )

    reused_key_payload = dict(losing_payload)
    reused_key_payload["expectedRevision"] = all_bindings_revision
    try:
        post_idempotent_case_command(
            client,
            f"{case_path}/resources",
            reused_key_payload,
            noop_key,
            quiet=quiet,
        )
    except HttpStatusError as exc:
        require(
            exc.status == 409,
            "no-op 成功使用过的幂等键改作其他请求时没有返回 HTTP 409",
        )
    else:
        raise ContractError("no-op 成功使用过的幂等键被错误复用于另一绑定请求")

    after_noop = require_object(
        client.request("GET", case_path),
        "no-op 幂等校验后案件详情 data 必须是对象",
    )
    require(
        require_revision(after_noop, "no-op 幂等校验后缺少有效 revision")
        == all_bindings_revision,
        "no-op 或幂等键冲突错误地推进了 revision",
    )
    require(
        isinstance(after_noop.get("events"), list)
        and len(after_noop["events"]) == 3,
        "no-op 或幂等键冲突不应新增业务事件",
    )

    log("7/11 使用旧 revision 推进状态，必须得到 HTTP 409。", quiet=quiet)
    stale_payload = {
        "targetStatus": "ACTIVE",
        # 两个资源都绑定后当前 revision 已变化；这里故意提交第一条绑定后的旧值。
        "expectedRevision": first_binding_revision,
        "reason": "E2E 故意提交旧 revision，验证乐观并发保护。",
    }
    try:
        post_idempotent_case_command(
            client,
            f"{case_path}/status",
            stale_payload,
            f"shipment-case-e2e-stale-{uuid.uuid4()}",
            quiet=quiet,
        )
    except HttpStatusError as exc:
        require(exc.status == 409, "旧 revision 没有返回预期的 HTTP 409")
    else:
        raise ContractError("旧 revision 意外推进了案件状态")

    after_conflict = require_object(
        client.request("GET", case_path),
        "冲突后案件详情 data 必须是对象",
    )
    require(after_conflict.get("status") == "DRAFT", "409 后案件状态被意外修改")
    require(
        require_revision(after_conflict, "409 后案件缺少有效 revision")
        == all_bindings_revision,
        "409 后案件 revision 被意外推进",
    )
    events_after_conflict = after_conflict.get("events")
    require(
        isinstance(events_after_conflict, list) and len(events_after_conflict) == 3,
        "409 冲突不应新增案件事件",
    )

    log("8/11 使用当前 revision 把案件推进到 ACTIVE。", quiet=quiet)
    active_payload = {
        "targetStatus": "ACTIVE",
        "expectedRevision": all_bindings_revision,
        "reason": "装载任务与 LINK 草稿均已核对，进入执行阶段。",
    }
    active_key = f"shipment-case-e2e-active-{uuid.uuid4()}"
    activated = post_idempotent_case_command(
        client,
        f"{case_path}/status",
        active_payload,
        active_key,
        quiet=quiet,
    )
    require(activated.get("status") == "ACTIVE", "案件未进入 ACTIVE")
    active_revision = require_revision(
        activated,
        "案件激活后缺少有效 revision",
    )
    require(
        active_revision == all_bindings_revision + 1,
        "激活案件没有把 revision 精确推进一次",
    )

    log("9/11 重放 ACTIVE 命令，确认没有第二次推进或重复事件。", quiet=quiet)
    replayed_active = post_idempotent_case_command(
        client,
        f"{case_path}/status",
        active_payload,
        active_key,
        quiet=quiet,
    )
    require_case_identity(
        replayed_active,
        case_id=case_id,
        case_number=case_number,
        message="ACTIVE 重放返回了不同案件",
    )
    require(replayed_active.get("status") == "ACTIVE", "ACTIVE 重放状态不一致")
    require(
        require_revision(replayed_active, "ACTIVE 重放缺少有效 revision")
        == active_revision,
        "ACTIVE 重放错误地推进了 revision",
    )
    replayed_active_events = replayed_active.get("events")
    require(
        isinstance(replayed_active_events, list)
        and len(replayed_active_events) == 4,
        "ACTIVE 重放产生了重复事件",
    )

    log("10/11 把 ACTIVE 案件推进到 COMPLETED。", quiet=quiet)
    completed = post_idempotent_case_command(
        client,
        f"{case_path}/status",
        {
            "targetStatus": "COMPLETED",
            "expectedRevision": active_revision,
            "reason": "E2E 发运案件闭环验收完成。",
        },
        f"shipment-case-e2e-complete-{uuid.uuid4()}",
        quiet=quiet,
    )
    require(completed.get("status") == "COMPLETED", "案件未进入 COMPLETED")
    completed_revision = require_revision(
        completed,
        "案件完成后缺少有效 revision",
    )
    require(
        completed_revision == active_revision + 1,
        "完成案件没有把 revision 精确推进一次",
    )

    log("11/11 GET 复核绑定快照、revision 和事件 sequence。", quiet=quiet)
    final_case = require_object(
        client.request("GET", case_path),
        "最终案件详情 data 必须是对象",
    )
    require_case_identity(
        final_case,
        case_id=case_id,
        case_number=case_number,
        message="最终 GET 返回了不同案件",
    )
    require(final_case.get("status") == "COMPLETED", "最终案件状态不是 COMPLETED")
    final_revision = require_revision(final_case, "最终案件缺少有效 revision")
    require(final_revision == completed_revision, "最终 GET 的 revision 与完成响应不一致")

    # 只有真正改变案件事实的五次命令才推进 revision。创建/状态重放、并发失败、
    # 同资源 no-op、幂等键冲突和旧 revision 冲突都不允许占用新的 revision。
    mutation_revisions = [
        created_revision,
        first_binding_revision,
        all_bindings_revision,
        active_revision,
        completed_revision,
    ]
    require(
        mutation_revisions == [1, 2, 3, 4, 5],
        "成功写命令的 revision 没有形成连续且唯一的 1..5",
    )
    require(
        len(set(mutation_revisions)) == len(mutation_revisions),
        "不同成功写命令出现了重复 revision",
    )

    bindings = final_case.get("resourceBindings")
    require(isinstance(bindings, list), "最终案件缺少 resourceBindings 数组")
    require(len(bindings) == 2, "最终案件没有且仅有两个资源绑定")
    binding_by_key = {
        (binding.get("resourceType"), binding.get("resourceNumber")): binding
        for binding in bindings
        if isinstance(binding, dict)
    }
    expected_binding_keys = {
        ("LOAD_PLANNING_TASK", task_number),
        ("LINK_BOOKING_APPLICATION", application_number),
    }
    require(
        set(binding_by_key) == expected_binding_keys,
        "最终案件的两个资源绑定与本次创建资源不一致",
    )

    load_binding = binding_by_key[("LOAD_PLANNING_TASK", task_number)]
    link_binding = binding_by_key[
        ("LINK_BOOKING_APPLICATION", application_number)
    ]
    require(
        load_binding.get("resourceStatusAtVerification") == load_status_at_binding,
        "装载任务的绑定时状态快照与源资源不一致",
    )
    require(
        load_binding.get("verificationSource")
        == "LOCAL_LOAD_PLANNING_DATABASE",
        "装载任务没有记录预期的本地验证来源",
    )
    require(
        link_binding.get("resourceStatusAtVerification") == link_status_at_binding,
        "LINK 的绑定时状态快照与源资源不一致",
    )
    require(
        link_binding.get("verificationSource") == "LINK_BOOKING_HTTP",
        "LINK 没有记录预期的 HTTP 验证来源",
    )

    events = final_case.get("events")
    require(isinstance(events, list), "最终案件缺少 events 数组")
    require(len(events) == 5, "重放或冲突产生了多余事件，或成功命令缺少事件")
    sequences = [
        event.get("sequence")
        for event in events
        if isinstance(event, dict)
    ]
    require(len(sequences) == len(events), "案件事件中存在非对象元素")
    require(
        all(type(sequence) is int for sequence in sequences),
        "案件事件 sequence 必须是整数",
    )
    require(
        len(set(sequences)) == len(sequences),
        "案件事件出现重复 sequence",
    )
    require(
        sequences == list(range(1, len(events) + 1)),
        "案件事件 sequence 没有按 1..N 连续排序",
    )
    require(
        [event.get("eventType") for event in events]
        == [
            "CASE_CREATED",
            "RESOURCE_BOUND",
            "RESOURCE_BOUND",
            "CASE_STATUS_CHANGED",
            "CASE_STATUS_CHANGED",
        ],
        "最终案件事件类型或顺序不符合闭环预期",
    )
    require(
        final_revision == sequences[-1],
        "最终 revision 与最后一条事件 sequence 不一致",
    )

    log(
        f"PASS：案件 {case_number} 完成两个资源绑定、真实并发竞态、"
        "no-op 幂等登记、409 保护和 COMPLETED 闭环。",
        quiet=quiet,
    )


def run_self_test() -> None:
    """离线验证两类请求采用不同且安全的重试策略。"""

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
            return {"id": "offline-case", "revision": 1}

    idempotent_client = FakeIdempotentClient()
    fixed_key = "offline-fixed-key"
    result = post_idempotent_case_command(
        idempotent_client,
        f"{SHIPMENT_CASES_PATH}/offline/status",
        {
            "targetStatus": "ACTIVE",
            "expectedRevision": 1,
            "reason": "offline",
        },
        fixed_key,
        attempts=2,
        quiet=True,
    )
    require(result["id"] == "offline-case", "离线幂等重试没有返回模拟结果")
    require(
        idempotent_client.keys == [fixed_key, fixed_key],
        "网络结果未知时 Shipment Case 命令没有复用原幂等键",
    )

    class FakeNonIdempotentClient:
        def __init__(self) -> None:
            self.methods: list[str] = []

        def request(
            self,
            method: str,
            path: str,
            *,
            payload: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            expected_statuses: Iterable[int] = (200,),
        ) -> dict[str, Any]:
            del path, payload, headers, expected_statuses
            self.methods.append(method)
            if method == "POST":
                raise NetworkOutcomeUnknown("simulated")
            return {
                "taskNumber": "LP-OFFLINE",
                "status": "DRAFT",
            }

    non_idempotent_client = FakeNonIdempotentClient()
    recovered = create_load_task_or_recover(
        non_idempotent_client,
        "LP-OFFLINE",
        quiet=True,
    )
    require(recovered["taskNumber"] == "LP-OFFLINE", "离线 GET 恢复失败")
    require(
        non_idempotent_client.methods == ["POST", "GET"],
        "非幂等前置资源创建响应未知时错误地重发了 POST",
    )
    print(
        "[shipment-case-e2e] self-test PASS：案件命令复用原 key，"
        "非幂等资源创建只用 GET 恢复。"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只通过本机 8080 验证 Shipment Case 真实平台闭环。"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="不访问网络，只检查幂等重试与非幂等恢复策略。",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="只读取三条平台链路确认就绪，不创建任何测试数据。",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=30,
        help="等待平台三条读取链路就绪的最长秒数；默认 30，0 表示只尝试一次。",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="成功时不输出，失败仍返回非零退出码。",
    )
    args = parser.parse_args()
    if args.wait_seconds < 0:
        parser.error("--wait-seconds 不能小于 0")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            run_self_test()
            return 0

        client = BffClient()
        if not probe_platform(
            client,
            wait_seconds=args.wait_seconds,
            quiet=args.quiet,
        ):
            return 1
        if args.probe_only:
            return 0
        run_e2e(client, quiet=args.quiet)
        return 0
    except E2EError as exc:
        if not args.quiet:
            print(
                f"[shipment-case-e2e] error: {exc}",
                file=sys.stderr,
                flush=True,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
