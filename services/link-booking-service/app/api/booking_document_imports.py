"""PDF 订舱资料导入与人工复核 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.repositories.booking_document_imports import BookingDocumentImportRepository
from app.schemas.booking_application import BookingApplicationRead
from app.schemas.booking_document_import import (
    BookingDocumentConfirm,
    BookingDocumentImportRead,
)
from app.services.booking_document_imports import (
    BookingDocumentAlreadyConfirmedError,
    BookingDocumentImportNotFoundError,
    BookingDocumentImportService,
    ConcurrentDocumentConfirmationError,
    DuplicateImportedApplicationError,
)
from app.services.pdf_booking_parser import PdfBookingCandidateParser, UnreadableBookingPdfError
from app.storage.booking_documents import (
    BookingDocumentTooLargeError,
    LocalBookingDocumentStorage,
    UnsupportedBookingDocumentError,
)

router = APIRouter(prefix="/booking-document-imports", tags=["booking-document-imports"])


def get_document_import_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BookingDocumentImportService:
    """按请求组装导入服务，测试可以整体替换此依赖。"""

    return BookingDocumentImportService(
        BookingDocumentImportRepository(session),
        LocalBookingDocumentStorage(
            settings.booking_upload_dir,
            settings.booking_upload_max_bytes,
        ),
        PdfBookingCandidateParser(),
    )


ServiceDependency = Annotated[
    BookingDocumentImportService,
    Depends(get_document_import_service),
]


@router.post("", response_model=BookingDocumentImportRead, status_code=status.HTTP_201_CREATED)
async def upload_booking_document(
    file: Annotated[UploadFile, File(description="仅接受不超过 5 MiB 的文本型 PDF")],
    service: ServiceDependency,
) -> BookingDocumentImportRead:
    """上传 PDF 并返回等待人工复核的候选字段。"""

    try:
        return await service.upload(file)
    except BookingDocumentTooLargeError as error:
        raise HTTPException(status_code=413, detail="PDF 文件不能超过 5 MiB") from error
    except UnsupportedBookingDocumentError as error:
        raise HTTPException(status_code=415, detail="只接受有效的 PDF 文件") from error
    except UnreadableBookingPdfError as error:
        raise HTTPException(
            status_code=422,
            detail="PDF 已加密、损坏或没有可提取文本；当前版本不支持 OCR",
        ) from error


@router.get("/{document_id}", response_model=BookingDocumentImportRead)
async def get_booking_document_import(
    document_id: int,
    service: ServiceDependency,
) -> BookingDocumentImportRead:
    """重新读取一次人工复核会话。"""

    try:
        return await service.get(document_id)
    except BookingDocumentImportNotFoundError as error:
        raise HTTPException(status_code=404, detail="PDF 导入记录不存在") from error


@router.post(
    "/{document_id}/confirm",
    response_model=BookingApplicationRead,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_booking_document_import(
    document_id: int,
    payload: BookingDocumentConfirm,
    service: ServiceDependency,
) -> BookingApplicationRead:
    """使用人工确认值创建 DRAFT，而不是直接采用解析候选。"""

    try:
        return await service.confirm(document_id, payload)
    except BookingDocumentImportNotFoundError as error:
        raise HTTPException(status_code=404, detail="PDF 导入记录不存在") from error
    except BookingDocumentAlreadyConfirmedError as error:
        raise HTTPException(status_code=409, detail="该 PDF 已经创建过订舱草稿") from error
    except DuplicateImportedApplicationError as error:
        raise HTTPException(status_code=409, detail="申请编号已存在") from error
    except ConcurrentDocumentConfirmationError as error:
        raise HTTPException(
            status_code=409,
            detail="该 PDF 已被其他操作确认，请刷新后重试",
        ) from error
