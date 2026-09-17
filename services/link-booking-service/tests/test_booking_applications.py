"""订舱申请 API 测试。

测试仓储完全在内存中运行，只使用 ``DEMO-`` 开头的构造编号，既不连接 MySQL，
也不读取本地或真实 LINK 文件。数据库迁移在单独的命令中验证。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.booking_applications import get_booking_service
from app.main import app
from app.models.booking_application import BookingApplication
from app.services.booking_applications import BookingApplicationService


class InMemoryBookingApplicationRepository:
    """用于 HTTP 测试的最小异步假仓储，替代真实数据库连接。"""

    def __init__(self) -> None:
        self.items: dict[str, BookingApplication] = {}
        self.next_id = 1

    async def get_by_number(self, application_number: str) -> BookingApplication | None:
        """按构造申请编号获取对象。"""

        return self.items.get(application_number)

    async def list_all(self) -> list[BookingApplication]:
        """模拟生产仓储的倒序列表行为。"""

        return list(reversed(list(self.items.values())))

    async def add(self, application: BookingApplication) -> BookingApplication:
        """为 ORM 对象补齐测试所需的数据库生成字段后保存。"""

        application.id = self.next_id
        self.next_id += 1
        now = datetime(2026, 8, 25, 9, 0, 0, tzinfo=UTC)
        application.created_at = now
        application.updated_at = now
        self.items[application.application_number] = application
        return application

    async def save(self, application: BookingApplication) -> BookingApplication:
        """内存对象已原地更新，仅模拟更新时间戳。"""

        application.updated_at = datetime(2026, 8, 25, 9, 1, 0, tzinfo=UTC)
        return application


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
    """只允许草稿提交、提交确认或取消，确认后不能再取消。"""

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
    rejected = client.post(
        "/api/v1/booking-applications/DEMO-BOOKING-002/status",
        json={"target_status": "CANCELLED"},
    )

    assert submitted.json()["status"] == "SUBMITTED"
    assert confirmed.json()["status"] == "CONFIRMED"
    assert rejected.status_code == 409


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
