"""SQLAlchemy 声明基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有持久化模型共用的元数据容器。"""
