"""Shipment Case 数据就绪度的案件级持久化模型。

装载任务里的 ``CargoItem.quantity`` 表示“本票计划装多少箱”，不能被当成仓库真实
库存。数据就绪度因此单独保存业务人员、ERP 或 WMS 提供的补充事实，并保留每一次
补数记录，而不是回写、覆盖装载任务的原始主数据。

一票案件只有一条当前就绪度记录：

* ``facts`` 是追加式事实历史，用于审计“谁在什么时候根据什么来源补了什么”；
* ``assessment`` 是当前确定性规则评估快照，不是第二份事实来源；
* Shipment Agent 运行表只保存这条记录的投影，便于列表和 Trace 页面直接读取。
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.shipment_case import ShipmentCase


class ShipmentDataReadinessStatus(StrEnum):
    """一票案件当前的数据门禁状态。"""

    # 迁移前已经存在的 V1 运行不强行倒退到新门禁，避免升级后改变历史流程。
    LEGACY_BYPASSED = "LEGACY_BYPASSED"
    # 新运行已经创建，但尚未完成本轮确定性评估。
    NOT_EVALUATED = "NOT_EVALUATED"
    # 存在阻断问题，需要业务人员补数或确认主数据。
    ACTION_REQUIRED = "ACTION_REQUIRED"
    # 全部依据来自可用于正式业务的来源。
    READY = "READY"
    # 规则已满足，但输入中仍包含明确标记的构造演示数据。
    DEMO_READY = "DEMO_READY"


class ShipmentDataSourceMode(StrEnum):
    """本次评估使用的数据性质。"""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    OPERATIONAL = "OPERATIONAL"
    DEMO = "DEMO"


class ShipmentDataSubjectType(StrEnum):
    """补充事实所指向的装载输入类型。"""

    CARGO_ITEM = "CARGO_ITEM"
    PALLET_SPEC = "PALLET_SPEC"


class ShipmentDataFactType(StrEnum):
    """当前门禁真正需要业务人员补充的两类事实。"""

    AVAILABLE_QUANTITY = "AVAILABLE_QUANTITY"
    MASTER_DATA_CONFIRMED = "MASTER_DATA_CONFIRMED"


class ShipmentDataFactSourceType(StrEnum):
    """补充事实的来源。

    来源枚举刻意不复用装载主数据的 ``SourceType``：主数据说明箱规从哪里来，而这里
    说明“本次库存或确认事实”由谁、通过什么渠道取得。
    """

    MANUAL_CONFIRMATION = "MANUAL_CONFIRMATION"
    CSV_IMPORT = "CSV_IMPORT"
    ERP = "ERP"
    WMS = "WMS"
    SYNTHETIC_DEMO = "SYNTHETIC_DEMO"


class ShipmentCaseDataReadiness(Base):
    """一票 Shipment Case 的追加式补数事实与最新评估快照。"""

    __tablename__ = "shipment_case_data_readiness"
    __table_args__ = (
        CheckConstraint(
            "revision > 0",
            name="ck_shipment_case_data_readiness_revision_positive",
        ),
    )

    # 一对一表直接使用 case_id 作为主键，数据库层即可阻止一票案件出现两条当前记录。
    case_id: Mapped[str] = mapped_column(
        ForeignKey("shipment_cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[ShipmentDataReadinessStatus] = mapped_column(
        Enum(
            ShipmentDataReadinessStatus,
            name="shipment_data_readiness_status",
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[ShipmentDataSourceMode] = mapped_column(
        Enum(
            ShipmentDataSourceMode,
            name="shipment_data_source_mode",
            native_enum=False,
            length=24,
        ),
        nullable=False,
    )
    # JSON 列必须整体重新赋值，Service 不会对列表原地 append，确保 SQLAlchemy 能检测变更。
    facts: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    assessment: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    shipment_case: Mapped["ShipmentCase"] = relationship(back_populates="data_readiness")
