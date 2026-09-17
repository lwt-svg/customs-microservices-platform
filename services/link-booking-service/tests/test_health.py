"""验证服务不依赖外部系统也能启动的最小健康检查测试。"""

from fastapi.testclient import TestClient

from app.main import app


def test_should_report_alive_when_process_is_running() -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
