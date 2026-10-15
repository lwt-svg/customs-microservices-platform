"""FastAPI 生命周期后台重试 worker 的行为测试。"""

import asyncio
from threading import Event

from fastapi.testclient import TestClient

import app.main as main_module


def test_should_start_and_cancel_retry_worker_with_fastapi_lifespan(
    monkeypatch,
) -> None:
    """worker 由本地配置开启时，应用关闭必须取消并等待该任务。"""

    started = Event()
    cancelled = Event()

    async def fake_worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(main_module.settings, "carrier_callback_retry_worker_enabled", True)
    monkeypatch.setattr(main_module, "_callback_retry_worker", fake_worker)

    with TestClient(main_module.app):
        assert started.wait(timeout=1)

    assert cancelled.wait(timeout=1)
