"""装载任务 HTTP API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.load_planning_tasks import LoadPlanningTaskRepository
from app.schemas.load_planning import (
    LoadPlanningTaskCreate,
    LoadPlanningTaskRead,
    LoadPlanningTaskSummary,
)
from app.services.load_planning_tasks import (
    DuplicateTaskNumberError,
    LoadPlanningTaskService,
)

router = APIRouter(prefix="/load-planning-tasks", tags=["load-planning-tasks"])


def get_load_planning_task_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoadPlanningTaskService:
    """按请求组装 Service 和 Repository，测试可覆盖数据库依赖。"""

    return LoadPlanningTaskService(LoadPlanningTaskRepository(session))


ServiceDependency = Annotated[
    LoadPlanningTaskService,
    Depends(get_load_planning_task_service),
]


@router.post("", response_model=LoadPlanningTaskRead, status_code=status.HTTP_201_CREATED)
async def create_load_planning_task(
    payload: LoadPlanningTaskCreate,
    service: ServiceDependency,
) -> LoadPlanningTaskRead:
    """原子创建任务及多条箱规、托盘规格。"""

    try:
        task = await service.create(payload)
    except DuplicateTaskNumberError as error:
        raise HTTPException(status_code=409, detail="装载任务编号已存在") from error
    return LoadPlanningTaskRead.model_validate(task)


@router.get("", response_model=list[LoadPlanningTaskSummary])
async def list_load_planning_tasks(
    service: ServiceDependency,
) -> list[LoadPlanningTaskSummary]:
    """返回装载任务摘要列表。"""

    return await service.list()


@router.get("/{task_number}", response_model=LoadPlanningTaskRead)
async def get_load_planning_task(
    task_number: str,
    service: ServiceDependency,
) -> LoadPlanningTaskRead:
    """按任务编号读取完整算法输入，不存在时返回 404。"""

    task = await service.get(task_number)
    if task is None:
        raise HTTPException(status_code=404, detail="装载任务不存在")
    return LoadPlanningTaskRead.model_validate(task)
