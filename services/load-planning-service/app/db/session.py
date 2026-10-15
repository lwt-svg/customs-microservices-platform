"""异步数据库引擎和 FastAPI 会话依赖。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# 创建引擎不会立即连接 MySQL；真正执行 SQL 时才建立连接。
# pool_pre_ping 可在本地 MySQL 重启后尽早识别失效连接；pool_recycle 则避免一个连接
# 长时间闲置到超过 MySQL/NAT 的超时时间后才被业务请求第一次发现。两者配合比只设置
# pre_ping 更适合本地长时间运行和企业部署中的连接池。
engine = create_async_engine(
    settings.load_planning_database_url,
    pool_pre_ping=True,
    pool_recycle=1_800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """为每个 HTTP 请求提供独立异步会话。

    路由、Service 和 Repository 在一次请求内共享同一个会话。事务何时提交由仓储层
    明确决定，不能在依赖结束时偷偷自动提交。
    """

    async with SessionLocal() as session:
        yield session
