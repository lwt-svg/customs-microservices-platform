"""Nacos 注册适配层测试；全部使用 MockTransport，不连接真实 Nacos。"""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import Settings
from app.integrations.nacos_registry import NacosServiceRegistry


def test_should_use_load_planning_specific_nacos_identity_by_default() -> None:
    """共享 .env.local 中 LINK 的 8000 端口不能污染装载服务身份。"""

    settings = Settings(_env_file=None)

    assert settings.nacos_service_name == "load-planning-service"
    assert settings.nacos_register_port == 8200
    assert settings.nacos_discovery_enabled is False


def test_should_read_load_planning_specific_register_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务专属注册 IP 不能被共享的 LINK 配置覆盖或静默忽略。"""

    monkeypatch.setenv("LOAD_PLANNING_NACOS_REGISTER_IP", "127.0.0.9")

    settings = Settings(_env_file=None)

    assert settings.nacos_register_ip == "127.0.0.9"


@pytest.mark.asyncio
async def test_should_register_and_deregister_load_planning_service() -> None:
    """注册和注销必须使用相同的服务名、分组、IP 与 8200 端口。"""

    observed: list[httpx.Request] = []

    def nacos_mock(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"accessToken": "test-token"})
        if request.url.path.endswith("/v1/ns/instance/list"):
            return httpx.Response(
                200,
                json={"hosts": [{"ip": "127.0.0.1", "port": 8200}]},
            )
        return httpx.Response(200, text="ok")

    http_client = httpx.AsyncClient(
        base_url="http://nacos.test/nacos",
        transport=httpx.MockTransport(nacos_mock),
    )
    registry = NacosServiceRegistry(
        Settings(
            nacos_service_name="load-planning-service",
            nacos_group="CUSTOMS_AFFAIRS_LOCAL",
            nacos_register_ip="127.0.0.1",
            nacos_register_port=8200,
        ),
        http_client=http_client,
    )

    await registry.register()
    await registry.close()
    await http_client.aclose()

    registration_form = parse_qs(observed[1].content.decode())
    deregistration_form = parse_qs(observed[-1].content.decode())
    assert registration_form["serviceName"] == ["load-planning-service"]
    assert registration_form["groupName"] == ["CUSTOMS_AFFAIRS_LOCAL"]
    assert registration_form["ip"] == ["127.0.0.1"]
    assert registration_form["port"] == ["8200"]
    assert registration_form["clusterName"] == ["DEFAULT"]
    assert observed[-1].method == "DELETE"
    assert deregistration_form["serviceName"] == registration_form["serviceName"]


@pytest.mark.asyncio
async def test_should_retry_until_registered_instance_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次反查为空时，适配层应等待同步并再次查询。"""

    list_request_count = 0
    sleep_count = 0

    def nacos_mock(request: httpx.Request) -> httpx.Response:
        nonlocal list_request_count
        if request.url.path.endswith("/v1/auth/login"):
            return httpx.Response(200, json={"accessToken": "test-token"})
        if request.url.path.endswith("/v1/ns/instance/list"):
            list_request_count += 1
            hosts = [] if list_request_count == 1 else [{"ip": "127.0.0.1", "port": 8200}]
            return httpx.Response(200, json={"hosts": hosts})
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
            nacos_service_name="load-planning-service",
            nacos_register_ip="127.0.0.1",
            nacos_register_port=8200,
        ),
        http_client=http_client,
    )

    await registry.register()
    await registry.close()
    await http_client.aclose()

    assert list_request_count == 2
    assert sleep_count == 1


@pytest.mark.asyncio
async def test_should_reregister_after_heartbeat_token_expiration() -> None:
    """心跳 403 后应重新登录注册，并使用 group@@service 继续心跳。"""

    login_count = 0
    heartbeat_count = 0
    heartbeat_names: list[str] = []
    recovered = asyncio.Event()

    def nacos_mock(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, heartbeat_count
        if request.url.path.endswith("/v1/auth/login"):
            login_count += 1
            return httpx.Response(200, json={"accessToken": f"token-{login_count}"})
        if request.url.path.endswith("/v1/ns/instance/list"):
            return httpx.Response(
                200,
                json={"hosts": [{"ip": "127.0.0.1", "port": 8200}]},
            )
        if request.url.path.endswith("/v1/ns/instance/beat"):
            heartbeat_count += 1
            form = parse_qs(request.content.decode())
            heartbeat_names.append(form["serviceName"][0])
            if heartbeat_count == 1:
                return httpx.Response(403, text="token expired")
            recovered.set()
            return httpx.Response(200, json={"code": 10200})
        return httpx.Response(200, text="ok")

    http_client = httpx.AsyncClient(
        base_url="http://nacos.test/nacos",
        transport=httpx.MockTransport(nacos_mock),
    )
    registry = NacosServiceRegistry(
        Settings(
            nacos_service_name="load-planning-service",
            nacos_register_ip="127.0.0.1",
            nacos_register_port=8200,
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
    assert heartbeat_count >= 2
    assert set(heartbeat_names) == {"CUSTOMS_AFFAIRS_LOCAL@@load-planning-service"}
