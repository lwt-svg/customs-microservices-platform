"""PDF 上传、候选解析和人工确认业务规则。"""

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.models.booking_application import (
    BookingApplication,
    BookingApplicationEvent,
    BookingEventType,
    BookingStatus,
)
from app.models.booking_document_import import (
    BookingDocumentImport,
    BookingDocumentImportStatus,
)
from app.repositories.booking_document_imports import (
    BookingDocumentImportRepository,
    ConcurrentDocumentConfirmationPersistenceError,
    DuplicateImportedApplicationPersistenceError,
)
from app.schemas.booking_document_import import (
    BookingDocumentConfirm,
    BookingDocumentImportRead,
    candidate_payload,
)
from app.services.pdf_booking_parser import PdfBookingCandidateParser
from app.storage.booking_documents import LocalBookingDocumentStorage


class BookingDocumentImportNotFoundError(Exception):
    """指定导入记录不存在。"""


class BookingDocumentAlreadyConfirmedError(Exception):
    """导入记录已经创建过草稿。"""


class DuplicateImportedApplicationError(Exception):
    """人工确认的申请编号已经存在。"""


class ConcurrentDocumentConfirmationError(Exception):
    """同一导入记录被并发确认。"""


class BookingDocumentImportService:
    """编排文件存储、规则解析和数据库事务。"""

    def __init__(
        self,
        repository: BookingDocumentImportRepository,
        storage: LocalBookingDocumentStorage,
        parser: PdfBookingCandidateParser,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.parser = parser

    async def upload(self, upload: UploadFile) -> BookingDocumentImportRead:
        """上传并同步解析；任何失败都会补偿删除已落盘文件。"""

        stored = await self.storage.store(upload)
        try:
            candidates = await run_in_threadpool(self.parser.parse, stored.path)
            document = BookingDocumentImport(
                original_filename=stored.original_filename,
                storage_key=stored.storage_key,
                content_type=stored.content_type,
                file_size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                status=BookingDocumentImportStatus.PARSED,
                parser_version=self.parser.VERSION,
                candidate_payload=candidate_payload(candidates),
            )
            saved = await self.repository.add(document)
        except Exception:
            self.storage.delete(stored.storage_key)
            raise
        return BookingDocumentImportRead.from_model(saved, created_application_number=None)

    async def get(self, document_id: int) -> BookingDocumentImportRead:
        """读取可恢复的人工复核会话。"""

        document = await self.repository.get(document_id)
        if document is None:
            raise BookingDocumentImportNotFoundError
        application = document.application
        return BookingDocumentImportRead.from_model(
            document,
            created_application_number=(
                application.application_number if application is not None else None
            ),
        )

    async def confirm(
        self, document_id: int, payload: BookingDocumentConfirm
    ) -> BookingApplication:
        """用人工确认值原子创建草稿，并把导入记录标为已确认。"""

        document = await self.repository.get(document_id)
        if document is None:
            raise BookingDocumentImportNotFoundError
        if document.status is BookingDocumentImportStatus.CONFIRMED:
            raise BookingDocumentAlreadyConfirmedError
        if await self.repository.get_application_by_number(payload.application_number):
            raise DuplicateImportedApplicationError

        application_values = payload.model_dump(exclude={"booking_details"})
        application_values["booking_details"] = (
            payload.booking_details.model_dump(mode="json")
            if payload.booking_details is not None
            else None
        )
        application = BookingApplication(**application_values, status=BookingStatus.DRAFT)
        event = BookingApplicationEvent(
            application=application,
            event_type=BookingEventType.DRAFT_CREATED,
            previous_status=None,
            current_status=BookingStatus.DRAFT,
            actor_label="SYSTEM_DEMO",
        )
        document.status = BookingDocumentImportStatus.CONFIRMED

        try:
            return await self.repository.confirm_with_application(document, application, event)
        except DuplicateImportedApplicationPersistenceError as error:
            raise DuplicateImportedApplicationError from error
        except ConcurrentDocumentConfirmationPersistenceError as error:
            raise ConcurrentDocumentConfirmationError from error
