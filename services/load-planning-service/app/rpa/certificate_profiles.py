"""产地证来源版式、业务协定与网页执行策略的配置目录。

旧系统把 ``processorProfile`` 同时当成“文件来自哪里”“办理哪一种产地证”和
“浏览器应该怎么点击”。这样写到后期通常会出现很多分散的 ``if/else``：新增一种
Excel 版式时，很容易误改网页流程；网页流程变化时，又可能影响文件解析。

本模块把三个概念拆开：

* :class:`SourceFormatProfile`：上传资料或最终导入文件采用哪一种已知版式；
* :class:`CertificateProgram`：业务上办理 FORM E、RCEP 还是中国—韩国 FTA；
* :class:`PortalFlowProfile`：进入单一窗口后采用哪一种浏览器执行策略。

``CertificateProfileCatalog`` 只负责把旧系统传来的 8 个 ``processorProfile`` 映射
成上述中性配置；``ExecutionPlanBuilder`` 再把配置翻译为可执行的导入批次计划。
这一阶段不操作数据库，也不点击网页，因此可以用纯单元测试把规则锁定。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class CertificateProgram(StrEnum):
    """产地证所属的业务协定。

    这是业务概念，不代表某个客户文件的版式，也不包含任何 XPath。
    """

    FORM_E = "FORM_E"
    RCEP = "RCEP"
    CHINA_KOREA_FTA = "CHINA_KOREA_FTA"


class PortalFlowProfile(StrEnum):
    """单一窗口产地证页面的三种稳定执行策略。"""

    # 复制历史证书 -> 清空旧货物 -> 导入一个 Excel -> 暂存。
    STANDARD = "STANDARD"
    # 在标准流程中，导入 Excel 前还要新增本票发票信息。
    RCEP_WITH_INVOICE = "RCEP_WITH_INVOICE"
    # 正常货物与包装附加行分两次导入，且两次的页面选项不同。
    SPLIT_GOODS_PACKAGING = "SPLIT_GOODS_PACKAGING"


class SourceFormatProfile(StrEnum):
    """当前旧系统已经产出的 8 种产地证文件版式。

    枚举值故意使用新的稳定代码；旧的 ``processorProfile`` 只保留在
    :class:`CertificateProfile` 的兼容字段中。这样以后替换解析器或重命名客户来源时，
    不必改变业务协定和网页执行策略。
    """

    FORM_E_WUDI_V1 = "FORM_E_WUDI_V1"
    FORM_E_BETTER_V1 = "FORM_E_BETTER_V1"
    RCEP_YUANYI_V1 = "RCEP_YUANYI_V1"
    RCEP_CHUANGYUAN_RAW_V1 = "RCEP_CHUANGYUAN_RAW_V1"
    RCEP_CHUANGYUAN_EQUIPMENT_V1 = "RCEP_CHUANGYUAN_EQUIPMENT_V1"
    RCEP_PTI_V1 = "RCEP_PTI_V1"
    CHINA_KOREA_YUANYI_V1 = "CHINA_KOREA_YUANYI_V1"
    CHINA_KOREA_CHUANGYUAN_EQUIPMENT_V1 = "CHINA_KOREA_CHUANGYUAN_EQUIPMENT_V1"


class ImportRecordScope(StrEnum):
    """一个 Excel 导入批次应该包含哪类记录。"""

    ALL = "ALL"
    GOODS = "GOODS"
    PACKAGING = "PACKAGING"


@dataclass(frozen=True, slots=True)
class CertificateProfile:
    """一个旧 ``processorProfile`` 对应的完整、只读业务配置。

    ``processor_profile`` 是旧 Java 系统和历史任务仍在使用的兼容键。
    其余字段才是新 RPA 代码应该依赖的语义，浏览器 Handler 不应再根据兼容键猜流程。
    """

    processor_profile: str
    source_format: SourceFormatProfile
    program: CertificateProgram
    portal_flow: PortalFlowProfile
    display_name: str

    def __post_init__(self) -> None:
        """尽早拒绝写错的目录项，避免错误配置一直流入浏览器阶段。"""

        normalized = self.processor_profile.strip().lower()
        if not normalized:
            raise ValueError("processor_profile 不能为空")
        if normalized != self.processor_profile:
            raise ValueError("processor_profile 必须使用无首尾空格的小写稳定代码")
        if not self.display_name.strip():
            raise ValueError("产地证配置的 display_name 不能为空")


@dataclass(frozen=True, slots=True)
class ImportBatchPlan:
    """一次 Excel 导入动作的声明式计划。

    ``record_scope`` 决定当前批次选哪些行；``auto_generate_goods_description`` 对应
    单一窗口导入弹窗中的“自动生成货物描述”选项。这里只描述意图，真正的 Excel
    拆分与网页点击由后续 Adapter/Handler 完成。
    """

    batch_code: str
    display_name: str
    record_scope: ImportRecordScope
    auto_generate_goods_description: bool

    def __post_init__(self) -> None:
        if not self.batch_code.strip():
            raise ValueError("导入批次 batch_code 不能为空")
        if not self.display_name.strip():
            raise ValueError("导入批次 display_name 不能为空")

    def accepts(self, *, is_non_goods: bool) -> bool:
        """判断一行是否属于当前批次。

        旧产地证草稿使用 ``isNonGoods=1`` 标记包装附加行。调用方先把旧字段转换为
        ``bool``，再调用本方法；领域计划因此不需要依赖旧 JSON 字段名。
        """

        if self.record_scope is ImportRecordScope.ALL:
            return True
        if self.record_scope is ImportRecordScope.GOODS:
            return not is_non_goods
        return is_non_goods


@dataclass(frozen=True, slots=True)
class CertificateExecutionPlan:
    """执行一票产地证任务所需的完整静态计划。

    对拆分流程而言，计划会声明两个候选批次。后续文件准备器应只生成实际有数据的批次；
    如果两个批次都没有数据，则应在打开浏览器前把任务转人工，而不是上传空文件。
    """

    profile: CertificateProfile
    requires_invoice_information: bool
    import_batches: tuple[ImportBatchPlan, ...]

    def __post_init__(self) -> None:
        if not self.import_batches:
            raise ValueError("产地证执行计划至少需要一个导入批次")
        batch_codes = [batch.batch_code for batch in self.import_batches]
        if len(batch_codes) != len(set(batch_codes)):
            raise ValueError("产地证执行计划中的 batch_code 不能重复")

    @property
    def processor_profile(self) -> str:
        """返回旧任务兼容键，方便日志和迁移期审计。"""

        return self.profile.processor_profile

    @property
    def program(self) -> CertificateProgram:
        """返回业务协定，调用方无需穿透 ``profile``。"""

        return self.profile.program

    @property
    def portal_flow(self) -> PortalFlowProfile:
        """返回网页执行策略，调用方无需再次从字符串推断。"""

        return self.profile.portal_flow


class UnsupportedCertificateProfileError(ValueError):
    """任务携带了目录中不存在的 ``processorProfile``。"""


class DuplicateCertificateProfileError(ValueError):
    """配置目录出现了重复的 ``processorProfile``。"""


_DEFAULT_CERTIFICATE_PROFILES: Final[tuple[CertificateProfile, ...]] = (
    CertificateProfile(
        processor_profile="wudi_form_e",
        source_format=SourceFormatProfile.FORM_E_WUDI_V1,
        program=CertificateProgram.FORM_E,
        portal_flow=PortalFlowProfile.STANDARD,
        display_name="FORM E（无棣版式）",
    ),
    CertificateProfile(
        processor_profile="better_form_e",
        source_format=SourceFormatProfile.FORM_E_BETTER_V1,
        program=CertificateProgram.FORM_E,
        portal_flow=PortalFlowProfile.STANDARD,
        display_name="FORM E（BETTER 版式）",
    ),
    CertificateProfile(
        processor_profile="heying_rcep_yuanyi",
        source_format=SourceFormatProfile.RCEP_YUANYI_V1,
        program=CertificateProgram.RCEP,
        portal_flow=PortalFlowProfile.RCEP_WITH_INVOICE,
        display_name="RCEP（远益版式）",
    ),
    CertificateProfile(
        processor_profile="heying_rcep_chuangyuan_raw",
        source_format=SourceFormatProfile.RCEP_CHUANGYUAN_RAW_V1,
        program=CertificateProgram.RCEP,
        portal_flow=PortalFlowProfile.RCEP_WITH_INVOICE,
        display_name="RCEP（创元原料版式）",
    ),
    CertificateProfile(
        processor_profile="heying_rcep_chuangyuan_equipment",
        source_format=SourceFormatProfile.RCEP_CHUANGYUAN_EQUIPMENT_V1,
        program=CertificateProgram.RCEP,
        portal_flow=PortalFlowProfile.RCEP_WITH_INVOICE,
        display_name="RCEP（创元设备版式）",
    ),
    CertificateProfile(
        processor_profile="heying_rcep_pti",
        source_format=SourceFormatProfile.RCEP_PTI_V1,
        program=CertificateProgram.RCEP,
        portal_flow=PortalFlowProfile.RCEP_WITH_INVOICE,
        display_name="RCEP（PTI 版式）",
    ),
    CertificateProfile(
        processor_profile="wang_korea_yuanyi",
        source_format=SourceFormatProfile.CHINA_KOREA_YUANYI_V1,
        program=CertificateProgram.CHINA_KOREA_FTA,
        portal_flow=PortalFlowProfile.STANDARD,
        display_name="中国—韩国产地证（远益版式）",
    ),
    CertificateProfile(
        processor_profile="wang_korea_chuangyuan_equipment",
        source_format=SourceFormatProfile.CHINA_KOREA_CHUANGYUAN_EQUIPMENT_V1,
        program=CertificateProgram.CHINA_KOREA_FTA,
        portal_flow=PortalFlowProfile.SPLIT_GOODS_PACKAGING,
        display_name="中国—韩国产地证（创元设备版式）",
    ),
)


class CertificateProfileCatalog:
    """集中解析旧 ``processorProfile`` 的只读配置目录。

    默认目录就是已从旧 Java 系统核对过的 8 个配置。构造函数允许测试或以后由启动配置
    传入自定义条目，但同一个兼容键只能出现一次。
    """

    def __init__(
        self,
        profiles: Iterable[CertificateProfile] = _DEFAULT_CERTIFICATE_PROFILES,
    ) -> None:
        by_processor_profile: dict[str, CertificateProfile] = {}
        for profile in profiles:
            key = self._normalize_lookup_key(profile.processor_profile)
            if key in by_processor_profile:
                raise DuplicateCertificateProfileError(
                    f"重复的产地证 processorProfile：{profile.processor_profile}"
                )
            by_processor_profile[key] = profile
        if not by_processor_profile:
            raise ValueError("产地证配置目录不能为空")
        # MappingProxyType 防止运行中误改目录，保证同一任务重试时仍得到同一套计划。
        self._by_processor_profile = MappingProxyType(by_processor_profile)

    @staticmethod
    def _normalize_lookup_key(processor_profile: str) -> str:
        """只对查询输入做大小写和首尾空格兼容，不猜测别名。"""

        if not isinstance(processor_profile, str):
            raise UnsupportedCertificateProfileError("产地证 processorProfile 必须是字符串")
        return processor_profile.strip().lower()

    def resolve(self, processor_profile: str) -> CertificateProfile:
        """解析一个兼容键；未知或空值必须明确失败，绝不退回默认流程。"""

        key = self._normalize_lookup_key(processor_profile)
        try:
            return self._by_processor_profile[key]
        except KeyError as error:
            supplied = processor_profile.strip() or "<空>"
            raise UnsupportedCertificateProfileError(
                f"不支持的产地证 processorProfile：{supplied}"
            ) from error

    @property
    def profiles(self) -> tuple[CertificateProfile, ...]:
        """按声明顺序返回全部配置，供管理页面或测试展示。"""

        return tuple(self._by_processor_profile.values())

    @property
    def supported_processor_profiles(self) -> tuple[str, ...]:
        """返回可写入任务契约的 8 个兼容键。"""

        return tuple(self._by_processor_profile)


class ExecutionPlanBuilder:
    """根据配置目录生成网页执行计划。

    Builder 只做确定性规则翻译，不读取数据库、不调用 LLM，也不操作浏览器。RPA Handler
    只需执行返回的计划，因此不会再出现多个位置各自判断 ``heying_rcep_*`` 的情况。
    """

    _SINGLE_BATCH: Final[tuple[ImportBatchPlan, ...]] = (
        ImportBatchPlan(
            batch_code="ALL_ROWS",
            display_name="全部货物",
            record_scope=ImportRecordScope.ALL,
            auto_generate_goods_description=True,
        ),
    )
    _SPLIT_BATCHES: Final[tuple[ImportBatchPlan, ...]] = (
        ImportBatchPlan(
            batch_code="GOODS",
            display_name="正常货物",
            record_scope=ImportRecordScope.GOODS,
            auto_generate_goods_description=True,
        ),
        ImportBatchPlan(
            batch_code="PACKAGING",
            display_name="包装附加行",
            record_scope=ImportRecordScope.PACKAGING,
            auto_generate_goods_description=False,
        ),
    )

    def __init__(self, catalog: CertificateProfileCatalog | None = None) -> None:
        self.catalog = catalog or CertificateProfileCatalog()

    def build(self, processor_profile: str) -> CertificateExecutionPlan:
        """生成静态计划；未知配置会由 Catalog 抛出明确异常。"""

        profile = self.catalog.resolve(processor_profile)
        if profile.portal_flow is PortalFlowProfile.STANDARD:
            return CertificateExecutionPlan(
                profile=profile,
                requires_invoice_information=False,
                import_batches=self._SINGLE_BATCH,
            )
        if profile.portal_flow is PortalFlowProfile.RCEP_WITH_INVOICE:
            return CertificateExecutionPlan(
                profile=profile,
                requires_invoice_information=True,
                import_batches=self._SINGLE_BATCH,
            )
        if profile.portal_flow is PortalFlowProfile.SPLIT_GOODS_PACKAGING:
            return CertificateExecutionPlan(
                profile=profile,
                requires_invoice_information=False,
                import_batches=self._SPLIT_BATCHES,
            )
        # StrEnum 当前只有三种值，但保留防御分支，未来新增值时不会静默套用旧计划。
        raise ValueError(f"尚未实现产地证网页流程：{profile.portal_flow}")
