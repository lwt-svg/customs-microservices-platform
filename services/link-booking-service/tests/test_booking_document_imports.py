"""PDF 上传、候选复核与草稿创建 API 测试。

测试只生成内存中的构造 PDF，并使用临时目录和内存仓储，不连接 MySQL 或真实文件。
"""

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from reportlab.pdfgen import canvas

from app.api.booking_document_imports import get_document_import_service
from app.main import app
from app.models.booking_application import BookingApplication, BookingApplicationEvent
from app.models.booking_document_import import (
    BookingDocumentImport,
    BookingDocumentImportStatus,
)
from app.schemas.booking_application import BookingApplicationCreate
from app.schemas.booking_document_import import BookingDocumentCandidates
from app.services.booking_document_imports import BookingDocumentImportService
from app.services.pdf_booking_parser import PdfBookingCandidateParser
from app.storage.booking_documents import LocalBookingDocumentStorage


class InMemoryBookingDocumentImportRepository:
    """模拟导入记录及确认事务。"""

    def __init__(self) -> None:
        self.documents: dict[int, BookingDocumentImport] = {}
        self.applications: dict[str, BookingApplication] = {}
        self.events: list[BookingApplicationEvent] = []
        self.next_document_id = 1
        self.next_application_id = 1
        self.next_event_id = 1

    async def add(self, document: BookingDocumentImport) -> BookingDocumentImport:
        """补齐数据库生成字段并保存。"""

        now = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
        document.id = self.next_document_id
        self.next_document_id += 1
        document.created_at = now
        document.updated_at = now
        document.version = 1
        self.documents[document.id] = document
        return document

    async def get(self, document_id: int) -> BookingDocumentImport | None:
        """按主键读取导入记录。"""

        return self.documents.get(document_id)

    async def get_application_by_number(
        self, application_number: str
    ) -> BookingApplication | None:
        """按申请编号读取已创建草稿。"""

        return self.applications.get(application_number)

    async def confirm_with_application(
        self,
        document: BookingDocumentImport,
        application: BookingApplication,
        event: BookingApplicationEvent,
    ) -> BookingApplication:
        """模拟一次事务提交三个对象。"""

        now = datetime(2026, 10, 9, 10, 5, tzinfo=UTC)
        application.id = self.next_application_id
        self.next_application_id += 1
        application.created_at = now
        application.updated_at = now
        application.version = 1
        event.id = self.next_event_id
        self.next_event_id += 1
        event.application_id = application.id
        event.occurred_at = now
        application.source_document_import_id = document.id
        document.application = application
        document.updated_at = now
        document.version += 1
        self.applications[application.application_number] = application
        self.events.append(event)
        return application


def build_pdf(lines: list[str]) -> bytes:
    """用构造文本生成最小 PDF。"""

    output = BytesIO()
    document = canvas.Canvas(output)
    y = 800
    for line in lines:
        document.drawString(72, y, line)
        y -= 24
    document.save()
    return output.getvalue()


@pytest.fixture
def document_test_context(
    tmp_path: Path,
) -> tuple[TestClient, InMemoryBookingDocumentImportRepository, Path]:
    """给每个测试准备独立仓储和临时上传目录。"""

    repository = InMemoryBookingDocumentImportRepository()
    upload_dir = tmp_path / "uploads"
    service = BookingDocumentImportService(
        repository,  # type: ignore[arg-type]
        LocalBookingDocumentStorage(upload_dir, 5 * 1024 * 1024),
        PdfBookingCandidateParser(),
    )
    app.dependency_overrides[get_document_import_service] = lambda: service
    with TestClient(app) as client:
        yield client, repository, upload_dir
    app.dependency_overrides.clear()


def test_should_upload_pdf_without_creating_application(
    document_test_context: tuple[
        TestClient,
        InMemoryBookingDocumentImportRepository,
        Path,
    ],
) -> None:
    """上传只产生候选，不得绕过人工复核创建草稿。"""

    client, repository, upload_dir = document_test_context
    pdf = build_pdf(
        [
            "Application Number: DEMO-PDF-001",
            "Origin Port: DEMO-SHA",
            "Destination Port: DEMO-LAX",
            "Transport Mode: SEA",
            "Container Type: 40HQ",
            "Requested ETD: 2026-10-20",
        ]
    )

    response = client.post(
        "/api/v1/booking-document-imports",
        files={"file": ("demo-booking.pdf", pdf, "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PARSED"
    assert body["candidates"]["application_number"]["value"] == "DEMO-PDF-001"
    assert body["candidates"]["origin_port"]["evidence"] == "Origin Port: DEMO-SHA"
    assert body["candidates"]["requested_etd"]["confidence"] == "HIGH"
    assert body["created_application_number"] is None
    assert repository.applications == {}
    assert len(list(upload_dir.glob("*.pdf"))) == 1


def test_should_parse_packing_list_details_and_reconcile_totals(
    document_test_context: tuple[
        TestClient,
        InMemoryBookingDocumentImportRepository,
        Path,
    ],
) -> None:
    """RULES_V2 识别表头、明细、包组、PO 分配及 Total all 对账。"""

    client, _, _ = document_test_context
    pdf = build_pdf(
        [
            "SHIPPER: DEMO EXPORTER CO., LTD.",
            "CONSIGNEE: DEMO IMPORTER",
            "NOTIFY: DEMO BROKER",
            "Date: 2026-10-08",
            "Invoice Number:",
            "CIQ No: DEMO-CIQ-8",
            "MARKS: DEMO-BRAND",
            "PALLET: 2",
            "CBM: 3.75",
            (
                "SKU-A LOT-A 4500000001 DEMO PRODUCT ALPHA "
                "PCS 100 PCS 10 20.00 25.00"
            ),
            "100 1 10 20.00 35.00 1.50",
            (
                "SKU-B LOT-B 4500000002 DEMO PRODUCT BETA "
                "PCS 200 PCS 20 40.00 50.00"
            ),
            "200 1 20 40.00 60.00 2.25",
            "300 2 30 60.00 95.00",
            "2026-888",
            "Packing List",
        ]
    )

    response = client.post(
        "/api/v1/booking-document-imports",
        files={"file": ("demo-packing-list.pdf", pdf, "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["parser_version"] == "RULES_V2"
    assert body["candidates"]["application_number"] == {
        "value": "DEMO-PL-2026-888",
        "evidence": (
            "由 PDF Invoice Number 2026-888 派生，仅用于本地 DEMO 申请编号"
        ),
        "confidence": "MEDIUM",
    }
    details = body["candidates"]["booking_details"]
    assert details["invoice_number"] == "2026-888"
    assert details["document_date"] == "2026-10-08"
    assert details["ciq_number"] == "DEMO-CIQ-8"
    assert details["shipper"] == "DEMO EXPORTER CO., LTD."
    assert details["consignee"] == "DEMO IMPORTER"
    assert details["notify_party"] == "DEMO BROKER"
    assert details["marks"] == "DEMO-BRAND"
    assert details["total_pallets"] == 2
    assert details["total_cartons"] == 30
    assert details["total_quantity"] == 300
    assert details["total_cbm"] == 3.75
    assert details["total_net_weight_kg"] == 60.0
    assert details["total_gross_weight_kg"] == 95.0
    assert len(details["items"]) == 2
    assert len(details["packages"]) == 2
    assert details["packages"][0]["po_allocations"] == [
        {"po_number": "4500000001", "quantity": 100}
    ]
    assert all(check.endswith("✓") for check in details["checks"])


def test_should_read_rules_v1_candidate_payload_without_booking_details() -> None:
    """数据库中的旧 candidate_payload 缺少新键时仍可读取。"""

    candidates = BookingDocumentCandidates.model_validate(
        {
            "application_number": {},
            "origin_port": {},
            "destination_port": {},
            "transport_mode": {},
            "container_type": {},
            "requested_etd": {},
        }
    )

    assert candidates.booking_details is None


def test_should_create_draft_from_human_confirmed_values(
    document_test_context: tuple[
        TestClient,
        InMemoryBookingDocumentImportRepository,
        Path,
    ],
) -> None:
    """确认接口采用人工修改值，并原子生成首条 DRAFT_CREATED 事件。"""

    client, repository, _ = document_test_context
    uploaded = client.post(
        "/api/v1/booking-document-imports",
        files={
            "file": (
                "demo-booking.pdf",
                build_pdf(["Application Number: DEMO-PDF-CANDIDATE"]),
                "application/pdf",
            )
        },
    ).json()

    response = client.post(
        f"/api/v1/booking-document-imports/{uploaded['id']}/confirm",
        json={
            "application_number": "DEMO-PDF-REVIEWED",
            "origin_port": "DEMO-NGB",
            "destination_port": "DEMO-LGB",
            "transport_mode": "SEA",
            "container_type": "20GP",
            "requested_etd": "2026-11-01",
            "booking_details": {
                "invoice_number": "DEMO-INVOICE-1",
                "document_date": "2026-10-08",
                "total_pallets": 2,
                "total_cartons": 30,
                "total_quantity": 300,
                "items": [],
                "packages": [],
                "checks": ["DEMO 人工确认"],
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["application_number"] == "DEMO-PDF-REVIEWED"
    assert body["origin_port"] == "DEMO-NGB"
    assert body["transport_mode"] == "SEA"
    assert body["booking_details"]["invoice_number"] == "DEMO-INVOICE-1"
    assert body["booking_details"]["document_date"] == "2026-10-08"
    assert body["status"] == "DRAFT"
    assert repository.applications["DEMO-PDF-REVIEWED"].booking_details == {
        "invoice_number": "DEMO-INVOICE-1",
        "document_date": "2026-10-08",
        "ciq_number": None,
        "shipper": None,
        "consignee": None,
        "notify_party": None,
        "marks": None,
        "total_pallets": 2,
        "total_cartons": 30,
        "total_quantity": 300,
        "total_cbm": None,
        "total_net_weight_kg": None,
        "total_gross_weight_kg": None,
        "items": [],
        "packages": [],
        "checks": ["DEMO 人工确认"],
    }
    assert repository.events[0].event_type == "DRAFT_CREATED"
    assert (
        repository.documents[uploaded["id"]].status
        is BookingDocumentImportStatus.CONFIRMED
    )

    refreshed = client.get(f"/api/v1/booking-document-imports/{uploaded['id']}")
    assert refreshed.json()["created_application_number"] == "DEMO-PDF-REVIEWED"


def test_should_reject_second_confirmation(
    document_test_context: tuple[
        TestClient,
        InMemoryBookingDocumentImportRepository,
        Path,
    ],
) -> None:
    """同一 PDF 只能产生一份草稿。"""

    client, _, _ = document_test_context
    uploaded = client.post(
        "/api/v1/booking-document-imports",
        files={
            "file": (
                "demo.pdf",
                build_pdf(["Application Number: DEMO-ONCE-001"]),
                "application/pdf",
            )
        },
    ).json()
    payload = {
        "application_number": "DEMO-ONCE-001",
        "origin_port": "DEMO-SHA",
        "destination_port": "DEMO-LAX",
        "transport_mode": "SEA",
        "container_type": "40HQ",
    }

    assert client.post(
        f"/api/v1/booking-document-imports/{uploaded['id']}/confirm",
        json=payload,
    ).status_code == 201
    second = client.post(
        f"/api/v1/booking-document-imports/{uploaded['id']}/confirm",
        json=payload,
    )

    assert second.status_code == 409
    assert second.json()["detail"] == "该 PDF 已经创建过订舱草稿"


def test_should_reject_non_demo_number_at_python_boundary(
    document_test_context: tuple[
        TestClient,
        InMemoryBookingDocumentImportRepository,
        Path,
    ],
) -> None:
    """即使绕过 Java，Python 也必须拒绝真实风格编号。"""

    client, _, _ = document_test_context
    uploaded = client.post(
        "/api/v1/booking-document-imports",
        files={"file": ("demo.pdf", build_pdf(["Origin Port: DEMO-SHA"]), "application/pdf")},
    ).json()

    response = client.post(
        f"/api/v1/booking-document-imports/{uploaded['id']}/confirm",
        json={
            "application_number": "REAL-CUSTOMER-001",
            "origin_port": "SHA",
            "destination_port": "LAX",
            "transport_mode": "SEA",
            "container_type": "40HQ",
        },
    )

    assert response.status_code == 422


def test_should_reject_invalid_or_textless_pdf_and_remove_file(
    document_test_context: tuple[
        TestClient,
        InMemoryBookingDocumentImportRepository,
        Path,
    ],
) -> None:
    """无效文件和无文本 PDF 都不能留下可复核记录或磁盘文件。"""

    client, repository, upload_dir = document_test_context

    invalid = client.post(
        "/api/v1/booking-document-imports",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    textless = client.post(
        "/api/v1/booking-document-imports",
        files={"file": ("blank.pdf", build_pdf([]), "application/pdf")},
    )

    assert invalid.status_code == 415
    assert textless.status_code == 422
    assert repository.documents == {}
    assert list(upload_dir.iterdir()) == []


def test_should_reject_missing_document_when_confirming(
    document_test_context: tuple[
        TestClient,
        InMemoryBookingDocumentImportRepository,
        Path,
    ],
) -> None:
    """未知导入 ID 不得隐式创建申请。"""

    client, _, _ = document_test_context
    response = client.post(
        "/api/v1/booking-document-imports/999/confirm",
        json={
            "application_number": "DEMO-MISSING-DOCUMENT",
            "origin_port": "DEMO-SHA",
            "destination_port": "DEMO-LAX",
            "transport_mode": "SEA",
            "container_type": "40HQ",
        },
    )

    assert response.status_code == 404


def test_existing_create_schema_should_also_reject_non_demo_number() -> None:
    """原有创建接口同步收紧 Python 权威校验。"""

    with pytest.raises(ValidationError):
        BookingApplicationCreate(
            application_number="REAL-ORDER-001",
            origin_port="SHA",
            destination_port="LAX",
            container_type="40HQ",
        )
