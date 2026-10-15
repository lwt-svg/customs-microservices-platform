"""订舱申请 API 测试。

测试仓储完全在内存中运行，只使用 ``DEMO-`` 开头的构造编号，既不连接 MySQL，
也不读取本地或真实 LINK 文件。数据库迁移在单独的命令中验证。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.booking_applications import get_booking_service
from app.main import app
from app.models.booking_application import (
    BookingApplication,
    BookingApplicationEvent,
    BookingStatus,
)
from app.models.carrier_submission import CarrierBookingRequest, CarrierBookingRequestStatus
from app.repositories.booking_applications import (
    ConcurrentStatusChangePersistenceError,
    DuplicateApplicationNumberPersistenceError,
)
from app.schemas.booking_application import BookingApplicationCreate
from app.services.booking_applications import (
    BookingApplicationService,
    InvalidStatusTransitionError,
)


class InMemoryBookingApplicationRepository:
    """用于 HTTP 测试的最小异步假仓储，替代真实数据库连接。"""

    def __init__(self) -> None:
        self.items: dict[str, BookingApplication] = {}
        self.events: dict[int, list[BookingApplicationEvent]] = {}
        self.next_id = 1
        self.next_event_id = 1
        self.next_carrier_request_id = 1
        self.carrier_requests: dict[int, CarrierBookingRequest] = {}

    async def get_by_number(self, application_number: str) -> BookingApplication | None:
        """按构造申请编号获取对象。"""

        return self.items.get(application_number)

    async def list_all(self) -> list[BookingApplication]:
        """模拟生产仓储的倒序列表行为。"""

        return list(reversed(list(self.items.values())))

    async def list_events(self, application_id: int) -> list[BookingApplicationEvent]:
        """返回与生产仓储相同的倒序事件列表。"""

        return list(reversed(self.events.get(application_id, [])))

    async def add_with_event(
        self, application: BookingApplication, event: BookingApplicationEvent
    ) -> BookingApplication:
        """模拟“申请和首条事件同一事务提交”。"""

        application.id = self.next_id
        self.next_id += 1
        now = datetime(2026, 8, 25, 9, 0, 0, tzinfo=UTC)
        application.created_at = now
        application.updated_at = now
        self.items[application.application_number] = application
        self._append_event(application, event, now)
        return application

    async def save_with_event(
        self, application: BookingApplication, event: BookingApplicationEvent
    ) -> BookingApplication:
        """模拟“状态更新和事件同一事务提交”。"""

        now = datetime(2026, 8, 25, 9, 1, 0, tzinfo=UTC)
        application.updated_at = now
        self._append_event(application, event, now)
        return application

    async def save_submission_with_event(
        self,
        application: BookingApplication,
        event: BookingApplicationEvent,
        carrier_request: CarrierBookingRequest,
    ) -> BookingApplication:
        """模拟 SUBMITTED、事件和 PENDING 任务同事务提交。"""

        carrier_request.id = self.next_carrier_request_id
        self.next_carrier_request_id += 1
        carrier_request.application_id = application.id
        self.carrier_requests[application.id] = carrier_request
        return await self.save_with_event(application, event)

    async def save_cancellation_with_event(
        self,
        application: BookingApplication,
        event: BookingApplicationEvent,
        carrier_request: CarrierBookingRequest | None,
    ) -> BookingApplication:
        """模拟取消申请和停止投递任务同事务提交。"""

        return await self.save_with_event(application, event)

    def _append_event(
        self,
        application: BookingApplication,
        event: BookingApplicationEvent,
        occurred_at: datetime,
    ) -> None:
        """补齐数据库会生成的事件主键、外键和时间。"""

        event.id = self.next_event_id
        self.next_event_id += 1
        event.application_id = application.id
        event.occurred_at = occurred_at
        self.events.setdefault(application.id, []).append(event)


class ConcurrentDuplicateRepository(InMemoryBookingApplicationRepository):
    """模拟两个请求同时通过前置查询、最终被数据库唯一约束拦住的场景。"""

    async def get_by_number(self, application_number: str) -> BookingApplication | None:
        """并发窗口中，两个请求都可能暂时查不到对方尚未提交的数据。"""

        return None

    async def add_with_event(
        self, application: BookingApplication, event: BookingApplicationEvent
    ) -> BookingApplication:
        """模拟 Repository 捕获数据库 IntegrityError 后抛出的持久化冲突。"""

        raise DuplicateApplicationNumberPersistenceError


class ConcurrentStatusChangeRepository(InMemoryBookingApplicationRepository):
    """模拟另一个请求已经先更新同一申请，当前请求拿着过期 version 提交的场景。"""

    async def save_with_event(
        self, application: BookingApplication, event: BookingApplicationEvent
    ) -> BookingApplication:
        """生产仓储会由 SQLAlchemy 乐观锁抛出此异常。"""

        raise ConcurrentStatusChangePersistenceError


class FakeAcceptedCarrierService:
    """模拟任务已经被承运人接受，用于验证平台不能本地取消。"""

    def __init__(self, repository: InMemoryBookingApplicationRepository) -> None:
        self.repository = repository
        self.dispatch_count = 0

    async def dispatch_new(self, request_id: int) -> CarrierBookingRequest | None:
        self.dispatch_count += 1
        request = next(
            (
                item
                for item in self.repository.carrier_requests.values()
                if item.id == request_id
            ),
            None,
        )
        if request is not None:
            request.status = CarrierBookingRequestStatus.ACCEPTED
        return request

    async def get_for_application(
        self, application_id: int
    ) -> CarrierBookingRequest | None:
        return self.repository.carrier_requests.get(application_id)

    async def get_cancellable_for_update(
        self, application_id: int
    ) -> CarrierBookingRequest | None:
        request = self.repository.carrier_requests.get(application_id)
        return request if request is not None and request.can_cancel else None


@pytest.fixture
def client() -> TestClient:
    """让每个测试拥有独立内存仓储，避免测试之间共享状态。"""

    repository = InMemoryBookingApplicationRepository()
    app.dependency_overrides[get_booking_service] = lambda: BookingApplicationService(repository)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_should_create_and_query_synthetic_booking_application(client: TestClient) -> None:
    """创建构造申请后可由列表和单项 API 查询到，并且初始为草稿。"""

    payload = {
        "application_number": "DEMO-BOOKING-001",
        "origin_port": "DEMO-ORIGIN",
        "destination_port": "DEMO-DESTINATION",
        "container_type": "40HQ",
        "requested_etd": "2026-09-01",
    }
    created = client.post("/api/v1/booking-applications", json=payload)

    assert created.status_code == 201
    assert created.json()["status"] == "DRAFT"
    assert client.get("/api/v1/booking-applications").json()[0]["application_number"] == "DEMO-BOOKING-001"
    assert client.get("/api/v1/booking-applications/DEMO-BOOKING-001").status_code == 200


def test_should_enforce_minimum_booking_status_machine(client: TestClient) -> None:
    """浏览器只能提交或取消，不能再同步伪造承运人确认。"""

    client.post(
        "/api/v1/booking-applications",
        json={
            "application_number": "DEMO-BOOKING-002",
            "origin_port": "DEMO-ORIGIN",
            "destination_port": "DEMO-DESTINATION",
            "container_type": "20GP",
        },
    )

    submitted = client.post(
        "/api/v1/booking-applications/DEMO-BOOKING-002/status",
        json={"target_status": "SUBMITTED"},
    )
    confirmed = client.post(
        "/api/v1/booking-applications/DEMO-BOOKING-002/status",
        json={"target_status": "CONFIRMED"},
    )
    cancelled = client.post(
        "/api/v1/booking-applications/DEMO-BOOKING-002/status",
        json={"target_status": "CANCELLED"},
    )

    assert submitted.json()["status"] == "SUBMITTED"
    assert confirmed.status_code == 409
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["booking_reference"] is None


def test_should_allow_submitted_application_to_cancel(client: TestClient) -> None:
    """已提交申请可取消，但取消后成为终态，不会生成自动订舱参考号。"""

    client.post(
        "/api/v1/booking-applications",
        json={
            "application_number": "DEMO-BOOKING-004",
            "origin_port": "DEMO-ORIGIN",
            "destination_port": "DEMO-DESTINATION",
            "container_type": "40HQ",
        },
    )
    assert client.post(
        "/api/v1/booking-applications/DEMO-BOOKING-004/status",
        json={"target_status": "SUBMITTED"},
    ).status_code == 200

    cancelled = client.post(
        "/api/v1/booking-applications/DEMO-BOOKING-004/status",
        json={"target_status": "CANCELLED"},
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["booking_reference"] is None


def test_should_return_not_found_when_changing_unknown_application(client: TestClient) -> None:
    """状态操作不能隐式创建申请；未知编号必须明确返回 404。"""

    response = client.post(
        "/api/v1/booking-applications/DEMO-MISSING-001/status",
        json={"target_status": "SUBMITTED"},
    )

    assert response.status_code == 404


def test_should_return_current_snapshot_and_audit_events(client: TestClient) -> None:
    """详情把“当前是什么”和“经历过什么”分开返回，且事件按最新在前排列。"""

    client.post(
        "/api/v1/booking-applications",
        json={
            "application_number": "DEMO-DETAIL-001",
            "origin_port": "DEMO-ORIGIN",
            "destination_port": "DEMO-DESTINATION",
            "container_type": "40HQ",
        },
    )
    client.post(
        "/api/v1/booking-applications/DEMO-DETAIL-001/status",
        json={"target_status": "SUBMITTED"},
    )

    response = client.get("/api/v1/booking-applications/DEMO-DETAIL-001/detail")

    assert response.status_code == 200
    body = response.json()
    assert body["application"]["status"] == "SUBMITTED"
    assert [event["event_type"] for event in body["events"]] == [
        "SUBMITTED",
        "DRAFT_CREATED",
    ]
    assert body["events"][0]["previous_status"] == "DRAFT"
    assert body["events"][0]["current_status"] == "SUBMITTED"
    assert body["events"][0]["actor_label"] == "SYSTEM_DEMO"


def test_should_keep_concurrent_status_change_as_conflict() -> None:
    """乐观锁冲突需要稳定返回 409，让页面刷新而不是显示操作成功。"""

    repository = ConcurrentStatusChangeRepository()
    app.dependency_overrides[get_booking_service] = lambda: BookingApplicationService(repository)  # type: ignore[arg-type]

    try:
        with TestClient(app) as test_client:
            created = test_client.post(
                "/api/v1/booking-applications",
                json={
                    "application_number": "DEMO-CONCURRENT-STATUS-001",
                    "origin_port": "DEMO-ORIGIN",
                    "destination_port": "DEMO-DESTINATION",
                    "container_type": "20GP",
                },
            )
            response = test_client.post(
                "/api/v1/booking-applications/DEMO-CONCURRENT-STATUS-001/status",
                json={"target_status": "SUBMITTED"},
            )

        assert created.status_code == 201
        assert response.status_code == 409
        assert response.json()["detail"] == "订舱申请状态已被其他操作更新，请刷新后重试"
    finally:
        app.dependency_overrides.clear()


def test_should_reject_duplicate_synthetic_application_number(client: TestClient) -> None:
    """重复构造编号必须返回冲突，防止调用方误创建两份相同申请。"""

    payload = {
        "application_number": "DEMO-BOOKING-003",
        "origin_port": "DEMO-ORIGIN",
        "destination_port": "DEMO-DESTINATION",
        "container_type": "40HQ",
    }
    assert client.post("/api/v1/booking-applications", json=payload).status_code == 201
    assert client.post("/api/v1/booking-applications", json=payload).status_code == 409


def test_should_translate_concurrent_database_duplicate_to_conflict() -> None:
    """即使前置查询没有发现重复，数据库唯一约束冲突也必须稳定返回 409。"""

    repository = ConcurrentDuplicateRepository()
    app.dependency_overrides[get_booking_service] = lambda: BookingApplicationService(repository)  # type: ignore[arg-type]

    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/v1/booking-applications",
                json={
                    "application_number": "DEMO-CONCURRENT-001",
                    "origin_port": "DEMO-ORIGIN",
                    "destination_port": "DEMO-DESTINATION",
                    "container_type": "40HQ",
                },
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "申请编号已存在"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_should_reject_cancellation_after_carrier_acceptance() -> None:
    """承运人已受理后，LINK 不能只改本地状态伪装成取消成功。"""

    repository = InMemoryBookingApplicationRepository()
    carrier_service = FakeAcceptedCarrierService(repository)
    service = BookingApplicationService(repository, carrier_service)  # type: ignore[arg-type]
    application = await service.create(
        BookingApplicationCreate(
            application_number="DEMO-CANCEL-GUARD-001",
            origin_port="DEMO-SHA",
            destination_port="DEMO-LAX",
            container_type="40HQ",
        )
    )
    await service.change_status(application, BookingStatus.SUBMITTED)

    with pytest.raises(InvalidStatusTransitionError):
        await service.change_status(application, BookingStatus.CANCELLED)


@pytest.mark.asyncio
async def test_core_mode_should_save_pending_without_contacting_carrier() -> None:
    """core 模式 VALIDATE 后只持久化 PENDING，不要求 8100 必须启动。"""

    repository = InMemoryBookingApplicationRepository()
    carrier_service = FakeAcceptedCarrierService(repository)
    service = BookingApplicationService(
        repository,
        carrier_service,  # type: ignore[arg-type]
        dispatch_on_submit=False,
    )
    application = await service.create(
        BookingApplicationCreate(
            application_number="DEMO-CORE-WIZARD-001",
            origin_port="DEMO-SHA",
            destination_port="DEMO-LAX",
            container_type="40HQ",
        )
    )

    submitted = await service.change_status(application, BookingStatus.SUBMITTED)

    assert submitted.status is BookingStatus.SUBMITTED
    assert carrier_service.dispatch_count == 0
    assert repository.carrier_requests[application.id].status is CarrierBookingRequestStatus.PENDING
