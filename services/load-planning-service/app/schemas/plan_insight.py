"""候选装托方案解读 Copilot 的 HTTP 契约。

这个接口只解释确定性算法已经生成的方案，不产生新坐标、不改变运行状态，也不代替
仓库、安全或审批人员。把模型输出约束成结构化字段，可以让 Java BFF 和 Vue 页面稳定
渲染，而不需要解析一整段不可控的聊天文本。
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

InsightListItem = Annotated[str, Field(min_length=1, max_length=500)]


class PlanInsightProviderCode(StrEnum):
    """响应中公开实际产生文本的 Provider，而不是仅返回配置值。"""

    OPENAI = "OPENAI"
    CODEX_CLI = "CODEX_CLI"
    RULE_BASED = "RULE_BASED"


class PlanInsightRequest(BaseModel):
    """选择要解读的候选方案。

    没传 ``plan_code`` 时，服务优先选择已批准方案；运行尚未批准时，则选择确定性评分
    最高的候选方案。选择逻辑在服务端完成，不交给 LLM 猜测。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    plan_code: str | None = Field(default=None, min_length=1, max_length=64)


class PlanInsightContent(BaseModel):
    """Provider 必须返回的纯内容结构。

    ``extra="forbid"`` 同时用于 Pydantic 校验和 JSON Schema：模型不能悄悄增加
    “自动批准”“修改坐标”等越权字段。
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1_200)
    tradeoffs: list[InsightListItem] = Field(max_length=6)
    risks: list[InsightListItem] = Field(max_length=6)
    next_checks: list[InsightListItem] = Field(max_length=6)


class PlanInsightRead(PlanInsightContent):
    """返回给 BFF/前端的完整只读解读结果。"""

    run_id: str
    plan_code: str
    provider: PlanInsightProviderCode
    model: str
    degraded: bool
    generated_at: datetime
    disclaimer: str
