"""RULES_V3 坐标解析和 multipart 预览接口测试。

测试 PDF 全部由 reportlab 生成，只包含构造编码与构造商品名，不复制企业文件原文。
"""

from io import BytesIO

from httpx import AsyncClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.api.packing_list_imports import get_packing_list_preview_service
from app.main import app
from app.services.packing_list_imports import PackingListPreviewService
from app.services.packing_list_parser import CoordinatePackingListParser


def build_coordinate_pdf() -> bytes:
    """生成与目标模板列坐标相似、但业务内容完全构造的 PDF。"""

    output = BytesIO()
    document = canvas.Canvas(output, pagesize=A4)
    document.setFont("Helvetica", 7)

    # 声明汇总：标签和值是同一 y、不同 x 的独立文字对象。
    document.drawString(300, 790, "PALLET:")
    document.drawString(330, 790, "3")
    document.drawString(300, 780, "CBM:")
    document.drawString(330, 780, "1.52")

    def item_row(
        y: float,
        item_code: str,
        description: str,
        cartons: int,
        units: int,
        net_weight: str,
        gross_weight: str,
        dimensions: str,
    ) -> None:
        # 这些相邻列故意只相差 10~13pt，用来证明 visitor_text 不会把数字粘起来。
        document.drawString(69, y, item_code)
        document.drawString(216, y, description)
        document.drawString(419, y, str(cartons))
        document.drawString(487, y, str(units))
        document.drawString(523, y, net_weight)
        document.drawString(535, y, gross_weight)
        document.drawString(545, y, dimensions)

    item_row(730, "DEMO-A", "DEMO PRODUCT A", 10, 12, "0.50", "6.00", "30*20*10cm")
    item_row(718, "DEMO-A", "DEMO PRODUCT A", 4, 12, "0.50", "6.00", "30*20*10cm")
    # 不同商品编码但相同箱规，也应聚合到同一个 cargo_key。
    item_row(706, "DEMO-B", "DEMO PRODUCT B", 2, 12, "0.50", "6.00", "30*20*10cm")

    document.drawString(330, 690, "1 layer * 4 CTNS")
    document.drawString(375, 690, "@8")
    document.drawString(407, 690, "@/")
    document.drawString(438, 690, "@20")
    document.drawString(463, 690, "@0.51")
    document.drawString(500, 690, "120*80*53cm")
    document.drawString(330, 682, "Fumigation Pallet")
    document.drawString(401, 682, "2")

    item_row(650, "DEMO-C", "DEMO PRODUCT C", 4, 8, "1.10", "9.50", "40*25*20cm")
    document.drawString(330, 634, "1 layer * 2 CTNS")
    document.drawString(375, 634, "@4")
    document.drawString(407, 634, "@/")
    document.drawString(438, 634, "@20")
    document.drawString(463, 634, "@0.50")
    document.drawString(500, 634, "1200*800*600mm")
    document.drawString(330, 626, "Fumigation Pallet")
    document.drawString(401, 626, "1")

    document.drawString(192, 600, "Total all")
    document.drawString(388, 600, "3")
    document.drawString(418, 600, "20")
    document.save()
    return output.getvalue()


def build_blank_pdf() -> bytes:
    """生成语法有效但没有文字层的空白 PDF。"""

    output = BytesIO()
    document = canvas.Canvas(output, pagesize=A4)
    document.showPage()
    document.save()
    return output.getvalue()


async def test_should_preview_coordinate_pdf_without_merging_adjacent_columns(
    client: AsyncClient,
) -> None:
    """净重、毛重和尺寸靠得很近时仍应按 x 坐标独立解析。"""

    response = await client.post(
        "/api/v1/packing-list-imports/preview",
        files={
            "file": (
                "demo-coordinate-pl.pdf",
                build_coordinate_pdf(),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["parser_version"] == "RULES_V3"
    assert body["declared_total_pallets"] == 3
    assert body["declared_total_cbm"] == 1.52
    assert len(body["cargo_candidates"]) == 2
    first = body["cargo_candidates"][0]
    assert first["carton_net_weight_kg"] == 0.5
    assert first["carton_gross_weight_kg"] == 6.0
    assert (first["length_mm"], first["width_mm"], first["height_mm"]) == (
        300.0,
        200.0,
        100.0,
    )


async def test_should_aggregate_same_carton_spec_and_link_historical_patterns(
    client: AsyncClient,
) -> None:
    """相同箱规的多商品行合并，模式通过 cargo_key 关联而不是复制箱规。"""

    response = await client.post(
        "/api/v1/packing-list-imports/preview",
        files={
            "file": (
                "demo-coordinate-pl.pdf",
                build_coordinate_pdf(),
                "application/pdf",
            )
        },
    )
    body = response.json()

    first = body["cargo_candidates"][0]
    assert first["item_code"] == "DEMO-A"
    assert first["item_codes"] == ["DEMO-A", "DEMO-B"]
    assert first["carton_quantity"] == 16
    assert first["units_per_carton"] == 12
    assert "DEMO PRODUCT" not in first["source_locator"]

    patterns = body["historical_pallet_patterns"]
    assert len(patterns) == 2
    assert patterns[0]["cargo_key"] == first["cargo_key"]
    assert patterns[0]["cartons_per_layer"] == 4
    assert patterns[0]["pattern_carton_count"] == 8
    assert patterns[0]["pallet_quantity"] == 2
    assert patterns[0]["pallet_tare_weight_kg"] == 20.0
    assert patterns[0]["loaded_length_mm"] == 1200.0
    assert patterns[0]["loaded_width_mm"] == 800.0
    assert patterns[0]["loaded_height_mm"] == 530.0
    assert patterns[0]["cbm"] == 0.51
    assert any("2 条历史装托模式行，声明 3 托" in check for check in body["checks"])
    assert any("不是逐托明细" in check for check in body["checks"])
    assert any("模式覆盖托数 3 = 声明总托数 3" in check for check in body["checks"])


async def test_should_reject_wrong_mime_type(client: AsyncClient) -> None:
    """扩展名看似 PDF，但 MIME 不正确时返回 415。"""

    response = await client.post(
        "/api/v1/packing-list-imports/preview",
        files={"file": ("fake.pdf", b"not-a-pdf", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json() == {"detail": "只接受有效的 PDF 文件"}


async def test_should_reject_invalid_or_blank_pdf(client: AsyncClient) -> None:
    """损坏 PDF 和空白 PDF 都返回不含原文的稳定错误。"""

    invalid = await client.post(
        "/api/v1/packing-list-imports/preview",
        files={"file": ("invalid.pdf", b"%PDF-invalid-private-text", "application/pdf")},
    )
    blank = await client.post(
        "/api/v1/packing-list-imports/preview",
        files={"file": ("blank.pdf", build_blank_pdf(), "application/pdf")},
    )

    assert invalid.status_code == 422
    assert blank.status_code == 422
    assert "private-text" not in invalid.text
    assert "当前版本不支持 OCR" in invalid.text


async def test_should_stop_reading_after_configured_size_limit(
    client: AsyncClient,
) -> None:
    """接口只读取上限加一个字节，并在解析前返回 413。"""

    app.dependency_overrides[get_packing_list_preview_service] = lambda: PackingListPreviewService(
        CoordinatePackingListParser(), max_bytes=32
    )
    try:
        response = await client.post(
            "/api/v1/packing-list-imports/preview",
            files={
                "file": (
                    "oversized.pdf",
                    b"%PDF-" + b"x" * 100,
                    "application/pdf",
                )
            },
        )
    finally:
        app.dependency_overrides.pop(get_packing_list_preview_service, None)

    assert response.status_code == 413
    assert response.json()["detail"].startswith("PDF 文件不能超过")
