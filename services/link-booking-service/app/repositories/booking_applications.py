"""订舱申请的数据库读写操作。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.models.booking_application import BookingApplication, BookingApplicationEvent
from app.models.carrier_submission import CarrierBookingRequest


class DuplicateApplicationNumberPersistenceError(Exception):
    """数据库唯一约束发现重复申请编号时抛出，由业务层转换成稳定领域错误。"""


class ConcurrentStatusChangePersistenceError(Exception):
    """乐观锁发现申请已被其他请求更新时抛出，由业务层转换成 HTTP 409。"""


class BookingApplicationRepository:
    """将 SQLAlchemy 细节留在仓储层，避免路由直接拼接 SQL。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_number(self, application_number: str) -> BookingApplication | None:
        """按服务内业务键查询。

        application_number 是接口使用的业务查询键；id 只是数据库内部主键，不需要调用方
        为了查询一份申请而知道数据库自增值。
        """

        statement = select(BookingApplication).where(
            BookingApplication.application_number == application_number
        )
        return await self.session.scalar(statement)

    async def list_all(self) -> list[BookingApplication]:
        """按创建时间倒序返回申请列表。"""

        statement = select(BookingApplication).order_by(BookingApplication.id.desc())
        return list((await self.session.scalars(statement)).all())

    async def list_events(self, application_id: int) -> list[BookingApplicationEvent]:
        """倒序查询一份申请的审计流水。

        事件与申请使用同一服务、同一 schema；Java BFF 只读取 Python 已整理的详情，不会
        跨库拼接流水。
        """

        statement = (
            select(BookingApplicationEvent)
            .where(BookingApplicationEvent.application_id == application_id)
            .order_by(BookingApplicationEvent.id.desc())
        )
        return list((await self.session.scalars(statement)).all())

    async def add_with_event(
        self, application: BookingApplication, event: BookingApplicationEvent
    ) -> BookingApplication:
        """原子创建草稿及其首条操作流水。

        业务层会先查询一次重复编号，给普通请求更清晰的响应；但两个并发请求可能同时通过
        这次查询。数据库 unique 约束才是最终防线，所以这里仍需捕获 IntegrityError。

        ``event.application = application`` 让 SQLAlchemy 在一次事务中先插入申请、取回
        数据库主键，再插入事件。不能先提交申请、再单独提交事件，否则第二次失败会留下
        一份没有“创建记录”的草稿。
        """

        self.session.add(application)
        self.session.add(event)
        try:
            await self.session.commit()
        except IntegrityError as error:
            # commit 失败后会话处于回滚等待状态；必须 rollback 后才能继续正常关闭或复用。
            await self.session.rollback()
            raise DuplicateApplicationNumberPersistenceError from error
        await self.session.refresh(application)
        return application

    async def save_with_event(
        self, application: BookingApplication, event: BookingApplicationEvent
    ) -> BookingApplication:
        """原子保存状态变化及对应审计事件。

        ``BookingApplication.version`` 会让 SQLAlchemy 使用乐观锁。并发请求若拿着过期
        version 提交，UPDATE 不会命中任何行并抛出 StaleDataError；必须 rollback，不能
        让后一次操作静默覆盖先完成的状态。事件也因此不会给失败的状态变化留下“成功”
        记录。
        """

        self.session.add(event)
        try:
            await self.session.commit()
        except (IntegrityError, StaleDataError) as error:
            await self.session.rollback()
            raise ConcurrentStatusChangePersistenceError from error
        await self.session.refresh(application)
        return application

    async def save_submission_with_event(
        self,
        application: BookingApplication,
        event: BookingApplicationEvent,
        carrier_request: CarrierBookingRequest,
    ) -> BookingApplication:
        """同一事务保存 SUBMITTED、审计事件和待投递承运人任务。

        不能先把申请改成 SUBMITTED，再在第二个事务创建投递任务；否则进程恰好在两个
        commit 之间退出时，这份申请将永远没有可重试的承运人请求。
        """

        self.session.add(event)
        self.session.add(carrier_request)
        try:
            await self.session.commit()
        except (IntegrityError, StaleDataError) as error:
            await self.session.rollback()
            raise ConcurrentStatusChangePersistenceError from error
        await self.session.refresh(application)
        await self.session.refresh(carrier_request)
        return application

    async def save_cancellation_with_event(
        self,
        application: BookingApplication,
        event: BookingApplicationEvent,
        carrier_request: CarrierBookingRequest | None,
    ) -> BookingApplication:
        """取消申请时同时停止尚未完成的本地投递任务。"""

        self.session.add(event)
        if carrier_request is not None:
            self.session.add(carrier_request)
        try:
            await self.session.commit()
        except StaleDataError as error:
            await self.session.rollback()
            raise ConcurrentStatusChangePersistenceError from error
        await self.session.refresh(application)
        return application
