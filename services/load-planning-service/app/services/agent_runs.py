"""可恢复、可审计的装托智能体状态机。

本模块体现了“智能体”的核心：它能观察输入、发现阻塞条件、暂停并提出结构化问题，收到
人工答案后从原运行继续，调用确定性工具，最后停在人工审批节点。大模型不是形成这个闭环
的必要条件；算法坐标和评分都由普通代码生成，因而可复现、可测试。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy.exc import IntegrityError

from app.models.agent_run import AgentRun, AgentRunEvent, AgentRunStatus, PackingPlan
from app.models.load_planning import CargoItem, LoadPlanningTask, PalletSpec
from app.repositories.agent_runs import AgentRunRepository
from app.schemas.agent_run import (
    AgentDecisionCode,
    AgentQuestionRead,
    AgentRunCreate,
    AgentRunDecision,
    AgentRunRead,
    AgentRunReplan,
    AgentRunResume,
    PackingPlanRead,
    PackingStrategyCode,
    QuestionValueType,
)
from app.services.pallet_packing import (
    ALGORITHM_VERSION,
    CargoSkuInput,
    PackingStrategy,
    PalletPackingService,
    PalletSpecInput,
)


class LoadPlanningTaskNotFoundError(Exception):
    """任务编号不存在。"""


class AgentRunNotFoundError(Exception):
    """运行编号不存在。"""


class AgentRunConflictError(Exception):
    """当前状态不允许执行请求动作。"""


class AgentRunValidationError(Exception):
    """业务值能通过 JSON 类型校验，但不符合本运行的问题约束。"""


PLAN_SCORE_VERSION = "PALLET_PLAN_SCORE_V1"


def _number(value: Decimal | float) -> float:
    """把数据库 Decimal 转为 JSON 可稳定表示的普通数字。"""

    return float(value)


def _task_snapshot(task: LoadPlanningTask) -> dict[str, object]:
    """构造排序稳定、可哈希的算法输入快照。"""

    cargo = [
        {
            "item_code": item.item_code,
            "sku_code": item.sku_code,
            "quantity": item.quantity,
            "length_mm": _number(item.length_mm),
            "width_mm": _number(item.width_mm),
            "height_mm": _number(item.height_mm),
            "gross_weight_kg": _number(item.gross_weight_kg),
            "stackable": item.stackable,
            "max_stack_layers": item.max_stack_layers,
            "max_top_load_kg": (
                _number(item.max_top_load_kg) if item.max_top_load_kg is not None else None
            ),
            "keep_upright": item.keep_upright,
            "fragile": item.fragile,
        }
        for item in sorted(task.cargo_items, key=lambda value: value.item_code)
    ]
    pallets = [
        {
            "pallet_code": pallet.pallet_code,
            "quantity": pallet.quantity,
            "length_mm": _number(pallet.length_mm),
            "width_mm": _number(pallet.width_mm),
            "height_mm": _number(pallet.height_mm),
            "tare_weight_kg": _number(pallet.tare_weight_kg),
            "max_load_kg": (
                _number(pallet.max_load_kg) if pallet.max_load_kg is not None else None
            ),
            "max_total_height_mm": (
                _number(pallet.max_total_height_mm)
                if pallet.max_total_height_mm is not None
                else None
            ),
        }
        for pallet in sorted(task.pallet_specs, key=lambda value: value.pallet_code)
    ]
    return {
        "task_number": task.task_number,
        "cargo_items": cargo,
        "pallet_specs": pallets,
    }


def _snapshot_hash(snapshot: dict[str, object]) -> str:
    """使用规范 JSON 生成输入版本指纹。"""

    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _create_request_hash(payload: AgentRunCreate) -> str:
    """为创建请求生成规范指纹，用来发现“同 key 却换了参数”的误用。"""

    return _snapshot_hash(
        {
            "selected_pallet_code": payload.selected_pallet_code,
            "strategy_codes": [strategy.value for strategy in payload.strategy_codes],
        }
    )


def _idempotency_key_hash(normalized_key: str) -> str:
    """把规范化后的重规划幂等键转换为跨数据库一致的固定长度摘要。"""

    return hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()


def _replan_request_hash(payload: AgentRunReplan) -> str:
    """为重新规划请求生成规范指纹，防止同一个 key 被换参数复用。"""

    return _snapshot_hash(
        {
            "reason": payload.reason,
            "selected_pallet_code": payload.selected_pallet_code,
            "strategy_codes": (
                [strategy.value for strategy in payload.strategy_codes]
                if payload.strategy_codes is not None
                else None
            ),
        }
    )


def _normalize_idempotency_key(value: str | None) -> str | None:
    """清理 HTTP 幂等键，并拒绝无法安全落库的控制字符或超长值。"""

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 128 or any(ord(character) < 32 for character in normalized):
        raise AgentRunValidationError("Idempotency-Key 必须是 1 到 128 个可见字符")
    return normalized


def _question(
    *,
    key: str,
    label: str,
    value_type: QuestionValueType,
    unit: str | None,
    reason: str,
    min_value: float | None = None,
    max_value: float | None = None,
) -> AgentQuestionRead:
    """减少问题定义中的重复字段，并保证所有问题都是必填约束。"""

    return AgentQuestionRead(
        key=key,
        label=label,
        value_type=value_type,
        unit=unit,
        reason=reason,
        required=True,
        min_value=min_value,
        max_value=max_value,
    )


class StatefulPalletAgentService:
    """装托状态机的应用服务。"""

    def __init__(
        self,
        repository: AgentRunRepository,
        packing_service: PalletPackingService | None = None,
    ) -> None:
        self.repository = repository
        self.packing_service = packing_service or PalletPackingService()

    async def create(
        self,
        task_number: str,
        payload: AgentRunCreate,
        idempotency_key: str | None = None,
    ) -> AgentRunRead:
        """检查任务并推进到“等待输入”或“等待评审”。

        ``Idempotency-Key`` 是网络请求级语义：同一任务、同一个 key 和同一份创建参数
        会返回原运行；同一个 key 换了参数则返回冲突，避免客户端把一次超时误认为
        “服务没有创建成功”而重复产生第二条业务运行。
        """

        task = await self.repository.get_task(task_number)
        if task is None:
            raise LoadPlanningTaskNotFoundError("装载任务不存在")
        # 事务回滚会让 AsyncSession 中已经加载的 ORM 对象全部过期。并发创建时若唯一键
        # 裁决失败，后面的幂等恢复分支只能继续使用这个普通整数，不能再访问 task.id，
        # 否则异步 ORM 会尝试隐式刷新并触发 MissingGreenlet。
        task_id = task.id

        if payload.selected_pallet_code is not None:
            self._find_pallet(task, payload.selected_pallet_code)

        normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)
        request_hash = _create_request_hash(payload)
        if normalized_idempotency_key is not None:
            existing = await self.repository.get_by_idempotency_key(
                task.id,
                normalized_idempotency_key,
            )
            if existing is not None:
                self._ensure_same_create_request(existing, request_hash)
                return self._to_read(existing)

        snapshot = _task_snapshot(task)
        run = AgentRun(
            id=str(uuid4()),
            task_id=task.id,
            status=AgentRunStatus.AWAITING_INPUT,
            current_step="CHECKING_CONSTRAINTS",
            selected_pallet_code=payload.selected_pallet_code,
            selected_plan_code=None,
            strategy_codes=[strategy.value for strategy in payload.strategy_codes],
            input_snapshot_hash=_snapshot_hash(snapshot),
            input_snapshot=snapshot,
            answers={},
            run_version=1,
            parent_run_id=None,
            idempotency_key=normalized_idempotency_key,
            idempotency_request_hash=(
                request_hash if normalized_idempotency_key is not None else None
            ),
        )
        self._append_event(
            run,
            "RUN_CREATED",
            "装托智能体运行已创建",
            f"已保存任务 {task.task_number} 的输入快照，开始检查安全约束。",
        )

        # 只有一种托盘规格时不必打断人工；这是从任务事实得出的唯一选择，不是猜约束。
        if run.selected_pallet_code is None and len(task.pallet_specs) == 1:
            run.selected_pallet_code = task.pallet_specs[0].pallet_code
            self._append_event(
                run,
                "PALLET_SELECTED",
                "自动选择唯一托盘规格",
                f"任务只有 {run.selected_pallet_code} 一种托盘规格。",
            )

        try:
            self._advance(run, task)
        except Exception as error:
            self._mark_failed(run, error)
            await self._save_created_run(
                run,
                task_id,
                normalized_idempotency_key,
                request_hash,
            )
            raise
        persisted = await self._save_created_run(
            run,
            task_id,
            normalized_idempotency_key,
            request_hash,
        )
        return self._to_read(persisted)

    async def _save_created_run(
        self,
        run: AgentRun,
        task_id: int,
        idempotency_key: str | None,
        request_hash: str,
    ) -> AgentRun:
        """保存新运行，并把并发唯一键冲突转换成幂等重试响应。"""

        try:
            return await self.repository.save(run)
        except IntegrityError:
            # 两个请求可能同时通过“先查询没有记录”，只有数据库唯一约束能裁决最终赢家。
            # 保存仓储已经回滚当前事务，因此这里可以安全地重新查询对方创建的运行。
            if idempotency_key is None:
                raise
            existing = await self.repository.get_by_idempotency_key(task_id, idempotency_key)
            if existing is None:
                raise
            self._ensure_same_create_request(existing, request_hash)
            return existing

    @staticmethod
    def _ensure_same_create_request(run: AgentRun, request_hash: str) -> None:
        """同一幂等键不能被拿来偷偷改变托盘或策略。"""

        if run.idempotency_request_hash != request_hash:
            raise AgentRunConflictError(
                "Idempotency-Key 已用于另一份创建请求，请更换幂等键后再启动规划"
            )

    async def get(self, run_id: str) -> AgentRunRead:
        """读取当前运行，不产生任何新事件。"""

        run = await self.repository.get(run_id)
        if run is None:
            raise AgentRunNotFoundError("装托智能体运行不存在")
        return self._to_read(run)

    async def list_active(self, task_number: str) -> list[AgentRunRead]:
        """让页面在清缓存或换设备后仍能找到任务的未完成运行。"""

        task = await self.repository.get_task(task_number)
        if task is None:
            raise LoadPlanningTaskNotFoundError("装载任务不存在")
        runs = await self.repository.list_active_for_task(task.id)
        return [self._to_read(run) for run in runs]

    async def list_history(self, task_number: str) -> list[AgentRunRead]:
        """返回任务的完整运行历史，供失败追踪和审计页面使用。"""

        task = await self.repository.get_task(task_number)
        if task is None:
            raise LoadPlanningTaskNotFoundError("装载任务不存在")
        runs = await self.repository.list_history_for_task(task.id)
        return [self._to_read(run) for run in runs]

    async def resume(
        self,
        run_id: str,
        payload: AgentRunResume,
    ) -> AgentRunRead:
        """合并人工答案、重新检查约束，并在条件满足时生成方案。"""

        run, task = await self._load_context(
            run_id,
            verify_snapshot=True,
            lock_for_update=True,
        )
        submitted = {answer.key: answer.value for answer in payload.answers}
        normalized: dict[str, object] = {}
        for key, value in submitted.items():
            definition = self._answer_definition(task, key)
            normalized[key] = self._normalize_answer(definition, value)

        # 一次 resume 可能只回答部分问题，运行仍保持 AWAITING_INPUT。网络重试同一份
        # partial answers 时也必须先按业务内容幂等返回，不能先被已经递增的版本号挡成 409，
        # 更不能重复追加 INPUT_RECORDED 或无意义地再次 bump run_version。
        if all(run.answers.get(key) == value for key, value in normalized.items()):
            return self._to_read(run)

        if run.status is not AgentRunStatus.AWAITING_INPUT:
            # 网络重试经常会重复发送已经成功的 resume。相同答案直接返回当前状态，
            # 不新增事件、不重复生成方案；不同答案则必须新开运行，避免偷偷改写审计事实。
            raise AgentRunConflictError("当前运行已不在等待输入状态，不能修改约束答案")

        self._check_expected_version(run, payload.expected_run_version)
        old_answers = dict(run.answers)
        merged_answers = {**old_answers, **normalized}
        changed = {
            key: value
            for key, value in normalized.items()
            if key not in old_answers or old_answers[key] != value
        }
        run.answers = merged_answers

        selected_answer = merged_answers.get("selected_pallet_code")
        if selected_answer is not None:
            selected_code = str(selected_answer)
            self._find_pallet(task, selected_code)
            run.selected_pallet_code = selected_code

        if changed:
            self._append_event(
                run,
                "INPUT_RECORDED",
                "人工约束已记录",
                "本次记录：" + "、".join(sorted(changed)),
            )

        previous_question_keys = {
            question.key for question in self._collect_questions(run, task, old_answers)
        }
        try:
            self._advance(run, task, previous_question_keys=previous_question_keys)
        except Exception as error:
            self._mark_failed(run, error)
            self._bump_version(run)
            await self.repository.save(run)
            raise
        self._bump_version(run)
        persisted = await self.repository.save(run)
        return self._to_read(persisted)

    async def decide(
        self,
        run_id: str,
        payload: AgentRunDecision,
    ) -> AgentRunRead:
        """执行人工审批节点。"""

        run, _task = await self._load_context(
            run_id,
            verify_snapshot=True,
            lock_for_update=True,
        )

        target_status = (
            AgentRunStatus.APPROVED
            if payload.decision is AgentDecisionCode.APPROVE
            else AgentRunStatus.REJECTED
        )
        if run.status in {AgentRunStatus.APPROVED, AgentRunStatus.REJECTED}:
            same_plan = payload.plan_code is None or payload.plan_code == run.selected_plan_code
            if (
                run.status is target_status
                and same_plan
                and payload.comment == run.decision_comment
            ):
                return self._to_read(run)
            raise AgentRunConflictError("该运行已经完成人工决策")
        if run.status is not AgentRunStatus.READY_FOR_REVIEW:
            raise AgentRunConflictError("方案尚未生成，当前不能审批")

        self._check_expected_version(run, payload.expected_run_version)
        if payload.decision is AgentDecisionCode.APPROVE:
            plan = next(
                (candidate for candidate in run.plans if candidate.plan_code == payload.plan_code),
                None,
            )
            if plan is None:
                raise AgentRunValidationError("plan_code 不属于当前运行")
            if not plan.feasible:
                raise AgentRunConflictError("不可行方案不能批准")
            run.status = AgentRunStatus.APPROVED
            run.current_step = "COMPLETED"
            run.selected_plan_code = plan.plan_code
            run.decision_comment = payload.comment
            self._append_event(
                run,
                "PLAN_APPROVED",
                "装托方案已批准",
                f"人工批准方案 {plan.plan_code}。"
                + (f" 备注：{payload.comment}" if payload.comment else ""),
            )
        else:
            if payload.plan_code is not None and not any(
                plan.plan_code == payload.plan_code for plan in run.plans
            ):
                raise AgentRunValidationError("plan_code 不属于当前运行")
            run.status = AgentRunStatus.REJECTED
            run.current_step = "COMPLETED"
            run.selected_plan_code = payload.plan_code
            run.decision_comment = payload.comment
            self._append_event(
                run,
                "RUN_REJECTED",
                "装托方案已驳回",
                f"人工驳回本次运行。原因：{payload.comment}",
            )

        self._bump_version(run)
        persisted = await self.repository.save(run)
        return self._to_read(persisted)

    async def replan(
        self,
        run_id: str,
        payload: AgentRunReplan,
        idempotency_key: str | None = None,
    ) -> AgentRunRead:
        """从驳回/失败运行复制已确认约束，创建一条新的可审计运行。

        旧运行永远不被覆盖。新运行继承输入快照和人工答案，但可以改用另一个
        托盘规格或候选策略。尺寸、重量等事实变化必须创建新的任务版本。同一父运行下，
        ``Idempotency-Key`` 与请求内容都相同时直接返回原子运行；相同 key 换参数则冲突。
        """

        parent, task = await self._load_context(
            run_id,
            # 幂等重放必须先于可变主数据的快照校验。第一次请求已经成功后，即使任务后来
            # 被修改，同一网络请求的重试仍应返回原 child，而不是错误变成 snapshot 409。
            verify_snapshot=False,
            lock_for_update=True,
        )
        parent_run_id = parent.id
        normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)
        key_hash = (
            _idempotency_key_hash(normalized_idempotency_key)
            if normalized_idempotency_key is not None
            else None
        )
        request_hash = _replan_request_hash(payload)
        if key_hash is not None:
            existing = await self.repository.get_replan_by_idempotency_key(
                parent_run_id,
                key_hash,
            )
            if existing is not None:
                self._ensure_same_replan_request(existing, request_hash)
                return self._to_read(existing)

        if parent.status not in {AgentRunStatus.REJECTED, AgentRunStatus.FAILED}:
            raise AgentRunConflictError("只有已驳回或失败的运行才能重新规划")
        current_hash = _snapshot_hash(_task_snapshot(task))
        if current_hash != parent.input_snapshot_hash:
            raise AgentRunConflictError(
                "任务输入在本次运行创建后发生变化，请基于新任务版本重新启动规划"
            )

        selected_pallet_code = payload.selected_pallet_code or parent.selected_pallet_code
        if selected_pallet_code is not None:
            self._find_pallet(task, selected_pallet_code)
        # Pydantic 请求里是 PackingStrategyCode，数据库 JSON 中则是普通字符串。
        # 不能把二者放进同一个推导式后统一取 .value；未显式传策略时应原样复制父运行。
        strategy_codes = (
            [strategy.value for strategy in payload.strategy_codes]
            if payload.strategy_codes is not None
            else list(parent.strategy_codes)
        )
        inherited_answers = deepcopy(parent.answers)
        # selected_pallet_code 同时存在于聚合字段和人工答案中。重新规划改选托盘后必须同步
        # 两处事实，否则下一次 resume 合并答案时会用父运行的旧答案把新托盘悄悄改回去。
        if len(task.pallet_specs) > 1 and selected_pallet_code is not None:
            inherited_answers["selected_pallet_code"] = selected_pallet_code
        else:
            inherited_answers.pop("selected_pallet_code", None)
        run = AgentRun(
            id=str(uuid4()),
            task_id=parent.task_id,
            status=AgentRunStatus.AWAITING_INPUT,
            current_step="CHECKING_CONSTRAINTS",
            selected_pallet_code=selected_pallet_code,
            selected_plan_code=None,
            strategy_codes=strategy_codes,
            input_snapshot_hash=parent.input_snapshot_hash,
            input_snapshot=deepcopy(parent.input_snapshot),
            answers=inherited_answers,
            run_version=1,
            parent_run_id=parent_run_id,
            replan_idempotency_key_hash=key_hash,
            replan_request_hash=request_hash,
        )
        new_run_id = run.id
        self._append_event(
            run,
            "RUN_REPLANNED",
            "已从上一运行创建重新规划",
            f"父运行 {parent_run_id}；原因：{payload.reason}",
        )
        try:
            self._advance(run, task)
        except Exception as error:
            self._mark_failed(run, error)
            persisted = await self._save_replanned_run(
                run,
                parent_run_id,
                key_hash,
                request_hash,
            )
            # 如果本请求只是在唯一约束竞争中输给了同 key 的并发请求，应返回数据库赢家；
            # 只有当前失败运行确实成功落库时，才把原算法异常继续抛给调用方。
            if persisted.id != new_run_id:
                return self._to_read(persisted)
            raise
        persisted = await self._save_replanned_run(
            run,
            parent_run_id,
            key_hash,
            request_hash,
        )
        return self._to_read(persisted)

    async def _save_replanned_run(
        self,
        run: AgentRun,
        parent_run_id: str,
        key_hash: str | None,
        request_hash: str,
    ) -> AgentRun:
        """保存重规划子运行，并把数据库唯一约束竞争恢复为幂等响应。"""

        try:
            return await self.repository.save(run)
        except IntegrityError:
            # rollback 后 ORM parent/run 可能已过期；这里只使用调用前保存的普通字符串。
            if key_hash is None:
                raise
            existing = await self.repository.get_replan_by_idempotency_key(
                parent_run_id,
                key_hash,
            )
            if existing is None:
                raise
            self._ensure_same_replan_request(existing, request_hash)
            return existing

    @staticmethod
    def _ensure_same_replan_request(run: AgentRun, request_hash: str) -> None:
        """同一个父运行下的重规划幂等键不能被拿来改变请求内容。"""

        if run.replan_request_hash != request_hash:
            raise AgentRunConflictError(
                "Idempotency-Key 已用于另一份重新规划请求，请更换幂等键后再提交"
            )

    async def _load_context(
        self,
        run_id: str,
        *,
        verify_snapshot: bool = False,
        lock_for_update: bool = False,
    ) -> tuple[AgentRun, LoadPlanningTask]:
        """恢复状态机所需的运行和任务输入。"""

        run = await self.repository.get(run_id, for_update=lock_for_update)
        if run is None:
            raise AgentRunNotFoundError("装托智能体运行不存在")
        task = await self.repository.get_task_by_id(run.task_id)
        if task is None:  # 外键正常时不会发生；保留清晰错误以便排查损坏数据。
            raise LoadPlanningTaskNotFoundError("装载任务不存在")
        if verify_snapshot:
            current_hash = _snapshot_hash(_task_snapshot(task))
            if current_hash != run.input_snapshot_hash:
                raise AgentRunConflictError(
                    "任务输入在本次运行创建后发生变化，请基于新任务版本重新启动规划"
                )
        return run, task

    @staticmethod
    def _check_expected_version(run: AgentRun, expected_version: int | None) -> None:
        """新客户端可携带版本号；旧客户端不携带时保持向后兼容。"""

        if expected_version is not None and expected_version != run.run_version:
            raise AgentRunConflictError(
                f"运行版本已变化，当前版本为 {run.run_version}，请刷新后再提交"
            )

    @staticmethod
    def _bump_version(run: AgentRun) -> None:
        run.run_version += 1

    def _mark_failed(self, run: AgentRun, error: Exception) -> None:
        """把不可预期的算法/持久化前置错误转成可查询的失败状态。

        不保存堆栈和数据库连接细节，只保留有限长度的安全摘要；完整堆栈交给服务日志。
        """

        run.status = AgentRunStatus.FAILED
        run.current_step = "FAILED"
        run.failure_code = type(error).__name__
        run.failure_message = str(error)[:2_000] or "智能体执行失败"
        # 多策略生成必须是“全有或全无”。任何策略失败时都清空旧候选，避免 FAILED
        # 运行携带一套看似可审批、实际只生成了一半的方案集合。
        run.plans.clear()
        self._append_event(
            run,
            "RUN_FAILED",
            "智能体运行失败",
            f"{run.failure_code}：{run.failure_message}",
        )

    def _advance(
        self,
        run: AgentRun,
        task: LoadPlanningTask,
        *,
        previous_question_keys: set[str] | None = None,
    ) -> None:
        """反复执行不需要人工判断的步骤，遇到输入或审批节点时停下。"""

        questions = self._collect_questions(run, task, run.answers)
        if questions:
            run.status = AgentRunStatus.AWAITING_INPUT
            run.current_step = "AWAITING_CONSTRAINTS"
            current_keys = {question.key for question in questions}
            if previous_question_keys is None or current_keys != previous_question_keys:
                self._append_event(
                    run,
                    "INPUT_REQUIRED",
                    "需要补充装托约束",
                    "待回答：" + "、".join(question.label for question in questions),
                )
            return

        run.current_step = "GENERATING_PLANS"
        self._replace_plans(run, task)
        run.status = AgentRunStatus.READY_FOR_REVIEW
        run.current_step = "AWAITING_REVIEW"
        self._append_event(
            run,
            "PLANS_GENERATED",
            "候选装托方案已生成",
            f"确定性算法已生成 {len(run.plans)} 套方案，等待人工审批。",
        )

    def _collect_questions(
        self,
        run: AgentRun,
        task: LoadPlanningTask,
        answers: dict[str, object],
    ) -> list[AgentQuestionRead]:
        """根据已知事实动态计算仍缺失的问题，不在数据库中复制派生状态。"""

        return self._collect_questions_from_snapshot(run, _task_snapshot(task), answers)

    def _answer_definition(
        self,
        task: LoadPlanningTask,
        key: str,
    ) -> AgentQuestionRead:
        """找到答案的类型定义，同时阻止覆盖任务中已经确定的安全参数。"""

        if key == "selected_pallet_code" and len(task.pallet_specs) > 1:
            return _question(
                key=key,
                label="选择本次计算使用的托盘规格",
                value_type=QuestionValueType.TEXT,
                unit=None,
                reason="任务包含多个托盘规格。",
            )

        parts = key.split(".")
        if len(parts) != 3:
            raise AgentRunValidationError(f"未知 answer key：{key}")
        scope, code, field = parts
        if scope == "pallet":
            pallet = self._find_pallet(task, code)
            if field == "max_load_kg" and pallet.max_load_kg is None:
                return self._pallet_max_load_question(pallet)
            if field == "max_total_height_mm" and pallet.max_total_height_mm is None:
                return self._pallet_height_question(pallet)
        if scope == "cargo":
            item = self._find_cargo(task, code)
            if field == "stackable" and item.stackable is None:
                return self._cargo_stackable_question(item)
            if field == "max_stack_layers" and item.max_stack_layers is None:
                return self._cargo_layers_question(item)
        raise AgentRunValidationError(f"answer key 不可填写或对应字段已有任务真值：{key}")

    def _normalize_answer(
        self,
        definition: AgentQuestionRead,
        value: str | float | bool,
    ) -> object:
        """按问题定义做严格业务类型校验；bool 不能冒充 0/1 数字。"""

        if definition.value_type is QuestionValueType.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise AgentRunValidationError(f"{definition.key} 必须是非空文本")
            return value.strip()
        if definition.value_type is QuestionValueType.BOOLEAN:
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return value.lower() == "true"
            raise AgentRunValidationError(f"{definition.key} 必须是布尔值")
        if isinstance(value, bool):
            raise AgentRunValidationError(f"{definition.key} 必须是数字")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as error:
            raise AgentRunValidationError(f"{definition.key} 必须是数字") from error
        if definition.value_type is QuestionValueType.INTEGER and not numeric.is_integer():
            raise AgentRunValidationError(f"{definition.key} 必须是整数")
        if definition.min_value is not None and numeric < definition.min_value:
            raise AgentRunValidationError(f"{definition.key} 不能小于 {definition.min_value:g}")
        if definition.max_value is not None and numeric > definition.max_value:
            raise AgentRunValidationError(f"{definition.key} 不能大于 {definition.max_value:g}")
        return int(numeric) if definition.value_type is QuestionValueType.INTEGER else numeric

    def _replace_plans(self, run: AgentRun, task: LoadPlanningTask) -> None:
        """重算时替换旧方案，而不是在同一运行里不断追加重复候选。"""

        pallet = self._find_pallet(task, run.selected_pallet_code or "")
        answers = run.answers
        pallet_input = PalletSpecInput(
            pallet_code=pallet.pallet_code,
            quantity=pallet.quantity,
            length_mm=_number(pallet.length_mm),
            width_mm=_number(pallet.width_mm),
            height_mm=_number(pallet.height_mm),
            tare_weight_kg=_number(pallet.tare_weight_kg),
            max_load_kg=self._effective_number(
                pallet.max_load_kg,
                answers,
                f"pallet.{pallet.pallet_code}.max_load_kg",
            ),
            max_total_height_mm=self._effective_number(
                pallet.max_total_height_mm,
                answers,
                f"pallet.{pallet.pallet_code}.max_total_height_mm",
            ),
        )
        cargo_inputs = [
            CargoSkuInput(
                # 算法按输入行独立装托。即使两行意外共用 sku_code，item_code 仍是任务内
                # 唯一业务键，可防止两种箱规被错误合并到同一托盘。
                sku_code=item.item_code,
                item_code=item.item_code,
                quantity=item.quantity,
                length_mm=_number(item.length_mm),
                width_mm=_number(item.width_mm),
                height_mm=_number(item.height_mm),
                gross_weight_kg=_number(item.gross_weight_kg),
                max_stack_layers=self._effective_layers(item, answers),
            )
            for item in sorted(task.cargo_items, key=lambda value: value.item_code)
        ]
        safety_warnings = self._safety_warnings(task)

        generated_plans: list[PackingPlan] = []
        for strategy_code in run.strategy_codes:
            raw = self.packing_service.plan(
                [pallet_input],
                cargo_inputs,
                PackingStrategy(strategy_code),
            )
            normalized = self._normalize_plan_result(
                run,
                strategy_code,
                raw,
                safety_warnings,
                pallet_input,
            )
            generated_plans.append(PackingPlan(run_id=run.id, **normalized))

        # 先在内存中完整生成所有策略，全部成功后才一次性替换关联集合。若中途异常，
        # _mark_failed 会清空原方案，数据库不会保存“只有第一套成功”的半成品。
        run.plans.clear()
        run.plans.extend(generated_plans)

    def _normalize_plan_result(
        self,
        run: AgentRun,
        strategy_code: str,
        raw: dict[str, object],
        safety_warnings: list[str],
        pallet: PalletSpecInput,
    ) -> dict[str, object]:
        """把算法内部结果收敛到稳定 API，并用可解释公式计算评分。"""

        algorithm_metrics = dict(raw.get("metrics", {}))
        algorithm_pallets = list(raw.get("pallets", []))
        pallets: list[dict[str, object]] = []
        for pallet_index, algorithm_pallet in enumerate(algorithm_pallets, start=1):
            utilization = dict(algorithm_pallet.get("utilization", {}))
            pattern = dict(algorithm_pallet.get("pattern", {}))
            placements = [
                {
                    "placement_id": placement.get("placement_id"),
                    "item_code": placement["item_code"],
                    "box_index": placement["sequence"],
                    "layer": placement.get("layer"),
                    "x_mm": placement["x_mm"],
                    "y_mm": placement["y_mm"],
                    "z_mm": placement["z_mm"],
                    "length_mm": placement["length_mm"],
                    "width_mm": placement["width_mm"],
                    "height_mm": placement["height_mm"],
                    "rotation_deg": placement.get("rotation_deg"),
                    "gross_weight_kg": placement.get("gross_weight_kg"),
                    "orientation": (
                        "LENGTH_ALONG_X" if placement["rotation_deg"] == 0 else "WIDTH_ALONG_X"
                    ),
                }
                for placement in algorithm_pallet["placements"]
            ]
            pallets.append(
                {
                    "pallet_index": pallet_index,
                    "pallet_code": algorithm_pallet["pallet_code"],
                    "loaded_height_mm": algorithm_pallet["total_height_mm"],
                    "payload_weight_kg": algorithm_pallet.get("payload_weight_kg"),
                    "gross_weight_kg": algorithm_pallet["gross_weight_kg"],
                    "box_count": algorithm_pallet["box_count"],
                    "layer_count": algorithm_pallet.get("layer_count"),
                    "capacity_box_count": algorithm_pallet.get("capacity_box_count"),
                    "layer_area_utilization": utilization.get("layer_area_ratio"),
                    "payload_weight_utilization": utilization.get("payload_weight_ratio"),
                    "height_utilization": utilization.get("height_ratio"),
                    "balance_offset": utilization.get("balance_offset"),
                    "pattern_type": pattern.get("type"),
                    "boxes_per_full_layer": pattern.get("boxes_per_full_layer"),
                    "placements": placements,
                }
            )

        algorithm_warning_messages = [
            str(warning.get("message", warning)) if isinstance(warning, dict) else str(warning)
            for warning in list(raw.get("warnings", []))
        ]
        warnings = list(dict.fromkeys([*algorithm_warning_messages, *safety_warnings]))
        total_payload_capacity = float(pallet.max_load_kg or 0) * len(pallets)
        payload_weight = sum(
            float(value["gross_weight_kg"]) - float(pallet.tare_weight_kg) for value in pallets
        )
        loaded_heights = [float(value["loaded_height_mm"]) for value in pallets]
        layer_area_utilization = round(
            float(algorithm_metrics.get("average_layer_area_utilization", 0)),
            6,
        )
        metrics: dict[str, int | float] = {
            "total_pallets": len(pallets),
            "total_boxes": int(algorithm_metrics.get("placed_box_count", 0)),
            "unplaced_boxes": int(algorithm_metrics.get("unplaced_box_count", 0)),
            # 新字段使用准确名称。旧字段暂时返回同一数值，保证旧页面、旧 Java DTO
            # 与已经持久化的方案在滚动升级期间仍然可读。
            "layer_area_utilization": layer_area_utilization,
            "volume_utilization": layer_area_utilization,
            "weight_utilization": round(
                payload_weight / total_payload_capacity if total_payload_capacity else 0,
                6,
            ),
            "average_loaded_height_mm": round(
                sum(loaded_heights) / len(loaded_heights) if loaded_heights else 0,
                6,
            ),
            "max_loaded_height_mm": max(loaded_heights, default=0),
            "total_gross_weight_kg": round(
                sum(float(value["gross_weight_kg"]) for value in pallets),
                6,
            ),
        }

        feasible = bool(raw.get("feasible", int(metrics["unplaced_boxes"]) == 0))
        score = self._score(strategy_code, metrics, pallet, feasible)
        names = {
            PackingStrategyCode.STABLE.value: "稳定优先",
            PackingStrategyCode.BALANCED.value: "均衡方案",
            PackingStrategyCode.UTILIZATION.value: "利用率优先",
        }
        plan_code = f"{strategy_code}-001"
        summary = (
            f"{names[strategy_code]}：使用 {metrics['total_pallets']} 托，"
            f"放置 {metrics['total_boxes']} 箱，未放置 {metrics['unplaced_boxes']} 箱；"
            f"层面积利用率 {float(metrics['layer_area_utilization']) * 100:.1f}%，"
            f"确定性评分 {score:.1f}。"
        )
        return {
            "plan_id": str(uuid5(NAMESPACE_URL, f"{run.id}/{plan_code}")),
            "plan_code": plan_code,
            "strategy_code": strategy_code,
            "strategy_name": names[strategy_code],
            "algorithm_version": str(raw.get("algorithm_version", ALGORITHM_VERSION)),
            "score_version": PLAN_SCORE_VERSION,
            "feasible": feasible,
            "score": score,
            "summary": summary,
            "metrics": metrics,
            "warnings": warnings,
            "pallets": pallets,
        }

    @staticmethod
    def _score(
        strategy_code: str,
        metrics: dict[str, object],
        pallet: PalletSpecInput,
        feasible: bool,
    ) -> float:
        """透明评分公式：完成度 35、层面积 30、重量 15、策略目标 20。"""

        total = int(metrics["total_boxes"]) + int(metrics["unplaced_boxes"])
        completion = int(metrics["total_boxes"]) / total if total else 1.0
        layer_area = min(
            1.0,
            max(0.0, float(metrics["layer_area_utilization"])),
        )
        weight = min(1.0, max(0.0, float(metrics["weight_utilization"])))
        height_ratio = min(
            1.0,
            float(metrics["average_loaded_height_mm"])
            / max(1.0, float(pallet.max_total_height_mm or pallet.height_mm)),
        )
        stability = 1.0 - height_ratio
        if strategy_code == PackingStrategyCode.STABLE.value:
            strategy_component = stability
        elif strategy_code == PackingStrategyCode.UTILIZATION.value:
            strategy_component = layer_area
        else:
            strategy_component = (stability + layer_area) / 2
        score = (
            35 * completion
            + 30 * layer_area
            + 15 * weight
            + 20 * strategy_component
        )
        if not feasible:
            score *= completion
        return round(score, 2)

    @staticmethod
    def _effective_number(
        database_value: Decimal | None,
        answers: dict[str, object],
        answer_key: str,
    ) -> float:
        """读取数据库真值或已经验证过的人工答案。"""

        if database_value is not None:
            return _number(database_value)
        return float(answers[answer_key])

    @staticmethod
    def _effective_layers(item: CargoItem, answers: dict[str, object]) -> int:
        """不可堆码严格限制为一层；可堆码必须已有最大层数。"""

        stackable = (
            item.stackable
            if item.stackable is not None
            else bool(answers[f"cargo.{item.item_code}.stackable"])
        )
        if not stackable:
            return 1
        if item.max_stack_layers is not None:
            return item.max_stack_layers
        return int(answers[f"cargo.{item.item_code}.max_stack_layers"])

    @staticmethod
    def _safety_warnings(task: LoadPlanningTask) -> list[str]:
        """未知但不阻塞本阶段算法的字段必须显式提醒，绝不填经验默认值。"""

        warnings: list[str] = []
        for item in sorted(task.cargo_items, key=lambda value: value.item_code):
            if item.fragile is None:
                warnings.append(f"{item.item_code} 的 fragile 未知，方案未增加易碎品防护规则。")
            if item.keep_upright is None:
                warnings.append(
                    f"{item.item_code} 的 keep_upright 未知；本算法仍统一保持箱体竖直。"
                )
            if item.max_top_load_kg is None:
                warnings.append(
                    f"{item.item_code} 的 max_top_load_kg 未知，需在现场执行前复核顶部承载。"
                )
        return warnings

    @staticmethod
    def _append_event(
        run: AgentRun,
        event_type: str,
        title: str,
        detail: str,
    ) -> None:
        """按运行内单调序号追加事件。"""

        sequence = max((event.sequence for event in run.events), default=0) + 1
        run.events.append(
            AgentRunEvent(
                run_id=run.id,
                sequence=sequence,
                event_type=event_type,
                title=title,
                detail=detail,
            )
        )

    def _collect_questions_from_snapshot(
        self,
        run: AgentRun,
        snapshot: dict[str, object],
        answers: dict[str, object],
    ) -> list[AgentQuestionRead]:
        """根据一份结构化任务快照重建待回答问题。

        GET、跨设备恢复和幂等重放不能读取后来被修改过的任务主表来解释旧运行。否则页面
        可能先展示“这个问题已经不缺了”，提交时又因为快照哈希变化返回 409。运行响应
        因而始终以 ``input_snapshot`` 为事实来源；真正的 resume/decision 仍会额外比较
        当前任务哈希，阻止旧运行基于新主数据继续执行。
        """

        raw_pallets = snapshot.get("pallet_specs")
        raw_cargo = snapshot.get("cargo_items")
        if not isinstance(raw_pallets, list) or not isinstance(raw_cargo, list):
            raise TypeError("智能体运行输入快照结构损坏")

        pallets = [value for value in raw_pallets if isinstance(value, dict)]
        cargo_items = [value for value in raw_cargo if isinstance(value, dict)]
        if len(pallets) != len(raw_pallets) or len(cargo_items) != len(raw_cargo):
            raise RuntimeError("智能体运行输入快照包含无效记录")

        questions: list[AgentQuestionRead] = []
        selected_code = run.selected_pallet_code
        if selected_code is None and len(pallets) > 1:
            questions.append(
                _question(
                    key="selected_pallet_code",
                    label="选择本次计算使用的托盘规格",
                    value_type=QuestionValueType.TEXT,
                    unit=None,
                    reason="任务包含多个托盘规格，算法不能擅自替人工选择。",
                )
            )
        elif selected_code is not None:
            pallet = next(
                (value for value in pallets if str(value.get("pallet_code")) == selected_code),
                None,
            )
            if pallet is None:
                raise RuntimeError(f"运行快照中不存在托盘规格：{selected_code}")
            if pallet.get("max_load_kg") is None and (
                f"pallet.{selected_code}.max_load_kg" not in answers
            ):
                questions.append(
                    _question(
                        key=f"pallet.{selected_code}.max_load_kg",
                        label=f"{selected_code} 最大载重",
                        value_type=QuestionValueType.NUMBER,
                        unit="kg",
                        reason="缺少最大载重时无法证明方案未超重。",
                        min_value=0.001,
                        max_value=1_000_000,
                    )
                )
            if pallet.get("max_total_height_mm") is None and (
                f"pallet.{selected_code}.max_total_height_mm" not in answers
            ):
                try:
                    pallet_height = float(pallet["height_mm"])
                except (KeyError, TypeError, ValueError) as error:
                    raise RuntimeError("运行快照中的托盘高度无效") from error
                questions.append(
                    _question(
                        key=f"pallet.{selected_code}.max_total_height_mm",
                        label=f"{selected_code} 含货最大总高度",
                        value_type=QuestionValueType.NUMBER,
                        unit="mm",
                        reason="缺少含托盘总高度上限时无法证明方案未超高。",
                        min_value=pallet_height + 0.001,
                        max_value=1_000_000,
                    )
                )

        for item in sorted(cargo_items, key=lambda value: str(value.get("item_code"))):
            item_code = str(item.get("item_code"))
            if not item_code or item_code == "None":
                raise RuntimeError("运行快照中的货物编码无效")
            stackable_key = f"cargo.{item_code}.stackable"
            stackable = item.get("stackable")
            if stackable is None:
                if stackable_key not in answers:
                    questions.append(
                        _question(
                            key=stackable_key,
                            label=f"{item_code} 是否允许堆码",
                            value_type=QuestionValueType.BOOLEAN,
                            unit=None,
                            reason="未知堆码能力时不能擅自把箱子叠放。",
                        )
                    )
                    continue
                stackable = bool(answers[stackable_key])
            if bool(stackable):
                layers_key = f"cargo.{item_code}.max_stack_layers"
                if item.get("max_stack_layers") is None and layers_key not in answers:
                    questions.append(
                        _question(
                            key=layers_key,
                            label=f"{item_code} 最大堆码层数",
                            value_type=QuestionValueType.INTEGER,
                            unit="层",
                            reason="已确认可堆码，但仍需明确最大允许层数。",
                            min_value=1,
                            max_value=10_000,
                        )
                    )
        return questions

    def _to_read(self, run: AgentRun) -> AgentRunRead:
        """把 ORM 聚合转换为统一响应，并只用运行快照解释历史状态。"""

        questions = (
            self._collect_questions_from_snapshot(run, run.input_snapshot, run.answers)
            if run.status is AgentRunStatus.AWAITING_INPUT
            else []
        )
        task_number = run.input_snapshot.get("task_number")
        if not isinstance(task_number, str) or not task_number:
            raise RuntimeError("智能体运行输入快照缺少任务编号")
        return AgentRunRead(
            run_id=run.id,
            parent_run_id=run.parent_run_id,
            task_number=task_number,
            status=run.status,
            current_step=run.current_step,
            run_version=run.run_version,
            selected_pallet_code=run.selected_pallet_code,
            selected_plan_code=run.selected_plan_code,
            failure_code=run.failure_code,
            failure_message=run.failure_message,
            questions=questions,
            events=run.events,
            plans=[PackingPlanRead.model_validate(plan) for plan in run.plans],
        )

    @staticmethod
    def _find_pallet(task: LoadPlanningTask, code: str) -> PalletSpec:
        """按业务编码定位托盘规格。"""

        pallet = next(
            (candidate for candidate in task.pallet_specs if candidate.pallet_code == code),
            None,
        )
        if pallet is None:
            raise AgentRunValidationError(f"托盘规格不存在：{code}")
        return pallet

    @staticmethod
    def _find_cargo(task: LoadPlanningTask, code: str) -> CargoItem:
        """按业务编码定位货物箱规。"""

        item = next(
            (candidate for candidate in task.cargo_items if candidate.item_code == code),
            None,
        )
        if item is None:
            raise AgentRunValidationError(f"货物箱规不存在：{code}")
        return item

    @staticmethod
    def _pallet_max_load_question(pallet: PalletSpec) -> AgentQuestionRead:
        return _question(
            key=f"pallet.{pallet.pallet_code}.max_load_kg",
            label=f"{pallet.pallet_code} 最大载重",
            value_type=QuestionValueType.NUMBER,
            unit="kg",
            reason="缺少最大载重时无法证明方案未超重。",
            min_value=0.001,
            max_value=1_000_000,
        )

    @staticmethod
    def _pallet_height_question(pallet: PalletSpec) -> AgentQuestionRead:
        return _question(
            key=f"pallet.{pallet.pallet_code}.max_total_height_mm",
            label=f"{pallet.pallet_code} 含货最大总高度",
            value_type=QuestionValueType.NUMBER,
            unit="mm",
            reason="缺少含托盘总高度上限时无法证明方案未超高。",
            min_value=_number(pallet.height_mm) + 0.001,
            max_value=1_000_000,
        )

    @staticmethod
    def _cargo_stackable_question(item: CargoItem) -> AgentQuestionRead:
        return _question(
            key=f"cargo.{item.item_code}.stackable",
            label=f"{item.item_code} 是否允许堆码",
            value_type=QuestionValueType.BOOLEAN,
            unit=None,
            reason="未知堆码能力时不能擅自把箱子叠放。",
        )

    @staticmethod
    def _cargo_layers_question(item: CargoItem) -> AgentQuestionRead:
        return _question(
            key=f"cargo.{item.item_code}.max_stack_layers",
            label=f"{item.item_code} 最大堆码层数",
            value_type=QuestionValueType.INTEGER,
            unit="层",
            reason="已确认可堆码，但仍需明确最大允许层数。",
            min_value=1,
            max_value=10_000,
        )
