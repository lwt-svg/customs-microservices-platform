"""订舱申请业务规则及最小状态机。"""

from typing import ClassVar

from app.models.booking_application import BookingApplication, BookingStatus
from app.repositories.booking_applications import BookingApplicationRepository
from app.schemas.booking_application import BookingApplicationCreate


class DuplicateApplicationNumberError(Exception):
    """调用方重复使用申请编号时抛出，路由将其转为 409。"""


class InvalidStatusTransitionError(Exception):
    """请求的状态迁移不在首期状态机中时抛出。"""


class BookingApplicationService:
    """集中处理创建、查询和状态迁移，避免规则散落在 HTTP 层。"""

    # ClassVar 表明这是不可变更的领域规则，不是每个服务实例的可变状态。
    _ALLOWED_TRANSITIONS: ClassVar[dict[BookingStatus, set[BookingStatus]]] = {
        BookingStatus.DRAFT: {BookingStatus.SUBMITTED},
        BookingStatus.SUBMITTED: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED},
        BookingStatus.CONFIRMED: set(),
        BookingStatus.CANCELLED: set(),
    }

    def __init__(self, repository: BookingApplicationRepository) -> None:
        self.repository = repository

    async def create(self, payload: BookingApplicationCreate) -> BookingApplication:
        """创建草稿；重复编号先显式拒绝，便于 API 返回稳定的冲突语义。"""

        if await self.repository.get_by_number(payload.application_number):
            raise DuplicateApplicationNumberError
        # 显式设为草稿，保证 API 与无数据库的单元测试具有相同的初始状态。
        application = BookingApplication(**payload.model_dump(), status=BookingStatus.DRAFT)
        return await self.repository.add(application)

    async def get(self, application_number: str) -> BookingApplication | None:
        """查询一个申请。"""

        return await self.repository.get_by_number(application_number)

    async def list(self) -> list[BookingApplication]:
        """查询申请列表。"""

        return await self.repository.list_all()

    async def change_status(
        self, application: BookingApplication, target_status: BookingStatus
    ) -> BookingApplication:
        """仅允许 DRAFT→SUBMITTED、SUBMITTED→CONFIRMED/CANCELLED。"""

        if target_status not in self._ALLOWED_TRANSITIONS[application.status]:
            raise InvalidStatusTransitionError
        application.status = target_status
        return await self.repository.save(application)
