"""
agent-service – Pydantic schemas
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


# ── Agent ─────────────────────────────────────────────────────────────────────

class AgentBase(BaseModel):
    name: str
    description: Optional[str] = None
    owner_team: str = "default"
    model: str
    daily_token_budget: int = 100_000
    status: str = "ACTIVE"


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    owner_team: Optional[str] = None
    model: Optional[str] = None
    daily_token_budget: Optional[int] = None
    status: Optional[str] = None


class AgentOut(AgentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


# ── Run request / response ────────────────────────────────────────────────────

class RunRequest(BaseModel):
    prompt: Optional[str] = ""
    source: str = "demo"               # demo | seed | loadtest
    simulate_failures: bool = False


class UsageInfo(BaseModel):
    usage_id: Optional[int] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    status: str = "success"
    error_message: Optional[str] = None


class CostInfo(BaseModel):
    cost_id: Optional[int] = None
    cost_usd: Optional[str] = None
    tokens_today: Optional[int] = None
    budget_used_pct: Optional[str] = None
    alert: Optional[str] = None


class RunResponse(BaseModel):
    request_id: str
    trace: List[str]
    agent: AgentOut
    usage: UsageInfo
    cost: CostInfo
    timings_ms: Dict[str, float]


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DashboardOverview(BaseModel):
    agents: List[AgentOut]
    total_requests: int
    total_tokens: int
    total_cost_usd: str
    failed_model_calls: int
    cost_per_model: List[Dict[str, Any]]
    tokens_per_agent: List[Dict[str, Any]]
    recent_alerts: List[Dict[str, Any]]
