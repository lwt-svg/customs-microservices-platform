"""LINK 向模拟承运人可靠投递，并接收幂等终态回调。"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Protocol

import httpx

from app.core.config import Settings, settings
from app.models.booking_application import (
    BookingApplicationEvent,
    BookingEventType,
    BookingStatus,
)
from app.models.carrier_submission import (
    CarrierBookingRequest,
    CarrierBookingRequestStatus,
    CarrierCallbackReceipt,
)
from app.repositories.carrier_submissions import (
    CarrierSubmissionRepository,
    ConcurrentCarrierCallbackPersistenceError,
    DuplicateCallbackReceiptPersistenceError,
)
from app.schemas.carrier_submission import (
    CarrierCallbackPayload,
    CarrierCallbackResult,
    CarrierSubmissionRead,
)


class CarrierSubmissionNotFoundError(Exception):
    """申请尚未创建承运人提交任务。"""


class CarrierSubmissionNotRetryableError(Exception):
    """当前提交状态不允许人工重复发送。"""


class InvalidCarrierCallbackTokenError(Exception):
    """回调 Token 与本地约定不一致。"""


class CarrierCallbackConflictError(Exception):
    """相同 callback_id 携带了不同事实，或承运人请求号不匹配。"""


class ConcurrentCarrierCallbackError(Exception):
    """另一个回调已先改变申请，调用方可安全重放本次 callback_id。"""


class AsyncHttpPoster(Protocol):
    """测试只需实现的最小异步 HTTP 接口。"""

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        """发送 HTTP POST。"""


class CarrierSubmissionService:
    """投递任务的状态机、退避规则和回调幂等规则。"""

    _TERMINAL_APPLICATION_STATUSES: ClassVar[set[BookingStatus]] = {
        BookingStatus.CONFIRMED,
        BookingStatus.REJECTED,
        BookingStatus.CANCELLED,
    }

    def __init__(
        self,
        repository: CarrierSubmissionRepository,
        config: Settings = settings,
        http_client: AsyncHttpPoster | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.http_client = http_client

    @staticmethod
    def now() -> datetime:
        """项目数据库使用无时区 DateTime，因此统一去掉 tzinfo 后再比较。"""

        return datetime.now(UTC).replace(tzinfo=None)

    @staticmethod
    def request_payload(application: object) -> dict[str, object]:
        """从申请快照重建发送体，不额外存一份可能过时的 JSON。"""

        return {
            "application_number": application.application_number,
            "origin_port": application.origin_port,
            "destination_port": application.destination_port,
            "transport_mode": application.transport_mode,
            "container_type": application.container_type,
            "requested_etd": (
                application.requested_etd.isoformat()
                if application.requested_etd is not None
                else None
            ),
        }

    @staticmethod
    def stable_hash(payload: dict[str, object]) -> str:
        """对规范化 JSON 计算哈希，用于请求和回调的幂等比对。"""

        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def build_pending_request(cls, application: object) -> CarrierBookingRequest:
        """创建和申请一同提交的 PENDING 任务。"""

        payload = cls.request_payload(application)
        return CarrierBookingRequest(
            application=application,
            idempotency_key=f"DEMO-LINK-CARRIER-{application.application_number}",
            payload_hash=cls.stable_hash(payload),
            status=CarrierBookingRequestStatus.PENDING,
            attempt_count=0,
            manual_retry_count=0,
        )

    async def get_for_application(self, application_id: int) -> CarrierBookingRequest | None:
        """详情接口查询提交快照。"""

        return await self.repository.get_by_application_id(application_id)

    async def get_cancellable_for_update(
        self, application_id: int
    ) -> CarrierBookingRequest | None:
        """在申请取消事务中锁住尚未受理的投递任务。"""

        return await self.repository.get_cancellable_for_update(application_id)

    async def dispatch_new(self, request_id: int) -> CarrierBookingRequest | None:
        """首次提交后立即尝试一次；若已被其他 worker 抢占则返回当前快照。"""

        request = await self._claim(request_id, manual=False)
        if request is None:
            return await self.repository.get_request(request_id)
        return await self._send_claimed(request)

    async def retry_manually(self, application_number: str) -> CarrierBookingRequest:
        """页面明确重试 RETRY_WAIT 或 DEAD 任务。"""

        existing = await self.repository.get_by_application_number(application_number)
        if existing is None:
            raise CarrierSubmissionNotFoundError
        request = await self._claim(existing.id, manual=True)
        if request is None:
            raise CarrierSubmissionNotRetryableError
        return await self._send_claimed(request)

    async def retry_due(self, limit: int = 50) -> list[CarrierBookingRequest]:
        """供调度器周期调用；每条任务仍通过租约做并发抢占。"""

        now = self.now()
        request_ids = await self.repository.list_due_request_ids(now, limit)
        results: list[CarrierBookingRequest] = []
        for request_id in request_ids:
            request = await self._claim(request_id, manual=False)
            if request is not None:
                results.append(await self._send_claimed(request))
        return results

    async def _claim(
        self, request_id: int, *, manual: bool
    ) -> CarrierBookingRequest | None:
        """为一次 HTTP 尝试设置有限租约并原子增加次数。"""

        now = self.now()
        return await self.repository.claim_for_delivery(
            request_id,
            now,
            now + timedelta(seconds=self.config.carrier_dispatch_lease_seconds),
            manual=manual,
        )

    async def _post(self, request: CarrierBookingRequest) -> httpx.Response:
        """向独立模拟服务发送一次带幂等键的请求。"""

        url = f"{self.config.carrier_simulator_url.rstrip('/')}/api/v1/carrier/bookings"
        kwargs: dict[str, object] = {
            "json": self.request_payload(request.application),
            "headers": {"Idempotency-Key": request.idempotency_key},
        }
        if self.http_client is not None:
            return await self.http_client.post(url, **kwargs)
        async with httpx.AsyncClient(timeout=self.config.carrier_dispatch_timeout_seconds) as client:
            return await client.post(url, **kwargs)

    async def _send_claimed(self, request: CarrierBookingRequest) -> CarrierBookingRequest:
        """执行网络调用，并把成功、可重试失败或永久失败写回任务。"""

        try:
            response = await self._post(request)
        except httpx.HTTPError as error:
            return await self.repository.finish_delivery(
                request.id,
                self._retryable_failure_values(
                    request,
                    code=type(error).__name__,
                    message=str(error),
                ),
            )

        if response.status_code in {408, 429} or response.status_code >= 500:
            return await self.repository.finish_delivery(
                request.id,
                self._retryable_failure_values(
                    request,
                    code=f"HTTP_{response.status_code}",
                    message="模拟承运人暂时未接受请求",
                    http_status=response.status_code,
                ),
            )
        if response.is_error:
            return await self.repository.finish_delivery(
                request.id,
                self._permanent_failure_values(
                    code=f"HTTP_{response.status_code}",
                    message="模拟承运人拒绝了请求，请检查接口契约",
                    http_status=response.status_code,
                ),
            )

        try:
            payload = response.json()
            carrier_request_id = payload["carrier_request_id"]
            if not isinstance(carrier_request_id, str) or not carrier_request_id:
                raise ValueError("carrier_request_id 缺失")
        except (ValueError, KeyError, TypeError):
            return await self.repository.finish_delivery(
                request.id,
                self._permanent_failure_values(
                    code="INVALID_RESPONSE",
                    message="模拟承运人成功响应缺少 carrier_request_id",
                    http_status=response.status_code,
                ),
            )

        return await self.repository.finish_delivery(
            request.id,
            {
                "carrier_request_id": carrier_request_id,
                "status": CarrierBookingRequestStatus.ACCEPTED,
                "next_attempt_at": None,
                "lease_until": None,
                "last_http_status": response.status_code,
                "last_error_code": None,
                "last_error_message": None,
            },
        )

    def _retryable_failure_values(
        self,
        request: CarrierBookingRequest,
        *,
        code: str,
        message: str,
        http_status: int | None = None,
    ) -> dict[str, object]:
        """网络、超时、408、429、5xx 使用指数退避，达到上限后转 DEAD。"""

        values: dict[str, object] = {
            "lease_until": None,
            "last_http_status": http_status,
            "last_error_code": code[:64],
            "last_error_message": message[:500],
        }
        if request.attempt_count >= self.config.carrier_dispatch_max_attempts:
            values["status"] = CarrierBookingRequestStatus.DEAD
            values["next_attempt_at"] = None
            return values
        delay = self.config.carrier_dispatch_retry_base_seconds * (
            2 ** (request.attempt_count - 1)
        )
        values["status"] = CarrierBookingRequestStatus.RETRY_WAIT
        values["next_attempt_at"] = self.now() + timedelta(seconds=delay)
        return values

    @staticmethod
    def _permanent_failure_values(
        *,
        code: str,
        message: str,
        http_status: int | None,
    ) -> dict[str, object]:
        """不可恢复的 4xx 或响应契约错误直接等待人工处理。"""

        return {
            "status": CarrierBookingRequestStatus.DEAD,
            "next_attempt_at": None,
            "lease_until": None,
            "last_http_status": http_status,
            "last_error_code": code[:64],
            "last_error_message": message[:500],
        }

    async def receive_callback(
        self,
        token: str,
        payload: CarrierCallbackPayload,
    ) -> CarrierCallbackResult:
        """验证 Token，幂等接收终态，并保护申请不被乱序回调倒退。"""

        expected_token = self.config.carrier_callback_token.get_secret_value()
        if not secrets.compare_digest(token, expected_token):
            raise InvalidCarrierCallbackTokenError

        normalized_payload = payload.model_dump(mode="json")
        payload_hash = self.stable_hash(normalized_payload)
        existing_receipt = await self.repository.get_callback(payload.callback_id)
        if existing_receipt is not None:
            if existing_receipt.payload_hash != payload_hash:
                raise CarrierCallbackConflictError
            request = await self.repository.get_request(existing_receipt.request_id)
            if request is None:  # pragma: no cover - 外键已保证
                raise CarrierSubmissionNotFoundError
            return CarrierCallbackResult(
                callback_id=payload.callback_id,
                duplicate=True,
                ignored=existing_receipt.ignored,
                application_status=request.application.status,
            )

        request = await self.repository.get_by_application_number(payload.application_number)
        if request is None:
            raise CarrierSubmissionNotFoundError
        if (
            request.carrier_request_id is not None
            and request.carrier_request_id != payload.carrier_request_id
        ):
            raise CarrierCallbackConflictError
        if request.idempotency_key != payload.idempotency_key:
            raise CarrierCallbackConflictError

        application = request.application
        ignored = application.status in self._TERMINAL_APPLICATION_STATUSES
        if (
            payload.status is CarrierBookingRequestStatus.PROCESSING
            and request.status is CarrierBookingRequestStatus.PROCESSING
        ):
            ignored = True
        event: BookingApplicationEvent | None = None
        if not ignored:
            previous_status = application.status
            request.status = payload.status
            request.carrier_request_id = payload.carrier_request_id
            request.next_attempt_at = None
            request.lease_until = None
            request.last_error_code = None
            request.last_error_message = None
            if payload.status is CarrierBookingRequestStatus.PROCESSING:
                # 承运人开始处理不等于 LINK 申请进入终态。申请仍保持 SUBMITTED，只追加
                # 一条明确的处理事件，页面无需从任务状态猜测时间线。
                event = BookingApplicationEvent(
                    application=application,
                    event_type=BookingEventType.CARRIER_PROCESSING,
                    previous_status=previous_status,
                    current_status=application.status,
                    actor_label="CARRIER_CALLBACK_DEMO",
                )
            else:
                target_status = BookingStatus(payload.status.value)
                application.status = target_status
                application.booking_reference = payload.booking_reference
                event = BookingApplicationEvent(
                    application=application,
                    event_type=BookingEventType(target_status.value),
                    previous_status=previous_status,
                    current_status=target_status,
                    booking_reference=payload.booking_reference,
                    actor_label="CARRIER_CALLBACK_DEMO",
                )

        receipt = CarrierCallbackReceipt(
            callback_id=payload.callback_id,
            request=request,
            payload_hash=payload_hash,
            carrier_status=payload.status,
            ignored=ignored,
            occurred_at=(
                payload.occurred_at.astimezone(UTC).replace(tzinfo=None)
                if payload.occurred_at.tzinfo is not None
                else payload.occurred_at
            ),
        )
        try:
            await self.repository.save_callback(application, event, request, receipt)
        except DuplicateCallbackReceiptPersistenceError:
            # 另一个并发请求可能刚写入相同 callback_id；重新走幂等比对，而不是返回 500。
            concurrent_receipt = await self.repository.get_callback(payload.callback_id)
            if concurrent_receipt is None or concurrent_receipt.payload_hash != payload_hash:
                raise CarrierCallbackConflictError from None
            concurrent_request = await self.repository.get_request(
                concurrent_receipt.request_id
            )
            if concurrent_request is None:  # pragma: no cover - 外键已保证
                raise CarrierSubmissionNotFoundError
            return CarrierCallbackResult(
                callback_id=payload.callback_id,
                duplicate=True,
                ignored=concurrent_receipt.ignored,
                application_status=concurrent_request.application.status,
            )
        except ConcurrentCarrierCallbackPersistenceError as error:
            raise ConcurrentCarrierCallbackError from error

        return CarrierCallbackResult(
            callback_id=payload.callback_id,
            duplicate=False,
            ignored=ignored,
            application_status=application.status,
        )


def to_submission_read(request: CarrierBookingRequest) -> CarrierSubmissionRead:
    """集中构造只读视图，防止 API 意外暴露幂等键、租约或内部错误码。"""

    return CarrierSubmissionRead.model_validate(request)
