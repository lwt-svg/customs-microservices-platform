"""统一 RPA Job 的 SQLAlchemy 仓储。

仓储只负责查询、行锁和 ``flush``。合法状态迁移、attempt token 校验、未知结果对账等
业务规则全部放在 Service，避免数据库访问层悄悄形成第二套状态机。
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.rpa_job import RpaJobCallbackReceipt, RpaJobRecord
from app.models.shipment_agent_run import (
    SHIPMENT_AGENT_RPA_JOB_STATE_CHANGED_WAKE_REASON,
    ShipmentAgentRpaJobBinding,
    ShipmentAgentRun,
    ShipmentAgentRunStatus,
)
from app.rpa.contracts import (
    RpaDomain,
    RpaEnvironment,
    RpaJobStatus,
    RpaOperation,
)


class RpaJobRepository:
    """RPA Job 与事件的数据库访问入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _with_events():
        return selectinload(RpaJobRecord.events)

    async def get(
        self,
        job_id: str,
        *,
        for_update: bool = False,
    ) -> RpaJobRecord | None:
        statement = (
            select(RpaJobRecord)
            .options(self._with_events())
            .where(RpaJobRecord.id == job_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> RpaJobRecord | None:
        statement = (
            select(RpaJobRecord)
            .options(self._with_events())
            .where(RpaJobRecord.idempotency_key == idempotency_key)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_all(
        self,
        *,
        domain: RpaDomain | None = None,
        status: RpaJobStatus | None = None,
        case_number: str | None = None,
    ) -> list[RpaJobRecord]:
        statement = select(RpaJobRecord).options(self._with_events())
        if domain is not None:
            statement = statement.where(RpaJobRecord.domain == domain)
        if status is not None:
            statement = statement.where(RpaJobRecord.status == status)
        if case_number is not None:
            statement = statement.where(RpaJobRecord.case_number == case_number)
        statement = statement.order_by(
            RpaJobRecord.updated_at.desc(),
            RpaJobRecord.created_at.desc(),
            RpaJobRecord.id.desc(),
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def next_queued_for_update(
        self,
        *,
        domain: RpaDomain,
        environment: RpaEnvironment,
        operations: list[RpaOperation],
    ) -> RpaJobRecord | None:
        """领取队列中最早的一票。

        MySQL 使用 ``FOR UPDATE SKIP LOCKED``，多个 Worker 不会同时领取同一行。SQLite
        单元测试会忽略这两个数据库特性，但仍能覆盖状态与 attempt token 规则。
        """

        statement = (
            select(RpaJobRecord)
            .options(self._with_events())
            .where(
                RpaJobRecord.domain == domain,
                RpaJobRecord.environment == environment,
                RpaJobRecord.operation.in_(operations),
                RpaJobRecord.status == RpaJobStatus.QUEUED,
                RpaJobRecord.attempt_count < RpaJobRecord.max_attempts,
            )
            .order_by(RpaJobRecord.created_at.asc(), RpaJobRecord.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def claim_if_still_queued(
        self,
        *,
        job_id: str,
        expected_revision: int,
        values: dict[str, object],
    ) -> bool:
        """用状态和 revision 做第二道原子抢占。

        MySQL 的 ``FOR UPDATE SKIP LOCKED`` 已能协调多 Worker；这条条件 UPDATE 让不支持
        行锁的 SQLite 测试也能验证“最多一个领取成功”，并防止未来仓储改动绕过锁。
        """

        result = await self.session.execute(
            update(RpaJobRecord)
            .where(
                RpaJobRecord.id == job_id,
                RpaJobRecord.status == RpaJobStatus.QUEUED,
                RpaJobRecord.revision == expected_revision,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def list_expired_running_for_update(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[RpaJobRecord]:
        """领取已过期的 RUNNING 租约，交给 Service 判断能否安全重试。"""

        statement = (
            select(RpaJobRecord)
            .options(self._with_events())
            .where(
                RpaJobRecord.status == RpaJobStatus.RUNNING,
                RpaJobRecord.lease_expires_at.is_not(None),
                RpaJobRecord.lease_expires_at <= now,
            )
            .order_by(RpaJobRecord.lease_expires_at.asc(), RpaJobRecord.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def list_due_retryable_for_update(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[RpaJobRecord]:
        """锁住已到重试时间的明确失败任务，交给 Service 做最终安全判断。"""

        statement = (
            select(RpaJobRecord)
            .options(self._with_events())
            .where(
                RpaJobRecord.status == RpaJobStatus.RETRYABLE_FAILED,
                RpaJobRecord.next_attempt_at.is_not(None),
                RpaJobRecord.next_attempt_at <= now,
            )
            .order_by(RpaJobRecord.next_attempt_at.asc(), RpaJobRecord.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def get_callback_receipt(
        self,
        callback_id: str,
        *,
        for_update: bool = False,
    ) -> RpaJobCallbackReceipt | None:
        statement = select(RpaJobCallbackReceipt).where(
            RpaJobCallbackReceipt.callback_id == callback_id
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def wake_bound_shipment_runs(
        self,
        job_id: str,
        *,
        now: datetime,
    ) -> int:
        """在 Job 状态事务内把所有绑定运行标记为立即可唤醒。

        回调线程只写控制面标记，不调用 ``resume``、不写 Shipment 人工命令，也不直接
        执行 LangGraph。这样一个 Job 即使被多个历史/恢复运行复用，后续仍由现有数据库
        租约 Worker 逐条、安全地协调。
        """

        bound_run_ids = select(ShipmentAgentRpaJobBinding.run_id).where(
            ShipmentAgentRpaJobBinding.job_id == job_id
        )
        result = await self.session.execute(
            update(ShipmentAgentRun)
            .where(
                ShipmentAgentRun.id.in_(bound_run_ids),
                ShipmentAgentRun.status.not_in(
                    {
                        ShipmentAgentRunStatus.COMPLETED,
                        ShipmentAgentRunStatus.FAILED,
                        ShipmentAgentRunStatus.CANCELLED,
                    }
                ),
            )
            .values(
                next_wake_at=now,
                wake_reason=SHIPMENT_AGENT_RPA_JOB_STATE_CHANGED_WAKE_REASON,
                # 控制面唤醒不能改变业务列表的更新时间排序。
                updated_at=ShipmentAgentRun.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    def add_callback_receipt(self, receipt: RpaJobCallbackReceipt) -> None:
        self.session.add(receipt)

    def add(self, job: RpaJobRecord) -> None:
        self.session.add(job)

    async def flush(self) -> None:
        await self.session.flush()
