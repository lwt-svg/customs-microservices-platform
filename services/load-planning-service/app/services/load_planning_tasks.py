"""装载任务的领域服务。"""

from app.models.load_planning import CargoItem, LoadPlanningTask, PalletSpec
from app.repositories.load_planning_tasks import (
    DuplicateTaskNumberPersistenceError,
    LoadPlanningTaskRepository,
)
from app.schemas.load_planning import (
    CargoItemCreate,
    LoadPlanningTaskCreate,
    LoadPlanningTaskSummary,
    PalletSpecCreate,
)


class DuplicateTaskNumberError(Exception):
    """调用方重复使用任务编号。"""


def _source_values(payload: CargoItemCreate | PalletSpecCreate) -> dict[str, object]:
    """显式拷贝来源字段，防止后续新增 API 字段时意外写入 ORM。"""

    return {
        "source_type": payload.source_type,
        "source_reference": payload.source_reference,
        "source_locator": payload.source_locator,
        "confidence": payload.confidence,
        "verified": payload.verified,
    }


class LoadPlanningTaskService:
    """组织任务创建、查询和列表摘要规则。"""

    def __init__(self, repository: LoadPlanningTaskRepository) -> None:
        self.repository = repository

    async def create(self, payload: LoadPlanningTaskCreate) -> LoadPlanningTask:
        """创建聚合；安全字段缺失时保持 None，不做任何猜测。"""

        # 前置查询用于提供清晰错误；数据库唯一约束仍负责并发时的最终防线。
        if await self.repository.get_by_number(payload.task_number) is not None:
            raise DuplicateTaskNumberError

        task = LoadPlanningTask(
            task_number=payload.task_number,
            task_name=payload.task_name,
            description=payload.description,
        )
        task.cargo_items = [
            CargoItem(
                item_code=item.item_code,
                sku_code=item.sku_code,
                cargo_name=item.cargo_name,
                quantity=item.quantity,
                length_mm=item.length_mm,
                width_mm=item.width_mm,
                height_mm=item.height_mm,
                gross_weight_kg=item.gross_weight_kg,
                max_stack_layers=item.max_stack_layers,
                max_top_load_kg=item.max_top_load_kg,
                stackable=item.stackable,
                keep_upright=item.keep_upright,
                fragile=item.fragile,
                **_source_values(item),
            )
            for item in payload.cargo_items
        ]
        task.pallet_specs = [
            PalletSpec(
                pallet_code=pallet.pallet_code,
                pallet_name=pallet.pallet_name,
                quantity=pallet.quantity,
                length_mm=pallet.length_mm,
                width_mm=pallet.width_mm,
                height_mm=pallet.height_mm,
                tare_weight_kg=pallet.tare_weight_kg,
                max_load_kg=pallet.max_load_kg,
                max_total_height_mm=pallet.max_total_height_mm,
                **_source_values(pallet),
            )
            for pallet in payload.pallet_specs
        ]
        try:
            return await self.repository.add(task)
        except DuplicateTaskNumberPersistenceError as error:
            raise DuplicateTaskNumberError from error

    async def get(self, task_number: str) -> LoadPlanningTask | None:
        """查询一份任务详情。"""

        return await self.repository.get_by_number(task_number)

    async def list(self) -> list[LoadPlanningTaskSummary]:
        """把 ORM 聚合转换成列表页所需摘要。"""

        tasks = await self.repository.list_all()
        return [
            LoadPlanningTaskSummary(
                id=task.id,
                task_number=task.task_number,
                task_name=task.task_name,
                status=task.status,
                cargo_item_count=len(task.cargo_items),
                cargo_box_count=sum(item.quantity for item in task.cargo_items),
                pallet_spec_count=len(task.pallet_specs),
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            for task in tasks
        ]
