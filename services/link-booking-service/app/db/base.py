"""SQLAlchemy 声明式模型基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 LINK 服务表共享的元数据容器，供 Alembic 发现迁移对象。"""
