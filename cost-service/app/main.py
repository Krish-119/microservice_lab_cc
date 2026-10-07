"""
cost-service – main FastAPI application
"""
import logging
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

import pytz
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db, init_db
from .models import Alert, Cost, Pricing
from .schemas import (
    AgentAnalytics,
    AlertOut,
    CostCalculateRequest,
    CostCalculateResponse,
    CostOut,
    ModelAnalytics,
    OverviewAnalytics,
    PricingCreate,
    PricingOut,
    PricingUpdate,
)
from .seed import seed_pricing

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | cost-service | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="cost-service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    request_id = request.headers.get("x-request-id", "-")
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "request | request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
        request_id, request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


@app.on_event("startup")
async def startup():
    await init_db()
    from .database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        await seed_pricing(session)
    logger.info("cost-service startup complete")


# ── helpers ───────────────────────────────────────────────────────────────────

def _today_ist() -> date:
    return datetime.now(IST).date()


async def _get_pricing_by_model(model: str, db: AsyncSession) -> Optional[Pricing]:
    result = await db.execute(select(Pricing).where(Pricing.model == model))
    return result.scalar_one_or_none()


def _compute_cost(pricing: Pricing, input_tokens: int, output_tokens: int) -> Decimal:
    i_cost = Decimal(input_tokens) * pricing.input_price_per_1k / Decimal("1000")
    o_cost = Decimal(output_tokens) * pricing.output_price_per_1k / Decimal("1000")
    return (i_cost + o_cost).quantize(Decimal("0.000001"))


# ── /pricing ──────────────────────────────────────────────────────────────────

@app.get("/pricing", response_model=List[PricingOut])
async def list_pricing(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Pricing).order_by(Pricing.id))
    return result.scalars().all()


@app.post("/pricing", response_model=PricingOut, status_code=201)
async def create_pricing(body: PricingCreate, db: AsyncSession = Depends(get_db)):
    existing = await _get_pricing_by_model(body.model, db)
    if existing:
        raise HTTPException(409, f"Pricing for model '{body.model}' already exists")
    p = Pricing(**body.model_dump())
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@app.put("/pricing/{model_name}", response_model=PricingOut)
async def update_pricing(model_name: str, body: PricingUpdate, db: AsyncSession = Depends(get_db)):
    p = await _get_pricing_by_model(model_name, db)
    if not p:
        raise HTTPException(404, f"Pricing for model '{model_name}' not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(p, field, value)
    await db.commit()
    await db.refresh(p)
    return p


# ── /cost/calculate ───────────────────────────────────────────────────────────

@app.post("/cost/calculate", response_model=CostCalculateResponse)
async def calculate_cost(
    body: CostCalculateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    request_id = request.headers.get("x-request-id", body.request_id)
    logger.info(
        "calculate | request_id=%s agent_id=%d model=%s tokens=%d",
        request_id, body.agent_id, body.model, body.total_tokens,
    )

    # Look up pricing; fall back to zero-cost entry
    pricing = await _get_pricing_by_model(body.model, db)
    if not pricing:
        # Insert a zero-price placeholder so the FK is satisfied
        pricing = Pricing(
            model=body.model,
            input_price_per_1k=Decimal("0"),
            output_price_per_1k=Decimal("0"),
            currency="USD",
        )
        db.add(pricing)
        await db.flush()

    cost_usd = _compute_cost(pricing, body.input_tokens, body.output_tokens)

    cost_row = Cost(
        usage_id=body.usage_id,
        request_id=request_id,
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        pricing_id=pricing.id,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        total_tokens=body.total_tokens,
        cost_usd=cost_usd,
        source=body.source,
    )
    db.add(cost_row)
    await db.flush()

    # tokens_today for this agent (IST date, from costs table only)
    today = _today_ist()
    today_start = datetime(today.year, today.month, today.day, tzinfo=IST)
    today_end_utc = today_start.astimezone(timezone.utc).replace(hour=23, minute=59, second=59)

    tokens_today_result = await db.execute(
        select(func.coalesce(func.sum(Cost.total_tokens), 0))
        .where(Cost.agent_id == body.agent_id)
        .where(func.date(func.timezone("Asia/Kolkata", Cost.created_at)) == today)
    )
    tokens_today = int(tokens_today_result.scalar_one())

    budget_used_pct = Decimal("0")
    if body.daily_token_budget > 0:
        budget_used_pct = (Decimal(tokens_today) / Decimal(body.daily_token_budget) * 100).quantize(Decimal("0.01"))

    # Determine alert level
    alert_level: Optional[str] = None
    if budget_used_pct >= 100:
        alert_level = "CRITICAL"
    elif budget_used_pct >= 80:
        alert_level = "WARNING"

    # Upsert alert (ON CONFLICT DO NOTHING prevents spam)
    if alert_level:
        msg = (
            f"Agent '{body.agent_name}' has used {budget_used_pct}% of its "
            f"daily budget ({tokens_today}/{body.daily_token_budget} tokens)"
        )
        alert_stmt = pg_insert(Alert).values(
            agent_id=body.agent_id,
            agent_name=body.agent_name,
            level=alert_level,
            budget_used_pct=budget_used_pct,
            tokens_today=tokens_today,
            daily_token_budget=body.daily_token_budget,
            alert_date=today,
            message=msg,
        ).on_conflict_do_nothing(
            index_elements=None,
            constraint="uq_alert_agent_date_level",
        )
        await db.execute(alert_stmt)

    await db.commit()
    await db.refresh(cost_row)

    logger.info(
        "calculated | request_id=%s cost_id=%d cost_usd=%s tokens_today=%d budget_pct=%s alert=%s",
        request_id, cost_row.id, cost_usd, tokens_today, budget_used_pct, alert_level,
    )

    return CostCalculateResponse(
        cost_id=cost_row.id,
        cost_usd=cost_usd,
        tokens_today=tokens_today,
        budget_used_pct=budget_used_pct,
        alert=alert_level,
    )


# ── /cost/agent/{id} ──────────────────────────────────────────────────────────

@app.get("/cost/agent/{agent_id}", response_model=List[CostOut])
async def cost_by_agent(agent_id: int, limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Cost)
        .where(Cost.agent_id == agent_id)
        .order_by(Cost.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ── /analytics ────────────────────────────────────────────────────────────────

@app.get("/analytics/overview", response_model=OverviewAnalytics)
async def analytics_overview(db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(
            func.count(Cost.id),
            func.coalesce(func.sum(Cost.total_tokens), 0),
            func.coalesce(func.sum(Cost.cost_usd), Decimal("0")),
        )
    )
    row = r.one()
    return OverviewAnalytics(
        total_requests=row[0],
        total_tokens=row[1],
        total_cost_usd=row[2] or Decimal("0"),
        failed_model_calls=0,   # cost-service doesn't know about failures; agent-service fills this
    )


@app.get("/analytics/models", response_model=List[ModelAnalytics])
async def analytics_models(db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(
            Pricing.model,
            func.count(Cost.id),
            func.coalesce(func.sum(Cost.total_tokens), 0),
            func.coalesce(func.sum(Cost.cost_usd), Decimal("0")),
        )
        .join(Pricing, Cost.pricing_id == Pricing.id)
        .group_by(Pricing.model)
        .order_by(func.sum(Cost.cost_usd).desc().nullslast())
    )
    return [
        ModelAnalytics(model=row[0], request_count=row[1], total_tokens=row[2], total_cost_usd=row[3] or Decimal("0"))
        for row in r.all()
    ]


@app.get("/analytics/agents", response_model=List[AgentAnalytics])
async def analytics_agents(db: AsyncSession = Depends(get_db)):
    today = _today_ist()
    r = await db.execute(
        select(
            Cost.agent_id,
            Cost.agent_name,
            func.count(Cost.id),
            func.coalesce(func.sum(Cost.total_tokens), 0),
            func.coalesce(func.sum(Cost.cost_usd), Decimal("0")),
            func.coalesce(
                func.sum(Cost.total_tokens).filter(
                    func.date(func.timezone("Asia/Kolkata", Cost.created_at)) == today
                ), 0
            ),
        )
        .group_by(Cost.agent_id, Cost.agent_name)
        .order_by(func.sum(Cost.cost_usd).desc().nullslast())
    )
    return [
        AgentAnalytics(
            agent_id=row[0],
            agent_name=row[1],
            request_count=row[2],
            total_tokens=row[3],
            total_cost_usd=row[4] or Decimal("0"),
            tokens_today=row[5],
        )
        for row in r.all()
    ]


# ── /alerts ───────────────────────────────────────────────────────────────────

@app.get("/alerts", response_model=List[AlertOut])
async def list_alerts(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "cost-service"}


@app.get("/health/deps")
async def health_deps(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
    return {"service": "cost-service", "database": db_status}
