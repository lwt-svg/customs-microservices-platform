"""本地 RPA Sandbox 网站。

这不是对真实 LINK 或单一窗口页面的复制，也不包含企业 URL、账号、Cookie、UKey 口令或
原始 XPath。它只保留业务动作的形状，供 Playwright Worker 在 Mac、Windows 和 CI 中做
可重复的端到端练习：

* LINK：普通登录 → 七步字段 → 保存草稿；
* 单一窗口：卡介质就绪 → 产地证/商检字段 → 暂存；
* 所有页面只有 ``SAVE/STAGE``，没有正式提交或申报按钮。

页面使用 ``data-rpa`` 语义选择器。真实网站以后使用独立 SelectorPack，业务 Handler
不应把 Sandbox 的 DOM 选择器误当成生产网站事实。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.core.config import settings
from app.rpa.contracts import RpaOperation

router = APIRouter(prefix="/rpa-sandbox", tags=["rpa-sandbox"])

LINK_SANDBOX_FINGERPRINT = "LINK_SANDBOX_V1"
SINGLE_WINDOW_SANDBOX_FINGERPRINT = "SINGLE_WINDOW_SANDBOX_V1"


class SandboxDraftWrite(BaseModel):
    """浏览器页面保存到 Sandbox 的最小请求。

    ``fields`` 只用于本地 E2E 对账，不写主业务表；真实企业资料不得发送到本接口。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_reference: str = Field(min_length=1, max_length=128)
    fields: dict[str, JsonValue]


class SandboxDraftRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: RpaOperation
    business_reference: str
    idempotency_key: str
    external_reference: str
    payload_hash: str
    fields: dict[str, JsonValue]
    saved_at: datetime


@dataclass(frozen=True, slots=True)
class _StoredDraft:
    operation: RpaOperation
    business_reference: str
    idempotency_key: str
    external_reference: str
    payload_hash: str
    fields: dict[str, JsonValue]
    saved_at: datetime


class RpaSandboxDraftStore:
    """进程内、带幂等校验的 Sandbox 草稿库。

    主业务可靠性由 ``rpa_jobs`` MySQL 表保证。这里故意只做轻量外站模拟，重启后可以
    清空；Worker 的未知结果与对账逻辑仍然经过真实 RPA Job 控制面。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_business_key: dict[tuple[RpaOperation, str], _StoredDraft] = {}
        self._by_idempotency_key: dict[str, _StoredDraft] = {}

    async def save(
        self,
        operation: RpaOperation,
        idempotency_key: str,
        payload: SandboxDraftWrite,
    ) -> _StoredDraft:
        canonical = json.dumps(
            payload.fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        async with self._lock:
            replay = self._by_idempotency_key.get(idempotency_key)
            if replay is not None:
                if (
                    replay.operation is not operation
                    or replay.business_reference != payload.business_reference
                    or replay.payload_hash != payload_hash
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Sandbox Idempotency-Key 已用于另一份草稿内容",
                    )
                return replay

            business_key = (operation, payload.business_reference)
            existing = self._by_business_key.get(business_key)
            if existing is not None and existing.payload_hash != payload_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="同一业务编号已有不同内容的 Sandbox 草稿",
                )

            reference_prefix = {
                RpaOperation.LINK_BOOKING_STAGE: "LINK-DRAFT",
                RpaOperation.COO_STAGE: "COO-STAGED",
                RpaOperation.CIQ_STAGE: "CIQ-STAGED",
            }[operation]
            stored = _StoredDraft(
                operation=operation,
                business_reference=payload.business_reference,
                idempotency_key=idempotency_key,
                external_reference=(
                    f"{reference_prefix}-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:12].upper()}"
                ),
                payload_hash=payload_hash,
                fields=dict(payload.fields),
                saved_at=datetime.now(UTC),
            )
            self._by_business_key[business_key] = stored
            self._by_idempotency_key[idempotency_key] = stored
            return stored

    async def get(
        self,
        operation: RpaOperation,
        business_reference: str,
    ) -> _StoredDraft | None:
        async with self._lock:
            return self._by_business_key.get((operation, business_reference))

    async def clear(self) -> None:
        async with self._lock:
            self._by_business_key.clear()
            self._by_idempotency_key.clear()


sandbox_draft_store = RpaSandboxDraftStore()


def _require_enabled() -> None:
    if not settings.rpa_sandbox_portal_enabled:
        raise HTTPException(status_code=404, detail="RPA Sandbox 未开启")


def _to_read(stored: _StoredDraft) -> SandboxDraftRead:
    return SandboxDraftRead(
        operation=stored.operation,
        business_reference=stored.business_reference,
        idempotency_key=stored.idempotency_key,
        external_reference=stored.external_reference,
        payload_hash=stored.payload_hash,
        fields=stored.fields,
        saved_at=stored.saved_at,
    )


@router.post(
    "/drafts/{operation}",
    response_model=SandboxDraftRead,
)
async def save_sandbox_draft(
    operation: RpaOperation,
    payload: SandboxDraftWrite,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
) -> SandboxDraftRead:
    _require_enabled()
    stored = await sandbox_draft_store.save(operation, idempotency_key, payload)
    return _to_read(stored)


@router.get(
    "/drafts/{operation}/{business_reference}",
    response_model=SandboxDraftRead,
)
async def get_sandbox_draft(
    operation: RpaOperation,
    business_reference: str,
) -> SandboxDraftRead:
    _require_enabled()
    stored = await sandbox_draft_store.get(operation, business_reference)
    if stored is None:
        raise HTTPException(status_code=404, detail="Sandbox 草稿不存在")
    return _to_read(stored)


def _page_shell(
    *,
    title: str,
    subtitle: str,
    fingerprint: str,
    body: str,
    script: str,
) -> str:
    """生成不依赖前端构建工具的稳定练习页面。"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="rpa-page-fingerprint" content="{fingerprint}">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #07101f; color: #e6edf7; }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 32px; }}
    .hero {{ display:flex; justify-content:space-between; gap:24px; align-items:end;
      padding:28px; border:1px solid #24324a; border-radius:20px;
      background:linear-gradient(135deg,#101d32,#0a1425); }}
    .eyebrow {{ color:#62d6c5; letter-spacing:.14em; font-size:12px; font-weight:700; }}
    h1 {{ margin:8px 0; font-size:30px; }}
    .muted {{ color:#91a1ba; line-height:1.7; }}
    .badge {{ padding:8px 12px; border-radius:999px; background:#17334a; color:#8fe9dc; }}
    .login,.panel {{ margin-top:18px; padding:22px; border:1px solid #24324a;
      border-radius:16px; background:#0d1829; }}
    .steps {{ display:grid; grid-template-columns:repeat(7,1fr); gap:8px; margin:20px 0; }}
    .step {{ padding:10px; text-align:center; border-radius:10px; background:#121f33;
      color:#91a1ba; font-size:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }}
    label {{ display:flex; flex-direction:column; gap:7px; color:#a9b7cc; font-size:13px; }}
    input,select,textarea {{ width:100%; border:1px solid #30415e; border-radius:10px;
      background:#07111f; color:#f4f7fb; padding:11px 12px; }}
    textarea {{ min-height:86px; resize:vertical; }}
    button {{ border:0; border-radius:10px; padding:11px 18px; cursor:pointer;
      background:#2cc8b5; color:#05201c; font-weight:800; }}
    button.secondary {{ background:#233550; color:#dce7f6; }}
    button:disabled {{ opacity:.45; cursor:not-allowed; }}
    .actions {{ display:flex; justify-content:flex-end; gap:12px; margin-top:20px; }}
    .result {{ min-height:24px; margin-top:14px; color:#72e4d3; }}
    .warning {{ color:#ffcb6b; }}
    [hidden] {{ display:none !important; }}
    @media(max-width:800px) {{ .grid {{ grid-template-columns:1fr; }}
      .steps {{ grid-template-columns:repeat(2,1fr); }} }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div><div class="eyebrow">TRADEOPS RPA SANDBOX</div><h1>{title}</h1>
      <div class="muted">{subtitle}</div></div>
      <div class="badge">仅保存草稿 / 暂存</div>
    </section>
    {body}
  </main>
  <script>{script}</script>
</body>
</html>"""


_COMMON_SAVE_SCRIPT = """
async function saveDraft(operation) {
  const form = document.querySelector("[data-rpa='business-form']");
  const result = document.querySelector("[data-rpa='save-result']");
  const fields = {};
  for (const element of form.querySelectorAll("[data-field]")) {
    fields[element.dataset.field] = element.type === "checkbox" ? element.checked : element.value;
  }
  result.dataset.status = "saving";
  result.textContent = "正在保存 Sandbox 草稿…";
  const response = await fetch(`/api/v1/rpa-sandbox/drafts/${operation}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": document.querySelector("[data-rpa='idempotency-key']").value
    },
    body: JSON.stringify({
      business_reference: document.querySelector("[data-rpa='business-reference']").value,
      fields
    })
  });
  const data = await response.json();
  if (!response.ok) {
    result.dataset.status = "error";
    result.textContent = data.detail || "保存失败";
    return;
  }
  result.dataset.status = "saved";
  result.dataset.externalReference = data.external_reference;
  result.textContent = `已保存：${data.external_reference}`;
}
"""


@router.get("/sites/link", response_class=HTMLResponse)
async def link_sandbox_site() -> str:
    _require_enabled()
    body = """
<section class="login" data-rpa="login-panel">
  <strong>LINK Sandbox 登录</strong>
  <p class="muted">演示站不需要真实账号。Worker 只验证登录态，不保存账号密码。</p>
  <button type="button" data-rpa="login-button">进入订舱工作区</button>
</section>
<section class="panel" data-rpa="workspace" hidden>
  <div class="steps">
    <div class="step">1 登录</div><div class="step">2 Destination</div>
    <div class="step">3 Package</div><div class="step">4 Purchase Order</div>
    <div class="step">5 Participants</div><div class="step">6 Attachment</div>
    <div class="step">7 Recap / SAVE</div>
  </div>
  <form data-rpa="business-form">
    <input type="hidden" data-rpa="idempotency-key">
    <input type="hidden" data-rpa="business-reference">
    <div class="grid">
      <label>申请编号<input data-field="application_number"></label>
      <label>起运港<input data-field="origin_port"></label>
      <label>目的港<input data-field="destination_port"></label>
      <label>运输方式<input data-field="transport_mode"></label>
      <label>箱型<input data-field="container_type"></label>
      <label>预计离港日<input type="date" data-field="requested_etd"></label>
      <label>发票号<input data-field="invoice_number"></label>
      <label>托盘数<input type="number" data-field="total_pallets"></label>
      <label>箱数<input type="number" data-field="total_cartons"></label>
      <label>件数<input type="number" data-field="total_quantity"></label>
      <label>体积 CBM<input type="number" step="0.001" data-field="total_cbm"></label>
      <label>毛重 KG<input type="number" step="0.001" data-field="total_gross_weight_kg"></label>
      <label>净重 KG<input type="number" step="0.001" data-field="total_net_weight_kg"></label>
      <label>发货人<textarea data-field="shipper"></textarea></label>
      <label>收货人<textarea data-field="consignee"></textarea></label>
      <label>通知人<textarea data-field="notify_party"></textarea></label>
      <label>唛头<textarea data-field="marks"></textarea></label>
    </div>
    <div class="actions">
      <button type="button" data-rpa="save-draft">SAVE DRAFT</button>
    </div>
    <div class="result" data-rpa="save-result"></div>
  </form>
</section>
"""
    script = (
        _COMMON_SAVE_SCRIPT
        + """
document.querySelector("[data-rpa='login-button']").addEventListener("click", () => {
  document.querySelector("[data-rpa='login-panel']").hidden = true;
  document.querySelector("[data-rpa='workspace']").hidden = false;
  document.body.dataset.loginState = "authenticated";
});
document.querySelector("[data-rpa='save-draft']").addEventListener("click", () =>
  saveDraft("LINK_BOOKING_STAGE")
);
"""
    )
    return _page_shell(
        title="LINK 七步订舱模拟站",
        subtitle="用于验证自动登录、字段映射、草稿保存、幂等和结果对账。",
        fingerprint=LINK_SANDBOX_FINGERPRINT,
        body=body,
        script=script,
    )


@router.get("/sites/single-window", response_class=HTMLResponse)
async def single_window_sandbox_site(
    operation: Annotated[
        RpaOperation,
        Query(),
    ] = RpaOperation.COO_STAGE,
    ukey: Annotated[str, Query(pattern="^(present|missing)$")] = "missing",
) -> str:
    _require_enabled()
    if operation not in {RpaOperation.COO_STAGE, RpaOperation.CIQ_STAGE}:
        raise HTTPException(status_code=422, detail="单一窗口只支持 COO_STAGE 或 CIQ_STAGE")
    operation_label = "产地证" if operation is RpaOperation.COO_STAGE else "商检"
    body = f"""
<section class="login" data-rpa="card-login-panel" data-ukey-state="{ukey}">
  <strong>单一窗口卡介质登录</strong>
  <p class="muted">Sandbox 只模拟“卡是否就绪”。真实 UKey、控件和口令永远留在 Windows 申报机。</p>
  <label><input type="checkbox" data-rpa="ukey-ready" {'checked' if ukey == 'present' else ''} disabled>
    UKey 与客户端控件已就绪</label>
  <div class="actions"><button type="button" data-rpa="card-login-button"
    {'disabled' if ukey != 'present' else ''}>卡介质登录</button></div>
  <div class="result warning" data-rpa="card-message"></div>
</section>
<section class="panel" data-rpa="workspace" hidden>
  <h2>{operation_label}暂存工作区</h2>
  <form data-rpa="business-form">
    <input type="hidden" data-rpa="idempotency-key">
    <input type="hidden" data-rpa="business-reference">
    <div class="grid">
      <label>订单号<input data-field="order_number"></label>
      <label>目的国<input data-field="destination_country_code"></label>
      <label>收发货人<input data-field="party_name"></label>
      <label>发票号<input data-field="invoice_number"></label>
      <label>发票日期<input type="date" data-field="invoice_date"></label>
      <label>价格条款<input data-field="price_terms"></label>
      <label>币制<input data-field="currency"></label>
      <label>运输方式<input data-field="transport_mode"></label>
      <label>运输工具<input data-field="transport_name"></label>
      <label>包装件数<input type="number" data-field="package_count"></label>
      <label>毛重 KG<input type="number" step="0.001" data-field="gross_weight_kg"></label>
      <label>净重 KG<input type="number" step="0.001" data-field="net_weight_kg"></label>
      <label>来源版式<input data-field="source_profile"></label>
      <label>网页流程<input data-field="flow_profile"></label>
    </div>
    <div class="actions">
      <button type="button" data-rpa="stage-draft">暂存（不申报）</button>
    </div>
    <div class="result" data-rpa="save-result"></div>
  </form>
</section>
"""
    script = (
        _COMMON_SAVE_SCRIPT
        + f"""
document.querySelector("[data-rpa='card-login-button']").addEventListener("click", () => {{
  if (!document.querySelector("[data-rpa='ukey-ready']").checked) {{
    document.querySelector("[data-rpa='card-message']").textContent = "请插入 UKey 并完成客户端登录。";
    return;
  }}
  document.querySelector("[data-rpa='card-login-panel']").hidden = true;
  document.querySelector("[data-rpa='workspace']").hidden = false;
  document.body.dataset.loginState = "authenticated";
}});
document.querySelector("[data-rpa='stage-draft']").addEventListener("click", () =>
  saveDraft("{operation.value}")
);
"""
    )
    return _page_shell(
        title=f"单一窗口 {operation_label}模拟站",
        subtitle="模拟 Windows UKey 登录、历史草稿复用后的字段补录与最终暂存。",
        fingerprint=SINGLE_WINDOW_SANDBOX_FINGERPRINT,
        body=body,
        script=script,
    )
