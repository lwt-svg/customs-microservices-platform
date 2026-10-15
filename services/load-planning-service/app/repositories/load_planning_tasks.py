"""装载任务的数据库读写仓储。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.load_planning import LoadPlanningTask


class DuplicateTaskNumberPersistenceError(Exception):
    """数据库唯一约束发现任务编号重复。"""


class LoadPlanningTaskRepository:
    """隔离 SQLAlchemy 查询与事务细节。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _with_children():
        """统一定义详情和列表需要预加载的子表。"""

        return (
            selectinload(LoadPlanningTask.cargo_items),
            selectinload(LoadPlanningTask.pallet_specs),
        )

    async def get_by_number(self, task_number: str) -> LoadPlanningTask | None:
        """按稳定业务编号查询任务及全部输入明细。"""

        statement = (
            select(LoadPlanningTask)
            .options(*self._with_children())
            .where(LoadPlanningTask.task_number == task_number)
        )
        return await self.session.scalar(statement)

    async def list_all(self) -> list[LoadPlanningTask]:
        """按最新任务在前返回；子表用于计算列表摘要数量。"""

        statement = (
            select(LoadPlanningTask)
            .options(*self._with_children())
            .order_by(LoadPlanningTask.id.desc())
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def add(self, task: LoadPlanningTask) -> LoadPlanningTask:
        """一次事务写入任务、货物箱规和托盘规格。"""

        self.session.add(task)
        try:
            await self.session.commit()
        except IntegrityError as error:
            # 失败后必须 rollback，否则会话会停留在 PendingRollbackError 状态。
            await self.session.rollback()
            raise DuplicateTaskNumberPersistenceError from error
        # 重新查询一次，确保服务端默认时间、状态和两个 selectin 集合都已完整加载。
        persisted = await self.get_by_number(task.task_number)
        if persisted is None:  # pragma: no cover - commit 成功后理论上不可能发生
            raise RuntimeError("装载任务提交成功后无法重新读取")
        return persisted
