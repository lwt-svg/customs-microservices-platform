"""Shipment Case 数据就绪度的数据库仓储。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.load_planning import LoadPlanningTask
from app.models.shipment_data_readiness import ShipmentCaseDataReadiness


class ShipmentDataReadinessRepository:
    """隔离案件级事实记录和装载任务读取所需的 SQLAlchemy 细节。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_case(
        self,
        case_id: str,
        *,
        for_update: bool = False,
    ) -> ShipmentCaseDataReadiness | None:
        statement = (
            select(ShipmentCaseDataReadiness)
            .where(ShipmentCaseDataReadiness.case_id == case_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_load_task(
        self,
        task_number: str,
    ) -> LoadPlanningTask | None:
        """读取评估所需的箱规、计划数量、托盘规格和来源字段。"""

        statement = (
            select(LoadPlanningTask)
            .options(
                selectinload(LoadPlanningTask.cargo_items),
                selectinload(LoadPlanningTask.pallet_specs),
            )
            .where(LoadPlanningTask.task_number == task_number)
        )
        return await self.session.scalar(statement)

    def add(self, value: ShipmentCaseDataReadiness) -> None:
        self.session.add(value)
