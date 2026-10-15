"""装载任务、货物箱规和托盘规格的数据库模型。

本章只建立“可追溯的主数据基础”，尚不计算三维坐标。所有尺寸统一保存为毫米，
所有重量统一保存为千克。未知的安全参数必须为 NULL，不能用 0、False 或经验值代替。
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LoadPlanningTaskStatus(StrEnum):
    """任务状态；后续章节会在完成校验、规划和审批时扩展状态流转。"""

    DRAFT = "DRAFT"


class SourceType(StrEnum):
    """结构化数据来自哪里。

    ENTERPRISE_HISTORICAL 表示从历史业务资料整理但尚未由仓库确认；
    DOCUMENT_IMPORT 表示本次由 PDF/Excel 解析得到；MANUAL 表示人工录入；
    SYNTHETIC_DEMO 只允许教程和测试使用，不能自动标记为正式数据。
    """

    MANUAL = "MANUAL"
    ENTERPRISE_HISTORICAL = "ENTERPRISE_HISTORICAL"
    DOCUMENT_IMPORT = "DOCUMENT_IMPORT"
    SYNTHETIC_DEMO = "SYNTHETIC_DEMO"


class LoadPlanningTask(Base):
    """装载任务聚合根。

    一份任务包含多条货物箱规和一个或多个候选托盘规格。算法只读取这份任务内的数据，
    不跨库读取 LINK 订舱表，也不直接读取企业原始文件。
    """

    __tablename__ = "load_planning_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    task_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[LoadPlanningTaskStatus] = mapped_column(
        Enum(
            LoadPlanningTaskStatus,
            name="load_planning_task_status",
            native_enum=False,
            length=32,
        ),
        nullable=False,
        default=LoadPlanningTaskStatus.DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # selectin 能在详情查询时批量加载两个集合，避免序列化阶段触发异步懒加载错误。
    cargo_items: Mapped[list["CargoItem"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CargoItem.id",
    )
    pallet_specs: Mapped[list["PalletSpec"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PalletSpec.id",
    )


class CargoItem(Base):
    """任务中的一条箱规。

    ``quantity`` 表示相同箱规的箱数，而不是箱内产品件数。下一章解析 PL 时，可以另外
    保留“每箱件数”等业务字段，但装载算法的最小输入是外箱尺寸、单箱毛重和箱数。
    """

    __tablename__ = "cargo_items"
    __table_args__ = (
        UniqueConstraint("task_id", "item_code", name="uq_cargo_items_task_item_code"),
        CheckConstraint("quantity > 0", name="ck_cargo_items_quantity_positive"),
        CheckConstraint("length_mm > 0", name="ck_cargo_items_length_positive"),
        CheckConstraint("width_mm > 0", name="ck_cargo_items_width_positive"),
        CheckConstraint("height_mm > 0", name="ck_cargo_items_height_positive"),
        CheckConstraint(
            "gross_weight_kg > 0",
            name="ck_cargo_items_gross_weight_positive",
        ),
        CheckConstraint(
            "max_stack_layers IS NULL OR max_stack_layers > 0",
            name="ck_cargo_items_stack_layers_positive",
        ),
        CheckConstraint(
            "max_top_load_kg IS NULL OR max_top_load_kg > 0",
            name="ck_cargo_items_top_load_positive",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_cargo_items_confidence_range",
        ),
        CheckConstraint(
            "source_type <> 'SYNTHETIC_DEMO' OR verified = 0",
            name="ck_cargo_items_demo_not_verified",
        ),
        Index("ix_cargo_items_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("load_planning_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_code: Mapped[str] = mapped_column(String(64))
    sku_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cargo_name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer)

    # Numeric 比 FLOAT 更适合物流尺寸和重量，避免 0.1 一类二进制浮点误差。
    length_mm: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    width_mm: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    height_mm: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    gross_weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3))

    # 这些是安全相关参数。None 明确表示“未知，必须询问或人工确认”。
    max_stack_layers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_top_load_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    stackable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    keep_upright: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fragile: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="load_planning_source_type", native_enum=False, length=32),
        nullable=False,
    )
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_locator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    task: Mapped[LoadPlanningTask] = relationship(back_populates="cargo_items")


class PalletSpec(Base):
    """任务可使用的托盘规格。

    ``max_load_kg`` 和 ``max_total_height_mm`` 都属于安全/现场约束。历史文件没有给出时
    保持 NULL，算法必须报告缺失，而不能自行猜一个行业“常见值”。
    """

    __tablename__ = "pallet_specs"
    __table_args__ = (
        UniqueConstraint("task_id", "pallet_code", name="uq_pallet_specs_task_pallet_code"),
        CheckConstraint("quantity > 0", name="ck_pallet_specs_quantity_positive"),
        CheckConstraint("length_mm > 0", name="ck_pallet_specs_length_positive"),
        CheckConstraint("width_mm > 0", name="ck_pallet_specs_width_positive"),
        CheckConstraint("height_mm > 0", name="ck_pallet_specs_height_positive"),
        CheckConstraint(
            "tare_weight_kg > 0",
            name="ck_pallet_specs_tare_weight_positive",
        ),
        CheckConstraint(
            "max_load_kg IS NULL OR max_load_kg > 0",
            name="ck_pallet_specs_max_load_positive",
        ),
        CheckConstraint(
            "max_total_height_mm IS NULL OR max_total_height_mm > 0",
            name="ck_pallet_specs_total_height_positive",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_pallet_specs_confidence_range",
        ),
        CheckConstraint(
            "source_type <> 'SYNTHETIC_DEMO' OR verified = 0",
            name="ck_pallet_specs_demo_not_verified",
        ),
        Index("ix_pallet_specs_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("load_planning_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    pallet_code: Mapped[str] = mapped_column(String(64))
    pallet_name: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer)
    length_mm: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    width_mm: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    height_mm: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    tare_weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    max_load_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    max_total_height_mm: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)

    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="load_planning_source_type", native_enum=False, length=32),
        nullable=False,
    )
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_locator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    task: Mapped[LoadPlanningTask] = relationship(back_populates="pallet_specs")
