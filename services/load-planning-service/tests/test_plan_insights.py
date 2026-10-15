"""候选方案解读 Copilot 的安全边界、Provider 和 HTTP 合同测试。

测试绝不真实调用 Codex 或 OpenAI。CLI 使用假的异步子进程，Responses API 使用
``httpx.MockTransport``；因此本文件在离线 CI 中也能稳定执行，不消耗模型额度。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import settings
from app.models.agent_run import AgentRun, AgentRunStatus, PackingPlan
from app.schemas.plan_insight import (
    PlanInsightContent,
    PlanInsightProviderCode,
    PlanInsightRequest,
)
from app.services.plan_insights import (
    OpenAIResponsesPlanInsightProvider,
    PlanInsightService,
    _NumericPlanContext,
)


def build_task(task_number: str, *, complete: bool = True) -> dict[str, object]:
    """创建包含敏感样例文本的任务，用来证明这些文本不会进入模型 prompt。"""

    cargo: dict[str, object] = {
        "item_code": "ITEM-PRIVATE-001",
        "sku_code": "SKU-PRIVATE-001",
        "cargo_name": "客户专属秘密产品",
        "quantity": 24,
        "length_mm": 300,
        "width_mm": 200,
        "height_mm": 180,
        "gross_weight_kg": 8,
        "keep_upright": True,
        "fragile": False,
        "source_type": "MANUAL",
        "verified": True,
    }
    pallet: dict[str, object] = {
        "pallet_code": "PALLET-PRIVATE-001",
        "pallet_name": "内部托盘",
        "quantity": 10,
        "length_mm": 1200,
        "width_mm": 1000,
        "height_mm": 150,
        "tare_weight_kg": 25,
        "source_type": "MANUAL",
        "verified": True,
    }
    if complete:
        cargo.update(stackable=True, max_stack_layers=4)
        pallet.update(max_load_kg=500, max_total_height_mm=1250)
    return {
        "task_number": task_number,
        "task_name": "绝密客户装载任务",
        "description": "这段业务描述绝不能发给任何模型",
        "cargo_items": [cargo],
        "pallet_specs": [pallet],
    }


async def create_run(
    client: AsyncClient,
    task_number: str,
    *,
    complete: bool = True,
) -> dict[str, object]:
    task_response = await client.post(
        "/api/v1/load-planning-tasks",
        json=build_task(task_number, complete=complete),
    )
    assert task_response.status_code == 201, task_response.text
    run_response = await client.post(
        f"/api/v1/load-planning/tasks/{task_number}/agent-runs",
        json={},
    )
    assert run_response.status_code == 201, run_response.text
    return run_response.json()


async def test_plan_insights_are_disabled_by_default(client: AsyncClient) -> None:
    """默认关闭时明确返回 503，不会静默调用规则或外部模型。"""

    run = await create_run(client, "LP-INSIGHT-DISABLED")
    response = await client.post(
        f"/api/v1/load-planning/agent-runs/{run['run_id']}/plan-insights",
        json={},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "候选方案智能解读功能尚未开启"


async def test_rule_based_insight_is_read_only_and_selects_requested_plan(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """规则 Provider 返回稳定合同，且调用前后的 AgentRun 完全一致。"""

    monkeypatch.setattr(settings, "load_planning_plan_insights_enabled", True)
    monkeypatch.setattr(settings, "load_planning_plan_insights_provider", "rule_based")
    run = await create_run(client, "LP-INSIGHT-RULE")
    before = await client.get(f"/api/v1/load-planning/agent-runs/{run['run_id']}")
    target_plan_code = run["plans"][1]["plan_code"]

    response = await client.post(
        f"/api/v1/load-planning/agent-runs/{run['run_id']}/plan-insights",
        json={"plan_code": target_plan_code},
    )
    after = await client.get(f"/api/v1/load-planning/agent-runs/{run['run_id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run["run_id"]
    assert body["plan_code"] == target_plan_code
    assert body["provider"] == "RULE_BASED"
    assert body["model"] == "deterministic-rules-v1"
    assert body["degraded"] is False
    assert body["generated_at"]
    assert body["summary"]
    assert body["tradeoffs"]
    assert body["risks"]
    assert body["next_checks"]
    assert "不参与方案计算" in body["disclaimer"]
    assert before.json() == after.json()


async def test_plan_insight_http_errors_are_stable(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不存在、未生成和错误 plan_code 分别返回 404/409/422。"""

    monkeypatch.setattr(settings, "load_planning_plan_insights_enabled", True)
    monkeypatch.setattr(settings, "load_planning_plan_insights_provider", "rule_based")

    missing = await client.post(
        "/api/v1/load-planning/agent-runs/not-found/plan-insights",
        json={},
    )
    awaiting = await create_run(client, "LP-INSIGHT-AWAITING", complete=False)
    no_plans = await client.post(
        f"/api/v1/load-planning/agent-runs/{awaiting['run_id']}/plan-insights",
        json={},
    )
    ready = await create_run(client, "LP-INSIGHT-WRONG-CODE")
    wrong_plan = await client.post(
        f"/api/v1/load-planning/agent-runs/{ready['run_id']}/plan-insights",
        json={"plan_code": "NOT-IN-THIS-RUN"},
    )

    assert missing.status_code == 404
    assert no_plans.status_code == 409
    assert wrong_plan.status_code == 422


class _FakeCodexProcess:
    """模拟 ``asyncio.subprocess.Process`` 的最小行为。"""

    def __init__(self, *, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.stdin_payload: bytes | None = None
        self.killed = False

    async def communicate(self, payload: bytes | None = None) -> tuple[bytes, bytes]:
        if payload is not None:
            self.stdin_payload = payload
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


async def test_codex_cli_uses_safe_process_contract_and_anonymized_prompt(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI 必须使用参数数组、空 cwd、最小 env 和结构化输出，且不读取真实业务字段。"""

    monkeypatch.setattr(settings, "load_planning_plan_insights_enabled", True)
    monkeypatch.setattr(settings, "load_planning_plan_insights_provider", "codex_cli")
    monkeypatch.setattr(settings, "load_planning_codex_model", None)
    monkeypatch.setenv("MYSQL_PASSWORD", "must-not-reach-codex")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("LOAD_PLANNING_DATABASE_URL", "must-not-reach-codex")
    monkeypatch.setenv("NACOS_PASSWORD", "must-not-reach-codex")

    model_content = {
        "summary": "结构化模型摘要",
        "tradeoffs": ["利用率与高度需要综合比较"],
        "risks": ["仍需现场复核"],
        "next_checks": ["检查三维布局"],
    }
    login_process = _FakeCodexProcess(stdout=b"Logged in")
    exec_process = _FakeCodexProcess(stdout=json.dumps(model_content, ensure_ascii=False).encode())
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*arguments: str, **kwargs: object) -> object:
        environment = kwargs["env"]
        assert "MYSQL_PASSWORD" not in environment
        assert "OPENAI_API_KEY" not in environment
        assert "LOAD_PLANNING_DATABASE_URL" not in environment
        assert "NACOS_PASSWORD" not in environment
        if arguments == ("codex", "login", "status"):
            captured["login_arguments"] = arguments
            captured["login_kwargs"] = kwargs
            return login_process

        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        workdir = kwargs["cwd"]
        assert list(workdir.iterdir()) == []
        schema_path = arguments[arguments.index("--output-schema") + 1]
        schema_text = await asyncio.to_thread(Path(schema_path).read_text, encoding="utf-8")
        schema = json.loads(schema_text)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "summary",
            "tradeoffs",
            "risks",
            "next_checks",
        }
        return exec_process

    monkeypatch.setattr(
        "app.services.plan_insights.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    run = await create_run(client, "LP-INSIGHT-CODEX")
    response = await client.post(
        f"/api/v1/load-planning/agent-runs/{run['run_id']}/plan-insights",
        json={},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "CODEX_CLI"
    assert body["model"] == "codex-default"
    assert body["degraded"] is False
    assert captured["login_arguments"] == ("codex", "login", "status")
    assert captured["login_kwargs"]["cwd"] == captured["kwargs"]["cwd"]
    arguments = captured["arguments"]
    assert "--ephemeral" in arguments
    assert ("--sandbox", "read-only") == (
        arguments[arguments.index("--sandbox")],
        arguments[arguments.index("--sandbox") + 1],
    )
    assert "--skip-git-repo-check" in arguments
    assert "--ignore-user-config" in arguments
    assert "--ignore-rules" in arguments
    assert "--model" not in arguments
    assert arguments[-1] == "-"
    prompt = exec_process.stdin_payload.decode()
    assert "不得调用任何工具" in prompt
    assert "ITEM-PRIVATE-001" not in prompt
    assert "SKU-PRIVATE-001" not in prompt
    assert "PALLET-PRIVATE-001" not in prompt
    assert "LP-INSIGHT-CODEX" not in prompt
    assert "绝密客户装载任务" not in prompt
    assert "这段业务描述绝不能发给任何模型" not in prompt
    assert "source_locator" not in prompt


async def test_codex_cli_failure_falls_back_without_leaking_stderr(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登录预检失败会快速降级，stderr 诊断不会进入 HTTP 响应。"""

    monkeypatch.setattr(settings, "load_planning_plan_insights_enabled", True)
    monkeypatch.setattr(settings, "load_planning_plan_insights_provider", "codex_cli")
    login_process = _FakeCodexProcess(
        stdout=b"",
        stderr=b"secret-local-path-and-auth-diagnostic",
        returncode=7,
    )
    exec_started = False

    async def fake_create_subprocess_exec(*arguments: str, **_kwargs: object) -> object:
        nonlocal exec_started
        if arguments == ("codex", "login", "status"):
            return login_process
        exec_started = True
        return _FakeCodexProcess(stdout=b"must-not-run")

    monkeypatch.setattr(
        "app.services.plan_insights.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    run = await create_run(client, "LP-INSIGHT-CODEX-FALLBACK")
    response = await client.post(
        f"/api/v1/load-planning/agent-runs/{run['run_id']}/plan-insights",
        json={},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "RULE_BASED"
    assert body["model"] == "deterministic-rules-v1"
    assert body["degraded"] is True
    assert "secret-local-path" not in response.text
    assert exec_started is False


async def test_openai_responses_provider_uses_structured_output_and_mock_http() -> None:
    """Responses Provider 只发送匿名数字，并解析 REST output 结构。"""

    captured: dict[str, object] = {}
    expected_content = {
        "summary": "OpenAI 结构化摘要",
        "tradeoffs": ["托盘数与利用率取舍"],
        "risks": ["需要人工复核"],
        "next_checks": ["比较候选方案"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        body = json.loads(request.content)
        captured["body"] = body
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(expected_content, ensure_ascii=False),
                            }
                        ],
                    }
                ]
            },
        )

    provider = OpenAIResponsesPlanInsightProvider(
        api_key=SecretStr("test-only-key"),
        base_url="https://api.openai.com/v1",
        model_name="gpt-6-luna",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    context = _NumericPlanContext(
        feasible_flag=1,
        score=88.5,
        total_pallets=2,
        total_boxes=24,
        unplaced_boxes=0,
        volume_utilization=0.72,
        weight_utilization=0.61,
        average_loaded_height_mm=870,
        max_loaded_height_mm=900,
        total_gross_weight_kg=242,
        warning_count=1,
        min_boxes_per_pallet=12,
        max_boxes_per_pallet=12,
        average_boxes_per_pallet=12,
        min_pallet_gross_weight_kg=121,
        max_pallet_gross_weight_kg=121,
        average_pallet_gross_weight_kg=121,
        min_loaded_height_mm=840,
    )

    result = await provider.generate(context)

    assert result == PlanInsightContent.model_validate(expected_content)
    request = captured["request"]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer test-only-key"
    body = captured["body"]
    assert body["model"] == "gpt-6-luna"
    assert body["store"] is False
    assert body["tools"] == []
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"]["additionalProperties"] is False
    assert "ITEM-PRIVATE-001" not in body["input"]
    assert "source_locator" not in body["input"]


async def test_openai_without_key_degrades_to_rules(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有 API Key 是可预期的 Provider 不可用，应透明降级而不是 500。"""

    monkeypatch.setattr(settings, "load_planning_plan_insights_enabled", True)
    monkeypatch.setattr(settings, "load_planning_plan_insights_provider", "openai_responses")
    monkeypatch.setattr(settings, "openai_api_key", None)
    run = await create_run(client, "LP-INSIGHT-NO-KEY")

    response = await client.post(
        f"/api/v1/load-planning/agent-runs/{run['run_id']}/plan-insights",
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "RULE_BASED"
    assert response.json()["degraded"] is True


def build_persisted_run() -> AgentRun:
    """构造不依赖数据库的持久化聚合，用于隔离测试 Provider 注入。"""

    plan = PackingPlan(
        id=1,
        run_id="run-provider-stub",
        plan_id="plan-provider-stub",
        plan_code="BALANCED-001",
        strategy_code="BALANCED",
        strategy_name="均衡方案",
        algorithm_version="TEST",
        score_version="TEST",
        feasible=True,
        score=90,
        summary="绝不能进入 Provider 的旧摘要",
        metrics={
            "total_pallets": 1,
            "total_boxes": 10,
            "unplaced_boxes": 0,
            "volume_utilization": 0.7,
            "weight_utilization": 0.6,
            "average_loaded_height_mm": 800,
            "max_loaded_height_mm": 800,
            "total_gross_weight_kg": 120,
        },
        warnings=["绝不能进入 Provider 的旧警告"],
        pallets=[
            {
                "pallet_code": "PRIVATE-PALLET",
                "loaded_height_mm": 800,
                "gross_weight_kg": 120,
                "box_count": 10,
                "placements": [{"item_code": "PRIVATE-ITEM"}],
            }
        ],
    )
    return AgentRun(
        id="run-provider-stub",
        task_id=1,
        status=AgentRunStatus.READY_FOR_REVIEW,
        current_step="AWAITING_REVIEW",
        selected_pallet_code="PRIVATE-PALLET",
        selected_plan_code=None,
        strategy_codes=["BALANCED"],
        input_snapshot_hash="hash",
        input_snapshot={"task_number": "PRIVATE-TASK"},
        answers={},
        run_version=1,
        events=[],
        plans=[plan],
    )


async def test_service_accepts_mock_provider_and_does_not_mask_programming_errors() -> None:
    """Service 只降级显式 ProviderError；普通 RuntimeError 必须暴露给测试和监控。"""

    run = build_persisted_run()
    repository = SimpleNamespace(get=AsyncMock(return_value=run))
    provider = SimpleNamespace(
        provider_code=PlanInsightProviderCode.OPENAI,
        model_name="mock-model",
        generate=AsyncMock(side_effect=RuntimeError("programming bug")),
    )
    fallback = SimpleNamespace(
        provider_code=PlanInsightProviderCode.RULE_BASED,
        model_name="mock-fallback",
        generate=AsyncMock(),
    )
    service = PlanInsightService(
        repository,
        provider,
        enabled=True,
        fallback_provider=fallback,
    )

    with pytest.raises(RuntimeError, match="programming bug"):
        await service.generate("run-provider-stub", PlanInsightRequest())

    fallback.generate.assert_not_awaited()
    context = provider.generate.await_args.args[0]
    assert isinstance(context, _NumericPlanContext)
    assert all(isinstance(value, (int, float)) for value in context.model_dump().values())
