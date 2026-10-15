"""Shipment Case HTTP API。

Vue 和后续 Java BFF 只依赖这组稳定契约。API 层负责组装数据库会话工厂、资源验证器和
领域 Service，并把领域异常转换成明确的 HTTP 状态码；它不直接拼 SQL 或修改案件对象。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import get_session_factory
from app.integrations.shipment_resources import (
    LinkBookingApplicationResourceVerifier,
    LoadPlanningTaskResourceVerifier,
    ShipmentResourceNotFoundError,
    ShipmentResourceProtocolError,
    ShipmentResourceUnavailableError,
    ShipmentResourceVerifier,
    UnsupportedShipmentResourceTypeError,
)
from app.schemas.shipment_case import (
    ShipmentCaseCreate,
    ShipmentCaseRead,
    ShipmentCaseResourceBind,
    ShipmentCaseStatusChange,
)
from app.services.shipment_cases import (
    ShipmentCaseConflictError,
    ShipmentCaseNotFoundError,
    ShipmentCaseService,
    ShipmentCaseValidationError,
)

router = APIRouter(prefix="/shipment-cases", tags=["shipment-cases"])

SessionFactoryDependency = Annotated[
    async_sessionmaker[AsyncSession],
    Depends(get_session_factory),
]


def get_shipment_resource_verifier(
    session_factory: SessionFactoryDependency,
) -> ShipmentResourceVerifier:
    """组装本地装载与远程 LINK 两种只读验证器。

    测试只需覆盖这个依赖，就能模拟 LINK 成功、超时或不存在，同时保留真实案件事务。
    """

    return ShipmentResourceVerifier(
        [
            LoadPlanningTaskResourceVerifier(session_factory),
            LinkBookingApplicationResourceVerifier(
                settings.link_booking_internal_base_url,
                timeout=settings.shipment_resource_verify_timeout_seconds,
            ),
        ]
    )


ResourceVerifierDependency = Annotated[
    ShipmentResourceVerifier,
    Depends(get_shipment_resource_verifier),
]


def get_shipment_case_service(
    session_factory: SessionFactoryDependency,
    resource_verifier: ResourceVerifierDependency,
) -> ShipmentCaseService:
    """按请求组装案件 Service；Service 自己管理短会话和事务阶段。"""

    return ShipmentCaseService(session_factory, resource_verifier)


ServiceDependency = Annotated[
    ShipmentCaseService,
    Depends(get_shipment_case_service),
]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="调用方生成的写命令幂等键；重试同一命令时保持不变",
    ),
]


def _raise_http(error: Exception) -> None:
    """把案件与资源边界异常转换成稳定 HTTP 语义。"""

    if isinstance(error, ShipmentCaseNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, ShipmentCaseConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, ShipmentCaseValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, ShipmentResourceNotFoundError):
        # 案件存在，但请求引用了不存在的业务资源；请求的业务内容无法执行。
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(error, ShipmentResourceUnavailableError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, ShipmentResourceProtocolError):
        raise HTTPException(status_code=502, detail=str(error)) from error
    if isinstance(error, UnsupportedShipmentResourceTypeError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    raise error  # pragma: no cover - 新异常不能被静默吞成错误状态码


@router.post(
    "",
    response_model=ShipmentCaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_shipment_case(
    payload: ShipmentCaseCreate,
    service: ServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> ShipmentCaseRead:
    """创建案件；网络重试使用同一个 key 会返回同一条案件。"""

    try:
        return await service.create(payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get("", response_model=list[ShipmentCaseRead])
async def list_shipment_cases(
    service: ServiceDependency,
) -> list[ShipmentCaseRead]:
    """读取案件中心列表。首个切片暂时返回完整时间线，后续可另增轻量分页摘要。"""

    try:
        return await service.list()
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get("/{case_number}", response_model=ShipmentCaseRead)
async def get_shipment_case(
    case_number: str,
    service: ServiceDependency,
) -> ShipmentCaseRead:
    """读取案件当前快照、资源引用和完整时间线。"""

    try:
        return await service.get(case_number)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post("/{case_number}/resources", response_model=ShipmentCaseRead)
async def bind_shipment_case_resource(
    case_number: str,
    payload: ShipmentCaseResourceBind,
    service: ServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> ShipmentCaseRead:
    """验证并绑定装载任务或 LINK 申请；成功重放统一返回 200。"""

    try:
        return await service.bind_resource(case_number, payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post("/{case_number}/status", response_model=ShipmentCaseRead)
async def change_shipment_case_status(
    case_number: str,
    payload: ShipmentCaseStatusChange,
    service: ServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> ShipmentCaseRead:
    """按 revision 和最小状态机推进案件；成功重放统一返回 200。"""

    try:
        return await service.change_status(case_number, payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover
