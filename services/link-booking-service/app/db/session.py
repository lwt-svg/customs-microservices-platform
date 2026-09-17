"""异步数据库会话依赖。

请求结束时会话会关闭，提交动作必须由业务仓储显式完成，避免隐式写库。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# 连接 URL 来自被 Git 忽略的本地环境变量；此处绝不记录 URL 或密码。
engine = create_async_engine(settings.link_database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """为单个 HTTP 请求提供仅属于 LINK 服务的异步数据库会话。"""

    async with SessionLocal() as session:
        yield session
