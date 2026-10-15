"""Shipment Case HTTP API 的首个业务闭环契约测试。

本文件只通过公开 REST API 验证行为，不直接调用 Repository 或 Service。这样测试描述的
是前端、Java BFF 和未来 Shipment Orchestrator 可以依赖的稳定契约，而不是某一种内部
实现方式。

测试数据全部使用 ``SHP-DEMO-*``、``LP-DEMO-*`` 等构造编号，不包含企业真实数据。
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.shipment_cases import get_shipment_resource_verifier
from app.db.base import Base
from app.integrations.shipment_resources import (
    ShipmentResourceNotFoundError,
    VerifiedShipmentResource,
)
from app.main import app
from app.models.shipment_case import (
    ShipmentCase,
    ShipmentCaseCommand,
    ShipmentCaseCommandResult,
    ShipmentCaseCommandType,
    ShipmentResourceType,
)
from app.schemas.shipment_case import ShipmentCaseCreate, ShipmentCaseResourceBind
from app.services.shipment_cases import (
    ShipmentCaseConflictError,
    ShipmentCaseService,
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def build_case_payload(
    case_number: str = "SHP-DEMO-API-001",
    *,
    case_name: str = "构造海运整柜案件",
) -> dict[str, object]:
    """构造创建案件的最小可信输入。

    ``responsible_organization_code`` 和 actor 故意不在请求中：在真实系统中它们应来自
    已认证的 Java BFF 上下文，当前学习阶段则由 Python 服务端注入安全演示身份。
    """

    return {
        "case_number": case_number,
        "case_name": case_name,
        "transport_scenario": "SEA_FCL",
        "description": "验证 Shipment Case、资源绑定、状态机、幂等和审计时间线。",
    }


def build_load_planning_task_payload(task_number: str) -> dict[str, object]:
    """创建一条真实落库的装载任务，供案件资源验证器按业务编号查询。"""

    return {
        "task_number": task_number,
        "task_name": "案件绑定测试装载任务",
        "cargo_items": [
            {
                "item_code": "ITEM-CASE-001",
                "sku_code": "SKU-CASE-001",
                "cargo_name": "构造测试箱",
                "quantity": 8,
                "length_mm": 300,
                "width_mm": 200,
                "height_mm": 180,
                "gross_weight_kg": 8,
                "stackable": True,
                "max_stack_layers": 4,
                "keep_upright": True,
                "fragile": False,
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "SHIPMENT_CASE_TEST_FIXTURE",
                "verified": False,
            }
        ],
        "pallet_specs": [
            {
                "pallet_code": "PALLET-CASE-001",
                "pallet_name": "构造测试托盘",
                "quantity": 2,
                "length_mm": 1200,
                "width_mm": 1000,
                "height_mm": 150,
                "tare_weight_kg": 25,
                "max_load_kg": 500,
                "max_total_height_mm": 1250,
                "source_type": "SYNTHETIC_DEMO",
                "source_reference": "SHIPMENT_CASE_TEST_FIXTURE",
                "verified": False,
            }
        ],
    }


def idempotency_headers(key: str) -> dict[str, str]:
    """所有写命令都必须携带调用方生成、可安全重放的幂等键。"""

    return {"Idempotency-Key": key}


async def create_case(
    client: AsyncClient,
    case_number: str,
    *,
    key: str | None = None,
) -> dict[str, object]:
    """创建案件并在辅助函数内先验证公共成功契约。"""

    response = await client.post(
        "/api/v1/shipment-cases",
        json=build_case_payload(case_number),
        headers=idempotency_headers(key or f"create-{case_number}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


@dataclass(slots=True)
class FakeShipmentResourceVerifier:
    """API 测试使用的可控资源边界。

    LINK 的 HTTP 错误映射由另一个测试文件使用 ``httpx.MockTransport`` 单独验证。这里
    只关心：验证成功后才能写案件；验证失败时案件事务不能留下半条绑定或半条事件。
    """

    resources: dict[tuple[str, str], VerifiedShipmentResource] = field(default_factory=dict)
    failures: dict[tuple[str, str], Exception] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def add(
        self,
        resource_type: ShipmentResourceType,
        resource_number: str,
        *,
        status: str = "DRAFT",
    ) -> None:
        """登记一个可以被成功验证的构造资源。"""

        key = (resource_type.value, resource_number)
        self.resources[key] = VerifiedShipmentResource(
            resource_type=resource_type,
            resource_number=resource_number,
            resource_status=status,
            verification_source="TEST_FAKE",
            verified_at=datetime.now(UTC),
        )

    async def verify(
        self,
        resource_type: ShipmentResourceType,
        resource_number: str,
    ) -> VerifiedShipmentResource:
        """按 Protocol 返回资源，或抛出和真实适配器一致的领域异常。"""

        key = (str(resource_type), resource_number)
        self.calls.append(key)
        if failure := self.failures.get(key):
            raise failure
        if resource := self.resources.get(key):
            return resource
        raise ShipmentResourceNotFoundError("构造资源不存在")


@dataclass(slots=True)
class StagedConcurrentResourceVerifier:
    """让两个真实 Service 调用都先完成阶段 A，再按可控顺序结束资源验证。

    SQLite 不实现 MySQL 的 ``SELECT ... FOR UPDATE`` 行锁，也不适合稳定制造两个同时
    写事务。因此测试让两个协程真实并发进入验证阶段，再让第一个提交、第二个继续，
    专门验证阶段 C 必须重新读取绑定并为 loser 写入 ``NO_OP`` 命令台账。
    """

    resources: dict[tuple[str, str], VerifiedShipmentResource] = field(
        default_factory=dict
    )
    calls: list[tuple[str, str]] = field(default_factory=list)
    first_arrived: asyncio.Event = field(default_factory=asyncio.Event)
    both_arrived: asyncio.Event = field(default_factory=asyncio.Event)
    release_second: asyncio.Event = field(default_factory=asyncio.Event)

    def add(
        self,
        resource_type: ShipmentResourceType,
        resource_number: str,
    ) -> None:
        self.resources[(resource_type.value, resource_number)] = (
            VerifiedShipmentResource(
                resource_type=resource_type,
                resource_number=resource_number,
                resource_status="DRAFT",
                verification_source="CONCURRENT_TEST_FAKE",
                verified_at=datetime.now(UTC),
            )
        )

    async def verify(
        self,
        resource_type: ShipmentResourceType,
        resource_number: str,
    ) -> VerifiedShipmentResource:
        key = (resource_type.value, resource_number)
        self.calls.append(key)
        call_number = len(self.calls)

        if call_number == 1:
            self.first_arrived.set()
            await self.both_arrived.wait()
        elif call_number == 2:
            self.both_arrived.set()
            await self.release_second.wait()

        if resource := self.resources.get(key):
            return resource
        raise ShipmentResourceNotFoundError("构造资源不存在")


@pytest_asyncio.fixture
async def client_with_fake_resource_verifier(
    client: AsyncClient,
) -> AsyncIterator[tuple[AsyncClient, FakeShipmentResourceVerifier]]:
    """只覆盖跨资源验证边界，数据库和 REST 路由仍使用真实实现。"""

    verifier = FakeShipmentResourceVerifier()
    app.dependency_overrides[get_shipment_resource_verifier] = lambda: verifier
    try:
        yield client, verifier
    finally:
        # ``client`` 夹具还维护着 get_session 覆盖；不能在这里 clear 全部依赖。
        app.dependency_overrides.pop(get_shipment_resource_verifier, None)


async def test_create_list_and_detail_start_with_one_audited_revision(
    client: AsyncClient,
) -> None:
    """创建、列表和详情形成最小闭环，并从 revision/sequence=1 开始。"""

    created = await create_case(client, "SHP-DEMO-CREATE-001")

    assert created["case_number"] == "SHP-DEMO-CREATE-001"
    assert created["status"] == "DRAFT"
    assert created["revision"] == 1
    assert created["responsible_organization_code"] == "DEMO_ORG"
    assert created["resource_bindings"] == []
    assert len(created["events"]) == 1

    event = created["events"][0]
    assert event["sequence"] == 1
    assert event["event_type"] == "CASE_CREATED"
    assert event["previous_status"] is None
    assert event["current_status"] == "DRAFT"
    assert event["actor_subject"] == "SYSTEM_DEMO"
    assert event["actor_display_name"] == "SYSTEM_DEMO"
    assert event["actor_source"] == "SYSTEM_DEMO"

    listed = await client.get("/api/v1/shipment-cases")
    detail = await client.get("/api/v1/shipment-cases/SHP-DEMO-CREATE-001")

    assert listed.status_code == 200
    assert [item["case_number"] for item in listed.json()] == ["SHP-DEMO-CREATE-001"]
    assert listed.json()[0]["revision"] == 1
    assert detail.status_code == 200
    assert detail.json() == created


async def test_create_is_idempotent_and_rejects_reusing_key_for_new_parameters(
    client: AsyncClient,
) -> None:
    """同请求可安全重放；同一个 key 不能被换参数后冒充另一条命令。"""

    path = "/api/v1/shipment-cases"
    headers = idempotency_headers("create-case-idempotency-001")
    payload = build_case_payload("SHP-DEMO-IDEMPOTENT-001")

    first = await client.post(path, json=payload, headers=headers)
    replay = await client.post(path, json=payload, headers=headers)
    changed = await client.post(
        path,
        json={**payload, "case_name": "试图复用同一个 key 改名"},
        headers=headers,
    )
    listed = await client.get("/api/v1/shipment-cases")

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert changed.status_code == 409
    assert len(listed.json()) == 1
    assert len(first.json()["events"]) == 1


@pytest.mark.parametrize(
    ("forged_field", "forged_value"),
    [
        ("actor_subject", "ATTACKER"),
        ("actor_display_name", "伪造审批人"),
        ("actor_source", "UNTRUSTED_BODY"),
        ("responsible_organization_code", "OTHER_ORG"),
    ],
)
async def test_create_rejects_actor_or_organization_injection(
    client: AsyncClient,
    forged_field: str,
    forged_value: str,
) -> None:
    """调用方不能在 JSON 中伪造可信身份或越权指定负责组织。"""

    payload = {
        **build_case_payload(f"SHP-DEMO-INJECTION-{forged_field.upper()}"),
        forged_field: forged_value,
    }
    response = await client.post(
        "/api/v1/shipment-cases",
        json=payload,
        headers=idempotency_headers(f"reject-injection-{forged_field}"),
    )

    assert response.status_code == 422


async def test_every_write_endpoint_requires_idempotency_key(
    client: AsyncClient,
) -> None:
    """缺少幂等键时，命令必须在进入业务写事务前被 FastAPI 拒绝。"""

    missing_on_create = await client.post(
        "/api/v1/shipment-cases",
        json=build_case_payload("SHP-DEMO-NO-KEY-001"),
    )
    assert missing_on_create.status_code == 422

    await create_case(client, "SHP-DEMO-NO-KEY-002")
    missing_on_binding = await client.post(
        "/api/v1/shipment-cases/SHP-DEMO-NO-KEY-002/resources",
        json={
            "resource_type": "LOAD_PLANNING_TASK",
            "resource_number": "LP-DEMO-NO-KEY",
            "expected_revision": 1,
        },
    )
    missing_on_status = await client.post(
        "/api/v1/shipment-cases/SHP-DEMO-NO-KEY-002/status",
        json={
            "target_status": "ACTIVE",
            "expected_revision": 1,
            "reason": "测试缺少幂等键时不能改状态",
        },
    )

    assert missing_on_binding.status_code == 422
    assert missing_on_status.status_code == 422


async def test_bind_existing_load_planning_task_through_real_local_verifier(
    client: AsyncClient,
) -> None:
    """先真实创建装载任务，再由默认本地验证器确认其存在并绑定到案件。"""

    task_number = "LP-DEMO-CASE-BIND-001"
    task = await client.post(
        "/api/v1/load-planning-tasks",
        json=build_load_planning_task_payload(task_number),
    )
    assert task.status_code == 201, task.text

    await create_case(client, "SHP-DEMO-LOCAL-VERIFY-001")
    bound = await client.post(
        "/api/v1/shipment-cases/SHP-DEMO-LOCAL-VERIFY-001/resources",
        json={
            "resource_type": "LOAD_PLANNING_TASK",
            "resource_number": task_number,
            "expected_revision": 1,
        },
        headers=idempotency_headers("bind-real-load-task-001"),
    )

    assert bound.status_code == 200, bound.text
    body = bound.json()
    assert body["revision"] == 2
    assert len(body["resource_bindings"]) == 1
    binding = body["resource_bindings"][0]
    assert binding["resource_type"] == "LOAD_PLANNING_TASK"
    assert binding["resource_number"] == task_number
    assert binding["resource_status_at_verification"] == "DRAFT"
    assert binding["verification_source"] == "LOCAL_LOAD_PLANNING_DATABASE"
    assert body["events"][-1]["event_type"] == "RESOURCE_BOUND"
    assert body["events"][-1]["sequence"] == body["revision"]


async def test_verification_failure_leaves_revision_bindings_and_events_unchanged(
    client_with_fake_resource_verifier: tuple[
        AsyncClient,
        FakeShipmentResourceVerifier,
    ],
) -> None:
    """远端或本地资源验证失败时，案件事务不能写入任何半成品。"""

    client, verifier = client_with_fake_resource_verifier
    case_number = "SHP-DEMO-VERIFY-ROLLBACK-001"
    resource_number = "DEMO-BOOKING-MISSING-001"
    await create_case(client, case_number)
    verifier.failures[("LINK_BOOKING_APPLICATION", resource_number)] = (
        ShipmentResourceNotFoundError("构造 LINK 订舱申请不存在")
    )

    failed = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": resource_number,
            "expected_revision": 1,
        },
        headers=idempotency_headers("bind-missing-link-001"),
    )
    detail = await client.get(f"/api/v1/shipment-cases/{case_number}")

    # 案件本身存在，但请求引用了无效业务资源，因此属于命令校验失败而不是案件 404。
    assert failed.status_code == 422
    assert detail.status_code == 200
    assert detail.json()["revision"] == 1
    assert detail.json()["resource_bindings"] == []
    assert [event["event_type"] for event in detail.json()["events"]] == ["CASE_CREATED"]


async def test_same_resource_cannot_be_bound_to_two_cases(
    client_with_fake_resource_verifier: tuple[
        AsyncClient,
        FakeShipmentResourceVerifier,
    ],
) -> None:
    """一个稳定业务资源只能属于一票案件，避免形成两条互相矛盾的总时间线。"""

    client, verifier = client_with_fake_resource_verifier
    resource_number = "DEMO-BOOKING-EXCLUSIVE-001"
    verifier.add(
        ShipmentResourceType.LINK_BOOKING_APPLICATION,
        resource_number,
    )
    await create_case(client, "SHP-DEMO-OWNER-A")
    await create_case(client, "SHP-DEMO-OWNER-B")

    first = await client.post(
        "/api/v1/shipment-cases/SHP-DEMO-OWNER-A/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": resource_number,
            "expected_revision": 1,
        },
        headers=idempotency_headers("bind-exclusive-a"),
    )
    second = await client.post(
        "/api/v1/shipment-cases/SHP-DEMO-OWNER-B/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": resource_number,
            "expected_revision": 1,
        },
        headers=idempotency_headers("bind-exclusive-b"),
    )
    second_detail = await client.get("/api/v1/shipment-cases/SHP-DEMO-OWNER-B")

    assert first.status_code == 200
    assert second.status_code == 409
    assert second_detail.json()["revision"] == 1
    assert second_detail.json()["resource_bindings"] == []
    assert len(second_detail.json()["events"]) == 1


async def test_repeated_binding_is_domain_idempotent_without_new_revision(
    client_with_fake_resource_verifier: tuple[
        AsyncClient,
        FakeShipmentResourceVerifier,
    ],
) -> None:
    """成功 NO_OP 必须登记 key，但不能多写绑定、事件或 revision。"""

    client, verifier = client_with_fake_resource_verifier
    case_number = "SHP-DEMO-DOMAIN-IDEMPOTENT-001"
    resource_number = "DEMO-BOOKING-DOMAIN-IDEMPOTENT-001"
    another_resource_number = "DEMO-BOOKING-DOMAIN-IDEMPOTENT-002"
    verifier.add(
        ShipmentResourceType.LINK_BOOKING_APPLICATION,
        resource_number,
    )
    verifier.add(
        ShipmentResourceType.LINK_BOOKING_APPLICATION,
        another_resource_number,
    )
    await create_case(client, case_number)
    payload = {
        "resource_type": "LINK_BOOKING_APPLICATION",
        "resource_number": resource_number,
        "expected_revision": 1,
    }

    first = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json=payload,
        headers=idempotency_headers("bind-domain-first"),
    )
    # 页面仍带着绑定前的 expected_revision=1，但资源已经属于同一案件。领域级重复命令
    # 应优先识别成 no-op，而不是误报旧页面冲突。
    repeated = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json=payload,
        headers=idempotency_headers("bind-domain-second"),
    )
    replayed_noop = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json=payload,
        headers=idempotency_headers("bind-domain-second"),
    )
    # 第二个 key 已经得到过成功 NO_OP，不能再换成另一资源。这个回归断言覆盖了原实现
    # “直接返回但不落任何幂等记录”的漏洞。
    changed_payload = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": another_resource_number,
            "expected_revision": 2,
        },
        headers=idempotency_headers("bind-domain-second"),
    )
    detail = await client.get(f"/api/v1/shipment-cases/{case_number}")

    assert first.status_code == repeated.status_code == replayed_noop.status_code == 200
    assert changed_payload.status_code == 409
    assert repeated.json() == first.json()
    assert replayed_noop.json() == repeated.json()
    assert repeated.json()["revision"] == 2
    assert len(repeated.json()["resource_bindings"]) == 1
    assert [event["sequence"] for event in repeated.json()["events"]] == [1, 2]
    assert detail.json()["revision"] == 2
    assert len(detail.json()["resource_bindings"]) == 1
    assert len(detail.json()["events"]) == 2
    assert verifier.calls == [
        ("LINK_BOOKING_APPLICATION", resource_number),
    ]


async def test_concurrent_service_binding_loser_records_noop_command(
    tmp_path: Path,
) -> None:
    """两个并发 Service 调用竞争同一资源时，loser 成功 NO_OP 也必须占用自己的 key。

    文件 SQLite 允许两个独立异步会话真实重叠，但它没有 MySQL 行锁语义。测试用验证器
    屏障确保两个调用都先通过阶段 A，再让第一个提交后释放第二个；这样稳定覆盖阶段 C
    的并发重查与 NO_OP 台账，不伪装成 MySQL 同时写锁测试。
    """

    database_path = tmp_path / "shipment-case-concurrency.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    verifier = StagedConcurrentResourceVerifier()
    resource_number = "DEMO-BOOKING-CONCURRENT-NOOP-001"
    other_resource_number = "DEMO-BOOKING-CONCURRENT-NOOP-002"
    verifier.add(ShipmentResourceType.LINK_BOOKING_APPLICATION, resource_number)
    verifier.add(
        ShipmentResourceType.LINK_BOOKING_APPLICATION,
        other_resource_number,
    )
    service = ShipmentCaseService(session_factory, verifier)
    case_number = "SHP-DEMO-CONCURRENT-NOOP-001"

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        created = await service.create(
            ShipmentCaseCreate.model_validate(build_case_payload(case_number)),
            "concurrent-create-key",
        )
        assert created.revision == 1

        payload = ShipmentCaseResourceBind(
            resource_type=ShipmentResourceType.LINK_BOOKING_APPLICATION,
            resource_number=resource_number,
            expected_revision=1,
        )
        first_task = asyncio.create_task(
            service.bind_resource(case_number, payload, "concurrent-bind-winner")
        )
        await asyncio.wait_for(verifier.first_arrived.wait(), timeout=2)
        second_task = asyncio.create_task(
            service.bind_resource(case_number, payload, "concurrent-bind-loser")
        )
        await asyncio.wait_for(verifier.both_arrived.wait(), timeout=2)

        first = await asyncio.wait_for(first_task, timeout=2)
        verifier.release_second.set()
        second = await asyncio.wait_for(second_task, timeout=2)

        assert first.revision == second.revision == 2
        assert len(second.resource_bindings) == 1
        assert [event.sequence for event in second.events] == [1, 2]

        with pytest.raises(ShipmentCaseConflictError):
            await service.bind_resource(
                case_number,
                ShipmentCaseResourceBind(
                    resource_type=(
                        ShipmentResourceType.LINK_BOOKING_APPLICATION
                    ),
                    resource_number=other_resource_number,
                    expected_revision=2,
                ),
                "concurrent-bind-loser",
            )

        # key 冲突在阶段 A 被命令台账拦住，不应再次调用外部资源验证器。
        assert verifier.calls == [
            ("LINK_BOOKING_APPLICATION", resource_number),
            ("LINK_BOOKING_APPLICATION", resource_number),
        ]

        async with session_factory() as session:
            commands = list(
                (
                    await session.scalars(
                        select(ShipmentCaseCommand).order_by(
                            ShipmentCaseCommand.id
                        )
                    )
                ).all()
            )

        assert [command.command_type for command in commands] == [
            ShipmentCaseCommandType.CREATE_CASE,
            ShipmentCaseCommandType.BIND_RESOURCE,
            ShipmentCaseCommandType.BIND_RESOURCE,
        ]
        assert [command.result_kind for command in commands] == [
            ShipmentCaseCommandResult.APPLIED,
            ShipmentCaseCommandResult.APPLIED,
            ShipmentCaseCommandResult.NO_OP,
        ]
        assert commands[-1].result_revision == 2
        assert commands[-1].result_event_id is None
    finally:
        await engine.dispose()


async def test_binding_idempotency_replay_wins_over_stale_revision(
    client_with_fake_resource_verifier: tuple[
        AsyncClient,
        FakeShipmentResourceVerifier,
    ],
) -> None:
    """网络重试必须先命中已保存的命令结果，再做 expected_revision 检查。"""

    client, verifier = client_with_fake_resource_verifier
    case_number = "SHP-DEMO-REPLAY-001"
    resource_number = "DEMO-BOOKING-REPLAY-001"
    verifier.add(
        ShipmentResourceType.LINK_BOOKING_APPLICATION,
        resource_number,
    )
    await create_case(client, case_number)
    payload = {
        "resource_type": "LINK_BOOKING_APPLICATION",
        "resource_number": resource_number,
        "expected_revision": 1,
    }
    headers = idempotency_headers("bind-replay-same-key")

    first = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json=payload,
        headers=headers,
    )
    replay = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json=payload,
        headers=headers,
    )

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["revision"] == 2


async def test_binding_rejects_reusing_key_for_another_resource(
    client_with_fake_resource_verifier: tuple[
        AsyncClient,
        FakeShipmentResourceVerifier,
    ],
) -> None:
    """相同案件内，幂等键和规范化请求体必须保持一一对应。"""

    client, verifier = client_with_fake_resource_verifier
    case_number = "SHP-DEMO-BIND-KEY-CONFLICT-001"
    first_number = "DEMO-BOOKING-KEY-A"
    second_number = "DEMO-BOOKING-KEY-B"
    verifier.add(ShipmentResourceType.LINK_BOOKING_APPLICATION, first_number)
    verifier.add(ShipmentResourceType.LINK_BOOKING_APPLICATION, second_number)
    await create_case(client, case_number)
    headers = idempotency_headers("bind-key-cannot-change-resource")

    first = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": first_number,
            "expected_revision": 1,
        },
        headers=headers,
    )
    changed = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": second_number,
            "expected_revision": 1,
        },
        headers=headers,
    )

    assert first.status_code == 200
    assert changed.status_code == 409
    detail = await client.get(f"/api/v1/shipment-cases/{case_number}")
    assert detail.json()["revision"] == 2
    assert [binding["resource_number"] for binding in detail.json()["resource_bindings"]] == [
        first_number
    ]


async def test_stale_revision_cannot_append_a_different_command(
    client_with_fake_resource_verifier: tuple[
        AsyncClient,
        FakeShipmentResourceVerifier,
    ],
) -> None:
    """旧页面不能在案件已经变化后继续追加另一条资源绑定。"""

    client, verifier = client_with_fake_resource_verifier
    case_number = "SHP-DEMO-STALE-REVISION-001"
    first_number = "DEMO-BOOKING-STALE-A"
    second_number = "DEMO-BOOKING-STALE-B"
    verifier.add(ShipmentResourceType.LINK_BOOKING_APPLICATION, first_number)
    verifier.add(ShipmentResourceType.LINK_BOOKING_APPLICATION, second_number)
    await create_case(client, case_number)

    first = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": first_number,
            "expected_revision": 1,
        },
        headers=idempotency_headers("bind-before-stale"),
    )
    stale = await client.post(
        f"/api/v1/shipment-cases/{case_number}/resources",
        json={
            "resource_type": "LINK_BOOKING_APPLICATION",
            "resource_number": second_number,
            "expected_revision": 1,
        },
        headers=idempotency_headers("bind-with-stale-revision"),
    )

    assert first.status_code == 200
    assert stale.status_code == 409
    detail = await client.get(f"/api/v1/shipment-cases/{case_number}")
    assert detail.json()["revision"] == 2
    assert len(detail.json()["resource_bindings"]) == 1
    assert len(detail.json()["events"]) == 2


async def test_draft_active_completed_flow_and_terminal_guard(
    client: AsyncClient,
) -> None:
    """正常生命周期每次只前进一格；COMPLETED 后不允许取消或回退。"""

    case_number = "SHP-DEMO-COMPLETE-001"
    await create_case(client, case_number)

    activated = await client.post(
        f"/api/v1/shipment-cases/{case_number}/status",
        json={
            "target_status": "ACTIVE",
            "expected_revision": 1,
            "reason": "资料已完成首轮核对，开始并行作业。",
        },
        headers=idempotency_headers("activate-complete-flow"),
    )
    completed = await client.post(
        f"/api/v1/shipment-cases/{case_number}/status",
        json={
            "target_status": "COMPLETED",
            "expected_revision": 2,
            "reason": "本次演示案件的计划内工作已经完成。",
        },
        headers=idempotency_headers("complete-complete-flow"),
    )
    terminal_change = await client.post(
        f"/api/v1/shipment-cases/{case_number}/status",
        json={
            "target_status": "CANCELLED",
            "expected_revision": 3,
            "reason": "终态后不应接受这次取消。",
        },
        headers=idempotency_headers("cancel-after-completed"),
    )

    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"
    assert activated.json()["revision"] == 2
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["revision"] == 3
    assert terminal_change.status_code == 409

    detail = await client.get(f"/api/v1/shipment-cases/{case_number}")
    body = detail.json()
    assert body["status"] == "COMPLETED"
    assert body["revision"] == 3
    assert [event["sequence"] for event in body["events"]] == [1, 2, 3]
    assert [event["sequence"] for event in body["events"]] == list(range(1, body["revision"] + 1))
    assert [event["event_type"] for event in body["events"]] == [
        "CASE_CREATED",
        "CASE_STATUS_CHANGED",
        "CASE_STATUS_CHANGED",
    ]


async def test_cancelled_is_terminal_and_does_not_accept_reactivation(
    client: AsyncClient,
) -> None:
    """DRAFT 可以直接取消，但 CANCELLED 后不能重新变成 ACTIVE。"""

    case_number = "SHP-DEMO-CANCELLED-001"
    await create_case(client, case_number)

    cancelled = await client.post(
        f"/api/v1/shipment-cases/{case_number}/status",
        json={
            "target_status": "CANCELLED",
            "expected_revision": 1,
            "reason": "演示业务在执行前由人工取消。",
        },
        headers=idempotency_headers("cancel-draft-case"),
    )
    reactivated = await client.post(
        f"/api/v1/shipment-cases/{case_number}/status",
        json={
            "target_status": "ACTIVE",
            "expected_revision": 2,
            "reason": "终态案件不能靠普通状态命令恢复。",
        },
        headers=idempotency_headers("reactivate-cancelled-case"),
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["revision"] == 2
    assert reactivated.status_code == 409

    detail = await client.get(f"/api/v1/shipment-cases/{case_number}")
    assert detail.json()["status"] == "CANCELLED"
    assert detail.json()["revision"] == 2
    assert [event["sequence"] for event in detail.json()["events"]] == [1, 2]


def test_resource_binding_metadata_has_no_foreign_key_to_business_resources() -> None:
    """ORM 绑定表只能外键到案件，不能耦合装载表或 LINK 的独立数据库。"""

    table = Base.metadata.tables["shipment_case_resource_bindings"]
    foreign_keys = {
        (foreign_key.parent.name, foreign_key.target_fullname) for foreign_key in table.foreign_keys
    }

    assert foreign_keys == {("case_id", "shipment_cases.id")}
    assert not any(
        foreign_key.parent.name in {"resource_type", "resource_number"}
        for foreign_key in table.foreign_keys
    )


def test_command_ledger_metadata_is_separate_from_business_events() -> None:
    """命令结果独立保存，业务事件不再被迫承担 HTTP 幂等登记。"""

    command_table = Base.metadata.tables["shipment_case_commands"]
    event_table = Base.metadata.tables["shipment_case_events"]
    case_table = Base.metadata.tables["shipment_cases"]

    assert "idempotency_key_hash" in command_table.c
    assert "request_hash" in command_table.c
    assert "result_kind" in command_table.c
    assert "result_revision" in command_table.c
    assert "result_event_id" in command_table.c
    assert "idempotency_key_hash" not in event_table.c
    assert "request_hash" not in event_table.c
    assert "create_idempotency_key_hash" not in case_table.c
    assert "create_request_hash" not in case_table.c

    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in command_table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("idempotency_key_hash",) in unique_column_sets
    assert ("result_event_id",) in unique_column_sets

    result_event_foreign_key = next(
        foreign_key
        for foreign_key in command_table.foreign_keys
        if foreign_key.parent.name == "result_event_id"
    )
    assert result_event_foreign_key.ondelete == "RESTRICT"
    assert "delete-orphan" not in ShipmentCase.events.property.cascade


def test_alembic_migration_has_the_same_resource_boundary(
    tmp_path: Path,
) -> None:
    """实际执行全部 Alembic 迁移，确认数据库结构没有隐藏的跨资源外键。"""

    database_path = tmp_path / "shipment-case-migration.sqlite3"
    environment = {
        **os.environ,
        "LOAD_PLANNING_DATABASE_URL": (f"sqlite+aiosqlite:///{database_path.as_posix()}"),
        "LOAD_PLANNING_NACOS_DISCOVERY_ENABLED": "false",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        foreign_keys = inspector.get_foreign_keys(
            "shipment_case_resource_bindings"
        )
        command_foreign_keys = inspector.get_foreign_keys(
            "shipment_case_commands"
        )
        command_unique_constraints = inspector.get_unique_constraints(
            "shipment_case_commands"
        )
        event_columns = {
            column["name"]
            for column in inspector.get_columns("shipment_case_events")
        }
        case_columns = {
            column["name"] for column in inspector.get_columns("shipment_cases")
        }
    finally:
        engine.dispose()

    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["case_id"]
    assert foreign_keys[0]["referred_table"] == "shipment_cases"
    assert foreign_keys[0]["referred_columns"] == ["id"]
    assert {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
        )
        for foreign_key in command_foreign_keys
    } == {
        (("case_id",), "shipment_cases"),
        (("result_event_id",), "shipment_case_events"),
    }
    result_event_foreign_key = next(
        foreign_key
        for foreign_key in command_foreign_keys
        if foreign_key["constrained_columns"] == ["result_event_id"]
    )
    assert result_event_foreign_key["options"].get("ondelete") == "RESTRICT"
    assert {
        tuple(constraint["column_names"])
        for constraint in command_unique_constraints
    } >= {
        ("idempotency_key_hash",),
        ("result_event_id",),
    }
    assert "idempotency_key_hash" not in event_columns
    assert "request_hash" not in event_columns
    assert "create_idempotency_key_hash" not in case_columns
    assert "create_request_hash" not in case_columns


def test_command_ledger_migration_backfills_an_already_applied_01_database(
    tmp_path: Path,
) -> None:
    """真实按 01→写旧数据→02 升级，保证现有本机 MySQL 的线性升级路径有测试替身。"""

    database_path = tmp_path / "shipment-case-command-backfill.sqlite3"
    environment = {
        **os.environ,
        "LOAD_PLANNING_DATABASE_URL": (
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        ),
        "LOAD_PLANNING_NACOS_DISCOVERY_ENABLED": "false",
    }

    upgrade_01 = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "20261015_01",
        ],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade_01.returncode == 0, upgrade_01.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO shipment_cases (
                        id,
                        case_number,
                        case_name,
                        responsible_organization_code,
                        transport_scenario,
                        status,
                        revision,
                        description,
                        create_idempotency_key_hash,
                        create_request_hash
                    ) VALUES (
                        :id,
                        :case_number,
                        :case_name,
                        'DEMO_ORG',
                        'SEA_FCL',
                        'DRAFT',
                        1,
                        NULL,
                        :key_hash,
                        :request_hash
                    )
                    """
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000024",
                    "case_number": "SHP-MIGRATION-BACKFILL-001",
                    "case_name": "旧版案件迁移测试",
                    "key_hash": "a" * 64,
                    "request_hash": "b" * 64,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO shipment_case_events (
                        case_id,
                        sequence,
                        event_type,
                        previous_status,
                        current_status,
                        resource_type,
                        resource_number,
                        actor_subject,
                        actor_display_name,
                        actor_source,
                        request_id,
                        idempotency_key_hash,
                        request_hash,
                        detail
                    ) VALUES (
                        :case_id,
                        1,
                        'CASE_CREATED',
                        NULL,
                        'DRAFT',
                        NULL,
                        NULL,
                        'SYSTEM_DEMO',
                        'SYSTEM_DEMO',
                        'SYSTEM_DEMO',
                        :request_id,
                        :key_hash,
                        :request_hash,
                        '旧版创建事件'
                    )
                    """
                ),
                {
                    "case_id": "00000000-0000-0000-0000-000000000024",
                    "request_id": "00000000-0000-0000-0000-000000000025",
                    "key_hash": "a" * 64,
                    "request_hash": "b" * 64,
                },
            )
    finally:
        engine.dispose()

    upgrade_head = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade_head.returncode == 0, upgrade_head.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            command = (
                connection.execute(
                    text(
                        """
                        SELECT
                            command_type,
                            idempotency_key_hash,
                            request_hash,
                            result_kind,
                            result_revision,
                            result_event_id
                        FROM shipment_case_commands
                        """
                    )
                )
                .mappings()
                .one()
            )
        inspector = inspect(engine)
        event_columns = {
            column["name"]
            for column in inspector.get_columns("shipment_case_events")
        }
    finally:
        engine.dispose()

    assert command == {
        "command_type": "CREATE_CASE",
        "idempotency_key_hash": "a" * 64,
        "request_hash": "b" * 64,
        "result_kind": "APPLIED",
        "result_revision": 1,
        "result_event_id": 1,
    }
    assert "idempotency_key_hash" not in event_columns
    assert "request_hash" not in event_columns
