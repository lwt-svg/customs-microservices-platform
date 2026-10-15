"""独立模拟承运人 FastAPI 程序入口。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.carrier_bookings import carrier_router, portal_router
from app.core.config import settings
from app.db.session import SessionLocal
from app.repositories.carrier_bookings import CarrierBookingRepository
from app.services.carrier_bookings import CallbackDeliveryService

logger = logging.getLogger("uvicorn.error")


async def _retry_due_callback_deliveries() -> None:
    """在一个短生命周期会话中处理到期或租约过期的回调。

    此函数不接收也不输出 Token；HTTP Header 仅在 ``CallbackDeliveryService`` 内存中创建。
    """

    async with SessionLocal() as session:
        service = CallbackDeliveryService(CarrierBookingRepository(session))
        await service.retry_due()


async def _callback_retry_worker() -> None:
    """每隔短周期领取一次到期任务，直到 FastAPI 正常关闭。"""

    while True:
        try:
            await _retry_due_callback_deliveries()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - worker 不能因未知单条任务错误而整体退出
            # 不拼接异常文本，避免数据库驱动或未知对端错误把秘密信息带入日志。
            logger.warning("模拟承运人回调重试轮询失败；下一周期将继续尝试")
        await asyncio.sleep(settings.carrier_callback_retry_poll_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动可配置的回调 worker，并在关闭时可靠取消、等待任务结束。"""

    worker: asyncio.Task[None] | None = None
    if settings.carrier_callback_retry_worker_enabled:
        worker = asyncio.create_task(
            _callback_retry_worker(),
            name="carrier-callback-retry-worker",
        )
        app.state.callback_retry_worker = worker
    try:
        yield
    finally:
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

app = FastAPI(
    title="LINK Carrier Simulator",
    version="0.1.0",
    description="仅本地使用的构造承运人门户与回调服务。",
    lifespan=lifespan,
)
app.include_router(carrier_router, prefix=settings.api_prefix)
app.include_router(portal_router, prefix=settings.api_prefix)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """进程存活探针，不依赖数据库或 LINK。"""

    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    """当前程序已启动并可接受请求；数据库迁移由启动步骤显式执行。"""

    return {"status": "ready", "service": settings.app_name}
