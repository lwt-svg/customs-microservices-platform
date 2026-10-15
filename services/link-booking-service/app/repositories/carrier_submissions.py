"""承运人提交任务、租约和回调收件记录的数据库操作。"""

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.models.booking_application import BookingApplication, BookingApplicationEvent
from app.models.carrier_submission import (
    CarrierBookingRequest,
    CarrierBookingRequestStatus,
    CarrierCallbackReceipt,
)


class DuplicateCallbackReceiptPersistenceError(Exception):
    """两个并发请求同时写入相同 callback_id 时由数据库唯一约束裁决。"""


class ConcurrentCarrierCallbackPersistenceError(Exception):
    """回调更新申请时遇到乐观锁冲突。"""


class CarrierSubmissionRepository:
    """集中管理投递任务状态，HTTP 层不直接更新租约或重试次数。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_request(self, request_id: int) -> CarrierBookingRequest | None:
        """按内部任务主键读取，并预加载申请以便重建发送 JSON。"""

        statement = (
            select(CarrierBookingRequest)
            .options(selectinload(CarrierBookingRequest.application))
            .where(CarrierBookingRequest.id == request_id)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_by_application_id(
        self, application_id: int
    ) -> CarrierBookingRequest | None:
        """详情接口按申请主键读取唯一投递任务。"""

        statement = select(CarrierBookingRequest).where(
            CarrierBookingRequest.application_id == application_id
        )
        return await self.session.scalar(statement)

    async def get_cancellable_for_update(
        self, application_id: int
    ) -> CarrierBookingRequest | None:
        """锁住仍未受理的任务，避免取消与 worker 抢占同时成功。"""

        statement = (
            select(CarrierBookingRequest)
            .where(
                CarrierBookingRequest.application_id == application_id,
                CarrierBookingRequest.status.in_(
                    {
                        CarrierBookingRequestStatus.PENDING,
                        CarrierBookingRequestStatus.RETRY_WAIT,
                        CarrierBookingRequestStatus.DEAD,
                    }
                ),
            )
            .with_for_update()
        )
        return await self.session.scalar(statement)

    async def get_by_application_number(
        self, application_number: str
    ) -> CarrierBookingRequest | None:
        """手工重试和回调可使用对外业务编号定位任务。"""

        statement = (
            select(CarrierBookingRequest)
            .join(BookingApplication)
            .options(selectinload(CarrierBookingRequest.application))
            .where(BookingApplication.application_number == application_number)
        )
        return await self.session.scalar(statement)

    async def get_callback(self, callback_id: str) -> CarrierCallbackReceipt | None:
        """按承运人提供的幂等键读取收件凭证。"""

        statement = select(CarrierCallbackReceipt).where(
            CarrierCallbackReceipt.callback_id == callback_id
        )
        return await self.session.scalar(statement)

    async def claim_for_delivery(
        self,
        request_id: int,
        now: datetime,
        lease_until: datetime,
        *,
        manual: bool,
    ) -> CarrierBookingRequest | None:
        """使用条件 UPDATE 抢占任务，避免两个 worker 同时向承运人发送。

        ``SENDING`` 租约过期后可被自动重试接管，防止进程在 HTTP 调用中途退出后任务永久
        卡住。手工重试只接受 RETRY_WAIT/DEAD，不能重复发送已经接受或终态的请求。
        """

        if manual:
            claimable = CarrierBookingRequest.status.in_(
                {
                    CarrierBookingRequestStatus.RETRY_WAIT,
                    CarrierBookingRequestStatus.DEAD,
                }
            )
        else:
            claimable = or_(
                CarrierBookingRequest.status == CarrierBookingRequestStatus.PENDING,
                (
                    CarrierBookingRequest.status == CarrierBookingRequestStatus.RETRY_WAIT
                )
                & (CarrierBookingRequest.next_attempt_at <= now),
                (
                    CarrierBookingRequest.status == CarrierBookingRequestStatus.SENDING
                )
                & (CarrierBookingRequest.lease_until <= now),
            )

        values: dict[str, object] = {
            "status": CarrierBookingRequestStatus.SENDING,
            "attempt_count": CarrierBookingRequest.attempt_count + 1,
            "next_attempt_at": None,
            "lease_until": lease_until,
        }
        if manual:
            values["manual_retry_count"] = CarrierBookingRequest.manual_retry_count + 1

        result = await self.session.execute(
            update(CarrierBookingRequest)
            .where(CarrierBookingRequest.id == request_id, claimable)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        if result.rowcount != 1:
            return None
        return await self.get_request(request_id)

    async def list_due_request_ids(self, now: datetime, limit: int = 50) -> list[int]:
        """返回可自动重试的任务主键；真正发送前仍须再次条件抢占。"""

        statement = (
            select(CarrierBookingRequest.id)
            .where(
                or_(
                    CarrierBookingRequest.status == CarrierBookingRequestStatus.PENDING,
                    (
                        CarrierBookingRequest.status
                        == CarrierBookingRequestStatus.RETRY_WAIT
                    )
                    & (CarrierBookingRequest.next_attempt_at <= now),
                    (
                        CarrierBookingRequest.status == CarrierBookingRequestStatus.SENDING
                    )
                    & (CarrierBookingRequest.lease_until <= now),
                )
            )
            .order_by(CarrierBookingRequest.id)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def commit_request(self, request: CarrierBookingRequest) -> CarrierBookingRequest:
        """保存一次投递的成功或失败结果。"""

        await self.session.commit()
        await self.session.refresh(request)
        return request

    async def finish_delivery(
        self,
        request_id: int,
        values: dict[str, object],
    ) -> CarrierBookingRequest:
        """仅当任务仍为 SENDING 时写入 HTTP 结果。

        网络请求在数据库事务之外执行。期间如果承运人回调已经把任务改成终态，这个条件
        UPDATE 将不命中，从而避免较晚返回的 HTTP 201 把 CONFIRMED/REJECTED 倒退为
        ACCEPTED。
        """

        await self.session.execute(
            update(CarrierBookingRequest)
            .where(
                CarrierBookingRequest.id == request_id,
                CarrierBookingRequest.status == CarrierBookingRequestStatus.SENDING,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        request = await self.get_request(request_id)
        if request is None:  # pragma: no cover - 主键在本次操作期间不会物理删除
            raise RuntimeError("承运人提交任务在投递完成后意外缺失")
        return request

    async def save_callback(
        self,
        application: BookingApplication,
        event: BookingApplicationEvent | None,
        request: CarrierBookingRequest,
        receipt: CarrierCallbackReceipt,
    ) -> None:
        """同一事务保存终态、审计事件、请求快照和回调收件记录。"""

        if event is not None:
            self.session.add(event)
        self.session.add(receipt)
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateCallbackReceiptPersistenceError from error
        except StaleDataError as error:
            await self.session.rollback()
            raise ConcurrentCarrierCallbackPersistenceError from error
        await self.session.refresh(application)
        await self.session.refresh(request)
