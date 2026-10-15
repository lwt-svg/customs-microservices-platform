"""本地 FastAPI 服务使用的 Nacos 2.x HTTP 注册适配层。

在当前本机 Nacos 2.5.3 环境中，Python gRPC 客户端未能持久化实例；因此本适配层
改用已经人工验证可用的 Nacos 2.x HTTP OpenAPI。
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
    """负责一个本地 Nacos 服务实例的注册、心跳、自动恢复与注销。

    这里故意使用 Nacos HTTP OpenAPI，而不是 Python gRPC SDK。HTTP 的职责只是把本服务
    写入 Nacos 注册表；Java BFF 随后仍可通过自己的 Nacos 客户端发现该实例并用 HTTP
    调用 LINK 的业务 API。
    """

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
        # 首次启动保持 fail-fast：本机 Nacos 配置错误时，不让一个“看似启动成功、实际无法被
        # Java 发现”的服务继续运行。后续运行期故障则由心跳循环自动恢复。
        await self._authenticate_and_register(client)

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

    async def _authenticate_and_register(self, client: httpx.AsyncClient) -> None:
        """完成一次可独立重试的“登录 → 注册 → 可见性确认”流程。"""

        self._access_token = await self._login(client)
        await self._register_instance(client)
        await self._verify_instance_visible(client)

    async def _register_instance(self, client: httpx.AsyncClient) -> None:
        """使用当前 token 将本地实例注册为 Nacos 临时实例。"""

        # Nacos HTTP OpenAPI 接收表单字段；metadata 虽然表达对象，但在表单中必须再次编码成
        # JSON 字符串，因此这里存在“表单 + JSON 字符串”两层编码。
        response = await client.post("/v1/ns/instance", data=self._instance_form())
        response.raise_for_status()

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
            # 临时实例依赖心跳续约；进程退出或心跳消失后，Nacos 会自动将它剔除。
            "ephemeral": "true",
            "enabled": "true",
            "healthy": "true",
            "metadata": json.dumps({"protocol": "http", "health_path": "/health/live"}),
            "accessToken": self._access_token,
        }

    async def _verify_instance_visible(self, client: httpx.AsyncClient) -> None:
        """轮询查询 Nacos，确认刚注册的实例已经完成可见性同步。

        Nacos 的注册接口返回成功后，查询接口偶尔会短暂返回空列表。这里进行有限次数的
        短间隔重试，避免把正常的服务端同步延迟误判成启动失败。
        """

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
            is_visible = any(
                host.get("ip") == self._settings.nacos_register_ip
                and host.get("port") == self._settings.nacos_register_port
                for host in hosts
            )
            if is_visible:
                return

            if attempt < 9:
                await asyncio.sleep(0.2)

        raise RuntimeError("Nacos 注册后等待 2 秒仍未查询到本服务实例。")

    async def _send_heartbeats(self) -> None:
        """定期续约临时实例，并在运行期故障后重新登录、注册和确认。

        Nacos 的 access token 有有效期，且 Nacos 重启时会遗失内存中的临时实例。旧实现中，
        任一心跳异常都会让这个后台 Task 直接结束，FastAPI 虽仍存活，却会从 Nacos 消失。
        本循环将心跳失败统一转为恢复流程；只有应用明确关闭时才结束。
        """

        retry_delay = self._settings.nacos_recovery_initial_delay_seconds
        while True:
            await asyncio.sleep(self._settings.nacos_heartbeat_interval_seconds)
            if self._http_client is None:
                return

            try:
                await self._send_heartbeat(self._http_client)
                # 一旦心跳恢复正常，下一次故障从最小等待时间重新开始计算。
                retry_delay = self._settings.nacos_recovery_initial_delay_seconds
            except asyncio.CancelledError:
                # close() 会取消任务；不能把正常关闭误判成连接故障后继续重连。
                raise
            except (httpx.HTTPError, RuntimeError, ValueError) as error:
                logger.warning(
                    "Nacos 心跳失败，将重新登录并注册：service=%s group=%s address=%s:%s error=%s",
                    self._settings.nacos_service_name,
                    self._settings.nacos_group,
                    self._settings.nacos_register_ip,
                    self._settings.nacos_register_port,
                    self._describe_error(error),
                )
                retry_delay = await self._recover_registration(retry_delay)

    async def _send_heartbeat(self, client: httpx.AsyncClient) -> None:
        """发送一次心跳；失败交由调用方统一执行恢复，而非静默吞掉异常。"""

        # Nacos 2.x 的 beat 接口不会像注册接口一样可靠地使用独立 groupName 参数查找实例；
        # 非 DEFAULT_GROUP 的实例必须把分组编码进 serviceName。否则 HTTP 虽返回 200，
        # CUSTOMS_AFFAIRS_LOCAL 中的原实例仍收不到续约，会每隔一段时间在
        # healthy → unhealthy → deleted → recreated 之间循环。
        grouped_service_name = (
            f"{self._settings.nacos_group}@@{self._settings.nacos_service_name}"
        )
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
        """持续尝试恢复 Nacos 可见性，并返回下次故障的初始退避时间。"""

        while True:
            try:
                client = self._get_or_create_http_client()
                await self._authenticate_and_register(client)
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
        """输出足够排障、但不含 token、密码或响应体的简短错误信息。"""

        if isinstance(error, httpx.HTTPStatusError):
            return f"HTTP {error.response.status_code}"
        return type(error).__name__
