#!/usr/bin/env python3
"""通过 Java BFF 验证智能装载真实链路、状态化运行和正式版本闭环。

这个脚本只访问 127.0.0.1:8080。它不会直连 Python、Nacos 或 MySQL，
因此成功意味着请求确实经过了 BFF 的服务发现和字段转换。完整模式会验证两条
相互独立的业务链路：

1. READY_FOR_REVIEW → REJECTED → 重规划 → APPROVED → LOCKED V1；
2. AWAITING_INPUT → GET 恢复 → resume → READY_FOR_REVIEW → APPROVED → LOCKED V1。

第一条链路还会通过 list/current/detail 三条读取路径复核冻结快照。
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
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

BASE_URL = "http://127.0.0.1:8080"
TASKS_PATH = "/api/v1/platform/load-planning/tasks"


class E2EError(RuntimeError):
    """用户可读、且不会包含请求体或环境变量的验收错误。"""


class NetworkOutcomeUnknown(E2EError):
    """请求可能已到达服务端，但客户端没有收到完整响应。"""


class HttpStatusError(E2EError):
    """服务端明确返回了非 2xx 状态。"""

    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"{path} 明确返回 HTTP {status}")
        self.status = status
        self.path = path


class ContractError(E2EError):
    """BFF 返回成功 HTTP，但 JSON 契约不符合预期。"""


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
    """仅面向 Java BFF 的极小 JSON 客户端。"""

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
            # HTTPError means the server response is known. Do not label it as
            # a network-unknown result and do not retry a write automatically.
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

        expected: set[int] = set(expected_statuses)
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
    if not quiet:
        print(f"[e2e] {message}", flush=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def probe_chain(
    client: BffClient,
    *,
    wait_seconds: int,
    quiet: bool,
) -> bool:
    """轮询任务列表，验证 BFF→Nacos→Python→MySQL，而非只看端口。"""

    deadline = time.monotonic() + max(0, wait_seconds)
    last_error: E2EError | None = None
    while True:
        try:
            data = client.request("GET", TASKS_PATH)
            require(isinstance(data, list), "任务列表 data 必须是数组")
            log(
                f"真实链路已就绪：BFF 返回 {len(data)} 条装载任务摘要。",
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
            f"[e2e] error: 真实链路未就绪：{last_error or '未知错误'}。",
            file=sys.stderr,
            flush=True,
        )
    return False


def build_task_payload(task_number: str) -> dict[str, Any]:
    """生成完全脱敏的确定性测试数据，不读取企业文件或环境变量。"""

    return {
        "taskNumber": task_number,
        "taskName": "脚本真实链路验收",
        "description": "仅用于本地 BFF 端到端验收的构造数据。",
        "cargoItems": [
            {
                "itemCode": "E2E-ITEM-A",
                "skuCode": "E2E-SKU-A",
                "cargoName": "构造测试纸箱 A",
                "quantity": 36,
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
                "itemCode": "E2E-ITEM-B",
                "skuCode": "E2E-SKU-B",
                "cargoName": "构造测试纸箱 B",
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
                "palletCode": "E2E-PALLET-1200X1000",
                "palletName": "构造测试 1200×1000 托盘",
                "quantity": 20,
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


def build_awaiting_input_task_payload(task_number: str) -> dict[str, Any]:
    """生成会稳定触发三个结构化问题的脱敏任务。

    两种托盘让智能体必须询问本次使用哪一种；货物 A 缺少是否可堆叠，货物 B
    明确可堆叠但缺少最大层数。回答时选择约束完整的 B 托盘，因此 A 托盘故意
    缺少的承重和限高不会成为本次规划的问题。
    """

    return {
        "taskNumber": task_number,
        "taskName": "脚本追问恢复验收",
        "description": "仅用于验证缺约束暂停、恢复、审批和正式锁定的构造数据。",
        "cargoItems": [
            {
                "itemCode": "E2E-ASK-A",
                "skuCode": "E2E-ASK-SKU-A",
                "cargoName": "构造追问纸箱 A",
                "quantity": 24,
                "lengthMm": 300,
                "widthMm": 200,
                "heightMm": 180,
                "grossWeightKg": 8,
                "maxTopLoadKg": 160,
                "keepUpright": True,
                "fragile": False,
                "sourceType": "SYNTHETIC_DEMO",
                "sourceReference": None,
                "sourceLocator": None,
                "confidence": 1,
                "verified": False,
            },
            {
                "itemCode": "E2E-ASK-B",
                "skuCode": "E2E-ASK-SKU-B",
                "cargoName": "构造追问纸箱 B",
                "quantity": 12,
                "lengthMm": 250,
                "widthMm": 250,
                "heightMm": 200,
                "grossWeightKg": 10,
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
                "palletCode": "E2E-ASK-PALLET-A",
                "palletName": "构造待补充托盘 A",
                "quantity": 20,
                "lengthMm": 1200,
                "widthMm": 1000,
                "heightMm": 150,
                "tareWeightKg": 25,
                "sourceType": "SYNTHETIC_DEMO",
                "sourceReference": None,
                "sourceLocator": None,
                "confidence": 1,
                "verified": False,
            },
            {
                "palletCode": "E2E-ASK-PALLET-B",
                "palletName": "构造约束完整托盘 B",
                "quantity": 20,
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
            },
        ],
    }


def recover_created_task(
    client: BffClient,
    task_number: str,
    *,
    wait_seconds: int = 5,
) -> dict[str, Any]:
    """创建任务响应未知时只读取恢复，不盲目重复无幂等键的 POST。"""

    detail_path = f"{TASKS_PATH}/{task_number}"
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            task = client.request("GET", detail_path)
            require(isinstance(task, dict), "恢复的任务详情必须是对象")
            return task
        except HttpStatusError as exc:
            if exc.status != 404:
                raise
        if time.monotonic() >= deadline:
            raise NetworkOutcomeUnknown(
                "创建任务响应未知，读取后仍不能确认结果；为避免重复创建，脚本没有重发 POST"
            )
        time.sleep(0.5)


def create_task_or_recover(
    client: BffClient,
    task_number: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """创建任务；若写入响应未知，只按业务编号 GET 恢复，不重复 POST。"""

    try:
        task = client.request(
            "POST",
            TASKS_PATH,
            payload=payload,
            expected_statuses=(201,),
        )
    except NetworkOutcomeUnknown:
        task = recover_created_task(client, task_number)
    require(isinstance(task, dict), "创建任务 data 必须是对象")
    require(task.get("taskNumber") == task_number, "创建任务编号与请求不一致")
    return task


def create_agent_run_with_retry(
    client: Any,
    path: str,
    payload: dict[str, Any],
    idempotency_key: str,
    *,
    attempts: int = 3,
    quiet: bool = False,
) -> dict[str, Any]:
    """响应未知时重试创建运行，并保证每次都使用同一个幂等键。"""

    headers = {"Idempotency-Key": idempotency_key}
    for attempt in range(1, attempts + 1):
        try:
            result = client.request(
                "POST",
                path,
                payload=payload,
                headers=headers,
                expected_statuses=(201,),
            )
            require(isinstance(result, dict), "创建运行 data 必须是对象")
            return result
        except NetworkOutcomeUnknown:
            if attempt >= attempts:
                raise
            log(
                "创建运行的响应未知；将复用原 Idempotency-Key 重试，"
                "不会生成新的请求键。",
                quiet=quiet,
            )
            time.sleep(0.5)
    raise AssertionError("unreachable")


def recover_run_state(
    client: BffClient,
    run_id: str,
    expected_status: str,
    *,
    wait_seconds: int = 5,
) -> dict[str, Any]:
    """决策响应未知时，通过 GET 恢复权威状态，不重复提交决策。"""

    path = f"/api/v1/platform/load-planning/agent-runs/{run_id}"
    deadline = time.monotonic() + wait_seconds
    while True:
        run = client.request("GET", path)
        require(isinstance(run, dict), "恢复的运行快照必须是对象")
        if run.get("status") == expected_status:
            return run
        if time.monotonic() >= deadline:
            raise NetworkOutcomeUnknown(
                f"写入响应未知，且运行尚未进入 {expected_status}；脚本没有盲目重复提交"
            )
        time.sleep(0.5)


def recover_replanned_child(
    client: BffClient,
    task_number: str,
    parent_run_id: str,
    *,
    wait_seconds: int = 5,
) -> dict[str, Any]:
    """重规划响应未知时，从 active runs 查找父子关联的新运行。"""

    path = f"{TASKS_PATH}/{task_number}/agent-runs"
    deadline = time.monotonic() + wait_seconds
    while True:
        active_runs = client.request("GET", path)
        require(isinstance(active_runs, list), "active runs data 必须是数组")
        matches = [
            item
            for item in active_runs
            if isinstance(item, dict) and item.get("parentRunId") == parent_run_id
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ContractError("同一个父运行出现多个 active 重规划子运行")
        if time.monotonic() >= deadline:
            raise NetworkOutcomeUnknown(
                "重规划响应未知，且未找到父子关联的新运行；脚本没有盲目重复提交"
            )
        time.sleep(0.5)


def post_decision_or_recover(
    client: BffClient,
    run_id: str,
    payload: dict[str, Any],
    expected_status: str,
) -> dict[str, Any]:
    path = f"/api/v1/platform/load-planning/agent-runs/{run_id}/decision"
    try:
        result = client.request("POST", path, payload=payload)
        require(isinstance(result, dict), "决策 data 必须是对象")
        return result
    except NetworkOutcomeUnknown:
        return recover_run_state(client, run_id, expected_status)


def resume_run_or_recover(
    client: BffClient,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """恢复缺约束运行；响应未知时用 GET 查权威状态，不盲目重复答案。"""

    path = f"/api/v1/platform/load-planning/agent-runs/{run_id}/resume"
    try:
        result = client.request("POST", path, payload=payload)
        require(isinstance(result, dict), "恢复运行 data 必须是对象")
        return result
    except NetworkOutcomeUnknown:
        return recover_run_state(client, run_id, "READY_FOR_REVIEW")


def replan_with_retry(
    client: Any,
    task_number: str,
    parent_run_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    *,
    attempts: int = 3,
    quiet: bool = False,
) -> dict[str, Any]:
    """响应未知时复用同一个重规划幂等键；最终仍未知时再按父子链恢复。"""

    path = f"/api/v1/platform/load-planning/agent-runs/{parent_run_id}/replan"
    headers = {"Idempotency-Key": idempotency_key}
    for attempt in range(1, attempts + 1):
        try:
            result = client.request(
                "POST",
                path,
                payload=payload,
                headers=headers,
            )
            require(isinstance(result, dict), "重规划 data 必须是对象")
            return result
        except NetworkOutcomeUnknown:
            if attempt >= attempts:
                if isinstance(client, BffClient):
                    return recover_replanned_child(
                        client,
                        task_number,
                        parent_run_id,
                    )
                raise
            log(
                "重规划响应未知；将复用原 Idempotency-Key 重试，不会创建第二条子运行。",
                quiet=quiet,
            )
            time.sleep(0.5)
    raise AssertionError("unreachable")


def lock_plan_version_with_retry(
    client: Any,
    path: str,
    payload: dict[str, Any],
    *,
    attempts: int = 3,
    quiet: bool = False,
) -> dict[str, Any]:
    """正式锁定响应未知时重放相同请求，依靠 sourceRunId 业务幂等取回同一 V1。"""

    for attempt in range(1, attempts + 1):
        try:
            result = client.request(
                "POST",
                path,
                payload=payload,
                expected_statuses=(201,),
            )
            require(isinstance(result, dict), "正式锁定 data 必须是对象")
            return result
        except NetworkOutcomeUnknown:
            if attempt >= attempts:
                raise
            log(
                "正式锁定响应未知；将重放完全相同的锁定请求，"
                "服务端会按来源运行返回同一正式版本。",
                quiet=quiet,
            )
            time.sleep(0.5)
    raise AssertionError("unreachable")


def run_e2e(client: BffClient, *, quiet: bool = False) -> None:
    suffix = uuid.uuid4().hex[:6].upper()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_number = f"LP-E2E-{timestamp}-{suffix}"
    task_payload = build_task_payload(task_number)

    log("1/9 创建脱敏构造装载任务。", quiet=quiet)
    create_task_or_recover(client, task_number, task_payload)

    run_path = f"{TASKS_PATH}/{task_number}/agent-runs"
    run_payload = {
        "selectedPalletCode": "E2E-PALLET-1200X1000",
        "strategyCodes": ["STABLE", "BALANCED", "UTILIZATION"],
    }
    idempotency_key = f"load-planning-e2e-{uuid.uuid4()}"

    log("2/9 使用固定幂等键创建运行，并显式重放同一请求。", quiet=quiet)
    first_run = create_agent_run_with_retry(
        client,
        run_path,
        run_payload,
        idempotency_key,
        quiet=quiet,
    )
    replayed_run = create_agent_run_with_retry(
        client,
        run_path,
        run_payload,
        idempotency_key,
        quiet=quiet,
    )
    run_id = first_run.get("runId")
    require(isinstance(run_id, str) and run_id, "创建运行响应缺少 runId")
    require(replayed_run.get("runId") == run_id, "同一幂等键重放产生了不同 runId")
    require(first_run.get("status") == "READY_FOR_REVIEW", "首个运行未进入待复核状态")
    plans = first_run.get("plans")
    require(isinstance(plans, list) and len(plans) == 3, "首个运行应生成三套候选方案")
    require(
        all(
            isinstance(plan, dict)
            and plan.get("algorithmVersion") == "DETERMINISTIC_GRID_STRIP_V1"
            and plan.get("scoreVersion") == "PALLET_PLAN_SCORE_V1"
            for plan in plans
        ),
        "候选方案缺少预期的算法或评分版本",
    )

    log("3/9 验证 active runs 可恢复当前运行。", quiet=quiet)
    active_runs = client.request("GET", run_path)
    require(isinstance(active_runs, list), "active runs data 必须是数组")
    require(
        [item.get("runId") for item in active_runs if isinstance(item, dict)].count(
            run_id
        )
        == 1,
        "active runs 没有且仅有一条当前运行",
    )

    log("4/9 驳回第一条运行，并保留旧运行状态。", quiet=quiet)
    rejected = post_decision_or_recover(
        client,
        run_id,
        {
            "decision": "REJECT",
            "planCode": None,
            "comment": "E2E 验收：比较利用率方案后再批准。",
            "expectedRunVersion": first_run.get("runVersion"),
        },
        "REJECTED",
    )
    require(rejected.get("status") == "REJECTED", "运行未进入 REJECTED")

    log("5/9 使用固定幂等键创建父子关联重规划，并显式重放。", quiet=quiet)
    replan_payload = {
        "reason": "E2E 验收：只比较利用率策略。",
        "selectedPalletCode": "E2E-PALLET-1200X1000",
        "strategyCodes": ["UTILIZATION"],
    }
    replan_idempotency_key = f"load-planning-replan-e2e-{uuid.uuid4()}"
    replanned = replan_with_retry(
        client,
        task_number,
        run_id,
        replan_payload,
        replan_idempotency_key,
        quiet=quiet,
    )
    replayed_replan = replan_with_retry(
        client,
        task_number,
        run_id,
        replan_payload,
        replan_idempotency_key,
        quiet=quiet,
    )
    child_run_id = replanned.get("runId")
    require(isinstance(child_run_id, str) and child_run_id, "重规划响应缺少 runId")
    require(child_run_id != run_id, "重规划必须创建新的 runId")
    require(
        replayed_replan.get("runId") == child_run_id,
        "同一重规划幂等键重放产生了第二条子运行",
    )
    require(replanned.get("parentRunId") == run_id, "重规划没有保留 parentRunId")
    require(replanned.get("status") == "READY_FOR_REVIEW", "重规划未进入待复核状态")
    child_plans = replanned.get("plans")
    require(
        isinstance(child_plans, list) and len(child_plans) == 1,
        "利用率重规划应只有一套方案",
    )
    plan = child_plans[0]
    require(
        isinstance(plan, dict) and plan.get("feasible") is True, "重规划方案必须可行"
    )
    plan_code = plan.get("planCode")
    require(isinstance(plan_code, str) and plan_code, "重规划方案缺少 planCode")

    log("6/9 批准重规划方案。", quiet=quiet)
    approved = post_decision_or_recover(
        client,
        child_run_id,
        {
            "decision": "APPROVE",
            "planCode": plan_code,
            "comment": "E2E 验收批准。",
            "expectedRunVersion": replanned.get("runVersion"),
        },
        "APPROVED",
    )
    require(approved.get("status") == "APPROVED", "重规划运行未进入 APPROVED")
    require(approved.get("selectedPlanCode") == plan_code, "批准方案编号未保存")

    log("7/9 通过完整历史恢复两个终态运行。", quiet=quiet)
    run_history = client.request("GET", f"{run_path}/history")
    require(isinstance(run_history, list), "运行历史 data 必须是数组")
    history_by_id = {
        item.get("runId"): item
        for item in run_history
        if isinstance(item, dict) and isinstance(item.get("runId"), str)
    }
    require(
        history_by_id.get(run_id, {}).get("status") == "REJECTED",
        "完整历史没有保留原 REJECTED 运行",
    )
    require(
        history_by_id.get(child_run_id, {}).get("status") == "APPROVED",
        "完整历史没有恢复 APPROVED 重规划运行",
    )

    log("8/9 锁定正式 V1，重放请求并复核冻结快照。", quiet=quiet)
    lock_path = (
        f"/api/v1/platform/load-planning/agent-runs/{child_run_id}/plan-versions"
    )
    lock_payload = {
        "expectedRunVersion": approved.get("runVersion"),
        "expectedCurrentVersionNumber": 0,
        "lockReason": "E2E 验收：发布为不可变正式装载版本。",
    }
    locked = lock_plan_version_with_retry(
        client,
        lock_path,
        lock_payload,
        quiet=quiet,
    )
    replayed_lock = lock_plan_version_with_retry(
        client,
        lock_path,
        lock_payload,
        quiet=quiet,
    )
    version_id = locked.get("versionId")
    require(isinstance(version_id, str) and version_id, "正式版本响应缺少 versionId")
    require(replayed_lock.get("versionId") == version_id, "相同锁定请求生成了两个版本")
    require(locked.get("status") == "LOCKED", "正式版本状态不是 LOCKED")
    require(locked.get("versionNumber") == 1, "首次正式锁定没有生成 V1")
    require(locked.get("sourceRunId") == child_run_id, "正式版本来源运行不一致")
    require(locked.get("planCode") == plan_code, "正式版本来源方案不一致")
    require(
        locked.get("algorithmVersion") == "DETERMINISTIC_GRID_STRIP_V1"
        and locked.get("scoreVersion") == "PALLET_PLAN_SCORE_V1",
        "正式版本没有冻结预期算法和评分版本",
    )
    for hash_field in (
        "inputSnapshotHash",
        "constraintSnapshotHash",
        "planSnapshotHash",
    ):
        value = locked.get(hash_field)
        require(
            isinstance(value, str) and len(value) == 64,
            f"正式版本 {hash_field} 不是 64 位 SHA-256",
        )
    require(isinstance(locked.get("inputSnapshot"), dict), "正式版本缺少输入快照")
    require(
        isinstance(locked.get("constraintSnapshot"), dict),
        "正式版本缺少约束快照",
    )
    require(isinstance(locked.get("planSnapshot"), dict), "正式版本缺少方案快照")
    events = locked.get("events")
    require(
        isinstance(events, list)
        and len(events) == 1
        and isinstance(events[0], dict)
        and events[0].get("eventType") == "PLAN_VERSION_LOCKED",
        "正式版本没有且仅有一条锁定审计事件",
    )

    versions_path = f"{TASKS_PATH}/{task_number}/plan-versions"
    version_list = client.request("GET", versions_path)
    current_version = client.request("GET", f"{versions_path}/current")
    version_detail = client.request(
        "GET",
        f"/api/v1/platform/load-planning/plan-versions/{version_id}",
    )
    require(version_list == [locked], "正式版本列表与锁定响应不一致")
    require(current_version == locked, "当前正式版本与锁定响应不一致")
    require(version_detail == locked, "正式版本详情与锁定响应不一致")
    require(
        current_version.get("planSnapshotHash") == locked.get("planSnapshotHash"),
        "重新读取后方案快照哈希发生变化",
    )

    log("9/9 验证终态不在 active runs，锁定不改写 APPROVED。", quiet=quiet)
    final_active = client.request("GET", run_path)
    require(final_active == [], "全部运行进入终态后 active runs 应为空")
    original = client.request(
        "GET",
        f"/api/v1/platform/load-planning/agent-runs/{run_id}",
    )
    require(isinstance(original, dict), "旧运行详情必须是对象")
    require(original.get("status") == "REJECTED", "重规划不应覆盖旧运行状态")
    restored_approved = client.request(
        "GET",
        f"/api/v1/platform/load-planning/agent-runs/{child_run_id}",
    )
    require(
        isinstance(restored_approved, dict)
        and restored_approved.get("status") == "APPROVED",
        "正式锁定不应把来源运行的 APPROVED 改写为 LOCKED",
    )

    log(
        f"PASS：任务 {task_number} 完成幂等、恢复、驳回、重规划、批准和 LOCKED V1 闭环。",
        quiet=quiet,
    )


def run_awaiting_input_e2e(client: BffClient, *, quiet: bool = False) -> None:
    """验证缺约束暂停后从同一个 run 恢复，并完成审批和正式锁定。"""

    suffix = uuid.uuid4().hex[:6].upper()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_number = f"LP-E2E-ASK-{timestamp}-{suffix}"
    task_payload = build_awaiting_input_task_payload(task_number)

    log("追问场景 1/7 创建缺少三个规划约束的独立任务。", quiet=quiet)
    create_task_or_recover(client, task_number, task_payload)

    run_path = f"{TASKS_PATH}/{task_number}/agent-runs"
    run_payload = {
        "selectedPalletCode": None,
        "strategyCodes": ["STABLE", "BALANCED", "UTILIZATION"],
    }
    idempotency_key = f"load-planning-awaiting-input-e2e-{uuid.uuid4()}"

    log("追问场景 2/7 启动运行并确认暂停在 AWAITING_INPUT。", quiet=quiet)
    started = create_agent_run_with_retry(
        client,
        run_path,
        run_payload,
        idempotency_key,
        quiet=quiet,
    )
    run_id = started.get("runId")
    require(isinstance(run_id, str) and run_id, "追问运行响应缺少 runId")
    require(started.get("status") == "AWAITING_INPUT", "缺约束运行没有暂停")
    require(
        started.get("currentStep") == "AWAITING_CONSTRAINTS",
        "缺约束运行没有停在 AWAITING_CONSTRAINTS",
    )
    expected_question_keys = {
        "selected_pallet_code",
        "cargo.E2E-ASK-A.stackable",
        "cargo.E2E-ASK-B.max_stack_layers",
    }
    questions = started.get("questions")
    require(isinstance(questions, list), "缺约束运行 questions 必须是数组")
    require(
        {
            question.get("key")
            for question in questions
            if isinstance(question, dict)
        }
        == expected_question_keys,
        "缺约束运行没有返回预期的三个结构化问题",
    )
    require(started.get("plans") == [], "AWAITING_INPUT 时不应提前生成候选方案")

    log("追问场景 3/7 通过 GET 恢复同一运行的持久化状态。", quiet=quiet)
    restored = client.request(
        "GET",
        f"/api/v1/platform/load-planning/agent-runs/{run_id}",
    )
    require(isinstance(restored, dict), "GET 恢复的运行快照必须是对象")
    require(restored.get("runId") == run_id, "GET 恢复返回了不同 runId")
    require(restored.get("status") == "AWAITING_INPUT", "GET 未恢复暂停状态")
    restored_run_version = restored.get("runVersion")
    require(
        isinstance(restored_run_version, int),
        "GET 恢复的运行快照缺少整数 runVersion",
    )
    require(
        restored_run_version == started.get("runVersion"),
        "只读 GET 不应改变运行版本",
    )
    restored_questions = restored.get("questions")
    require(isinstance(restored_questions, list), "GET 恢复缺少 questions")
    require(
        {
            question.get("key")
            for question in restored_questions
            if isinstance(question, dict)
        }
        == expected_question_keys,
        "GET 恢复后结构化问题发生变化",
    )

    log("追问场景 4/7 补充答案并从同一个 run 恢复到 READY_FOR_REVIEW。", quiet=quiet)
    resumed = resume_run_or_recover(
        client,
        run_id,
        {
            "answers": [
                {
                    "key": "selected_pallet_code",
                    "value": "E2E-ASK-PALLET-B",
                },
                {
                    "key": "cargo.E2E-ASK-A.stackable",
                    "value": False,
                },
                {
                    "key": "cargo.E2E-ASK-B.max_stack_layers",
                    "value": 4,
                },
            ],
            "expectedRunVersion": restored_run_version,
        },
    )
    require(resumed.get("runId") == run_id, "resume 不应创建新的 runId")
    require(
        resumed.get("status") == "READY_FOR_REVIEW",
        "补充完整答案后未进入 READY_FOR_REVIEW",
    )
    require(resumed.get("questions") == [], "resume 后仍有未回答问题")
    require(
        resumed.get("selectedPalletCode") == "E2E-ASK-PALLET-B",
        "resume 没有保存人工选择的托盘",
    )
    require(
        resumed.get("runVersion") == restored_run_version + 1,
        "resume 没有推进运行版本",
    )
    plans = resumed.get("plans")
    require(
        isinstance(plans, list) and len(plans) == 3,
        "resume 后应生成三套候选方案",
    )
    feasible_plan = next(
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
    require(feasible_plan is not None, "resume 后没有可审批的可行方案")
    plan_code = feasible_plan["planCode"]

    log("追问场景 5/7 人工批准恢复后生成的可行方案。", quiet=quiet)
    approved = post_decision_or_recover(
        client,
        run_id,
        {
            "decision": "APPROVE",
            "planCode": plan_code,
            "comment": "E2E 追问恢复场景批准。",
            "expectedRunVersion": resumed.get("runVersion"),
        },
        "APPROVED",
    )
    require(approved.get("status") == "APPROVED", "追问运行未进入 APPROVED")
    require(approved.get("selectedPlanCode") == plan_code, "批准方案编号未保存")

    log("追问场景 6/7 将批准方案锁定为独立的正式 V1。", quiet=quiet)
    lock_path = f"/api/v1/platform/load-planning/agent-runs/{run_id}/plan-versions"
    lock_payload = {
        "expectedRunVersion": approved.get("runVersion"),
        "expectedCurrentVersionNumber": 0,
        "lockReason": "E2E 追问恢复场景：发布不可变正式版本。",
    }
    locked = lock_plan_version_with_retry(
        client,
        lock_path,
        lock_payload,
        quiet=quiet,
    )
    version_id = locked.get("versionId")
    require(isinstance(version_id, str) and version_id, "追问场景锁定缺少 versionId")
    require(locked.get("status") == "LOCKED", "追问场景正式版本不是 LOCKED")
    require(locked.get("versionNumber") == 1, "追问场景首次锁定没有生成 V1")
    require(locked.get("sourceRunId") == run_id, "追问场景正式版本来源运行不一致")
    require(locked.get("planCode") == plan_code, "追问场景正式版本来源方案不一致")

    log("追问场景 7/7 复核正式版本和终态运行均可重新读取。", quiet=quiet)
    versions_path = f"{TASKS_PATH}/{task_number}/plan-versions"
    current_version = client.request("GET", f"{versions_path}/current")
    version_detail = client.request(
        "GET",
        f"/api/v1/platform/load-planning/plan-versions/{version_id}",
    )
    require(current_version == locked, "追问场景当前正式版本与锁定响应不一致")
    require(version_detail == locked, "追问场景正式版本详情与锁定响应不一致")
    restored_approved = client.request(
        "GET",
        f"/api/v1/platform/load-planning/agent-runs/{run_id}",
    )
    require(
        isinstance(restored_approved, dict)
        and restored_approved.get("status") == "APPROVED",
        "正式锁定不应改写追问运行的 APPROVED 状态",
    )
    final_active = client.request("GET", run_path)
    require(final_active == [], "追问运行进入终态后 active runs 应为空")

    log(
        f"PASS：任务 {task_number} 完成缺约束暂停、GET 恢复、resume、批准和 LOCKED V1。",
        quiet=quiet,
    )


def run_self_test() -> None:
    """离线验证创建/重规划的请求幂等键和正式锁定业务幂等重试。"""

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def request(
            self,
            method: str,
            path: str,
            *,
            payload: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            expected_statuses: Iterable[int] = (200,),
        ) -> dict[str, Any]:
            del payload, expected_statuses
            key = (headers or {}).get("Idempotency-Key", "")
            self.calls.append((method, key))
            if len(self.calls) == 1:
                raise NetworkOutcomeUnknown("simulated")
            return {"runId": "offline-self-test"}

    fake = FakeClient()
    key = "offline-fixed-key"
    result = create_agent_run_with_retry(
        fake,
        f"{TASKS_PATH}/LP-OFFLINE/agent-runs",
        {"strategyCodes": ["STABLE"]},
        key,
        attempts=2,
        quiet=True,
    )
    require(result["runId"] == "offline-self-test", "离线重试没有返回模拟结果")
    require(len(fake.calls) == 2, "离线重试次数不正确")
    require(all(call_key == key for _, call_key in fake.calls), "离线重试轮换了幂等键")

    replan_fake = FakeClient()
    replan_key = "offline-replan-fixed-key"
    replanned = replan_with_retry(
        replan_fake,
        "LP-OFFLINE",
        "offline-parent",
        {"reason": "离线重规划"},
        replan_key,
        attempts=2,
        quiet=True,
    )
    require(replanned["runId"] == "offline-self-test", "离线重规划没有返回模拟结果")
    require(
        all(call_key == replan_key for _, call_key in replan_fake.calls),
        "离线重规划重试轮换了幂等键",
    )

    lock_fake = FakeClient()
    locked = lock_plan_version_with_retry(
        lock_fake,
        "/api/v1/platform/load-planning/agent-runs/offline/plan-versions",
        {
            "expectedRunVersion": 2,
            "expectedCurrentVersionNumber": 0,
            "lockReason": "离线锁定",
        },
        attempts=2,
        quiet=True,
    )
    require(locked["runId"] == "offline-self-test", "离线正式锁定没有返回模拟结果")
    require(
        all(not call_key for _, call_key in lock_fake.calls),
        "正式锁定不应伪造 HTTP Idempotency-Key",
    )
    print("[e2e] self-test PASS：创建/重规划复用请求幂等键，正式锁定重放相同业务请求。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只通过本机 8080 验证智能装载真实链路与状态化智能体闭环。"
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="只调用任务列表，验证 BFF→Nacos→Python→MySQL。",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="不访问网络，只检查幂等重试的脚本逻辑。",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=30,
        help="探活最长等待秒数；默认 30，0 表示只尝试一次。",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="成功时不输出，适合前台监督轮询。",
    )
    args = parser.parse_args()
    if args.wait_seconds < 0:
        parser.error("--wait-seconds 不能小于 0")
    if args.probe_only and args.self_test:
        parser.error("--probe-only 与 --self-test 不能同时使用")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            run_self_test()
            return 0

        client = BffClient()
        if not probe_chain(
            client,
            wait_seconds=args.wait_seconds,
            quiet=args.quiet,
        ):
            return 1
        if args.probe_only:
            return 0

        run_e2e(client, quiet=args.quiet)
        run_awaiting_input_e2e(client, quiet=args.quiet)
        return 0
    except E2EError as exc:
        if not args.quiet:
            print(f"[e2e] error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
