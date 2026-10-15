"""模拟承运人订舱业务规则和可靠回调投递。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from app.core.config import Settings, settings
from app.models.carrier_booking import (
    CallbackDelivery,
    CallbackDeliveryStatus,
    CallbackEventType,
    CarrierBooking,
    CarrierBookingEvent,
    CarrierBookingEventType,
    CarrierBookingStatus,
)
from app.repositories.carrier_bookings import (
    CarrierBookingRepository,
    ConcurrentBookingChangePersistenceError,
    DuplicateIdempotencyKeyPersistenceError,
)
from app.schemas.carrier_booking import (
    CarrierBookingCreate,
    CarrierBookingDecision,
    CarrierDecision,
)


class DuplicateIdempotencyKeyError(Exception):
    """相同幂等键与不同请求体组合时抛出。"""


class BookingNotFoundError(Exception):
    """内部主键找不到对应的模拟承运人申请。"""


class InvalidCarrierBookingTransitionError(Exception):
    """门户正在进行违反状态机的操作。"""


class CallbackDeliveryNotFoundError(Exception):
    """门户请求重试一个不存在的回调投递。"""


class CallbackDeliveryNotRetryableError(Exception):
    """已经成功或尚未失败的投递不允许由门户重复触发。"""


class AsyncHttpPoster(Protocol):
    """为单元测试保留的最小 HTTP 客户端抽象。"""

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        """发送一条 HTTP POST 请求。"""


@dataclass(frozen=True)
class CreateCarrierBookingResult:
    """幂等创建接口同时需要返回快照和“本次是否新建”的事实。"""

    booking: CarrierBooking
    created: bool


@dataclass(frozen=True)
class CarrierDecisionResult:
    """最终决定的快照、回调任务，以及本次是否真的创建了新决定。"""

    booking: CarrierBooking
    delivery: CallbackDelivery
    decided_now: bool


class CarrierBookingService:
    """处理平台提交和承运人门户状态机。"""

    def __init__(self, repository: CarrierBookingRepository) -> None:
        self.repository = repository

    @staticmethod
    def _fingerprint(payload: CarrierBookingCreate) -> str:
        """为同一幂等键的请求体生成稳定指纹。

        幂等键重复本身不是错误：网络超时后的原请求重放应返回原结果。只有同一键配上
        不同字段才可能把两笔业务混为一谈，因此明确返回 409。
        """

        raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _carrier_request_id(idempotency_key: str) -> str:
        """只从幂等键推导本地构造请求号，重放请求始终看到同一号码。"""

        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12].upper()
        return f"DEMO-CARRIER-REQ-{digest}"

    async def create(
        self,
        idempotency_key: str,
        payload: CarrierBookingCreate,
    ) -> CreateCarrierBookingResult:
        """接收平台异步提交的构造订舱申请。"""

        fingerprint = self._fingerprint(payload)
        existing = await self.repository.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise DuplicateIdempotencyKeyError
            return CreateCarrierBookingResult(booking=existing, created=False)

        booking = CarrierBooking(
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            carrier_request_id=self._carrier_request_id(idempotency_key),
            **payload.model_dump(),
            status=CarrierBookingStatus.RECEIVED,
        )
        event = CarrierBookingEvent(
            booking=booking,
            event_type=CarrierBookingEventType.RECEIVED,
            previous_status=None,
            current_status=CarrierBookingStatus.RECEIVED,
            actor_label="LINK_PLATFORM_DEMO",
        )
        try:
            persisted = await self.repository.add_with_event(booking, event)
        except DuplicateIdempotencyKeyPersistenceError as error:
            # 两个并发请求都没有查到旧行时，最终由唯一约束裁决。随后读已有行的重放
            # 语义由调用方下一次请求获得，避免把未知请求体误判为同一笔业务。
            raise DuplicateIdempotencyKeyError from error
        return CreateCarrierBookingResult(booking=persisted, created=True)

    async def get(self, booking_id: int) -> CarrierBooking:
        """查询单份当前快照。"""

        booking = await self.repository.get(booking_id)
        if booking is None:
            raise BookingNotFoundError
        return booking

    async def get_by_idempotency_key(self, idempotency_key: str) -> CarrierBooking:
        """让平台能在超时后通过幂等键找回原请求。"""

        booking = await self.repository.get_by_idempotency_key(idempotency_key)
        if booking is None:
            raise BookingNotFoundError
        return booking

    async def list(
        self,
        status: CarrierBookingStatus | None = None,
        keyword: str | None = None,
    ) -> list[CarrierBooking]:
        """门户列表查询。"""

        return await self.repository.list_bookings(status, keyword)

    async def detail(
        self, booking_id: int
    ) -> tuple[CarrierBooking, list[CarrierBookingEvent], list[CallbackDelivery]]:
        """门户详情查询。"""

        detail = await self.repository.get_detail(booking_id)
        if detail is None:
            raise BookingNotFoundError
        return detail

    async def start_processing(self, booking_id: int) -> tuple[CarrierBooking, CallbackDelivery]:
        """只允许已收到的申请进入人工处理，并原子创建 PROCESSING 回调。"""

        booking = await self.get(booking_id)
        if booking.status is not CarrierBookingStatus.RECEIVED:
            raise InvalidCarrierBookingTransitionError
        previous_status = booking.status
        booking.status = CarrierBookingStatus.PROCESSING
        event = CarrierBookingEvent(
            booking=booking,
            event_type=CarrierBookingEventType.PROCESSING_STARTED,
            previous_status=previous_status,
            current_status=booking.status,
            actor_label="CARRIER_PORTAL_DEMO",
        )
        delivery = CallbackDelivery(
            booking=booking,
            event_type=CallbackEventType.BOOKING_PROCESSING,
            status=CallbackDeliveryStatus.PENDING,
            attempt_count=0,
        )
        try:
            persisted = await self.repository.save_with_event(booking, event, delivery)
        except ConcurrentBookingChangePersistenceError as error:
            raise InvalidCarrierBookingTransitionError from error
        persisted_delivery = await self.repository.get_delivery(delivery.id)
        if persisted_delivery is None:  # pragma: no cover - 数据库事务已保证，防御性分支
            raise RuntimeError("处理中回调投递在提交后意外缺失")
        return persisted, persisted_delivery

    async def decide(
        self,
        booking_id: int,
        payload: CarrierBookingDecision,
    ) -> CarrierDecisionResult:
        """对处理中申请确认或拒绝，并原子创建应回调给 LINK 的任务。"""

        booking = await self.get(booking_id)
        target_status = CarrierBookingStatus(payload.decision.value)
        if booking.status in {CarrierBookingStatus.CONFIRMED, CarrierBookingStatus.REJECTED}:
            # 前端重试或网络超时重放同一个最终决定时，不能再写第二条事件或第二条回调。
            # 相反决定仍然是明确的业务冲突。
            if booking.status is not target_status:
                raise InvalidCarrierBookingTransitionError
            delivery = await self.repository.get_latest_delivery_for_booking(booking.id)
            if delivery is None:  # pragma: no cover - 正常终态总由同一事务创建 delivery
                raise RuntimeError("最终决定缺少回调投递记录")
            return CarrierDecisionResult(
                booking=booking,
                delivery=delivery,
                decided_now=False,
            )
        if booking.status is not CarrierBookingStatus.PROCESSING:
            raise InvalidCarrierBookingTransitionError

        previous_status = booking.status
        if payload.decision is CarrierDecision.CONFIRMED:
            booking.status = CarrierBookingStatus.CONFIRMED
            # booking 已在首次提交时写入数据库，id 可作为本地稳定构造参考号的一部分。
            booking.booking_reference = f"DEMO-CARRIER-BOOKING-{booking.id:06d}"
            booking.rejection_reason_code = None
            event_type = CarrierBookingEventType.CONFIRMED
            callback_event_type = CallbackEventType.BOOKING_CONFIRMED
        else:
            booking.status = CarrierBookingStatus.REJECTED
            booking.booking_reference = None
            booking.rejection_reason_code = payload.rejection_reason_code
            event_type = CarrierBookingEventType.REJECTED
            callback_event_type = CallbackEventType.BOOKING_REJECTED

        event = CarrierBookingEvent(
            booking=booking,
            event_type=event_type,
            previous_status=previous_status,
            current_status=booking.status,
            actor_label="CARRIER_PORTAL_DEMO",
        )
        delivery = CallbackDelivery(
            booking=booking,
            event_type=callback_event_type,
            status=CallbackDeliveryStatus.PENDING,
            # SQLAlchemy ``default`` 在 INSERT/flush 阶段才生效。这里显式赋值，确保
            # 业务服务和无数据库单元测试在创建后的第一次投递前拥有相同的整数语义。
            attempt_count=0,
        )
        try:
            persisted = await self.repository.save_with_event(booking, event, delivery)
        except ConcurrentBookingChangePersistenceError as error:
            raise InvalidCarrierBookingTransitionError from error
        # flush/commit 后 ORM 默认值 callback_id 已可读取。再加载可确保独立会话实现一致。
        persisted_delivery = await self.repository.get_delivery(delivery.id)
        if persisted_delivery is None:  # pragma: no cover - 数据库事务已保证，防御性分支
            raise RuntimeError("回调投递在提交后意外缺失")
        return CarrierDecisionResult(
            booking=persisted,
            delivery=persisted_delivery,
            decided_now=True,
        )


class CallbackDeliveryService:
    """负责把承运人终态可靠地回调给 LINK。

    回调尝试绝不改变承运人订舱终态，只更新 ``CallbackDelivery``。这样网络问题不可能
    把“已确认”重新变为“处理中”，人工操作员也能在门户中看到失败原因并手动重试。
    """

    def __init__(
        self,
        repository: CarrierBookingRepository,
        config: Settings = settings,
        http_client: AsyncHttpPoster | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.http_client = http_client

    @staticmethod
    def _now() -> datetime:
        """统一生成无时区数据库时间，匹配本项目现有 MySQL DateTime 配置。"""

        return datetime.now(UTC).replace(tzinfo=None)

    def _payload(self, delivery: CallbackDelivery) -> dict[str, str]:
        """构造与 LINK 锁定的回调合同；不夹带门户用户、Token 或原始文件。"""

        booking = delivery.booking
        payload = {
            "callback_id": delivery.callback_id,
            "carrier_request_id": booking.carrier_request_id,
            "idempotency_key": booking.idempotency_key,
            "application_number": booking.application_number,
            "status": booking.status.value,
            # 回调任务在最终决定提交后创建；created_at 就是这条构造终态事实的发生时间。
            "occurred_at": delivery.created_at.isoformat(),
        }
        if booking.booking_reference is not None:
            payload["booking_reference"] = booking.booking_reference
        if booking.rejection_reason_code is not None:
            payload["reason_code"] = booking.rejection_reason_code
        return payload

    async def _post_callback(self, delivery: CallbackDelivery) -> None:
        """发送 HTTP 请求，并只以 2xx 作为 LINK 已接收的成功信号。"""

        headers = {
            "X-Carrier-Callback-Token": self.config.carrier_callback_token.get_secret_value(),
            "Content-Type": "application/json",
        }
        if self.http_client is not None:
            response = await self.http_client.post(
                self.config.carrier_callback_url,
                json=self._payload(delivery),
                headers=headers,
            )
            response.raise_for_status()
            return

        async with httpx.AsyncClient(timeout=self.config.carrier_callback_timeout_seconds) as client:
            response = await client.post(
                self.config.carrier_callback_url,
                json=self._payload(delivery),
                headers=headers,
            )
            response.raise_for_status()

    def _record_failure(self, delivery: CallbackDelivery, error: Exception) -> None:
        """按指数退避安排下一次尝试，超过次数则标记为人工处理的 DEAD。"""

        # 不保存服务端响应体，以免未来对端错误页意外带入敏感内容。
        delivery.last_error = f"{type(error).__name__}: {str(error)[:420]}"
        if delivery.attempt_count >= self.config.carrier_callback_max_attempts:
            delivery.status = CallbackDeliveryStatus.DEAD
            delivery.next_attempt_at = None
            delivery.lease_until = None
            return

        delay = self.config.carrier_callback_retry_base_seconds * (
            2 ** (delivery.attempt_count - 1)
        )
        delivery.status = CallbackDeliveryStatus.RETRY_WAIT
        delivery.next_attempt_at = self._now() + timedelta(seconds=delay)
        delivery.lease_until = None

    async def deliver_new(self, delivery_id: int) -> CallbackDelivery:
        """立即投递新创建的 PENDING 任务。"""

        delivery = await self.repository.get_delivery(delivery_id)
        if delivery is None:
            raise CallbackDeliveryNotFoundError
        if delivery.status is not CallbackDeliveryStatus.PENDING:
            raise CallbackDeliveryNotRetryableError
        claimed = await self._attempt(
            delivery_id,
            {CallbackDeliveryStatus.PENDING},
        )
        if claimed is None:  # pragma: no cover - 领取条件被并发请求抢占时的保护
            raise CallbackDeliveryNotRetryableError
        return claimed

    async def retry_manually(self, delivery_id: int) -> CallbackDelivery:
        """门户人工重试一个失败或 DEAD 的任务。"""

        delivery = await self.repository.get_delivery(delivery_id)
        if delivery is None:
            raise CallbackDeliveryNotFoundError
        if delivery.status not in {
            CallbackDeliveryStatus.RETRY_WAIT,
            CallbackDeliveryStatus.DEAD,
        }:
            raise CallbackDeliveryNotRetryableError
        claimed = await self._attempt(
            delivery_id,
            {
                CallbackDeliveryStatus.RETRY_WAIT,
                CallbackDeliveryStatus.DEAD,
            },
            allow_attempt_beyond_max=True,
        )
        if claimed is None:  # pragma: no cover - 领取条件被并发请求抢占时的保护
            raise CallbackDeliveryNotRetryableError
        return claimed

    async def retry_due(self) -> list[CallbackDelivery]:
        """供未来调度器调用：投递所有已经到达退避时间的任务。"""

        due = await self.repository.list_due_deliveries(self._now())
        delivered: list[CallbackDelivery] = []
        for item in due:
            attempted = await self._attempt(
                item.id,
                {
                    CallbackDeliveryStatus.RETRY_WAIT,
                    CallbackDeliveryStatus.SENDING,
                },
                require_due=True,
            )
            if attempted is not None:
                delivered.append(attempted)
        return delivered

    async def _attempt(
        self,
        delivery_id: int,
        allowed_statuses: set[CallbackDeliveryStatus],
        require_due: bool = False,
        allow_attempt_beyond_max: bool = False,
    ) -> CallbackDelivery | None:
        """条件领取后实际尝试一次回调，并持久化成功或失败的结果。"""

        now = self._now()
        delivery = await self.repository.claim_delivery(
            delivery_id=delivery_id,
            allowed_statuses=allowed_statuses,
            now=now,
            lease_until=now + timedelta(seconds=self.config.carrier_callback_lease_seconds),
            require_due=require_due,
        )
        if delivery is None:
            return None
        # 已经耗尽次数、之后又因崩溃留下 SENDING 租约的任务不能无限发送。
        if (
            delivery.attempt_count > self.config.carrier_callback_max_attempts
            and not allow_attempt_beyond_max
        ):
            delivery.status = CallbackDeliveryStatus.DEAD
            delivery.lease_until = None
            delivery.next_attempt_at = None
            delivery.last_error = "MAX_ATTEMPTS_EXCEEDED"
            return await self.repository.commit_delivery(delivery)
        try:
            await self._post_callback(delivery)
        except httpx.HTTPError as error:
            self._record_failure(delivery, error)
        else:
            delivery.status = CallbackDeliveryStatus.DELIVERED
            delivery.delivered_at = self._now()
            delivery.next_attempt_at = None
            delivery.lease_until = None
            delivery.last_error = None
        return await self.repository.commit_delivery(delivery)
