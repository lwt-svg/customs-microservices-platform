"""发运案件仓储。

Shipment Case 的一次 ``APPLIED`` 命令可能同时修改案件修订号、追加审计事件并新增资源
绑定；``NO_OP`` 则只新增命令台账。仓储只负责 SQL 和 ORM 加载策略，事务边界由 Service
明确控制，避免在一次业务命令中途提交。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.shipment_case import (
    ShipmentCase,
    ShipmentCaseCommand,
    ShipmentCaseResourceBinding,
    ShipmentResourceType,
)


class ShipmentCaseRepository:
    """案件聚合根、资源绑定和只追加事件的统一查询入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _case_options():
        """所有公开案件视图都需要同时加载资源和时间线。"""

        return (
            selectinload(ShipmentCase.resource_bindings),
            selectinload(ShipmentCase.events),
        )

    async def get_by_number(
        self,
        case_number: str,
        *,
        for_update: bool = False,
    ) -> ShipmentCase | None:
        """按稳定案件编号读取完整聚合，可选择锁住案件主行。"""

        statement = (
            select(ShipmentCase)
            .options(*self._case_options())
            .where(ShipmentCase.case_number == case_number)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_by_id(self, case_id: str) -> ShipmentCase | None:
        """按内部 UUID 重新读取完整聚合，供并发冲突恢复使用。"""

        statement = (
            select(ShipmentCase)
            .options(*self._case_options())
            .where(ShipmentCase.id == case_id)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_command_by_idempotency_key_hash(
        self,
        idempotency_key_hash: str,
    ) -> ShipmentCaseCommand | None:
        """按全局幂等键摘要读取已经成功的命令结果。

        命令台账与业务事件分离，所以这里既能找到真正推进过案件的 ``APPLIED``，也能
        找到没有增加 revision 或事件的 ``NO_OP``。
        """

        return await self.session.scalar(
            select(ShipmentCaseCommand).where(
                ShipmentCaseCommand.idempotency_key_hash == idempotency_key_hash
            )
        )

    async def list_all(self) -> list[ShipmentCase]:
        """按最近更新时间返回案件中心当前全部案件。"""

        statement = (
            select(ShipmentCase)
            .options(*self._case_options())
            .order_by(
                ShipmentCase.updated_at.desc(),
                ShipmentCase.created_at.desc(),
                ShipmentCase.case_number.desc(),
            )
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def get_binding_by_resource(
        self,
        resource_type: ShipmentResourceType,
        resource_number: str,
    ) -> ShipmentCaseResourceBinding | None:
        """按全局稳定资源标识检查它是否已经属于另一个案件。"""

        return await self.session.scalar(
            select(ShipmentCaseResourceBinding).where(
                ShipmentCaseResourceBinding.resource_type == resource_type,
                ShipmentCaseResourceBinding.resource_number == resource_number,
            )
        )

    def add(self, value: object) -> None:
        """把新案件、绑定或事件加入当前事务。"""

        self.session.add(value)

    async def flush(self) -> None:
        """提前执行约束，让 Service 能把数据库冲突翻译成领域错误。"""

        await self.session.flush()
