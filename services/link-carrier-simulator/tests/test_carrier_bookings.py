"""模拟承运人核心闭环测试。

所有编号均为 ``DEMO-`` 构造数据；测试用内存仓储验证领域规则，不连接 MySQL 或真实
LINK 回调地址。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.carrier_bookings import get_booking_service, get_callback_delivery_service
from app.core.config import Settings
from app.main import app
from app.models.carrier_booking import (
    CallbackDelivery,
    CallbackDeliveryStatus,
    CarrierBooking,
    CarrierBookingEvent,
    CarrierBookingStatus,
)
from app.schemas.carrier_booking import (
    CarrierBookingCreate,
    CarrierBookingDecision,
    CarrierDecision,
)
from app.services.carrier_bookings import (
    CallbackDeliveryService,
    CarrierBookingService,
    DuplicateIdempotencyKeyError,
    InvalidCarrierBookingTransitionError,
)


class InMemoryCarrierBookingRepository:
    """与生产仓储同名方法的最小内存实现。"""

    def __init__(self) -> None:
        self.bookings: dict[int, CarrierBooking] = {}
        self.keys: dict[str, CarrierBooking] = {}
        self.deliveries: dict[int, CallbackDelivery] = {}
        self.next_booking_id = 1
        self.next_event_id = 1
        self.next_delivery_id = 1

    @staticmethod
    def _now() -> datetime:
        return datetime(2026, 10, 9, 9, 0, 0, tzinfo=UTC).replace(tzinfo=None)

    async def get(self, booking_id: int) -> CarrierBooking | None:
        return self.bookings.get(booking_id)

    async def get_by_idempotency_key(self, key: str) -> CarrierBooking | None:
        return self.keys.get(key)

    async def list_bookings(
        self,
        status: CarrierBookingStatus | None = None,
        keyword: str | None = None,
    ) -> list[CarrierBooking]:
        items = list(reversed(list(self.bookings.values())))
        if status is not None:
            items = [item for item in items if item.status is status]
        if keyword:
            items = [
                item
                for item in items
                if keyword in item.application_number or keyword in item.carrier_request_id
            ]
        return items

    async def get_detail(
        self, booking_id: int
    ) -> tuple[CarrierBooking, list[CarrierBookingEvent], list[CallbackDelivery]] | None:
        booking = self.bookings.get(booking_id)
        if booking is None:
            return None
        events = sorted(booking.events, key=lambda item: item.id, reverse=True)
        deliveries = sorted(booking.callback_deliveries, key=lambda item: item.id, reverse=True)
        return booking, events, deliveries

    async def add_with_event(
        self, booking: CarrierBooking, event: CarrierBookingEvent
    ) -> CarrierBooking:
        booking.id = self.next_booking_id
        self.next_booking_id += 1
        now = self._now()
        booking.created_at = now
        booking.updated_at = now
        event.id = self.next_event_id
        self.next_event_id += 1
        event.booking_id = booking.id
        event.occurred_at = now
        self.bookings[booking.id] = booking
        self.keys[booking.idempotency_key] = booking
        return booking

    async def save_with_event(
        self,
        booking: CarrierBooking,
        event: CarrierBookingEvent,
        delivery: CallbackDelivery | None = None,
    ) -> CarrierBooking:
        booking.updated_at = self._now()
        event.id = self.next_event_id
        self.next_event_id += 1
        event.booking_id = booking.id
        event.occurred_at = self._now()
        if delivery is not None:
            delivery.id = self.next_delivery_id
            self.next_delivery_id += 1
            delivery.booking_id = booking.id
            delivery.callback_id = str(uuid4())
            delivery.created_at = self._now()
            delivery.updated_at = self._now()
            self.deliveries[delivery.id] = delivery
        return booking

    async def get_delivery(self, delivery_id: int) -> CallbackDelivery | None:
        return self.deliveries.get(delivery_id)

    async def get_latest_delivery_for_booking(self, booking_id: int) -> CallbackDelivery | None:
        matches = [item for item in self.deliveries.values() if item.booking_id == booking_id]
        return max(matches, key=lambda item: item.id) if matches else None

    async def list_deliveries(self) -> list[CallbackDelivery]:
        return list(reversed(list(self.deliveries.values())))

    async def list_due_deliveries(self, now: datetime) -> list[CallbackDelivery]:
        return [
            item
            for item in self.deliveries.values()
            if (
                item.status is CallbackDeliveryStatus.RETRY_WAIT
                and item.next_attempt_at is not None
                and item.next_attempt_at <= now
            )
            or (
                item.status is CallbackDeliveryStatus.SENDING
                and item.lease_until is not None
                and item.lease_until <= now
            )
        ]

    async def claim_delivery(
        self,
        delivery_id: int,
        allowed_statuses: set[CallbackDeliveryStatus],
        now: datetime,
        lease_until: datetime,
        require_due: bool = False,
    ) -> CallbackDelivery | None:
        delivery = self.deliveries.get(delivery_id)
        if delivery is None or delivery.status not in allowed_statuses:
            return None
        if require_due and not (
            (
                delivery.status is CallbackDeliveryStatus.RETRY_WAIT
                and delivery.next_attempt_at is not None
                and delivery.next_attempt_at <= now
            )
            or (
                delivery.status is CallbackDeliveryStatus.SENDING
                and delivery.lease_until is not None
                and delivery.lease_until <= now
            )
        ):
            return None
        delivery.status = CallbackDeliveryStatus.SENDING
        delivery.attempt_count += 1
        delivery.lease_until = lease_until
        return delivery

    async def commit_delivery(self, delivery: CallbackDelivery) -> CallbackDelivery:
        delivery.updated_at = self._now()
        return delivery


class RecordingHttpClient:
    """可预设成功或失败的回调 HTTP Mock。"""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.requests.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def payload(number: str = "DEMO-BOOKING-2201") -> CarrierBookingCreate:
    """生成一份可在多个测试复用的构造申请。"""

    return CarrierBookingCreate(
        application_number=number,
        origin_port="DEMO-SHA",
        destination_port="DEMO-LAX",
        transport_mode="OCEAN",
        container_type="40HQ",
        requested_etd="2026-10-20",
    )


@pytest.mark.anyio
async def test_should_make_same_idempotency_key_a_safe_replay() -> None:
    repository = InMemoryCarrierBookingRepository()
    service = CarrierBookingService(repository)  # type: ignore[arg-type]

    first = await service.create("DEMO-IDEMPOTENCY-001", payload())
    replay = await service.create("DEMO-IDEMPOTENCY-001", payload())

    assert first.created is True
    assert replay.created is False
    assert first.booking.id == replay.booking.id
    assert first.booking.status is CarrierBookingStatus.RECEIVED
    assert first.booking.carrier_request_id.startswith("DEMO-CARRIER-REQ-")

    with pytest.raises(DuplicateIdempotencyKeyError):
        await service.create("DEMO-IDEMPOTENCY-001", payload("DEMO-BOOKING-DIFFERENT"))


@pytest.mark.anyio
async def test_should_create_callback_after_confirm_and_deliver_constructed_fact() -> None:
    repository = InMemoryCarrierBookingRepository()
    booking_service = CarrierBookingService(repository)  # type: ignore[arg-type]
    created = await booking_service.create("DEMO-IDEMPOTENCY-002", payload())
    processing, _ = await booking_service.start_processing(created.booking.id)
    decision = await booking_service.decide(
        processing.id,
        CarrierBookingDecision(decision=CarrierDecision.CONFIRMED),
    )
    booking = decision.booking
    delivery = decision.delivery
    http_client = RecordingHttpClient(
        [httpx.Response(204, request=httpx.Request("POST", "http://link.test"))]
    )
    delivery_service = CallbackDeliveryService(
        repository,  # type: ignore[arg-type]
        Settings(
            carrier_callback_url="http://link.test/api/v1/carrier-callbacks",
            carrier_callback_token="DEMO-CALLBACK-TOKEN",
        ),
        http_client,
    )

    delivered = await delivery_service.deliver_new(delivery.id)

    assert booking.status is CarrierBookingStatus.CONFIRMED
    assert booking.booking_reference == "DEMO-CARRIER-BOOKING-000001"
    assert delivered.status is CallbackDeliveryStatus.DELIVERED
    assert delivered.attempt_count == 1
    assert http_client.requests[0][0] == "http://link.test/api/v1/carrier-callbacks"
    request_kwargs = http_client.requests[0][1]
    assert request_kwargs["headers"] == {
        "X-Carrier-Callback-Token": "DEMO-CALLBACK-TOKEN",
        "Content-Type": "application/json",
    }
    assert request_kwargs["json"] == {
        "callback_id": delivery.callback_id,
        "carrier_request_id": booking.carrier_request_id,
        "idempotency_key": "DEMO-IDEMPOTENCY-002",
        "application_number": "DEMO-BOOKING-2201",
        "status": "CONFIRMED",
        "booking_reference": "DEMO-CARRIER-BOOKING-000001",
        "occurred_at": delivery.created_at.isoformat(),
    }


@pytest.mark.anyio
async def test_should_backoff_after_failure_and_allow_manual_retry() -> None:
    repository = InMemoryCarrierBookingRepository()
    booking_service = CarrierBookingService(repository)  # type: ignore[arg-type]
    created = await booking_service.create("DEMO-IDEMPOTENCY-003", payload())
    await booking_service.start_processing(created.booking.id)
    decision = await booking_service.decide(
        created.booking.id,
        CarrierBookingDecision(
            decision=CarrierDecision.REJECTED,
            rejection_reason_code="DEMO-NO-SPACE",
        ),
    )
    delivery = decision.delivery
    http_client = RecordingHttpClient(
        [
            httpx.ConnectError("DEMO callback unavailable"),
            httpx.Response(200, request=httpx.Request("POST", "http://link.test")),
        ]
    )
    delivery_service = CallbackDeliveryService(
        repository,  # type: ignore[arg-type]
        Settings(
            carrier_callback_url="http://link.test/api/v1/carrier-callbacks",
            carrier_callback_token="DEMO-CALLBACK-TOKEN",
            carrier_callback_retry_base_seconds=10,
            carrier_callback_max_attempts=3,
        ),
        http_client,
    )

    failed = await delivery_service.deliver_new(delivery.id)
    failed_status = failed.status
    failed_attempt_count = failed.attempt_count
    failed_next_attempt_at = failed.next_attempt_at
    failed_last_error = failed.last_error
    retried = await delivery_service.retry_manually(delivery.id)

    # 内存仓储返回的是同一个 ORM 对象；先保存失败时的快照断言，避免后续手动重试
    # 原地修改对象后掩盖了 RETRY_WAIT 的真实中间状态。
    assert failed_status is CallbackDeliveryStatus.RETRY_WAIT
    assert failed_attempt_count == 1
    assert failed_next_attempt_at is not None
    assert "ConnectError" in (failed_last_error or "")
    assert retried.status is CallbackDeliveryStatus.DELIVERED
    assert retried.attempt_count == 2
    assert retried.last_error is None


@pytest.mark.anyio
async def test_should_require_processing_before_final_decision() -> None:
    repository = InMemoryCarrierBookingRepository()
    service = CarrierBookingService(repository)  # type: ignore[arg-type]
    created = await service.create("DEMO-IDEMPOTENCY-004", payload())

    with pytest.raises(InvalidCarrierBookingTransitionError):
        await service.decide(
            created.booking.id,
            CarrierBookingDecision(decision=CarrierDecision.CONFIRMED),
        )


@pytest.mark.anyio
async def test_should_reclaim_expired_sending_lease_in_due_retry_worker() -> None:
    """进程在 SENDING 时异常后，到期租约必须允许下一轮 worker 回收投递。"""

    repository = InMemoryCarrierBookingRepository()
    booking_service = CarrierBookingService(repository)  # type: ignore[arg-type]
    created = await booking_service.create("DEMO-IDEMPOTENCY-LEASE-001", payload())
    _, processing_delivery = await booking_service.start_processing(created.booking.id)
    processing_delivery.status = CallbackDeliveryStatus.SENDING
    processing_delivery.attempt_count = 1
    processing_delivery.lease_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    callback_client = RecordingHttpClient(
        [httpx.Response(204, request=httpx.Request("POST", "http://link.test"))]
    )
    delivery_service = CallbackDeliveryService(
        repository,  # type: ignore[arg-type]
        Settings(
            carrier_callback_url="http://link.test/api/v1/carrier-callbacks/link",
            carrier_callback_token="DEMO-CALLBACK-TOKEN",
        ),
        callback_client,
    )

    retried = await delivery_service.retry_due()

    assert len(retried) == 1
    assert retried[0].status is CallbackDeliveryStatus.DELIVERED
    assert retried[0].attempt_count == 2
    assert retried[0].lease_until is None


@pytest.mark.anyio
async def test_should_allow_manual_retry_after_automatic_attempts_reach_dead() -> None:
    """DEAD 是停止自动轮询，不是禁止门户操作员明确发起的补偿重试。"""

    repository = InMemoryCarrierBookingRepository()
    booking_service = CarrierBookingService(repository)  # type: ignore[arg-type]
    created = await booking_service.create("DEMO-IDEMPOTENCY-DEAD-001", payload())
    _, delivery = await booking_service.start_processing(created.booking.id)
    delivery.status = CallbackDeliveryStatus.DEAD
    delivery.attempt_count = 3
    callback_client = RecordingHttpClient(
        [httpx.Response(204, request=httpx.Request("POST", "http://link.test"))]
    )
    delivery_service = CallbackDeliveryService(
        repository,  # type: ignore[arg-type]
        Settings(
            carrier_callback_url="http://link.test/api/v1/carrier-callbacks/link",
            carrier_callback_token="DEMO-CALLBACK-TOKEN",
            carrier_callback_max_attempts=3,
        ),
        callback_client,
    )

    delivered = await delivery_service.retry_manually(delivery.id)

    assert delivered.status is CallbackDeliveryStatus.DELIVERED
    assert delivered.attempt_count == 4


@pytest.mark.anyio
async def test_should_make_same_final_decision_idempotent_without_second_final_callback() -> None:
    repository = InMemoryCarrierBookingRepository()
    service = CarrierBookingService(repository)  # type: ignore[arg-type]
    created = await service.create("DEMO-IDEMPOTENCY-DECISION-001", payload())
    await service.start_processing(created.booking.id)
    first = await service.decide(
        created.booking.id,
        CarrierBookingDecision(decision=CarrierDecision.CONFIRMED),
    )
    replay = await service.decide(
        created.booking.id,
        CarrierBookingDecision(decision=CarrierDecision.CONFIRMED),
    )

    assert first.decided_now is True
    assert replay.decided_now is False
    assert replay.delivery.id == first.delivery.id
    # 一条 PROCESSING 回调加一条 CONFIRMED 回调，重放不能追加第三条。
    assert len(repository.deliveries) == 2
    with pytest.raises(InvalidCarrierBookingTransitionError):
        await service.decide(
            created.booking.id,
            CarrierBookingDecision(
                decision=CarrierDecision.REJECTED,
                rejection_reason_code="DEMO-NO-SPACE",
            ),
        )


def test_should_complete_http_contract_with_idempotency_and_portal_actions() -> None:
    """路由层必须保留平台提交与独立门户两套 API 的合同。"""

    repository = InMemoryCarrierBookingRepository()
    callback_client = RecordingHttpClient(
        [
            httpx.Response(204, request=httpx.Request("POST", "http://link.test")),
            httpx.Response(204, request=httpx.Request("POST", "http://link.test")),
        ]
    )
    callback_settings = Settings(
        carrier_callback_url="http://link.test/api/v1/carrier-callbacks/link",
        carrier_callback_token="DEMO-CALLBACK-TOKEN",
    )
    app.dependency_overrides[get_booking_service] = lambda: CarrierBookingService(  # type: ignore[arg-type]
        repository
    )
    app.dependency_overrides[get_callback_delivery_service] = lambda: CallbackDeliveryService(  # type: ignore[arg-type]
        repository,
        callback_settings,
        callback_client,
    )
    try:
        with TestClient(app) as client:
            request_body = {
                "application_number": "DEMO-BOOKING-HTTP-001",
                "origin_port": "DEMO-SHA",
                "destination_port": "DEMO-LAX",
                "transport_mode": "OCEAN",
                "container_type": "40HQ",
                "requested_etd": "2026-10-20",
            }
            created = client.post(
                "/api/v1/carrier/bookings",
                json=request_body,
                headers={"Idempotency-Key": "DEMO-IDEMPOTENCY-HTTP-001"},
            )
            replay = client.post(
                "/api/v1/carrier/bookings",
                json=request_body,
                headers={"Idempotency-Key": "DEMO-IDEMPOTENCY-HTTP-001"},
            )

            assert created.status_code == 201
            assert replay.status_code == 200
            booking_id = created.json()["id"]
            assert created.json()["carrier_request_id"].startswith("DEMO-CARRIER-REQ-")
            # 先注册静态路径，避免被 /{booking_id} 的动态路由误解析为整数。
            idempotency_lookup = client.get(
                "/api/v1/carrier/bookings/by-idempotency-key/DEMO-IDEMPOTENCY-HTTP-001"
            )
            assert idempotency_lookup.status_code == 200
            assert idempotency_lookup.json()["id"] == booking_id
            assert client.post(f"/api/v1/portal/bookings/{booking_id}/process").status_code == 200
            decision = client.post(
                f"/api/v1/portal/bookings/{booking_id}/decision",
                json={"decision": "CONFIRMED"},
            )

            assert decision.status_code == 200
            assert decision.json()["status"] == "CONFIRMED"
            deliveries = client.get("/api/v1/portal/callback-deliveries")
            assert deliveries.status_code == 200
            assert deliveries.json()[0]["status"] == "DELIVERED"
            assert deliveries.json()[0]["callback_id"]
    finally:
        app.dependency_overrides.clear()
