"""统一 RPA Job 的 Playwright Sandbox Worker。

运行方式：

.. code-block:: powershell

   python -m app.rpa.browser_worker --domain LINK
   python -m app.rpa.browser_worker --domain SINGLE_WINDOW --ukey-present

Worker 只负责“领取任务 → 操作网页 → 回报事实”。它不运行 LangGraph，也不修改
Shipment Run。LangGraph 仍由服务端根据 RPA Job 的权威状态决定等待、请求人工或继续。

当前 Adapter 只允许 ``SANDBOX``，目标是本仓库的模拟页面。以后接真实授权网站时应新增
独立 SelectorPack/Handler；不能把真实账号、Cookie、卡口令或企业 URL 写进本文件。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import socket
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from playwright.async_api import (
    Browser,
    Page,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.api.rpa_sandbox import (
    LINK_SANDBOX_FINGERPRINT,
    SINGLE_WINDOW_SANDBOX_FINGERPRINT,
)
from app.rpa.contracts import (
    RpaBusinessOutcome,
    RpaDomain,
    RpaEnvironment,
    RpaJobStatus,
    RpaOperation,
    RpaSideEffectPhase,
)

logger = logging.getLogger("rpa-browser-worker")


class WorkerExecutionError(Exception):
    """浏览器 Worker 可以映射为稳定 RPA 状态的异常。"""

    status = RpaJobStatus.FAILED
    error_code = "WORKER_EXECUTION_FAILED"

    def __init__(
        self,
        message: str,
        *,
        side_effect_phase: RpaSideEffectPhase = RpaSideEffectPhase.READ_ONLY,
    ) -> None:
        super().__init__(message)
        self.side_effect_phase = side_effect_phase


class WaitingHumanError(WorkerExecutionError):
    """缺少登录态、UKey 或必须由业务人员完成的动作。"""

    status = RpaJobStatus.WAITING_HUMAN
    error_code = "LOGIN_OR_DEVICE_REQUIRED"


class SiteVersionUnknownError(WorkerExecutionError):
    """页面指纹或关键语义选择器与 SelectorPack 不匹配。"""

    status = RpaJobStatus.SITE_VERSION_UNKNOWN
    error_code = "SELECTOR_PACK_MISMATCH"


class RetryableBrowserError(WorkerExecutionError):
    """尚未开始外部写操作时发生的临时浏览器/网络错误。"""

    status = RpaJobStatus.RETRYABLE_FAILED
    error_code = "TRANSIENT_BROWSER_ERROR"


class ResultUnknownError(WorkerExecutionError):
    """外部保存已经开始，但最终结果无法确认。"""

    status = RpaJobStatus.RESULT_UNKNOWN
    error_code = "SAVE_RESULT_UNKNOWN"


class BusinessPayloadValidationError(WorkerExecutionError):
    """Job 载荷缺少网页保存所需的确定性必填字段。"""

    error_code = "BUSINESS_PAYLOAD_INVALID"


class ExternalSaveRejectedError(WorkerExecutionError):
    """外部页面明确返回“未保存”，结果已知但不能自动假装成功。"""

    error_code = "EXTERNAL_SAVE_REJECTED"


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """单个 Worker 进程的不可变运行配置。"""

    domain: RpaDomain
    control_base_url: str
    sandbox_base_url: str
    worker_id: str
    headless: bool
    browser_channel: str | None
    poll_seconds: float
    request_timeout_seconds: float
    browser_timeout_ms: int
    ukey_present: bool
    evidence_directory: Path

    @property
    def operations(self) -> tuple[RpaOperation, ...]:
        if self.domain is RpaDomain.LINK:
            return (RpaOperation.LINK_BOOKING_STAGE,)
        return (RpaOperation.COO_STAGE, RpaOperation.CIQ_STAGE)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """领取接口返回的私有执行信封。"""

    job_id: str
    attempt_token: UUID
    idempotency_key: str
    operation: RpaOperation
    environment: RpaEnvironment
    adapter_key: str
    selector_pack_version: str
    business_reference: str
    side_effect_phase: RpaSideEffectPhase
    business_payload: dict[str, Any]

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> ClaimedJob:
        job = payload["job"]
        return cls(
            job_id=str(job["job_id"]),
            attempt_token=UUID(str(payload["attempt_token"])),
            idempotency_key=str(payload["idempotency_key"]),
            operation=RpaOperation(job["operation"]),
            environment=RpaEnvironment(job["environment"]),
            adapter_key=str(job["adapter_key"]),
            selector_pack_version=str(job["selector_pack_version"]),
            business_reference=str(job["business_reference"]),
            side_effect_phase=RpaSideEffectPhase(job["side_effect_phase"]),
            business_payload=dict(payload["business_payload"]),
        )


@dataclass(frozen=True, slots=True)
class BrowserExecutionResult:
    business_outcome: RpaBusinessOutcome
    external_reference: str
    page_fingerprint: str
    evidence: dict[str, object]


class RpaControlPlaneClient:
    """只封装 Worker 需要的四个 HTTP 动作，不读取页面控制台接口。"""

    def __init__(self, config: WorkerConfig) -> None:
        self.config = config

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.control_base_url.rstrip("/"),
            timeout=self.config.request_timeout_seconds,
        )

    async def claim(self) -> ClaimedJob | None:
        body = {
            "domain": self.config.domain.value,
            "environment": RpaEnvironment.SANDBOX.value,
            "worker_id": self.config.worker_id,
            "supported_operations": [
                operation.value for operation in self.config.operations
            ],
        }
        async with self._client() as client:
            response = await client.post("/api/v1/rpa/worker/jobs/claim", json=body)
        response.raise_for_status()
        if response.json() is None:
            return None
        return ClaimedJob.from_response(response.json())

    async def progress(
        self,
        job: ClaimedJob,
        *,
        current_step: str,
        side_effect_phase: RpaSideEffectPhase,
        message: str,
        page_fingerprint: str | None = None,
        evidence: dict[str, object] | None = None,
    ) -> None:
        async with self._client() as client:
            response = await client.post(
                f"/api/v1/rpa/worker/jobs/{quote(job.job_id, safe='')}/progress",
                json={
                    "attempt_token": str(job.attempt_token),
                    "current_step": current_step,
                    "side_effect_phase": side_effect_phase.value,
                    "message": message,
                    "page_fingerprint": page_fingerprint,
                    "evidence": evidence or {},
                },
            )
        response.raise_for_status()

    async def result(
        self,
        job: ClaimedJob,
        *,
        callback_id: UUID,
        status: RpaJobStatus,
        side_effect_phase: RpaSideEffectPhase,
        message: str,
        business_outcome: RpaBusinessOutcome | None = None,
        error_code: str | None = None,
        external_reference: str | None = None,
        evidence: dict[str, object] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        """同一 callback ID 最多重发三次，避免响应丢失制造第二次业务结果。"""

        body = {
            "callback_id": str(callback_id),
            "attempt_token": str(job.attempt_token),
            "status": status.value,
            "side_effect_phase": side_effect_phase.value,
            "business_outcome": (
                business_outcome.value if business_outcome is not None else None
            ),
            "message": message,
            "error_code": error_code,
            "external_reference": external_reference,
            "evidence": evidence or {},
            "retry_after_seconds": retry_after_seconds,
        }
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with self._client() as client:
                    response = await client.post(
                        f"/api/v1/rpa/worker/jobs/{quote(job.job_id, safe='')}/result",
                        json=body,
                    )
                response.raise_for_status()
                return
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error
                if attempt < 3:
                    await asyncio.sleep(float(attempt))
        if last_error is not None:
            raise last_error


class SandboxBrowserHandler:
    """LINK 与单一窗口 Sandbox 共用的浏览器安全骨架。"""

    def __init__(
        self,
        config: WorkerConfig,
        control_client: RpaControlPlaneClient,
    ) -> None:
        self.config = config
        self.control_client = control_client

    async def execute(
        self,
        browser: Browser,
        job: ClaimedJob,
    ) -> BrowserExecutionResult:
        try:
            self._validate_job(job)
            fields = self._business_fields(job)
            self._validate_required_fields(job, fields)
        except WorkerExecutionError as error:
            # 重试任务可能已经处于 LOCAL_PREPARED；任何本地校验失败都不能把控制面阶段
            # 倒退回 READ_ONLY。
            error.side_effect_phase = job.side_effect_phase
            raise
        page = await browser.new_page()
        page.set_default_timeout(self.config.browser_timeout_ms)
        current_phase = job.side_effect_phase
        fingerprint: str | None = None
        try:
            await self.control_client.progress(
                job,
                current_step="OPENING_SANDBOX_SITE",
                side_effect_phase=current_phase,
                message="正在打开版本化 RPA Sandbox 页面。",
            )
            await page.goto(self._site_url(job), wait_until="domcontentloaded")
            fingerprint = await self._verify_fingerprint(page, job)
            await self.control_client.progress(
                job,
                current_step="PAGE_VERSION_VERIFIED",
                side_effect_phase=current_phase,
                message="页面指纹与 SelectorPack 匹配。",
                page_fingerprint=fingerprint,
            )
            await self._login(page, job)
            await self._fill_fields(page, fields)
            # 这两个值是 Worker 与 Sandbox API 的控制字段，不应展示给业务人员。
            # Playwright 的 fill() 会等待元素可见，不能用于 type=hidden，因此直接在
            # 页面上下文设置 value；业务输入字段仍坚持使用真实 fill() 行为。
            await self._set_hidden_value(
                page,
                "[data-rpa='idempotency-key']",
                job.idempotency_key,
            )
            await self._set_hidden_value(
                page,
                "[data-rpa='business-reference']",
                job.business_reference
            )
            await self.control_client.progress(
                job,
                current_step="FORM_PREPARED",
                side_effect_phase=RpaSideEffectPhase.LOCAL_PREPARED,
                message="业务字段已填入页面，尚未点击保存或暂存。",
                page_fingerprint=fingerprint,
            )
            current_phase = RpaSideEffectPhase.LOCAL_PREPARED

            # 在点击前先把控制面推进到“可能发生外部写入”。如果进程恰好在这两句之间
            # 崩溃，系统会保守进入 RESULT_UNKNOWN，而不是冒险自动再点一次。
            await self.control_client.progress(
                job,
                current_step="EXTERNAL_SAVE_ARMED",
                side_effect_phase=RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED,
                message="即将执行草稿保存；从此刻开始禁止盲目自动重试。",
                page_fingerprint=fingerprint,
            )
            current_phase = RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED
            await page.locator(self._save_selector(job)).click()
            result = page.locator("[data-rpa='save-result']")
            await result.wait_for(state="visible")
            await page.wait_for_function(
                """() => document.querySelector("[data-rpa='save-result']")
                    ?.dataset.status === "saved"
                  || document.querySelector("[data-rpa='save-result']")
                    ?.dataset.status === "error" """
            )
            save_status = await result.get_attribute("data-status")
            if save_status == "error":
                raise ExternalSaveRejectedError(
                    "Sandbox 明确拒绝保存：" + (await result.inner_text()).strip(),
                    side_effect_phase=current_phase,
                )
            external_reference = await result.get_attribute("data-external-reference")
            if not external_reference:
                raise ResultUnknownError("Sandbox 已返回保存状态，但缺少外部草稿编号")
            try:
                evidence = await self._capture_evidence(page, job, "succeeded")
            except (OSError, PlaywrightError) as error:
                # 外部草稿编号已经被页面确认。截图失败只降低证据完整度，不能把一个已经
                # 确认成功的业务结果错误地改成 RESULT_UNKNOWN。
                logger.warning(
                    "RPA evidence capture failed after confirmed save: job_id=%s error=%s",
                    job.job_id,
                    type(error).__name__,
                )
                evidence = {
                    "evidence_capture": "FAILED_AFTER_CONFIRMED_SAVE",
                    "worker_platform": platform.system(),
                    "browser_engine": "chromium",
                }
            return BrowserExecutionResult(
                business_outcome=self._business_outcome(job),
                external_reference=external_reference,
                page_fingerprint=fingerprint,
                evidence=evidence,
            )
        except WorkerExecutionError as error:
            error.side_effect_phase = current_phase
            raise
        except PlaywrightTimeoutError as error:
            if current_phase is RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED:
                raise ResultUnknownError(
                    "点击保存后页面未能确认结果，禁止自动再次点击",
                    side_effect_phase=current_phase,
                ) from error
            raise RetryableBrowserError(
                "打开或填写 Sandbox 页面超时",
                side_effect_phase=current_phase,
            ) from error
        except PlaywrightError as error:
            if current_phase is RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED:
                raise ResultUnknownError(
                    "保存阶段浏览器连接中断，外部结果需要对账",
                    side_effect_phase=current_phase,
                ) from error
            raise RetryableBrowserError(
                "浏览器在外部写入前暂时不可用",
                side_effect_phase=current_phase,
            ) from error
        finally:
            # 浏览器进程崩溃时 close() 也可能报错；不能让清理错误覆盖上面已经分类好的
            # RESULT_UNKNOWN / RETRYABLE_FAILED 等业务异常。
            with suppress(PlaywrightError):
                await page.close()

    def _validate_job(self, job: ClaimedJob) -> None:
        if job.environment is not RpaEnvironment.SANDBOX:
            raise WorkerExecutionError("当前浏览器 Worker 只允许执行 SANDBOX Job")
        if job.operation not in self.config.operations:
            raise WorkerExecutionError(
                f"{self.config.domain.value} Worker 不能执行 {job.operation.value}"
            )
        expected_adapter = {
            RpaDomain.LINK: "link-playwright",
            RpaDomain.SINGLE_WINDOW: "single-window-playwright",
        }[self.config.domain]
        if job.adapter_key != expected_adapter:
            raise SiteVersionUnknownError(
                f"Job adapter_key={job.adapter_key}，当前 Worker 只支持 {expected_adapter}"
            )

    def _site_url(self, job: ClaimedJob) -> str:
        base = self.config.sandbox_base_url.rstrip("/")
        if job.operation is RpaOperation.LINK_BOOKING_STAGE:
            return f"{base}/api/v1/rpa-sandbox/sites/link"
        ukey = "present" if self.config.ukey_present else "missing"
        return (
            f"{base}/api/v1/rpa-sandbox/sites/single-window"
            f"?operation={job.operation.value}&ukey={ukey}"
        )

    async def _verify_fingerprint(self, page: Page, job: ClaimedJob) -> str:
        expected_pack, expected_fingerprint = {
            RpaOperation.LINK_BOOKING_STAGE: (
                "link-sandbox-v1",
                LINK_SANDBOX_FINGERPRINT,
            ),
            RpaOperation.COO_STAGE: (
                "single-window-sandbox-v1",
                SINGLE_WINDOW_SANDBOX_FINGERPRINT,
            ),
            RpaOperation.CIQ_STAGE: (
                "single-window-sandbox-v1",
                SINGLE_WINDOW_SANDBOX_FINGERPRINT,
            ),
        }[job.operation]
        if job.selector_pack_version != expected_pack:
            raise SiteVersionUnknownError(
                f"Job 要求 {job.selector_pack_version}，Worker 只安装了 {expected_pack}"
            )
        fingerprint = await page.locator(
            "meta[name='rpa-page-fingerprint']"
        ).get_attribute("content")
        if fingerprint != expected_fingerprint:
            raise SiteVersionUnknownError("页面指纹与 SelectorPack 不匹配")
        return fingerprint

    async def _login(self, page: Page, job: ClaimedJob) -> None:
        if job.operation is RpaOperation.LINK_BOOKING_STAGE:
            await page.locator("[data-rpa='login-button']").click()
        else:
            ready = page.locator("[data-rpa='ukey-ready']")
            if not await ready.is_checked():
                raise WaitingHumanError(
                    "单一窗口需要 Windows 申报机插入 UKey，并由人工确认客户端登录态"
                )
            await page.locator("[data-rpa='card-login-button']").click()
        await page.locator("[data-rpa='workspace']").wait_for(state="visible")

    async def _fill_fields(
        self,
        page: Page,
        fields: dict[str, object],
    ) -> None:
        for name, raw_value in fields.items():
            if raw_value is None:
                continue
            locator = page.locator(f"[data-field='{name}']")
            if await locator.count() != 1:
                raise SiteVersionUnknownError(f"页面缺少唯一语义字段：{name}")
            await locator.fill(_render_field_value(raw_value))

    @staticmethod
    async def _set_hidden_value(
        page: Page,
        selector: str,
        value: str,
    ) -> None:
        locator = page.locator(selector)
        if await locator.count() != 1:
            raise SiteVersionUnknownError(f"页面缺少唯一控制字段：{selector}")
        await locator.evaluate(
            "(element, nextValue) => { element.value = nextValue; }",
            value,
        )

    def _business_fields(self, job: ClaimedJob) -> dict[str, object]:
        if job.operation is RpaOperation.LINK_BOOKING_STAGE:
            payload = job.business_payload
            details = payload.get("booking_details") or {}
            if not isinstance(details, dict):
                raise WorkerExecutionError("LINK booking_details 结构无效")
            return {
                "application_number": payload.get("application_number"),
                "origin_port": payload.get("origin_port"),
                "destination_port": payload.get("destination_port"),
                "transport_mode": payload.get("transport_mode"),
                "container_type": payload.get("container_type"),
                "requested_etd": payload.get("requested_etd"),
                "invoice_number": details.get("invoice_number"),
                "total_pallets": details.get("total_pallets"),
                "total_cartons": details.get("total_cartons"),
                "total_quantity": details.get("total_quantity"),
                "total_cbm": details.get("total_cbm"),
                "total_gross_weight_kg": details.get("total_gross_weight_kg"),
                "total_net_weight_kg": details.get("total_net_weight_kg"),
                "shipper": details.get("shipper"),
                "consignee": details.get("consignee"),
                "notify_party": details.get("notify_party"),
                "marks": details.get("marks"),
            }

        # COO/CIQ 的第一版接收标准订单转换后的扁平执行载荷。来源版式与网页流程由
        # Job 控制面校验并持久化，Worker 不再根据客户名称临时猜分支。
        payload = job.business_payload
        return {
            "order_number": payload.get("order_number"),
            "destination_country_code": payload.get("destination_country_code"),
            "party_name": payload.get("party_name"),
            "invoice_number": payload.get("invoice_number"),
            "invoice_date": payload.get("invoice_date"),
            "price_terms": payload.get("price_terms"),
            "currency": payload.get("currency"),
            "transport_mode": payload.get("transport_mode"),
            "transport_name": payload.get("transport_name"),
            "package_count": payload.get("package_count"),
            "gross_weight_kg": payload.get("gross_weight_kg"),
            "net_weight_kg": payload.get("net_weight_kg"),
            "source_profile": payload.get("source_profile"),
            "flow_profile": payload.get("flow_profile"),
        }

    @staticmethod
    def _validate_required_fields(
        job: ClaimedJob,
        fields: dict[str, object],
    ) -> None:
        """在打开网页前拒绝不完整业务载荷。

        浏览器页面自身不是业务校验器。如果 Worker 对缺字段的任务仍然点击 SAVE/STAGE，
        外站可能留下无法使用的草稿，控制面却会误报成功。因此这里按 operation 固定必填
        集合，并把失败稳定映射为 ``BUSINESS_PAYLOAD_INVALID``。
        """

        required_by_operation = {
            RpaOperation.LINK_BOOKING_STAGE: (
                "application_number",
                "origin_port",
                "destination_port",
                "transport_mode",
                "container_type",
            ),
            RpaOperation.COO_STAGE: (
                "order_number",
                "destination_country_code",
                "party_name",
                "source_profile",
                "flow_profile",
            ),
            RpaOperation.CIQ_STAGE: (
                "order_number",
                "destination_country_code",
                "party_name",
            ),
        }
        missing = [
            name
            for name in required_by_operation[job.operation]
            if fields.get(name) is None
            or (
                isinstance(fields.get(name), str)
                and not str(fields.get(name)).strip()
            )
        ]
        if missing:
            raise BusinessPayloadValidationError(
                "业务载荷缺少必填字段：" + ", ".join(missing)
            )

    @staticmethod
    def _save_selector(job: ClaimedJob) -> str:
        if job.operation is RpaOperation.LINK_BOOKING_STAGE:
            return "[data-rpa='save-draft']"
        return "[data-rpa='stage-draft']"

    @staticmethod
    def _business_outcome(job: ClaimedJob) -> RpaBusinessOutcome:
        return {
            RpaOperation.LINK_BOOKING_STAGE: RpaBusinessOutcome.LINK_DRAFT_SAVED,
            RpaOperation.COO_STAGE: RpaBusinessOutcome.COO_STAGED,
            RpaOperation.CIQ_STAGE: RpaBusinessOutcome.CIQ_STAGED,
        }[job.operation]

    async def _capture_evidence(
        self,
        page: Page,
        job: ClaimedJob,
        suffix: str,
    ) -> dict[str, object]:
        self.config.evidence_directory.mkdir(parents=True, exist_ok=True)
        filename = f"{job.job_id}-{job.attempt_token}-{suffix}.png"
        path = self.config.evidence_directory / filename
        await page.screenshot(path=str(path), full_page=True)
        return {
            "screenshot_ref": f"runtime/rpa-evidence/{filename}",
            "worker_platform": platform.system(),
            "browser_engine": "chromium",
        }


class BrowserRpaWorker:
    """按运行域轮询统一队列，并把浏览器结果映射回 RPA 状态机。"""

    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self.client = RpaControlPlaneClient(config)
        self.handler = SandboxBrowserHandler(config, self.client)

    async def run_once(self) -> bool:
        job = await self.client.claim()
        if job is None:
            return False

        callback_id = uuid4()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=self.config.headless,
                    channel=self.config.browser_channel,
                )
                try:
                    result = await self.handler.execute(browser, job)
                finally:
                    with suppress(PlaywrightError):
                        await browser.close()
            await self.client.result(
                job,
                callback_id=callback_id,
                status=RpaJobStatus.SUCCEEDED,
                side_effect_phase=RpaSideEffectPhase.EXTERNAL_EFFECT_CONFIRMED,
                business_outcome=result.business_outcome,
                message="Sandbox 页面已确认草稿/暂存成功。",
                external_reference=result.external_reference,
                evidence={
                    **result.evidence,
                    "page_fingerprint": result.page_fingerprint,
                },
            )
            logger.info(
                "RPA Job succeeded: job_id=%s operation=%s",
                job.job_id,
                job.operation.value,
            )
        except WorkerExecutionError as error:
            retry_after = 5 if error.status is RpaJobStatus.RETRYABLE_FAILED else None
            await self.client.result(
                job,
                callback_id=callback_id,
                status=error.status,
                side_effect_phase=error.side_effect_phase,
                message=str(error),
                error_code=error.error_code,
                retry_after_seconds=retry_after,
            )
            logger.warning(
                "RPA Job stopped: job_id=%s status=%s code=%s",
                job.job_id,
                error.status.value,
                error.error_code,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            # 控制面本身不可用时，无法安全伪造一个服务端状态。当前 attempt 会由租约恢复器
            # 根据已上报的 side_effect_phase 收敛；日志只记录类型，不输出 URL/载荷。
            logger.error(
                "RPA control-plane communication failed: job_id=%s error=%s",
                job.job_id,
                type(error).__name__,
            )
        return True

    async def run_forever(self) -> None:
        logger.info(
            "RPA Browser Worker ready: id=%s domain=%s headless=%s",
            self.config.worker_id,
            self.config.domain.value,
            self.config.headless,
        )
        while True:
            try:
                claimed = await self.run_once()
            except httpx.HTTPStatusError as error:
                logger.error(
                    "RPA control plane rejected worker request: status=%s",
                    error.response.status_code,
                )
                claimed = False
            except (httpx.TimeoutException, httpx.TransportError) as error:
                logger.warning(
                    "RPA control plane temporarily unavailable: %s",
                    type(error).__name__,
                )
                claimed = False
            if not claimed:
                await asyncio.sleep(self.config.poll_seconds)


def _render_field_value(value: object) -> str:
    if isinstance(value, (date, Decimal)):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_worker_id(domain: RpaDomain) -> str:
    machine = socket.gethostname().strip() or "local-machine"
    return f"{domain.value.lower()}-{machine}"[:128]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TradeOps RPA Sandbox Browser Worker")
    parser.add_argument(
        "--domain",
        required=True,
        choices=[domain.value for domain in RpaDomain],
    )
    parser.add_argument("--once", action="store_true", help="最多领取一票后退出")
    parser.add_argument("--headed", action="store_true", help="显示 Chromium 窗口")
    parser.add_argument(
        "--browser-channel",
        default=os.getenv("RPA_BROWSER_CHANNEL") or None,
        help="可选使用本机 Chrome/Edge 渠道，例如 chrome 或 msedge",
    )
    parser.add_argument(
        "--ukey-present",
        action="store_true",
        help="仅模拟单一窗口 Sandbox 的 UKey 已就绪；不读取真实卡口令",
    )
    parser.add_argument(
        "--control-base-url",
        default=os.getenv("RPA_CONTROL_BASE_URL", "http://127.0.0.1:8200"),
    )
    parser.add_argument(
        "--sandbox-base-url",
        default=os.getenv("RPA_SANDBOX_BASE_URL", "http://127.0.0.1:8200"),
    )
    parser.add_argument("--worker-id", default=None)
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("RPA_WORKER_POLL_SECONDS", "2")),
    )
    return parser.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    domain = RpaDomain(args.domain)
    config = WorkerConfig(
        domain=domain,
        control_base_url=args.control_base_url,
        sandbox_base_url=args.sandbox_base_url,
        worker_id=args.worker_id or _default_worker_id(domain),
        headless=not args.headed,
        browser_channel=args.browser_channel,
        poll_seconds=max(args.poll_seconds, 0.25),
        request_timeout_seconds=10,
        browser_timeout_ms=15_000,
        ukey_present=bool(args.ukey_present),
        evidence_directory=_repository_root() / "runtime" / "rpa-evidence",
    )
    worker = BrowserRpaWorker(config)
    if args.once:
        await worker.run_once()
        return 0
    await worker.run_forever()
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
