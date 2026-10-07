"""
agent-service – database models (SQLAlchemy 2.0 async)
"""
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    owner_team = Column(String(100), nullable=False, default="default")
    model = Column(String(100), nullable=False)
    daily_token_budget = Column(Integer, nullable=False, default=100_000)
    status = Column(String(10), nullable=False, default="ACTIVE")  # ACTIVE | INACTIVE
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
