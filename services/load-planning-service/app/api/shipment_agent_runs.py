"""Shipment Coordinator Agent 的 FastAPI 边界。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import get_session_factory
from app.integrations.shipment_agent_resources import (
    HttpLinkBookingClient,
    LinkBookingGateway,
)
from app.schemas.shipment_agent_run import (
    ShipmentAgentReconcile,
    ShipmentAgentResume,
    ShipmentAgentRunRead,
    ShipmentAgentStart,
)
from app.services.shipment_agent_runs import (
    ShipmentAgentCaseNotFoundError,
    ShipmentAgentConflictError,
    ShipmentAgentRunNotFoundError,
    ShipmentAgentService,
    ShipmentAgentValidationError,
)
from app.workflows.shipment_case import (
    ShipmentWorkflowOrchestrator,
    ShipmentWorkflowStateError,
)

router = APIRouter(tags=["shipment-agent-runs"])

SessionFactoryDependency = Annotated[
    async_sessionmaker[AsyncSession],
    Depends(get_session_factory),
]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="调用方生成的命令幂等键；同一命令网络重试时保持不变",
    ),
]


def get_link_booking_gateway() -> LinkBookingGateway:
    """组装 LINK 客户端；测试覆盖此依赖即可模拟回调、DEAD 和超时。"""

    return HttpLinkBookingClient(
        settings.link_booking_internal_base_url,
        timeout_seconds=settings.shipment_resource_verify_timeout_seconds,
    )


LinkGatewayDependency = Annotated[
    LinkBookingGateway,
    Depends(get_link_booking_gateway),
]


def get_shipment_agent_service(
    request: Request,
    session_factory: SessionFactoryDependency,
    link_gateway: LinkGatewayDependency,
) -> ShipmentAgentService:
    """复用应用级 LangGraph saver，同时让每次业务步骤创建短数据库会话。"""

    checkpointer = getattr(request.app.state, "load_planning_checkpointer", None)
    if checkpointer is None:
        # 只用于未运行 lifespan 的轻量 ASGI 测试；Uvicorn 正常启动会换成 SQLite saver。
        checkpointer = InMemorySaver()
    return ShipmentAgentService(
        session_factory,
        link_gateway,
        checkpointer,
        rpa_enabled=settings.shipment_agent_rpa_enabled,
        rpa_environment=settings.shipment_agent_rpa_environment,
        rpa_adapter_key=settings.shipment_agent_rpa_adapter_key,
        rpa_selector_pack_version=(settings.shipment_agent_rpa_selector_pack_version),
    )


ServiceDependency = Annotated[
    ShipmentAgentService,
    Depends(get_shipment_agent_service),
]


def get_shipment_workflow(
    service: ServiceDependency,
) -> ShipmentWorkflowOrchestrator:
    return ShipmentWorkflowOrchestrator(
        service=service,
        checkpointer=service.checkpointer,
    )


WorkflowDependency = Annotated[
    ShipmentWorkflowOrchestrator,
    Depends(get_shipment_workflow),
]


def _raise_http(error: Exception) -> None:
    if isinstance(
        error,
        (ShipmentAgentRunNotFoundError, ShipmentAgentCaseNotFoundError),
    ):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(
        error,
        (ShipmentAgentConflictError, ShipmentWorkflowStateError),
    ):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, ShipmentAgentValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post(
    "/shipment-cases/{case_number}/agent-runs",
    response_model=ShipmentAgentRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_shipment_agent(
    case_number: str,
    payload: ShipmentAgentStart,
    workflow: WorkflowDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> ShipmentAgentRunRead:
    """启动并推进到首个人工、子 Agent 或外部等待点。"""

    try:
        return await workflow.start(case_number, payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/shipment-cases/{case_number}/agent-runs/active",
    response_model=ShipmentAgentRunRead | None,
)
async def get_active_shipment_agent(
    case_number: str,
    service: ServiceDependency,
) -> ShipmentAgentRunRead | None:
    """无运行时返回 200 + null，方便案件工作台无分支处理。"""

    try:
        return await service.active(case_number)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/shipment-cases/{case_number}/agent-runs/history",
    response_model=list[ShipmentAgentRunRead],
)
async def list_shipment_agent_history(
    case_number: str,
    service: ServiceDependency,
) -> list[ShipmentAgentRunRead]:
    try:
        return await service.history(case_number)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/shipment-agent-runs",
    response_model=list[ShipmentAgentRunRead],
)
async def list_all_shipment_agent_runs(
    service: ServiceDependency,
) -> list[ShipmentAgentRunRead]:
    """Agent Center 全局真实运行列表，按 updated_at 倒序。"""

    try:
        return await service.list_all()
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.get(
    "/shipment-agent-runs/{run_id}",
    response_model=ShipmentAgentRunRead,
)
async def get_shipment_agent_run(
    run_id: str,
    service: ServiceDependency,
) -> ShipmentAgentRunRead:
    try:
        return await service.get(run_id)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/shipment-agent-runs/{run_id}/resume",
    response_model=ShipmentAgentRunRead,
)
async def resume_shipment_agent(
    run_id: str,
    payload: ShipmentAgentResume,
    workflow: WorkflowDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> ShipmentAgentRunRead:
    try:
        return await workflow.resume(run_id, payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover


@router.post(
    "/shipment-agent-runs/{run_id}/reconcile",
    response_model=ShipmentAgentRunRead,
)
async def reconcile_shipment_agent(
    run_id: str,
    payload: ShipmentAgentReconcile,
    workflow: WorkflowDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> ShipmentAgentRunRead:
    try:
        return await workflow.reconcile(run_id, payload, idempotency_key)
    except Exception as error:
        _raise_http(error)
        raise  # pragma: no cover
