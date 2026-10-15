"""模拟承运人数据库访问。

事务边界放在仓储方法内，确保“最终决定 + 时间线 + 一条回调任务”要么一起提交，
要么一起回滚。
"""

from datetime import datetime

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.models.carrier_booking import (
    CallbackDelivery,
    CallbackDeliveryStatus,
    CarrierBooking,
    CarrierBookingEvent,
    CarrierBookingStatus,
)


class DuplicateIdempotencyKeyPersistenceError(Exception):
    """数据库唯一约束发现重复幂等键。"""


class ConcurrentBookingChangePersistenceError(Exception):
    """乐观锁发现门户操作已被另一请求抢先完成。"""


class CarrierBookingRepository:
    """封装 ORM 查询和提交细节，业务服务不拼 SQL。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, booking_id: int) -> CarrierBooking | None:
        """按内部主键查询快照。"""

        return await self.session.get(CarrierBooking, booking_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> CarrierBooking | None:
        """按调用方幂等键查询已有请求结果。"""

        return await self.session.scalar(
            select(CarrierBooking).where(CarrierBooking.idempotency_key == idempotency_key)
        )

    async def list_bookings(
        self,
        status: CarrierBookingStatus | None = None,
        keyword: str | None = None,
    ) -> list[CarrierBooking]:
        """给门户返回最新在前的轻量列表。"""

        statement: Select[tuple[CarrierBooking]] = select(CarrierBooking).order_by(
            CarrierBooking.id.desc()
        )
        if status is not None:
            statement = statement.where(CarrierBooking.status == status)
        if keyword:
            # keyword 是参数化绑定，用户在门户输入的字符不会成为 SQL 片段。
            pattern = f"%{keyword.strip()}%"
            statement = statement.where(
                or_(
                    CarrierBooking.application_number.like(pattern),
                    CarrierBooking.carrier_request_id.like(pattern),
                )
            )
        return list((await self.session.scalars(statement)).all())

    async def get_detail(
        self, booking_id: int
    ) -> tuple[CarrierBooking, list[CarrierBookingEvent], list[CallbackDelivery]] | None:
        """一次加载详情所需的两条关联集合，避免门户出现 N+1 查询。"""

        statement = (
            select(CarrierBooking)
            .where(CarrierBooking.id == booking_id)
            .options(
                selectinload(CarrierBooking.events),
                selectinload(CarrierBooking.callback_deliveries),
            )
        )
        booking = await self.session.scalar(statement)
        if booking is None:
            return None
        return booking, list(booking.events), list(booking.callback_deliveries)

    async def add_with_event(
        self,
        booking: CarrierBooking,
        event: CarrierBookingEvent,
    ) -> CarrierBooking:
        """在同一提交中保存收到申请快照和首条时间线。"""

        self.session.add_all([booking, event])
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateIdempotencyKeyPersistenceError from error
        await self.session.refresh(booking)
        return booking

    async def save_with_event(
        self,
        booking: CarrierBooking,
        event: CarrierBookingEvent,
        delivery: CallbackDelivery | None = None,
    ) -> CarrierBooking:
        """将门户状态变化、时间线和可选回调任务作为一个原子提交。"""

        self.session.add(event)
        if delivery is not None:
            self.session.add(delivery)
        try:
            await self.session.commit()
        except StaleDataError as error:
            await self.session.rollback()
            raise ConcurrentBookingChangePersistenceError from error
        await self.session.refresh(booking)
        return booking

    async def get_latest_delivery_for_booking(self, booking_id: int) -> CallbackDelivery | None:
        """读取同一最终/处理中操作已创建的唯一回调任务，供幂等重放返回。"""

        statement = (
            select(CallbackDelivery)
            .where(CallbackDelivery.booking_id == booking_id)
            .order_by(CallbackDelivery.id.desc())
            .options(selectinload(CallbackDelivery.booking))
        )
        return await self.session.scalar(statement)

    async def get_delivery(self, delivery_id: int) -> CallbackDelivery | None:
        """读取单条回调任务，并预加载关联订舱以组装请求 JSON。"""

        statement = (
            select(CallbackDelivery)
            .where(CallbackDelivery.id == delivery_id)
            .options(selectinload(CallbackDelivery.booking))
        )
        return await self.session.scalar(statement)

    async def list_deliveries(self) -> list[CallbackDelivery]:
        """供门户观察全部回调投递状态。"""

        statement = select(CallbackDelivery).order_by(CallbackDelivery.id.desc())
        return list((await self.session.scalars(statement)).all())

    async def list_due_deliveries(self, now: datetime) -> list[CallbackDelivery]:
        """获取达到重试时间或领取租约已过期的任务。"""

        statement = (
            select(CallbackDelivery)
            .where(
                or_(
                    and_(
                        CallbackDelivery.status == CallbackDeliveryStatus.RETRY_WAIT,
                        CallbackDelivery.next_attempt_at <= now,
                    ),
                    and_(
                        CallbackDelivery.status == CallbackDeliveryStatus.SENDING,
                        CallbackDelivery.lease_until <= now,
                    ),
                )
            )
            .order_by(CallbackDelivery.next_attempt_at.asc())
            .options(selectinload(CallbackDelivery.booking))
        )
        return list((await self.session.scalars(statement)).all())

    async def claim_delivery(
        self,
        delivery_id: int,
        allowed_statuses: set[CallbackDeliveryStatus],
        now: datetime,
        lease_until: datetime,
        require_due: bool = False,
    ) -> CallbackDelivery | None:
        """条件更新领取一条任务，防止多个 worker 对同一回调并发投递。

        ``UPDATE ... WHERE status ...`` 是最终裁决：两个 worker 即使都先读到了任务，也
        只有一个能把它改成 SENDING 并拿到租约。``attempt_count`` 在领取时递增，故崩溃
        后重领也会被准确计入最大尝试次数。
        """

        conditions = [
            CallbackDelivery.id == delivery_id,
            CallbackDelivery.status.in_(allowed_statuses),
        ]
        if require_due:
            conditions.append(
                or_(
                    and_(
                        CallbackDelivery.status == CallbackDeliveryStatus.RETRY_WAIT,
                        CallbackDelivery.next_attempt_at <= now,
                    ),
                    and_(
                        CallbackDelivery.status == CallbackDeliveryStatus.SENDING,
                        CallbackDelivery.lease_until <= now,
                    ),
                )
            )
        result = await self.session.execute(
            update(CallbackDelivery)
            .where(*conditions)
            .values(
                status=CallbackDeliveryStatus.SENDING,
                attempt_count=CallbackDelivery.attempt_count + 1,
                lease_until=lease_until,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.get_delivery(delivery_id)

    async def commit_delivery(self, delivery: CallbackDelivery) -> CallbackDelivery:
        """提交一次投递尝试后的状态。"""

        try:
            await self.session.commit()
        except StaleDataError:
            await self.session.rollback()
            raise
        await self.session.refresh(delivery)
        return delivery
