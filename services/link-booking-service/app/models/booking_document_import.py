"""PDF 订舱资料导入记录。

导入记录保存文件元数据和结构化候选，不保存 PDF 的完整提取文本。只有人工确认后，
它才会与一份订舱草稿建立一对一关系。
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import JSON, BigInteger, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.booking_application import BookingApplication


class BookingDocumentImportStatus(StrEnum):
    """PDF 导入会话允许出现的状态。"""

    PARSED = "PARSED"
    CONFIRMED = "CONFIRMED"


class BookingDocumentImport(Base):
    """一份已成功解析、等待人工复核的 PDF。"""

    __tablename__ = "booking_document_imports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    # storage_key 是服务生成的随机相对路径，绝不能使用客户端原文件名。
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[BookingDocumentImportStatus] = mapped_column(
        Enum(BookingDocumentImportStatus, name="booking_document_import_status"),
        default=BookingDocumentImportStatus.PARSED,
    )
    parser_version: Mapped[str] = mapped_column(String(32))
    # 只保存字段值、置信等级和最多 200 字符的单行证据，不保存整篇原文。
    candidate_payload: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}

    application: Mapped["BookingApplication | None"] = relationship(
        back_populates="source_document",
        uselist=False,
    )
