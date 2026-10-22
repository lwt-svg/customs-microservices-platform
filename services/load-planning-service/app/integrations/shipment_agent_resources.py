"""Shipment Agent 访问 LINK 的小型防腐层。

总 Agent 只读取 LINK 详情，并在明确人工审批后调用“提交”或“人工重试”。客户端不会把
LINK 的原始响应、URL 或异常正文写入 Agent Trace；上游协议先被规范化为下面的快照。

所有 HTTP 调用都由 Service 在数据库事务之外执行。尤其提交超时时，结果可能已经在 LINK
落库，调用方不能盲目重复 POST；``LinkCommandResultUnknownError`` 会让总 Agent 暂停并
要求重新读取事实。
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

LINK_RPA_DRAFT_PAYLOAD_VERSION = "LINK_RPA_DRAFT_V1"


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


class _StrictLinkRpaPayloadModel(BaseModel):
    """LINK RPA 读取合同的共同安全配置。

    ``extra='forbid'`` 会拒绝双方尚未约定的嵌套字段；``strict=True`` 会阻止
    ``"12"`` 被静默转换成数字 12。RPA 会在外部网页产生副作用，因此宁可因协议变化
    暂停，也不能把形状不确定的数据交给浏览器脚本猜测。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class LinkRpaBookingItem(_StrictLinkRpaPayloadModel):
    """Packing List 中一条经 LINK 人工确认的商品明细。"""

    item_code: str = Field(min_length=1, max_length=64)
    lot_number: str | None = Field(default=None, max_length=64)
    po_number: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=0)
    cartons: int = Field(ge=0)
    net_weight_kg: float = Field(ge=0)
    gross_weight_kg: float = Field(ge=0)


class LinkRpaPoAllocation(_StrictLinkRpaPayloadModel):
    """一个包组分配给某张 PO 的数量。"""

    po_number: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=0)


class LinkRpaPackage(_StrictLinkRpaPayloadModel):
    """Packing List 中由小计行收口的一个包组。"""

    description: str = Field(min_length=1, max_length=500)
    pallets: int = Field(ge=0)
    cartons: int = Field(ge=0)
    quantity: int = Field(ge=0)
    net_weight_kg: float = Field(ge=0)
    gross_weight_kg: float = Field(ge=0)
    cbm: float | None = Field(default=None, ge=0)
    po_allocations: tuple[LinkRpaPoAllocation, ...] = ()


class LinkRpaBookingDetails(_StrictLinkRpaPayloadModel):
    """RPA 填写 LINK 七步草稿时可以使用的结构化 Packing List 数据。"""

    invoice_number: str | None = Field(default=None, max_length=64)
    document_date: date | None = None
    ciq_number: str | None = Field(default=None, max_length=64)
    shipper: str | None = Field(default=None, max_length=1000)
    consignee: str | None = Field(default=None, max_length=1000)
    notify_party: str | None = Field(default=None, max_length=1000)
    marks: str | None = Field(default=None, max_length=1000)
    total_pallets: int | None = Field(default=None, ge=0)
    total_cartons: int | None = Field(default=None, ge=0)
    total_quantity: int | None = Field(default=None, ge=0)
    total_cbm: float | None = Field(default=None, ge=0)
    total_net_weight_kg: float | None = Field(default=None, ge=0)
    total_gross_weight_kg: float | None = Field(default=None, ge=0)
    items: tuple[LinkRpaBookingItem, ...] = ()
    packages: tuple[LinkRpaPackage, ...] = ()
    checks: tuple[str, ...] = ()


class LinkRpaDraftPayload(_StrictLinkRpaPayloadModel):
    """LINK 详情转换出的、可安全交给 RPA Worker 的 v1 草稿合同。

    当前 LINK 服务只接受 ``DEMO-`` 编号，因此这里必须显式标记为
    ``SYNTHETIC_DEMO``。这个固定标签会继续经过 RPA Job 的环境校验，防止演示数据被
    误派发到 Production。
    """

    payload_schema_version: Literal["LINK_RPA_DRAFT_V1"] = LINK_RPA_DRAFT_PAYLOAD_VERSION
    data_source_label: Literal["SYNTHETIC_DEMO"] = "SYNTHETIC_DEMO"
    application_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^DEMO-[A-Za-z0-9_-]+$",
    )
    origin_port: str = Field(min_length=1, max_length=64)
    destination_port: str = Field(min_length=1, max_length=64)
    transport_mode: str = Field(min_length=1, max_length=32)
    container_type: str = Field(min_length=1, max_length=32)
    requested_etd: date | None
    booking_details: LinkRpaBookingDetails | None


@dataclass(frozen=True, slots=True)
class LinkBookingSnapshot:
    """总 Agent 决策需要的最小 LINK 快照。"""

    application_number: str
    application_status: str
    booking_reference: str | None
    carrier_status: str | None
    carrier_can_retry: bool
    carrier_request_id: str | None
    # 默认值保证现有测试 Fake 和旧调用方无需立刻知道 RPA 新合同。
    rpa_payload: LinkRpaDraftPayload | None = None


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
                response = await client.get(f"/api/v1/booking-applications/{encoded}/detail")
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
            raise LinkCommandResultUnknownError("LINK 承运人重试结果未知，需要重新检查") from error

        if response.status_code == httpx.codes.NOT_FOUND:
            raise LinkBookingNotFoundError("LINK 承运人提交任务不存在")
        if response.status_code == httpx.codes.CONFLICT:
            raise LinkCommandConflictError("LINK 承运人提交任务当前不能重试")
        if response.status_code >= 500:
            raise LinkCommandResultUnknownError("LINK 承运人重试结果未知，需要重新检查")
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

        # 这些字段即使业务值允许为 null（requested_etd、booking_details），JSON 键也
        # 必须存在。否则无法区分“LINK 明确返回暂无值”和“双方接口协议已经漂移”。
        rpa_field_names = (
            "origin_port",
            "destination_port",
            "transport_mode",
            "container_type",
            "requested_etd",
            "booking_details",
        )
        missing_rpa_fields = [
            field_name for field_name in rpa_field_names if field_name not in application
        ]
        if missing_rpa_fields:
            raise LinkBookingProtocolError(
                "LINK 详情缺少 RPA 字段：" + "、".join(missing_rpa_fields)
            )

        # model_validate_json 保留严格数字校验，同时允许 JSON 使用 ISO 字符串表达 date。
        # 只挑选白名单字段，绝不把 LINK 的数据库 id、时间戳或未来新增字段直接下发给 RPA。
        try:
            rpa_payload = LinkRpaDraftPayload.model_validate_json(
                json.dumps(
                    {
                        "application_number": application_number,
                        **{field_name: application[field_name] for field_name in rpa_field_names},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        except (TypeError, ValidationError) as error:
            raise LinkBookingProtocolError("LINK RPA 草稿字段不符合 v1 协议") from error

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
            rpa_payload=rpa_payload,
        )
