"""订舱申请 HTTP API。

该模块不处理文件上传或解析；首期仅接收已验证的结构化、非敏感申请元数据。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.booking_applications import BookingApplicationRepository
from app.schemas.booking_application import (
    BookingApplicationCreate,
    BookingApplicationRead,
    BookingStatusChange,
)
from app.services.booking_applications import (
    BookingApplicationService,
    DuplicateApplicationNumberError,
    InvalidStatusTransitionError,
)

router = APIRouter(prefix="/booking-applications", tags=["booking-applications"])


def get_booking_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BookingApplicationService:
    """按请求组装服务和仓储，测试可覆盖此依赖而无需真实数据库。"""

    return BookingApplicationService(BookingApplicationRepository(session))


ServiceDependency = Annotated[BookingApplicationService, Depends(get_booking_service)]


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


@router.post("/{application_number}/status", response_model=BookingApplicationRead)
async def change_booking_application_status(
    application_number: str, payload: BookingStatusChange, service: ServiceDependency
) -> BookingApplicationRead:
    """执行最小状态机转换；非法转换返回 409 而不是静默覆盖。"""

    application = await service.get(application_number)
    if application is None:
        raise HTTPException(status_code=404, detail="订舱申请不存在")
    try:
        return await service.change_status(application, payload.target_status)
    except InvalidStatusTransitionError as error:
        raise HTTPException(status_code=409, detail="不允许的订舱状态转换") from error
