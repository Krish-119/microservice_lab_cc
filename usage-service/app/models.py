"""
usage-service – database models (SQLAlchemy 2.0 async)
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Index, Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Model(Base):
    """Model registry – seeded at startup."""
    __tablename__ = "models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    provider = Column(String(100), nullable=False, default="unknown")
    is_local = Column(Boolean, nullable=False, default=False)

    usage_records = relationship("UsageRecord", back_populates="model_ref")


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False)
    agent_id = Column(Integer, nullable=False)
    agent_name = Column(String(200), nullable=False)
    model_id = Column(Integer, ForeignKey("models.id"), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer, nullable=False, default=0)
    status = Column(String(10), nullable=False, default="success")   # success | failed
    error_message = Column(Text, nullable=True)
    source = Column(String(20), nullable=False, default="demo")      # seed | demo | loadtest
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    model_ref = relationship("Model", back_populates="usage_records")

    __table_args__ = (
        Index("ix_usage_agent_created", "agent_id", "created_at"),
        Index("ix_usage_model_id", "model_id"),
    )
