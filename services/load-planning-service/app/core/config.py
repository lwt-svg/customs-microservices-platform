"""智能装载服务的运行配置。

数据库连接串只能来自本机环境变量或安全默认值。源码、日志和 API 响应都不应暴露
数据库密码，也不能把企业原始文件路径当作公开下载地址。LLM 解读功能默认关闭；
即使显式开启，也只能接收服务端构造的脱敏数值摘要，不能读取原始业务文件。
"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """服务配置；默认值仅面向本地学习环境。"""

    # 支持从当前服务目录或仓库根目录启动。环境变量优先级高于 env 文件。
    model_config = SettingsConfigDict(
        env_file=(".env.local", "../../.env.local"),
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "load-planning-service"
    environment: str = "local"
    api_prefix: str = "/api/v1"
    # 使用独立 schema，禁止与 LINK 或 Java 平台表混在一起。
    load_planning_database_url: str = (
        "mysql+asyncmy://lab_user:local-only@127.0.0.1:3306/load_planning_service"
    )
    # Shipment Case 直接验证 LINK 资源，不能绕回 Java BFF 后再调用 Python。
    # 本机进程使用 127.0.0.1；Docker/K8s 只需替换这个地址，领域 Service 不感知
    # Nacos、容器 DNS 或 Service DNS 的具体实现。
    link_booking_internal_base_url: str = "http://127.0.0.1:8000"
    # 外部资源验证发生在数据库写事务之外，但仍必须设置短超时，避免一个请求无限等待。
    shipment_resource_verify_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    # 预览接口只在内存中读取这段上限内的字节，不把原 PDF 保存到 runtime 或数据库。
    packing_list_preview_max_bytes: int = 5 * 1024 * 1024

    # --- LangGraph 工作流编排 ---
    # 默认开启第一版“无 LLM”编排。Graph 只保存 run_id、状态和版本等游标，
    # AgentRun/PackingPlan/LoadPlanVersion 仍以业务数据库为唯一事实来源。
    load_planning_langgraph_enabled: bool = True
    # SQLite checkpointer 是嵌入 Python 进程的本地文件，不是需要额外启动的服务。
    # 一键启动脚本会覆盖为仓库 runtime/checkpoints 下的绝对路径；这里的相对默认值
    # 兼容从 services/load-planning-service 目录直接启动 uvicorn 的学习方式。
    load_planning_langgraph_checkpoint_path: str = (
        "../../runtime/checkpoints/load-planning-langgraph.sqlite3"
    )

    # --- Shipment Agent 自动唤醒 ---
    # Worker 随当前 FastAPI 进程的 lifespan 启停，不增加 Redis、Celery 或第二条启动命令。
    # 所有值都有安全默认值；已有 .env.local 不补这些变量也可以直接升级运行。
    shipment_agent_wake_worker_enabled: bool = True
    shipment_agent_wake_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    shipment_agent_wake_lease_seconds: float = Field(default=30.0, gt=5, le=300)
    shipment_agent_wake_batch_size: int = Field(default=20, ge=1, le=200)
    shipment_agent_wake_wait_seconds: float = Field(default=5.0, gt=0, le=300)
    shipment_agent_wake_resource_binding_seconds: float = Field(
        default=10.0,
        gt=0,
        le=600,
    )
    shipment_agent_wake_recovery_grace_seconds: float = Field(
        # RUNNING 每次落库时都会显式设置这段恢复窗口；正常 Graph 一般会在窗口内走到
        # 等待点并重算计划，只有进程中途退出时才由 Worker 接管恢复。
        default=120.0,
        gt=0,
        le=600,
    )
    shipment_agent_wake_retry_base_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60,
    )
    shipment_agent_wake_retry_max_seconds: float = Field(
        default=60.0,
        gt=0,
        le=3_600,
    )
    # 新建 Shipment Run 默认使用 V3：先由 RPA 自动填写并保存 LINK 草稿，再进入原有
    # 人工提交与承运人异步确认。测试可以显式关闭，以验证历史 V2 行为仍然兼容。
    shipment_agent_rpa_enabled: bool = True
    shipment_agent_rpa_environment: Literal["SANDBOX", "PRODUCTION"] = "SANDBOX"
    shipment_agent_rpa_adapter_key: str = "link-playwright"
    shipment_agent_rpa_selector_pack_version: str = "link-sandbox-v1"

    # --- 统一 RPA Job 控制面 ---
    # Worker 租期和恢复批次由服务端决定，浏览器 Worker 不能自行申请任意长租约。
    rpa_worker_lease_seconds: int = Field(default=120, ge=15, le=3_600)
    # 租约恢复器与 FastAPI 使用同一个进程，不新增手工启动命令。关闭它只适用于测试或
    # 明确由外部调度器接管恢复的部署环境。
    rpa_lease_recovery_worker_enabled: bool = True
    rpa_lease_recovery_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    rpa_lease_recovery_batch_size: int = Field(default=100, ge=1, le=1_000)
    # 本学习项目默认只能派发 Sandbox。即使显式开启 Production，Service 仍会拒绝
    # SYNTHETIC_DEMO 载荷，避免构造数据误触真实网站。
    rpa_production_dispatch_enabled: bool = False
    # 本地模拟网页与企业网站完全隔离。生产部署应关闭；关闭后相关页面和草稿 API 返回 404。
    rpa_sandbox_portal_enabled: bool = True

    # --- 候选方案解读 Copilot ---
    # 默认关闭，避免开发者只启动 Python 服务就意外产生外部模型调用或费用。它不是
    # LangGraph 的决策节点，也不会写数据库；开启后只为已经生成的候选方案补充说明文字。
    load_planning_plan_insights_enabled: bool = False
    # rule_based 不联网且完全可复现；codex_cli 复用本机 Codex CLI 登录状态；
    # openai_responses 通过 Responses API 调用显式配置的模型。
    load_planning_plan_insights_provider: Literal[
        "rule_based",
        "codex_cli",
        "openai_responses",
    ] = "rule_based"
    # Responses API 使用明确模型；Codex CLI 模型集合可能不同，因此默认不强行覆盖
    # 本机 Codex 的默认模型。需要固定 CLI 模型时再单独设置 LOAD_PLANNING_CODEX_MODEL。
    load_planning_llm_model: str = "gpt-6-luna"
    load_planning_codex_model: str | None = None
    # 本机 Codex 首次建立连接通常比普通 HTTP 请求慢，90 秒仍是有界等待；超时后会
    # 自动降级为规则说明，不阻塞装载方案本身的查看、审批和发布。
    load_planning_llm_timeout_seconds: float = Field(default=90.0, gt=0, le=300)
    # SecretStr 的 repr 会自动遮蔽值；业务代码也不会把它写入日志、响应或数据库。
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    # --- 本地 Nacos 服务发现 ---
    # 三个服务身份字段使用 LOAD_PLANNING_ 前缀，避免共享 .env.local 中 LINK 的
    # NACOS_REGISTER_PORT=8000 把本服务错误注册到 8000 端口。
    nacos_service_name: str = Field(
        default="load-planning-service",
        validation_alias="LOAD_PLANNING_NACOS_SERVICE_NAME",
    )
    nacos_group: str = "CUSTOMS_AFFAIRS_LOCAL"
    nacos_cluster_name: str = "DEFAULT"
    nacos_discovery_server_addr: str = "127.0.0.1:8848"
    nacos_register_ip: str = Field(
        default="127.0.0.1",
        validation_alias="LOAD_PLANNING_NACOS_REGISTER_IP",
    )
    nacos_register_port: int = Field(
        default=8200,
        validation_alias="LOAD_PLANNING_NACOS_REGISTER_PORT",
    )
    nacos_heartbeat_interval_seconds: float = 5.0
    nacos_recovery_initial_delay_seconds: float = 1.0
    nacos_recovery_max_delay_seconds: float = 30.0
    # 默认关闭，使 pytest 和单独阅读代码时不依赖 Docker。full 启动再显式开启。
    nacos_discovery_enabled: bool = Field(
        default=False,
        validation_alias="LOAD_PLANNING_NACOS_DISCOVERY_ENABLED",
    )
    nacos_username: str = ""
    nacos_password: str = ""


settings = Settings()
