"""本地 Nacos 2.x HTTP 服务注册适配层。

本机学习环境使用已经验证可用的 Nacos HTTP OpenAPI。Java BFF 通过自己的 Nacos 客户端
发现 ``load-planning-service``，随后仍使用普通 HTTP 调用本服务。
"""

import asyncio
import contextlib
import json
import logging
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class NacosServiceRegistry:
    """负责注册、心跳、故障恢复和正常注销一个临时实例。"""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._access_token: str | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def register(self) -> None:
        """登录、注册、反查实例可见性，再启动心跳。"""

        client = self._get_or_create_http_client()
        await self._authenticate_and_register(client)
        self._heartbeat_task = asyncio.create_task(
            self._send_heartbeats(),
            name="load-planning-nacos-heartbeat",
        )

    async def close(self) -> None:
        """停止心跳并尽力注销临时实例。"""

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

        if self._http_client is not None and self._access_token is not None:
            # 注销失败不阻止进程退出；临时实例也会在心跳超时后被 Nacos 清理。
            with contextlib.suppress(httpx.HTTPError):
                response = await self._http_client.request(
                    "DELETE",
                    "/v1/ns/instance",
                    data=self._instance_form(),
                )
                response.raise_for_status()

        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
        self._http_client = None
        self._access_token = None

    async def _authenticate_and_register(self, client: httpx.AsyncClient) -> None:
        """完成一次可重试的登录、注册和可见性确认。"""

        self._access_token = await self._login(client)
        response = await client.post("/v1/ns/instance", data=self._instance_form())
        response.raise_for_status()
        await self._verify_instance_visible(client)

    def _get_or_create_http_client(self) -> httpx.AsyncClient:
        """创建复用客户端；密码不拼进 URL，也不输出日志。"""

        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=f"http://{self._settings.nacos_discovery_server_addr}/nacos",
                timeout=5.0,
            )
        return self._http_client

    async def _login(self, client: httpx.AsyncClient) -> str:
        """用本机环境变量中的凭据换取短期 token。"""

        response = await client.post(
            "/v1/auth/login",
            data={
                "username": self._settings.nacos_username,
                "password": self._settings.nacos_password,
            },
        )
        response.raise_for_status()
        access_token = response.json().get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("本地 Nacos 登录响应中没有 accessToken。")
        return access_token

    def _instance_form(self) -> dict[str, str]:
        """生成注册和注销共用的服务身份。"""

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
        """有限轮询反查，避免把 Nacos 短暂同步延迟误判成失败。"""

        for attempt in range(10):
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
            if any(
                host.get("ip") == self._settings.nacos_register_ip
                and host.get("port") == self._settings.nacos_register_port
                for host in hosts
            ):
                return
            if attempt < 9:
                await asyncio.sleep(0.2)
        raise RuntimeError("Nacos 注册后等待 2 秒仍未查询到本服务实例。")

    async def _send_heartbeats(self) -> None:
        """持续发送心跳，token 过期或 Nacos 重启后自动恢复注册。"""

        retry_delay = self._settings.nacos_recovery_initial_delay_seconds
        while True:
            await asyncio.sleep(self._settings.nacos_heartbeat_interval_seconds)
            if self._http_client is None:
                return
            try:
                await self._send_heartbeat(self._http_client)
                retry_delay = self._settings.nacos_recovery_initial_delay_seconds
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, RuntimeError, ValueError) as error:
                logger.warning(
                    "Nacos 心跳失败，将重新注册：service=%s address=%s:%s error=%s",
                    self._settings.nacos_service_name,
                    self._settings.nacos_register_ip,
                    self._settings.nacos_register_port,
                    self._describe_error(error),
                )
                retry_delay = await self._recover_registration(retry_delay)

    async def _send_heartbeat(self, client: httpx.AsyncClient) -> None:
        """发送一次非默认分组兼容的 Nacos 心跳。"""

        # Nacos 旧 HTTP beat 接口在非 DEFAULT_GROUP 下需要 group@@serviceName，
        # 否则接口可能返回 200，但实际实例仍会因没有续约而消失。
        grouped_service_name = f"{self._settings.nacos_group}@@{self._settings.nacos_service_name}"
        beat = {
            "serviceName": grouped_service_name,
            "ip": self._settings.nacos_register_ip,
            "port": self._settings.nacos_register_port,
            "cluster": self._settings.nacos_cluster_name,
            "weight": 1.0,
            "metadata": {"protocol": "http", "health_path": "/health/live"},
            "scheduled": True,
        }
        response = await client.put(
            "/v1/ns/instance/beat",
            data={
                "serviceName": grouped_service_name,
                "groupName": self._settings.nacos_group,
                "ip": self._settings.nacos_register_ip,
                "port": str(self._settings.nacos_register_port),
                "beat": json.dumps(beat),
                "accessToken": self._access_token,
            },
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").startswith("application/json"):
            code = response.json().get("code")
            if code not in (None, 10200):
                raise RuntimeError(f"Nacos heartbeat rejected with code {code}")

    async def _recover_registration(self, retry_delay: float) -> float:
        """按指数退避重新登录和注册，成功后重置等待时间。"""

        while True:
            try:
                await self._authenticate_and_register(self._get_or_create_http_client())
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, RuntimeError, ValueError) as error:
                logger.warning(
                    "Nacos 重新注册失败，%.1f 秒后重试：service=%s error=%s",
                    retry_delay,
                    self._settings.nacos_service_name,
                    self._describe_error(error),
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(
                    retry_delay * 2,
                    self._settings.nacos_recovery_max_delay_seconds,
                )
            else:
                logger.info(
                    "Nacos 注册已自动恢复：service=%s group=%s address=%s:%s",
                    self._settings.nacos_service_name,
                    self._settings.nacos_group,
                    self._settings.nacos_register_ip,
                    self._settings.nacos_register_port,
                )
                return self._settings.nacos_recovery_initial_delay_seconds

    @staticmethod
    def _describe_error(error: Exception) -> str:
        """只输出错误类型或状态码，不泄露 token、密码和响应体。"""

        if isinstance(error, httpx.HTTPStatusError):
            return f"HTTP {error.response.status_code}"
        return type(error).__name__
