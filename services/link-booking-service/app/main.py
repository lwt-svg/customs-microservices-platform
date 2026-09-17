"""本地 LINK FastAPI 微服务的程序入口。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.booking_applications import router as booking_applications_router
from app.core.config import settings
from app.integrations.nacos_registry import NacosServiceRegistry

# 使用 Uvicorn 已配置为 INFO 的日志通道，确保本地启动时能看到注册结果。
# 这里只记录服务名、地址和端口等运行状态；绝不记录 Nacos 用户名、密码或 Token。
logger = logging.getLogger("uvicorn.error")

# 异步上下文管理器。这样让程序知道在启动时注册服务，在关闭时注销服务。避免在导入模块时就注册，导致静态检查或测试时访问 Nacos。
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """在应用启动时初始化基础设施，并在停止时执行清理。"""

    registry: NacosServiceRegistry | None = None

    if settings.nacos_discovery_enabled:
        # 只有进程真正启动时才注册，导入 Python 模块或静态检查时不会访问 Nacos。
        registry = NacosServiceRegistry(settings)   # 传入settings对象，创建NacosServiceRegistry实例
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

    try:
        # yield 前是启动阶段；yield 后是收到 Ctrl+C 等正常停止信号后的关闭阶段。
        yield
    finally:
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


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """当 Python 进程仍存活时返回成功。"""

    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    """临时就绪检查；MySQL 与 Nacos 的就绪判断将在后续小节补充。"""

    return {"status": "ready", "service": settings.app_name}
