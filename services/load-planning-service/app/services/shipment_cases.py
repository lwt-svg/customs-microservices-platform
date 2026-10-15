"""发运案件应用服务。

案件把装载任务和 LINK 订舱申请关联到同一票业务，但不复制两个专业模块的明细。每个写
命令都同时处理幂等、乐观并发和数据库唯一约束；只有真正改变案件事实的命令才追加审计
事件，成功 ``NO_OP`` 只登记命令台账。

资源绑定故意拆成三个阶段：

1. 用短数据库会话检查案件、幂等记录和已有绑定；
2. 关闭会话后验证本地或远程资源；
3. 新建写事务，锁住案件并重新检查后提交绑定与事件。

第二阶段没有数据库事务，因此 LINK 超时不会长时间占用案件行锁或连接事务。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.shipment_resources import ShipmentResourceVerifier
from app.models.shipment_case import (
    ShipmentCase,
    ShipmentCaseCommand,
    ShipmentCaseCommandResult,
    ShipmentCaseCommandType,
    ShipmentCaseEvent,
    ShipmentCaseResourceBinding,
    ShipmentCaseStatus,
)
from app.repositories.shipment_cases import ShipmentCaseRepository
from app.schemas.shipment_case import (
    ShipmentCaseCreate,
    ShipmentCaseEventRead,
    ShipmentCaseRead,
    ShipmentCaseResourceBind,
    ShipmentCaseResourceBindingRead,
    ShipmentCaseStatusChange,
)


class ShipmentCaseNotFoundError(Exception):
    """案件编号不存在。"""


class ShipmentCaseConflictError(Exception):
    """幂等、并发、状态或资源归属发生冲突。"""


class ShipmentCaseValidationError(Exception):
    """HTTP 类型有效，但命令值不满足案件规则。"""


# 当前阶段还没有经过认证的 Java BFF 用户上下文。服务器端写入明确的演示占位身份，
# 至少不会直接采用浏览器请求体中的 actor。后续接入可信服务身份和用户上下文时，再把
# 这组常量替换为独立的 actor provider；当前值不能用于真实责任追溯。
SYSTEM_DEMO_ACTOR_SUBJECT = "SYSTEM_DEMO"
SYSTEM_DEMO_ACTOR_DISPLAY_NAME = "SYSTEM_DEMO"
SYSTEM_DEMO_ACTOR_SOURCE = "SYSTEM_DEMO"
DEMO_ORGANIZATION_CODE = "DEMO_ORG"

_ALLOWED_STATUS_TRANSITIONS: dict[ShipmentCaseStatus, set[ShipmentCaseStatus]] = {
    ShipmentCaseStatus.DRAFT: {
        ShipmentCaseStatus.ACTIVE,
        ShipmentCaseStatus.CANCELLED,
    },
    ShipmentCaseStatus.ACTIVE: {
        ShipmentCaseStatus.COMPLETED,
        ShipmentCaseStatus.CANCELLED,
    },
    ShipmentCaseStatus.COMPLETED: set(),
    ShipmentCaseStatus.CANCELLED: set(),
}


def _canonical_hash(value: dict[str, object]) -> str:
    """把结构化命令转换成排序稳定的 SHA-256 指纹。"""

    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_idempotency_key(value: str) -> str:
    """校验写请求的 HTTP 幂等键。

    数据库只保存返回值的 SHA-256。原始请求头既不会进入事件，也不会出现在日志或响应。
    """

    normalized = value.strip()
    if not normalized:
        raise ShipmentCaseValidationError("Idempotency-Key 不能为空")
    if len(normalized) > 128 or any(ord(character) < 32 for character in normalized):
        raise ShipmentCaseValidationError("Idempotency-Key 必须是 1 到 128 个可见字符")
    return normalized


def _idempotency_key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _database_utc(value: datetime) -> datetime:
    """把验证器的时区时间转换成项目数据库约定的 UTC naive DATETIME。"""

    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class ShipmentCaseService:
    """发运案件创建、查询、资源绑定和生命周期变更服务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        resource_verifier: ShipmentResourceVerifier,
    ) -> None:
        self.session_factory = session_factory
        self.resource_verifier = resource_verifier

    async def create(
        self,
        payload: ShipmentCaseCreate,
        idempotency_key: str,
    ) -> ShipmentCaseRead:
        """创建 DRAFT 案件，并原子写入事件与 ``APPLIED`` 命令结果。"""

        normalized_key = _normalize_idempotency_key(idempotency_key)
        key_hash = _idempotency_key_hash(normalized_key)
        request_hash = _canonical_hash(
            {
                "action": "CREATE_CASE",
                "case_number": payload.case_number,
                "case_name": payload.case_name,
                "transport_scenario": payload.transport_scenario.value,
                "description": payload.description,
            }
        )
        case_id = str(uuid4())
        request_id = str(uuid4())

        try:
            async with self.session_factory() as session:
                repository = ShipmentCaseRepository(session)
                async with session.begin():
                    existing_command = (
                        await repository.get_command_by_idempotency_key_hash(
                            key_hash
                        )
                    )
                    if existing_command is not None:
                        self._ensure_same_command(
                            existing_command,
                            command_type=ShipmentCaseCommandType.CREATE_CASE,
                            request_hash=request_hash,
                        )
                        existing = await repository.get_by_id(
                            existing_command.case_id
                        )
                        if existing is None:  # pragma: no cover - 外键应保证存在
                            raise RuntimeError("幂等命令对应的发运案件不存在")
                        return self._to_read(existing)

                    same_number = await repository.get_by_number(payload.case_number)
                    if same_number is not None:
                        raise ShipmentCaseConflictError("发运案件编号已存在")

                    shipment_case = ShipmentCase(
                        id=case_id,
                        case_number=payload.case_number,
                        case_name=payload.case_name,
                        responsible_organization_code=DEMO_ORGANIZATION_CODE,
                        transport_scenario=payload.transport_scenario,
                        status=ShipmentCaseStatus.DRAFT,
                        revision=1,
                        description=payload.description,
                    )
                    event = self._event(
                        shipment_case=shipment_case,
                        event_type="CASE_CREATED",
                        request_id=request_id,
                        detail=f"发运案件 {payload.case_number} 已创建。",
                        previous_status=None,
                        current_status=ShipmentCaseStatus.DRAFT,
                    )
                    shipment_case.events.append(event)
                    repository.add(shipment_case)
                    # 先 flush 业务事实，取得事件自增主键；命令台账随后在同一事务写入。
                    await repository.flush()
                    repository.add(
                        self._command(
                            shipment_case=shipment_case,
                            command_type=ShipmentCaseCommandType.CREATE_CASE,
                            idempotency_key_hash=key_hash,
                            request_hash=request_hash,
                            request_id=request_id,
                            result_kind=ShipmentCaseCommandResult.APPLIED,
                            result_event_id=event.id,
                        )
                    )
                    await repository.flush()
        except IntegrityError:
            return await self._recover_create_conflict(
                payload.case_number,
                key_hash,
                request_hash,
            )

        return await self._read_by_id(case_id)

    async def list(self) -> list[ShipmentCaseRead]:
        """读取案件中心列表；首个切片返回完整资源和事件。"""

        async with self.session_factory() as session:
            cases = await ShipmentCaseRepository(session).list_all()
            return [self._to_read(shipment_case) for shipment_case in cases]

    async def get(self, case_number: str) -> ShipmentCaseRead:
        """按稳定案件编号读取详情。"""

        async with self.session_factory() as session:
            shipment_case = await ShipmentCaseRepository(session).get_by_number(
                case_number
            )
            if shipment_case is None:
                raise ShipmentCaseNotFoundError("发运案件不存在")
            return self._to_read(shipment_case)

    async def bind_resource(
        self,
        case_number: str,
        payload: ShipmentCaseResourceBind,
        idempotency_key: str,
    ) -> ShipmentCaseRead:
        """验证并绑定一个装载任务或 LINK 申请。

        第一次成功后，同一个幂等键即使仍携带旧 ``expected_revision``，也会在阶段 A
        直接返回，不重新访问 LINK。相同资源已经属于本案件时返回业务 ``NO_OP``，但
        仍会登记命令台账，防止这个已成功的 key 以后被换 payload 使用。
        """

        normalized_key = _normalize_idempotency_key(idempotency_key)
        key_hash = _idempotency_key_hash(normalized_key)
        request_hash = _canonical_hash(
            {
                "action": "BIND_RESOURCE",
                "resource_type": payload.resource_type.value,
                "resource_number": payload.resource_number,
                "expected_revision": payload.expected_revision,
            }
        )
        request_id = str(uuid4())

        # 阶段 A：短事务只检查案件、命令和绑定。已有绑定的 NO_OP 也在这里原子登记。
        try:
            async with self.session_factory() as session:
                repository = ShipmentCaseRepository(session)
                async with session.begin():
                    shipment_case = await repository.get_by_number(
                        case_number,
                        for_update=True,
                    )
                    if shipment_case is None:
                        raise ShipmentCaseNotFoundError("发运案件不存在")
                    case_id = shipment_case.id

                    command = (
                        await repository.get_command_by_idempotency_key_hash(
                            key_hash
                        )
                    )
                    if command is not None:
                        self._ensure_same_command(
                            command,
                            command_type=ShipmentCaseCommandType.BIND_RESOURCE,
                            request_hash=request_hash,
                            expected_case_id=case_id,
                        )
                        return self._to_read(shipment_case)

                    binding = await repository.get_binding_by_resource(
                        payload.resource_type,
                        payload.resource_number,
                    )
                    if binding is not None:
                        if binding.case_id != case_id:
                            raise ShipmentCaseConflictError(
                                "该业务资源已绑定到另一发运案件"
                            )
                        repository.add(
                            self._command(
                                shipment_case=shipment_case,
                                command_type=(
                                    ShipmentCaseCommandType.BIND_RESOURCE
                                ),
                                idempotency_key_hash=key_hash,
                                request_hash=request_hash,
                                request_id=request_id,
                                result_kind=ShipmentCaseCommandResult.NO_OP,
                            )
                        )
                        await repository.flush()
                        return self._to_read(shipment_case)

                    self._ensure_case_accepts_resources(shipment_case)
        except IntegrityError:
            return await self._recover_binding_conflict(
                case_number,
                payload,
                key_hash,
                request_hash,
                request_id,
            )

        # 阶段 B：此处没有打开案件数据库会话或事务。
        verified = await self.resource_verifier.verify(
            payload.resource_type,
            payload.resource_number,
        )

        # 阶段 C：远程等待结束后，用新事务重新检查全部并发条件。
        try:
            async with self.session_factory() as session:
                repository = ShipmentCaseRepository(session)
                async with session.begin():
                    shipment_case = await repository.get_by_number(
                        case_number,
                        for_update=True,
                    )
                    if shipment_case is None:
                        raise ShipmentCaseNotFoundError("发运案件不存在")
                    case_id = shipment_case.id

                    command = (
                        await repository.get_command_by_idempotency_key_hash(
                            key_hash
                        )
                    )
                    if command is not None:
                        self._ensure_same_command(
                            command,
                            command_type=ShipmentCaseCommandType.BIND_RESOURCE,
                            request_hash=request_hash,
                            expected_case_id=case_id,
                        )
                        return self._to_read(shipment_case)

                    binding = await repository.get_binding_by_resource(
                        payload.resource_type,
                        payload.resource_number,
                    )
                    if binding is not None:
                        if binding.case_id != case_id:
                            raise ShipmentCaseConflictError(
                                "该业务资源已绑定到另一发运案件"
                            )
                        repository.add(
                            self._command(
                                shipment_case=shipment_case,
                                command_type=(
                                    ShipmentCaseCommandType.BIND_RESOURCE
                                ),
                                idempotency_key_hash=key_hash,
                                request_hash=request_hash,
                                request_id=request_id,
                                result_kind=ShipmentCaseCommandResult.NO_OP,
                            )
                        )
                        await repository.flush()
                        return self._to_read(shipment_case)

                    self._ensure_case_accepts_resources(shipment_case)
                    self._ensure_revision(shipment_case, payload.expected_revision)

                    shipment_case.revision += 1
                    shipment_case.resource_bindings.append(
                        ShipmentCaseResourceBinding(
                            case_id=shipment_case.id,
                            resource_type=verified.resource_type,
                            resource_number=verified.resource_number,
                            resource_status_at_verification=verified.resource_status,
                            verification_source=verified.verification_source,
                            verified_at=_database_utc(verified.verified_at),
                        )
                    )
                    event = self._event(
                        shipment_case=shipment_case,
                        event_type="RESOURCE_BOUND",
                        request_id=request_id,
                        detail=(
                            f"已绑定 {verified.resource_type.value} "
                            f"{verified.resource_number}；绑定时状态为 "
                            f"{verified.resource_status}。"
                        ),
                        previous_status=shipment_case.status,
                        current_status=shipment_case.status,
                        resource_type=verified.resource_type,
                        resource_number=verified.resource_number,
                    )
                    shipment_case.events.append(event)
                    await repository.flush()
                    repository.add(
                        self._command(
                            shipment_case=shipment_case,
                            command_type=ShipmentCaseCommandType.BIND_RESOURCE,
                            idempotency_key_hash=key_hash,
                            request_hash=request_hash,
                            request_id=request_id,
                            result_kind=ShipmentCaseCommandResult.APPLIED,
                            result_event_id=event.id,
                        )
                    )
                    await repository.flush()
        except IntegrityError:
            return await self._recover_binding_conflict(
                case_number,
                payload,
                key_hash,
                request_hash,
                request_id,
            )

        return await self._read_by_id(case_id)

    async def change_status(
        self,
        case_number: str,
        payload: ShipmentCaseStatusChange,
        idempotency_key: str,
    ) -> ShipmentCaseRead:
        """按最小状态机推进案件，并把原因写入只追加事件。"""

        normalized_key = _normalize_idempotency_key(idempotency_key)
        key_hash = _idempotency_key_hash(normalized_key)
        request_hash = _canonical_hash(
            {
                "action": "CHANGE_STATUS",
                "target_status": payload.target_status.value,
                "expected_revision": payload.expected_revision,
                "reason": payload.reason,
            }
        )
        request_id = str(uuid4())

        try:
            async with self.session_factory() as session:
                repository = ShipmentCaseRepository(session)
                async with session.begin():
                    shipment_case = await repository.get_by_number(
                        case_number,
                        for_update=True,
                    )
                    if shipment_case is None:
                        raise ShipmentCaseNotFoundError("发运案件不存在")

                    command = (
                        await repository.get_command_by_idempotency_key_hash(
                            key_hash
                        )
                    )
                    if command is not None:
                        self._ensure_same_command(
                            command,
                            command_type=ShipmentCaseCommandType.CHANGE_STATUS,
                            request_hash=request_hash,
                            expected_case_id=shipment_case.id,
                        )
                        return self._to_read(shipment_case)

                    self._ensure_revision(shipment_case, payload.expected_revision)
                    allowed = _ALLOWED_STATUS_TRANSITIONS[shipment_case.status]
                    if payload.target_status not in allowed:
                        raise ShipmentCaseConflictError(
                            f"不允许从 {shipment_case.status.value} 转为 "
                            f"{payload.target_status.value}"
                        )

                    previous_status = shipment_case.status
                    shipment_case.status = payload.target_status
                    shipment_case.revision += 1
                    event = self._event(
                        shipment_case=shipment_case,
                        event_type="CASE_STATUS_CHANGED",
                        request_id=request_id,
                        detail=(
                            f"案件状态从 {previous_status.value} 变更为 "
                            f"{payload.target_status.value}。原因：{payload.reason}"
                        ),
                        previous_status=previous_status,
                        current_status=payload.target_status,
                    )
                    shipment_case.events.append(event)
                    await repository.flush()
                    repository.add(
                        self._command(
                            shipment_case=shipment_case,
                            command_type=ShipmentCaseCommandType.CHANGE_STATUS,
                            idempotency_key_hash=key_hash,
                            request_hash=request_hash,
                            request_id=request_id,
                            result_kind=ShipmentCaseCommandResult.APPLIED,
                            result_event_id=event.id,
                        )
                    )
                    await repository.flush()
                    case_id = shipment_case.id
        except IntegrityError:
            return await self._recover_event_conflict(
                case_number,
                key_hash,
                request_hash,
            )

        return await self._read_by_id(case_id)

    async def _recover_create_conflict(
        self,
        case_number: str,
        key_hash: str,
        request_hash: str,
    ) -> ShipmentCaseRead:
        """把并发创建的唯一约束结果翻译成幂等成功或明确冲突。"""

        async with self.session_factory() as session:
            repository = ShipmentCaseRepository(session)
            command = await repository.get_command_by_idempotency_key_hash(key_hash)
            if command is not None:
                self._ensure_same_command(
                    command,
                    command_type=ShipmentCaseCommandType.CREATE_CASE,
                    request_hash=request_hash,
                )
                existing = await repository.get_by_id(command.case_id)
                if existing is None:  # pragma: no cover - 外键应保证存在
                    raise RuntimeError("幂等命令对应的发运案件不存在")
                return self._to_read(existing)
            if await repository.get_by_number(case_number) is not None:
                raise ShipmentCaseConflictError("发运案件编号已存在")
        raise ShipmentCaseConflictError("发运案件并发创建冲突，请刷新后重试")

    async def _recover_binding_conflict(
        self,
        case_number: str,
        payload: ShipmentCaseResourceBind,
        key_hash: str,
        request_hash: str,
        request_id: str,
    ) -> ShipmentCaseRead:
        """由数据库唯一约束裁决并发绑定，并补登记赢家造成的 ``NO_OP``。"""

        try:
            async with self.session_factory() as session:
                repository = ShipmentCaseRepository(session)
                async with session.begin():
                    shipment_case = await repository.get_by_number(
                        case_number,
                        for_update=True,
                    )
                    if shipment_case is None:
                        raise ShipmentCaseNotFoundError("发运案件不存在")

                    command = (
                        await repository.get_command_by_idempotency_key_hash(
                            key_hash
                        )
                    )
                    if command is not None:
                        self._ensure_same_command(
                            command,
                            command_type=ShipmentCaseCommandType.BIND_RESOURCE,
                            request_hash=request_hash,
                            expected_case_id=shipment_case.id,
                        )
                        return self._to_read(shipment_case)

                    binding = await repository.get_binding_by_resource(
                        payload.resource_type,
                        payload.resource_number,
                    )
                    if binding is not None:
                        if binding.case_id != shipment_case.id:
                            raise ShipmentCaseConflictError(
                                "该业务资源已绑定到另一发运案件"
                            )
                        repository.add(
                            self._command(
                                shipment_case=shipment_case,
                                command_type=(
                                    ShipmentCaseCommandType.BIND_RESOURCE
                                ),
                                idempotency_key_hash=key_hash,
                                request_hash=request_hash,
                                request_id=request_id,
                                result_kind=ShipmentCaseCommandResult.NO_OP,
                            )
                        )
                        await repository.flush()
                        return self._to_read(shipment_case)
        except IntegrityError:
            return await self._recover_command_conflict(
                case_number,
                key_hash,
                ShipmentCaseCommandType.BIND_RESOURCE,
                request_hash,
                "资源绑定发生并发冲突，请刷新后重试",
            )
        raise ShipmentCaseConflictError("资源绑定发生并发冲突，请刷新后重试")

    async def _recover_event_conflict(
        self,
        case_number: str,
        key_hash: str,
        request_hash: str,
    ) -> ShipmentCaseRead:
        """恢复同一案件内并发提交的相同幂等命令。"""

        return await self._recover_command_conflict(
            case_number,
            key_hash,
            ShipmentCaseCommandType.CHANGE_STATUS,
            request_hash,
            "案件状态发生并发变化，请刷新后重试",
        )

    async def _recover_command_conflict(
        self,
        case_number: str,
        key_hash: str,
        command_type: ShipmentCaseCommandType,
        request_hash: str,
        fallback_message: str,
    ) -> ShipmentCaseRead:
        """唯一约束竞争回滚后，从独立命令台账恢复赢家结果。"""

        async with self.session_factory() as session:
            repository = ShipmentCaseRepository(session)
            shipment_case = await repository.get_by_number(case_number)
            if shipment_case is None:
                raise ShipmentCaseNotFoundError("发运案件不存在")
            command = await repository.get_command_by_idempotency_key_hash(
                key_hash
            )
            if command is not None:
                self._ensure_same_command(
                    command,
                    command_type=command_type,
                    request_hash=request_hash,
                    expected_case_id=shipment_case.id,
                )
                return self._to_read(shipment_case)
        raise ShipmentCaseConflictError(fallback_message)

    async def _read_by_id(self, case_id: str) -> ShipmentCaseRead:
        """提交后重新查询，取得数据库生成的时间和完整集合。"""

        async with self.session_factory() as session:
            shipment_case = await ShipmentCaseRepository(session).get_by_id(case_id)
            if shipment_case is None:  # pragma: no cover - 提交成功后理论上不可发生
                raise RuntimeError("发运案件提交成功后无法重新读取")
            return self._to_read(shipment_case)

    @staticmethod
    def _ensure_same_command(
        command: ShipmentCaseCommand,
        *,
        command_type: ShipmentCaseCommandType,
        request_hash: str,
        expected_case_id: str | None = None,
    ) -> None:
        """确认一个全局唯一 key 仍指向完全相同的案件命令。"""

        if (
            command.command_type != command_type
            or command.request_hash != request_hash
            or (
                expected_case_id is not None
                and command.case_id != expected_case_id
            )
        ):
            raise ShipmentCaseConflictError(
                "Idempotency-Key 已用于另一份案件命令，请更换后重试"
            )

    @staticmethod
    def _ensure_revision(
        shipment_case: ShipmentCase,
        expected_revision: int,
    ) -> None:
        if shipment_case.revision != expected_revision:
            raise ShipmentCaseConflictError(
                f"案件修订号已变化，当前为 {shipment_case.revision}，请刷新后重试"
            )

    @staticmethod
    def _ensure_case_accepts_resources(shipment_case: ShipmentCase) -> None:
        if shipment_case.status in {
            ShipmentCaseStatus.COMPLETED,
            ShipmentCaseStatus.CANCELLED,
        }:
            raise ShipmentCaseConflictError(
                f"{shipment_case.status.value} 案件不能继续绑定业务资源"
            )

    @staticmethod
    def _event(
        *,
        shipment_case: ShipmentCase,
        event_type: str,
        request_id: str,
        detail: str,
        previous_status: ShipmentCaseStatus | None,
        current_status: ShipmentCaseStatus | None,
        resource_type=None,
        resource_number: str | None = None,
    ) -> ShipmentCaseEvent:
        """按 ``event.sequence == case.revision`` 构造服务端事件。

        事件结构和顺序由服务端控制；幂等摘要属于命令台账。actor 当前仍是
        ``SYSTEM_DEMO`` 占位值，尚不能代表经过登录认证的真实操作人。
        """

        return ShipmentCaseEvent(
            case_id=shipment_case.id,
            sequence=shipment_case.revision,
            event_type=event_type,
            previous_status=previous_status,
            current_status=current_status,
            resource_type=resource_type,
            resource_number=resource_number,
            actor_subject=SYSTEM_DEMO_ACTOR_SUBJECT,
            actor_display_name=SYSTEM_DEMO_ACTOR_DISPLAY_NAME,
            actor_source=SYSTEM_DEMO_ACTOR_SOURCE,
            request_id=request_id,
            detail=detail,
        )

    @staticmethod
    def _command(
        *,
        shipment_case: ShipmentCase,
        command_type: ShipmentCaseCommandType,
        idempotency_key_hash: str,
        request_hash: str,
        request_id: str,
        result_kind: ShipmentCaseCommandResult,
        result_event_id: int | None = None,
    ) -> ShipmentCaseCommand:
        """构造一次成功命令的持久化结果，不把原始幂等键写入数据库。"""

        return ShipmentCaseCommand(
            case_id=shipment_case.id,
            command_type=command_type,
            idempotency_key_hash=idempotency_key_hash,
            request_hash=request_hash,
            request_id=request_id,
            result_kind=result_kind,
            result_revision=shipment_case.revision,
            result_event_id=result_event_id,
        )

    @staticmethod
    def _to_read(shipment_case: ShipmentCase) -> ShipmentCaseRead:
        """把 ORM 聚合转换成不暴露幂等摘要的 HTTP 视图。

        ``id`` 供内部技术关联和调试使用；跨页面、跨服务和人工沟通仍应使用稳定的
        ``case_number``。
        """

        return ShipmentCaseRead(
            id=shipment_case.id,
            case_number=shipment_case.case_number,
            case_name=shipment_case.case_name,
            responsible_organization_code=(
                shipment_case.responsible_organization_code
            ),
            transport_scenario=shipment_case.transport_scenario,
            status=shipment_case.status,
            revision=shipment_case.revision,
            description=shipment_case.description,
            created_at=shipment_case.created_at,
            updated_at=shipment_case.updated_at,
            resource_bindings=[
                ShipmentCaseResourceBindingRead.model_validate(binding)
                for binding in shipment_case.resource_bindings
            ],
            events=[
                ShipmentCaseEventRead.model_validate(event)
                for event in shipment_case.events
            ],
        )
