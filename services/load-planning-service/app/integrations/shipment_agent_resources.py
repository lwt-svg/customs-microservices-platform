"""Shipment Agent 访问 LINK 的小型防腐层。

总 Agent 只读取 LINK 详情，并在明确人工审批后调用“提交”或“人工重试”。客户端不会把
LINK 的原始响应、URL 或异常正文写入 Agent Trace；上游协议先被规范化为下面的快照。

所有 HTTP 调用都由 Service 在数据库事务之外执行。尤其提交超时时，结果可能已经在 LINK
落库，调用方不能盲目重复 POST；``LinkCommandResultUnknownError`` 会让总 Agent 暂停并
要求重新读取事实。
"""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx


class LinkBookingClientError(Exception):
    """LINK 客户端稳定异常基类。"""


class LinkBookingNotFoundError(LinkBookingClientError):
    """申请编号不存在。"""


class LinkBookingUnavailableError(LinkBookingClientError):
    """只读查询未到达或未得到可靠响应，可以稍后重新检查。"""


class LinkBookingProtocolError(LinkBookingClientError):
    """LINK 返回内容不符合约定，不能把残缺数据当成业务事实。"""


class LinkCommandResultUnknownError(LinkBookingClientError):
    """写请求发生超时/断连，远端是否已接受命令未知。"""


class LinkCommandConflictError(LinkBookingClientError):
    """远端当前状态不允许执行写命令。"""


@dataclass(frozen=True, slots=True)
class LinkBookingSnapshot:
    """总 Agent 决策需要的最小 LINK 快照。"""

    application_number: str
    application_status: str
    booking_reference: str | None
    carrier_status: str | None
    carrier_can_retry: bool
    carrier_request_id: str | None


class LinkBookingGateway(Protocol):
    """测试 fake 与真实 HTTP 客户端共同遵循的合同。"""

    async def get_detail(self, application_number: str) -> LinkBookingSnapshot:
        """读取申请与承运人提交当前状态。"""

    async def submit(self, application_number: str) -> None:
        """把 DRAFT 申请提交给 LINK；结果不确定时必须抛专用异常。"""

    async def retry(self, application_number: str) -> None:
        """人工重试 DEAD/RETRY_WAIT 的承运人投递任务。"""


class HttpLinkBookingClient:
    """基于 LINK 现有 REST 契约的异步客户端。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def get_detail(self, application_number: str) -> LinkBookingSnapshot:
        """读取详情；GET 失败只分类，不向上层泄露 URL 或响应正文。"""

        encoded = quote(application_number, safe="")
        try:
            async with self._client() as client:
                response = await client.get(
                    f"/api/v1/booking-applications/{encoded}/detail"
                )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise LinkBookingUnavailableError("LINK 订舱服务暂时不可用") from error

        if response.status_code == httpx.codes.NOT_FOUND:
            raise LinkBookingNotFoundError("LINK 订舱申请不存在")
        if response.status_code >= 500:
            raise LinkBookingUnavailableError("LINK 订舱服务暂时不可用")
        if not response.is_success:
            raise LinkBookingProtocolError("LINK 详情接口返回非预期状态")
        return self._parse_detail(response, application_number)

    async def submit(self, application_number: str) -> None:
        """提交草稿。

        POST 超时与 GET 超时的语义不同：提交可能已经成功，只是响应丢失，所以这里返回
        ``LinkCommandResultUnknownError``，上层只能重新 GET，不能自动再 POST。
        """

        encoded = quote(application_number, safe="")
        try:
            async with self._client() as client:
                response = await client.post(
                    f"/api/v1/booking-applications/{encoded}/status",
                    json={"target_status": "SUBMITTED"},
                )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise LinkCommandResultUnknownError("LINK 提交结果未知，需要重新检查") from error

        if response.status_code == httpx.codes.NOT_FOUND:
            raise LinkBookingNotFoundError("LINK 订舱申请不存在")
        if response.status_code == httpx.codes.CONFLICT:
            raise LinkCommandConflictError("LINK 申请当前状态不允许提交")
        if response.status_code >= 500:
            raise LinkCommandResultUnknownError("LINK 提交结果未知，需要重新检查")
        if not response.is_success:
            raise LinkBookingProtocolError("LINK 提交接口返回非预期状态")

    async def retry(self, application_number: str) -> None:
        """触发一次明确的人工投递重试。"""

        encoded = quote(application_number, safe="")
        try:
            async with self._client() as client:
                response = await client.post(
                    f"/api/v1/booking-applications/{encoded}/carrier-submission/retry"
                )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise LinkCommandResultUnknownError(
                "LINK 承运人重试结果未知，需要重新检查"
            ) from error

        if response.status_code == httpx.codes.NOT_FOUND:
            raise LinkBookingNotFoundError("LINK 承运人提交任务不存在")
        if response.status_code == httpx.codes.CONFLICT:
            raise LinkCommandConflictError("LINK 承运人提交任务当前不能重试")
        if response.status_code >= 500:
            raise LinkCommandResultUnknownError(
                "LINK 承运人重试结果未知，需要重新检查"
            )
        if not response.is_success:
            raise LinkBookingProtocolError("LINK 重试接口返回非预期状态")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            transport=self._transport,
        )

    @staticmethod
    def _parse_detail(
        response: httpx.Response,
        requested_number: str,
    ) -> LinkBookingSnapshot:
        """只接受双方约定的对象结构，不回显原始响应。"""

        try:
            payload = response.json()
        except ValueError as error:
            raise LinkBookingProtocolError("LINK 详情不是有效 JSON") from error
        if not isinstance(payload, dict):
            raise LinkBookingProtocolError("LINK 详情必须是 JSON 对象")

        application = payload.get("application")
        if not isinstance(application, dict):
            raise LinkBookingProtocolError("LINK 详情缺少 application")
        application_number = application.get("application_number")
        application_status = application.get("status")
        booking_reference = application.get("booking_reference")
        if application_number != requested_number:
            raise LinkBookingProtocolError("LINK 详情申请编号与请求不一致")
        if not isinstance(application_status, str) or not application_status:
            raise LinkBookingProtocolError("LINK 详情缺少申请状态")
        if booking_reference is not None and not isinstance(booking_reference, str):
            raise LinkBookingProtocolError("LINK booking_reference 类型无效")

        carrier = payload.get("carrier_submission")
        carrier_status: str | None = None
        carrier_can_retry = False
        carrier_request_id: str | None = None
        if carrier is not None:
            if not isinstance(carrier, dict):
                raise LinkBookingProtocolError("LINK carrier_submission 类型无效")
            raw_status = carrier.get("status")
            raw_can_retry = carrier.get("can_retry")
            raw_request_id = carrier.get("carrier_request_id")
            if not isinstance(raw_status, str) or not raw_status:
                raise LinkBookingProtocolError("LINK 承运人提交缺少状态")
            if not isinstance(raw_can_retry, bool):
                raise LinkBookingProtocolError("LINK 承运人提交缺少 can_retry")
            if raw_request_id is not None and not isinstance(raw_request_id, str):
                raise LinkBookingProtocolError("LINK carrier_request_id 类型无效")
            carrier_status = raw_status
            carrier_can_retry = raw_can_retry
            carrier_request_id = raw_request_id

        return LinkBookingSnapshot(
            application_number=application_number,
            application_status=application_status,
            booking_reference=booking_reference,
            carrier_status=carrier_status,
            carrier_can_retry=carrier_can_retry,
            carrier_request_id=carrier_request_id,
        )
