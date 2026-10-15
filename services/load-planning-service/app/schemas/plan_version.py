"""正式装载方案版本的 HTTP 输入输出契约。"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.plan_version import LoadPlanVersionStatus


class LoadPlanLockRequest(BaseModel):
    """把一条已批准运行锁定为任务的下一个正式版本。

    请求体故意不包含 actor。当前演示身份由服务器端常量写入；接入真实认证后应从可信
    JWT/SSO 上下文取得，不能允许浏览器自称“某某已审批/锁定”。
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    expected_run_version: int = Field(ge=1)
    expected_current_version_number: int = Field(
        ge=0,
        description="调用方看到的任务当前正式版本号；任务尚无正式版本时传 0",
    )
    lock_reason: str = Field(min_length=1, max_length=2_000)


class LoadPlanVersionEventRead(BaseModel):
    """一条按 sequence 追加的正式版本审计事件。"""

    model_config = ConfigDict(from_attributes=True)

    sequence: int
    event_type: str
    actor_subject: str
    actor_display_name: str
    actor_source: str
    request_id: str
    detail: str
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def mark_database_timestamp_as_utc(cls, value: datetime) -> datetime:
        """当前 MySQL DATETIME 无时区；API 统一补成 UTC，避免浏览器误读。"""

        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class LoadPlanVersionRead(BaseModel):
    """一个已锁定正式版本的完整不可变视图。

    list/current/detail/lock 使用同一响应结构，方便 BFF 和前端只维护一个 DTO。当前数据量
    适合教程闭环；生产中若方案坐标很大，可在不改变 detail 的前提下另增轻量 summary。
    """

    version_id: str
    status: LoadPlanVersionStatus
    task_number: str
    version_number: int
    source_run_id: str
    source_plan_id: str
    plan_code: str
    selected_pallet_code: str
    supersedes_version_id: str | None
    source_run_version: int
    snapshot_schema_version: str
    input_snapshot_hash: str
    constraint_snapshot_hash: str
    plan_snapshot_hash: str
    algorithm_version: str
    score_version: str
    approval_actor_subject: str
    approval_actor_display_name: str
    approval_actor_source: str
    approved_at: datetime
    approval_comment: str | None
    locked_by_subject: str
    locked_by_display_name: str
    locked_by_source: str
    lock_reason: str
    locked_at: datetime
    input_snapshot: dict[str, object]
    constraint_snapshot: dict[str, object]
    plan_snapshot: dict[str, object]
    events: list[LoadPlanVersionEventRead]

    @field_validator("approved_at", "locked_at")
    @classmethod
    def mark_database_timestamp_as_utc(cls, value: datetime) -> datetime:
        """数据库时间统一以带 ``Z`` 的 UTC 返回。"""

        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
