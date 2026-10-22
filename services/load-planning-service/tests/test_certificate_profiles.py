"""产地证配置目录与执行计划的纯单元测试。

这些测试不需要 MySQL、浏览器、UKey 或单一窗口。它们先把“8 种旧 profile 到 3 种
网页策略”的映射固定下来，后续接 Selenium/Playwright 时只消费计划，不重复写判断。
"""

import pytest

from app.rpa.certificate_profiles import (
    CertificateExecutionPlan,
    CertificateProfile,
    CertificateProfileCatalog,
    CertificateProgram,
    DuplicateCertificateProfileError,
    ExecutionPlanBuilder,
    ImportRecordScope,
    PortalFlowProfile,
    SourceFormatProfile,
    UnsupportedCertificateProfileError,
)


@pytest.mark.parametrize(
    ("processor_profile", "source_format", "program", "portal_flow"),
    [
        (
            "wudi_form_e",
            SourceFormatProfile.FORM_E_WUDI_V1,
            CertificateProgram.FORM_E,
            PortalFlowProfile.STANDARD,
        ),
        (
            "better_form_e",
            SourceFormatProfile.FORM_E_BETTER_V1,
            CertificateProgram.FORM_E,
            PortalFlowProfile.STANDARD,
        ),
        (
            "heying_rcep_yuanyi",
            SourceFormatProfile.RCEP_YUANYI_V1,
            CertificateProgram.RCEP,
            PortalFlowProfile.RCEP_WITH_INVOICE,
        ),
        (
            "heying_rcep_chuangyuan_raw",
            SourceFormatProfile.RCEP_CHUANGYUAN_RAW_V1,
            CertificateProgram.RCEP,
            PortalFlowProfile.RCEP_WITH_INVOICE,
        ),
        (
            "heying_rcep_chuangyuan_equipment",
            SourceFormatProfile.RCEP_CHUANGYUAN_EQUIPMENT_V1,
            CertificateProgram.RCEP,
            PortalFlowProfile.RCEP_WITH_INVOICE,
        ),
        (
            "heying_rcep_pti",
            SourceFormatProfile.RCEP_PTI_V1,
            CertificateProgram.RCEP,
            PortalFlowProfile.RCEP_WITH_INVOICE,
        ),
        (
            "wang_korea_yuanyi",
            SourceFormatProfile.CHINA_KOREA_YUANYI_V1,
            CertificateProgram.CHINA_KOREA_FTA,
            PortalFlowProfile.STANDARD,
        ),
        (
            "wang_korea_chuangyuan_equipment",
            SourceFormatProfile.CHINA_KOREA_CHUANGYUAN_EQUIPMENT_V1,
            CertificateProgram.CHINA_KOREA_FTA,
            PortalFlowProfile.SPLIT_GOODS_PACKAGING,
        ),
    ],
)
def test_catalog_maps_all_eight_legacy_profiles(
    processor_profile: str,
    source_format: SourceFormatProfile,
    program: CertificateProgram,
    portal_flow: PortalFlowProfile,
) -> None:
    """8 个旧兼容键必须分别映射到明确的版式、协定和网页策略。"""

    profile = CertificateProfileCatalog().resolve(processor_profile)

    assert profile.processor_profile == processor_profile
    assert profile.source_format is source_format
    assert profile.program is program
    assert profile.portal_flow is portal_flow


def test_catalog_exposes_exactly_eight_supported_processor_profiles() -> None:
    """第一阶段只接受已经从旧系统核对过的 8 种任务，不偷偷接受 ``auto``。"""

    catalog = CertificateProfileCatalog()

    assert len(catalog.profiles) == 8
    assert set(catalog.supported_processor_profiles) == {
        "wudi_form_e",
        "better_form_e",
        "heying_rcep_yuanyi",
        "heying_rcep_chuangyuan_raw",
        "heying_rcep_chuangyuan_equipment",
        "heying_rcep_pti",
        "wang_korea_yuanyi",
        "wang_korea_chuangyuan_equipment",
    }


def test_catalog_accepts_only_harmless_case_and_whitespace_variation() -> None:
    """迁移期允许大小写/首尾空格差异，但不会把未知别名猜成已有配置。"""

    profile = CertificateProfileCatalog().resolve("  HEYING_RCEP_PTI  ")

    assert profile.processor_profile == "heying_rcep_pti"


@pytest.mark.parametrize("unknown_profile", ["", "auto", "unknown_profile"])
def test_catalog_rejects_unknown_profile_explicitly(unknown_profile: str) -> None:
    """未知配置不能静默走 STANDARD，否则可能在真实网站填错证书。"""

    with pytest.raises(
        UnsupportedCertificateProfileError,
        match="不支持的产地证 processorProfile",
    ):
        CertificateProfileCatalog().resolve(unknown_profile)


def test_catalog_rejects_non_string_profile_explicitly() -> None:
    """坏任务载荷应在打开浏览器之前失败。"""

    with pytest.raises(
        UnsupportedCertificateProfileError,
        match="必须是字符串",
    ):
        CertificateProfileCatalog().resolve(None)  # type: ignore[arg-type]


def test_catalog_rejects_duplicate_legacy_key_after_normalization() -> None:
    """目录不能同时存在仅大小写不同的两个兼容键。"""

    original = CertificateProfileCatalog().resolve("wudi_form_e")
    duplicate = CertificateProfile(
        processor_profile="wudi_form_e",
        source_format=SourceFormatProfile.FORM_E_BETTER_V1,
        program=CertificateProgram.FORM_E,
        portal_flow=PortalFlowProfile.STANDARD,
        display_name="重复测试配置",
    )

    with pytest.raises(DuplicateCertificateProfileError, match="重复"):
        CertificateProfileCatalog([original, duplicate])


def test_standard_flow_builds_one_all_rows_batch() -> None:
    """FORM E 和普通中韩 FTA 使用一次 Excel 导入。"""

    plan = ExecutionPlanBuilder().build("wudi_form_e")

    assert isinstance(plan, CertificateExecutionPlan)
    assert plan.program is CertificateProgram.FORM_E
    assert plan.portal_flow is PortalFlowProfile.STANDARD
    assert plan.requires_invoice_information is False
    assert len(plan.import_batches) == 1
    batch = plan.import_batches[0]
    assert batch.record_scope is ImportRecordScope.ALL
    assert batch.auto_generate_goods_description is True
    assert batch.accepts(is_non_goods=False) is True
    assert batch.accepts(is_non_goods=True) is True


@pytest.mark.parametrize(
    "processor_profile",
    [
        "heying_rcep_yuanyi",
        "heying_rcep_chuangyuan_raw",
        "heying_rcep_chuangyuan_equipment",
        "heying_rcep_pti",
    ],
)
def test_rcep_flow_requires_invoice_information_before_single_import(
    processor_profile: str,
) -> None:
    """四种 RCEP 版式共用同一条“先新增发票、再导入”网页流程。"""

    plan = ExecutionPlanBuilder().build(processor_profile)

    assert plan.program is CertificateProgram.RCEP
    assert plan.portal_flow is PortalFlowProfile.RCEP_WITH_INVOICE
    assert plan.requires_invoice_information is True
    assert [batch.record_scope for batch in plan.import_batches] == [
        ImportRecordScope.ALL
    ]


def test_korea_equipment_flow_declares_goods_and_packaging_batches() -> None:
    """中韩创元设备把正常货物和包装附加行分开导入。"""

    plan = ExecutionPlanBuilder().build("wang_korea_chuangyuan_equipment")

    assert plan.program is CertificateProgram.CHINA_KOREA_FTA
    assert plan.portal_flow is PortalFlowProfile.SPLIT_GOODS_PACKAGING
    assert plan.requires_invoice_information is False
    assert [batch.record_scope for batch in plan.import_batches] == [
        ImportRecordScope.GOODS,
        ImportRecordScope.PACKAGING,
    ]
    goods, packaging = plan.import_batches
    assert goods.auto_generate_goods_description is True
    assert packaging.auto_generate_goods_description is False
    assert goods.accepts(is_non_goods=False) is True
    assert goods.accepts(is_non_goods=True) is False
    assert packaging.accepts(is_non_goods=False) is False
    assert packaging.accepts(is_non_goods=True) is True

