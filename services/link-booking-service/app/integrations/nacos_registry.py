"""本地 FastAPI 服务使用的 Nacos 2.x HTTP 注册适配层。

在当前本机 Nacos 2.5.3 环境中，Python gRPC 客户端未能持久化实例；因此本适配层
改用已经人工验证可用的 Nacos 2.x HTTP OpenAPI。
"""

import asyncio
import contextlib
import json
from typing import Any

import httpx

from app.core.config import Settings


class NacosServiceRegistry:
    """负责一个本地 Nacos 服务实例的注册、心跳、确认与注销。"""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        # 单元测试传入 MockTransport 客户端；真实运行时才创建网络客户端。
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._access_token: str | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def register(self) -> None:
        """登录、注册实例、向 Nacos 反查确认，再启动定时心跳。"""

        client = self._get_or_create_http_client()
        self._access_token = await self._login(client)

        # Nacos v1 HTTP OpenAPI expects form fields. metadata must itself be a JSON string.
        response = await client.post("/v1/ns/instance", data=self._instance_form())
        response.raise_for_status()
        await self._verify_instance_visible(client)

        # HTTP 注册的临时实例需要定期续约；后台任务只在 FastAPI 进程存活期间运行。
        self._heartbeat_task = asyncio.create_task(self._send_heartbeats())

    async def close(self) -> None:
        """停止心跳、正常注销实例，并关闭本地 HTTP 连接。"""

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

        if self._http_client is not None and self._access_token is not None:
            # 注销失败不应阻止本地进程退出；临时实例也会在心跳超时后被 Nacos 清理。
            with contextlib.suppress(httpx.HTTPError):
                # HTTPX 的 delete() 快捷方法不接收 data；使用通用 request() 发送表单体。
                response = await self._http_client.request(
                    "DELETE", "/v1/ns/instance", data=self._instance_form()
                )
                response.raise_for_status()

        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
        self._http_client = None
        self._access_token = None

    def _get_or_create_http_client(self) -> httpx.AsyncClient:
        """创建可复用 HTTP 客户端；密码绝不写入日志或 URL。"""

        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=f"http://{self._settings.nacos_discovery_server_addr}/nacos",
                timeout=5.0,
            )
        return self._http_client

    async def _login(self, client: httpx.AsyncClient) -> str:
        """将本地 Nacos 用户名和密码换取短期有效的访问令牌。"""

        response = await client.post(
            "/v1/auth/login",
            data={"username": self._settings.nacos_username, "password": self._settings.nacos_password},
        )
        response.raise_for_status()
        access_token = response.json().get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("本地 Nacos 登录响应中没有 accessToken。")
        return access_token

    def _instance_form(self) -> dict[str, str]:
        """生成注册与注销共用的服务身份表单。"""

        if self._access_token is None:
            raise RuntimeError("Nacos 尚未登录，不能发送实例请求。")

        return {
            "serviceName": self._settings.nacos_service_name,
            "groupName": self._settings.nacos_group,
            "ip": self._settings.nacos_register_ip,
            "port": str(self._settings.nacos_register_port),
            "clusterName": self._settings.nacos_cluster_name,
            "ephemeral": "true",
            "enabled": "true",
            "healthy": "true",
            "metadata": json.dumps({"protocol": "http", "health_path": "/health/live"}),
            "accessToken": self._access_token,
        }

    async def _verify_instance_visible(self, client: httpx.AsyncClient) -> None:
        """直接查询 Nacos，确保“启动成功”代表服务端确实保存了实例。"""

        response = await client.get(
            "/v1/ns/instance/list",
            params={
                "serviceName": self._settings.nacos_service_name,
                "groupName": self._settings.nacos_group,
                "accessToken": self._access_token,
            },
        )
        response.raise_for_status()
        hosts: list[dict[str, Any]] = response.json().get("hosts", [])
        is_visible = any(
            host.get("ip") == self._settings.nacos_register_ip
            and host.get("port") == self._settings.nacos_register_port
            for host in hosts
        )
        if not is_visible:
            raise RuntimeError("Nacos 注册后未查询到本服务实例。")

    async def _send_heartbeats(self) -> None:
        """每五秒续约一次 HTTP 临时实例，直到应用停止。"""

        while True:
            await asyncio.sleep(5)
            if self._http_client is None:
                return

            beat = {
                "serviceName": self._settings.nacos_service_name,
                "ip": self._settings.nacos_register_ip,
                "port": self._settings.nacos_register_port,
                "cluster": self._settings.nacos_cluster_name,
                "weight": 1.0,
                "metadata": {"protocol": "http", "health_path": "/health/live"},
                "scheduled": True,
            }
            response = await self._http_client.put(
                "/v1/ns/instance/beat",
                data={
                    "serviceName": self._settings.nacos_service_name,
                    "groupName": self._settings.nacos_group,
                    "ip": self._settings.nacos_register_ip,
                    "port": str(self._settings.nacos_register_port),
                    "beat": json.dumps(beat),
                    "accessToken": self._access_token,
                },
            )
            response.raise_for_status()
