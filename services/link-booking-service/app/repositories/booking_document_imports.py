"""PDF 导入记录及人工确认事务的持久化操作。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.models.booking_application import BookingApplication, BookingApplicationEvent
from app.models.booking_document_import import BookingDocumentImport


class DuplicateImportedApplicationPersistenceError(Exception):
    """人工确认使用了已存在的申请编号。"""


class ConcurrentDocumentConfirmationPersistenceError(Exception):
    """同一导入记录已被另一个请求确认。"""


class BookingDocumentImportRepository:
    """保存解析会话，并原子完成“文档确认 + 草稿 + 事件”。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, document: BookingDocumentImport) -> BookingDocumentImport:
        """保存已成功解析的导入记录。"""

        self.session.add(document)
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def get(self, document_id: int) -> BookingDocumentImport | None:
        """读取导入记录及其可能关联的订舱申请。"""

        statement = (
            select(BookingDocumentImport)
            .options(selectinload(BookingDocumentImport.application))
            .where(BookingDocumentImport.id == document_id)
        )
        return await self.session.scalar(statement)

    async def get_application_by_number(
        self, application_number: str
    ) -> BookingApplication | None:
        """在提交事务前提供清晰的重复编号提示。"""

        statement = select(BookingApplication).where(
            BookingApplication.application_number == application_number
        )
        return await self.session.scalar(statement)

    async def confirm_with_application(
        self,
        document: BookingDocumentImport,
        application: BookingApplication,
        event: BookingApplicationEvent,
    ) -> BookingApplication:
        """一次提交完成文档确认、草稿创建和首条审计事件。"""

        try:
            # 先刷新导入记录的乐观锁版本。这样两个请求即使填写不同申请编号，也会先在
            # document.version 上分出胜负，而不是等到来源外键唯一约束时报成“编号重复”。
            await self.session.flush([document])
            application.source_document_import_id = document.id
            self.session.add(application)
            self.session.add(event)
            await self.session.commit()
        except StaleDataError as error:
            await self.session.rollback()
            raise ConcurrentDocumentConfirmationPersistenceError from error
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateImportedApplicationPersistenceError from error
        await self.session.refresh(application)
        return application
