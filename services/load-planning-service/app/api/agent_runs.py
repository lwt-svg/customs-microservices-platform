"""状态化装托智能体 HTTP API。

本文件故意保留两种依赖：

* 查询接口直接使用 ``StatefulPalletAgentService``，因为读业务事实不需要推进 Graph；
* create/resume/decision/replan 使用 ``WorkflowDependency``，由 LangGraph 选择和恢复
  正确的人工节点，再调用同一个领域 Service。

因此 URL、请求体和响应体没有因为接入 LangGraph 而变化，Java BFF 无需了解 checkpoint。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.repositories.agent_runs import AgentRunRepository
from app.schemas.agent_run import (
    AgentRunCreate,
    AgentRunDecision,
    AgentRunRead,
    AgentRunReplan,
    AgentRunResume,
)
from app.schemas.plan_insight import PlanInsightRead, PlanInsightRequest
from app.services.agent_runs import (
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentRunValidationError,
    LoadPlanningTaskNotFoundError,
    StatefulPalletAgentService,
)
from app.services.plan_insights import (
    PlanInsightsDisabledError,
    PlanInsightService,
    RuleBasedPlanInsightProvider,
    create_plan_insight_provider,
)
from app.workflows.load_planning import LoadPlanningWorkflowStateError

from .workflow_dependencies import WorkflowDependency

router = APIRouter(prefix="/load-planning", tags=["load-planning-agent"])


def get_agent_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatefulPalletAgentService:
    """按请求创建状态机 Service；同一次调用只使用一个数据库事务。"""

    return StatefulPalletAgentService(AgentRunRepository(session))


ServiceDependency = Annotated[StatefulPalletAgentService, Depends(get_agent_service)]


def get_plan_insight_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanInsightService:
    """创建只读 Copilot Service。

    Provider 选择属于接口基础设施，不进入 LangGraph。规则 Provider 同时显式作为降级
    路径；外部模型不可用时不会阻断方案查看和审批。
    """

    return PlanInsightService(
        AgentRunRepository(session),
        create_plan_insight_provider(settings),
        enabled=settings.load_planning_plan_insights_enabled,
        fallback_provider=RuleBasedPlanInsightProvider(),
    )


PlanInsightServiceDependency = Annotated[
    PlanInsightService,
    Depends(get_plan_insight_service),
]


def _raise_http(error: Exception) -> None:
    """把领域异常集中映射成稳定的 404/409/422。

    编排状态不匹配属于 409：资源存在，但客户端动作与当前流程阶段冲突。字段或业务规则
    校验失败属于 422；找不到任务/运行才是 404。
    """

    if isinstance(error, (LoadPlanningTaskNotFoundError, AgentRunNotFoundError)):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, AgentRunConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, LoadPlanningWorkflowStateError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, AgentRunValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, PlanInsightsDisabledError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    raise error  # pragma: no cover - 防止未来新增异常被静默吞掉


@router.post(
    "/tasks/{task_number}/agent-runs",
    response_model=AgentRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_run(
    task_number: str,
    payload: AgentRunCreate,
    workflow: WorkflowDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentRunRead:
    """启动运行；若约束齐全，会在同一请求中直接产生候选方案。

    同一任务重试时可带 ``Idempotency-Key``。它不会写入任务主数据，只用来保证一次
    网络请求最多创建一条 AgentRun。
    """

    try:
        return await workflow.create(task_number, payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get("/agent-runs/{run_id}", response_model=AgentRunRead)
async def get_agent_run(
    run_id: str,
    service: ServiceDependency,
) -> AgentRunRead:
    """读取可恢复的当前状态、全部事件和方案。"""

    try:
        return await service.get(run_id)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/agent-runs/{run_id}/plan-insights",
    response_model=PlanInsightRead,
)
async def create_plan_insight(
    run_id: str,
    payload: PlanInsightRequest,
    service: PlanInsightServiceDependency,
) -> PlanInsightRead:
    """解释一套已经生成的候选方案。

    本端点是只读 Copilot，不保存模型输出、不追加运行事件、不改变审批状态，也不会把
    输出交给 LangGraph。刷新页面会重新生成一次瞬时说明，而业务事实始终来自 AgentRun
    和确定性算法。
    """

    try:
        return await service.generate(run_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get("/tasks/{task_number}/agent-runs", response_model=list[AgentRunRead])
async def list_active_agent_runs(
    task_number: str,
    service: ServiceDependency,
) -> list[AgentRunRead]:
    """列出任务当前仍可继续的运行，按最近更新时间倒序。"""

    try:
        return await service.list_active(task_number)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/tasks/{task_number}/agent-runs/history",
    response_model=list[AgentRunRead],
)
async def list_agent_run_history(
    task_number: str,
    service: ServiceDependency,
) -> list[AgentRunRead]:
    """列出任务全部运行，包括批准、驳回和算法失败记录。"""

    try:
        return await service.list_history(task_number)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post("/agent-runs/{run_id}/resume", response_model=AgentRunRead)
async def resume_agent_run(
    run_id: str,
    payload: AgentRunResume,
    workflow: WorkflowDependency,
) -> AgentRunRead:
    """提交人工答案并从约束检查步骤继续执行。

    路由本身不调用 ``Command``；这个 LangGraph 细节被 Facade 封装，便于 API 层继续只
    处理 HTTP 契约和异常映射。
    """

    try:
        return await workflow.resume(run_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post("/agent-runs/{run_id}/decision", response_model=AgentRunRead)
async def decide_agent_run(
    run_id: str,
    payload: AgentRunDecision,
    workflow: WorkflowDependency,
) -> AgentRunRead:
    """人工批准可行方案或驳回运行。"""

    try:
        return await workflow.decide(run_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post("/agent-runs/{run_id}/replan", response_model=AgentRunRead)
async def replan_agent_run(
    run_id: str,
    payload: AgentRunReplan,
    workflow: WorkflowDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentRunRead:
    """从已驳回/失败运行创建新运行；请求可用幂等键安全重试。"""

    try:
        return await workflow.replan(run_id, payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover
