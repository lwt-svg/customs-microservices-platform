"""装载任务 API 集成测试。"""

from copy import deepcopy

from httpx import AsyncClient


def build_task_payload(task_number: str = "LP-DEMO-0001") -> dict[str, object]:
    """构造不含真实企业编号和客户名称的完整请求。"""

    return {
        "task_number": task_number,
        "task_name": "演示装托任务",
        "description": "验证多箱规、多托盘规格和来源追踪。",
        "cargo_items": [
            {
                "item_code": "ITEM-001",
                "sku_code": "DEMO-SKU-A",
                "cargo_name": "演示产品 A",
                "quantity": 20,
                "length_mm": 300,
                "width_mm": 200,
                "height_mm": 150,
                "gross_weight_kg": 4.0,
                # 未知堆码能力必须省略，接口返回时应保持 null。
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "DEMO-PL.xlsx",
                "source_locator": "Packing List!R10:Z10",
                "confidence": 0.93,
                "verified": False,
            },
            {
                "item_code": "ITEM-002",
                "sku_code": "DEMO-SKU-B",
                "cargo_name": "演示产品 B",
                "quantity": 12,
                "length_mm": 250,
                "width_mm": 180,
                "height_mm": 120,
                "gross_weight_kg": 2.5,
                "stackable": True,
                "keep_upright": True,
                "fragile": False,
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "仓库演示确认记录",
                "verified": False,
            },
        ],
        "pallet_specs": [
            {
                "pallet_code": "PALLET-1200X800",
                "pallet_name": "1200×800 演示托盘",
                "quantity": 10,
                "length_mm": 1200,
                "width_mm": 800,
                "height_mm": 144,
                "tare_weight_kg": 23,
                # 最大承重和成托高度未知，不能写经验默认值。
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "DEMO-PL.pdf",
                "source_locator": "page=1;region=pallet-summary",
                "confidence": 0.88,
                "verified": False,
            },
            {
                "pallet_code": "PALLET-1200X1000",
                "pallet_name": "1200×1000 演示托盘",
                "quantity": 5,
                "length_mm": 1200,
                "width_mm": 1000,
                "height_mm": 150,
                "tare_weight_kg": 25,
                "max_load_kg": 800,
                "max_total_height_mm": 1600,
                "source_type": "SYNTHETIC_DEMO",
                "verified": False,
            },
        ],
    }


async def test_should_create_list_and_get_task_with_multiple_inputs(
    client: AsyncClient,
) -> None:
    """创建响应、列表摘要和详情应形成最小闭环。"""

    created = await client.post(
        "/api/v1/load-planning-tasks",
        json=build_task_payload(),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["task_number"] == "LP-DEMO-0001"
    assert body["status"] == "DRAFT"
    assert body["units"] == {"dimension": "mm", "weight": "kg"}
    assert len(body["cargo_items"]) == 2
    assert len(body["pallet_specs"]) == 2

    task_list = await client.get("/api/v1/load-planning-tasks")
    assert task_list.status_code == 200
    assert task_list.json()[0]["cargo_item_count"] == 2
    assert task_list.json()[0]["cargo_box_count"] == 32
    assert task_list.json()[0]["pallet_spec_count"] == 2

    detail = await client.get("/api/v1/load-planning-tasks/LP-DEMO-0001")
    assert detail.status_code == 200
    assert detail.json()["cargo_items"][0]["source_locator"] == "Packing List!R10:Z10"


async def test_should_preserve_unknown_safety_constraints_as_null(
    client: AsyncClient,
) -> None:
    """系统不能把缺失的堆码、承重和最大高度参数编造成默认值。"""

    response = await client.post(
        "/api/v1/load-planning-tasks",
        json=build_task_payload("LP-DEMO-NULL-SAFETY"),
    )

    assert response.status_code == 201
    cargo = response.json()["cargo_items"][0]
    pallet = response.json()["pallet_specs"][0]
    assert cargo["max_stack_layers"] is None
    assert cargo["max_top_load_kg"] is None
    assert cargo["stackable"] is None
    assert cargo["keep_upright"] is None
    assert cargo["fragile"] is None
    assert pallet["max_load_kg"] is None
    assert pallet["max_total_height_mm"] is None


async def test_should_reject_non_positive_dimension_or_weight(client: AsyncClient) -> None:
    """尺寸和重量为 0 或负数时，在进入 Service 前返回 422。"""

    payload = build_task_payload("LP-DEMO-BAD-DIMENSION")
    cargo_items = payload["cargo_items"]
    assert isinstance(cargo_items, list)
    cargo_items[0]["length_mm"] = 0
    cargo_items[1]["gross_weight_kg"] = -1

    response = await client.post("/api/v1/load-planning-tasks", json=payload)

    assert response.status_code == 422
    error_locations = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert ("body", "cargo_items", 0, "length_mm") in error_locations
    assert ("body", "cargo_items", 1, "gross_weight_kg") in error_locations


async def test_should_require_trace_for_file_derived_data(client: AsyncClient) -> None:
    """历史文件或导入文件必须同时提供文件标识和页/行/单元格定位。"""

    payload = build_task_payload("LP-DEMO-MISSING-TRACE")
    cargo_items = payload["cargo_items"]
    assert isinstance(cargo_items, list)
    cargo_items[0]["source_type"] = "DOCUMENT_IMPORT"
    cargo_items[0].pop("source_locator")

    response = await client.post("/api/v1/load-planning-tasks", json=payload)

    assert response.status_code == 422
    assert "source_reference 和 source_locator" in response.text


async def test_should_reject_duplicate_codes_inside_one_task(client: AsyncClient) -> None:
    """同一任务内重复业务编码会让算法结果无法回指输入，因此必须拒绝。"""

    payload = build_task_payload("LP-DEMO-DUPLICATE-CODE")
    cargo_items = payload["cargo_items"]
    assert isinstance(cargo_items, list)
    cargo_items[1]["item_code"] = "ITEM-001"

    response = await client.post("/api/v1/load-planning-tasks", json=payload)

    assert response.status_code == 422
    assert "item_code 不能重复" in response.text


async def test_should_return_conflict_for_duplicate_task_number(client: AsyncClient) -> None:
    """任务编号是幂等业务键，重复创建返回 409。"""

    first = await client.post(
        "/api/v1/load-planning-tasks",
        json=build_task_payload("LP-DEMO-DUPLICATE-TASK"),
    )
    second = await client.post(
        "/api/v1/load-planning-tasks",
        json=build_task_payload("LP-DEMO-DUPLICATE-TASK"),
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "装载任务编号已存在"


async def test_should_reject_invalid_confidence_and_pallet_weight(
    client: AsyncClient,
) -> None:
    """置信度必须在 0..1，托盘皮重也必须是正数。"""

    payload = deepcopy(build_task_payload("LP-DEMO-BAD-SOURCE"))
    cargo_items = payload["cargo_items"]
    pallet_specs = payload["pallet_specs"]
    assert isinstance(cargo_items, list)
    assert isinstance(pallet_specs, list)
    cargo_items[0]["confidence"] = 1.2
    pallet_specs[0]["tare_weight_kg"] = 0

    response = await client.post("/api/v1/load-planning-tasks", json=payload)

    assert response.status_code == 422


async def test_should_never_mark_synthetic_demo_data_as_verified(
    client: AsyncClient,
) -> None:
    """构造数据不能靠一个布尔值自动升级成正式主数据。"""

    payload = build_task_payload("LP-DEMO-NOT-FORMAL")
    cargo_items = payload["cargo_items"]
    assert isinstance(cargo_items, list)
    cargo_items[0]["verified"] = True

    response = await client.post("/api/v1/load-planning-tasks", json=payload)

    assert response.status_code == 422
    assert "构造数据不能标记为已核实正式数据" in response.text


async def test_should_return_not_found_for_unknown_task(client: AsyncClient) -> None:
    """查询未知任务不能隐式创建空任务。"""

    response = await client.get("/api/v1/load-planning-tasks/LP-NOT-FOUND")

    assert response.status_code == 404
    assert response.json()["detail"] == "装载任务不存在"


async def test_health_does_not_depend_on_mysql(client: AsyncClient) -> None:
    """存活探针不应因为本机 MySQL 未启动而失败。"""

    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["service"] == "load-planning-service"
