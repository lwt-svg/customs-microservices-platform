"""确定性的单 SKU 装托算法。

本模块刻意不依赖 ORM、Pydantic 或后续章节才会新增的模型。主服务只需把数据库记录
转换成下面两个输入 dataclass，即可调用 :class:`PalletPackingService`。

坐标约定
--------
每个托盘使用自己的局部右手坐标系，单位均为毫米：

* 原点 ``(0, 0, 0)`` 位于托盘左下角在地面的投影点；
* ``x`` 沿托盘 ``length_mm`` 方向，``y`` 沿 ``width_mm`` 方向；
* ``z=0`` 是托盘底面，首层纸箱底面位于 ``z=pallet.height_mm``；
* placement 的 ``x/y/z`` 表示纸箱左下后角，纸箱只能绕 z 轴旋转 0° 或 90°。

算法不是随机搜索：输入相同，候选 pattern、托盘选择、坐标和输出顺序就完全相同。
规则网格是基础 pattern；BALANCED/UTILIZATION 还会尝试由两种 XY 朝向组成的条带混排。
每个托盘只放一个 SKU，任何剩余货物都会显式进入 ``unplaced``，不会被静默丢弃。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from enum import StrEnum

ALGORITHM_VERSION = "DETERMINISTIC_GRID_STRIP_V1"
_ZERO = Decimal(0)
_ONE_BILLION = Decimal(1_000_000_000)
_OUTPUT_QUANTUM = Decimal("0.000001")
_MAX_MIXED_STRIP_ENUMERATIONS = 10_000


class PackingStrategy(StrEnum):
    """装托候选的确定性排序策略。"""

    STABLE = "STABLE"
    BALANCED = "BALANCED"
    UTILIZATION = "UTILIZATION"


def _positive_decimal(value: object, field_name: str) -> Decimal:
    """把调用方常见数字类型统一为 Decimal，并拒绝 bool、NaN 和非正数。"""

    if isinstance(value, bool):
        raise TypeError(f"{field_name} 必须是正数，不能是 bool")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{field_name} 必须是有效数字") from error
    if not number.is_finite() or number <= _ZERO:
        raise ValueError(f"{field_name} 必须是有限正数")
    return number


def _optional_positive_decimal(value: object | None, field_name: str) -> Decimal | None:
    """安全约束允许未知，但一旦提供就必须为有限正数。"""

    if value is None:
        return None
    return _positive_decimal(value, field_name)


def _positive_int(value: object, field_name: str) -> int:
    """数量和层数必须是真正的正整数，避免把 1.5 箱悄悄截断。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} 必须是正整数")
    return value


def _required_code(value: object, field_name: str) -> str:
    code = str(value).strip()
    if not code:
        raise ValueError(f"{field_name} 不能为空")
    return code


@dataclass(frozen=True, slots=True)
class PalletSpecInput:
    """算法所需的最小托盘规格。

    ``max_load_kg`` 表示托盘允许承载的货物净载荷，不含托盘皮重；
    ``max_total_height_mm`` 表示从托盘底面到最高纸箱顶面的总高度。
    两项为 ``None`` 时算法不会猜默认值，该规格会被跳过并产生 warning。
    """

    pallet_code: str
    quantity: int
    length_mm: Decimal
    width_mm: Decimal
    height_mm: Decimal
    tare_weight_kg: Decimal
    max_load_kg: Decimal | None
    max_total_height_mm: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pallet_code", _required_code(self.pallet_code, "pallet_code"))
        object.__setattr__(self, "quantity", _positive_int(self.quantity, "quantity"))
        for field_name in ("length_mm", "width_mm", "height_mm", "tare_weight_kg"):
            object.__setattr__(
                self,
                field_name,
                _positive_decimal(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "max_load_kg",
            _optional_positive_decimal(self.max_load_kg, "max_load_kg"),
        )
        object.__setattr__(
            self,
            "max_total_height_mm",
            _optional_positive_decimal(
                self.max_total_height_mm,
                "max_total_height_mm",
            ),
        )


@dataclass(frozen=True, slots=True)
class CargoSkuInput:
    """一条独立装托的 SKU 箱规；``quantity`` 是外箱数量。"""

    sku_code: str
    quantity: int
    length_mm: Decimal
    width_mm: Decimal
    height_mm: Decimal
    gross_weight_kg: Decimal
    max_stack_layers: int | None
    item_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sku_code", _required_code(self.sku_code, "sku_code"))
        object.__setattr__(self, "quantity", _positive_int(self.quantity, "quantity"))
        for field_name in (
            "length_mm",
            "width_mm",
            "height_mm",
            "gross_weight_kg",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_decimal(getattr(self, field_name), field_name),
            )
        if self.max_stack_layers is not None:
            object.__setattr__(
                self,
                "max_stack_layers",
                _positive_int(self.max_stack_layers, "max_stack_layers"),
            )
        if self.item_code is not None:
            object.__setattr__(
                self,
                "item_code",
                _required_code(self.item_code, "item_code"),
            )


@dataclass(frozen=True, slots=True)
class _Slot:
    """一层 pattern 中的一个可放箱位。"""

    x: Decimal
    y: Decimal
    length: Decimal
    width: Decimal
    rotation_deg: int


@dataclass(frozen=True, slots=True)
class _LayerPattern:
    """一个托盘单层 pattern，slots 之间已经保证不重叠。"""

    pattern_type: str
    strip_axis: str | None
    signature: str
    slots: tuple[_Slot, ...]
    balance_offset: Decimal
    minimum_span_ratio: Decimal

    @property
    def boxes_per_layer(self) -> int:
        return len(self.slots)


@dataclass(frozen=True, slots=True)
class _PlacedBox:
    sequence: int
    layer: int
    x: Decimal
    y: Decimal
    z: Decimal
    length: Decimal
    width: Decimal
    height: Decimal
    rotation_deg: int


@dataclass(frozen=True, slots=True)
class _PackingOption:
    pallet: PalletSpecInput
    pattern: _LayerPattern
    capacity: int
    box_count: int
    layer_count: int
    balance_offset: Decimal


def _floor_ratio(numerator: Decimal, denominator: Decimal) -> int:
    return int((numerator / denominator).to_integral_value(rounding=ROUND_FLOOR))


def _number(value: Decimal) -> float:
    """输出 JSON 友好的稳定小数；内部计算仍全程使用 Decimal。"""

    return float(value.quantize(_OUTPUT_QUANTUM))


def _strategy(value: PackingStrategy | str) -> PackingStrategy:
    if isinstance(value, PackingStrategy):
        return value
    try:
        return PackingStrategy(str(value).strip().upper())
    except ValueError as error:
        choices = ", ".join(item.value for item in PackingStrategy)
        raise ValueError(f"strategy 必须是 {choices} 之一") from error


def _orientation_values(cargo: CargoSkuInput) -> tuple[tuple[Decimal, Decimal, int], ...]:
    """只允许保持竖直后的 XY 旋转；正方形底面无需生成重复朝向。"""

    first = (cargo.length_mm, cargo.width_mm, 0)
    if cargo.length_mm == cargo.width_mm:
        return (first,)
    return (first, (cargo.width_mm, cargo.length_mm, 90))


def _make_pattern(
    *,
    pattern_type: str,
    strip_axis: str | None,
    signature: str,
    slots: list[_Slot],
    pallet: PalletSpecInput,
) -> _LayerPattern:
    if not slots:
        raise RuntimeError("内部错误：空 slots 不能构成 pattern")

    center_x = sum(
        (slot.x + slot.length / 2 for slot in slots),
        start=_ZERO,
    ) / len(slots)
    center_y = sum(
        (slot.y + slot.width / 2 for slot in slots),
        start=_ZERO,
    ) / len(slots)
    # 用两个方向的归一化偏移之和衡量重心偏离；0 表示理论中心。
    balance_offset = (
        abs(center_x - pallet.length_mm / 2) / pallet.length_mm
        + abs(center_y - pallet.width_mm / 2) / pallet.width_mm
    )

    min_x = min(slot.x for slot in slots)
    max_x = max(slot.x + slot.length for slot in slots)
    min_y = min(slot.y for slot in slots)
    max_y = max(slot.y + slot.width for slot in slots)
    minimum_span_ratio = min(
        (max_x - min_x) / pallet.length_mm,
        (max_y - min_y) / pallet.width_mm,
    )
    return _LayerPattern(
        pattern_type=pattern_type,
        strip_axis=strip_axis,
        signature=signature,
        slots=tuple(slots),
        balance_offset=balance_offset,
        minimum_span_ratio=minimum_span_ratio,
    )


def _regular_patterns(
    pallet: PalletSpecInput,
    cargo: CargoSkuInput,
) -> list[_LayerPattern]:
    """生成两种可能朝向的居中规则网格。"""

    patterns: list[_LayerPattern] = []
    for box_length, box_width, rotation in _orientation_values(cargo):
        count_x = _floor_ratio(pallet.length_mm, box_length)
        count_y = _floor_ratio(pallet.width_mm, box_width)
        if count_x <= 0 or count_y <= 0:
            continue

        used_length = box_length * count_x
        used_width = box_width * count_y
        offset_x = (pallet.length_mm - used_length) / 2
        offset_y = (pallet.width_mm - used_width) / 2
        slots = [
            _Slot(
                x=offset_x + box_length * x_index,
                y=offset_y + box_width * y_index,
                length=box_length,
                width=box_width,
                rotation_deg=rotation,
            )
            for y_index in range(count_y)
            for x_index in range(count_x)
        ]
        patterns.append(
            _make_pattern(
                pattern_type="GRID",
                strip_axis=None,
                signature=f"GRID-R{rotation}-X{count_x}-Y{count_y}",
                slots=slots,
                pallet=pallet,
            )
        )
    return patterns


def _balanced_strip_sequence(count_a: int, count_b: int) -> tuple[int, ...]:
    """把两种条带尽量均匀穿插，避免把所有旋转箱都堆到托盘同一侧。"""

    sequence: list[int] = []
    used_a = 0
    used_b = 0
    while used_a < count_a or used_b < count_b:
        if used_a >= count_a:
            sequence.append(1)
            used_b += 1
            continue
        if used_b >= count_b:
            sequence.append(0)
            used_a += 1
            continue

        next_a_position = (Decimal(used_a) + Decimal("0.5")) / count_a
        next_b_position = (Decimal(used_b) + Decimal("0.5")) / count_b
        if next_a_position <= next_b_position:
            sequence.append(0)
            used_a += 1
        else:
            sequence.append(1)
            used_b += 1
    return tuple(sequence)


def _mixed_strip_pattern(
    pallet: PalletSpecInput,
    orientations: tuple[tuple[Decimal, Decimal, int], ...],
    strip_axis: str,
    count_a: int,
    count_b: int,
) -> _LayerPattern | None:
    """构造一个混排条带。

    ``strip_axis='x'`` 表示沿 x 方向并排多个竖条，每个竖条内部沿 y 填箱；
    ``strip_axis='y'`` 则沿 y 方向并排横条，每个横条内部沿 x 填箱。
    """

    sequence = _balanced_strip_sequence(count_a, count_b)
    if strip_axis == "x":
        used_axis = sum((orientations[index][0] for index in sequence), start=_ZERO)
        cursor = (pallet.length_mm - used_axis) / 2
        slots: list[_Slot] = []
        for orientation_index in sequence:
            box_length, box_width, rotation = orientations[orientation_index]
            box_count = _floor_ratio(pallet.width_mm, box_width)
            if box_count <= 0:
                return None
            offset_y = (pallet.width_mm - box_width * box_count) / 2
            slots.extend(
                _Slot(
                    x=cursor,
                    y=offset_y + box_width * index,
                    length=box_length,
                    width=box_width,
                    rotation_deg=rotation,
                )
                for index in range(box_count)
            )
            cursor += box_length
    elif strip_axis == "y":
        used_axis = sum((orientations[index][1] for index in sequence), start=_ZERO)
        cursor = (pallet.width_mm - used_axis) / 2
        slots = []
        for orientation_index in sequence:
            box_length, box_width, rotation = orientations[orientation_index]
            box_count = _floor_ratio(pallet.length_mm, box_length)
            if box_count <= 0:
                return None
            offset_x = (pallet.length_mm - box_length * box_count) / 2
            slots.extend(
                _Slot(
                    x=offset_x + box_length * index,
                    y=cursor,
                    length=box_length,
                    width=box_width,
                    rotation_deg=rotation,
                )
                for index in range(box_count)
            )
            cursor += box_width
    else:
        raise RuntimeError(f"内部错误：未知 strip_axis={strip_axis}")

    return _make_pattern(
        pattern_type="STRIP_MIXED",
        strip_axis=strip_axis,
        signature=f"STRIP-{strip_axis.upper()}-A{count_a}-B{count_b}",
        slots=slots,
        pallet=pallet,
    )


def _mixed_strip_patterns(
    pallet: PalletSpecInput,
    cargo: CargoSkuInput,
) -> tuple[list[_LayerPattern], bool]:
    """枚举非劣的条带混排；极端微小尺寸时跳过，防止调用线程被超大循环占满。"""

    orientations = _orientation_values(cargo)
    if len(orientations) < 2:
        return [], False

    patterns: list[_LayerPattern] = []
    skipped_for_size = False
    for strip_axis in ("x", "y"):
        axis_size = pallet.length_mm if strip_axis == "x" else pallet.width_mm
        thickness_a = orientations[0][0 if strip_axis == "x" else 1]
        thickness_b = orientations[1][0 if strip_axis == "x" else 1]
        max_a = _floor_ratio(axis_size, thickness_a)
        max_b = _floor_ratio(axis_size, thickness_b)
        if max_a + max_b > _MAX_MIXED_STRIP_ENUMERATIONS:
            skipped_for_size = True
            continue

        # 固定 A 条带数后，B 取剩余空间可容纳的最大值。更少的 B 箱位数更低且更空，
        # 在本算法的三个策略中都被支配，因此无需重复加入候选。
        for count_a in range(1, max_a + 1):
            residual = axis_size - thickness_a * count_a
            count_b = _floor_ratio(residual, thickness_b)
            if count_b <= 0:
                continue
            pattern = _mixed_strip_pattern(
                pallet,
                orientations,
                strip_axis,
                count_a,
                count_b,
            )
            if pattern is not None:
                patterns.append(pattern)
    return patterns, skipped_for_size


def _select_pattern(
    patterns: Sequence[_LayerPattern],
    strategy: PackingStrategy,
) -> _LayerPattern | None:
    if not patterns:
        return None

    if strategy is PackingStrategy.STABLE:
        # 稳定策略优先结构简单、现场容易复核的规则网格，再比较容量和覆盖跨度。
        return min(
            patterns,
            key=lambda item: (
                0 if item.pattern_type == "GRID" else 1,
                -item.boxes_per_layer,
                -item.minimum_span_ratio,
                item.balance_offset,
                item.signature,
            ),
        )
    if strategy is PackingStrategy.BALANCED:
        # 平衡策略不牺牲单层箱数，在同容量候选中优先理论重心更接近托盘中心者。
        return min(
            patterns,
            key=lambda item: (
                -item.boxes_per_layer,
                item.balance_offset,
                0 if item.pattern_type == "GRID" else 1,
                -item.minimum_span_ratio,
                item.signature,
            ),
        )

    # 利用率策略首先最大化单层箱数；同容量时优先混排，便于明确展示混排收益。
    return min(
        patterns,
        key=lambda item: (
            -item.boxes_per_layer,
            0 if item.pattern_type == "STRIP_MIXED" else 1,
            item.balance_offset,
            item.signature,
        ),
    )


def _ordered_slots(
    pattern: _LayerPattern,
    pallet: PalletSpecInput,
) -> tuple[_Slot, ...]:
    """从中心向外使用箱位，使最后一个不满层也尽量保持居中。"""

    center_x = pallet.length_mm / 2
    center_y = pallet.width_mm / 2
    return tuple(
        sorted(
            pattern.slots,
            key=lambda slot: (
                (slot.x + slot.length / 2 - center_x) ** 2
                + (slot.y + slot.width / 2 - center_y) ** 2,
                slot.y,
                slot.x,
                slot.rotation_deg,
            ),
        )
    )


def _placements_for(
    pallet: PalletSpecInput,
    cargo: CargoSkuInput,
    pattern: _LayerPattern,
    box_count: int,
) -> list[_PlacedBox]:
    slots = _ordered_slots(pattern, pallet)
    placements: list[_PlacedBox] = []
    for index in range(box_count):
        layer_index = index // pattern.boxes_per_layer
        slot = slots[index % pattern.boxes_per_layer]
        placements.append(
            _PlacedBox(
                sequence=index + 1,
                layer=layer_index + 1,
                x=slot.x,
                y=slot.y,
                z=pallet.height_mm + cargo.height_mm * layer_index,
                length=slot.length,
                width=slot.width,
                height=cargo.height_mm,
                rotation_deg=slot.rotation_deg,
            )
        )
    return placements


def _placement_balance(
    placements: Sequence[_PlacedBox],
    pallet: PalletSpecInput,
) -> Decimal:
    if not placements:
        return Decimal("Infinity")
    center_x = sum(
        (placement.x + placement.length / 2 for placement in placements),
        start=_ZERO,
    ) / len(placements)
    center_y = sum(
        (placement.y + placement.width / 2 for placement in placements),
        start=_ZERO,
    ) / len(placements)
    return (
        abs(center_x - pallet.length_mm / 2) / pallet.length_mm
        + abs(center_y - pallet.width_mm / 2) / pallet.width_mm
    )


def _build_option(
    pallet: PalletSpecInput,
    cargo: CargoSkuInput,
    pattern: _LayerPattern | None,
    remaining_boxes: int,
    strategy: PackingStrategy,
) -> _PackingOption | None:
    """合并策略堆高、物理层数、高度和载重上限，得到单托盘真实容量。"""

    if (
        pattern is None
        or cargo.max_stack_layers is None
        or pallet.max_load_kg is None
        or pallet.max_total_height_mm is None
    ):
        return None

    usable_height = pallet.max_total_height_mm - pallet.height_mm
    if usable_height < cargo.height_mm:
        return None
    height_layer_limit = _floor_ratio(usable_height, cargo.height_mm)
    layer_limit = min(cargo.max_stack_layers, height_layer_limit)
    if layer_limit <= 0:
        return None

    # 三种策略都不突破物理上限，只对“愿意用到多少层”作不同取舍：
    # STABLE 保留约一半堆高余量，BALANCED 使用约四分之三，UTILIZATION 用满允许层数。
    if strategy is PackingStrategy.STABLE:
        strategy_layer_limit = max(1, (layer_limit + 1) // 2)
    elif strategy is PackingStrategy.BALANCED:
        strategy_layer_limit = max(1, (3 * layer_limit + 3) // 4)
    else:
        strategy_layer_limit = layer_limit

    weight_box_limit = _floor_ratio(pallet.max_load_kg, cargo.gross_weight_kg)
    capacity = min(
        pattern.boxes_per_layer * strategy_layer_limit,
        weight_box_limit,
    )
    if capacity <= 0:
        return None

    box_count = min(remaining_boxes, capacity)
    layer_count = (box_count + pattern.boxes_per_layer - 1) // pattern.boxes_per_layer
    placements = _placements_for(pallet, cargo, pattern, box_count)
    return _PackingOption(
        pallet=pallet,
        pattern=pattern,
        capacity=capacity,
        box_count=box_count,
        layer_count=layer_count,
        balance_offset=_placement_balance(placements, pallet),
    )


def _select_option(
    options: Sequence[_PackingOption],
    strategy: PackingStrategy,
) -> _PackingOption:
    """在多种仍有库存的托盘规格之间作确定性选择。"""

    if strategy is PackingStrategy.STABLE:
        return min(
            options,
            key=lambda item: (
                -item.box_count,
                item.layer_count,
                0 if item.pattern.pattern_type == "GRID" else 1,
                -item.pattern.minimum_span_ratio,
                item.balance_offset,
                item.pallet.pallet_code,
            ),
        )
    if strategy is PackingStrategy.BALANCED:
        return min(
            options,
            key=lambda item: (
                -item.box_count,
                item.balance_offset,
                item.layer_count,
                -item.capacity,
                item.pallet.pallet_code,
            ),
        )
    return min(
        options,
        key=lambda item: (
            -item.box_count,
            -item.capacity,
            -item.pattern.boxes_per_layer,
            item.balance_offset,
            item.pallet.pallet_code,
        ),
    )


def _rectangles_overlap(first: _PlacedBox, second: _PlacedBox) -> bool:
    return (
        first.x < second.x + second.length
        and second.x < first.x + first.length
        and first.y < second.y + second.width
        and second.y < first.y + first.width
    )


def _validate_layout(
    *,
    pallet: PalletSpecInput,
    cargo: CargoSkuInput,
    placements: Sequence[_PlacedBox],
    layer_count: int,
) -> None:
    """对算法自己的输出再做一次硬约束审计。

    这是内部不变量检查；一旦失败说明实现存在 bug，因此抛 RuntimeError，而不是把一个
    可能越界或碰撞的计划交给调用方。
    """

    if (
        cargo.max_stack_layers is None
        or pallet.max_load_kg is None
        or pallet.max_total_height_mm is None
    ):
        raise RuntimeError("内部错误：缺少安全约束时不应生成 placements")
    if layer_count > cargo.max_stack_layers:
        raise RuntimeError("内部错误：输出超过 max_stack_layers")

    payload_weight = cargo.gross_weight_kg * len(placements)
    if payload_weight > pallet.max_load_kg:
        raise RuntimeError("内部错误：输出超过 max_load_kg")

    by_layer: dict[int, list[_PlacedBox]] = {}
    for placement in placements:
        if (
            placement.x < _ZERO
            or placement.y < _ZERO
            or placement.z < pallet.height_mm
            or placement.x + placement.length > pallet.length_mm
            or placement.y + placement.width > pallet.width_mm
            or placement.z + placement.height > pallet.max_total_height_mm
        ):
            raise RuntimeError("内部错误：placement 越出托盘或最大高度边界")
        by_layer.setdefault(placement.layer, []).append(placement)

    # 同层使用 x 方向扫线：只有 x 投影相交的矩形才继续检查 y 投影。
    for layer_placements in by_layer.values():
        ordered = sorted(layer_placements, key=lambda item: (item.x, item.y, item.sequence))
        for index, current in enumerate(ordered):
            for other in ordered[index + 1 :]:
                if other.x >= current.x + current.length:
                    break
                if _rectangles_overlap(current, other):
                    raise RuntimeError("内部错误：同层 placements 发生碰撞")


def _warning(
    warnings: list[dict[str, object]],
    warning_keys: set[tuple[str, str | None, str | None]],
    *,
    code: str,
    message: str,
    sku_code: str | None = None,
    pallet_code: str | None = None,
) -> None:
    key = (code, sku_code, pallet_code)
    if key in warning_keys:
        return
    warning_keys.add(key)
    item: dict[str, object] = {"code": code, "message": message}
    if sku_code is not None:
        item["sku_code"] = sku_code
    if pallet_code is not None:
        item["pallet_code"] = pallet_code
    warnings.append(item)


def _orientation_summary(pattern: _LayerPattern) -> list[dict[str, int]]:
    counts: dict[int, int] = {}
    for slot in pattern.slots:
        counts[slot.rotation_deg] = counts.get(slot.rotation_deg, 0) + 1
    return [
        {"rotation_deg": rotation, "box_count": counts[rotation]} for rotation in sorted(counts)
    ]


def _placement_dict(
    placement: _PlacedBox,
    *,
    pallet_id: str,
    cargo: CargoSkuInput,
) -> dict[str, object]:
    return {
        "placement_id": f"{pallet_id}-BOX-{placement.sequence:05d}",
        "pallet_id": pallet_id,
        "sku_code": cargo.sku_code,
        "item_code": cargo.item_code,
        "sequence": placement.sequence,
        "layer": placement.layer,
        "x_mm": _number(placement.x),
        "y_mm": _number(placement.y),
        "z_mm": _number(placement.z),
        "length_mm": _number(placement.length),
        "width_mm": _number(placement.width),
        "height_mm": _number(placement.height),
        "rotation_deg": placement.rotation_deg,
    }


class PalletPackingService:
    """无状态、可直接单元测试的确定性装托服务。"""

    def plan(
        self,
        pallet_specs: Sequence[PalletSpecInput],
        cargo_skus: Sequence[CargoSkuInput],
        strategy: PackingStrategy | str,
    ) -> dict[str, object]:
        """计算一个策略的计划，并返回完全由 JSON 基础类型组成的 dict。"""

        selected_strategy = _strategy(strategy)
        pallets = sorted(pallet_specs, key=lambda item: item.pallet_code)
        cargo_items = sorted(cargo_skus, key=lambda item: item.sku_code)
        self._validate_unique_codes(pallets, cargo_items)

        warnings: list[dict[str, object]] = []
        warning_keys: set[tuple[str, str | None, str | None]] = set()
        unplaced: list[dict[str, object]] = []
        pallet_results: list[dict[str, object]] = []
        flat_placements: list[dict[str, object]] = []
        inventory = {pallet.pallet_code: pallet.quantity for pallet in pallets}
        used_by_code = {pallet.pallet_code: 0 for pallet in pallets}
        pattern_cache: dict[tuple[str, str], _LayerPattern | None] = {}

        for pallet in pallets:
            missing = []
            if pallet.max_load_kg is None:
                missing.append("max_load_kg")
            if pallet.max_total_height_mm is None:
                missing.append("max_total_height_mm")
            if missing:
                _warning(
                    warnings,
                    warning_keys,
                    code="PALLET_SAFETY_CONSTRAINT_MISSING",
                    message=f"托盘缺少 {', '.join(missing)}，算法不会猜测默认值",
                    pallet_code=pallet.pallet_code,
                )

        for cargo in cargo_items:
            if cargo.max_stack_layers is None:
                _warning(
                    warnings,
                    warning_keys,
                    code="CARGO_MAX_STACK_LAYERS_MISSING",
                    message="SKU 缺少 max_stack_layers，无法生成安全装托坐标",
                    sku_code=cargo.sku_code,
                )
                unplaced.append(
                    {
                        "sku_code": cargo.sku_code,
                        "item_code": cargo.item_code,
                        "quantity": cargo.quantity,
                        "reason": "MISSING_MAX_STACK_LAYERS",
                    }
                )
                continue

            remaining = cargo.quantity
            while remaining > 0:
                available_options: list[_PackingOption] = []
                compatible_without_inventory = False
                for pallet in pallets:
                    pattern = self._pattern_for(
                        pallet=pallet,
                        cargo=cargo,
                        strategy=selected_strategy,
                        cache=pattern_cache,
                        warnings=warnings,
                        warning_keys=warning_keys,
                    )
                    option = _build_option(
                        pallet,
                        cargo,
                        pattern,
                        remaining,
                        selected_strategy,
                    )
                    if option is None:
                        continue
                    compatible_without_inventory = True
                    if inventory[pallet.pallet_code] > 0:
                        available_options.append(option)

                if not available_options:
                    reason = (
                        "PALLET_INVENTORY_EXHAUSTED"
                        if compatible_without_inventory
                        else "NO_COMPATIBLE_PALLET"
                    )
                    message = (
                        "可兼容托盘数量不足，剩余外箱未装载"
                        if compatible_without_inventory
                        else "没有同时满足箱体尺寸、层数、高度和载重约束的托盘"
                    )
                    _warning(
                        warnings,
                        warning_keys,
                        code=reason,
                        message=message,
                        sku_code=cargo.sku_code,
                    )
                    unplaced.append(
                        {
                            "sku_code": cargo.sku_code,
                            "item_code": cargo.item_code,
                            "quantity": remaining,
                            "reason": reason,
                        }
                    )
                    break

                option = _select_option(available_options, selected_strategy)
                pallet = option.pallet
                inventory[pallet.pallet_code] -= 1
                used_by_code[pallet.pallet_code] += 1
                pallet_id = f"{pallet.pallet_code}-{used_by_code[pallet.pallet_code]:04d}"
                placements = _placements_for(
                    pallet,
                    cargo,
                    option.pattern,
                    option.box_count,
                )
                _validate_layout(
                    pallet=pallet,
                    cargo=cargo,
                    placements=placements,
                    layer_count=option.layer_count,
                )

                placement_dicts = [
                    _placement_dict(
                        placement,
                        pallet_id=pallet_id,
                        cargo=cargo,
                    )
                    for placement in placements
                ]
                flat_placements.extend(placement_dicts)
                payload_weight = cargo.gross_weight_kg * option.box_count
                total_height = pallet.height_mm + cargo.height_mm * option.layer_count
                deck_area = pallet.length_mm * pallet.width_mm
                used_layer_area = cargo.length_mm * cargo.width_mm * option.box_count
                available_layer_area = deck_area * option.layer_count
                pallet_results.append(
                    {
                        "pallet_id": pallet_id,
                        "pallet_code": pallet.pallet_code,
                        "sku_code": cargo.sku_code,
                        "item_code": cargo.item_code,
                        "box_count": option.box_count,
                        "layer_count": option.layer_count,
                        "payload_weight_kg": _number(payload_weight),
                        "gross_weight_kg": _number(payload_weight + pallet.tare_weight_kg),
                        "total_height_mm": _number(total_height),
                        "capacity_box_count": option.capacity,
                        "utilization": {
                            "layer_area_ratio": _number(used_layer_area / available_layer_area),
                            "payload_weight_ratio": _number(payload_weight / pallet.max_load_kg),
                            "height_ratio": _number(total_height / pallet.max_total_height_mm),
                            "balance_offset": _number(option.balance_offset),
                        },
                        "pattern": {
                            "type": option.pattern.pattern_type,
                            "strip_axis": option.pattern.strip_axis,
                            "signature": option.pattern.signature,
                            "boxes_per_full_layer": option.pattern.boxes_per_layer,
                            "orientations": _orientation_summary(option.pattern),
                        },
                        "placements": placement_dicts,
                    }
                )
                remaining -= option.box_count

        total_input_boxes = sum(item.quantity for item in cargo_items)
        placed_boxes = len(flat_placements)
        used_pallets = len(pallet_results)
        available_pallets = sum(item.quantity for item in pallets)
        payload_weight = sum(
            (
                item.gross_weight_kg
                * sum(1 for placement in flat_placements if placement["sku_code"] == item.sku_code)
                for item in cargo_items
            ),
            start=_ZERO,
        )
        placed_volume = sum(
            (
                item.length_mm
                * item.width_mm
                * item.height_mm
                * sum(1 for placement in flat_placements if placement["sku_code"] == item.sku_code)
                for item in cargo_items
            ),
            start=_ZERO,
        )
        total_used_layer_area = sum(
            (
                Decimal(str(result["utilization"]["layer_area_ratio"]))
                * next(
                    pallet.length_mm * pallet.width_mm
                    for pallet in pallets
                    if pallet.pallet_code == result["pallet_code"]
                )
                * int(result["layer_count"])
                for result in pallet_results
            ),
            start=_ZERO,
        )
        total_available_layer_area = sum(
            (
                next(
                    pallet.length_mm * pallet.width_mm
                    for pallet in pallets
                    if pallet.pallet_code == result["pallet_code"]
                )
                * int(result["layer_count"])
                for result in pallet_results
            ),
            start=_ZERO,
        )

        return {
            "algorithm_version": ALGORITHM_VERSION,
            "strategy": selected_strategy.value,
            "feasible": placed_boxes == total_input_boxes,
            "units": {"dimension": "mm", "weight": "kg"},
            "metrics": {
                "input_sku_count": len(cargo_items),
                "input_box_count": total_input_boxes,
                "placed_box_count": placed_boxes,
                "unplaced_box_count": total_input_boxes - placed_boxes,
                "available_pallet_count": available_pallets,
                "used_pallet_count": used_pallets,
                "remaining_pallet_count": available_pallets - used_pallets,
                "pallet_usage_ratio": _number(
                    Decimal(used_pallets) / available_pallets if available_pallets else _ZERO
                ),
                "payload_weight_kg": _number(payload_weight),
                "placed_cargo_volume_m3": _number(placed_volume / _ONE_BILLION),
                "average_layer_area_utilization": _number(
                    total_used_layer_area / total_available_layer_area
                    if total_available_layer_area
                    else _ZERO
                ),
            },
            "warnings": warnings,
            "pallets": pallet_results,
            # 顶层扁平列表方便前端一次绘制；每个 pallet 内也保留同一组 placement。
            "placements": flat_placements,
            "unplaced": unplaced,
        }

    def plan_all(
        self,
        pallet_specs: Sequence[PalletSpecInput],
        cargo_skus: Sequence[CargoSkuInput],
    ) -> dict[str, dict[str, object]]:
        """一次返回三种策略，键名固定为 STABLE/BALANCED/UTILIZATION。"""

        return {
            strategy.value: self.plan(pallet_specs, cargo_skus, strategy)
            for strategy in PackingStrategy
        }

    @staticmethod
    def _validate_unique_codes(
        pallets: Sequence[PalletSpecInput],
        cargo_items: Sequence[CargoSkuInput],
    ) -> None:
        pallet_codes = [item.pallet_code for item in pallets]
        if len(pallet_codes) != len(set(pallet_codes)):
            raise ValueError("pallet_code 必须唯一，否则无法确定库存归属")
        sku_codes = [item.sku_code for item in cargo_items]
        if len(sku_codes) != len(set(sku_codes)):
            raise ValueError("sku_code 必须唯一；同 SKU 请先聚合 quantity")

    @staticmethod
    def _pattern_for(
        *,
        pallet: PalletSpecInput,
        cargo: CargoSkuInput,
        strategy: PackingStrategy,
        cache: dict[tuple[str, str], _LayerPattern | None],
        warnings: list[dict[str, object]],
        warning_keys: set[tuple[str, str | None, str | None]],
    ) -> _LayerPattern | None:
        cache_key = (pallet.pallet_code, cargo.sku_code)
        if cache_key in cache:
            return cache[cache_key]

        patterns = _regular_patterns(pallet, cargo)
        if strategy is not PackingStrategy.STABLE:
            mixed, skipped = _mixed_strip_patterns(pallet, cargo)
            patterns.extend(mixed)
            if skipped:
                _warning(
                    warnings,
                    warning_keys,
                    code="MIXED_PATTERN_SEARCH_SKIPPED",
                    message="条带数量超过确定性枚举保护上限，本次仅使用规则网格候选",
                    sku_code=cargo.sku_code,
                    pallet_code=pallet.pallet_code,
                )
        cache[cache_key] = _select_pattern(patterns, strategy)
        return cache[cache_key]


def plan_pallet_packing(
    pallet_specs: Sequence[PalletSpecInput],
    cargo_skus: Sequence[CargoSkuInput],
    strategy: PackingStrategy | str,
) -> dict[str, object]:
    """函数式便捷入口；适合 FastAPI Service 直接调用。"""

    return PalletPackingService().plan(pallet_specs, cargo_skus, strategy)


def plan_all_pallet_strategies(
    pallet_specs: Sequence[PalletSpecInput],
    cargo_skus: Sequence[CargoSkuInput],
) -> dict[str, dict[str, object]]:
    """函数式便捷入口；一次计算三种策略。"""

    return PalletPackingService().plan_all(pallet_specs, cargo_skus)
