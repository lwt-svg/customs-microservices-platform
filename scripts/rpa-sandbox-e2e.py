#!/usr/bin/env python3
"""真实浏览器验证 LINK、产地证和商检 Sandbox RPA 闭环。

前置条件：

1. 已运行 ``./scripts/run-local.sh core`` 或 Windows 对应命令；
2. 已通过 setup 安装 Playwright Chromium；
3. 8200 必须是本机地址。本脚本拒绝把构造数据发送到远程或生产环境。

脚本经过统一 RPA Job HTTP API 派发任务，再启动真实 Playwright Worker，不会直接调用
Sandbox 草稿保存 API，也不会点击任何正式提交/申报按钮。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = REPOSITORY_ROOT / "services" / "load-planning-service"
TERMINAL_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "RESULT_UNKNOWN",
    "SITE_VERSION_UNKNOWN",
    "WAITING_HUMAN",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TradeOps RPA Sandbox E2E")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8200",
        help="本机 load-planning-service 地址",
    )
    parser.add_argument("--headed", action="store_true", help="显示 Chromium 操作过程")
    parser.add_argument(
        "--browser-channel",
        default=None,
        help="可选使用本机浏览器渠道，例如 macOS 的 chrome 或 Windows 的 msedge",
    )
    parser.add_argument("--timeout-seconds", type=int, default=45)
    return parser.parse_args()


def _require_loopback(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit("base-url 必须使用 http/https")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("安全拒绝：RPA Sandbox E2E 只能访问本机 loopback 地址")
    return base_url.rstrip("/")


def _dispatch(
    client: httpx.Client,
    *,
    operation: str,
    business_reference: str,
    adapter_key: str,
    selector_pack_version: str,
    business_payload: dict[str, object],
    source_profile: str | None = None,
    flow_profile: str | None = None,
) -> dict[str, Any]:
    domain = "LINK" if operation == "LINK_BOOKING_STAGE" else "SINGLE_WINDOW"
    # case_number 数据库字段上限为 64；业务参考可以更长，所以这里使用稳定短哈希式后缀。
    case_number = f"SHP-RPA-E2E-{uuid4().hex[:16].upper()}"
    response = client.post(
        "/api/v1/rpa/jobs",
        json={
            "case_number": case_number,
            "domain": domain,
            "operation": operation,
            "environment": "SANDBOX",
            "business_reference": business_reference,
            "side_effect_boundary": "DRAFT_ONLY",
            "adapter_key": adapter_key,
            "source_profile": source_profile,
            "flow_profile": flow_profile,
            "selector_pack_version": selector_pack_version,
            "payload_schema_version": "SANDBOX_E2E_V1",
            "max_attempts": 3,
            "business_payload": business_payload,
        },
    )
    response.raise_for_status()
    return response.json()


def _run_worker(
    domain: str,
    *,
    headed: bool,
    browser_channel: str | None,
    ukey_present: bool = False,
) -> None:
    command = [
        sys.executable,
        "-m",
        "app.rpa.browser_worker",
        "--domain",
        domain,
        "--once",
    ]
    if headed:
        command.append("--headed")
    if browser_channel:
        command.extend(["--browser-channel", browser_channel])
    if ukey_present:
        command.append("--ukey-present")
    completed = subprocess.run(command, cwd=SERVICE_ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"{domain} Worker 退出码为 {completed.returncode}")


def _wait_for_job(
    client: httpx.Client,
    job_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/rpa/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(0.5)
    raise SystemExit(f"等待 RPA Job {job_id} 超时")


def _assert_success(job: dict[str, Any], expected_outcome: str) -> None:
    if job["status"] != "SUCCEEDED":
        raise SystemExit(
            f"{job['operation']} 未成功：status={job['status']} "
            f"error={job.get('error_code')}"
        )
    if job["business_outcome"] != expected_outcome:
        raise SystemExit(
            f"{job['operation']} outcome 不匹配：{job['business_outcome']}"
        )
    print(
        f"[rpa-e2e] {job['operation']} -> {job['business_outcome']} "
        f"reference={job['external_reference']}"
    )


def main() -> int:
    args = _parse_args()
    base_url = _require_loopback(args.base_url)
    suffix = uuid4().hex[:10].upper()
    with httpx.Client(base_url=base_url, timeout=10) as client:
        health = client.get("/health/live")
        health.raise_for_status()

        link_reference = f"DEMO-RPA-LINK-{suffix}"
        link = _dispatch(
            client,
            operation="LINK_BOOKING_STAGE",
            business_reference=link_reference,
            adapter_key="link-playwright",
            selector_pack_version="link-sandbox-v1",
            business_payload={
                "payload_schema_version": "LINK_RPA_DRAFT_V1",
                "data_source_label": "SYNTHETIC_DEMO",
                "application_number": link_reference,
                "origin_port": "CNSHA",
                "destination_port": "FRLEH",
                "transport_mode": "SEA",
                "container_type": "40HQ",
                "requested_etd": "2026-11-15",
                "booking_details": {
                    "invoice_number": f"DEMO-INV-{suffix}",
                    "total_pallets": 4,
                    "total_cartons": 80,
                    "total_quantity": 800,
                    "total_cbm": 42.5,
                    "total_net_weight_kg": 7200,
                    "total_gross_weight_kg": 7800,
                    "shipper": "DEMO SHIPPER",
                    "consignee": "DEMO CONSIGNEE",
                    "notify_party": "DEMO NOTIFY PARTY",
                    "marks": "DEMO MARKS ONLY",
                    "items": [],
                    "packages": [],
                    "checks": [],
                },
            },
        )
        _run_worker(
            "LINK",
            headed=args.headed,
            browser_channel=args.browser_channel,
        )
        _assert_success(
            _wait_for_job(
                client,
                link["job_id"],
                timeout_seconds=args.timeout_seconds,
            ),
            "LINK_DRAFT_SAVED",
        )

        coo_reference = f"DEMO-RPA-COO-{suffix}"
        coo = _dispatch(
            client,
            operation="COO_STAGE",
            business_reference=coo_reference,
            adapter_key="single-window-playwright",
            selector_pack_version="single-window-sandbox-v1",
            source_profile="wudi_form_e",
            flow_profile="STANDARD",
            business_payload={
                "data_source_label": "SYNTHETIC_DEMO",
                "order_number": coo_reference,
                "destination_country_code": "FR",
                "party_name": "DEMO CONSIGNEE",
                "invoice_number": f"DEMO-INV-{suffix}",
                "invoice_date": "2026-10-22",
                "price_terms": "FOB",
                "currency": "USD",
                "transport_mode": "SEA",
                "transport_name": "DEMO VESSEL",
                "package_count": 80,
                "gross_weight_kg": 7800,
                "net_weight_kg": 7200,
                "source_profile": "wudi_form_e",
                "flow_profile": "STANDARD",
            },
        )
        _run_worker(
            "SINGLE_WINDOW",
            headed=args.headed,
            browser_channel=args.browser_channel,
            ukey_present=True,
        )
        _assert_success(
            _wait_for_job(
                client,
                coo["job_id"],
                timeout_seconds=args.timeout_seconds,
            ),
            "COO_STAGED",
        )

        ciq_reference = f"DEMO-RPA-CIQ-{suffix}"
        ciq = _dispatch(
            client,
            operation="CIQ_STAGE",
            business_reference=ciq_reference,
            adapter_key="single-window-playwright",
            selector_pack_version="single-window-sandbox-v1",
            business_payload={
                "data_source_label": "SYNTHETIC_DEMO",
                "order_number": ciq_reference,
                "destination_country_code": "FR",
                "party_name": "DEMO RECEIVER",
                "invoice_number": f"DEMO-INV-{suffix}",
                "invoice_date": "2026-10-22",
                "price_terms": "FOB",
                "currency": "USD",
                "transport_mode": "SEA",
                "transport_name": "DEMO VESSEL",
                "package_count": 80,
                "gross_weight_kg": 7800,
                "net_weight_kg": 7200,
                "source_profile": "CIQ_DEMO_V1",
                "flow_profile": "STANDARD",
            },
        )
        _run_worker(
            "SINGLE_WINDOW",
            headed=args.headed,
            browser_channel=args.browser_channel,
            ukey_present=True,
        )
        _assert_success(
            _wait_for_job(
                client,
                ciq["job_id"],
                timeout_seconds=args.timeout_seconds,
            ),
            "CIQ_STAGED",
        )

    print("[rpa-e2e] LINK / COO / CIQ 三条真实浏览器 Sandbox 闭环全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
