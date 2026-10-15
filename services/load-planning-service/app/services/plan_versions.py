"""把已批准候选方案固化为正式、可校验的装载方案版本。"""

from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from app.models.agent_run import AgentRun, AgentRunStatus, PackingPlan
from app.models.load_planning import LoadPlanningTask
from app.models.plan_version import (
    LoadPlanVersion,
    LoadPlanVersionEvent,
    LoadPlanVersionStatus,
)
from app.repositories.plan_versions import (
    LoadPlanVersionPersistenceConflict,
    LoadPlanVersionRepository,
)
from app.schemas.plan_version import (
    LoadPlanLockRequest,
    LoadPlanVersionEventRead,
    LoadPlanVersionRead,
)
from app.services.agent_runs import _snapshot_hash, _task_snapshot


class LoadPlanVersionNotFoundError(Exception):
    """正式版本编号不存在。"""


class LoadPlanVersionTaskNotFoundError(Exception):
    """装载任务编号不存在。"""


class LoadPlanVersionSourceRunNotFoundError(Exception):
    """用于锁定的智能体运行不存在。"""


class LoadPlanVersionConflictError(Exception):
    """当前运行、任务输入或版本号不允许执行锁定。"""


class LoadPlanVersionValidationError(Exception):
    """来源运行内部缺少形成正式版本所需的完整事实。"""


SNAPSHOT_SCHEMA_VERSION = "LOAD_PLAN_VERSION_SNAPSHOT_V1"

# 当前项目尚未接入用户认证。这里使用服务器端常量，是为了让演示数据明确暴露其身份性质，
# 而不是允许请求体伪造“张三已审批”。接入 JWT/SSO 后只替换这一处 actor provider：
# 从可信认证上下文读取 subject/display_name/source，API schema 仍然绝不能增加 actor 入参。
SYSTEM_DEMO_ACTOR_SUBJECT = "SYSTEM_DEMO"
SYSTEM_DEMO_ACTOR_DISPLAY_NAME = "SYSTEM_DEMO"
SYSTEM_DEMO_ACTOR_SOURCE = "SYSTEM_DEMO"


class LoadPlanVersionService:
    """正式方案版本的锁定、完整性检查和历史查询服务。"""

    def __init__(self, repository: LoadPlanVersionRepository) -> None:
        self.repository = repository

    async def lock(
        self,
        run_id: str,
        payload: LoadPlanLockRequest,
    ) -> LoadPlanVersionRead:
        """将已批准运行的选中方案固化为任务的下一正式版本。

        任务行锁负责串行化同一任务下不同运行的版本号分配；来源运行唯一约束负责同一
        run 的网络重试幂等。锁定不会把 AgentRun 的 APPROVED 改名为 LOCKED，而是创建
        独立正式版本，保留两层业务语义。
        """

        task_id = await self.repository.get_run_task_id(run_id)
        if task_id is None:
            raise LoadPlanVersionSourceRunNotFoundError("装托智能体运行不存在")

        # 必须先锁任务，再读取当前版本号；否则两个不同 run 可能同时计算出版本 1。
        task = await self.repository.get_task_for_update(task_id)
        if task is None:
            raise LoadPlanVersionTaskNotFoundError("装载任务不存在")
        run = await self.repository.get_run_for_update(run_id)
        if run is None:  # pragma: no cover - 已有 task_id 且无删除入口时理论上不可发生
            raise LoadPlanVersionSourceRunNotFoundError("装托智能体运行不存在")
        if run.task_id != task.id:
            raise LoadPlanVersionConflictError("来源运行所属任务在锁定期间发生变化，请刷新后重试")

        existing = await self.repository.get_by_source_run(run_id, for_update=True)
        if existing is not None:
            self._validate_idempotent_retry(existing, payload)
            return self._to_read(existing)

        plan, approved_at = self._validate_source_run(run, task, payload)
        current = await self.repository.get_current_for_task(task.id, for_update=True)
        current_version_number = current.version_number if current is not None else 0
        if payload.expected_current_version_number != current_version_number:
            raise LoadPlanVersionConflictError(
                f"正式方案版本已变化，当前版本为 {current_version_number}，请刷新后重新锁定"
            )

        input_snapshot = deepcopy(run.input_snapshot)
        constraint_snapshot = self._constraint_snapshot(run)
        plan_snapshot = self._plan_snapshot(plan)
        version_id = str(uuid4())
        request_id = str(uuid4())
        version_number = current_version_number + 1
        plan_version = LoadPlanVersion(
            id=version_id,
            task_id=task.id,
            version_number=version_number,
            status=LoadPlanVersionStatus.LOCKED,
            source_run_id=run.id,
            source_plan_id=plan.plan_id,
            plan_code=plan.plan_code,
            selected_pallet_code=run.selected_pallet_code,
            supersedes_version_id=current.id if current is not None else None,
            source_run_version=run.run_version,
            snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
            input_snapshot=input_snapshot,
            input_snapshot_hash=_snapshot_hash(input_snapshot),
            constraint_snapshot=constraint_snapshot,
            constraint_snapshot_hash=_snapshot_hash(constraint_snapshot),
            plan_snapshot=plan_snapshot,
            plan_snapshot_hash=_snapshot_hash(plan_snapshot),
            algorithm_version=plan.algorithm_version,
            score_version=plan.score_version,
            approval_actor_subject=SYSTEM_DEMO_ACTOR_SUBJECT,
            approval_actor_display_name=SYSTEM_DEMO_ACTOR_DISPLAY_NAME,
            approval_actor_source=SYSTEM_DEMO_ACTOR_SOURCE,
            approved_at=approved_at,
            approval_comment=run.decision_comment,
            locked_by_subject=SYSTEM_DEMO_ACTOR_SUBJECT,
            locked_by_display_name=SYSTEM_DEMO_ACTOR_DISPLAY_NAME,
            locked_by_source=SYSTEM_DEMO_ACTOR_SOURCE,
            lock_reason=payload.lock_reason,
        )
        plan_version.events.append(
            LoadPlanVersionEvent(
                plan_version_id=version_id,
                sequence=1,
                event_type="PLAN_VERSION_LOCKED",
                actor_subject=SYSTEM_DEMO_ACTOR_SUBJECT,
                actor_display_name=SYSTEM_DEMO_ACTOR_DISPLAY_NAME,
                actor_source=SYSTEM_DEMO_ACTOR_SOURCE,
                request_id=request_id,
                detail=(
                    f"已将运行 {run.id} 的方案 {plan.plan_code} "
                    f"锁定为任务 {task.task_number} 的正式版本 V{version_number}。"
                ),
            )
        )

        try:
            persisted = await self.repository.save(plan_version)
        except LoadPlanVersionPersistenceConflict as error:
            # 数据库唯一约束是并发请求的最终裁判。若赢家锁的是同一 run，则返回赢家，
            # 保持请求幂等；若是另一 run 抢先生成下一版本，则要求调用方刷新。
            winner = await self.repository.get_by_source_run(run_id)
            if winner is not None:
                self._validate_idempotent_retry(winner, payload)
                return self._to_read(winner)
            raise LoadPlanVersionConflictError(
                "正式方案版本已被并发请求更新，请刷新后重新锁定"
            ) from error
        return self._to_read(persisted)

    async def get(self, version_id: str) -> LoadPlanVersionRead:
        """按版本 UUID 读取完整快照和审计事件。"""

        plan_version = await self.repository.get(version_id)
        if plan_version is None:
            raise LoadPlanVersionNotFoundError("正式装载方案版本不存在")
        return self._to_read(plan_version)

    async def current(self, task_number: str) -> LoadPlanVersionRead:
        """读取任务当前版本；尚未锁定任何版本时返回明确 404。"""

        task = await self.repository.get_task_by_number(task_number)
        if task is None:
            raise LoadPlanVersionTaskNotFoundError("装载任务不存在")
        plan_version = await self.repository.get_current_for_task(task.id)
        if plan_version is None:
            raise LoadPlanVersionNotFoundError("该任务尚无正式装载方案版本")
        return self._to_read(plan_version)

    async def list(self, task_number: str) -> list[LoadPlanVersionRead]:
        """按版本号倒序读取任务的完整正式版本历史。"""

        task = await self.repository.get_task_by_number(task_number)
        if task is None:
            raise LoadPlanVersionTaskNotFoundError("装载任务不存在")
        versions = await self.repository.list_for_task(task.id)
        return [self._to_read(plan_version) for plan_version in versions]

    @staticmethod
    def _validate_idempotent_retry(
        existing: LoadPlanVersion,
        payload: LoadPlanLockRequest,
    ) -> None:
        """同一来源运行的相同锁定请求返回原版本，不重复追加事件。"""

        if payload.expected_run_version != existing.source_run_version:
            raise LoadPlanVersionConflictError(
                "该运行已经锁定，但 expected_run_version 与已锁定版本不一致"
            )
        if payload.lock_reason != existing.lock_reason:
            raise LoadPlanVersionConflictError(
                "该运行已经锁定；同一来源运行不能用另一锁定原因覆盖正式版本"
            )
        # 不再比较 expected_current_version_number：第一次成功后当前版本必然已经递增，
        # 网络超时重试必须仍能拿回同一版本。

    @staticmethod
    def _validate_source_run(
        run: AgentRun,
        task: LoadPlanningTask,
        payload: LoadPlanLockRequest,
    ) -> tuple[PackingPlan, datetime]:
        """验证批准状态、乐观锁、选中可行方案和输入快照完整性。"""

        if run.status is not AgentRunStatus.APPROVED:
            raise LoadPlanVersionConflictError("只有 APPROVED 运行才能锁定正式方案")
        if payload.expected_run_version != run.run_version:
            raise LoadPlanVersionConflictError(
                f"运行版本已变化，当前版本为 {run.run_version}，请刷新后再锁定"
            )
        if not run.selected_plan_code:
            raise LoadPlanVersionValidationError("已批准运行缺少 selected_plan_code")
        if not run.selected_pallet_code:
            raise LoadPlanVersionValidationError("已批准运行缺少 selected_pallet_code")

        plan = next(
            (candidate for candidate in run.plans if candidate.plan_code == run.selected_plan_code),
            None,
        )
        if plan is None:
            raise LoadPlanVersionValidationError("选中方案不属于来源运行")
        if not plan.feasible:
            raise LoadPlanVersionConflictError("不可行候选方案不能锁定为正式版本")
        if not plan.algorithm_version or not plan.score_version:
            raise LoadPlanVersionValidationError("选中方案缺少算法或评分版本")

        # 先验证运行自身保存的 JSON 没有与哈希脱节，再验证任务当前输入仍等于批准时输入。
        # 这样既能发现数据库异常改写，也能阻止主数据变化后继续锁定旧算法结果。
        if _snapshot_hash(run.input_snapshot) != run.input_snapshot_hash:
            raise LoadPlanVersionConflictError("来源运行的输入快照完整性校验失败")
        current_task_hash = _snapshot_hash(_task_snapshot(task))
        if current_task_hash != run.input_snapshot_hash:
            raise LoadPlanVersionConflictError(
                "任务输入在运行批准后发生变化，请基于新任务版本重新规划"
            )

        approval_event = next(
            (event for event in reversed(run.events) if event.event_type == "PLAN_APPROVED"),
            None,
        )
        if approval_event is None or approval_event.created_at is None:
            raise LoadPlanVersionValidationError("已批准运行缺少 PLAN_APPROVED 审计事件")
        return plan, approval_event.created_at

    @staticmethod
    def _constraint_snapshot(run: AgentRun) -> dict[str, object]:
        """固化本次计算实际采用的人工约束和策略选择。"""

        return {
            "selected_pallet_code": run.selected_pallet_code,
            "strategy_codes": list(run.strategy_codes),
            "answers": deepcopy(run.answers),
        }

    @staticmethod
    def _plan_snapshot(plan: PackingPlan) -> dict[str, object]:
        """把选中候选方案复制成与来源 ORM 生命周期无关的完整 JSON。"""

        return {
            "plan_id": plan.plan_id,
            "plan_code": plan.plan_code,
            "strategy_code": plan.strategy_code,
            "strategy_name": plan.strategy_name,
            "algorithm_version": plan.algorithm_version,
            "score_version": plan.score_version,
            "feasible": plan.feasible,
            "score": plan.score,
            "summary": plan.summary,
            "metrics": deepcopy(plan.metrics),
            "warnings": deepcopy(plan.warnings),
            "pallets": deepcopy(plan.pallets),
        }

    @staticmethod
    def _to_read(plan_version: LoadPlanVersion) -> LoadPlanVersionRead:
        """验证三份快照哈希后转换为统一 HTTP 视图。"""

        snapshots = (
            ("输入", plan_version.input_snapshot, plan_version.input_snapshot_hash),
            (
                "约束",
                plan_version.constraint_snapshot,
                plan_version.constraint_snapshot_hash,
            ),
            ("方案", plan_version.plan_snapshot, plan_version.plan_snapshot_hash),
        )
        for label, snapshot, expected_hash in snapshots:
            if _snapshot_hash(snapshot) != expected_hash:
                raise LoadPlanVersionConflictError(
                    f"正式版本{label}快照完整性校验失败，请停止执行并联系管理员"
                )
        if (
            plan_version.plan_snapshot.get("algorithm_version") != (plan_version.algorithm_version)
            or plan_version.plan_snapshot.get("score_version") != plan_version.score_version
        ):
            raise LoadPlanVersionConflictError(
                "正式版本算法/评分版本与方案快照不一致，请停止执行并联系管理员"
            )
        if (
            plan_version.plan_snapshot.get("plan_id") != plan_version.source_plan_id
            or plan_version.plan_snapshot.get("plan_code") != plan_version.plan_code
            or plan_version.plan_snapshot.get("feasible") is not True
        ):
            raise LoadPlanVersionConflictError(
                "正式版本来源方案元数据与方案快照不一致，请停止执行并联系管理员"
            )
        if plan_version.constraint_snapshot.get("selected_pallet_code") != (
            plan_version.selected_pallet_code
        ):
            raise LoadPlanVersionConflictError(
                "正式版本托盘约束与约束快照不一致，请停止执行并联系管理员"
            )
        if plan_version.input_snapshot.get("task_number") != (plan_version.task.task_number):
            raise LoadPlanVersionConflictError(
                "正式版本任务编号与输入快照不一致，请停止执行并联系管理员"
            )

        return LoadPlanVersionRead(
            version_id=plan_version.id,
            status=plan_version.status,
            task_number=plan_version.task.task_number,
            version_number=plan_version.version_number,
            source_run_id=plan_version.source_run_id,
            source_plan_id=plan_version.source_plan_id,
            plan_code=plan_version.plan_code,
            selected_pallet_code=plan_version.selected_pallet_code,
            supersedes_version_id=plan_version.supersedes_version_id,
            source_run_version=plan_version.source_run_version,
            snapshot_schema_version=plan_version.snapshot_schema_version,
            input_snapshot_hash=plan_version.input_snapshot_hash,
            constraint_snapshot_hash=plan_version.constraint_snapshot_hash,
            plan_snapshot_hash=plan_version.plan_snapshot_hash,
            algorithm_version=plan_version.algorithm_version,
            score_version=plan_version.score_version,
            approval_actor_subject=plan_version.approval_actor_subject,
            approval_actor_display_name=plan_version.approval_actor_display_name,
            approval_actor_source=plan_version.approval_actor_source,
            approved_at=plan_version.approved_at,
            approval_comment=plan_version.approval_comment,
            locked_by_subject=plan_version.locked_by_subject,
            locked_by_display_name=plan_version.locked_by_display_name,
            locked_by_source=plan_version.locked_by_source,
            lock_reason=plan_version.lock_reason,
            locked_at=plan_version.locked_at,
            input_snapshot=deepcopy(plan_version.input_snapshot),
            constraint_snapshot=deepcopy(plan_version.constraint_snapshot),
            plan_snapshot=deepcopy(plan_version.plan_snapshot),
            events=[
                LoadPlanVersionEventRead.model_validate(event) for event in plan_version.events
            ],
        )
