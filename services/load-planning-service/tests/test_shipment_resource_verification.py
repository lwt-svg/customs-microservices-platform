"""Shipment Case 资源验证适配器的边界测试。

LINK 属于独立 Python 服务和独立数据库。Agent Runtime 只能通过 LINK 的公开 HTTP API
确认订舱申请是否存在，不能跨库建外键或直接读 LINK 表。本文件使用
``httpx.MockTransport`` 模拟网络响应，因此不会启动 LINK、MySQL 或 Docker。
"""

from collections.abc import Callable

import httpx
import pytest

from app.integrations.shipment_resources import (
    LinkBookingApplicationResourceVerifier,
    ShipmentResourceNotFoundError,
    ShipmentResourceProtocolError,
    ShipmentResourceUnavailableError,
)

BASE_URL = "http://link.test"
APPLICATION_NUMBER = "DEMO-BOOKING-VERIFY-001"


def build_link_application(
    application_number: str = APPLICATION_NUMBER,
) -> dict[str, object]:
    """返回与 LINK ``GET /booking-applications/{number}`` 一致的构造快照。"""

    return {
        "id": 1,
        "application_number": application_number,
        "booking_reference": None,
        "origin_port": "DEMO-SHA",
        "destination_port": "DEMO-LAX",
        "transport_mode": "SEA",
        "container_type": "40HQ",
        "requested_etd": "2026-10-20",
        "booking_details": None,
        "status": "DRAFT",
        "created_at": "2026-10-15T08:00:00",
        "updated_at": "2026-10-15T08:00:00",
    }


def make_verifier(
    handler: Callable[[httpx.Request], httpx.Response],
) -> LinkBookingApplicationResourceVerifier:
    """把真实 HTTP 客户端替换成进程内 MockTransport。"""

    return LinkBookingApplicationResourceVerifier(
        base_url=BASE_URL,
        timeout_seconds=0.05,
        transport=httpx.MockTransport(handler),
    )


async def test_link_verifier_returns_normalized_resource_for_matching_application() -> None:
    """200 且编号匹配时，只返回案件绑定需要的稳定元数据。"""

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=build_link_application(), request=request)

    verified = await make_verifier(handler).verify(APPLICATION_NUMBER)

    assert len(captured_requests) == 1
    assert captured_requests[0].method == "GET"
    assert captured_requests[0].url.path == (f"/api/v1/booking-applications/{APPLICATION_NUMBER}")
    assert verified.resource_type == "LINK_BOOKING_APPLICATION"
    assert verified.resource_number == APPLICATION_NUMBER
    assert verified.resource_status == "DRAFT"
    assert verified.verification_source == "LINK_BOOKING_HTTP"
    assert verified.verified_at is not None


async def test_link_verifier_maps_404_to_resource_not_found() -> None:
    """LINK 明确返回 404 时，API 可以稳定映射成案件侧 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": "订舱申请不存在"},
            request=request,
        )

    verifier = make_verifier(handler)

    with pytest.raises(ShipmentResourceNotFoundError):
        await verifier.verify("DEMO-BOOKING-NOT-FOUND")


async def test_link_verifier_maps_timeout_to_dependency_unavailable() -> None:
    """网络超时是依赖暂不可用，不得误报成“业务资源不存在”。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    verifier = make_verifier(handler)

    with pytest.raises(ShipmentResourceUnavailableError):
        await verifier.verify(APPLICATION_NUMBER)


@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_link_verifier_maps_5xx_to_dependency_unavailable(
    status_code: int,
) -> None:
    """上游 5xx 是可恢复的服务故障，异常中不能依赖或泄漏响应正文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            content=b"synthetic upstream internal detail",
            request=request,
        )

    verifier = make_verifier(handler)

    with pytest.raises(ShipmentResourceUnavailableError) as captured:
        await verifier.verify(APPLICATION_NUMBER)

    assert "synthetic upstream internal detail" not in str(captured.value)


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda request: httpx.Response(
            200,
            content=b"{not-json",
            headers={"content-type": "application/json"},
            request=request,
        ),
        lambda request: httpx.Response(
            200,
            json=["unexpected", "array"],
            request=request,
        ),
        lambda request: httpx.Response(
            200,
            json={"status": "DRAFT"},
            request=request,
        ),
    ],
    ids=["invalid-json", "json-array", "missing-application-number"],
)
async def test_link_verifier_rejects_malformed_success_payload(
    response_factory: Callable[[httpx.Request], httpx.Response],
) -> None:
    """HTTP 200 不等于可信成功；JSON 结构错误时不能创建案件绑定。"""

    verifier = make_verifier(response_factory)

    with pytest.raises(ShipmentResourceProtocolError):
        await verifier.verify(APPLICATION_NUMBER)


async def test_link_verifier_rejects_application_number_mismatch() -> None:
    """响应编号必须与请求编号相同，防止网关缓存或上游缺陷串单。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=build_link_application("DEMO-BOOKING-OTHER-001"),
            request=request,
        )

    verifier = make_verifier(handler)

    with pytest.raises(ShipmentResourceProtocolError):
        await verifier.verify(APPLICATION_NUMBER)
