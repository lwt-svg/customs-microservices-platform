"""平台侧提交 API 与独立模拟承运人门户 API。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.carrier_booking import CarrierBookingStatus
from app.repositories.carrier_bookings import CarrierBookingRepository
from app.schemas.carrier_booking import (
    CallbackDeliveryRead,
    CarrierBookingCreate,
    CarrierBookingDecision,
    CarrierBookingDetailRead,
    CarrierBookingRead,
)
from app.services.carrier_bookings import (
    BookingNotFoundError,
    CallbackDeliveryNotFoundError,
    CallbackDeliveryNotRetryableError,
    CallbackDeliveryService,
    CarrierBookingService,
    DuplicateIdempotencyKeyError,
    InvalidCarrierBookingTransitionError,
)

carrier_router = APIRouter(prefix="/carrier/bookings", tags=["carrier-platform"])
portal_router = APIRouter(prefix="/portal", tags=["carrier-portal"])


async def _session_dependency() -> AsyncIterator[AsyncSession]:
    """保留可覆写的名字，使 HTTP 测试无需连接 MySQL。"""

    async for session in get_session():
        yield session


def get_booking_service(
    session: Annotated[AsyncSession, Depends(_session_dependency)],
) -> CarrierBookingService:
    """为请求构造使用同一异步事务会话的领域服务。"""

    return CarrierBookingService(CarrierBookingRepository(session))


def get_callback_delivery_service(
    session: Annotated[AsyncSession, Depends(_session_dependency)],
) -> CallbackDeliveryService:
    """回调投递服务与订舱服务共享本次 HTTP 请求的会话。"""

    return CallbackDeliveryService(CarrierBookingRepository(session))


def _not_found(error: Exception) -> HTTPException:
    """将领域缺失错误统一映射为不暴露数据库实现的 404。"""

    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error) or "资源不存在")


@carrier_router.post("", response_model=CarrierBookingRead, status_code=status.HTTP_201_CREATED)
async def submit_carrier_booking(
    payload: CarrierBookingCreate,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
    service: Annotated[CarrierBookingService, Depends(get_booking_service)],
) -> CarrierBookingRead:
    """供 LINK 异步提交构造申请；同键同体重放返回原快照和 200。"""

    try:
        result = await service.create(idempotency_key, payload)
    except DuplicateIdempotencyKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key 已用于不同的构造申请",
        ) from error
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return CarrierBookingRead.model_validate(result.booking)


@carrier_router.get("/by-idempotency-key/{idempotency_key}", response_model=CarrierBookingRead)
async def get_carrier_booking_by_idempotency_key(
    idempotency_key: str,
    service: Annotated[CarrierBookingService, Depends(get_booking_service)],
) -> CarrierBookingRead:
    """网络超时时，平台可按原幂等键安全找回请求结果。"""

    try:
        return CarrierBookingRead.model_validate(
            await service.get_by_idempotency_key(idempotency_key)
        )
    except BookingNotFoundError as error:
        raise _not_found(error) from error


@carrier_router.get("/{booking_id}", response_model=CarrierBookingRead)
async def get_carrier_booking(
    booking_id: int,
    service: Annotated[CarrierBookingService, Depends(get_booking_service)],
) -> CarrierBookingRead:
    """供 LINK 查询异步提交后的模拟承运人快照。"""

    try:
        return CarrierBookingRead.model_validate(await service.get(booking_id))
    except BookingNotFoundError as error:
        raise _not_found(error) from error


@portal_router.get("/bookings", response_model=list[CarrierBookingRead])
async def list_portal_bookings(
    service: Annotated[CarrierBookingService, Depends(get_booking_service)],
    booking_status: Annotated[CarrierBookingStatus | None, Query(alias="status")] = None,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
) -> list[CarrierBookingRead]:
    """门户列表，可按状态和构造申请号/承运人请求号过滤。"""

    return [
        CarrierBookingRead.model_validate(item)
        for item in await service.list(booking_status, keyword)
    ]


@portal_router.get("/bookings/{booking_id}", response_model=CarrierBookingDetailRead)
async def get_portal_booking_detail(
    booking_id: int,
    service: Annotated[CarrierBookingService, Depends(get_booking_service)],
) -> CarrierBookingDetailRead:
    """门户详情显示快照、人工处理时间线及回调投递状态。"""

    try:
        booking, events, deliveries = await service.detail(booking_id)
    except BookingNotFoundError as error:
        raise _not_found(error) from error
    return CarrierBookingDetailRead(
        booking=CarrierBookingRead.model_validate(booking),
        events=[item for item in events],
        callback_deliveries=[item for item in deliveries],
    )


@portal_router.post("/bookings/{booking_id}/process", response_model=CarrierBookingRead)
async def start_portal_processing(
    booking_id: int,
    booking_service: Annotated[CarrierBookingService, Depends(get_booking_service)],
    delivery_service: Annotated[
        CallbackDeliveryService,
        Depends(get_callback_delivery_service),
    ],
) -> CarrierBookingRead:
    """模拟门户开始处理，并提交后立即尝试一次 PROCESSING 回调。"""

    try:
        booking, delivery = await booking_service.start_processing(booking_id)
        await delivery_service.deliver_new(delivery.id)
        return CarrierBookingRead.model_validate(booking)
    except BookingNotFoundError as error:
        raise _not_found(error) from error
    except InvalidCarrierBookingTransitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前承运人状态不允许开始处理",
        ) from error


@portal_router.post("/bookings/{booking_id}/decision", response_model=CarrierBookingRead)
async def make_portal_decision(
    booking_id: int,
    payload: CarrierBookingDecision,
    booking_service: Annotated[CarrierBookingService, Depends(get_booking_service)],
    delivery_service: Annotated[
        CallbackDeliveryService,
        Depends(get_callback_delivery_service),
    ],
) -> CarrierBookingRead:
    """确认或拒绝，并在事务提交后立即尝试一次构造回调。"""

    try:
        result = await booking_service.decide(booking_id, payload)
        # 业务终态已经提交；即使这次网络投递失败，delivery 会转为 RETRY_WAIT/DEAD，
        # 响应仍返回真实的承运人决定，前端可在详情的投递列表看到失败。
        if result.decided_now:
            await delivery_service.deliver_new(result.delivery.id)
        return CarrierBookingRead.model_validate(result.booking)
    except BookingNotFoundError as error:
        raise _not_found(error) from error
    except InvalidCarrierBookingTransitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前承运人状态不允许确认或拒绝",
        ) from error


@portal_router.get("/callback-deliveries", response_model=list[CallbackDeliveryRead])
async def list_callback_deliveries(
    service: Annotated[CallbackDeliveryService, Depends(get_callback_delivery_service)],
) -> list[CallbackDeliveryRead]:
    """门户查看回调成功、等待重试和 DEAD 记录。"""

    return [
        CallbackDeliveryRead.model_validate(item)
        for item in await service.repository.list_deliveries()
    ]


@portal_router.post(
    "/callback-deliveries/{delivery_id}/retry",
    response_model=CallbackDeliveryRead,
)
async def retry_callback_delivery(
    delivery_id: int,
    service: Annotated[CallbackDeliveryService, Depends(get_callback_delivery_service)],
) -> CallbackDeliveryRead:
    """由门户对失败任务进行一次明确的人工重试。"""

    try:
        return CallbackDeliveryRead.model_validate(await service.retry_manually(delivery_id))
    except CallbackDeliveryNotFoundError as error:
        raise _not_found(error) from error
    except CallbackDeliveryNotRetryableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有失败或 DEAD 的回调可以手动重试",
        ) from error
