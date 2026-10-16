"""Shipment Coordinator Agent 的 SQLAlchemy 仓储。

仓储只处理查询、加载策略和数据库 flush。跨服务调用、状态机判断和事务分段全部留在
Service：尤其不能在持有 ``SELECT ... FOR UPDATE`` 的事务中等待 LINK HTTP。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.shipment_agent_run import (
    ShipmentAgentCommand,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
)
from app.models.shipment_case import ShipmentCase

_TERMINAL_STATUSES = {
    ShipmentAgentRunStatus.COMPLETED,
    ShipmentAgentRunStatus.FAILED,
    ShipmentAgentRunStatus.CANCELLED,
}


class ShipmentAgentRunRepository:
    """总 Agent 运行、命令和案件上下文的持久化入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _run_options():
        return (
            selectinload(ShipmentAgentRun.shipment_case).selectinload(
                ShipmentCase.resource_bindings
            ),
            selectinload(ShipmentAgentRun.events),
        )

    async def get(
        self,
        run_id: str,
        *,
        for_update: bool = False,
    ) -> ShipmentAgentRun | None:
        """读取运行、案件资源和完整 Trace；写命令可选择锁住运行主行。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(ShipmentAgentRun.id == run_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_case(
        self,
        case_number: str,
        *,
        for_update: bool = False,
    ) -> ShipmentCase | None:
        """按业务编号读取案件及资源绑定。"""

        statement = (
            select(ShipmentCase)
            .options(
                selectinload(ShipmentCase.resource_bindings),
                selectinload(ShipmentCase.events),
            )
            .where(ShipmentCase.case_number == case_number)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_active_for_case(
        self,
        case_id: str,
        *,
        for_update: bool = False,
    ) -> ShipmentAgentRun | None:
        """返回案件最近一条非终态运行。

        第一版每个案件只允许一个未完成总 Agent。数据库层保留历史运行；Service 在案件
        行锁下检查本查询，避免两个启动请求正常路径下同时创建两条运行。
        """

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(
                ShipmentAgentRun.case_id == case_id,
                ShipmentAgentRun.status.not_in(_TERMINAL_STATUSES),
            )
            .order_by(
                ShipmentAgentRun.updated_at.desc(),
                ShipmentAgentRun.created_at.desc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_for_case(self, case_id: str) -> list[ShipmentAgentRun]:
        """按更新时间倒序返回一票案件的全部运行历史。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(ShipmentAgentRun.case_id == case_id)
            .order_by(
                ShipmentAgentRun.updated_at.desc(),
                ShipmentAgentRun.created_at.desc(),
                ShipmentAgentRun.id.desc(),
            )
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def list_all(self) -> list[ShipmentAgentRun]:
        """Agent Center 使用的全局列表；一次查询避免按案件 N+1。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .order_by(
                ShipmentAgentRun.updated_at.desc(),
                ShipmentAgentRun.created_at.desc(),
                ShipmentAgentRun.id.desc(),
            )
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def get_by_start_key(
        self,
        idempotency_key_hash: str,
    ) -> ShipmentAgentRun | None:
        """恢复并发或网络重试创建的运行。"""

        statement = (
            select(ShipmentAgentRun)
            .options(*self._run_options())
            .where(
                ShipmentAgentRun.start_idempotency_key_hash
                == idempotency_key_hash
            )
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_command(
        self,
        idempotency_key_hash: str,
    ) -> ShipmentAgentCommand | None:
        """按全局幂等键摘要读取成功命令。"""

        return await self.session.scalar(
            select(ShipmentAgentCommand).where(
                ShipmentAgentCommand.idempotency_key_hash
                == idempotency_key_hash
            )
        )

    def add(self, value: object) -> None:
        self.session.add(value)

    async def flush(self) -> None:
        await self.session.flush()
