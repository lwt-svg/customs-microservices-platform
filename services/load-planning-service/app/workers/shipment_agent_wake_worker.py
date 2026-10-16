"""Shipment Agent 数据库租约唤醒器。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.services.shipment_agent_automation import (
    ShipmentAgentAutomationService,
    ShipmentAgentWakeLease,
)
from app.workflows.shipment_case import ShipmentWorkflowOrchestrator

logger = logging.getLogger("uvicorn.error")


class ShipmentAgentWakeWorker:
    """随 FastAPI lifespan 运行的轻量后台检查器。

    Worker 不调用人工 ``reconcile`` API，也不写命令台账。它使用数据库租约领取到期运行，
    调用工作流的只读自动检查入口；没有发现业务变化时，run_version、Trace 和通知都保持
    不变。单轮失败只会退避重试，不会结束 FastAPI 进程。
    """

    def __init__(
        self,
        *,
        automation: ShipmentAgentAutomationService,
        orchestrator: ShipmentWorkflowOrchestrator,
        poll_seconds: float,
        batch_size: int,
    ) -> None:
        self.automation = automation
        self.orchestrator = orchestrator
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size
        self._stop_event = asyncio.Event()

    async def run_once(self, *, now: datetime | None = None) -> int:
        """最多处理 ``batch_size`` 条，但每次只在即将执行前领取一条。

        旧实现会先租下整批再串行处理；排在后面的运行可能尚未开始就已经过了租期。
        单条即时领取使租约只覆盖当前正在处理的运行，也让其他实例可以公平处理剩余任务。
        """

        processed = 0
        while processed < self.batch_size:
            leases = await self.automation.claim_due(limit=1, now=now)
            if not leases:
                break
            await self._process_lease(leases[0], now=now)
            processed += 1
        return processed

    async def _process_lease(
        self,
        lease: ShipmentAgentWakeLease,
        *,
        now: datetime | None,
    ) -> None:
        heartbeat_stop = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_lease(
                lease,
                stop_event=heartbeat_stop,
                lease_lost=lease_lost,
                base_now=now,
            ),
            name=f"shipment-agent-lease-heartbeat:{lease.run_id}",
        )
        try:
            await self.orchestrator.automatic_wake(lease.run_id)
            if lease_lost.is_set():
                logger.warning(
                    "Shipment Agent 租约已失效，跳过迟到投影：run_id=%s",
                    lease.run_id,
                )
                return
            projected = await self.automation.project(
                lease.run_id,
                lease_token=lease.lease_token,
                successful_wake=True,
                now=now,
            )
            if not projected:
                logger.warning(
                    "Shipment Agent 投影前已失去租约：run_id=%s",
                    lease.run_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception(
                "Shipment Agent 自动唤醒失败：run_id=%s error=%s",
                lease.run_id,
                type(error).__name__,
            )
            await self.automation.mark_wake_failed(
                lease,
                error,
                now=now,
            )
        finally:
            heartbeat_stop.set()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                heartbeat_task.cancel()
                raise

    async def _heartbeat_lease(
        self,
        lease: ShipmentAgentWakeLease,
        *,
        stop_event: asyncio.Event,
        lease_lost: asyncio.Event,
        base_now: datetime | None,
    ) -> None:
        """处理较慢时续租；续租失败后由过期租约恢复机制重新接管。

        ``base_now`` 只供“把测试时钟推进到未来”的单轮测试使用。生产循环传 ``None``，
        每次续租都读取真实 UTC 时间。测试模式则在基准时间上叠加真实经过时长，避免续租
        把一条未来租约错误缩短回本机当前时间。
        """

        interval_seconds = max(
            min(self.automation.lease_seconds / 3, 5.0),
            0.01,
        )
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=interval_seconds,
                )
                return
            except TimeoutError:
                pass

            heartbeat_now = (
                None if base_now is None else base_now + timedelta(seconds=loop.time() - started_at)
            )
            try:
                renewed = await self.automation.renew_lease(
                    lease,
                    now=heartbeat_now,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                lease_lost.set()
                logger.exception(
                    "Shipment Agent 租约续期失败：run_id=%s",
                    lease.run_id,
                )
                return
            if not renewed:
                lease_lost.set()
                logger.warning(
                    "Shipment Agent 租约续期被拒绝：run_id=%s",
                    lease.run_id,
                )
                return

    async def run_forever(self) -> None:
        """运行到应用关闭；停止信号和任务取消都能及时退出。"""

        logger.info(
            "Shipment Agent 自动唤醒器已启动：poll=%ss batch=%s",
            self.poll_seconds,
            self.batch_size,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Shipment Agent 自动唤醒批次失败")

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            logger.info("Shipment Agent 自动唤醒器已停止")

    def stop(self) -> None:
        """请求后台循环在当前轮结束后退出。"""

        self._stop_event.set()
