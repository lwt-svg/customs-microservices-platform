"""本地学习服务的配置定义。

这里只从环境变量读取配置，避免把密码和个人密钥写进源码；同一份代码也能在 macOS
和 Windows 上运行。
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """仅用于本地开发的安全默认配置，不指向任何企业环境。"""

    # 支持从服务目录或仓库根目录启动；环境变量本身优先级最高。
    model_config = SettingsConfigDict(
        env_file=(".env.local", "../../.env.local"),
        extra="ignore",
    )

    app_name: str = "link-booking-service"
    environment: str = "local"
    api_prefix: str = "/api/v1"
    # 此 URL 只应指向本机的 LINK 独立 schema，不能填写企业数据库地址。
    link_database_url: str = "mysql+asyncmy://lab_user:local-only@127.0.0.1:3306/link_booking_service"
    # 上传文件只保存在 Git 忽略的本地运行目录。原文件名不会被当作磁盘路径使用。
    booking_upload_dir: str = "uploads/link-booking"
    # Java BFF 与 Python 都应执行相同的 5 MiB 限制；Python 仍按实际读取字节数复核。
    booking_upload_max_bytes: int = 5 * 1024 * 1024

    # --- 第 22 章：仅连接本机独立模拟承运人，不允许填写真实承运人地址 ---
    carrier_simulator_url: str = "http://127.0.0.1:8100"
    carrier_dispatch_timeout_seconds: float = Field(default=5, gt=0, le=30)
    carrier_dispatch_max_attempts: int = Field(default=3, ge=1, le=10)
    carrier_dispatch_retry_base_seconds: int = Field(default=10, ge=1, le=3600)
    carrier_dispatch_lease_seconds: int = Field(default=30, ge=5, le=300)
    # core 模式只保存 PENDING 可靠投递任务，不要求 8100 必须在线；full 模式显式开启后，
    # 提交会立即尝试一次，后台 worker 也会继续领取此前积压的 PENDING。
    carrier_dispatch_enabled: bool = False
    # pytest 默认关闭后台任务；本地联调由环境变量显式开启。
    carrier_dispatch_retry_enabled: bool = False
    carrier_dispatch_retry_interval_seconds: float = Field(default=5, ge=1, le=300)
    # Token 只参与常量时间比较，不写入日志、响应或数据库。
    carrier_callback_token: SecretStr = Field(default=SecretStr("LOCAL-DEMO-CALLBACK-TOKEN"))

    # --- Nacos 服务注册：13.3 学习阶段新增 ---
    # 服务名不是 Python 文件名，而是 Nacos 中给其他服务查询使用的稳定名称。
    nacos_service_name: str = "link-booking-service"
    # 这里与 Java BFF 使用同一个本地分组，便于稍后按“服务名 + 分组”发现服务。
    nacos_group: str = "CUSTOMS_AFFAIRS_LOCAL"
    # Nacos 将实例归入集群。Java 客户端默认注册到 DEFAULT；Python 也显式保持一致。
    nacos_cluster_name: str = "DEFAULT"
    # Nacos 客户端需要“主机:端口”，而不是浏览器使用的 http:// URL。
    # 字段加上 discovery，避免误用旧的 NACOS_SERVER_ADDR（其值可能是控制台 URL）。
    nacos_discovery_server_addr: str = "127.0.0.1:8848"
    # 当前 Java BFF 和 Python 都在同一台 Mac 上运行，因此先注册回环地址。
    # 将来服务放进 Docker 或部署到服务器时，再用环境变量改成可被调用方访问的地址。
    nacos_register_ip: str = "127.0.0.1"
    nacos_register_port: int = 8000
    # HTTP 临时实例必须主动发送心跳。间隔过短会造成无意义请求，过长则会延迟发现故障；
    # 本地学习环境使用 5 秒，生产环境应按 Nacos 服务端与运维规范统一设置。
    nacos_heartbeat_interval_seconds: float = 5.0
    # 注册成功后，如果心跳因 token 过期、Nacos 重启或短暂网络故障而失败，客户端会立刻
    # 尝试重新登录和注册；连续失败时按此初始值开始指数退避，避免高频打满 Nacos。
    nacos_recovery_initial_delay_seconds: float = 1.0
    nacos_recovery_max_delay_seconds: float = 30.0
    # 是否在 FastAPI 启动时向 Nacos 注册。默认关闭，使 pytest 不依赖 Docker 或网络；
    # 13.3 的本机运行由 .env.local 中的 NACOS_DISCOVERY_ENABLED=true 显式开启。
    nacos_discovery_enabled: bool = False
    # 用户名和密码只能由本机环境变量提供。默认空字符串只是避免源码中出现凭据。
    nacos_username: str = ""
    nacos_password: str = ""
    # 13.3 先使用 Nacos 的 public 命名空间，确保与已注册的 platform-bff 一致。
    # 注意：如果将来需要指定命名空间，应填写“命名空间 ID”，而不是显示名称。
    # 字段特意叫 nacos_namespace_id，因此不会误把旧的 NACOS_NAMESPACE 名称当作 ID 使用。
    nacos_namespace_id: str = ""
    # SDK 运行期缓存和日志放在 Git 已忽略的目录，不能混入源码、测试输出或提交记录。
    nacos_cache_dir: str = "runtime/nacos-cache"
    nacos_log_dir: str = "runtime/nacos-logs"


settings = Settings()
