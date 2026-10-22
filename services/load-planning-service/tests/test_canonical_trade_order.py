"""标准贸易订单 v1 的纯领域测试。

这些测试不启动数据库、浏览器或大模型。它们保护的是三类 RPA 共用的数据入口：只要
订单内部数量、重量、金额、必填字段或证据标签互相矛盾，就必须在打开外部网站之前失败。
"""

from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.rpa.canonical_trade_order import (
    CANONICAL_TRADE_ORDER_VERSION,
    CanonicalTradeOrder,
    FieldEvidenceSourceType,
    PartyRole,
    TradeDataSourceLabel,
)
from app.rpa.contracts import RpaOperation


def _valid_order_payload() -> dict[str, object]:
    """构造一份同时需要 LINK、产地证和商检的合成 Golden Case。"""

    return {
        "data_source_label": "SYNTHETIC_DEMO",
        "identity": {
            "case_number": "SHP-GOLDEN-001",
            "order_number": "SO-2026-001",
            "revision": 1,
            "commercial_invoice_number": "INV-2026-001",
            "packing_list_number": "PL-2026-001",
        },
        "transport": {
            "mode": "SEA_FCL",
            "origin_country_code": "CN",
            "destination_country_code": "FR",
            "port_of_loading": "CNSHA",
            "port_of_discharge": "FRLEH",
            "incoterm": "FOB",
            "requested_departure_date": "2026-11-10",
            "requested_arrival_date": "2026-12-12",
            "carrier_code": "DEMO_LINE",
            "equipment_type": "40HQ",
            "equipment_quantity": 1,
        },
        "parties": [
            {
                "role": "EXPORTER",
                "name": "演示出口商",
                "country_code": "CN",
                "address_lines": ["中国上海市演示路 1 号"],
            },
            {
                "role": "CONSIGNEE",
                "name": "Demo Consignee",
                "country_code": "FR",
                "address_lines": ["1 Demo Avenue, Le Havre"],
            },
        ],
        "goods_lines": [
            {
                "line_number": 1,
                "item_code": "SKU-001",
                "description": "演示商品 A",
                "hs_code": "84198990",
                "origin_country_code": "CN",
                "package_count": 3,
                "package_type": "CARTON",
                "quantity": "10",
                "quantity_unit": "PCS",
                "net_weight_kg": "10",
                "gross_weight_kg": "12",
                "volume_m3": "1.2",
                "line_amount": "100",
                "currency": "USD",
                "shipping_marks": "DEMO/A",
            },
            {
                "line_number": 2,
                "item_code": "SKU-002",
                "description": "演示商品 B",
                "hs_code": "84219990",
                "origin_country_code": "CN",
                "package_count": 1,
                "package_type": "CRATE",
                "quantity": "2",
                "quantity_unit": "SET",
                "net_weight_kg": "5",
                "gross_weight_kg": "6",
                "volume_m3": "0.4",
                "line_amount": "50",
                "currency": "USD",
            },
        ],
        "totals": {
            "line_count": 2,
            "total_package_count": 4,
            # 不同单位分别汇总，不能把 10 PCS 和 2 SET 错写成 12。
            "quantity_totals": [
                {"unit": "PCS", "quantity": "10"},
                {"unit": "SET", "quantity": "2"},
            ],
            "total_net_weight_kg": "15",
            "total_gross_weight_kg": "18",
            "total_volume_m3": "1.6",
            "total_amount": "150",
            "currency": "USD",
        },
        "required_flows": [
            "LINK_BOOKING_STAGE",
            "COO_STAGE",
            "CIQ_STAGE",
        ],
        # 第一版至少覆盖五个关键字段组。未来可以继续细化到单个商品行字段。
        "field_evidence": [
            {
                "field_path": "identity",
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "GOLDEN-CASE-001",
            },
            {
                "field_path": "transport",
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "GOLDEN-CASE-001",
            },
            {
                "field_path": "parties",
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "GOLDEN-CASE-001",
            },
            {
                "field_path": "goods_lines",
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "GOLDEN-CASE-001",
            },
            {
                "field_path": "totals",
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "GOLDEN-CASE-001",
            },
        ],
    }


def test_valid_order_is_one_read_only_input_for_all_three_rpa_flows() -> None:
    order = CanonicalTradeOrder.model_validate(_valid_order_payload())

    assert order.contract_version == CANONICAL_TRADE_ORDER_VERSION
    assert order.data_source_label is TradeDataSourceLabel.SYNTHETIC_DEMO
    assert order.required_flows == (
        RpaOperation.LINK_BOOKING_STAGE,
        RpaOperation.COO_STAGE,
        RpaOperation.CIQ_STAGE,
    )
    assert order.totals.total_gross_weight_kg == Decimal(18)
    assert order.party_for(PartyRole.EXPORTER).name == "演示出口商"  # type: ignore[union-attr]

    with pytest.raises(ValidationError, match="Instance is frozen"):
        order.identity.revision = 2  # type: ignore[misc]


def test_unknown_or_duplicate_required_flow_is_rejected() -> None:
    unknown = _valid_order_payload()
    unknown["required_flows"] = ["UNKNOWN_STAGE"]
    with pytest.raises(ValidationError, match="UNKNOWN_STAGE"):
        CanonicalTradeOrder.model_validate(unknown)

    duplicate = _valid_order_payload()
    duplicate["required_flows"] = ["LINK_BOOKING_STAGE", "LINK_BOOKING_STAGE"]
    with pytest.raises(ValidationError, match="required_flows 不能包含重复流程"):
        CanonicalTradeOrder.model_validate(duplicate)


def test_unknown_field_is_rejected_instead_of_being_silently_ignored() -> None:
    payload = _valid_order_payload()
    payload["identity"]["invoice_nubmer"] = "拼错的字段"  # type: ignore[index]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CanonicalTradeOrder.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_message"),
    [
        ("line_count", 3, "totals.line_count"),
        ("total_package_count", 5, "totals.total_package_count"),
        ("total_net_weight_kg", "15.1", "totals.total_net_weight_kg"),
        ("total_gross_weight_kg", "18.1", "totals.total_gross_weight_kg"),
        ("total_volume_m3", "1.7", "totals.total_volume_m3"),
        ("total_amount", "151", "totals.total_amount"),
    ],
)
def test_declared_totals_must_equal_recalculated_goods_line_totals(
    field_name: str,
    bad_value: object,
    expected_message: str,
) -> None:
    payload = _valid_order_payload()
    payload["totals"][field_name] = bad_value  # type: ignore[index]

    with pytest.raises(ValidationError, match=expected_message):
        CanonicalTradeOrder.model_validate(payload)


def test_quantities_are_reconciled_per_unit_instead_of_adding_unlike_units() -> None:
    payload = _valid_order_payload()
    payload["totals"]["quantity_totals"] = [  # type: ignore[index]
        {"unit": "PCS", "quantity": "12"}
    ]

    with pytest.raises(ValidationError, match="按单位汇总的数量不一致"):
        CanonicalTradeOrder.model_validate(payload)


def test_line_and_order_gross_weight_cannot_be_lower_than_net_weight() -> None:
    bad_line = _valid_order_payload()
    bad_line["goods_lines"][0]["gross_weight_kg"] = "9"  # type: ignore[index]
    with pytest.raises(ValidationError, match="gross_weight_kg 不能小于 net_weight_kg"):
        CanonicalTradeOrder.model_validate(bad_line)

    bad_totals = _valid_order_payload()
    bad_totals["totals"]["total_gross_weight_kg"] = "14"  # type: ignore[index]
    with pytest.raises(
        ValidationError,
        match="total_gross_weight_kg 不能小于 total_net_weight_kg",
    ):
        CanonicalTradeOrder.model_validate(bad_totals)


def test_link_flow_requires_ocean_booking_fields_before_browser_execution() -> None:
    payload = _valid_order_payload()
    payload["required_flows"] = ["LINK_BOOKING_STAGE"]
    payload["transport"]["port_of_discharge"] = None  # type: ignore[index]

    with pytest.raises(
        ValidationError,
        match="LINK_BOOKING_STAGE 缺少运输必填字段：port_of_discharge",
    ):
        CanonicalTradeOrder.model_validate(payload)


def test_single_window_flows_require_invoice_hs_code_and_origin() -> None:
    missing_invoice = _valid_order_payload()
    missing_invoice["required_flows"] = ["COO_STAGE"]
    missing_invoice["identity"]["commercial_invoice_number"] = None  # type: ignore[index]
    with pytest.raises(ValidationError, match="必须提供 commercial_invoice_number"):
        CanonicalTradeOrder.model_validate(missing_invoice)

    missing_goods_fields = _valid_order_payload()
    missing_goods_fields["required_flows"] = ["CIQ_STAGE"]
    missing_goods_fields["goods_lines"][1]["hs_code"] = None  # type: ignore[index]
    missing_goods_fields["goods_lines"][1]["origin_country_code"] = None  # type: ignore[index]
    with pytest.raises(
        ValidationError,
        match=r"商品行 2 缺少：hs_code, origin_country_code",
    ):
        CanonicalTradeOrder.model_validate(missing_goods_fields)


def test_exporter_and_consignee_are_required_and_party_roles_are_unique() -> None:
    missing_consignee = _valid_order_payload()
    missing_consignee["parties"] = [missing_consignee["parties"][0]]  # type: ignore[index]
    with pytest.raises(ValidationError, match="at least 2 items"):
        CanonicalTradeOrder.model_validate(missing_consignee)

    duplicate_role = _valid_order_payload()
    duplicate_role["parties"][1]["role"] = "EXPORTER"  # type: ignore[index]
    with pytest.raises(ValidationError, match="同一个 role 只能出现一次"):
        CanonicalTradeOrder.model_validate(duplicate_role)


def test_every_critical_field_group_must_have_evidence() -> None:
    payload = _valid_order_payload()
    payload["field_evidence"] = payload["field_evidence"][:-1]  # type: ignore[index]

    # Field 至少五条的基础约束也会阻止漏项；补一条 identity 后可继续验证“根字段覆盖”。
    payload["field_evidence"].append(  # type: ignore[union-attr]
        {
            "field_path": "identity.order_number",
            "source_type": "SYNTHETIC_DEMO",
            "source_reference": "GOLDEN-CASE-001",
        }
    )
    with pytest.raises(ValidationError, match="field_evidence 缺少关键字段组：totals"):
        CanonicalTradeOrder.model_validate(payload)


def test_demo_and_enterprise_real_evidence_can_never_be_mixed() -> None:
    demo_with_real_evidence = _valid_order_payload()
    demo_with_real_evidence["field_evidence"][0]["source_type"] = (  # type: ignore[index]
        "DOCUMENT_IMPORT"
    )
    demo_with_real_evidence["field_evidence"][0]["source_locator"] = (  # type: ignore[index]
        "Sheet1!A1"
    )
    with pytest.raises(ValidationError, match="全部字段证据都必须标记"):
        CanonicalTradeOrder.model_validate(demo_with_real_evidence)

    real_with_demo_evidence = _valid_order_payload()
    real_with_demo_evidence["data_source_label"] = "ENTERPRISE_REAL"
    with pytest.raises(ValidationError, match="不能混入 SYNTHETIC_DEMO"):
        CanonicalTradeOrder.model_validate(real_with_demo_evidence)


def test_enterprise_real_order_accepts_traceable_non_demo_evidence() -> None:
    payload = _valid_order_payload()
    payload["data_source_label"] = "ENTERPRISE_REAL"
    for index, evidence in enumerate(payload["field_evidence"]):  # type: ignore[union-attr]
        evidence["source_type"] = "DOCUMENT_IMPORT"
        evidence["source_reference"] = "脱敏业务资料.xlsx"
        evidence["source_locator"] = f"Sheet1!A{index + 1}"

    order = CanonicalTradeOrder.model_validate(payload)

    assert order.data_source_label is TradeDataSourceLabel.ENTERPRISE_REAL
    assert {evidence.source_type for evidence in order.field_evidence} == {
        FieldEvidenceSourceType.DOCUMENT_IMPORT
    }


def test_document_evidence_needs_locator_and_demo_evidence_cannot_be_verified() -> None:
    document_without_locator = _valid_order_payload()
    document_without_locator["field_evidence"][0]["source_type"] = (  # type: ignore[index]
        "DOCUMENT_IMPORT"
    )
    with pytest.raises(
        ValidationError,
        match="DOCUMENT_IMPORT 字段证据必须提供 source_locator",
    ):
        CanonicalTradeOrder.model_validate(document_without_locator)

    verified_demo = deepcopy(_valid_order_payload())
    verified_demo["field_evidence"][0]["verified"] = True  # type: ignore[index]
    with pytest.raises(
        ValidationError,
        match="SYNTHETIC_DEMO 字段证据不能标记为 verified",
    ):
        CanonicalTradeOrder.model_validate(verified_demo)
