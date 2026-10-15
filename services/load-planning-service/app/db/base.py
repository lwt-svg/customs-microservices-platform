"""SQLAlchemy 声明式模型基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """装载服务全部表的元数据容器，供 ORM 和 Alembic 共用。"""
