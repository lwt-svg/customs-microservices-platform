"""候选装托方案的只读解释层。

边界必须先于“接入哪个模型”：

1. 输入只能来自 :class:`PackingPlan` 已生成的数值结果；
2. 货物编码、客户、任务号、PDF 原文、描述和来源定位均不会进入 Provider 上下文；
3. 输出不会写数据库，不会追加 AgentRun 事件，也不会参与 LangGraph 路由或审批；
4. 外部 Provider 失败时返回规则解释并标记 ``degraded=true``，主业务流程不受影响。

因此 LLM 在这里扮演 Copilot：帮助人阅读方案，而不是替代确定性算法和人工决策。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.core.config import Settings
from app.models.agent_run import AgentRun, PackingPlan
from app.repositories.agent_runs import AgentRunRepository
from app.schemas.plan_insight import (
    PlanInsightContent,
    PlanInsightProviderCode,
    PlanInsightRead,
    PlanInsightRequest,
)
from app.services.agent_runs import (
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentRunValidationError,
)

logger = logging.getLogger("uvicorn.error")

# 免责声明由服务端固定生成，不能允许模型弱化或删除。
PLAN_INSIGHT_DISCLAIMER = (
    "本解读仅用于说明确定性装载算法已经生成的数值结果，不参与方案计算、流程路由或"
    "审批，也不构成装载与运输安全结论；正式执行前仍须由经过培训的业务人员复核。"
)


class PlanInsightsDisabledError(Exception):
    """功能开关关闭；调用方不应误以为模型调用成功。"""


class PlanInsightProviderError(Exception):
    """模型 Provider 无法产生符合合同的结构化内容。"""


class _NumericPlanContext(BaseModel):
    """允许发给 Provider 的完整上下文白名单。

    这个模型的每个叶子值都是 ``int`` 或 ``float``。新增业务字段时不会自动穿透到
    LLM；开发者必须在这里显式增加一个数值字段并接受代码审查，才能扩大数据边界。
    """

    model_config = ConfigDict(extra="forbid")

    feasible_flag: int = Field(ge=0, le=1)
    score: float = Field(ge=0, le=100)
    total_pallets: int = Field(ge=0)
    total_boxes: int = Field(ge=0)
    unplaced_boxes: int = Field(ge=0)
    layer_area_utilization: float = Field(ge=0, le=1)
    weight_utilization: float = Field(ge=0, le=1)
    average_loaded_height_mm: float = Field(ge=0)
    max_loaded_height_mm: float = Field(ge=0)
    total_gross_weight_kg: float = Field(ge=0)
    warning_count: int = Field(ge=0)
    min_boxes_per_pallet: int = Field(ge=0)
    max_boxes_per_pallet: int = Field(ge=0)
    average_boxes_per_pallet: float = Field(ge=0)
    min_pallet_gross_weight_kg: float = Field(ge=0)
    max_pallet_gross_weight_kg: float = Field(ge=0)
    average_pallet_gross_weight_kg: float = Field(ge=0)
    min_loaded_height_mm: float = Field(ge=0)


class PlanInsightProvider(Protocol):
    """可替换 Provider 的最小接口，便于测试时注入 Stub。"""

    provider_code: PlanInsightProviderCode
    model_name: str

    async def generate(self, context: _NumericPlanContext) -> PlanInsightContent:
        """只根据脱敏数值上下文生成说明。"""


def _finite_number(value: object, *, field_name: str) -> float:
    """拒绝损坏 JSON 中的字符串、布尔值、NaN 和 Infinity。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentRunConflictError(f"候选方案数值字段损坏：{field_name}")
    number = float(value)
    if not math.isfinite(number):
        raise AgentRunConflictError(f"候选方案数值字段损坏：{field_name}")
    return number


def _metric(plan: PackingPlan, field_name: str) -> float:
    """从算法指标白名单读取有限数值。"""

    if field_name not in plan.metrics:
        raise AgentRunConflictError(f"候选方案缺少数值字段：{field_name}")
    return _finite_number(plan.metrics[field_name], field_name=field_name)


def _integer_metric(plan: PackingPlan, field_name: str) -> int:
    """读取非负整数指标，避免把损坏的 1.5 静默截断为 1。"""

    number = _metric(plan, field_name)
    if not number.is_integer() or number < 0:
        raise AgentRunConflictError(f"候选方案整数指标损坏：{field_name}")
    return int(number)


def _build_numeric_context(plan: PackingPlan) -> _NumericPlanContext:
    """将完整方案压缩成不直接包含订单标识的统计数字。

    特别注意：这里不序列化 ``plan.summary``、``plan.warnings`` 的文本，也不遍历
    ``placements``，因此 item_code 等直接标识符不会进入任何模型请求。数值最小化只能
    降低重识别风险，不能承诺任意统计组合在所有场景下都绝对匿名。
    """

    box_counts: list[int] = []
    gross_weights: list[float] = []
    loaded_heights: list[float] = []
    for pallet_index, pallet in enumerate(plan.pallets):
        if not isinstance(pallet, dict):
            raise AgentRunConflictError("候选方案托盘统计结构损坏")
        box_count = _finite_number(
            pallet.get("box_count"),
            field_name=f"pallets[{pallet_index}].box_count",
        )
        if not box_count.is_integer() or box_count < 0:
            raise AgentRunConflictError("候选方案托盘箱数无效")
        box_counts.append(int(box_count))
        gross_weights.append(
            _finite_number(
                pallet.get("gross_weight_kg"),
                field_name=f"pallets[{pallet_index}].gross_weight_kg",
            )
        )
        loaded_heights.append(
            _finite_number(
                pallet.get("loaded_height_mm"),
                field_name=f"pallets[{pallet_index}].loaded_height_mm",
            )
        )

    pallet_count = len(box_counts)
    return _NumericPlanContext(
        feasible_flag=int(plan.feasible),
        score=_finite_number(plan.score, field_name="score"),
        total_pallets=_integer_metric(plan, "total_pallets"),
        total_boxes=_integer_metric(plan, "total_boxes"),
        unplaced_boxes=_integer_metric(plan, "unplaced_boxes"),
        # 新方案使用准确字段名；旧版持久化方案仍只有 volume_utilization，
        # 因此读取时保留兼容回退，但传给 Provider 的白名单只暴露准确语义。
        layer_area_utilization=(
            _metric(plan, "layer_area_utilization")
            if "layer_area_utilization" in plan.metrics
            else _metric(plan, "volume_utilization")
        ),
        weight_utilization=_metric(plan, "weight_utilization"),
        average_loaded_height_mm=_metric(plan, "average_loaded_height_mm"),
        max_loaded_height_mm=_metric(plan, "max_loaded_height_mm"),
        total_gross_weight_kg=_metric(plan, "total_gross_weight_kg"),
        warning_count=len(plan.warnings),
        min_boxes_per_pallet=min(box_counts, default=0),
        max_boxes_per_pallet=max(box_counts, default=0),
        average_boxes_per_pallet=(round(sum(box_counts) / pallet_count, 6) if pallet_count else 0),
        min_pallet_gross_weight_kg=min(gross_weights, default=0),
        max_pallet_gross_weight_kg=max(gross_weights, default=0),
        average_pallet_gross_weight_kg=(
            round(sum(gross_weights) / pallet_count, 6) if pallet_count else 0
        ),
        min_loaded_height_mm=min(loaded_heights, default=0),
    )


class RuleBasedPlanInsightProvider:
    """不联网的确定性解释器，也是所有外部 Provider 的降级路径。"""

    provider_code = PlanInsightProviderCode.RULE_BASED
    model_name = "deterministic-rules-v1"

    async def generate(self, context: _NumericPlanContext) -> PlanInsightContent:
        """根据同一组数字生成稳定、可测试的中文解释。"""

        completion_denominator = context.total_boxes + context.unplaced_boxes
        completion_rate = (
            context.total_boxes / completion_denominator if completion_denominator else 1.0
        )
        summary = (
            f"该方案确定性评分为 {context.score:.1f}，计划使用 {context.total_pallets} 个托盘，"
            f"已放置 {context.total_boxes} 箱，完成率 {completion_rate * 100:.1f}%；"
            f"平均装载高度 {context.average_loaded_height_mm:.1f} mm，"
            f"层面积利用率 {context.layer_area_utilization * 100:.1f}%。"
        )

        tradeoffs = [
            (
                f"托盘箱数范围为 {context.min_boxes_per_pallet}–"
                f"{context.max_boxes_per_pallet} 箱，平均 "
                f"{context.average_boxes_per_pallet:.1f} 箱。"
            ),
            (
                f"重量利用率为 {context.weight_utilization * 100:.1f}%，单托毛重范围为 "
                f"{context.min_pallet_gross_weight_kg:.1f}–"
                f"{context.max_pallet_gross_weight_kg:.1f} kg。"
            ),
            (
                f"单托装载高度范围为 {context.min_loaded_height_mm:.1f}–"
                f"{context.max_loaded_height_mm:.1f} mm。"
            ),
        ]

        risks: list[str] = []
        if not context.feasible_flag:
            risks.append("确定性算法已将当前候选标记为不可行，不能进入正式批准。")
        if context.unplaced_boxes:
            risks.append(f"仍有 {context.unplaced_boxes} 箱未放置，当前候选不能完整覆盖需求。")
        if context.warning_count:
            risks.append(f"原方案包含 {context.warning_count} 项提示，需返回方案明细逐项核对。")
        if context.layer_area_utilization >= 0.9:
            risks.append("层面积利用率较高，现场尺寸偏差和包装鼓包可能缩小平面余量。")
        if context.max_loaded_height_mm > context.average_loaded_height_mm * 1.25:
            risks.append("最高托盘明显高于平均值，应重点复核高度分布和搬运稳定性。")
        if not risks:
            risks.append("数值摘要未显示明显异常，但仍不能替代包装强度与现场固定检查。")

        next_checks = [
            "打开三维方案核对箱体朝向、越界、悬空和碰撞检查结果。",
            "逐项核对原方案提示，并确认托盘承重、限高和堆码约束来自已验证资料。",
            "与其他候选方案比较托盘数、利用率、高度分布和未放置箱数后再审批。",
        ]
        return PlanInsightContent(
            summary=summary,
            tradeoffs=tradeoffs,
            risks=risks,
            next_checks=next_checks,
        )


def _provider_prompt(context: _NumericPlanContext) -> str:
    """构造两个模型 Provider 共用的最小提示词。

    Prompt 自身再次声明禁止工具和文件访问；真正的数据最小化由
    :class:`_NumericPlanContext` 的字段白名单保证。
    """

    return (
        "你是企业装载方案的只读解读助手。请只解释下面的匿名数值上下文，并用中文输出。"
        "不得调用任何工具、运行命令、访问网络、读取文件或尝试寻找更多上下文；"
        "不得推断客户、商品、任务、文件来源等身份信息；不得修改数值、生成装载坐标、"
        "批准方案或声称方案已满足运输安全要求。summary 应简洁说明结果；tradeoffs 说明"
        "数字体现的取舍；risks 只指出需人工复核的风险；next_checks 给出具体复核动作。"
        "不要输出免责声明，免责声明由服务端统一追加。\n"
        "匿名数值上下文：\n"
        f"{context.model_dump_json()}"
    )


def _content_json_schema() -> dict[str, object]:
    """生成 Codex CLI 和 Responses API 共用的严格输出 Schema。"""

    return PlanInsightContent.model_json_schema()


def _parse_provider_content(raw_text: str) -> PlanInsightContent:
    """集中执行 JSON 解析与 Pydantic 边界校验。"""

    try:
        payload = json.loads(raw_text)
        return PlanInsightContent.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as error:
        raise PlanInsightProviderError("模型没有返回有效的结构化方案解读") from error


def _codex_subprocess_environment() -> dict[str, str]:
    """只向 Codex 子进程传递运行、认证目录和联网所需的最小环境。

    这里故意不复制 ``OPENAI_API_KEY`` 等凭证变量。Codex CLI 按自己的正常机制从
    ``CODEX_HOME``（或 HOME 下的默认目录）读取登录状态，Python 服务既不读取凭证内容，
    也不会把宿主进程的其他业务变量一并暴露给子进程。
    """

    allowed_names = {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    return {name: value for name in allowed_names if (value := os.environ.get(name))}


class CodexCliPlanInsightProvider:
    """通过本机 ``codex exec`` 生成结构化说明。

    服务不读取 Codex 凭证，也没有 Codex token 配置项。认证完全由 Codex CLI 自身处理；
    子进程只拿到匿名数字。命令使用 ``create_subprocess_exec`` 参数数组而不是 shell，
    从根源上避免 prompt 或模型名被解释为命令片段。
    """

    provider_code = PlanInsightProviderCode.CODEX_CLI

    def __init__(self, *, model_name: str | None, timeout_seconds: float) -> None:
        self.model_name = model_name or "codex-default"
        self._configured_model = model_name
        self._timeout_seconds = timeout_seconds

    async def _ensure_authenticated(self, workdir: Path) -> None:
        """在真正生成前快速确认 CLI 已登录。

        未登录的 ``codex exec`` 会做多轮网络重试，最后才返回 401。对页面来说这只是一个
        已知的本机配置问题，不应该让用户等待几十秒。预检同样使用空工作目录、最小环境和
        参数数组；stdout/stderr 都被丢弃，不写日志也不回传浏览器。
        """

        process = await asyncio.create_subprocess_exec(
            "codex",
            "login",
            "status",
            cwd=workdir,
            env=_codex_subprocess_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(
                process.communicate(),
                timeout=min(self._timeout_seconds, 5.0),
            )
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            raise PlanInsightProviderError("Codex CLI 登录状态检查超时") from error
        if process.returncode != 0:
            raise PlanInsightProviderError("Codex CLI 尚未登录")

    async def generate(self, context: _NumericPlanContext) -> PlanInsightContent:
        schema_text = json.dumps(_content_json_schema(), ensure_ascii=False)
        prompt = _provider_prompt(context)

        # schema 放在临时根目录；真正传给 Codex 的 cwd 是它的空子目录。这样既能使用
        # --output-schema，又不会让模型在工作目录里看到项目、笔记或企业文件。
        try:
            with tempfile.TemporaryDirectory(prefix="load-plan-insight-") as temporary_root:
                root = Path(temporary_root)
                empty_workdir = root / "workdir"
                empty_workdir.mkdir()
                schema_path = root / "output-schema.json"
                schema_path.write_text(schema_text, encoding="utf-8")

                await self._ensure_authenticated(empty_workdir)
                arguments = [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--ignore-rules",
                ]
                if self._configured_model:
                    arguments.extend(["--model", self._configured_model])
                arguments.extend(
                    [
                        "--output-schema",
                        str(schema_path),
                        "--color",
                        "never",
                        "--cd",
                        str(empty_workdir),
                        "-",
                    ]
                )
                process = await asyncio.create_subprocess_exec(
                    *arguments,
                    cwd=empty_workdir,
                    env=_codex_subprocess_environment(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, _stderr = await asyncio.wait_for(
                        process.communicate(prompt.encode("utf-8")),
                        timeout=self._timeout_seconds,
                    )
                except TimeoutError as error:
                    process.kill()
                    await process.communicate()
                    raise PlanInsightProviderError("Codex CLI 方案解读超时") from error
        except PlanInsightProviderError:
            raise
        except OSError as error:
            # 包含 codex 不在 PATH、临时目录不可用及子进程管道错误。
            raise PlanInsightProviderError("Codex CLI 不可用") from error

        if process.returncode != 0:
            # stderr 可能包含本机路径或认证诊断，只记录退出码，不回传也不写日志正文。
            raise PlanInsightProviderError(f"Codex CLI 方案解读失败，退出码 {process.returncode}")
        if len(stdout) > 100_000:
            raise PlanInsightProviderError("Codex CLI 返回内容超过安全上限")
        try:
            raw_text = stdout.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise PlanInsightProviderError("Codex CLI 返回了无效编码") from error
        return _parse_provider_content(raw_text)


def _extract_responses_output_text(payload: object) -> str:
    """从 Responses REST 响应中提取首个 ``output_text``。

    官方 SDK 有 ``response.output_text`` 便捷属性；这里为了不增加依赖而直接使用 httpx，
    因而同时兼容 REST 的 ``output[].content[]`` 结构和测试桩的顶层便捷字段。
    """

    if not isinstance(payload, dict):
        raise PlanInsightProviderError("Responses API 返回结构无效")
    top_level = payload.get("output_text")
    if isinstance(top_level, str) and top_level.strip():
        return top_level
    outputs = payload.get("output")
    if not isinstance(outputs, list):
        raise PlanInsightProviderError("Responses API 未返回 output")
    for output in outputs:
        if not isinstance(output, dict):
            continue
        contents = output.get("content")
        if not isinstance(contents, list):
            continue
        for content in contents:
            if (
                isinstance(content, dict)
                and content.get("type") == "output_text"
                and isinstance(content.get("text"), str)
                and content["text"].strip()
            ):
                return content["text"]
    raise PlanInsightProviderError("Responses API 未返回结构化文本")


class OpenAIResponsesPlanInsightProvider:
    """使用 httpx 调用 OpenAI Responses API，避免为一个端点增加 SDK 依赖。"""

    provider_code = PlanInsightProviderCode.OPENAI

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def generate(self, context: _NumericPlanContext) -> PlanInsightContent:
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            raise PlanInsightProviderError("OpenAI API Key 未配置")

        request_body = {
            "model": self.model_name,
            "input": _provider_prompt(context),
            # 不声明 tools，模型不能请求外部工具；store=false 还会要求 API 不持久化响应。
            "tools": [],
            "store": False,
            "max_output_tokens": 1_200,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "load_plan_insight",
                    "strict": True,
                    "schema": _content_json_schema(),
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/responses",
                    headers=headers,
                    json=request_body,
                )
                response.raise_for_status()
                if len(response.content) > 100_000:
                    raise PlanInsightProviderError("Responses API 返回内容超过安全上限")
                payload = response.json()
        except PlanInsightProviderError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            # 不把响应正文放进异常，避免上游错误页或代理回显任何敏感诊断。
            raise PlanInsightProviderError("Responses API 调用失败") from error

        return _parse_provider_content(_extract_responses_output_text(payload))


def create_plan_insight_provider(settings: Settings) -> PlanInsightProvider:
    """按配置创建主 Provider；选择发生在请求边界，不进入 LangGraph。"""

    if settings.load_planning_plan_insights_provider == "codex_cli":
        return CodexCliPlanInsightProvider(
            model_name=settings.load_planning_codex_model,
            timeout_seconds=settings.load_planning_llm_timeout_seconds,
        )
    if settings.load_planning_plan_insights_provider == "openai_responses":
        return OpenAIResponsesPlanInsightProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model_name=settings.load_planning_llm_model,
            timeout_seconds=settings.load_planning_llm_timeout_seconds,
        )
    return RuleBasedPlanInsightProvider()


class PlanInsightService:
    """编排“查方案 → 脱敏 → 调 Provider → 安全降级”的只读应用服务。"""

    def __init__(
        self,
        repository: AgentRunRepository,
        primary_provider: PlanInsightProvider,
        *,
        enabled: bool,
        fallback_provider: PlanInsightProvider | None = None,
    ) -> None:
        self._repository = repository
        self._primary_provider = primary_provider
        self._enabled = enabled
        self._fallback_provider = fallback_provider or RuleBasedPlanInsightProvider()

    async def generate(
        self,
        run_id: str,
        payload: PlanInsightRequest,
    ) -> PlanInsightRead:
        """生成一次瞬时解读；本方法没有任何 ``save`` 或 ``commit``。"""

        if not self._enabled:
            raise PlanInsightsDisabledError("候选方案智能解读功能尚未开启")

        run = await self._repository.get(run_id)
        if run is None:
            raise AgentRunNotFoundError("装托智能体运行不存在")
        plan = self._select_plan(run, payload.plan_code)
        context = _build_numeric_context(plan)

        actual_provider = self._primary_provider
        degraded = False
        try:
            content = await actual_provider.generate(context)
        except PlanInsightProviderError as error:
            # 外部 Provider 的网络、认证、超时、CLI 或格式错误都不应使候选方案页面不可用。
            # 日志只记录异常类型，不记录 prompt、API 响应、stderr 或凭证。
            if actual_provider.provider_code is PlanInsightProviderCode.RULE_BASED:
                raise
            logger.warning(
                "方案解读 Provider 降级：provider=%s error_type=%s",
                actual_provider.provider_code.value,
                type(error).__name__,
            )
            actual_provider = self._fallback_provider
            content = await actual_provider.generate(context)
            degraded = True

        return PlanInsightRead(
            run_id=run.id,
            plan_code=plan.plan_code,
            provider=actual_provider.provider_code,
            model=actual_provider.model_name,
            degraded=degraded,
            generated_at=datetime.now(UTC),
            disclaimer=PLAN_INSIGHT_DISCLAIMER,
            **content.model_dump(),
        )

    @staticmethod
    def _select_plan(run: AgentRun, requested_plan_code: str | None) -> PackingPlan:
        """选择逻辑固定在普通代码中，不能让 LLM 决定解释或审批哪套方案。"""

        if not run.plans:
            raise AgentRunConflictError("当前运行尚未生成候选方案，不能请求方案解读")
        if requested_plan_code is not None:
            selected = next(
                (plan for plan in run.plans if plan.plan_code == requested_plan_code),
                None,
            )
            if selected is None:
                raise AgentRunValidationError("plan_code 不属于当前运行")
            return selected
        if run.selected_plan_code is not None:
            selected = next(
                (plan for plan in run.plans if plan.plan_code == run.selected_plan_code),
                None,
            )
            if selected is not None:
                return selected
        # 尚未审批时解释评分最高的方案；plan_code 作为稳定次级键，保证并列分数可复现。
        return max(run.plans, key=lambda plan: (plan.score, plan.plan_code))
