"""状态化装托智能体运行仓储。

Service 只描述状态机规则；查询装载关系、提交事务和失败回滚集中在这里，避免 API 层到处
出现 SQLAlchemy 细节。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.load_planning import LoadPlanningTask


class AgentRunRepository:
    """运行、事件、方案和任务输入的统一持久化入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_task(self, task_number: str) -> LoadPlanningTask | None:
        """读取一次运行所需的全部任务输入。"""

        statement = (
            select(LoadPlanningTask)
            .options(
                selectinload(LoadPlanningTask.cargo_items),
                selectinload(LoadPlanningTask.pallet_specs),
            )
            .where(LoadPlanningTask.task_number == task_number)
        )
        return await self.session.scalar(statement)

    async def get_task_by_id(self, task_id: int) -> LoadPlanningTask | None:
        """按外键读取任务，供 GET/resume/decision 恢复上下文。"""

        statement = (
            select(LoadPlanningTask)
            .options(
                selectinload(LoadPlanningTask.cargo_items),
                selectinload(LoadPlanningTask.pallet_specs),
            )
            .where(LoadPlanningTask.id == task_id)
        )
        return await self.session.scalar(statement)

    async def get(self, run_id: str, *, for_update: bool = False) -> AgentRun | None:
        """读取运行及按顺序加载的事件、候选方案。

        状态迁移请求可以要求数据库行锁。MySQL 会在本次请求提交前锁住这一条 AgentRun，
        让两个浏览器同时审批时不只依赖应用层的 ``run_version`` 检查；SQLite 测试环境
        不支持真正的行锁，但仍会执行同一套版本校验。
        """

        statement = (
            select(AgentRun)
            .options(
                selectinload(AgentRun.events),
                selectinload(AgentRun.plans),
            )
            .where(AgentRun.id == run_id)
            # 当前会话可能已经加载过同一实体；强制用数据库最新结果覆盖集合。
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_by_idempotency_key(
        self,
        task_id: int,
        idempotency_key: str,
    ) -> AgentRun | None:
        """按任务和创建幂等键读取已有运行。

        这里仍然加载完整聚合，因为网络重试应该得到与第一次创建完全相同的响应快照，
        而不是只返回一个 run_id 让前端再发起第二次查询。
        """

        statement = (
            select(AgentRun)
            .options(
                selectinload(AgentRun.events),
                selectinload(AgentRun.plans),
            )
            .where(
                AgentRun.task_id == task_id,
                AgentRun.idempotency_key == idempotency_key,
            )
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_replan_by_idempotency_key(
        self,
        parent_run_id: str,
        replan_idempotency_key_hash: str,
    ) -> AgentRun | None:
        """按父运行和重新规划幂等键摘要读取已经创建的子运行。

        原始请求头不进入数据库。Service 会先规范化请求头，再查询它的 SHA-256 摘要；
        因此 MySQL 和 SQLite 不会因字符串排序规则差异而对同一个 key 得出不同结论。
        """

        statement = (
            select(AgentRun)
            .options(
                selectinload(AgentRun.events),
                selectinload(AgentRun.plans),
            )
            .where(
                AgentRun.parent_run_id == parent_run_id,
                AgentRun.replan_idempotency_key_hash == replan_idempotency_key_hash,
            )
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def list_active_for_task(self, task_id: int) -> list[AgentRun]:
        """按更新时间倒序返回仍可继续的运行。

        运行内容仍以 Python + MySQL 为权威来源；这个列表只是让新浏览器/新设备
        找到未完成运行，不让 localStorage 成为唯一恢复入口。
        """

        statement = (
            select(AgentRun)
            .options(
                selectinload(AgentRun.events),
                selectinload(AgentRun.plans),
            )
            .where(
                AgentRun.task_id == task_id,
                AgentRun.status.in_(
                    [AgentRunStatus.AWAITING_INPUT, AgentRunStatus.READY_FOR_REVIEW]
                ),
            )
            .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def list_history_for_task(self, task_id: int) -> list[AgentRun]:
        """返回任务的完整运行历史，包括终态和失败运行。

        active 列表只服务“继续处理”，不能把 ``FAILED`` 混进去误导前端调用 resume。
        独立历史查询则保留全部状态，让首次创建/重规划返回 500 后仍能找到已持久化的
        失败 run_id、失败摘要和审计事件。
        """

        statement = (
            select(AgentRun)
            .options(
                selectinload(AgentRun.events),
                selectinload(AgentRun.plans),
            )
            .where(AgentRun.task_id == task_id)
            .order_by(
                AgentRun.updated_at.desc(),
                AgentRun.created_at.desc(),
                AgentRun.id.desc(),
            )
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def save(self, run: AgentRun) -> AgentRun:
        """原子保存一次状态迁移，并返回数据库中的完整最新视图。"""

        self.session.add(run)
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        persisted = await self.get(run.id)
        if persisted is None:  # pragma: no cover - 提交成功后理论上不可发生
            raise RuntimeError("智能体运行提交成功后无法重新读取")
        return persisted
