"""LINK 详情到 RPA 草稿合同的边界测试。

这些测试只使用 ``httpx.MockTransport``，不会访问真实 LINK 或外部网站。重点验证：

* Agent 只读取双方约定的白名单字段；
* 演示编号会得到明确的 ``SYNTHETIC_DEMO`` 标签；
* 缺字段、错误类型和嵌套结构漂移都会在进入 RPA 队列前被拦住；
* 旧的 ``LinkBookingSnapshot`` Fake 不传 RPA 字段时仍然可用。
"""

from copy import deepcopy
from typing import Any

import httpx
import pytest

from app.integrations.shipment_agent_resources import (
    HttpLinkBookingClient,
    LinkBookingProtocolError,
    LinkBookingSnapshot,
)

BASE_URL = "http://link.test"
APPLICATION_NUMBER = "DEMO-LINK-RPA-001"


def build_detail_payload(
    application_number: str = APPLICATION_NUMBER,
) -> dict[str, object]:
    """构造与 LINK 详情接口一致、但不含任何企业真实数据的响应。"""

    return {
        "application": {
            "id": 101,
            "application_number": application_number,
            "booking_reference": None,
            "origin_port": "DEMO-SHA",
            "destination_port": "DEMO-LAX",
            "transport_mode": "SEA",
            "container_type": "40HQ",
            "requested_etd": "2026-11-15",
            "booking_details": {
                "invoice_number": "DEMO-INV-001",
                "document_date": "2026-11-01",
                "ciq_number": "DEMO-CIQ-001",
                "shipper": "DEMO SHIPPER",
                "consignee": "DEMO CONSIGNEE",
                "notify_party": "DEMO NOTIFY PARTY",
                "marks": "DEMO SHIPPING MARKS",
                "total_pallets": 1,
                "total_cartons": 10,
                "total_quantity": 100,
                "total_cbm": 8.5,
                "total_net_weight_kg": 900.0,
                "total_gross_weight_kg": 1000.0,
                "items": [
                    {
                        "item_code": "DEMO-SKU-001",
                        "lot_number": "DEMO-LOT-001",
                        "po_number": "DEMO-PO-001",
                        "description": "DEMO GOODS",
                        "quantity": 100,
                        "cartons": 10,
                        "net_weight_kg": 900.0,
                        "gross_weight_kg": 1000.0,
                    }
                ],
                "packages": [
                    {
                        "description": "DEMO PACKAGE",
                        "pallets": 1,
                        "cartons": 10,
                        "quantity": 100,
                        "net_weight_kg": 900.0,
                        "gross_weight_kg": 1000.0,
                        "cbm": 8.5,
                        "po_allocations": [
                            {
                                "po_number": "DEMO-PO-001",
                                "quantity": 100,
                            }
                        ],
                    }
                ],
                "checks": ["DEMO_TOTALS_MATCH"],
            },
            "status": "DRAFT",
            "created_at": "2026-10-22T08:00:00",
            "updated_at": "2026-10-22T08:00:00",
        },
        "events": [],
        "carrier_submission": None,
    }


def make_client(payload: dict[str, object]) -> HttpLinkBookingClient:
    """返回只会响应一份构造详情的 LINK HTTP 客户端。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return HttpLinkBookingClient(
        BASE_URL,
        timeout_seconds=0.05,
        transport=httpx.MockTransport(handler),
    )


async def test_should_build_strict_demo_rpa_payload_from_link_detail() -> None:
    """合法详情会得到可直接放进 RpaJob.business_payload 的 JSON 字典。"""

    snapshot = await make_client(build_detail_payload()).get_detail(APPLICATION_NUMBER)

    assert snapshot.rpa_payload is not None
    business_payload = snapshot.rpa_payload.model_dump(mode="json")

    assert business_payload["payload_schema_version"] == "LINK_RPA_DRAFT_V1"
    assert business_payload["data_source_label"] == "SYNTHETIC_DEMO"
    assert business_payload["application_number"] == APPLICATION_NUMBER
    assert business_payload["origin_port"] == "DEMO-SHA"
    assert business_payload["destination_port"] == "DEMO-LAX"
    assert business_payload["requested_etd"] == "2026-11-15"
    assert business_payload["booking_details"]["items"][0]["quantity"] == 100  # type: ignore[index]
    assert business_payload["booking_details"]["packages"][0]["cbm"] == 8.5  # type: ignore[index]

    # 只读合同只包含显式白名单，不会把 LINK 数据库 id、状态或时间戳交给 Worker。
    assert "id" not in business_payload
    assert "status" not in business_payload
    assert "created_at" not in business_payload


def test_existing_fake_snapshot_can_omit_rpa_payload() -> None:
    """新增字段带默认值，旧测试 Fake 和旧调用方无需同步修改。"""

    snapshot = LinkBookingSnapshot(
        application_number=APPLICATION_NUMBER,
        application_status="DRAFT",
        booking_reference=None,
        carrier_status=None,
        carrier_can_retry=False,
        carrier_request_id=None,
    )

    assert snapshot.rpa_payload is None


@pytest.mark.parametrize(
    "missing_field",
    [
        "origin_port",
        "destination_port",
        "transport_mode",
        "container_type",
        "requested_etd",
        "booking_details",
    ],
)
async def test_should_reject_missing_rpa_application_field(
    missing_field: str,
) -> None:
    """允许字段值为 null 不代表允许协议把整个字段删掉。"""

    payload = build_detail_payload()
    application = payload["application"]
    assert isinstance(application, dict)
    application.pop(missing_field)

    with pytest.raises(LinkBookingProtocolError, match="缺少 RPA 字段"):
        await make_client(payload).get_detail(APPLICATION_NUMBER)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("origin_port", 123),
        ("destination_port", ""),
        ("transport_mode", ["SEA"]),
        ("container_type", None),
        ("requested_etd", "15/11/2026"),
        ("booking_details", []),
    ],
)
async def test_should_reject_invalid_top_level_rpa_field_type_or_value(
    field_name: str,
    invalid_value: Any,
) -> None:
    """上游类型漂移不能靠 Pydantic 隐式转换后继续执行浏览器。"""

    payload = build_detail_payload()
    application = payload["application"]
    assert isinstance(application, dict)
    application[field_name] = invalid_value

    with pytest.raises(LinkBookingProtocolError, match="不符合 v1 协议"):
        await make_client(payload).get_detail(APPLICATION_NUMBER)


async def test_should_reject_invalid_nested_booking_detail() -> None:
    """嵌套商品数量同样执行严格校验，字符串数字不会被自动接纳。"""

    payload = deepcopy(build_detail_payload())
    application = payload["application"]
    assert isinstance(application, dict)
    booking_details = application["booking_details"]
    assert isinstance(booking_details, dict)
    items = booking_details["items"]
    assert isinstance(items, list)
    first_item = items[0]
    assert isinstance(first_item, dict)
    first_item["quantity"] = "100"

    with pytest.raises(LinkBookingProtocolError, match="不符合 v1 协议"):
        await make_client(payload).get_detail(APPLICATION_NUMBER)


async def test_should_reject_non_demo_application_as_rpa_source() -> None:
    """当前 LINK 数据域只允许 DEMO 编号，不能把未知来源冒充成真实企业数据。"""

    non_demo_number = "REAL-LINK-001"
    payload = build_detail_payload(non_demo_number)

    with pytest.raises(LinkBookingProtocolError, match="不符合 v1 协议"):
        await make_client(payload).get_detail(non_demo_number)
