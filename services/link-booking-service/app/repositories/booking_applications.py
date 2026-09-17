"""订舱申请的数据库读写操作。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking_application import BookingApplication


class BookingApplicationRepository:
    """将 SQLAlchemy 细节留在仓储层，避免路由直接拼接 SQL。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_number(self, application_number: str) -> BookingApplication | None:
        """按安全的服务内业务键查询单个申请。"""

        statement = select(BookingApplication).where(
            BookingApplication.application_number == application_number
        )
        return await self.session.scalar(statement)

    async def list_all(self) -> list[BookingApplication]:
        """按创建时间倒序返回申请列表。"""

        statement = select(BookingApplication).order_by(BookingApplication.id.desc())
        return list((await self.session.scalars(statement)).all())

    async def add(self, application: BookingApplication) -> BookingApplication:
        """保存并刷新实体，确保响应包含数据库生成的时间和 ID。"""

        self.session.add(application)
        await self.session.commit()
        await self.session.refresh(application)
        return application

    async def save(self, application: BookingApplication) -> BookingApplication:
        """提交已修改的申请状态并刷新数据库时间戳。"""

        await self.session.commit()
        await self.session.refresh(application)
        return application
