"""PDF 导入、候选字段和人工复核的 API 模型。"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.booking_document_import import (
    BookingDocumentImport,
    BookingDocumentImportStatus,
)


class CandidateConfidence(StrEnum):
    """规则解析器对单个候选字段的确定程度。"""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    NONE = "NONE"


class BookingFieldCandidate(BaseModel):
    """一个可编辑候选值及其短证据。"""

    value: str | None = None
    evidence: str | None = Field(default=None, max_length=200)
    confidence: CandidateConfidence = CandidateConfidence.NONE


class BookingItemDetails(BaseModel):
    """Packing List 中一条商品明细。"""

    item_code: str = Field(min_length=1, max_length=64)
    lot_number: str | None = Field(default=None, max_length=64)
    po_number: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=0)
    cartons: int = Field(ge=0)
    net_weight_kg: float = Field(ge=0)
    gross_weight_kg: float = Field(ge=0)


class BookingPoAllocation(BaseModel):
    """一个包组内分配给某张 PO 的商品数量。"""

    po_number: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=0)


class BookingPackageDetails(BaseModel):
    """Packing List 中由一行小计收口的包组。"""

    description: str = Field(min_length=1, max_length=500)
    pallets: int = Field(ge=0)
    cartons: int = Field(ge=0)
    quantity: int = Field(ge=0)
    net_weight_kg: float = Field(ge=0)
    gross_weight_kg: float = Field(ge=0)
    cbm: float | None = Field(default=None, ge=0)
    po_allocations: list[BookingPoAllocation] = Field(default_factory=list)


class BookingDetails(BaseModel):
    """经规则解析并等待人工确认的完整订舱业务明细。"""

    invoice_number: str | None = Field(default=None, max_length=64)
    document_date: date | None = None
    ciq_number: str | None = Field(default=None, max_length=64)
    shipper: str | None = Field(default=None, max_length=1000)
    consignee: str | None = Field(default=None, max_length=1000)
    notify_party: str | None = Field(default=None, max_length=1000)
    marks: str | None = Field(default=None, max_length=1000)
    total_pallets: int | None = Field(default=None, ge=0)
    total_cartons: int | None = Field(default=None, ge=0)
    total_quantity: int | None = Field(default=None, ge=0)
    total_cbm: float | None = Field(default=None, ge=0)
    total_net_weight_kg: float | None = Field(default=None, ge=0)
    total_gross_weight_kg: float | None = Field(default=None, ge=0)
    items: list[BookingItemDetails] = Field(default_factory=list)
    packages: list[BookingPackageDetails] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)


class BookingDocumentCandidates(BaseModel):
    """当前规则解析器支持的订舱候选字段。"""

    application_number: BookingFieldCandidate
    origin_port: BookingFieldCandidate
    destination_port: BookingFieldCandidate
    transport_mode: BookingFieldCandidate
    container_type: BookingFieldCandidate
    requested_etd: BookingFieldCandidate
    # RULES_V1 的历史 candidate_payload 没有该键，因此必须保留默认值。
    booking_details: BookingDetails | None = None


class BookingDocumentConfirm(BaseModel):
    """人工复核后的权威值；候选字段不会自动进入申请表。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    application_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^DEMO-[A-Za-z0-9_-]+$",
    )
    origin_port: str = Field(min_length=1, max_length=64)
    destination_port: str = Field(min_length=1, max_length=64)
    transport_mode: str = Field(min_length=1, max_length=32)
    container_type: str = Field(min_length=1, max_length=32)
    requested_etd: date | None = None
    booking_details: BookingDetails | None = None


class BookingDocumentImportRead(BaseModel):
    """返回给复核页面的导入会话，不暴露磁盘路径或全文。"""

    id: int
    status: BookingDocumentImportStatus
    original_filename: str
    file_size_bytes: int
    parser_version: str
    candidates: BookingDocumentCandidates
    created_application_number: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(
        cls,
        document: BookingDocumentImport,
        created_application_number: str | None,
    ) -> "BookingDocumentImportRead":
        """把数据库 JSON 重新校验后返回，避免响应层触发异步 ORM 懒加载。"""

        candidates = BookingDocumentCandidates.model_validate(document.candidate_payload)
        return cls(
            id=document.id,
            status=document.status,
            original_filename=document.original_filename,
            file_size_bytes=document.file_size_bytes,
            parser_version=document.parser_version,
            candidates=candidates,
            created_application_number=created_application_number,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


def candidate_payload(candidates: BookingDocumentCandidates) -> dict[str, Any]:
    """生成可写入 MySQL JSON 列的普通字典。"""

    return candidates.model_dump(mode="json")
