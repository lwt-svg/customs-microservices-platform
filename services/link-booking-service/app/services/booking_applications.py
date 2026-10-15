"""订舱申请业务规则及异步承运人状态机。"""

from __future__ import annotations

from typing import ClassVar

from app.models.booking_application import (
    BookingApplication,
    BookingApplicationEvent,
    BookingEventType,
    BookingStatus,
)
from app.models.carrier_submission import CarrierBookingRequest, CarrierBookingRequestStatus
from app.repositories.booking_applications import (
    BookingApplicationRepository,
    ConcurrentStatusChangePersistenceError,
    DuplicateApplicationNumberPersistenceError,
)
from app.schemas.booking_application import BookingApplicationCreate
from app.services.carrier_submissions import CarrierSubmissionService


class DuplicateApplicationNumberError(Exception):
    """调用方重复使用申请编号时抛出，路由将其转为 409。"""


class InvalidStatusTransitionError(Exception):
    """请求的状态迁移不在首期状态机中时抛出。"""


class ConcurrentStatusChangeError(Exception):
    """同一申请已被其他请求更新时抛出，调用方应刷新后再决定下一步。"""


class BookingApplicationService:
    """集中处理创建、查询和状态迁移，避免规则散落在 HTTP 层。"""

    # ClassVar 表明这是不可变更的领域规则，不是每个服务实例的可变状态。
    _ALLOWED_TRANSITIONS: ClassVar[dict[BookingStatus, set[BookingStatus]]] = {
        BookingStatus.DRAFT: {BookingStatus.SUBMITTED},
        # CONFIRMED/REJECTED 只能来自已校验的模拟承运人回调，浏览器不能直接指定。
        BookingStatus.SUBMITTED: {BookingStatus.CANCELLED},
        BookingStatus.CONFIRMED: set(),
        BookingStatus.REJECTED: set(),
        BookingStatus.CANCELLED: set(),
    }

    def __init__(
        self,
        repository: BookingApplicationRepository,
        carrier_service: CarrierSubmissionService | None = None,
        *,
        dispatch_on_submit: bool = True,
    ) -> None:
        self.repository = repository
        self.carrier_service = carrier_service
        self.dispatch_on_submit = dispatch_on_submit

    async def create(self, payload: BookingApplicationCreate) -> BookingApplication:
        """创建草稿，并把普通重复与并发重复统一转换成相同领域错误。"""

        # 先查可以让绝大多数重复请求在写库前就被拒绝，但它不是并发安全的最终保证。
        if await self.repository.get_by_number(payload.application_number):
            raise DuplicateApplicationNumberError
        # 显式设为草稿，保证 API 与无数据库的单元测试具有相同的初始状态。
        application_values = payload.model_dump(exclude={"booking_details"})
        application_values["booking_details"] = (
            payload.booking_details.model_dump(mode="json")
            if payload.booking_details is not None
            else None
        )
        application = BookingApplication(**application_values, status=BookingStatus.DRAFT)
        # 没有登录模块前，不能伪造“admin”之类真实身份。这个标签明确表示当前事件由
        # 本地学习流程产生；认证章节会把它替换为经过服务端验证的用户标识。
        event = BookingApplicationEvent(
            application=application,
            event_type=BookingEventType.DRAFT_CREATED,
            previous_status=None,
            current_status=BookingStatus.DRAFT,
            actor_label="SYSTEM_DEMO",
        )
        try:
            return await self.repository.add_with_event(application, event)
        except DuplicateApplicationNumberPersistenceError as error:
            # 两个请求同时通过“先查”时，数据库唯一约束会在这里拦住后提交的一方。
            raise DuplicateApplicationNumberError from error

    async def get(self, application_number: str) -> BookingApplication | None:
        """查询一个申请。"""

        return await self.repository.get_by_number(application_number)

    async def list(self) -> list[BookingApplication]:
        """查询申请列表。"""

        return await self.repository.list_all()

    async def detail(
        self, application: BookingApplication
    ) -> tuple[
        BookingApplication,
        list[BookingApplicationEvent],
        CarrierBookingRequest | None,
    ]:
        """返回详情快照及流水。

        当前申请快照和流水分开返回，是为了让 API 语义清楚：申请行描述“现在是什么”，
        事件列表描述“如何变成现在这样”。以后加入 PDF、承运人回调和重试任务时，也只需
        增加事件类型，不要把全部历史塞进申请主表。
        """

        carrier_request = None
        if self.carrier_service is not None:
            carrier_request = await self.carrier_service.get_for_application(application.id)
        return application, await self.repository.list_events(application.id), carrier_request

    async def change_status(
        self, application: BookingApplication, target_status: BookingStatus
    ) -> BookingApplication:
        """浏览器仅可提交草稿或取消；确认/拒绝只能由模拟承运人回调驱动。"""

        if target_status not in self._ALLOWED_TRANSITIONS[application.status]:
            raise InvalidStatusTransitionError

        previous_status = application.status
        carrier_request = None
        if target_status is BookingStatus.CANCELLED and self.carrier_service is not None:
            carrier_request = await self.carrier_service.get_cancellable_for_update(
                application.id
            )
            if carrier_request is None:
                # SENDING 也不允许取消：网络结果未知，承运人可能已经受理。
                raise InvalidStatusTransitionError

        application.status = target_status
        event = BookingApplicationEvent(
            application=application,
            event_type=BookingEventType(target_status.value),
            previous_status=previous_status,
            current_status=target_status,
            booking_reference=application.booking_reference,
            actor_label="SYSTEM_DEMO",
        )
        try:
            if target_status is BookingStatus.SUBMITTED:
                carrier_request = CarrierSubmissionService.build_pending_request(application)
                persisted = await self.repository.save_submission_with_event(
                    application,
                    event,
                    carrier_request,
                )
                # 申请、事件和 PENDING 任务已经先提交。下面的网络失败只更新任务状态，
                # 不会让申请退回 DRAFT，也不会丢失后续可重试依据。
                if self.carrier_service is not None and self.dispatch_on_submit:
                    await self.carrier_service.dispatch_new(carrier_request.id)
                return persisted

            if carrier_request is not None:
                carrier_request.status = CarrierBookingRequestStatus.CANCELLED
                carrier_request.next_attempt_at = None
                carrier_request.lease_until = None
            return await self.repository.save_cancellation_with_event(
                application,
                event,
                carrier_request,
            )
        except ConcurrentStatusChangePersistenceError as error:
            raise ConcurrentStatusChangeError from error
