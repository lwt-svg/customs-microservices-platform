"""智能装载 FastAPI 微服务入口。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.api.agent_runs import router as agent_runs_router
from app.api.load_planning_tasks import router as load_planning_tasks_router
from app.api.packing_list_imports import router as packing_list_imports_router
from app.api.plan_versions import router as plan_versions_router
from app.api.shipment_agent_runs import router as shipment_agent_runs_router
from app.api.shipment_cases import router as shipment_cases_router
from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations.nacos_registry import NacosServiceRegistry
from app.integrations.shipment_agent_resources import HttpLinkBookingClient
from app.services.shipment_agent_automation import ShipmentAgentAutomationService
from app.services.shipment_agent_runs import ShipmentAgentService
from app.workers.shipment_agent_wake_worker import ShipmentAgentWakeWorker
from app.workflows.shipment_case import ShipmentWorkflowOrchestrator

logger = logging.getLogger("uvicorn.error")


async def _stop_wake_worker(
    worker: ShipmentAgentWakeWorker,
    task: asyncio.Task[None],
) -> None:
    """先请求优雅停止；超时后取消任务，未完成租约会在数据库中自动过期。"""

    worker.stop()
    try:
        await asyncio.wait_for(task, timeout=5)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """初始化工作流 checkpoint、注册 Nacos，并在停止时统一释放资源。

    SQLite checkpointer 是嵌入式文件，不是另一项要手工启动的基础设施。它只记录
    LangGraph 的运行游标；AgentRun、方案坐标和正式版本仍保存在业务数据库。

    ``AsyncExitStack`` 统一管理异步资源的关闭顺序：退出 lifespan 时先执行后注册的
    Nacos close，再退出 SQLite Saver 上下文。即使启动或关闭中途抛出异常，已进入的
    资源也会按栈顺序释放。
    """

    async with AsyncExitStack() as stack:
        if settings.load_planning_langgraph_enabled:
            checkpoint_path = settings.load_planning_langgraph_checkpoint_path
            if checkpoint_path != ":memory:":
                # 配置允许相对路径和 ``~``。启动时统一解析成绝对路径，并提前创建目录，
                # 让日志显示的路径和实际 SQLite 文件位置一致。
                resolved_path = Path(checkpoint_path).expanduser().resolve()
                resolved_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path = str(resolved_path)
            checkpointer = await stack.enter_async_context(
                AsyncSqliteSaver.from_conn_string(checkpoint_path)
            )
            await checkpointer.setup()
            app.state.load_planning_checkpointer = checkpointer
            logger.info("LangGraph checkpoint 已就绪：%s", checkpoint_path)
        else:
            app.state.load_planning_checkpointer = None
            logger.info("LangGraph 编排已关闭，写请求使用原有 Service 路径。")

        if (
            settings.shipment_agent_wake_worker_enabled
            and app.state.load_planning_checkpointer is not None
        ):
            automation = ShipmentAgentAutomationService(SessionLocal)
            shipment_service = ShipmentAgentService(
                SessionLocal,
                HttpLinkBookingClient(
                    settings.link_booking_internal_base_url,
                    timeout_seconds=(settings.shipment_resource_verify_timeout_seconds),
                ),
                app.state.load_planning_checkpointer,
            )
            shipment_workflow = ShipmentWorkflowOrchestrator(
                service=shipment_service,
                checkpointer=app.state.load_planning_checkpointer,
                automation=automation,
            )
            wake_worker = ShipmentAgentWakeWorker(
                automation=automation,
                orchestrator=shipment_workflow,
                poll_seconds=settings.shipment_agent_wake_poll_seconds,
                batch_size=settings.shipment_agent_wake_batch_size,
            )
            wake_task = asyncio.create_task(
                wake_worker.run_forever(),
                name="shipment-agent-wake-worker",
            )
            app.state.shipment_agent_wake_worker = wake_worker
            stack.push_async_callback(
                _stop_wake_worker,
                wake_worker,
                wake_task,
            )
        else:
            app.state.shipment_agent_wake_worker = None
            logger.info("Shipment Agent 自动唤醒器已关闭。")

        registry: NacosServiceRegistry | None = None
        if settings.nacos_discovery_enabled:
            registry = NacosServiceRegistry(settings)
            await registry.register()
            app.state.nacos_registry = registry
            logger.info(
                "Nacos 服务注册完成：service=%s group=%s address=%s:%s",
                settings.nacos_service_name,
                settings.nacos_group,
                settings.nacos_register_ip,
                settings.nacos_register_port,
            )
        if registry is not None:
            stack.push_async_callback(registry.close)
        yield


app = FastAPI(
    title="Load Planning Service",
    version="0.1.0",
    description="箱规、托盘主数据与装载任务服务；当前阶段不输出安全结论。",
    lifespan=lifespan,
)
# httpx 的轻量 ASGI 单元测试若没有显式 lifespan manager，不会执行上面的启动逻辑。
# 先放一个进程内 saver 可避免这类测试访问未定义属性；真正由 Uvicorn 启动时，
# lifespan 会在首个请求前把它替换为持久化 SQLite saver。
app.state.load_planning_checkpointer = InMemorySaver()

app.include_router(load_planning_tasks_router, prefix=settings.api_prefix)
app.include_router(packing_list_imports_router, prefix=settings.api_prefix)
app.include_router(agent_runs_router, prefix=settings.api_prefix)
app.include_router(plan_versions_router, prefix=settings.api_prefix)
app.include_router(shipment_cases_router, prefix=settings.api_prefix)
app.include_router(shipment_agent_runs_router, prefix=settings.api_prefix)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """进程存活探针，不访问数据库。"""

    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    """当前阶段的轻量就绪探针；数据库深度探针后续随可观测性章节补充。"""

    return {"status": "ready", "service": settings.app_name}
