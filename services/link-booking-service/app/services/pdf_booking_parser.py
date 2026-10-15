"""文本型订舱 PDF 的确定性规则解析器。

RULES_V2 同时兼容教学用的 ``标签: 值`` 文件，以及带文字层的 Packing List。
解析结果只包含结构化字段和短证据，不保存 PDF 全文。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import ClassVar

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.schemas.booking_document_import import (
    BookingDetails,
    BookingDocumentCandidates,
    BookingFieldCandidate,
    BookingItemDetails,
    BookingPackageDetails,
    BookingPoAllocation,
    CandidateConfidence,
)


class UnreadableBookingPdfError(Exception):
    """PDF 损坏、加密或没有可提取文本。"""


class PdfBookingCandidateParser:
    """从明确标签和 Packing List 表格文字层中提取候选字段。"""

    VERSION = "RULES_V2"

    _LABELS: ClassVar[dict[str, tuple[str, ...]]] = {
        "application_number": ("application number", "application no", "booking number"),
        "origin_port": ("origin port", "port of loading"),
        "destination_port": ("destination port", "port of discharge"),
        "transport_mode": ("transport mode", "shipping mode"),
        "container_type": ("container type", "equipment type"),
        "requested_etd": ("requested etd", "etd"),
    }
    _ITEM = re.compile(
        r"^(\S+)\s+(\S+)\s+(\d{6,})"
        r"(?:\s+(\S+)\s+(\d{4}[-/]\d{1,2}[-/]\d{1,2}))?"
        r"\s+(.+?)\s+PCS\s+([\d,]+)\s+PCS\s+([\d,]+)"
        r"\s+([\d.,]+)\s+([\d.,]+)"
    )
    _TOTAL = re.compile(
        r"^(?:Total(?:\s+all)?\s+)?"
        r"([\d,]+)\s+(\d+)\s+([\d,]+)\s+([\d.,]+)\s+([\d.,]+)"
        r"(?:\s+([\d.,]+))?\s*$",
        flags=re.IGNORECASE,
    )
    _RIGHT_LABEL = re.compile(
        r"\s*(?:Date|Invoice Number|Order Number|CIQ No|Bill of lading No|"
        r"MARKS|PALLET|CBM)\s*[:：].*$",
        flags=re.IGNORECASE,
    )
    _BLOCK_START = re.compile(
        r"^(BUYER|SHIPPER|CONSIGNEE|NOTIFY(?:\s+PARTY)?)\s*[:：]\s*(.*)$",
        flags=re.IGNORECASE,
    )

    def parse(self, path: Path) -> BookingDocumentCandidates:
        """读取 PDF 并返回候选；不返回或保存完整提取文本。"""

        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise UnreadableBookingPdfError
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except (OSError, PdfReadError, ValueError) as error:
            raise UnreadableBookingPdfError from error

        if not text.strip():
            raise UnreadableBookingPdfError

        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        parsed: dict[str, object] = {
            field_name: self._find_candidate(lines, labels)
            for field_name, labels in self._LABELS.items()
        }
        booking_details = self._parse_booking_details(text, lines)
        parsed["booking_details"] = booking_details
        application_candidate = parsed["application_number"]
        if (
            isinstance(application_candidate, BookingFieldCandidate)
            and application_candidate.value is None
            and booking_details is not None
            and booking_details.invoice_number
        ):
            parsed["application_number"] = BookingFieldCandidate(
                value=f"DEMO-PL-{booking_details.invoice_number}"[:64],
                evidence=(
                    f"由 PDF Invoice Number {booking_details.invoice_number} 派生，"
                    "仅用于本地 DEMO 申请编号"
                ),
                confidence=CandidateConfidence.MEDIUM,
            )
        return BookingDocumentCandidates.model_validate(parsed)

    def _find_candidate(
        self, lines: list[str], labels: tuple[str, ...]
    ) -> BookingFieldCandidate:
        """只接受“标签: 值”形式，避免从任意正文猜测业务字段。"""

        for line in lines:
            for label in labels:
                match = re.match(
                    rf"^\s*{re.escape(label)}\s*[:：]\s*(.+?)\s*$",
                    line,
                    flags=re.IGNORECASE,
                )
                if match:
                    value = match.group(1).strip()
                    if value:
                        return BookingFieldCandidate(
                            value=value[:64],
                            evidence=line[:200],
                            confidence=CandidateConfidence.HIGH,
                        )
        return BookingFieldCandidate()

    def _parse_booking_details(
        self, text: str, lines: list[str]
    ) -> BookingDetails | None:
        """解析 Packing List；没有明细行时保留 RULES_V1 的空值行为。"""

        items: list[BookingItemDetails] = []
        packages: list[BookingPackageDetails] = []
        current_items: list[BookingItemDetails] = []
        total_all: tuple[int, int, int, float, float] | None = None

        for line in lines:
            item_match = self._ITEM.match(line)
            if item_match:
                item = BookingItemDetails(
                    item_code=item_match.group(1),
                    lot_number=item_match.group(2),
                    po_number=item_match.group(3),
                    description=item_match.group(6).strip(),
                    quantity=self._integer(item_match.group(7)),
                    cartons=self._integer(item_match.group(8)),
                    net_weight_kg=self._number(item_match.group(9)),
                    gross_weight_kg=self._number(item_match.group(10)),
                )
                items.append(item)
                current_items.append(item)
                continue

            total_match = self._TOTAL.match(line)
            if total_match is None:
                continue

            quantity = self._integer(total_match.group(1))
            pallets = self._integer(total_match.group(2))
            cartons = self._integer(total_match.group(3))
            net_weight = self._number(total_match.group(4))
            gross_weight = self._number(total_match.group(5))
            cbm = (
                self._number(total_match.group(6))
                if total_match.group(6) is not None
                else None
            )
            if current_items:
                po_quantities: defaultdict[str, int] = defaultdict(int)
                for item in current_items:
                    po_quantities[item.po_number] += item.quantity
                packages.append(
                    BookingPackageDetails(
                        description=self._package_description(current_items[0].description),
                        pallets=pallets,
                        cartons=cartons,
                        quantity=quantity,
                        net_weight_kg=net_weight,
                        gross_weight_kg=gross_weight,
                        cbm=cbm,
                        po_allocations=[
                            BookingPoAllocation(po_number=po, quantity=po_quantity)
                            for po, po_quantity in po_quantities.items()
                        ],
                    )
                )
                current_items = []
            else:
                total_all = (quantity, pallets, cartons, net_weight, gross_weight)

        if not items:
            return None

        quantity_sum = sum(item.quantity for item in items)
        cartons_sum = sum(item.cartons for item in items)
        net_weight_sum = self._round2(sum(item.net_weight_kg for item in items))
        item_gross_weight_sum = self._round2(
            sum(item.gross_weight_kg for item in items)
        )
        header_pallets = self._optional_integer(
            self._first_group(text, r"PALLET\s*[:：]\s*([\d.,]+)")
        )
        total_pallets = total_all[1] if total_all is not None else header_pallets
        total_cbm = self._optional_number(
            self._first_group(text, r"CBM\s*[:：]\s*([\d.,]+)")
        )
        checks = self._build_checks(
            total_all,
            quantity_sum,
            cartons_sum,
            net_weight_sum,
            item_gross_weight_sum,
        )
        blocks = self._party_blocks(lines)

        invoice_number = self._first_group(
            text, r"Invoice Number\s*[:：]\s*(20\d{2}-[\d-]+)"
        )
        if invoice_number is None:
            invoice_number = next(
                (
                    line
                    for line in lines
                    if re.fullmatch(r"20\d{2}-\d{3,4}(?:-\d+)?", line)
                ),
                None,
            )

        document_date_text = self._first_group(
            text, r"Date\s*[:：]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})"
        )
        return BookingDetails(
            invoice_number=invoice_number,
            document_date=self._date(document_date_text),
            ciq_number=self._first_group(
                text, r"CIQ No\s*[:：]\s*([0-9A-Za-z-]+)"
            ),
            shipper=self._find_shipper(lines, blocks),
            consignee=blocks.get("consignee"),
            notify_party=blocks.get("notify"),
            marks=self._first_group(text, r"MARKS\s*[:：]\s*(.+)"),
            total_pallets=total_pallets,
            total_cartons=total_all[2] if total_all is not None else cartons_sum,
            total_quantity=total_all[0] if total_all is not None else quantity_sum,
            total_cbm=total_cbm,
            total_net_weight_kg=(
                self._round2(total_all[3]) if total_all is not None else net_weight_sum
            ),
            total_gross_weight_kg=(
                self._round2(total_all[4])
                if total_all is not None
                else item_gross_weight_sum
            ),
            items=items,
            packages=packages,
            checks=checks,
        )

    def _party_blocks(self, lines: list[str]) -> dict[str, str]:
        """读取多行参与方块，并移除同一视觉行上属于右列的标签。"""

        values: dict[str, list[str]] = {}
        current: str | None = None
        for line in lines:
            start = self._BLOCK_START.match(line)
            if start:
                raw_name = start.group(1).lower()
                current = "notify" if raw_name.startswith("notify") else raw_name
                values.setdefault(current, [])
                self._append_block(values[current], start.group(2))
                continue
            if current is None:
                continue
            if (
                re.match(
                    r"^(CUSTOMER|Item Code|CBM\s*[:：]|Packing List)",
                    line,
                    re.IGNORECASE,
                )
                or self._ITEM.match(line)
            ):
                current = None
                continue
            self._append_block(values[current], line)
        return {
            name: " ".join(parts).strip()
            for name, parts in values.items()
            if parts
        }

    def _append_block(self, parts: list[str], value: str) -> None:
        cleaned = self._RIGHT_LABEL.sub("", value).strip()
        if cleaned:
            parts.append(cleaned)

    @staticmethod
    def _package_description(description: str) -> str:
        match = re.search(r"[（(]([^）)]+)[）)]?\s*$", description)
        return (match.group(1) if match else description).strip()

    def _find_shipper(self, lines: list[str], blocks: dict[str, str]) -> str | None:
        """优先显式 SHIPPER，再识别标题前的签发方，最后回退 BUYER。"""

        if blocks.get("shipper"):
            return blocks["shipper"]
        try:
            title_index = next(
                index
                for index, line in enumerate(lines)
                if line.casefold() == "packing list"
            )
        except StopIteration:
            title_index = -1
        if title_index >= 2:
            issuer_lines = lines[max(0, title_index - 2) : title_index]
            if not any(self._TOTAL.match(line) for line in issuer_lines):
                return " ".join(issuer_lines)
        return blocks.get("buyer")

    def _build_checks(
        self,
        total_all: tuple[int, int, int, float, float] | None,
        quantity_sum: int,
        cartons_sum: int,
        net_weight_sum: float,
        item_gross_weight_sum: float,
    ) -> list[str]:
        if total_all is None:
            return ["⚠ 未找到 Total all 总计行"]

        checks = [
            self._check("数量", quantity_sum, total_all[0], 0.5),
            self._check("箱数", cartons_sum, total_all[2], 0.5),
            self._check("净重", net_weight_sum, total_all[3], 0.02),
        ]
        pallets = total_all[1]
        if pallets > 0:
            pallet_weight = round((total_all[4] - item_gross_weight_sum) / pallets, 1)
            if 5 <= pallet_weight <= 40:
                checks.append(
                    "毛重：Total all "
                    f"{self._format(total_all[4])} = 明细 "
                    f"{self._format(item_gross_weight_sum)} + 木托 "
                    f"{pallets}×{self._format(pallet_weight)}kg ✓"
                )
            else:
                checks.append(
                    f"毛重：木托口径异常({self._format(pallet_weight)} kg/托) "
                    "✗ 请人工复核"
                )
        return checks

    def _check(self, name: str, mine: float, theirs: float, tolerance: float) -> str:
        operator = "=" if abs(mine - theirs) <= tolerance else "≠"
        suffix = "✓" if operator == "=" else "✗ 请人工复核"
        return (
            f"{name}：明细 {self._format(mine)} {operator} "
            f"Total all {self._format(theirs)} {suffix}"
        )

    @staticmethod
    def _first_group(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text)
        value = match.group(1).strip() if match else ""
        return value or None

    @staticmethod
    def _number(value: str) -> float:
        return float(value.replace(",", "").strip())

    @classmethod
    def _integer(cls, value: str) -> int:
        return int(cls._number(value))

    @classmethod
    def _optional_number(cls, value: str | None) -> float | None:
        return cls._number(value) if value else None

    @classmethod
    def _optional_integer(cls, value: str | None) -> int | None:
        return cls._integer(value) if value else None

    @staticmethod
    def _round2(value: float) -> float:
        return round(value + 1e-12, 2)

    @staticmethod
    def _format(value: float) -> str:
        return str(int(value)) if value == int(value) else str(value)

    @staticmethod
    def _date(value: str | None) -> date | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value.replace("/", "-"))
        except ValueError:
            return None
