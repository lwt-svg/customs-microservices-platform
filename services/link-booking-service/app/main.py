"""本地 LINK FastAPI 微服务的程序入口。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.booking_applications import router as booking_applications_router
from app.api.booking_document_imports import router as booking_document_imports_router
from app.api.carrier_callbacks import router as carrier_callbacks_router
from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations.nacos_registry import NacosServiceRegistry
from app.repositories.carrier_submissions import CarrierSubmissionRepository
from app.services.carrier_submissions import CarrierSubmissionService

# 使用 Uvicorn 已配置为 INFO 的日志通道，确保本地启动时能看到注册结果。
# 这里只记录服务名、地址和端口等运行状态；绝不记录 Nacos 用户名、密码或 Token。
logger = logging.getLogger("uvicorn.error")


async def run_carrier_dispatcher() -> None:
    """周期重试到期任务；每轮使用独立会话，网络等待期间不持有数据库事务。"""

    while True:
        try:
            async with SessionLocal() as session:
                service = CarrierSubmissionService(CarrierSubmissionRepository(session))
                retried = await service.retry_due()
                if retried:
                    # 只记录数量，绝不输出幂等键、回调 Token 或未知响应内容。
                    logger.info("模拟承运人到期任务重试完成：count=%s", len(retried))
        except asyncio.CancelledError:
            raise
        except Exception:
            # worker 单轮失败不能让 FastAPI 退出；具体任务错误已用脱敏字段写入任务表。
            logger.exception("模拟承运人后台重试轮次失败")
        await asyncio.sleep(settings.carrier_dispatch_retry_interval_seconds)


# 异步上下文管理器让程序在启动时注册服务、关闭时注销服务。
# 避免在导入模块时注册，否则静态检查或单元测试也会意外访问 Nacos。
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """在应用启动时初始化基础设施，并在停止时执行清理。"""

    registry: NacosServiceRegistry | None = None
    carrier_dispatcher_task: asyncio.Task[None] | None = None

    if settings.nacos_discovery_enabled:
        # 只有进程真正启动时才注册，导入 Python 模块或静态检查时不会访问 Nacos。
        # 注册器集中接收运行期配置，内部不再到处读取全局环境变量。
        registry = NacosServiceRegistry(settings)
        await registry.register()
        # 放到 app.state 仅用于运行期观察；业务 API 不应直接依赖它。
        app.state.nacos_registry = registry
        logger.info(
            "Nacos 服务注册完成：service=%s group=%s address=%s:%s",
            settings.nacos_service_name,
            settings.nacos_group,
            settings.nacos_register_ip,
            settings.nacos_register_port,
        )

    if settings.carrier_dispatch_retry_enabled:
        carrier_dispatcher_task = asyncio.create_task(
            run_carrier_dispatcher(),
            name="carrier-dispatcher",
        )
        app.state.carrier_dispatcher_task = carrier_dispatcher_task
        logger.info(
            "模拟承运人后台重试已启动：interval_seconds=%s",
            settings.carrier_dispatch_retry_interval_seconds,
        )

    try:
        # yield 前是启动阶段；yield 后是收到 Ctrl+C 等正常停止信号后的关闭阶段。
        yield
    finally:
        if carrier_dispatcher_task is not None:
            carrier_dispatcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await carrier_dispatcher_task
        if registry is not None:
            await registry.close()

app = FastAPI(
    title="LINK Booking Service",
    version="0.1.0",
    description="Local-only booking service built with synthetic data.",
    lifespan=lifespan,
)

# 所有业务 API 都位于版本化前缀下；健康检查保持无版本，便于容器探针调用。
app.include_router(booking_applications_router, prefix=settings.api_prefix)
app.include_router(booking_document_imports_router, prefix=settings.api_prefix)
app.include_router(carrier_callbacks_router, prefix=settings.api_prefix)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """当 Python 进程仍存活时返回成功。"""

    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    """临时就绪检查；MySQL 与 Nacos 的就绪判断将在后续小节补充。"""

    return {"status": "ready", "service": settings.app_name}
