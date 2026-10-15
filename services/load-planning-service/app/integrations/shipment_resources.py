"""Shipment Case 资源存在性验证适配层。

案件只保存资源编号，不复制装载任务或 LINK 订舱申请的业务明细。绑定前仍必须确认资源
真实存在，并记录当时的状态和验证来源：

* 装载任务与 Shipment Case 位于同一 Python Runtime，直接查询本地数据库；
* LINK 是独立确定性服务，通过只读 HTTP API 查询。

适配层只负责“读取并规范化”，不会在远程服务中创建或修改任何资源。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.load_planning import LoadPlanningTask
from app.models.shipment_case import ShipmentResourceType


class ShipmentResourceVerificationError(Exception):
    """资源验证失败的稳定基类；异常文本不得携带上游原始响应。"""


class ShipmentResourceNotFoundError(ShipmentResourceVerificationError):
    """资源编号在对应业务系统中不存在。"""


class ShipmentResourceUnavailableError(ShipmentResourceVerificationError):
    """验证依赖暂时不可用，调用方稍后可以安全重试。"""


class ShipmentResourceProtocolError(ShipmentResourceVerificationError):
    """上游响应不符合双方约定，不能把不完整数据当成已验证资源。"""


class UnsupportedShipmentResourceTypeError(ShipmentResourceVerificationError):
    """Dispatcher 没有为该资源类型配置验证器。"""


# 兼容较早的教程命名。它们是同一个异常类，不会形成两套错误分类。
ResourceNotFoundError = ShipmentResourceNotFoundError
ResourceDependencyUnavailableError = ShipmentResourceUnavailableError
ResourceUpstreamProtocolError = ShipmentResourceProtocolError


@dataclass(frozen=True, slots=True)
class VerifiedShipmentResource:
    """不同系统资源经过验证后的统一结果。

    ``resource_status`` 是绑定时的快照，不代替源系统当前状态。``verification_source`` 是
    可审计的适配器标识，不存 URL、Token 或上游响应正文。
    """

    resource_type: ShipmentResourceType
    resource_number: str
    resource_status: str
    verification_source: str
    verified_at: datetime


class ShipmentResourceTypeVerifier(Protocol):
    """单一资源类型验证器必须实现的最小合同。"""

    @property
    def resource_type(self) -> ShipmentResourceType:
        """返回该验证器负责的资源类型。"""

    async def verify(self, resource_number: str) -> VerifiedShipmentResource:
        """读取源系统并返回规范化验证结果。"""


class LoadPlanningTaskResourceVerifier:
    """通过本地异步 SQLAlchemy 会话验证装载任务。"""

    resource_type = ShipmentResourceType.LOAD_PLANNING_TASK
    verification_source = "LOCAL_LOAD_PLANNING_DATABASE"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def verify(self, resource_number: str) -> VerifiedShipmentResource:
        """按稳定 task_number 查询；整个操作只读且不持有跨服务事务。"""

        statement = select(LoadPlanningTask).where(
            LoadPlanningTask.task_number == resource_number
        )
        try:
            async with self._session_factory() as session:
                task = await session.scalar(statement)
        except SQLAlchemyError as error:
            # SQL 文本、连接串等诊断信息只留在异常链，领域错误本身保持稳定且脱敏。
            raise ShipmentResourceUnavailableError("装载任务数据库暂时不可用") from error

        if task is None:
            raise ShipmentResourceNotFoundError("装载任务不存在")

        status = task.status.value if hasattr(task.status, "value") else str(task.status)
        return VerifiedShipmentResource(
            resource_type=self.resource_type,
            resource_number=task.task_number,
            resource_status=status,
            verification_source=self.verification_source,
            verified_at=datetime.now(UTC),
        )


class LinkBookingApplicationResourceVerifier:
    """通过 LINK 只读详情接口验证订舱申请。

    每次验证创建一个短生命周期 ``AsyncClient``。测试传入 ``MockTransport`` 即可完整
    覆盖成功、404、超时和错误协议，不连接真实 LINK 服务。
    """

    resource_type = ShipmentResourceType.LINK_BOOKING_APPLICATION
    verification_source = "LINK_BOOKING_HTTP"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 3.0,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # ``timeout`` 是适配器的规范参数；``timeout_seconds`` 作为更直白的教学/测试别名。
        self._timeout = timeout if timeout_seconds is None else timeout_seconds
        self._transport = transport

    async def verify(self, resource_number: str) -> VerifiedShipmentResource:
        """查询 LINK；只根据状态码和约定 JSON 字段分类错误。"""

        encoded_number = quote(resource_number, safe="")
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/api/v1/booking-applications/{encoded_number}"
                )
        except (httpx.TimeoutException, httpx.ConnectError) as error:
            # 不拼接 ``str(error)``，因为底层错误可能包含目标 URL 或代理认证信息。
            raise ShipmentResourceUnavailableError("LINK 订舱服务暂时不可用") from error
        except httpx.TransportError as error:
            raise ShipmentResourceUnavailableError("LINK 订舱服务通信失败") from error

        if response.status_code == httpx.codes.NOT_FOUND:
            raise ShipmentResourceNotFoundError("LINK 订舱申请不存在")
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise ShipmentResourceUnavailableError("LINK 订舱服务暂时不可用")
        if not response.is_success:
            # 401/403/其他 4xx 都不是资源“不存在”。它们说明当前调用没有得到合同内响应。
            raise ShipmentResourceProtocolError("LINK 订舱服务返回了非预期状态")

        payload = self._read_payload(response)
        upstream_number = payload.get("application_number")
        upstream_status = payload.get("status")
        if not isinstance(upstream_number, str) or not upstream_number:
            raise ShipmentResourceProtocolError("LINK 响应缺少有效申请编号")
        if upstream_number != resource_number:
            raise ShipmentResourceProtocolError("LINK 响应申请编号与请求不一致")
        if not isinstance(upstream_status, str) or not upstream_status:
            raise ShipmentResourceProtocolError("LINK 响应缺少有效申请状态")

        return VerifiedShipmentResource(
            resource_type=self.resource_type,
            resource_number=upstream_number,
            resource_status=upstream_status,
            verification_source=self.verification_source,
            verified_at=datetime.now(UTC),
        )

    @staticmethod
    def _read_payload(response: httpx.Response) -> dict[str, object]:
        """解析 JSON 对象；错误时绝不回显响应正文。"""

        try:
            payload = response.json()
        except ValueError as error:
            raise ShipmentResourceProtocolError("LINK 响应不是有效 JSON") from error
        if not isinstance(payload, dict):
            raise ShipmentResourceProtocolError("LINK 响应必须是 JSON 对象")
        return payload


class ShipmentResourceVerifier:
    """按资源枚举选择验证器，给领域服务提供一个统一异步入口。"""

    def __init__(
        self,
        verifiers: (
            Iterable[ShipmentResourceTypeVerifier]
            | Mapping[ShipmentResourceType, ShipmentResourceTypeVerifier]
        ),
    ) -> None:
        if isinstance(verifiers, Mapping):
            self._verifiers = dict(verifiers)
        else:
            self._verifiers = {verifier.resource_type: verifier for verifier in verifiers}

    async def verify(
        self,
        resource_type: ShipmentResourceType,
        resource_number: str,
    ) -> VerifiedShipmentResource:
        """派发验证；未配置类型使用稳定领域异常，不泄露内部映射。"""

        verifier = self._verifiers.get(resource_type)
        if verifier is None:
            raise UnsupportedShipmentResourceTypeError("当前资源类型尚未配置验证器")
        return await verifier.verify(resource_number)


# 兼容教程中更强调“派发器”职责的命名；两者是同一个实现，不维护两套逻辑。
ShipmentResourceVerifierDispatcher = ShipmentResourceVerifier
