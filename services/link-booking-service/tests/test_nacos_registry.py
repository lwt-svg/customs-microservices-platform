"""Nacos HTTP 适配层单元测试：不连接真实 Nacos 服务端。"""

from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import Settings
from app.integrations.nacos_registry import NacosServiceRegistry


@pytest.mark.anyio
async def test_should_register_verify_and_deregister_a_temporary_instance() -> None:
    """适配层在注册、确认和注销时必须使用同一份服务身份信息。"""

    observed_requests: list[httpx.Request] = []

    def nacos_mock(request: httpx.Request) -> httpx.Response:
        observed_requests.append(request)
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"accessToken": "test-token"})
        if request.url.path.endswith("/v1/ns/instance/list"):
            return httpx.Response(200, json={"hosts": [{"ip": "127.0.0.1", "port": 8000}]})
        return httpx.Response(200, text="ok")

    http_client = httpx.AsyncClient(
        base_url="http://nacos.test/nacos",
        transport=httpx.MockTransport(nacos_mock),
    )
    registry = NacosServiceRegistry(
        Settings(
            nacos_service_name="link-booking-service",
            nacos_group="CUSTOMS_AFFAIRS_LOCAL",
            nacos_register_ip="127.0.0.1",
            nacos_register_port=8000,
        ),
        http_client=http_client,
    )

    await registry.register()
    await registry.close()
    await http_client.aclose()

    registration_form = parse_qs(observed_requests[1].content.decode())
    deregistration_form = parse_qs(observed_requests[-1].content.decode())

    assert observed_requests[1].method == "POST"
    assert observed_requests[1].url.path.endswith("/v1/ns/instance")
    assert registration_form["serviceName"] == ["link-booking-service"]
    assert registration_form["groupName"] == ["CUSTOMS_AFFAIRS_LOCAL"]
    assert registration_form["ip"] == ["127.0.0.1"]
    assert registration_form["port"] == ["8000"]
    assert registration_form["clusterName"] == ["DEFAULT"]
    assert observed_requests[-1].method == "DELETE"
    assert deregistration_form["serviceName"] == registration_form["serviceName"]
    assert deregistration_form["groupName"] == registration_form["groupName"]
