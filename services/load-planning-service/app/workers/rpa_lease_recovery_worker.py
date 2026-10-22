"""统一 RPA Job 的进程内租约恢复器。"""

from __future__ import annotations

import asyncio
import logging

from app.services.rpa_jobs import RpaJobService

logger = logging.getLogger("uvicorn.error")


class RpaLeaseRecoveryWorker:
    """随 FastAPI 启停的轻量受控循环。

    它只做两件事：

    * 回收浏览器 Worker 崩溃后留下的过期 ``RUNNING`` 租约；
    * 到达 ``retry_after_seconds`` 后，把明确安全的 ``RETRYABLE_FAILED`` 重新入队。

    它不运行 Playwright/Selenium，也不会在 ``RESULT_UNKNOWN`` 时再次点击外部网站。
    """

    def __init__(
        self,
        *,
        service: RpaJobService,
        poll_seconds: float,
        batch_size: int,
    ) -> None:
        self.service = service
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size
        self._stop_event = asyncio.Event()

    async def run_once(self) -> int:
        """完成一轮租约恢复和到期重试，返回本轮处理的 Job 数。"""

        expired = await self.service.recover_expired_leases(
            limit=self.batch_size,
        )
        remaining = max(self.batch_size - len(expired), 0)
        retried = await self.service.requeue_due_retryable(limit=remaining) if remaining > 0 else []
        return len(expired) + len(retried)

    async def run_forever(self) -> None:
        """持续运行到应用 lifespan 请求停止。"""

        logger.info(
            "RPA 租约恢复器已启动：poll=%ss batch=%s",
            self.poll_seconds,
            self.batch_size,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    processed = await self.run_once()
                    if processed:
                        logger.info("RPA 租约恢复器本轮处理 %s 条任务", processed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # 单轮数据库异常不能让整个 FastAPI 进程退出；下一轮仍会继续恢复。
                    logger.exception("RPA 租约恢复批次失败")

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            logger.info("RPA 租约恢复器已停止")

    def stop(self) -> None:
        """请求后台循环在当前轮结束后退出。"""

        self._stop_event.set()
