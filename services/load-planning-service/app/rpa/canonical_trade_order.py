"""LINK、产地证和商检 RPA 共用的标准贸易订单 v1。

为什么需要这一层？

原始资料可能来自 PDF、Excel、ERP、WMS 或人工补录，字段名称和结构都不一样。如果让
每个 RPA 直接读取原始文件，LINK、产地证和商检会各自写一套解析规则，同一票货还可能
得到三份互相矛盾的数据。

``CanonicalTradeOrder`` 是原始资料和浏览器 RPA 之间的“标准答案”：

* 上游 Parser/Agent 负责把不同文件整理成这个模型；
* 本模型只做确定性校验，不调用 LLM，也不操作浏览器；
* 下游 RPA 只读取通过校验的标准字段，不再猜测重量、金额或参与方；
* 每组关键字段必须保留证据定位，并明确标记为演示数据或企业真实数据。

这里故意不包含数据库、FastAPI、LangGraph、Playwright 或 Selenium 代码。纯领域模型
可以被 API、工作流和 Windows Worker 共同复用，也可以在没有任何外部服务时做单元测试。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.rpa.contracts import RpaOperation

CANONICAL_TRADE_ORDER_VERSION = "CANONICAL_TRADE_ORDER_V1"

# 金额、重量和体积不能使用二进制 float 做业务对账。Decimal 可以保证 0.1 + 0.2
# 仍然得到精确的十进制结果。六位小数足以覆盖当前贸易单据和装载规划的交换精度。
PositiveDecimal = Annotated[
    Decimal,
    Field(gt=0, max_digits=18, decimal_places=6),
]
NonNegativeDecimal = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=6),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class TradeDataSourceLabel(StrEnum):
    """整份标准订单的数据性质。

    ``SYNTHETIC_DEMO`` 只能用于教程、演示和自动化测试。``ENTERPRISE_REAL`` 表示字段
    来自真实业务资料或真实业务人员确认。标签不代表数据已经审批，只用于防止演示数据
    被误送到真实 RPA 环境。
    """

    SYNTHETIC_DEMO = "SYNTHETIC_DEMO"
    ENTERPRISE_REAL = "ENTERPRISE_REAL"


class FieldEvidenceSourceType(StrEnum):
    """某组字段的具体证据来源。"""

    SYNTHETIC_DEMO = "SYNTHETIC_DEMO"
    DOCUMENT_IMPORT = "DOCUMENT_IMPORT"
    MANUAL_CONFIRMATION = "MANUAL_CONFIRMATION"
    ERP = "ERP"
    WMS = "WMS"


class TradeTransportMode(StrEnum):
    """当前标准订单支持的运输方式。"""

    SEA_FCL = "SEA_FCL"
    SEA_LCL = "SEA_LCL"
    AIR = "AIR"
    EXPRESS = "EXPRESS"


class PartyRole(StrEnum):
    """参与方在本票业务中的角色，而不是企业主数据中的永久身份。"""

    EXPORTER = "EXPORTER"
    CONSIGNEE = "CONSIGNEE"
    IMPORTER = "IMPORTER"
    MANUFACTURER = "MANUFACTURER"
    NOTIFY_PARTY = "NOTIFY_PARTY"


class _StrictDomainModel(BaseModel):
    """标准订单所有子模型共用的安全边界。

    ``extra='forbid'`` 会拒绝拼错的字段名，避免调用方以为数据已经进入模型；``frozen``
    让通过校验的订单成为只读快照，RPA 执行中不能悄悄改掉它。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class TradeOrderIdentity(_StrictDomainModel):
    """一票贸易订单的稳定业务身份。"""

    case_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        description="TradeOps 内部稳定案件编号",
    )
    order_number: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*$",
        description="企业销售订单、发运订单或虚拟 Golden Case 编号",
    )
    revision: int = Field(
        ge=1,
        description="标准订单修订号；补数或修正后必须递增",
    )
    commercial_invoice_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*$",
    )
    packing_list_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*$",
    )
    contract_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*$",
    )


class TradeTransport(_StrictDomainModel):
    """三个 RPA 共用的运输事实。

    国家使用 ISO 3166-1 alpha-2 两位代码，港口使用 UN/LOCODE 五位代码。模型要求
    调用方提交大写标准代码，不在这里猜测“上海”究竟是城市、港口还是装货地。
    """

    mode: TradeTransportMode
    origin_country_code: str = Field(pattern=r"^[A-Z]{2}$")
    destination_country_code: str = Field(pattern=r"^[A-Z]{2}$")
    port_of_loading: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}[A-Z0-9]{3}$",
    )
    port_of_discharge: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}[A-Z0-9]{3}$",
    )
    incoterm: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    requested_departure_date: date | None = None
    requested_arrival_date: date | None = None
    carrier_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^[A-Z0-9][A-Z0-9_-]*$",
    )
    equipment_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^[A-Z0-9][A-Z0-9_-]*$",
        description="例如 20GP、40GP、40HQ；由业务配置决定，不在模型中写死所有箱型",
    )
    equipment_quantity: int | None = Field(default=None, ge=1, le=999)

    @model_validator(mode="after")
    def validate_dates_and_equipment_pair(self) -> Self:
        """拒绝只有箱型没有数量，以及到港日在离港日前的明显矛盾。"""

        if (self.equipment_type is None) != (self.equipment_quantity is None):
            raise ValueError("equipment_type 和 equipment_quantity 必须同时提供或同时为空")
        if (
            self.requested_departure_date is not None
            and self.requested_arrival_date is not None
            and self.requested_arrival_date < self.requested_departure_date
        ):
            raise ValueError("requested_arrival_date 不能早于 requested_departure_date")
        return self


class TradeParty(_StrictDomainModel):
    """订单中的一个参与方。"""

    role: PartyRole
    name: str = Field(min_length=1, max_length=256)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    address_lines: tuple[ShortText, ...] = Field(min_length=1, max_length=4)
    registration_number: str | None = Field(default=None, min_length=1, max_length=128)
    contact_name: str | None = Field(default=None, min_length=1, max_length=128)
    phone: str | None = Field(default=None, min_length=1, max_length=64)
    email: str | None = Field(default=None, min_length=3, max_length=254)


class TradeGoodsLine(_StrictDomainModel):
    """一条可对账的商品行。

    ``package_count`` 是外包装数量，``quantity`` 是商品数量，两者含义不同。例如
    10 箱、每箱 20 件，应分别填写 10 和 200，不能混成一个“数量”字段。
    """

    line_number: int = Field(ge=1, le=99_999)
    item_code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    description: str = Field(min_length=1, max_length=500)
    hs_code: str | None = Field(default=None, pattern=r"^[0-9]{6,10}$")
    origin_country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    package_count: int = Field(ge=1, le=999_999_999)
    package_type: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Z][A-Z0-9_-]*$",
    )
    quantity: PositiveDecimal
    quantity_unit: str = Field(
        min_length=1,
        max_length=16,
        pattern=r"^[A-Z][A-Z0-9_-]*$",
    )
    net_weight_kg: PositiveDecimal
    gross_weight_kg: PositiveDecimal
    volume_m3: PositiveDecimal
    line_amount: NonNegativeDecimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    shipping_marks: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_line_weight(self) -> Self:
        """单行毛重包含包装，不能小于同一行净重。"""

        if self.gross_weight_kg < self.net_weight_kg:
            raise ValueError(f"商品行 {self.line_number} 的 gross_weight_kg 不能小于 net_weight_kg")
        return self


class QuantityTotal(_StrictDomainModel):
    """按计量单位汇总的商品数量。

    不同计量单位不能直接相加。10 PCS 和 2 SET 会生成两条汇总，而不是伪造为 12。
    """

    unit: str = Field(
        min_length=1,
        max_length=16,
        pattern=r"^[A-Z][A-Z0-9_-]*$",
    )
    quantity: PositiveDecimal


class TradeOrderTotals(_StrictDomainModel):
    """原始单据声明的汇总值，创建订单时会与商品行重新计算并对账。"""

    line_count: int = Field(ge=1, le=99_999)
    total_package_count: int = Field(ge=1, le=999_999_999)
    quantity_totals: tuple[QuantityTotal, ...] = Field(min_length=1)
    total_net_weight_kg: PositiveDecimal
    total_gross_weight_kg: PositiveDecimal
    total_volume_m3: PositiveDecimal
    total_amount: NonNegativeDecimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("quantity_totals")
    @classmethod
    def quantity_units_must_be_unique(
        cls,
        values: tuple[QuantityTotal, ...],
    ) -> tuple[QuantityTotal, ...]:
        units = [value.unit for value in values]
        if len(units) != len(set(units)):
            raise ValueError("quantity_totals 中同一个 unit 只能出现一次")
        return values

    @model_validator(mode="after")
    def validate_total_weight(self) -> Self:
        """订单总毛重同样不能小于总净重。"""

        if self.total_gross_weight_kg < self.total_net_weight_kg:
            raise ValueError("total_gross_weight_kg 不能小于 total_net_weight_kg")
        return self


class FieldEvidence(_StrictDomainModel):
    """关键字段来自哪里的可审计证据。

    ``field_path`` 可以指向整个字段组（如 ``goods_lines``），也可以精确到
    ``goods_lines[0].hs_code``。证据只保存文件名、单号或页签定位，不应把客户原文、
    Cookie、账号密码或 UKey 信息复制进日志。
    """

    field_path: str = Field(
        min_length=1,
        max_length=255,
        pattern=(
            r"^(identity|transport|parties|goods_lines|totals)"
            r"(?:\[[0-9]+\])?(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
        ),
    )
    source_type: FieldEvidenceSourceType
    source_reference: str = Field(
        min_length=1,
        max_length=255,
        description="脱敏文件名、导入批次号、ERP/WMS 单号或演示案例编号",
    )
    source_locator: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="页码、页签/单元格或系统字段定位；不能保存敏感登录信息",
    )
    confidence: Decimal | None = Field(
        default=None,
        ge=0,
        le=1,
        max_digits=7,
        decimal_places=6,
    )
    verified: bool = False

    @model_validator(mode="after")
    def validate_evidence_source(self) -> Self:
        """文档解析需要定位；合成数据永远不能冒充已人工核验的真实事实。"""

        if (
            self.source_type is FieldEvidenceSourceType.DOCUMENT_IMPORT
            and self.source_locator is None
        ):
            raise ValueError("DOCUMENT_IMPORT 字段证据必须提供 source_locator")
        if self.source_type is FieldEvidenceSourceType.SYNTHETIC_DEMO and self.verified:
            raise ValueError("SYNTHETIC_DEMO 字段证据不能标记为 verified")
        return self


class CanonicalTradeOrder(_StrictDomainModel):
    """三个 RPA 共同消费的只读标准订单。

    该模型只表示“准备送入自动化流程的数据快照”。通过校验不等于完成业务审批，也不
    授权 RPA 正式订舱、签章或申报；副作用边界仍由 :class:`RpaJob` 控制。
    """

    contract_version: Literal["CANONICAL_TRADE_ORDER_V1"] = CANONICAL_TRADE_ORDER_VERSION
    data_source_label: TradeDataSourceLabel
    identity: TradeOrderIdentity
    transport: TradeTransport
    parties: tuple[TradeParty, ...] = Field(min_length=2)
    goods_lines: tuple[TradeGoodsLine, ...] = Field(min_length=1)
    totals: TradeOrderTotals
    required_flows: tuple[RpaOperation, ...] = Field(min_length=1)
    field_evidence: tuple[FieldEvidence, ...] = Field(min_length=5)

    @field_validator("required_flows")
    @classmethod
    def required_flows_must_be_unique(
        cls,
        values: tuple[RpaOperation, ...],
    ) -> tuple[RpaOperation, ...]:
        if len(values) != len(set(values)):
            raise ValueError("required_flows 不能包含重复流程")
        return values

    @model_validator(mode="after")
    def validate_complete_order(self) -> Self:
        """一次完成全部跨字段对账，保证下游 RPA 看到的是同一份业务事实。"""

        self._validate_parties()
        self._validate_line_numbers()
        self._validate_totals()
        self._validate_flow_requirements()
        self._validate_evidence()
        return self

    def _validate_parties(self) -> None:
        roles = [party.role for party in self.parties]
        if len(roles) != len(set(roles)):
            raise ValueError("parties 中同一个 role 只能出现一次")

        missing_roles = {
            PartyRole.EXPORTER,
            PartyRole.CONSIGNEE,
        }.difference(roles)
        if missing_roles:
            names = ", ".join(sorted(role.value for role in missing_roles))
            raise ValueError(f"标准订单缺少必填参与方：{names}")

    def _validate_line_numbers(self) -> None:
        line_numbers = [line.line_number for line in self.goods_lines]
        if len(line_numbers) != len(set(line_numbers)):
            raise ValueError("goods_lines 的 line_number 不能重复")

    def _validate_totals(self) -> None:
        """用商品行重新计算汇总，拒绝单据内部自相矛盾。"""

        if self.totals.line_count != len(self.goods_lines):
            raise ValueError("totals.line_count 与 goods_lines 行数不一致")

        package_count = sum(line.package_count for line in self.goods_lines)
        if self.totals.total_package_count != package_count:
            raise ValueError("totals.total_package_count 与商品行合计不一致")

        quantity_by_unit: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for line in self.goods_lines:
            quantity_by_unit[line.quantity_unit] += line.quantity
        declared_quantity_by_unit = {
            item.unit: item.quantity for item in self.totals.quantity_totals
        }
        if declared_quantity_by_unit != dict(quantity_by_unit):
            raise ValueError("totals.quantity_totals 与商品行按单位汇总的数量不一致")

        decimal_totals = (
            (
                "total_net_weight_kg",
                self.totals.total_net_weight_kg,
                sum(
                    (line.net_weight_kg for line in self.goods_lines),
                    Decimal(0),
                ),
            ),
            (
                "total_gross_weight_kg",
                self.totals.total_gross_weight_kg,
                sum(
                    (line.gross_weight_kg for line in self.goods_lines),
                    Decimal(0),
                ),
            ),
            (
                "total_volume_m3",
                self.totals.total_volume_m3,
                sum(
                    (line.volume_m3 for line in self.goods_lines),
                    Decimal(0),
                ),
            ),
            (
                "total_amount",
                self.totals.total_amount,
                sum(
                    (line.line_amount for line in self.goods_lines),
                    Decimal(0),
                ),
            ),
        )
        for field_name, declared, calculated in decimal_totals:
            if declared != calculated:
                raise ValueError(f"totals.{field_name} 与商品行合计不一致")

        line_currencies = {line.currency for line in self.goods_lines}
        if line_currencies != {self.totals.currency}:
            raise ValueError("商品行 currency 必须全部等于 totals.currency")

    def _validate_flow_requirements(self) -> None:
        """按所选流程检查真正会被网页使用的必填字段。"""

        if RpaOperation.LINK_BOOKING_STAGE in self.required_flows:
            if self.transport.mode not in {
                TradeTransportMode.SEA_FCL,
                TradeTransportMode.SEA_LCL,
            }:
                raise ValueError("LINK_BOOKING_STAGE 当前只支持 SEA_FCL 或 SEA_LCL")
            link_required_fields = {
                "port_of_loading": self.transport.port_of_loading,
                "port_of_discharge": self.transport.port_of_discharge,
                "requested_departure_date": self.transport.requested_departure_date,
                "equipment_type": self.transport.equipment_type,
                "equipment_quantity": self.transport.equipment_quantity,
            }
            missing = [name for name, value in link_required_fields.items() if value is None]
            if missing:
                raise ValueError("LINK_BOOKING_STAGE 缺少运输必填字段：" + ", ".join(missing))

        single_window_flows = {
            RpaOperation.COO_STAGE,
            RpaOperation.CIQ_STAGE,
        }.intersection(self.required_flows)
        if not single_window_flows:
            return

        if self.identity.commercial_invoice_number is None:
            raise ValueError("COO_STAGE/CIQ_STAGE 必须提供 commercial_invoice_number")

        for line in self.goods_lines:
            missing_line_fields: list[str] = []
            if line.hs_code is None:
                missing_line_fields.append("hs_code")
            if line.origin_country_code is None:
                missing_line_fields.append("origin_country_code")
            if missing_line_fields:
                raise ValueError(
                    f"COO_STAGE/CIQ_STAGE 的商品行 {line.line_number} 缺少："
                    + ", ".join(missing_line_fields)
                )

    def _validate_evidence(self) -> None:
        """保证五组关键数据都有来源，并阻止演示与真实标签互相冒充。"""

        required_roots = {
            "identity",
            "transport",
            "parties",
            "goods_lines",
            "totals",
        }
        evidence_roots = {
            evidence.field_path.split(".", maxsplit=1)[0].split("[", maxsplit=1)[0]
            for evidence in self.field_evidence
        }
        missing_roots = sorted(required_roots.difference(evidence_roots))
        if missing_roots:
            raise ValueError("field_evidence 缺少关键字段组：" + ", ".join(missing_roots))

        source_types = {evidence.source_type for evidence in self.field_evidence}
        if self.data_source_label is TradeDataSourceLabel.SYNTHETIC_DEMO:
            if source_types != {FieldEvidenceSourceType.SYNTHETIC_DEMO}:
                raise ValueError("SYNTHETIC_DEMO 订单的全部字段证据都必须标记为 SYNTHETIC_DEMO")
            return

        if FieldEvidenceSourceType.SYNTHETIC_DEMO in source_types:
            raise ValueError("ENTERPRISE_REAL 订单不能混入 SYNTHETIC_DEMO 字段证据")

    def party_for(self, role: PartyRole) -> TradeParty | None:
        """按角色读取参与方，供 RPA Handler 使用，避免各处重复遍历和比较字符串。"""

        return next((party for party in self.parties if party.role is role), None)
