"""正式装载方案版本 HTTP API。

正式版本是批准方案的不可变业务快照，不是 LangGraph checkpoint。创建正式版本属于会
推进流程的写操作，所以经过 ``WorkflowDependency``；列表、当前版本和按 ID 查询只是
读取业务事实，仍直接使用 ``LoadPlanVersionService``。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.plan_versions import LoadPlanVersionRepository
from app.schemas.plan_version import LoadPlanLockRequest, LoadPlanVersionRead
from app.services.agent_runs import AgentRunNotFoundError
from app.services.plan_versions import (
    LoadPlanVersionConflictError,
    LoadPlanVersionNotFoundError,
    LoadPlanVersionService,
    LoadPlanVersionSourceRunNotFoundError,
    LoadPlanVersionTaskNotFoundError,
    LoadPlanVersionValidationError,
)
from app.workflows.load_planning import LoadPlanningWorkflowStateError

from .workflow_dependencies import WorkflowDependency

router = APIRouter(prefix="/load-planning", tags=["load-plan-versions"])


def get_plan_version_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoadPlanVersionService:
    """同一请求内复用一个数据库事务。"""

    return LoadPlanVersionService(LoadPlanVersionRepository(session))


ServiceDependency = Annotated[
    LoadPlanVersionService,
    Depends(get_plan_version_service),
]


def _raise_http(error: Exception) -> None:
    """把正式版本领域异常映射成稳定的 HTTP 状态码。"""

    if isinstance(
        error,
        (
            LoadPlanVersionNotFoundError,
            LoadPlanVersionTaskNotFoundError,
            LoadPlanVersionSourceRunNotFoundError,
            AgentRunNotFoundError,
        ),
    ):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, LoadPlanVersionConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, LoadPlanningWorkflowStateError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, LoadPlanVersionValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error  # pragma: no cover


@router.post(
    "/agent-runs/{run_id}/plan-versions",
    response_model=LoadPlanVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def lock_load_plan_version(
    run_id: str,
    payload: LoadPlanLockRequest,
    workflow: WorkflowDependency,
) -> LoadPlanVersionRead:
    """从已批准运行创建正式版本；同一来源运行重试返回原版本。

    这里的“锁定”由领域 Service 负责生成快照、哈希、版本号和审计事件。LangGraph 只把
    ``FORMAL_RELEASE`` interrupt 恢复到该 Service，不能自行拼装正式版本。
    """

    try:
        return await workflow.lock(run_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/tasks/{task_number}/plan-versions",
    response_model=list[LoadPlanVersionRead],
)
async def list_load_plan_versions(
    task_number: str,
    service: ServiceDependency,
) -> list[LoadPlanVersionRead]:
    """按最新版本在前读取任务的正式版本历史。"""

    try:
        return await service.list(task_number)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/tasks/{task_number}/plan-versions/current",
    response_model=LoadPlanVersionRead,
)
async def get_current_load_plan_version(
    task_number: str,
    service: ServiceDependency,
) -> LoadPlanVersionRead:
    """读取任务当前正式版本；尚未锁定时返回 404。"""

    try:
        return await service.current(task_number)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/plan-versions/{version_id}",
    response_model=LoadPlanVersionRead,
)
async def get_load_plan_version(
    version_id: str,
    service: ServiceDependency,
) -> LoadPlanVersionRead:
    """按版本 UUID 读取完整快照、哈希和事件。"""

    try:
        return await service.get(version_id)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover
