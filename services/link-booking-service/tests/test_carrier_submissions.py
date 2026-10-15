"""第 22 章 LINK 侧承运人提交、重试和回调幂等测试。"""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.carrier_callbacks import get_carrier_callback_service
from app.core.config import Settings
from app.main import app
from app.models.booking_application import BookingApplication, BookingStatus
from app.models.carrier_submission import (
    CarrierBookingRequest,
    CarrierBookingRequestStatus,
    CarrierCallbackReceipt,
)
from app.schemas.carrier_submission import CarrierCallbackPayload
from app.services.carrier_submissions import (
    CarrierCallbackConflictError,
    CarrierSubmissionService,
)


class FakeHttpPoster:
    """按预设顺序返回响应或网络异常，并保留请求供断言。"""

    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self.outcomes = outcomes
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.requests.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class InMemoryCarrierSubmissionRepository:
    """覆盖服务使用的持久化语义，不连接本机 MySQL。"""

    def __init__(self, application: BookingApplication, request: CarrierBookingRequest) -> None:
        self.application = application
        self.request = request
        self.request.id = 1
        self.request.application_id = application.id
        self.callbacks: dict[str, CarrierCallbackReceipt] = {}
        self.events: list[object] = []

    async def get_request(self, request_id: int) -> CarrierBookingRequest | None:
        return self.request if request_id == self.request.id else None

    async def get_by_application_id(
        self, application_id: int
    ) -> CarrierBookingRequest | None:
        return self.request if application_id == self.application.id else None

    async def get_by_application_number(
        self, application_number: str
    ) -> CarrierBookingRequest | None:
        if application_number == self.application.application_number:
            return self.request
        return None

    async def get_callback(self, callback_id: str) -> CarrierCallbackReceipt | None:
        return self.callbacks.get(callback_id)

    async def claim_for_delivery(
        self,
        request_id: int,
        now: datetime,
        lease_until: datetime,
        *,
        manual: bool,
    ) -> CarrierBookingRequest | None:
        if request_id != self.request.id:
            return None
        allowed = (
            {
                CarrierBookingRequestStatus.RETRY_WAIT,
                CarrierBookingRequestStatus.DEAD,
            }
            if manual
            else {
                CarrierBookingRequestStatus.PENDING,
                CarrierBookingRequestStatus.RETRY_WAIT,
                CarrierBookingRequestStatus.SENDING,
            }
        )
        if self.request.status not in allowed:
            return None
        if (
            not manual
            and self.request.status is CarrierBookingRequestStatus.RETRY_WAIT
            and self.request.next_attempt_at is not None
            and self.request.next_attempt_at > now
        ):
            return None
        self.request.status = CarrierBookingRequestStatus.SENDING
        self.request.attempt_count += 1
        if manual:
            self.request.manual_retry_count += 1
        self.request.next_attempt_at = None
        self.request.lease_until = lease_until
        return self.request

    async def list_due_request_ids(self, now: datetime, limit: int = 50) -> list[int]:
        return [self.request.id]

    async def commit_request(self, request: CarrierBookingRequest) -> CarrierBookingRequest:
        return request

    async def finish_delivery(
        self,
        request_id: int,
        values: dict[str, object],
    ) -> CarrierBookingRequest:
        if self.request.status is CarrierBookingRequestStatus.SENDING:
            for field, value in values.items():
                setattr(self.request, field, value)
        return self.request

    async def save_callback(
        self,
        application: BookingApplication,
        event: object | None,
        request: CarrierBookingRequest,
        receipt: CarrierCallbackReceipt,
    ) -> None:
        receipt.id = len(self.callbacks) + 1
        receipt.request_id = request.id
        receipt.received_at = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
        self.callbacks[receipt.callback_id] = receipt
        if event is not None:
            self.events.append(event)


def make_application() -> BookingApplication:
    """创建只含构造字段的已提交申请。"""

    application = BookingApplication(
        id=1,
        application_number="DEMO-CARRIER-LINK-001",
        origin_port="DEMO-SHA",
        destination_port="DEMO-LAX",
        transport_mode="OCEAN",
        container_type="40HQ",
        status=BookingStatus.SUBMITTED,
    )
    application.created_at = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    application.updated_at = application.created_at
    return application


def make_service(
    outcomes: list[httpx.Response | Exception],
) -> tuple[
    CarrierSubmissionService,
    InMemoryCarrierSubmissionRepository,
    FakeHttpPoster,
]:
    """组装一套完全在内存中的 LINK 投递服务。"""

    application = make_application()
    request = CarrierSubmissionService.build_pending_request(application)
    repository = InMemoryCarrierSubmissionRepository(application, request)
    http_client = FakeHttpPoster(outcomes)
    config = Settings(
        carrier_simulator_url="http://carrier.test",
        carrier_callback_token="DEMO-CALLBACK-TOKEN",
        carrier_dispatch_retry_base_seconds=10,
        carrier_dispatch_max_attempts=3,
    )
    service = CarrierSubmissionService(repository, config, http_client)  # type: ignore[arg-type]
    return service, repository, http_client


@pytest.mark.asyncio
async def test_should_dispatch_with_idempotency_key_and_accept_carrier_request() -> None:
    """首次投递成功后保存承运人请求号，不生成旧 AUTO-LINK 参考号。"""

    response = httpx.Response(
        201,
        json={"carrier_request_id": "DEMO-CARRIER-REQ-001"},
        request=httpx.Request("POST", "http://carrier.test"),
    )
    service, repository, http_client = make_service([response])

    request = await service.dispatch_new(1)

    assert request is not None
    assert request.status is CarrierBookingRequestStatus.ACCEPTED
    assert request.carrier_request_id == "DEMO-CARRIER-REQ-001"
    assert request.attempt_count == 1
    assert request.can_cancel is False
    assert repository.application.booking_reference is None
    sent = http_client.requests[0]
    assert sent[0] == "http://carrier.test/api/v1/carrier/bookings"
    assert sent[1]["headers"] == {"Idempotency-Key": request.idempotency_key}


@pytest.mark.asyncio
async def test_should_backoff_then_allow_manual_retry() -> None:
    """网络失败进入 RETRY_WAIT，人工重试沿用幂等键并增加人工次数。"""

    success = httpx.Response(
        200,
        json={"carrier_request_id": "DEMO-CARRIER-REQ-002"},
        request=httpx.Request("POST", "http://carrier.test"),
    )
    service, repository, _ = make_service(
        [httpx.ConnectError("DEMO carrier unavailable"), success]
    )

    failed = await service.dispatch_new(1)
    assert failed is not None
    assert failed.status is CarrierBookingRequestStatus.RETRY_WAIT
    assert failed.next_attempt_at is not None
    assert failed.can_retry is True
    assert failed.can_cancel is True

    retried = await service.retry_manually(repository.application.application_number)
    assert retried.status is CarrierBookingRequestStatus.ACCEPTED
    assert retried.attempt_count == 2
    assert retried.manual_retry_count == 1


@pytest.mark.asyncio
async def test_should_process_callback_idempotently_and_protect_terminal_status() -> None:
    """同体回调重放返回 duplicate，不同体冲突，后到终态不会覆盖先到终态。"""

    service, repository, _ = make_service([])
    repository.request.status = CarrierBookingRequestStatus.ACCEPTED
    repository.request.carrier_request_id = "DEMO-CARRIER-REQ-003"
    processing = CarrierCallbackPayload(
        callback_id="DEMO-CALLBACK-000",
        carrier_request_id="DEMO-CARRIER-REQ-003",
        idempotency_key=repository.request.idempotency_key,
        application_number=repository.application.application_number,
        status="PROCESSING",
        occurred_at="2026-10-09T11:55:00",
    )
    processing_result = await service.receive_callback(
        "DEMO-CALLBACK-TOKEN",
        processing,
    )
    assert processing_result.ignored is False
    assert repository.application.status is BookingStatus.SUBMITTED
    assert repository.request.status is CarrierBookingRequestStatus.PROCESSING
    assert len(repository.events) == 1

    confirmed = CarrierCallbackPayload(
        callback_id="DEMO-CALLBACK-001",
        carrier_request_id="DEMO-CARRIER-REQ-003",
        idempotency_key=repository.request.idempotency_key,
        application_number=repository.application.application_number,
        status="CONFIRMED",
        booking_reference="DEMO-CARRIER-BOOKING-000001",
        occurred_at="2026-10-09T12:00:00",
    )

    first = await service.receive_callback("DEMO-CALLBACK-TOKEN", confirmed)
    duplicate = await service.receive_callback("DEMO-CALLBACK-TOKEN", confirmed)

    assert first.duplicate is False
    assert first.ignored is False
    assert duplicate.duplicate is True
    assert repository.application.status is BookingStatus.CONFIRMED
    assert repository.application.booking_reference == "DEMO-CARRIER-BOOKING-000001"
    assert len(repository.events) == 2

    rejected_late = CarrierCallbackPayload(
        callback_id="DEMO-CALLBACK-002",
        carrier_request_id="DEMO-CARRIER-REQ-003",
        idempotency_key=repository.request.idempotency_key,
        application_number=repository.application.application_number,
        status="REJECTED",
        reason_code="DEMO-NO-SPACE",
        occurred_at="2026-10-09T12:05:00",
    )
    late = await service.receive_callback("DEMO-CALLBACK-TOKEN", rejected_late)
    assert late.ignored is True
    assert repository.application.status is BookingStatus.CONFIRMED
    assert len(repository.events) == 2

    processing_late = processing.model_copy(
        update={
            "callback_id": "DEMO-CALLBACK-003",
            "occurred_at": datetime(2026, 10, 9, 12, 10, tzinfo=UTC),
        }
    )
    processing_late_result = await service.receive_callback(
        "DEMO-CALLBACK-TOKEN",
        processing_late,
    )
    assert processing_late_result.ignored is True
    assert repository.request.status is CarrierBookingRequestStatus.CONFIRMED

    conflicting = confirmed.model_copy(
        update={"booking_reference": "DEMO-CARRIER-BOOKING-DIFFERENT"}
    )
    with pytest.raises(CarrierCallbackConflictError):
        await service.receive_callback("DEMO-CALLBACK-TOKEN", conflicting)


def test_callback_http_contract_should_accept_processing_and_reject_bad_token() -> None:
    """HTTP 层固定使用最终字段名、/link 路径和回调 Token 头。"""

    service, repository, _ = make_service([])
    repository.request.status = CarrierBookingRequestStatus.ACCEPTED
    repository.request.carrier_request_id = "DEMO-CARRIER-REQ-HTTP"
    app.dependency_overrides[get_carrier_callback_service] = lambda: service
    payload = {
        "callback_id": "DEMO-CALLBACK-HTTP-001",
        "carrier_request_id": "DEMO-CARRIER-REQ-HTTP",
        "idempotency_key": repository.request.idempotency_key,
        "application_number": repository.application.application_number,
        "status": "PROCESSING",
        "occurred_at": "2026-10-09T12:00:00Z",
    }

    try:
        with TestClient(app) as client:
            unauthorized = client.post(
                "/api/v1/carrier-callbacks/link",
                json=payload,
                headers={"X-Carrier-Callback-Token": "DEMO-WRONG-TOKEN"},
            )
            accepted = client.post(
                "/api/v1/carrier-callbacks/link",
                json=payload,
                headers={"X-Carrier-Callback-Token": "DEMO-CALLBACK-TOKEN"},
            )
        assert unauthorized.status_code == 401
        assert accepted.status_code == 200
        assert accepted.json() == {
            "callback_id": "DEMO-CALLBACK-HTTP-001",
            "duplicate": False,
            "ignored": False,
            "application_status": "SUBMITTED",
        }
    finally:
        app.dependency_overrides.clear()
