"""Packing List 坐标解析预览的输入输出模型。"""

from pydantic import BaseModel, Field


class PackingListCargoCandidate(BaseModel):
    """由一个或多个商品行聚合得到的唯一箱规候选。"""

    cargo_key: str = Field(description="只由箱规数值生成的稳定关联键")
    item_code: str = Field(description="首个代表商品编码")
    item_codes: list[str] = Field(description="共享该箱规的全部商品编码")
    description: str = Field(description="首个代表商品描述")
    carton_quantity: int = Field(ge=0, description="相同箱规商品行的合计箱数")
    units_per_carton: int = Field(gt=0)
    carton_net_weight_kg: float = Field(gt=0)
    carton_gross_weight_kg: float = Field(gt=0)
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    source_locator: str = Field(
        max_length=500,
        description="页码和 y 坐标，不包含商品原文",
    )
    confidence: float = Field(ge=0, le=1)


class HistoricalPalletPattern(BaseModel):
    """历史文件中的一种装托批次/高度组合，不代表一条逐托三维坐标。"""

    cargo_key: str | None = Field(description="关联箱规；无法可靠关联时为空并进入人工复核")
    cartons_per_layer: int = Field(gt=0)
    pattern_carton_count: int = Field(gt=0)
    pallet_quantity: int | None = Field(
        default=None,
        gt=0,
        description="该模式覆盖的托盘数量，不等于模式行数",
    )
    pallet_tare_weight_kg: float = Field(gt=0)
    loaded_length_mm: float = Field(gt=0)
    loaded_width_mm: float = Field(gt=0)
    loaded_height_mm: float = Field(gt=0)
    cbm: float = Field(gt=0)
    source_locator: str = Field(
        max_length=255,
        description="模式行页码和 y 坐标，不包含原文",
    )


class PackingListPreview(BaseModel):
    """RULES_V3 预览结果；调用方复核后才能转成正式装载任务。"""

    parser_version: str
    declared_total_pallets: int | None = Field(default=None, ge=0)
    declared_total_cbm: float | None = Field(default=None, ge=0)
    cargo_candidates: list[PackingListCargoCandidate] = Field(default_factory=list)
    historical_pallet_patterns: list[HistoricalPalletPattern] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
