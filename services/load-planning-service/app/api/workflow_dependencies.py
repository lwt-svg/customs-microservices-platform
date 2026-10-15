"""智能装载 LangGraph 编排层的 FastAPI 依赖。

公开 HTTP 契约不暴露 thread_id、checkpoint 或 Node。路由只拿到一个 Facade，并继续
接收/返回原有 Pydantic 模型；Java BFF 和 Vue 因而无需为编排实现细节做任何改造。

依赖的生命周期分成两层：

* 请求级：FastAPI 为当前请求提供 ``AsyncSession``，再创建 Repository 和领域 Service；
* 应用级：``main.lifespan`` 只创建一个 SQLite checkpointer，供所有请求复用。

这样既不会把数据库 Session 放进 checkpoint，也不会为每次 HTTP 请求重复打开
checkpoint 文件连接。Orchestrator 和编译后的 Graph 可以随每个请求重新构造；持久化
状态并不保存在这些短生命周期 Python 对象里，而是由应用级共享 checkpointer 按
``thread_id`` 读取和保存，因此请求结束不会丢失流程位置。
"""

from typing import Annotated, Any

from fastapi import Depends, Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.plan_versions import LoadPlanVersionRepository
from app.services.agent_runs import StatefulPalletAgentService
from app.services.plan_versions import LoadPlanVersionService
from app.workflows.load_planning import LoadPlanningWorkflowOrchestrator


def get_load_planning_workflow(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoadPlanningWorkflowOrchestrator:
    """按请求绑定业务事务，并复用应用生命周期中的持久化 checkpointer。

    AsyncSession 不能放入 checkpoint，所以每次请求都会创建新的领域 Service；SQLite
    saver 则由 FastAPI lifespan 长期持有。两个领域 Service 共享当前请求的 Session，
    仍处在原来的事务边界内。

    关闭功能开关时仍注入一个不会被调用的内存 saver，让 Facade 保持单一构造方式，
    同时写请求直接委托给原有 Service。
    """

    checkpointer = getattr(request.app.state, "load_planning_checkpointer", None)
    if settings.load_planning_langgraph_enabled and checkpointer is None:
        raise RuntimeError("LangGraph 已启用，但应用 checkpointer 尚未初始化")
    if checkpointer is None:
        checkpointer = InMemorySaver()

    return LoadPlanningWorkflowOrchestrator(
        agent_service=StatefulPalletAgentService(AgentRunRepository(session)),
        plan_version_service=LoadPlanVersionService(LoadPlanVersionRepository(session)),
        checkpointer=checkpointer,
        enabled=settings.load_planning_langgraph_enabled,
    )


# 这两个别名只简化 FastAPI 类型声明；它们不会创建对象，也不会改变依赖生命周期。
WorkflowCheckpointer = BaseCheckpointSaver[Any]
WorkflowDependency = Annotated[
    LoadPlanningWorkflowOrchestrator,
    Depends(get_load_planning_workflow),
]
