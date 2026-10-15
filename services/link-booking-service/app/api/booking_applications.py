"""订舱申请 HTTP API。

21.4 新增“当前申请快照 + 不可变操作流水”的详情接口；文件上传与解析仍会在 21.6
单独实现，避免让一个路由同时承担上传、解析、创建和审计四种职责。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.repositories.booking_applications import BookingApplicationRepository
from app.repositories.carrier_submissions import CarrierSubmissionRepository
from app.schemas.booking_application import (
    BookingApplicationCreate,
    BookingApplicationDetailRead,
    BookingApplicationRead,
    BookingStatusChange,
)
from app.schemas.carrier_submission import CarrierSubmissionRead
from app.services.booking_applications import (
    BookingApplicationService,
    ConcurrentStatusChangeError,
    DuplicateApplicationNumberError,
    InvalidStatusTransitionError,
)
from app.services.carrier_submissions import (
    CarrierSubmissionNotFoundError,
    CarrierSubmissionNotRetryableError,
    CarrierSubmissionService,
    to_submission_read,
)

router = APIRouter(prefix="/booking-applications", tags=["booking-applications"])


def get_booking_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BookingApplicationService:
    """按请求组装服务和仓储，测试可覆盖此依赖而无需真实数据库。"""

    carrier_service = CarrierSubmissionService(CarrierSubmissionRepository(session))
    return BookingApplicationService(
        BookingApplicationRepository(session),
        carrier_service,
        dispatch_on_submit=settings.carrier_dispatch_enabled,
    )


def get_carrier_submission_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CarrierSubmissionService:
    """手工重试接口只需要承运人提交服务。"""

    return CarrierSubmissionService(CarrierSubmissionRepository(session))


ServiceDependency = Annotated[BookingApplicationService, Depends(get_booking_service)]
CarrierServiceDependency = Annotated[
    CarrierSubmissionService,
    Depends(get_carrier_submission_service),
]


@router.post("", response_model=BookingApplicationRead, status_code=status.HTTP_201_CREATED)
async def create_booking_application(
    payload: BookingApplicationCreate, service: ServiceDependency
) -> BookingApplicationRead:
    """创建一份初始状态为 DRAFT 的构造订舱申请。"""

    try:
        return await service.create(payload)
    except DuplicateApplicationNumberError as error:
        raise HTTPException(status_code=409, detail="申请编号已存在") from error


@router.get("", response_model=list[BookingApplicationRead])
async def list_booking_applications(service: ServiceDependency) -> list[BookingApplicationRead]:
    """查询 LINK 服务自己的申请列表。"""

    return await service.list()


@router.get("/{application_number}", response_model=BookingApplicationRead)
async def get_booking_application(
    application_number: str, service: ServiceDependency
) -> BookingApplicationRead:
    """按申请编号查询，不存在时返回 404。"""

    application = await service.get(application_number)
    if application is None:
        raise HTTPException(status_code=404, detail="订舱申请不存在")
    return application


@router.get("/{application_number}/detail", response_model=BookingApplicationDetailRead)
async def get_booking_application_detail(
    application_number: str, service: ServiceDependency
) -> BookingApplicationDetailRead:
    """读取申请当前快照和倒序审计流水。"""

    application = await service.get(application_number)
    if application is None:
        raise HTTPException(status_code=404, detail="订舱申请不存在")
    current_application, events, carrier_request = await service.detail(application)
    return BookingApplicationDetailRead(
        application=current_application,
        events=events,
        carrier_submission=(
            to_submission_read(carrier_request) if carrier_request is not None else None
        ),
    )


@router.post("/{application_number}/status", response_model=BookingApplicationRead)
async def change_booking_application_status(
    application_number: str, payload: BookingStatusChange, service: ServiceDependency
) -> BookingApplicationRead:
    """提交或取消申请；确认/拒绝只能由模拟承运人回调完成。"""

    application = await service.get(application_number)
    if application is None:
        raise HTTPException(status_code=404, detail="订舱申请不存在")
    try:
        return await service.change_status(application, payload.target_status)
    except InvalidStatusTransitionError as error:
        raise HTTPException(status_code=409, detail="不允许的订舱状态转换") from error
    except ConcurrentStatusChangeError as error:
        raise HTTPException(status_code=409, detail="订舱申请状态已被其他操作更新，请刷新后重试") from error


@router.post(
    "/{application_number}/carrier-submission/retry",
    response_model=CarrierSubmissionRead,
)
async def retry_carrier_submission(
    application_number: str,
    service: CarrierServiceDependency,
) -> CarrierSubmissionRead:
    """人工重试 RETRY_WAIT/DEAD 的 LINK→模拟承运人提交任务。"""

    try:
        request = await service.retry_manually(application_number)
    except CarrierSubmissionNotFoundError as error:
        raise HTTPException(status_code=404, detail="订舱申请尚无承运人提交任务") from error
    except CarrierSubmissionNotRetryableError as error:
        raise HTTPException(status_code=409, detail="当前承运人提交状态不允许重试") from error
    return to_submission_read(request)
