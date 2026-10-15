"""正式装载方案版本仓储。

锁定服务需要在同一事务中串行化“读取当前版本号 → 生成下一版本 → 写入”。仓储因此提供
任务行锁和当前版本锁；Service 仍只处理业务规则，不直接拼 SQL。
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent_run import AgentRun
from app.models.load_planning import LoadPlanningTask
from app.models.plan_version import LoadPlanVersion


class LoadPlanVersionPersistenceConflict(Exception):
    """数据库唯一约束裁决了并发锁定请求。"""


class LoadPlanVersionRepository:
    """正式方案版本的只追加持久化入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _version_options():
        return (
            selectinload(LoadPlanVersion.task),
            selectinload(LoadPlanVersion.events),
        )

    async def get_run_task_id(self, run_id: str) -> int | None:
        """先读取运行所属任务，用于随后锁住任务聚合根。"""

        return await self.session.scalar(select(AgentRun.task_id).where(AgentRun.id == run_id))

    async def get_run_for_update(self, run_id: str) -> AgentRun | None:
        """锁住来源运行并加载候选方案和审批事件。"""

        statement = (
            select(AgentRun)
            .options(
                selectinload(AgentRun.events),
                selectinload(AgentRun.plans),
            )
            .where(AgentRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_task_for_update(self, task_id: int) -> LoadPlanningTask | None:
        """按任务串行化正式版本号分配，并加载当前输入用于哈希复核。"""

        statement = (
            select(LoadPlanningTask)
            .options(
                selectinload(LoadPlanningTask.cargo_items),
                selectinload(LoadPlanningTask.pallet_specs),
            )
            .where(LoadPlanningTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_task_by_number(self, task_number: str) -> LoadPlanningTask | None:
        """查询 list/current 所属任务。"""

        return await self.session.scalar(
            select(LoadPlanningTask).where(LoadPlanningTask.task_number == task_number)
        )

    async def get(
        self,
        version_id: str,
        *,
        for_update: bool = False,
    ) -> LoadPlanVersion | None:
        """读取正式版本完整视图。"""

        statement = (
            select(LoadPlanVersion)
            .options(*self._version_options())
            .where(LoadPlanVersion.id == version_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_by_source_run(
        self,
        source_run_id: str,
        *,
        for_update: bool = False,
    ) -> LoadPlanVersion | None:
        """按来源运行查询，用于实现锁定请求幂等。"""

        statement = (
            select(LoadPlanVersion)
            .options(*self._version_options())
            .where(LoadPlanVersion.source_run_id == source_run_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_current_for_task(
        self,
        task_id: int,
        *,
        for_update: bool = False,
    ) -> LoadPlanVersion | None:
        """读取版本号最大的正式方案。"""

        statement = (
            select(LoadPlanVersion)
            .options(*self._version_options())
            .where(LoadPlanVersion.task_id == task_id)
            .order_by(LoadPlanVersion.version_number.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_for_task(self, task_id: int) -> list[LoadPlanVersion]:
        """按最新版本在前返回任务的完整正式版本历史。"""

        statement = (
            select(LoadPlanVersion)
            .options(*self._version_options())
            .where(LoadPlanVersion.task_id == task_id)
            .order_by(LoadPlanVersion.version_number.desc())
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def save(self, plan_version: LoadPlanVersion) -> LoadPlanVersion:
        """只追加一个新版本及其首条审计事件。"""

        self.session.add(plan_version)
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise LoadPlanVersionPersistenceConflict from error

        persisted = await self.get(plan_version.id)
        if persisted is None:  # pragma: no cover - commit 成功后理论上不可发生
            raise RuntimeError("正式装载方案版本提交成功后无法重新读取")
        return persisted
