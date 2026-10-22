"""统一 RPA Job 控制面服务。

这一层负责把纯领域契约变成可供两个 Worker 使用的可靠队列：

* Shipment Agent 或测试页面派发 Job；
* LINK / SINGLE_WINDOW Worker 按运行域领取；
* Worker 用本次 ``attempt_token`` 汇报进度和结果；
* ``RESULT_UNKNOWN`` 必须先对账，不能直接重新入队；
* 相同业务数据得到同一个 Job，相同 callback 重放不会重复推进状态。

浏览器点击、XPath、UKey 和 Excel 处理不属于本服务。它们分别留在 Adapter/Handler 中。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.rpa_job import (
    RpaJobCallbackReceipt,
    RpaJobEvent,
    RpaJobRecord,
)
from app.repositories.rpa_jobs import RpaJobRepository
from app.rpa.certificate_profiles import (
    ExecutionPlanBuilder,
    UnsupportedCertificateProfileError,
)
from app.rpa.contracts import (
    RPA_CONTRACT_VERSION,
    RpaBusinessOutcome,
    RpaEnvironment,
    RpaJob,
    RpaJobStatus,
    RpaOperation,
    RpaResult,
    RpaSideEffectPhase,
    assert_rpa_job_transition,
    assert_rpa_side_effect_phase_transition,
    can_automatically_retry,
)
from app.schemas.rpa_job import (
    RpaJobClaim,
    RpaJobClaimRead,
    RpaJobDispatch,
    RpaJobEventRead,
    RpaJobProgress,
    RpaJobRead,
    RpaJobReconcile,
    RpaJobRequeue,
    RpaJobResultReport,
    RpaReconciliationDecision,
)


class RpaJobNotFoundError(Exception):
    """RPA Job 不存在。"""


class RpaJobConflictError(Exception):
    """当前状态、revision、attempt token 或幂等内容发生冲突。"""


class RpaJobValidationError(Exception):
    """请求 JSON 合法，但不满足 RPA 业务安全规则。"""


def _utc_now_for_database() -> datetime:
    """项目的 MySQL/SQLite DATETIME 统一保存 UTC naive 值。"""

    return datetime.now(UTC).replace(tzinfo=None)


def _canonical_hash(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_PHASE_RANK = {
    RpaSideEffectPhase.READ_ONLY: 0,
    RpaSideEffectPhase.LOCAL_PREPARED: 1,
    RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED: 2,
    RpaSideEffectPhase.EXTERNAL_EFFECT_CONFIRMED: 3,
}


class RpaJobService:
    """统一 RPA Job 的派发、领取、回报与安全恢复服务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        certificate_plan_builder: ExecutionPlanBuilder | None = None,
        worker_lease_seconds: int = 120,
        production_dispatch_enabled: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.certificate_plan_builder = certificate_plan_builder or ExecutionPlanBuilder()
        self.worker_lease_seconds = worker_lease_seconds
        self.production_dispatch_enabled = production_dispatch_enabled

    async def dispatch(self, payload: RpaJobDispatch) -> RpaJobRead:
        """创建并立即排队；相同业务命令稳定返回同一 Job。"""

        self._validate_environment(payload)
        source_profile, flow_profile = self._resolve_execution_profiles(payload)
        domain_job = RpaJob(
            domain=payload.domain,
            operation=payload.operation,
            environment=payload.environment,
            business_reference=payload.business_reference,
            side_effect_boundary=payload.side_effect_boundary,
            business_payload=payload.business_payload,
        ).transition_to(RpaJobStatus.QUEUED)

        try:
            async with self.session_factory() as session:
                repository = RpaJobRepository(session)
                async with session.begin():
                    existing = await repository.get_by_idempotency_key(
                        domain_job.idempotency_key,
                        for_update=True,
                    )
                    if existing is not None:
                        self._ensure_same_dispatch(
                            existing,
                            payload,
                            source_profile=source_profile,
                            flow_profile=flow_profile,
                        )
                        return self._to_read(existing)

                    created_at = domain_job.created_at.replace(tzinfo=None)
                    record = RpaJobRecord(
                        id=str(domain_job.job_id),
                        contract_version=RPA_CONTRACT_VERSION,
                        case_number=payload.case_number,
                        originating_run_id=(
                            str(payload.originating_run_id)
                            if payload.originating_run_id is not None
                            else None
                        ),
                        business_reference=domain_job.business_reference,
                        business_payload=domain_job.business_payload,
                        business_payload_hash=domain_job.business_payload_hash,
                        idempotency_key=domain_job.idempotency_key,
                        domain=domain_job.domain,
                        operation=domain_job.operation,
                        environment=domain_job.environment,
                        side_effect_boundary=domain_job.side_effect_boundary,
                        effect_phase=domain_job.side_effect_phase,
                        adapter_key=payload.adapter_key,
                        source_profile=source_profile,
                        flow_profile=flow_profile,
                        selector_pack_version=payload.selector_pack_version,
                        payload_schema_version=payload.payload_schema_version,
                        status=RpaJobStatus.QUEUED,
                        current_step="WAITING_WORKER",
                        revision=1,
                        attempt_count=0,
                        max_attempts=payload.max_attempts,
                        attempt_token=None,
                        claimed_by=None,
                        lease_expires_at=None,
                        next_attempt_at=None,
                        page_fingerprint=None,
                        business_outcome=None,
                        external_reference=None,
                        error_code=None,
                        error_message=None,
                        evidence={},
                        result_payload=None,
                        result_hash=None,
                        created_at=created_at,
                        updated_at=created_at,
                        started_at=None,
                        completed_at=None,
                    )
                    record.events.append(
                        self._event(
                            record,
                            event_type="JOB_DISPATCHED",
                            previous_status=RpaJobStatus.CREATED,
                            message=(
                                f"{record.operation.value} 已进入 "
                                f"{record.domain.value} Worker 队列。"
                            ),
                            data={
                                "adapter_key": record.adapter_key,
                                "source_profile": record.source_profile,
                                "flow_profile": record.flow_profile,
                                "selector_pack_version": (record.selector_pack_version),
                            },
                        )
                    )
                    repository.add(record)
                    await repository.flush()
                    return self._to_read(record)
        except IntegrityError:
            # 两个相同派发并发插入时，唯一幂等键只允许一个成功。新会话读取赢家，
            # 返回与正常重放完全相同的结果。
            async with self.session_factory() as session:
                existing = await RpaJobRepository(session).get_by_idempotency_key(
                    domain_job.idempotency_key
                )
                if existing is None:
                    raise
                self._ensure_same_dispatch(
                    existing,
                    payload,
                    source_profile=source_profile,
                    flow_profile=flow_profile,
                )
                return self._to_read(existing)

    async def get(self, job_id: str) -> RpaJobRead:
        async with self.session_factory() as session:
            job = await RpaJobRepository(session).get(job_id)
            if job is None:
                raise RpaJobNotFoundError("RPA Job 不存在")
            return self._to_read(job)

    async def list_all(
        self,
        *,
        domain=None,
        status=None,
        case_number: str | None = None,
    ) -> list[RpaJobRead]:
        async with self.session_factory() as session:
            jobs = await RpaJobRepository(session).list_all(
                domain=domain,
                status=status,
                case_number=case_number,
            )
            return [self._to_read(job) for job in jobs]

    async def claim_next(self, payload: RpaJobClaim) -> RpaJobClaimRead | None:
        """领取一票排队任务并生成只属于本次尝试的 token。"""

        async with self.session_factory() as session:
            repository = RpaJobRepository(session)
            async with session.begin():
                job = await repository.next_queued_for_update(
                    domain=payload.domain,
                    environment=payload.environment,
                    operations=payload.supported_operations,
                )
                if job is None:
                    return None

                assert_rpa_job_transition(job.status, RpaJobStatus.RUNNING)
                previous_status = job.status
                now = _utc_now_for_database()
                token = str(uuid4())
                next_attempt_count = job.attempt_count + 1
                claimed = await repository.claim_if_still_queued(
                    job_id=job.id,
                    expected_revision=job.revision,
                    values={
                        "status": RpaJobStatus.RUNNING,
                        "current_step": "CLAIMED",
                        "revision": job.revision + 1,
                        "attempt_count": next_attempt_count,
                        "attempt_token": token,
                        "claimed_by": payload.worker_id,
                        "lease_expires_at": now + timedelta(seconds=self.worker_lease_seconds),
                        "next_attempt_at": None,
                        "started_at": job.started_at or now,
                        "error_code": None,
                        "error_message": None,
                        "result_payload": None,
                        "result_hash": None,
                        "updated_at": now,
                    },
                )
                if not claimed:
                    # 另一个 Worker 已经抢到同一候选。调用方下一轮轮询即可，不把竞争
                    # 当作业务错误，也绝不能继续使用本地旧快照生成事件。
                    return None
                refreshed = await repository.get(job.id, for_update=True)
                if refreshed is None:  # pragma: no cover - 同一事务中不应消失
                    raise RpaJobNotFoundError("RPA Job 不存在")
                job = refreshed
                job.events.append(
                    self._event(
                        job,
                        event_type="JOB_CLAIMED",
                        previous_status=previous_status,
                        message=(
                            f"Worker {payload.worker_id} 已领取第 {next_attempt_count} 次执行。"
                        ),
                        data={
                            "worker_id": payload.worker_id,
                            "attempt_count": next_attempt_count,
                            "lease_seconds": self.worker_lease_seconds,
                        },
                    )
                )
                await repository.flush()
                return RpaJobClaimRead(
                    job=self._to_read(job),
                    attempt_token=UUID(token),
                    idempotency_key=job.idempotency_key,
                    business_payload=job.business_payload,
                )

    async def report_progress(
        self,
        job_id: str,
        payload: RpaJobProgress,
    ) -> RpaJobRead:
        """记录关键进度；副作用阶段只能前进。"""

        async with self.session_factory() as session:
            repository = RpaJobRepository(session)
            async with session.begin():
                job = await self._get_locked(repository, job_id)
                self._require_attempt(job, str(payload.attempt_token))
                if job.status is not RpaJobStatus.RUNNING:
                    raise RpaJobConflictError(f"{job.status.value} Job 不能继续汇报运行进度")
                now = _utc_now_for_database()
                if job.lease_expires_at is None or job.lease_expires_at <= now:
                    raise RpaJobConflictError(
                        "Worker 租约已经过期，不能继续推进页面；请停止当前浏览器执行"
                    )
                try:
                    assert_rpa_side_effect_phase_transition(
                        job.effect_phase,
                        payload.side_effect_phase,
                    )
                except ValueError as error:
                    raise RpaJobConflictError(str(error)) from error

                previous_phase = job.effect_phase
                job.effect_phase = payload.side_effect_phase
                job.current_step = payload.current_step
                job.page_fingerprint = payload.page_fingerprint
                job.evidence = {**(job.evidence or {}), **payload.evidence}
                # 进度上报同时充当心跳。只要 token、状态和租约仍有效，长流程就可以继续；
                # 租期由服务端决定，Worker 不能自行请求任意时长。
                job.lease_expires_at = now + timedelta(seconds=self.worker_lease_seconds)
                job.revision += 1
                job.updated_at = now
                job.events.append(
                    self._event(
                        job,
                        event_type="JOB_PROGRESS_REPORTED",
                        previous_status=job.status,
                        message=payload.message,
                        data={
                            "previous_side_effect_phase": previous_phase.value,
                            "current_side_effect_phase": (payload.side_effect_phase.value),
                            "page_fingerprint": payload.page_fingerprint,
                        },
                    )
                )
                await repository.flush()
                return self._to_read(job)

    async def report_result(
        self,
        job_id: str,
        payload: RpaJobResultReport,
    ) -> RpaJobRead:
        """接受一次幂等 Worker 结果回调。"""

        result_payload = payload.model_dump(
            mode="json",
            exclude={"callback_id"},
        )
        payload_hash = _canonical_hash(result_payload)
        callback_id = str(payload.callback_id)

        try:
            async with self.session_factory() as session:
                repository = RpaJobRepository(session)
                async with session.begin():
                    existing_receipt = await repository.get_callback_receipt(
                        callback_id,
                        for_update=True,
                    )
                    if existing_receipt is not None:
                        if (
                            existing_receipt.job_id != job_id
                            or existing_receipt.payload_hash != payload_hash
                        ):
                            raise RpaJobConflictError("callback_id 已用于另一条 RPA 结果")
                        existing_receipt.occurrence_count += 1
                        job = await self._get_locked(repository, job_id)
                        await repository.flush()
                        return self._to_read(job)

                    job = await self._get_locked(repository, job_id)
                    self._require_attempt(job, str(payload.attempt_token))
                    result = RpaResult(
                        job_id=UUID(job.id),
                        operation=job.operation,
                        idempotency_key=job.idempotency_key,
                        business_payload_hash=job.business_payload_hash,
                        status=payload.status,
                        side_effect_phase=payload.side_effect_phase,
                        business_outcome=payload.business_outcome,
                        message=payload.message,
                        error_code=payload.error_code,
                        external_reference=payload.external_reference,
                        evidence=payload.evidence,
                        retry_after_seconds=payload.retry_after_seconds,
                    )
                    if (
                        result.status is RpaJobStatus.SUCCEEDED
                        and result.side_effect_phase
                        is not RpaSideEffectPhase.EXTERNAL_EFFECT_CONFIRMED
                    ):
                        raise RpaJobValidationError("SUCCEEDED 必须已经确认外部草稿/暂存结果")
                    if (
                        result.status is RpaJobStatus.RETRYABLE_FAILED
                        and _PHASE_RANK[result.side_effect_phase]
                        >= _PHASE_RANK[RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED]
                    ):
                        raise RpaJobValidationError(
                            "外部写操作开始后不能报告 RETRYABLE_FAILED；"
                            "结果不确定时必须报告 RESULT_UNKNOWN"
                        )
                    if (
                        result.status is RpaJobStatus.RETRYABLE_FAILED
                        and job.attempt_count >= job.max_attempts
                    ):
                        # Worker 只报告“这次错误可重试”；是否还拥有重试预算由控制面决定。
                        # 达到上限时在同一事务中收敛为明确失败，避免 UI 显示可重试、API
                        # 却永远拒绝重排的矛盾状态。
                        result = result.model_copy(
                            update={
                                "status": RpaJobStatus.FAILED,
                                "error_code": "MAX_ATTEMPTS_EXHAUSTED",
                                "message": (
                                    f"{result.message}；已达到最大尝试次数 {job.max_attempts}。"
                                ),
                                "retry_after_seconds": None,
                            }
                        )

                    # 同一次执行的响应可能丢失，Worker 也可能错误地换了 callback_id
                    # 重发完全相同的结果。结果哈希相同就只补收据，不重复增加 revision
                    # 或 Trace；不同内容仍继续走严格状态机。
                    if job.result_hash == payload_hash:
                        repository.add_callback_receipt(
                            RpaJobCallbackReceipt(
                                callback_id=callback_id,
                                job_id=job.id,
                                attempt_number=job.attempt_count,
                                payload_hash=payload_hash,
                                result_status=job.status,
                                occurrence_count=1,
                                received_at=_utc_now_for_database(),
                            )
                        )
                        await repository.flush()
                        return self._to_read(job)

                    # 终态回调响应丢失后，Worker 可能换了 callback_id 再发一次。内容完全
                    # 一致时仍按幂等成功处理；换内容则必须冲突，不能覆盖历史事实。
                    if job.status not in {
                        RpaJobStatus.RUNNING,
                        RpaJobStatus.RESULT_UNKNOWN,
                    }:
                        if job.result_hash == payload_hash:
                            repository.add_callback_receipt(
                                RpaJobCallbackReceipt(
                                    callback_id=callback_id,
                                    job_id=job.id,
                                    attempt_number=job.attempt_count,
                                    payload_hash=payload_hash,
                                    result_status=result.status,
                                    occurrence_count=1,
                                    received_at=_utc_now_for_database(),
                                )
                            )
                            await repository.flush()
                            return self._to_read(job)
                        raise RpaJobConflictError(
                            f"{job.status.value} Job 不能接受新的 Worker 结果"
                        )

                    self._apply_result(job, result, payload_hash)
                    repository.add_callback_receipt(
                        RpaJobCallbackReceipt(
                            callback_id=callback_id,
                            job_id=job.id,
                            attempt_number=job.attempt_count,
                            payload_hash=payload_hash,
                            result_status=result.status,
                            occurrence_count=1,
                            received_at=_utc_now_for_database(),
                        )
                    )
                    # 回调只把绑定的 Shipment Run 标记为立即可唤醒。真正的业务恢复仍由
                    # ShipmentAgentWakeWorker → automatic_wake → LangGraph 完成，绝不
                    # 在 Worker 回调线程里伪造人工 resume 命令。
                    await repository.wake_bound_shipment_runs(
                        job.id,
                        now=_utc_now_for_database(),
                    )
                    await repository.flush()
                    return self._to_read(job)
        except IntegrityError:
            # 并发提交同一个 callback_id 时，唯一主键只允许一个收据成功。
            async with self.session_factory() as session:
                repository = RpaJobRepository(session)
                receipt = await repository.get_callback_receipt(callback_id)
                if (
                    receipt is None
                    or receipt.job_id != job_id
                    or receipt.payload_hash != payload_hash
                ):
                    raise
                job = await repository.get(job_id)
                if job is None:
                    raise RpaJobNotFoundError("RPA Job 不存在")
                return self._to_read(job)

    async def recover_expired_leases(self, *, limit: int = 100) -> list[RpaJobRead]:
        """恢复 Worker 崩溃后遗留的过期租约。

        * 尚未开始外部写操作：可以安全回队列（未超过最大尝试次数时）；
        * 已经开始外部写操作：转 ``RESULT_UNKNOWN``，等待迟到回调或人工/查询对账；
        * 尝试次数耗尽：明确失败，不再形成无限重试。
        """

        now = _utc_now_for_database()
        async with self.session_factory() as session:
            repository = RpaJobRepository(session)
            async with session.begin():
                jobs = await repository.list_expired_running_for_update(
                    now=now,
                    limit=limit,
                )
                for job in jobs:
                    previous_status = job.status
                    if (
                        _PHASE_RANK[job.effect_phase]
                        >= _PHASE_RANK[RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED]
                    ):
                        assert_rpa_job_transition(
                            job.status,
                            RpaJobStatus.RESULT_UNKNOWN,
                        )
                        job.status = RpaJobStatus.RESULT_UNKNOWN
                        job.current_step = "LEASE_EXPIRED_RECONCILIATION_REQUIRED"
                        job.error_code = "WORKER_LEASE_EXPIRED_AFTER_MUTATION"
                        job.error_message = (
                            "Worker 租约在外部写操作开始后过期；禁止自动重跑，必须先核对外部网站。"
                        )
                        # 保留 attempt_token，允许本次 Worker 的迟到结果完成对账。
                        event_type = "JOB_LEASE_EXPIRED_RESULT_UNKNOWN"
                        message = job.error_message
                    elif job.attempt_count >= job.max_attempts:
                        assert_rpa_job_transition(
                            job.status,
                            RpaJobStatus.FAILED,
                        )
                        job.status = RpaJobStatus.FAILED
                        job.current_step = "MAX_ATTEMPTS_EXHAUSTED"
                        job.error_code = "MAX_ATTEMPTS_EXHAUSTED"
                        job.error_message = (
                            f"Worker 租约已过期，且已达到最大尝试次数 {job.max_attempts}。"
                        )
                        job.completed_at = now
                        job.attempt_token = None
                        event_type = "JOB_MAX_ATTEMPTS_EXHAUSTED"
                        message = job.error_message
                    else:
                        # 用两个合法状态迁移表达“本次明确可重试失败，然后重新排队”。
                        assert_rpa_job_transition(
                            job.status,
                            RpaJobStatus.RETRYABLE_FAILED,
                        )
                        assert_rpa_job_transition(
                            RpaJobStatus.RETRYABLE_FAILED,
                            RpaJobStatus.QUEUED,
                        )
                        job.status = RpaJobStatus.QUEUED
                        job.current_step = "WAITING_WORKER"
                        job.error_code = None
                        job.error_message = None
                        job.attempt_token = None
                        job.claimed_by = None
                        job.page_fingerprint = None
                        job.effect_phase = RpaSideEffectPhase.READ_ONLY
                        event_type = "JOB_LEASE_EXPIRED_REQUEUED"
                        message = "Worker 租约在外部写操作前过期，任务已安全重新入队。"

                    job.lease_expires_at = None
                    job.next_attempt_at = None
                    job.revision += 1
                    job.updated_at = now
                    job.events.append(
                        self._event(
                            job,
                            event_type=event_type,
                            previous_status=previous_status,
                            message=message,
                            data={
                                "attempt_count": job.attempt_count,
                                "max_attempts": job.max_attempts,
                                "side_effect_phase": job.effect_phase.value,
                            },
                        )
                    )
                    await repository.wake_bound_shipment_runs(
                        job.id,
                        now=now,
                    )
                await repository.flush()
                return [self._to_read(job) for job in jobs]

    async def requeue_due_retryable(
        self,
        *,
        limit: int = 100,
    ) -> list[RpaJobRead]:
        """执行受控 Agent Loop：只把“已明确可安全重试”的失败重新入队。

        浏览器 Worker 通过 ``retry_after_seconds`` 给出最早重试时间；真正是否重新入队
        仍由服务端状态机判断。只要历史数据表明外部写操作已经开始，本方法就停止自动循环
        并转人工，避免网络抖动导致重复订舱、重复暂存或重复申报。
        """

        now = _utc_now_for_database()
        async with self.session_factory() as session:
            repository = RpaJobRepository(session)
            async with session.begin():
                jobs = await repository.list_due_retryable_for_update(
                    now=now,
                    limit=limit,
                )
                for job in jobs:
                    previous_status = job.status
                    if (
                        _PHASE_RANK[job.effect_phase]
                        >= _PHASE_RANK[RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED]
                    ):
                        assert_rpa_job_transition(
                            previous_status,
                            RpaJobStatus.WAITING_HUMAN,
                        )
                        job.status = RpaJobStatus.WAITING_HUMAN
                        job.current_step = "UNSAFE_AUTO_RETRY_BLOCKED"
                        job.error_code = "UNSAFE_AUTO_RETRY_BLOCKED"
                        job.error_message = (
                            "检测到外部写操作已经开始，自动重试已停止，请先人工核对。"
                        )
                        event_type = "JOB_AUTO_RETRY_BLOCKED"
                        message = job.error_message
                    elif job.attempt_count >= job.max_attempts:
                        assert_rpa_job_transition(
                            previous_status,
                            RpaJobStatus.FAILED,
                        )
                        job.status = RpaJobStatus.FAILED
                        job.current_step = "MAX_ATTEMPTS_EXHAUSTED"
                        job.error_code = "MAX_ATTEMPTS_EXHAUSTED"
                        job.error_message = (
                            f"任务已达到最大尝试次数 {job.max_attempts}，受控 Agent Loop 已停止。"
                        )
                        job.completed_at = now
                        event_type = "JOB_MAX_ATTEMPTS_EXHAUSTED"
                        message = job.error_message
                    else:
                        assert_rpa_job_transition(
                            previous_status,
                            RpaJobStatus.QUEUED,
                        )
                        job.status = RpaJobStatus.QUEUED
                        job.current_step = "WAITING_WORKER"
                        job.effect_phase = RpaSideEffectPhase.READ_ONLY
                        job.error_code = None
                        job.error_message = None
                        event_type = "JOB_AUTO_REQUEUED"
                        message = "明确可重试的失败已按计划重新进入 Worker 队列。"

                    job.attempt_token = None
                    job.claimed_by = None
                    job.lease_expires_at = None
                    job.next_attempt_at = None
                    job.page_fingerprint = None
                    job.revision += 1
                    job.updated_at = now
                    job.events.append(
                        self._event(
                            job,
                            event_type=event_type,
                            previous_status=previous_status,
                            message=message,
                            data={
                                "attempt_count": job.attempt_count,
                                "max_attempts": job.max_attempts,
                            },
                        )
                    )
                    await repository.wake_bound_shipment_runs(
                        job.id,
                        now=now,
                    )
                await repository.flush()
                return [self._to_read(job) for job in jobs]

    async def reconcile(
        self,
        job_id: str,
        payload: RpaJobReconcile,
    ) -> RpaJobRead:
        """根据外部查询或人工核对，把未知状态解析成明确事实。"""

        async with self.session_factory() as session:
            repository = RpaJobRepository(session)
            async with session.begin():
                job = await self._get_locked(repository, job_id)
                if job.revision != payload.expected_revision:
                    raise RpaJobConflictError(f"RPA Job revision 已变化，当前为 {job.revision}")
                if job.status not in {
                    RpaJobStatus.RESULT_UNKNOWN,
                    RpaJobStatus.SITE_VERSION_UNKNOWN,
                }:
                    raise RpaJobConflictError(f"{job.status.value} Job 不需要未知结果对账")

                previous_status = job.status
                decision = payload.decision
                if decision is RpaReconciliationDecision.CONFIRMED_SUCCEEDED:
                    if previous_status is not RpaJobStatus.RESULT_UNKNOWN:
                        raise RpaJobValidationError("只有 RESULT_UNKNOWN 可以对账为成功")
                    target = RpaJobStatus.SUCCEEDED
                    job.effect_phase = RpaSideEffectPhase.EXTERNAL_EFFECT_CONFIRMED
                    job.business_outcome = self._expected_outcome(job.operation)
                    job.external_reference = payload.external_reference
                    job.error_code = None
                    job.error_message = None
                elif decision is RpaReconciliationDecision.CONFIRMED_NO_EFFECT:
                    if previous_status is not RpaJobStatus.RESULT_UNKNOWN:
                        raise RpaJobValidationError("只有 RESULT_UNKNOWN 可以确认外部未产生副作用")
                    target = RpaJobStatus.RETRYABLE_FAILED
                    job.business_outcome = None
                    job.error_code = "RECONCILED_NO_EXTERNAL_EFFECT"
                    job.error_message = payload.message
                    job.next_attempt_at = _utc_now_for_database()
                elif decision is RpaReconciliationDecision.NEEDS_HUMAN:
                    target = RpaJobStatus.WAITING_HUMAN
                    job.business_outcome = None
                    job.error_message = payload.message
                else:
                    target = RpaJobStatus.FAILED
                    job.business_outcome = None
                    job.error_code = "RECONCILED_FAILED"
                    job.error_message = payload.message

                try:
                    assert_rpa_job_transition(previous_status, target)
                except ValueError as error:
                    raise RpaJobConflictError(str(error)) from error
                job.status = target
                job.current_step = f"RECONCILED_{decision.value}"
                job.lease_expires_at = None
                job.evidence = {**(job.evidence or {}), **payload.evidence}
                job.result_payload = {
                    "source": "RECONCILIATION",
                    **payload.model_dump(mode="json"),
                }
                job.result_hash = _canonical_hash(job.result_payload)
                job.revision += 1
                job.updated_at = _utc_now_for_database()
                if target.is_terminal:
                    job.completed_at = _utc_now_for_database()
                job.events.append(
                    self._event(
                        job,
                        event_type="JOB_RECONCILED",
                        previous_status=previous_status,
                        message=payload.message,
                        data={
                            "decision": decision.value,
                            "external_reference": payload.external_reference,
                        },
                    )
                )
                await repository.wake_bound_shipment_runs(
                    job.id,
                    now=_utc_now_for_database(),
                )
                await repository.flush()
                return self._to_read(job)

    async def requeue(
        self,
        job_id: str,
        payload: RpaJobRequeue,
    ) -> RpaJobRead:
        """重新入队明确可安全重试的任务。"""

        async with self.session_factory() as session:
            repository = RpaJobRepository(session)
            async with session.begin():
                job = await self._get_locked(repository, job_id)
                if job.revision != payload.expected_revision:
                    raise RpaJobConflictError(f"RPA Job revision 已变化，当前为 {job.revision}")
                if job.status not in {
                    RpaJobStatus.RETRYABLE_FAILED,
                    RpaJobStatus.WAITING_HUMAN,
                }:
                    raise RpaJobConflictError(f"{job.status.value} Job 不能直接重新入队")
                if job.attempt_count >= job.max_attempts:
                    raise RpaJobConflictError(
                        f"任务已达到最大尝试次数 {job.max_attempts}，不能重新入队"
                    )
                if (
                    _PHASE_RANK[job.effect_phase]
                    >= _PHASE_RANK[RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED]
                    and not payload.confirmed_safe_to_retry
                ):
                    raise RpaJobValidationError(
                        "任务已经开始外部写操作，必须先确认不会重复后才能重新入队"
                    )

                previous_status = job.status
                try:
                    assert_rpa_job_transition(previous_status, RpaJobStatus.QUEUED)
                except ValueError as error:
                    raise RpaJobConflictError(str(error)) from error
                job.status = RpaJobStatus.QUEUED
                job.current_step = "WAITING_WORKER"
                job.revision += 1
                job.attempt_token = None
                job.claimed_by = None
                job.lease_expires_at = None
                job.next_attempt_at = None
                job.page_fingerprint = None
                job.business_outcome = None
                job.error_code = None
                job.error_message = None
                job.result_payload = None
                job.result_hash = None
                job.updated_at = _utc_now_for_database()
                # 新尝试尚未发生任何页面副作用；历史证据仍保存在 events/evidence。
                job.effect_phase = RpaSideEffectPhase.READ_ONLY
                job.events.append(
                    self._event(
                        job,
                        event_type="JOB_REQUEUED",
                        previous_status=previous_status,
                        message=payload.reason,
                        data={"confirmed_safe_to_retry": (payload.confirmed_safe_to_retry)},
                    )
                )
                await repository.wake_bound_shipment_runs(
                    job.id,
                    now=_utc_now_for_database(),
                )
                await repository.flush()
                return self._to_read(job)

    def _resolve_execution_profiles(
        self,
        payload: RpaJobDispatch,
    ) -> tuple[str | None, str | None]:
        """验证产地证 Profile，并由服务端生成网页流程代码。"""

        if payload.operation is not RpaOperation.COO_STAGE:
            return payload.source_profile, payload.flow_profile
        if payload.source_profile is None:
            raise RpaJobValidationError("COO_STAGE 必须提供 source_profile")
        try:
            plan = self.certificate_plan_builder.build(payload.source_profile)
        except UnsupportedCertificateProfileError as error:
            raise RpaJobValidationError(str(error)) from error
        generated = plan.portal_flow.value
        if payload.flow_profile is not None and payload.flow_profile != generated:
            raise RpaJobValidationError("flow_profile 与 source_profile 对应的确定性执行计划不一致")
        return plan.processor_profile, generated

    def _validate_environment(self, payload: RpaJobDispatch) -> None:
        """默认禁止真实网站；即使开启也不允许合成数据进入 Production。"""

        if payload.environment is not RpaEnvironment.PRODUCTION:
            return
        if not self.production_dispatch_enabled:
            raise RpaJobValidationError("当前服务未开启 Production RPA 派发，只允许 SANDBOX")
        source_label = str(
            payload.business_payload.get(
                "data_source_label",
                payload.business_payload.get("data_source", ""),
            )
        ).upper()
        if source_label != "ENTERPRISE_REAL":
            raise RpaJobValidationError(
                "Production 只接受服务端确认标记为 ENTERPRISE_REAL 的数据；"
                "缺少标签或 SYNTHETIC_DEMO 均禁止派发"
            )

    @staticmethod
    def _ensure_same_dispatch(
        job: RpaJobRecord,
        payload: RpaJobDispatch,
        *,
        source_profile: str | None,
        flow_profile: str | None,
    ) -> None:
        expected = {
            "case_number": payload.case_number,
            "adapter_key": payload.adapter_key,
            "source_profile": source_profile,
            "flow_profile": flow_profile,
            "selector_pack_version": payload.selector_pack_version,
            "payload_schema_version": payload.payload_schema_version,
            "max_attempts": payload.max_attempts,
        }
        actual = {key: getattr(job, key) for key in expected}
        if actual != expected:
            raise RpaJobConflictError("相同 RPA 幂等业务命令使用了不同执行配置，请读取既有 Job")

    @staticmethod
    async def _get_locked(
        repository: RpaJobRepository,
        job_id: str,
    ) -> RpaJobRecord:
        job = await repository.get(job_id, for_update=True)
        if job is None:
            raise RpaJobNotFoundError("RPA Job 不存在")
        return job

    @staticmethod
    def _require_attempt(job: RpaJobRecord, supplied_token: str) -> None:
        if job.attempt_token is None or job.attempt_token != supplied_token:
            raise RpaJobConflictError("attempt_token 已失效；旧 Worker 不能覆盖当前任务")

    def _apply_result(
        self,
        job: RpaJobRecord,
        result: RpaResult,
        payload_hash: str,
    ) -> None:
        """在已经锁住 Job 行的事务中应用一份通过契约校验的结果。"""

        contract = RpaJob(
            job_id=UUID(job.id),
            domain=job.domain,
            operation=job.operation,
            environment=job.environment,
            business_reference=job.business_reference,
            side_effect_boundary=job.side_effect_boundary,
            business_payload=job.business_payload,
            business_payload_hash=job.business_payload_hash,
            status=job.status,
            side_effect_phase=job.effect_phase,
            created_at=job.created_at,
        )
        try:
            changed = contract.apply_result(result)
        except ValueError as error:
            raise RpaJobConflictError(str(error)) from error

        previous_status = job.status
        job.status = changed.status
        job.effect_phase = changed.side_effect_phase
        job.current_step = self._result_step(result.status)
        job.revision += 1
        job.lease_expires_at = None
        job.business_outcome = result.business_outcome
        job.external_reference = result.external_reference
        job.error_code = result.error_code
        job.error_message = (
            result.message
            if result.status
            in {
                RpaJobStatus.RETRYABLE_FAILED,
                RpaJobStatus.FAILED,
                RpaJobStatus.RESULT_UNKNOWN,
                RpaJobStatus.SITE_VERSION_UNKNOWN,
            }
            else None
        )
        job.evidence = {**(job.evidence or {}), **result.evidence}
        job.result_payload = result.model_dump(mode="json")
        job.result_hash = payload_hash
        job.updated_at = _utc_now_for_database()
        job.next_attempt_at = (
            _utc_now_for_database() + timedelta(seconds=result.retry_after_seconds or 1)
            if result.status is RpaJobStatus.RETRYABLE_FAILED
            else None
        )
        if result.status.is_terminal:
            job.completed_at = _utc_now_for_database()
        job.events.append(
            self._event(
                job,
                event_type="JOB_RESULT_REPORTED",
                previous_status=previous_status,
                message=result.message,
                data={
                    "business_outcome": (
                        result.business_outcome.value
                        if result.business_outcome is not None
                        else None
                    ),
                    "error_code": result.error_code,
                    "external_reference": result.external_reference,
                    "side_effect_phase": result.side_effect_phase.value,
                    "retry_after_seconds": result.retry_after_seconds,
                },
            )
        )

    @staticmethod
    def _expected_outcome(operation: RpaOperation) -> RpaBusinessOutcome:
        return {
            RpaOperation.LINK_BOOKING_STAGE: (RpaBusinessOutcome.LINK_DRAFT_SAVED),
            RpaOperation.COO_STAGE: RpaBusinessOutcome.COO_STAGED,
            RpaOperation.CIQ_STAGE: RpaBusinessOutcome.CIQ_STAGED,
        }[operation]

    @staticmethod
    def _result_step(status: RpaJobStatus) -> str:
        return {
            RpaJobStatus.WAITING_HUMAN: "WAITING_HUMAN",
            RpaJobStatus.SUCCEEDED: "EXTERNAL_EFFECT_CONFIRMED",
            RpaJobStatus.RETRYABLE_FAILED: "RETRY_SCHEDULED",
            RpaJobStatus.FAILED: "FAILED",
            RpaJobStatus.RESULT_UNKNOWN: "RECONCILIATION_REQUIRED",
            RpaJobStatus.SITE_VERSION_UNKNOWN: "SELECTOR_REVIEW_REQUIRED",
            RpaJobStatus.CANCELLED: "CANCELLED",
        }[status]

    @staticmethod
    def _event(
        job: RpaJobRecord,
        *,
        event_type: str,
        previous_status: RpaJobStatus | None,
        message: str,
        data: dict[str, object],
    ) -> RpaJobEvent:
        return RpaJobEvent(
            sequence=len(job.events) + 1,
            event_type=event_type,
            previous_status=previous_status,
            current_status=job.status,
            current_step=job.current_step,
            message=message,
            data=data,
            occurred_at=_utc_now_for_database(),
        )

    @staticmethod
    def _to_read(job: RpaJobRecord) -> RpaJobRead:
        return RpaJobRead(
            job_id=job.id,
            contract_version=job.contract_version,
            case_number=job.case_number,
            originating_run_id=job.originating_run_id,
            business_reference=job.business_reference,
            domain=job.domain,
            operation=job.operation,
            environment=job.environment,
            side_effect_boundary=job.side_effect_boundary,
            side_effect_phase=job.effect_phase,
            adapter_key=job.adapter_key,
            source_profile=job.source_profile,
            flow_profile=job.flow_profile,
            selector_pack_version=job.selector_pack_version,
            payload_schema_version=job.payload_schema_version,
            business_payload_hash=job.business_payload_hash,
            status=job.status,
            current_step=job.current_step,
            revision=job.revision,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            claimed_by=job.claimed_by,
            lease_expires_at=job.lease_expires_at,
            next_attempt_at=job.next_attempt_at,
            page_fingerprint=job.page_fingerprint,
            business_outcome=job.business_outcome,
            external_reference=job.external_reference,
            error_code=job.error_code,
            error_message=job.error_message,
            evidence=job.evidence or {},
            can_auto_retry=(
                can_automatically_retry(job.status)
                and _PHASE_RANK[job.effect_phase]
                < _PHASE_RANK[RpaSideEffectPhase.EXTERNAL_MUTATION_STARTED]
                and job.attempt_count < job.max_attempts
            ),
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            events=[
                RpaJobEventRead.model_validate(event)
                for event in sorted(job.events, key=lambda value: value.sequence)
            ],
        )
