"""
cost-service – database models (SQLAlchemy 2.0 async)
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Pricing(Base):
    __tablename__ = "pricing"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(100), nullable=False, unique=True)
    input_price_per_1k = Column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    output_price_per_1k = Column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    currency = Column(String(10), nullable=False, default="USD")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    costs = relationship("Cost", back_populates="pricing_ref")


class Cost(Base):
    __tablename__ = "costs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    usage_id = Column(Integer, nullable=False)          # logical ref – no FK across DBs
    request_id = Column(String(64), nullable=False)
    agent_id = Column(Integer, nullable=False)
    agent_name = Column(String(200), nullable=False)
    pricing_id = Column(Integer, ForeignKey("pricing.id"), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Numeric(14, 6), nullable=False, default=Decimal("0"))
    source = Column(String(20), nullable=False, default="demo")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    pricing_ref = relationship("Pricing", back_populates="costs")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, nullable=False)
    agent_name = Column(String(200), nullable=False)
    level = Column(String(10), nullable=False)          # WARNING | CRITICAL
    budget_used_pct = Column(Numeric(6, 2), nullable=False)
    tokens_today = Column(Integer, nullable=False)
    daily_token_budget = Column(Integer, nullable=False)
    alert_date = Column(Date, nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "alert_date", "level", name="uq_alert_agent_date_level"),
    )
