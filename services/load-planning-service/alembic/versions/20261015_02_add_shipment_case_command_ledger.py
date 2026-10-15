"""separate shipment case command results from business events

Revision ID: 20261015_02
Revises: 20261015_01
Create Date: 2026-10-15 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261015_02"
down_revision: str | None = "20261015_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_BACKFILL_TABLE = "_shipment_case_command_backfill"

shipment_case_command_type = sa.Enum(
    "CREATE_CASE",
    "BIND_RESOURCE",
    "CHANGE_STATUS",
    name="shipment_case_command_type",
    native_enum=False,
    length=32,
)
shipment_case_command_result = sa.Enum(
    "APPLIED",
    "NO_OP",
    name="shipment_case_command_result",
    native_enum=False,
    length=16,
)


def _create_backfill_table() -> None:
    """创建无外键临时表，避免 SQLite batch 重建旧表时留下悬空引用。"""

    op.create_table(
        _BACKFILL_TABLE,
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("command_type", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("result_kind", sa.String(length=16), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("result_event_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
    )


def upgrade() -> None:
    """迁移历史成功事件，并建立可以单独记录 NO_OP 的命令台账。"""

    bind = op.get_bind()

    # MySQL 的 DDL 不参与事务。所有能从旧数据直接判断的阻断条件必须在创建临时表、
    # 删除列等 DDL 之前完成，否则一次预检失败会留下“版本仍是 01、结构却已改了一半”
    # 的数据库。
    unknown_event_type = bind.execute(
        sa.text(
            """
            SELECT event_type
            FROM shipment_case_events
            WHERE event_type NOT IN (
                'CASE_CREATED',
                'RESOURCE_BOUND',
                'CASE_STATUS_CHANGED'
            )
            LIMIT 1
            """
        )
    ).first()
    if unknown_event_type is not None:
        raise RuntimeError(
            "历史 Shipment Case 数据存在无法映射到命令类型的事件："
            f"{unknown_event_type[0]}"
        )

    # 旧模型只保证“案件内 key 唯一”。新台账把 key 提升为全局命令标识；如果历史数据
    # 曾跨案件复用同一个 key，必须人工清理歧义，不能让迁移静默选择其中一条。
    duplicate = bind.execute(
        sa.text(
            """
            SELECT idempotency_key_hash
            FROM shipment_case_events
            GROUP BY idempotency_key_hash
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "历史 Shipment Case 数据存在跨案件重复 Idempotency-Key 哈希，"
            "无法安全迁移到全局命令台账"
        )

    _create_backfill_table()
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_BACKFILL_TABLE} (
                case_id,
                command_type,
                idempotency_key_hash,
                request_hash,
                request_id,
                result_kind,
                result_revision,
                result_event_id,
                completed_at
            )
            SELECT
                case_id,
                CASE event_type
                    WHEN 'CASE_CREATED' THEN 'CREATE_CASE'
                    WHEN 'RESOURCE_BOUND' THEN 'BIND_RESOURCE'
                    WHEN 'CASE_STATUS_CHANGED' THEN 'CHANGE_STATUS'
                END,
                idempotency_key_hash,
                request_hash,
                request_id,
                'APPLIED',
                sequence,
                id,
                occurred_at
            FROM shipment_case_events
            """
        )
    )

    # 先用临时表保存历史命令，再重建旧表去掉幂等字段。这样全新 SQLite 测试库和已经
    # 执行过 01 的本机 MySQL 都会线性升级，而不是依赖修改旧 migration。
    with op.batch_alter_table("shipment_case_events") as batch:
        batch.drop_constraint(
            "uq_shipment_case_events_case_idempotency_key_hash",
            type_="unique",
        )
        batch.drop_column("request_hash")
        batch.drop_column("idempotency_key_hash")

    with op.batch_alter_table("shipment_cases") as batch:
        batch.drop_constraint(
            "uq_shipment_cases_create_idempotency_key_hash",
            type_="unique",
        )
        batch.drop_column("create_request_hash")
        batch.drop_column("create_idempotency_key_hash")

    op.create_table(
        "shipment_case_commands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column(
            "command_type",
            shipment_case_command_type,
            nullable=False,
        ),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column(
            "result_kind",
            shipment_case_command_result,
            nullable=False,
        ),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("result_event_id", sa.Integer(), nullable=True),
        sa.Column(
            "completed_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result_revision > 0",
            name="ck_shipment_case_commands_result_revision_positive",
        ),
        sa.CheckConstraint(
            "("
            "result_kind = 'APPLIED' AND result_event_id IS NOT NULL"
            ") OR ("
            "result_kind = 'NO_OP' AND result_event_id IS NULL"
            ")",
            name="ck_shipment_case_commands_result_event",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["shipment_cases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["result_event_id"],
            ["shipment_case_events.id"],
            # 业务事件是只追加事实。若允许级联删除命令，误删事件会释放已经成功使用过的
            # Idempotency-Key，因此这里禁止单独删除仍被 APPLIED 命令引用的事件。
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_shipment_case_commands_idempotency_key_hash",
        ),
        sa.UniqueConstraint(
            "result_event_id",
            name="uq_shipment_case_commands_result_event_id",
        ),
    )
    op.create_index(
        "ix_shipment_case_commands_case_id",
        "shipment_case_commands",
        ["case_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO shipment_case_commands (
                case_id,
                command_type,
                idempotency_key_hash,
                request_hash,
                request_id,
                result_kind,
                result_revision,
                result_event_id,
                completed_at
            )
            SELECT
                case_id,
                command_type,
                idempotency_key_hash,
                request_hash,
                request_id,
                result_kind,
                result_revision,
                result_event_id,
                completed_at
            FROM {_BACKFILL_TABLE}
            """
        )
    )
    op.drop_table(_BACKFILL_TABLE)


def downgrade() -> None:
    """恢复旧字段；NO_OP 没有对应事件，因此降级会丢失这类命令台账。"""

    _create_backfill_table()
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_BACKFILL_TABLE} (
                case_id,
                command_type,
                idempotency_key_hash,
                request_hash,
                request_id,
                result_kind,
                result_revision,
                result_event_id,
                completed_at
            )
            SELECT
                case_id,
                command_type,
                idempotency_key_hash,
                request_hash,
                request_id,
                result_kind,
                result_revision,
                result_event_id,
                completed_at
            FROM shipment_case_commands
            """
        )
    )

    # 不要在 MySQL 中先单独删除 case_id 索引。MySQL 会把它用于外键校验，并以
    # “Cannot drop index ... needed in a foreign key constraint” 拒绝迁移。直接删表
    # 会由数据库一起清理该表上的索引和外键，SQLite 与 MySQL 都能正确执行。
    op.drop_table("shipment_case_commands")

    with op.batch_alter_table("shipment_case_events") as batch:
        batch.add_column(
            sa.Column(
                "idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("request_hash", sa.String(length=64), nullable=True)
        )

    with op.batch_alter_table("shipment_cases") as batch:
        batch.add_column(
            sa.Column(
                "create_idempotency_key_hash",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "create_request_hash",
                sa.String(length=64),
                nullable=True,
            )
        )

    op.execute(
        sa.text(
            f"""
            UPDATE shipment_case_events
            SET
                idempotency_key_hash = (
                    SELECT idempotency_key_hash
                    FROM {_BACKFILL_TABLE}
                    WHERE result_event_id = shipment_case_events.id
                ),
                request_hash = (
                    SELECT request_hash
                    FROM {_BACKFILL_TABLE}
                    WHERE result_event_id = shipment_case_events.id
                )
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE shipment_cases
            SET
                create_idempotency_key_hash = (
                    SELECT idempotency_key_hash
                    FROM {_BACKFILL_TABLE}
                    WHERE
                        case_id = shipment_cases.id
                        AND command_type = 'CREATE_CASE'
                ),
                create_request_hash = (
                    SELECT request_hash
                    FROM {_BACKFILL_TABLE}
                    WHERE
                        case_id = shipment_cases.id
                        AND command_type = 'CREATE_CASE'
                )
            """
        )
    )

    with op.batch_alter_table("shipment_case_events") as batch:
        batch.alter_column(
            "idempotency_key_hash",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch.alter_column(
            "request_hash",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_shipment_case_events_case_idempotency_key_hash",
            ["case_id", "idempotency_key_hash"],
        )

    with op.batch_alter_table("shipment_cases") as batch:
        batch.alter_column(
            "create_idempotency_key_hash",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch.alter_column(
            "create_request_hash",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_shipment_cases_create_idempotency_key_hash",
            ["create_idempotency_key_hash"],
        )

    op.drop_table(_BACKFILL_TABLE)
