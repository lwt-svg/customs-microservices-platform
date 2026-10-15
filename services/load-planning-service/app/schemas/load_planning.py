"""装载任务 API 的输入输出约束。

字段名直接带 ``_mm`` 或 ``_kg``，调用方不能传入含糊的 ``length``、``weight`` 后再
让服务猜单位。Pydantic 负责 HTTP 边界校验，数据库约束负责最后一道持久化防线。
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.load_planning import LoadPlanningTaskStatus, SourceType


class SourceTraceInput(BaseModel):
    """每条主数据的来源和可信度。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_type: SourceType = Field(description="数据来源类型")
    source_reference: str | None = Field(
        default=None,
        max_length=255,
        description="来源文件或人工确认记录的稳定名称；禁止填写公开下载地址",
    )
    source_locator: str | None = Field(
        default=None,
        max_length=255,
        description="页码、Sheet/行号或单元格等定位信息",
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="解析置信度；人工录入且未评估时可为空",
    )
    verified: bool = Field(
        default=False,
        description="是否已由有权限的业务人员核对；历史资料导入时默认 false",
    )

    @model_validator(mode="after")
    def require_import_trace(self) -> Self:
        """从文件得到的数据必须能回到原始位置，避免出现无来源的“真值”。"""

        if self.source_type in {
            SourceType.ENTERPRISE_HISTORICAL,
            SourceType.DOCUMENT_IMPORT,
        } and (not self.source_reference or not self.source_locator):
            raise ValueError("文件来源数据必须同时填写 source_reference 和 source_locator")
        if self.source_type is SourceType.SYNTHETIC_DEMO and self.verified:
            raise ValueError("SYNTHETIC_DEMO 构造数据不能标记为已核实正式数据")
        return self


class CargoItemCreate(SourceTraceInput):
    """创建任务时提交的一条货物箱规。"""

    item_code: str = Field(min_length=1, max_length=64, examples=["ITEM-001"])
    sku_code: str | None = Field(default=None, max_length=128, examples=["DEMO-SKU-A"])
    cargo_name: str = Field(min_length=1, max_length=255, examples=["演示产品 A"])
    quantity: int = Field(gt=0, le=1_000_000, description="相同外箱的箱数")
    length_mm: float = Field(gt=0, le=1_000_000, description="外箱长度，单位 mm")
    width_mm: float = Field(gt=0, le=1_000_000, description="外箱宽度，单位 mm")
    height_mm: float = Field(gt=0, le=1_000_000, description="外箱高度，单位 mm")
    gross_weight_kg: float = Field(gt=0, le=1_000_000, description="单箱毛重，单位 kg")

    # 不给安全字段设置 False、0 或经验默认值。None 会进入后续“缺失约束询问”流程。
    max_stack_layers: int | None = Field(default=None, gt=0, le=10_000)
    max_top_load_kg: float | None = Field(default=None, gt=0, le=1_000_000)
    stackable: bool | None = None
    keep_upright: bool | None = None
    fragile: bool | None = None


class PalletSpecCreate(SourceTraceInput):
    """创建任务时提交的一种候选托盘规格。"""

    pallet_code: str = Field(min_length=1, max_length=64, examples=["PALLET-1200X800"])
    pallet_name: str = Field(min_length=1, max_length=128, examples=["1200×800 熏蒸托盘"])
    quantity: int = Field(gt=0, le=100_000, description="可用托盘数量")
    length_mm: float = Field(gt=0, le=100_000, description="托盘长度，单位 mm")
    width_mm: float = Field(gt=0, le=100_000, description="托盘宽度，单位 mm")
    height_mm: float = Field(gt=0, le=100_000, description="空托盘高度，单位 mm")
    tare_weight_kg: float = Field(gt=0, le=100_000, description="托盘皮重，单位 kg")
    max_load_kg: float | None = Field(
        default=None,
        gt=0,
        le=1_000_000,
        description="托盘最大载重；未知时必须为空",
    )
    max_total_height_mm: float | None = Field(
        default=None,
        gt=0,
        le=1_000_000,
        description="含货成托最大高度；未知时必须为空",
    )


class LoadPlanningTaskCreate(BaseModel):
    """一次性创建任务及其多条箱规、托盘规格。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    task_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^LP-[A-Za-z0-9_-]+$",
        examples=["LP-DEMO-0001"],
    )
    task_name: str = Field(min_length=1, max_length=128, examples=["演示装托验证"])
    description: str | None = Field(default=None, max_length=2_000)
    cargo_items: list[CargoItemCreate] = Field(min_length=1, max_length=1_000)
    pallet_specs: list[PalletSpecCreate] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_codes(self) -> Self:
        """任务内业务编码必须唯一，避免算法输出无法定位到具体输入行。"""

        item_codes = [item.item_code for item in self.cargo_items]
        if len(item_codes) != len(set(item_codes)):
            raise ValueError("同一任务内 item_code 不能重复")
        pallet_codes = [pallet.pallet_code for pallet in self.pallet_specs]
        if len(pallet_codes) != len(set(pallet_codes)):
            raise ValueError("同一任务内 pallet_code 不能重复")
        return self


class SourceTraceRead(BaseModel):
    """返回给前端的来源追踪字段。"""

    model_config = ConfigDict(from_attributes=True)

    source_type: SourceType
    source_reference: str | None
    source_locator: str | None
    confidence: float | None
    verified: bool


class CargoItemRead(SourceTraceRead):
    """货物箱规输出。"""

    id: int
    item_code: str
    sku_code: str | None
    cargo_name: str
    quantity: int
    length_mm: float
    width_mm: float
    height_mm: float
    gross_weight_kg: float
    max_stack_layers: int | None
    max_top_load_kg: float | None
    stackable: bool | None
    keep_upright: bool | None
    fragile: bool | None


class PalletSpecRead(SourceTraceRead):
    """托盘规格输出。"""

    id: int
    pallet_code: str
    pallet_name: str
    quantity: int
    length_mm: float
    width_mm: float
    height_mm: float
    tare_weight_kg: float
    max_load_kg: float | None
    max_total_height_mm: float | None


class UnitConvention(BaseModel):
    """API 固定单位声明；避免页面把 1200 mm 误当成 1200 cm。"""

    dimension: str = "mm"
    weight: str = "kg"


class LoadPlanningTaskRead(BaseModel):
    """任务详情，包含本次算法所需的全部输入。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_number: str
    task_name: str
    description: str | None
    status: LoadPlanningTaskStatus
    units: UnitConvention = Field(default_factory=UnitConvention)
    cargo_items: list[CargoItemRead]
    pallet_specs: list[PalletSpecRead]
    created_at: datetime
    updated_at: datetime


class LoadPlanningTaskSummary(BaseModel):
    """列表页只返回摘要，避免一次拉取大量箱规明细。"""

    id: int
    task_number: str
    task_name: str
    status: LoadPlanningTaskStatus
    cargo_item_count: int
    cargo_box_count: int
    pallet_spec_count: int
    created_at: datetime
    updated_at: datetime
