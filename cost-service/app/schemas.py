"""
cost-service – Pydantic schemas
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


# ── Pricing ──────────────────────────────────────────────────────────────────

class PricingBase(BaseModel):
    model: str
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    currency: str = "USD"


class PricingCreate(PricingBase):
    pass


class PricingUpdate(BaseModel):
    input_price_per_1k: Optional[Decimal] = None
    output_price_per_1k: Optional[Decimal] = None
    currency: Optional[str] = None


class PricingOut(PricingBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    updated_at: Optional[datetime] = None


# ── Cost calculation ──────────────────────────────────────────────────────────

class CostCalculateRequest(BaseModel):
    usage_id: int
    request_id: str
    agent_id: int
    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    daily_token_budget: int
    source: str = "demo"


class CostCalculateResponse(BaseModel):
    cost_id: int
    cost_usd: Decimal
    tokens_today: int
    budget_used_pct: Decimal
    alert: Optional[str] = None          # "WARNING" | "CRITICAL" | None


# ── Cost record out ───────────────────────────────────────────────────────────

class CostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    usage_id: int
    request_id: str
    agent_id: int
    agent_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: Decimal
    source: str
    created_at: datetime


# ── Alerts ───────────────────────────────────────────────────────────────────

class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    agent_id: int
    agent_name: str
    level: str
    budget_used_pct: Decimal
    tokens_today: int
    daily_token_budget: int
    alert_date: date
    message: str
    created_at: datetime


# ── Analytics ─────────────────────────────────────────────────────────────────

class OverviewAnalytics(BaseModel):
    total_requests: int
    total_tokens: int
    total_cost_usd: Decimal
    failed_model_calls: int


class ModelAnalytics(BaseModel):
    model: str
    request_count: int
    total_tokens: int
    total_cost_usd: Decimal


class AgentAnalytics(BaseModel):
    agent_id: int
    agent_name: str
    request_count: int
    total_tokens: int
    total_cost_usd: Decimal
    tokens_today: int
    budget_used_pct: Optional[Decimal] = None
