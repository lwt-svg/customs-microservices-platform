"""本地 RPA Sandbox 页面与草稿 API 测试。

这些测试不启动浏览器，只锁定模拟外站的安全边界和 DOM 合同。Playwright Worker 的真实
浏览器闭环由独立 E2E 脚本覆盖，避免普通单元测试依赖本机 Chromium。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api.rpa_sandbox import (
    LINK_SANDBOX_FINGERPRINT,
    SINGLE_WINDOW_SANDBOX_FINGERPRINT,
    sandbox_draft_store,
)


@pytest.mark.asyncio
async def test_link_sandbox_exposes_versioned_semantic_dom_without_submit(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/rpa-sandbox/sites/link")

    assert response.status_code == 200
    assert LINK_SANDBOX_FINGERPRINT in response.text
    assert "data-rpa=\"login-button\"" in response.text
    assert "data-rpa=\"save-draft\"" in response.text
    assert "data-field=\"application_number\"" in response.text
    assert "SUBMIT_BOOKING" not in response.text
    assert "正式提交" not in response.text


@pytest.mark.asyncio
async def test_single_window_sandbox_requires_explicit_ukey_state(
    client: AsyncClient,
) -> None:
    missing = await client.get(
        "/api/v1/rpa-sandbox/sites/single-window",
        params={"operation": "COO_STAGE", "ukey": "missing"},
    )
    ready = await client.get(
        "/api/v1/rpa-sandbox/sites/single-window",
        params={"operation": "CIQ_STAGE", "ukey": "present"},
    )

    assert missing.status_code == 200
    assert SINGLE_WINDOW_SANDBOX_FINGERPRINT in missing.text
    assert 'data-ukey-state="missing"' in missing.text
    assert "card-login-button\"\n    disabled" in missing.text
    assert ready.status_code == 200
    assert 'data-ukey-state="present"' in ready.text
    assert "暂存（不申报）" in ready.text
    assert "正式申报" not in ready.text


@pytest.mark.asyncio
async def test_sandbox_draft_save_is_idempotent_and_queryable(
    client: AsyncClient,
) -> None:
    await sandbox_draft_store.clear()
    body = {
        "business_reference": "DEMO-LINK-SANDBOX-001",
        "fields": {
            "application_number": "DEMO-LINK-SANDBOX-001",
            "origin_port": "CNSHA",
            "destination_port": "FRLEH",
        },
    }
    headers = {"Idempotency-Key": "RPA-SANDBOX-IDEMPOTENCY-001"}
    first = await client.post(
        "/api/v1/rpa-sandbox/drafts/LINK_BOOKING_STAGE",
        headers=headers,
        json=body,
    )
    replayed = await client.post(
        "/api/v1/rpa-sandbox/drafts/LINK_BOOKING_STAGE",
        headers=headers,
        json=body,
    )
    queried = await client.get(
        "/api/v1/rpa-sandbox/drafts/LINK_BOOKING_STAGE/DEMO-LINK-SANDBOX-001"
    )

    assert first.status_code == 200, first.text
    assert replayed.status_code == 200, replayed.text
    assert queried.status_code == 200, queried.text
    assert replayed.json()["external_reference"] == first.json()["external_reference"]
    assert queried.json()["payload_hash"] == first.json()["payload_hash"]

    conflict = await client.post(
        "/api/v1/rpa-sandbox/drafts/LINK_BOOKING_STAGE",
        headers=headers,
        json={
            **body,
            "fields": {
                **body["fields"],
                "destination_port": "USLAX",
            },
        },
    )
    assert conflict.status_code == 409
