"""不连接数据库即可验证服务启动入口。"""

from fastapi.testclient import TestClient

from app.main import app


def test_should_report_simulator_alive() -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json()["service"] == "link-carrier-simulator"
