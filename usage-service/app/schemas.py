"""
usage-service – Pydantic schemas
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class UsageRecordRequest(BaseModel):
    agent_id: int
    agent_name: str
    model: str
    daily_token_budget: int
    prompt: Optional[str] = ""
    source: str = "demo"
    simulate_failures: bool = False


class UsageRecordResponse(BaseModel):
    usage_id: int
    request_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    status: str                         # success | failed
    error_message: Optional[str] = None
    # cost chain result
    cost_id: Optional[int] = None
    cost_usd: Optional[str] = None
    tokens_today: Optional[int] = None
    budget_used_pct: Optional[str] = None
    alert: Optional[str] = None


class UsageRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    request_id: str
    agent_id: int
    agent_name: str
    model_id: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    status: str
    error_message: Optional[str] = None
    source: str
    created_at: datetime


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    provider: str
    is_local: bool


class UsageSummary(BaseModel):
    total_requests: int
    total_tokens: int
    avg_latency_ms: float
    success_count: int
    failed_count: int


class PaginatedUsage(BaseModel):
    items: List[UsageRecordOut]
    total: int
    page: int
    page_size: int
