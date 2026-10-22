"""统一 RPA Job 控制面的 FastAPI 边界。

页面只读取脱敏的 ``RpaJobRead``；只有 Worker 领取接口会返回业务载荷、幂等键和本次
``attempt_token``。Production 派发还会经过 Service 的显式开关和数据来源校验，因此
不能靠构造 HTTP 请求绕过安全边界。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import get_session_factory
from app.rpa.contracts import RpaDomain, RpaJobStatus
from app.schemas.rpa_job import (
    RpaJobClaim,
    RpaJobClaimRead,
    RpaJobDispatch,
    RpaJobProgress,
    RpaJobRead,
    RpaJobReconcile,
    RpaJobRequeue,
    RpaJobResultReport,
)
from app.services.rpa_jobs import (
    RpaJobConflictError,
    RpaJobNotFoundError,
    RpaJobService,
    RpaJobValidationError,
)

router = APIRouter(prefix="/rpa", tags=["rpa-jobs"])

SessionFactoryDependency = Annotated[
    async_sessionmaker[AsyncSession],
    Depends(get_session_factory),
]


def get_rpa_job_service(
    session_factory: SessionFactoryDependency,
) -> RpaJobService:
    """创建使用当前数据库会话工厂和服务端安全配置的 RPA 控制面。"""

    return RpaJobService(
        session_factory,
        worker_lease_seconds=settings.rpa_worker_lease_seconds,
        production_dispatch_enabled=settings.rpa_production_dispatch_enabled,
    )


ServiceDependency = Annotated[RpaJobService, Depends(get_rpa_job_service)]


def _raise_http(error: Exception) -> None:
    """把领域异常集中映射成稳定的 HTTP 语义。"""

    if isinstance(error, RpaJobNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, RpaJobConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, (RpaJobValidationError, ValueError)):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post(
    "/jobs",
    response_model=RpaJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def dispatch_rpa_job(
    payload: RpaJobDispatch,
    service: ServiceDependency,
) -> RpaJobRead:
    """幂等派发 RPA Job；当前学习环境默认只能创建 SANDBOX 任务。"""

    try:
        return await service.dispatch(payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get("/jobs", response_model=list[RpaJobRead])
async def list_rpa_jobs(
    service: ServiceDependency,
    domain: Annotated[RpaDomain | None, Query()] = None,
    job_status: Annotated[
        RpaJobStatus | None,
        Query(alias="status"),
    ] = None,
    case_number: Annotated[
        str | None,
        Query(min_length=1, max_length=64),
    ] = None,
) -> list[RpaJobRead]:
    """读取任务控制台列表，不返回业务载荷或 Worker 凭据。"""

    return await service.list_all(
        domain=domain,
        status=job_status,
        case_number=case_number,
    )


@router.get("/jobs/{job_id}", response_model=RpaJobRead)
async def get_rpa_job(
    job_id: str,
    service: ServiceDependency,
) -> RpaJobRead:
    try:
        return await service.get(job_id)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/worker/jobs/claim",
    response_model=RpaJobClaimRead | None,
)
async def claim_rpa_job(
    payload: RpaJobClaim,
    service: ServiceDependency,
) -> RpaJobClaimRead | None:
    """Worker 按运行域、环境和能力领取下一条任务；无任务时返回 ``null``。"""

    try:
        return await service.claim_next(payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/worker/jobs/{job_id}/progress",
    response_model=RpaJobRead,
)
async def report_rpa_job_progress(
    job_id: str,
    payload: RpaJobProgress,
    service: ServiceDependency,
) -> RpaJobRead:
    try:
        return await service.report_progress(job_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/worker/jobs/{job_id}/result",
    response_model=RpaJobRead,
)
async def report_rpa_job_result(
    job_id: str,
    payload: RpaJobResultReport,
    service: ServiceDependency,
) -> RpaJobRead:
    try:
        return await service.report_result(job_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/jobs/{job_id}/reconcile",
    response_model=RpaJobRead,
)
async def reconcile_rpa_job(
    job_id: str,
    payload: RpaJobReconcile,
    service: ServiceDependency,
) -> RpaJobRead:
    """对账只确认外部事实，不会再次点击保存或提交。"""

    try:
        return await service.reconcile(job_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/jobs/{job_id}/requeue",
    response_model=RpaJobRead,
)
async def requeue_rpa_job(
    job_id: str,
    payload: RpaJobRequeue,
    service: ServiceDependency,
) -> RpaJobRead:
    try:
        return await service.requeue(job_id, payload)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover
