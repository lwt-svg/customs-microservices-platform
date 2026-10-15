"""只供本地模拟承运人调用的回调入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.carrier_submissions import CarrierSubmissionRepository
from app.schemas.carrier_submission import CarrierCallbackPayload, CarrierCallbackResult
from app.services.carrier_submissions import (
    CarrierCallbackConflictError,
    CarrierSubmissionNotFoundError,
    CarrierSubmissionService,
    ConcurrentCarrierCallbackError,
    InvalidCarrierCallbackTokenError,
)

router = APIRouter(prefix="/carrier-callbacks", tags=["carrier-callbacks"])


def get_carrier_callback_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CarrierSubmissionService:
    """回调处理与业务申请更新共享同一个数据库会话。"""

    return CarrierSubmissionService(CarrierSubmissionRepository(session))


@router.post("/link", response_model=CarrierCallbackResult)
async def receive_link_carrier_callback(
    payload: CarrierCallbackPayload,
    callback_token: Annotated[str, Header(alias="X-Carrier-Callback-Token")],
    service: Annotated[
        CarrierSubmissionService,
        Depends(get_carrier_callback_service),
    ],
) -> CarrierCallbackResult:
    """验证本地 Token，幂等保存回调，并由承运人事实更新申请终态。"""

    try:
        return await service.receive_callback(callback_token, payload)
    except InvalidCarrierCallbackTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="模拟承运人回调凭据无效",
        ) from error
    except CarrierSubmissionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="回调对应的承运人提交任务不存在",
        ) from error
    except CarrierCallbackConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="回调幂等键或承运人请求号与已有记录冲突",
        ) from error
    except ConcurrentCarrierCallbackError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="申请已被其他回调更新，请使用相同 callback_id 重试",
        ) from error
