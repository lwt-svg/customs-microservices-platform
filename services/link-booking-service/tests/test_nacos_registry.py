"""Nacos HTTP 适配层单元测试：不连接真实 Nacos 服务端。"""

import asyncio
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


@pytest.mark.anyio
async def test_should_retry_when_registered_instance_is_not_immediately_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注册成功后的首次查询为空时，应等待 Nacos 同步后再次确认。"""

    list_request_count = 0
    sleep_count = 0

    def nacos_mock(request: httpx.Request) -> httpx.Response:
        nonlocal list_request_count
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"accessToken": "test-token"})
        if request.url.path.endswith("/v1/ns/instance/list"):
            list_request_count += 1
            if list_request_count == 1:
                return httpx.Response(200, json={"hosts": []})
            return httpx.Response(200, json={"hosts": [{"ip": "127.0.0.1", "port": 8000}]})
        return httpx.Response(200, text="ok")

    async def fake_sleep(_: float) -> None:
        nonlocal sleep_count
        sleep_count += 1

    monkeypatch.setattr("app.integrations.nacos_registry.asyncio.sleep", fake_sleep)
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

    assert list_request_count == 2
    assert sleep_count == 1


@pytest.mark.anyio
async def test_should_relogin_and_reregister_when_heartbeat_token_expires() -> None:
    """心跳收到 403 后，注册器应换取新 token 并恢复临时实例可见性。"""

    login_count = 0
    registration_tokens: list[str] = []
    heartbeat_count = 0
    heartbeat_service_names: list[str] = []
    recovered = asyncio.Event()

    def nacos_mock(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, heartbeat_count
        if request.url.path.endswith("/v1/auth/login"):
            login_count += 1
            return httpx.Response(200, json={"accessToken": f"token-{login_count}"})
        if request.url.path.endswith("/v1/ns/instance") and request.method == "POST":
            registration_tokens.append(parse_qs(request.content.decode())["accessToken"][0])
            return httpx.Response(200, text="ok")
        if request.url.path.endswith("/v1/ns/instance/list"):
            return httpx.Response(200, json={"hosts": [{"ip": "127.0.0.1", "port": 8000}]})
        if request.url.path.endswith("/v1/ns/instance/beat"):
            heartbeat_count += 1
            heartbeat_form = parse_qs(request.content.decode())
            heartbeat_service_names.append(heartbeat_form["serviceName"][0])
            if heartbeat_count == 1:
                # 模拟 token 已过期；Nacos 重启或网络错误也会进入同一恢复链。
                return httpx.Response(403, text="token expired")
            recovered.set()
            return httpx.Response(
                200,
                json={
                    "clientBeatInterval": 5000,
                    "code": 10200,
                    "lightBeatEnabled": True,
                },
            )
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
            nacos_heartbeat_interval_seconds=0.005,
            nacos_recovery_initial_delay_seconds=0.005,
            nacos_recovery_max_delay_seconds=0.02,
        ),
        http_client=http_client,
    )

    await registry.register()
    await asyncio.wait_for(recovered.wait(), timeout=0.5)
    await registry.close()
    await http_client.aclose()

    assert login_count == 2
    assert registration_tokens == ["token-1", "token-2"]
    assert heartbeat_count >= 2
    assert set(heartbeat_service_names) == {
        "CUSTOMS_AFFAIRS_LOCAL@@link-booking-service"
    }
