"""坐标感知的文本型 Packing List 解析器。

RULES_V3 不再先把整页文字拼成一行再写正则，而是使用 pypdf ``visitor_text`` 保存每个
文字片段的 x/y 坐标。相邻列即使视觉距离很近，也会先按列分别解析，避免把
``5.0000`` 和 ``40*30*20cm`` 这类相邻构造值粘成一个错误数字。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from typing import ClassVar

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.schemas.packing_list_import import (
    HistoricalPalletPattern,
    PackingListCargoCandidate,
    PackingListPreview,
)


class UnreadablePackingListPdfError(Exception):
    """PDF 损坏、加密、空白或没有可识别的装载结构。"""


@dataclass(frozen=True, slots=True)
class TextCell:
    """一个保留页面位置的短文本片段。"""

    page_number: int
    x: float
    y: float
    text: str


@dataclass(slots=True)
class TextRow:
    """同一页、近似相同 y 坐标的文字片段。"""

    page_number: int
    y: float
    cells: list[TextCell]


@dataclass(slots=True)
class ParsedItemRow:
    """尚未聚合的一条商品箱规行。"""

    cargo_key: str
    item_code: str
    description: str
    carton_quantity: int
    units_per_carton: int
    carton_net_weight_kg: float
    carton_gross_weight_kg: float
    length_mm: float
    width_mm: float
    height_mm: float
    page_number: int
    y: float


@dataclass(slots=True)
class CargoAccumulator:
    """把共享同一箱规的多个商品行聚合成一个候选。"""

    first: ParsedItemRow
    carton_quantity: int
    item_codes: set[str]
    locators: list[str]


class CoordinatePackingListParser:
    """解析当前企业 Packing List 模板中右侧箱规和历史装托模式。"""

    VERSION = "RULES_V3"
    _ROW_Y_TOLERANCE = 1.75
    _DIMENSIONS = re.compile(
        r"^\s*(\d+(?:\.\d+)?)\s*[*×xX]\s*"
        r"(\d+(?:\.\d+)?)\s*[*×xX]\s*"
        r"(\d+(?:\.\d+)?)\s*(cm|mm)\s*$",
        flags=re.IGNORECASE,
    )
    _LAYER = re.compile(
        r"(\d+)\s*layer\s*\*\s*(\d+)\s*CTNS",
        flags=re.IGNORECASE,
    )
    _NUMBER = re.compile(r"^@?\s*[\d,]+(?:\.\d+)?$")
    # PL 模板列锚点。重点不是把整行按空格切开，而是只读取对应 x 区间。
    _ITEM_CODE_X: ClassVar[tuple[float, float]] = (55, 98)
    _DESCRIPTION_X: ClassVar[tuple[float, float]] = (180, 305)
    _CARTON_QUANTITY_X: ClassVar[tuple[float, float]] = (405, 435)
    _UNITS_PER_CARTON_X: ClassVar[tuple[float, float]] = (480, 518)
    _CARTON_NET_WEIGHT_X: ClassVar[tuple[float, float]] = (518, 532)
    _CARTON_GROSS_WEIGHT_X: ClassVar[tuple[float, float]] = (532, 541)
    _PATTERN_CARTONS_X: ClassVar[tuple[float, float]] = (365, 400)
    _PATTERN_TARE_X: ClassVar[tuple[float, float]] = (425, 455)
    _PATTERN_CBM_X: ClassVar[tuple[float, float]] = (455, 480)
    _PATTERN_PALLET_QUANTITY_X: ClassVar[tuple[float, float]] = (390, 406)

    def parse_bytes(self, content: bytes) -> PackingListPreview:
        """在内存中解析 PDF，不写磁盘、不返回全文。"""

        rows = self._extract_rows(content)
        declared_total_pallets = self._find_labeled_integer(rows, "PALLET")
        declared_total_cbm = self._find_labeled_number(rows, "CBM")
        total_all_cartons = self._find_total_all_cartons(rows)

        parsed_items: list[ParsedItemRow] = []
        patterns: list[HistoricalPalletPattern] = []
        current_cargo_key: str | None = None

        for index, row in enumerate(rows):
            parsed_item = self._parse_item_row(row)
            if parsed_item is not None:
                parsed_items.append(parsed_item)
                current_cargo_key = parsed_item.cargo_key
                continue

            pattern = self._parse_pattern_row(
                row,
                rows[index + 1] if index + 1 < len(rows) else None,
                current_cargo_key,
            )
            if pattern is not None:
                patterns.append(pattern)

        cargo_candidates = self._aggregate_items(parsed_items)
        if not cargo_candidates and not patterns:
            raise UnreadablePackingListPdfError

        checks = self._build_checks(
            parsed_item_count=len(parsed_items),
            cargo_candidates=cargo_candidates,
            patterns=patterns,
            declared_total_pallets=declared_total_pallets,
            declared_total_cbm=declared_total_cbm,
            total_all_cartons=total_all_cartons,
        )
        return PackingListPreview(
            parser_version=self.VERSION,
            declared_total_pallets=declared_total_pallets,
            declared_total_cbm=declared_total_cbm,
            cargo_candidates=cargo_candidates,
            historical_pallet_patterns=patterns,
            checks=checks,
        )

    def _extract_rows(self, content: bytes) -> list[TextRow]:
        """用 visitor_text 收集坐标，并按页码和近似 y 坐标分组。"""

        try:
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise UnreadablePackingListPdfError
            cells: list[TextCell] = []
            for page_index, page in enumerate(reader.pages, start=1):

                def visitor(
                    text: str,
                    _current_transform: list[float],
                    text_matrix: list[float],
                    _font: dict[str, object] | None,
                    _font_size: float,
                    *,
                    current_page: int = page_index,
                ) -> None:
                    cleaned = " ".join(text.split())
                    if cleaned:
                        cells.append(
                            TextCell(
                                page_number=current_page,
                                x=round(float(text_matrix[4]), 3),
                                y=round(float(text_matrix[5]), 3),
                                text=cleaned,
                            )
                        )

                page.extract_text(visitor_text=visitor)
        except UnreadablePackingListPdfError:
            raise
        except (OSError, PdfReadError, TypeError, ValueError) as error:
            raise UnreadablePackingListPdfError from error

        if not cells:
            raise UnreadablePackingListPdfError

        rows: list[TextRow] = []
        for cell in sorted(cells, key=lambda value: (value.page_number, -value.y, value.x)):
            if (
                not rows
                or rows[-1].page_number != cell.page_number
                or abs(rows[-1].y - cell.y) > self._ROW_Y_TOLERANCE
            ):
                rows.append(
                    TextRow(
                        page_number=cell.page_number,
                        y=cell.y,
                        cells=[cell],
                    )
                )
            else:
                rows[-1].cells.append(cell)
        for row in rows:
            row.cells.sort(key=lambda value: value.x)
        return rows

    def _parse_item_row(self, row: TextRow) -> ParsedItemRow | None:
        """从固定 x 列分别读取商品编码、箱数、箱规和重量。"""

        dimension_cell = next(
            (cell for cell in row.cells if self._DIMENSIONS.fullmatch(cell.text)),
            None,
        )
        if dimension_cell is None:
            return None

        item_code_cell = self._first_text_cell(row, self._ITEM_CODE_X)
        description_cells = self._text_cells(row, self._DESCRIPTION_X)
        carton_quantity = self._integer_in_band(row, self._CARTON_QUANTITY_X)
        units_per_carton = self._integer_in_band(row, self._UNITS_PER_CARTON_X)
        carton_net_weight = self._number_in_band(row, self._CARTON_NET_WEIGHT_X)
        carton_gross_weight = self._number_in_band(row, self._CARTON_GROSS_WEIGHT_X)
        dimensions = self._parse_dimensions(dimension_cell.text)
        if (
            item_code_cell is None
            or not description_cells
            or carton_quantity is None
            or units_per_carton is None
            or carton_net_weight is None
            or carton_gross_weight is None
            or dimensions is None
        ):
            return None

        length_mm, width_mm, height_mm = dimensions
        cargo_key = self._cargo_key(
            length_mm,
            width_mm,
            height_mm,
            units_per_carton,
            carton_net_weight,
            carton_gross_weight,
        )
        return ParsedItemRow(
            cargo_key=cargo_key,
            item_code=item_code_cell.text[:64],
            description=" ".join(cell.text for cell in description_cells)[:500],
            carton_quantity=carton_quantity,
            units_per_carton=units_per_carton,
            carton_net_weight_kg=carton_net_weight,
            carton_gross_weight_kg=carton_gross_weight,
            length_mm=length_mm,
            width_mm=width_mm,
            height_mm=height_mm,
            page_number=row.page_number,
            y=row.y,
        )

    def _parse_pattern_row(
        self,
        row: TextRow,
        next_row: TextRow | None,
        cargo_key: str | None,
    ) -> HistoricalPalletPattern | None:
        """解析一条模式行，并从紧随其后的托盘说明行读取覆盖托数。"""

        layer_match = next(
            (self._LAYER.search(cell.text) for cell in row.cells if self._LAYER.search(cell.text)),
            None,
        )
        dimension_cell = next(
            (cell for cell in row.cells if self._DIMENSIONS.fullmatch(cell.text)),
            None,
        )
        pattern_cartons = self._integer_in_band(row, self._PATTERN_CARTONS_X)
        pallet_tare = self._number_in_band(row, self._PATTERN_TARE_X)
        cbm = self._number_in_band(row, self._PATTERN_CBM_X)
        dimensions = (
            self._parse_dimensions(dimension_cell.text) if dimension_cell is not None else None
        )
        if (
            layer_match is None
            or pattern_cartons is None
            or pallet_tare is None
            or cbm is None
            or dimensions is None
        ):
            return None

        pallet_quantity = None
        source_locator = f"page={row.page_number};y={row.y:.2f}"
        if (
            next_row is not None
            and next_row.page_number == row.page_number
            and 0 < row.y - next_row.y <= 10
            and any("PALLET" in cell.text.upper() for cell in next_row.cells)
        ):
            pallet_quantity = self._integer_in_band(
                next_row,
                self._PATTERN_PALLET_QUANTITY_X,
            )
            source_locator += f";detail_y={next_row.y:.2f}"

        loaded_length_mm, loaded_width_mm, loaded_height_mm = dimensions
        return HistoricalPalletPattern(
            cargo_key=cargo_key,
            cartons_per_layer=int(layer_match.group(2)),
            pattern_carton_count=pattern_cartons,
            pallet_quantity=pallet_quantity,
            pallet_tare_weight_kg=pallet_tare,
            loaded_length_mm=loaded_length_mm,
            loaded_width_mm=loaded_width_mm,
            loaded_height_mm=loaded_height_mm,
            cbm=cbm,
            source_locator=source_locator,
        )

    def _aggregate_items(
        self,
        parsed_items: list[ParsedItemRow],
    ) -> list[PackingListCargoCandidate]:
        """按箱规键聚合商品行，而不是按商品编码复制相同尺寸。"""

        accumulators: dict[str, CargoAccumulator] = {}
        for item in parsed_items:
            locator = f"page={item.page_number};y={item.y:.2f}"
            existing = accumulators.get(item.cargo_key)
            if existing is None:
                accumulators[item.cargo_key] = CargoAccumulator(
                    first=item,
                    carton_quantity=item.carton_quantity,
                    item_codes={item.item_code},
                    locators=[locator],
                )
                continue
            existing.carton_quantity += item.carton_quantity
            existing.item_codes.add(item.item_code)
            existing.locators.append(locator)

        return [
            PackingListCargoCandidate(
                cargo_key=key,
                item_code=value.first.item_code,
                item_codes=sorted(value.item_codes),
                description=value.first.description,
                carton_quantity=value.carton_quantity,
                units_per_carton=value.first.units_per_carton,
                carton_net_weight_kg=value.first.carton_net_weight_kg,
                carton_gross_weight_kg=value.first.carton_gross_weight_kg,
                length_mm=value.first.length_mm,
                width_mm=value.first.width_mm,
                height_mm=value.first.height_mm,
                source_locator="|".join(value.locators)[:500],
                confidence=0.99,
            )
            for key, value in accumulators.items()
        ]

    def _build_checks(
        self,
        *,
        parsed_item_count: int,
        cargo_candidates: list[PackingListCargoCandidate],
        patterns: list[HistoricalPalletPattern],
        declared_total_pallets: int | None,
        declared_total_cbm: float | None,
        total_all_cartons: int | None,
    ) -> list[str]:
        """生成只含数量和校验结论的人工复核提示。"""

        checks = [f"商品行聚合：{parsed_item_count} 行 → {len(cargo_candidates)} 种唯一箱规。"]
        if total_all_cartons is not None:
            candidate_cartons = sum(item.carton_quantity for item in cargo_candidates)
            checks.append(
                self._equality_check(
                    "箱规候选箱数",
                    candidate_cartons,
                    "Total all 箱数",
                    total_all_cartons,
                )
            )

        pattern_count = len(patterns)
        if declared_total_pallets is None:
            checks.append("⚠ 未识别声明总托数，必须人工复核。")
        else:
            checks.append(
                f"⚠ 检测到 {pattern_count} 条历史装托模式行，声明 "
                f"{declared_total_pallets} 托；模式行代表批次/高度组合，"
                "不是逐托明细，必须人工复核后使用。"
            )
            pattern_pallets = [
                pattern.pallet_quantity
                for pattern in patterns
                if pattern.pallet_quantity is not None
            ]
            if len(pattern_pallets) == pattern_count:
                checks.append(
                    self._equality_check(
                        "模式覆盖托数",
                        sum(pattern_pallets),
                        "声明总托数",
                        declared_total_pallets,
                    )
                )
            else:
                checks.append("⚠ 部分模式缺少覆盖托数，不能按模式行数推算总托数。")

        if total_all_cartons is not None and patterns:
            pattern_cartons = sum(
                pattern.pattern_carton_count * (pattern.pallet_quantity or 1)
                for pattern in patterns
            )
            checks.append(
                self._equality_check(
                    "模式覆盖箱数",
                    pattern_cartons,
                    "Total all 箱数",
                    total_all_cartons,
                )
            )

        if declared_total_cbm is None:
            checks.append("⚠ 未识别声明总 CBM，必须人工复核。")
        elif patterns:
            pattern_cbm = round(
                sum(pattern.cbm * (pattern.pallet_quantity or 1) for pattern in patterns),
                3,
            )
            # 逐模式 CBM 通常只保留 3 位，多托盘累加后会放大舍入误差，允许 0.05。
            if abs(pattern_cbm - declared_total_cbm) <= 0.05:
                checks.append(f"模式 CBM {pattern_cbm:g} ≈ 声明总 CBM {declared_total_cbm:g} ✓")
            else:
                checks.append(
                    f"模式 CBM {pattern_cbm:g} ≠ 声明总 CBM {declared_total_cbm:g}，请人工复核。"
                )

        if any(pattern.cargo_key is None for pattern in patterns):
            checks.append("⚠ 存在无法关联箱规的模式行，不能直接进入装载算法。")
        return checks

    @staticmethod
    def _equality_check(
        left_name: str,
        left_value: int,
        right_name: str,
        right_value: int,
    ) -> str:
        operator = "=" if left_value == right_value else "≠"
        suffix = " ✓" if operator == "=" else "，请人工复核。"
        return f"{left_name} {left_value} {operator} {right_name} {right_value}{suffix}"

    def _find_labeled_integer(self, rows: list[TextRow], label: str) -> int | None:
        value = self._find_labeled_number(rows, label)
        return int(value) if value is not None and value.is_integer() else None

    def _find_labeled_number(
        self,
        rows: list[TextRow],
        label: str,
    ) -> float | None:
        """按同一视觉行的标签和值坐标读取汇总，不扫描参与方正文。"""

        normalized_label = label.upper()
        for row in rows:
            for index, cell in enumerate(row.cells):
                normalized_cell = cell.text.upper().rstrip(":：").strip()
                if normalized_cell != normalized_label:
                    continue
                for candidate in row.cells[index + 1 :]:
                    value = self._number(candidate.text)
                    if candidate.x > cell.x and value is not None:
                        return value
        return None

    def _find_total_all_cartons(self, rows: list[TextRow]) -> int | None:
        """从 Total all 行的箱数列读取声明总箱数，仅用于对账。"""

        for row in rows:
            if not any(cell.text.casefold() == "total all" for cell in row.cells):
                continue
            return self._integer_in_band(row, self._CARTON_QUANTITY_X)
        return None

    def _parse_dimensions(self, value: str) -> tuple[float, float, float] | None:
        match = self._DIMENSIONS.fullmatch(value)
        if match is None:
            return None
        multiplier = 10.0 if match.group(4).lower() == "cm" else 1.0
        return tuple(round(float(match.group(index)) * multiplier, 3) for index in range(1, 4))

    @staticmethod
    def _cargo_key(
        length_mm: float,
        width_mm: float,
        height_mm: float,
        units_per_carton: int,
        net_weight_kg: float,
        gross_weight_kg: float,
    ) -> str:
        canonical = (
            f"{length_mm:.3f}|{width_mm:.3f}|{height_mm:.3f}|"
            f"{units_per_carton}|{net_weight_kg:.4f}|{gross_weight_kg:.4f}"
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:12].upper()
        return f"BOX-{digest}"

    def _first_text_cell(
        self,
        row: TextRow,
        x_band: tuple[float, float],
    ) -> TextCell | None:
        return next(
            (cell for cell in row.cells if x_band[0] <= cell.x < x_band[1] and cell.text.strip()),
            None,
        )

    @staticmethod
    def _text_cells(
        row: TextRow,
        x_band: tuple[float, float],
    ) -> list[TextCell]:
        return [cell for cell in row.cells if x_band[0] <= cell.x < x_band[1] and cell.text.strip()]

    def _integer_in_band(
        self,
        row: TextRow,
        x_band: tuple[float, float],
    ) -> int | None:
        value = self._number_in_band(row, x_band)
        return int(value) if value is not None and value.is_integer() else None

    def _number_in_band(
        self,
        row: TextRow,
        x_band: tuple[float, float],
    ) -> float | None:
        for cell in row.cells:
            if x_band[0] <= cell.x < x_band[1]:
                value = self._number(cell.text)
                if value is not None:
                    return value
        return None

    def _number(self, value: str) -> float | None:
        cleaned = value.strip()
        if not self._NUMBER.fullmatch(cleaned):
            return None
        try:
            return float(cleaned.lstrip("@").replace(",", "").strip())
        except ValueError:
            return None
