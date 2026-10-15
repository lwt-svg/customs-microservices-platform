"""模拟承运人服务的运行期配置。

所有默认值仅面向本机学习环境。回调 Token 只从环境变量读取，代码、日志和响应都不能
输出它的内容。
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中管理端口、独立 schema 和本地构造回调参数。"""

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "link-carrier-simulator"
    environment: str = "local"
    api_prefix: str = "/api/v1"
    carrier_database_url: str = (
        "mysql+asyncmy://lab_user:local-only@127.0.0.1:3306/link_carrier_simulator"
    )
    carrier_callback_url: str = "http://127.0.0.1:8000/api/v1/carrier-callbacks/link"
    carrier_callback_token: SecretStr = Field(default=SecretStr("LOCAL-DEMO-CALLBACK-TOKEN"))
    carrier_callback_timeout_seconds: float = Field(default=5, gt=0, le=30)
    carrier_callback_max_attempts: int = Field(default=3, ge=1, le=10)
    carrier_callback_retry_base_seconds: int = Field(default=10, ge=1, le=3600)
    # 正式本地联调默认开启；测试通过环境变量显式关闭，避免 TestClient 访问 MySQL。
    carrier_callback_retry_worker_enabled: bool = True
    carrier_callback_retry_poll_seconds: float = Field(default=2, gt=0, le=60)
    # SENDING 进程崩溃后，下一轮可回收过期任务，防止永久卡死。
    carrier_callback_lease_seconds: int = Field(default=15, ge=1, le=300)


settings = Settings()
