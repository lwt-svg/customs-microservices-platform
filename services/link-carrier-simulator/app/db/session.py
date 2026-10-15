"""异步 SQLAlchemy 会话依赖。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.carrier_database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """为单个 HTTP 请求提供并在结束时关闭一个异步会话。"""

    async with SessionLocal() as session:
        yield session
