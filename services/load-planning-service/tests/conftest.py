"""测试基础设施。

所有 API 测试都使用内存 SQLite。它验证真实 SQLAlchemy 映射、事务和关联关系，但完全
不依赖本机 MySQL，也不会读取 ``Downloads`` 中的企业文件。
"""

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# 必须在导入 app.main 之前覆盖，否则模块级全局引擎会尝试装载 asyncmy。
os.environ["LOAD_PLANNING_DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["LOAD_PLANNING_NACOS_DISCOVERY_ENABLED"] = "false"
# API 测试的目标包含“真实 REST 写入口经过 LangGraph”。显式开启可避免开发者本机
# ``.env.local`` 恰好关闭功能开关时，整组接口测试悄悄退回 legacy Service 路径仍然通过。
os.environ["LOAD_PLANNING_LANGGRAPH_ENABLED"] = "true"
# 既有 Shipment 测试锁定 V2 行为；V3 专项测试会在构造 Service 时显式开启 RPA，
# 这样可以同时证明历史流程不回归、新流程确实经过统一 RPA Job。
os.environ["SHIPMENT_AGENT_RPA_ENABLED"] = "false"

from app.db.base import Base
from app.db.session import get_session, get_session_factory
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """为每个测试创建一套干净表，并让 API 使用同一个内存数据库。"""

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    def override_get_session_factory() -> async_sessionmaker[AsyncSession]:
        """让 Shipment Case 的多阶段事务也使用本测试的内存 SQLite。"""

        return session_factory

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_session_factory] = override_get_session_factory
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
