"""
usage-service – main FastAPI application
"""
import logging
import os
import time
import uuid
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from .database import AsyncSessionLocal, get_db, init_db
from .models import Model, UsageRecord
from .schemas import (
    ModelOut,
    PaginatedUsage,
    UsageRecordOut,
    UsageRecordRequest,
    UsageRecordResponse,
    UsageSummary,
)
from .seed import seed_models
from .simulator import call_model

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | usage-service | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

COST_SERVICE_URL = os.environ.get("COST_SERVICE_URL", "http://cost-service:8000")

# Shared httpx client
_http_client: Optional[httpx.AsyncClient] = None

# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="usage-service", version="1.0.0")

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
    global _http_client
    _http_client = httpx.AsyncClient(timeout=10.0)
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_models(session)
    logger.info("usage-service startup complete")


@app.on_event("shutdown")
async def shutdown():
    if _http_client:
        await _http_client.aclose()


def get_http_client() -> httpx.AsyncClient:
    return _http_client


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_model(db: AsyncSession, model_name: str) -> Model:
    result = await db.execute(select(Model).where(Model.name == model_name))
    m = result.scalar_one_or_none()
    if m is None:
        m = Model(name=model_name, provider="unknown", is_local=False)
        db.add(m)
        await db.flush()
    return m


# ── /usage/record ─────────────────────────────────────────────────────────────

@app.post("/usage/record", response_model=UsageRecordResponse)
async def record_usage(
    body: UsageRecordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4()).replace("-", "")
    logger.info(
        "record | request_id=%s agent_id=%d model=%s source=%s",
        request_id, body.agent_id, body.model, body.source,
    )

    # Simulate / call model
    t0 = time.perf_counter()
    inp, out, latency_ms, status, err_msg = await call_model(
        http_client,
        model=body.model,
        agent_id=body.agent_id,
        prompt=body.prompt or "",
        simulate_failures=body.simulate_failures,
    )
    total = inp + out

    # Ensure model exists in registry
    model_obj = await _get_or_create_model(db, body.model)

    usage_row = UsageRecord(
        request_id=request_id,
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        model_id=model_obj.id,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
        latency_ms=latency_ms,
        status=status,
        error_message=err_msg or None,
        source=body.source,
    )
    db.add(usage_row)
    await db.commit()
    await db.refresh(usage_row)

    logger.info(
        "stored | request_id=%s usage_id=%d status=%s tokens=%d latency_ms=%d",
        request_id, usage_row.id, status, total, latency_ms,
    )

    # Call cost-service
    cost_id = None
    cost_usd = None
    tokens_today = None
    budget_used_pct = None
    alert = None

    try:
        t_cost0 = time.perf_counter()
        cost_resp = await http_client.post(
            f"{COST_SERVICE_URL}/cost/calculate",
            json={
                "usage_id": usage_row.id,
                "request_id": request_id,
                "agent_id": body.agent_id,
                "agent_name": body.agent_name,
                "model": body.model,
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": total,
                "daily_token_budget": body.daily_token_budget,
                "source": body.source,
            },
            headers={"x-request-id": request_id},
        )
        cost_ms = int((time.perf_counter() - t_cost0) * 1000)
        logger.info(
            "cost-call | request_id=%s status=%d duration_ms=%d target=cost-service",
            request_id, cost_resp.status_code, cost_ms,
        )
        if cost_resp.status_code == 200:
            cd = cost_resp.json()
            cost_id = cd.get("cost_id")
            cost_usd = str(cd.get("cost_usd", "0"))
            tokens_today = cd.get("tokens_today")
            budget_used_pct = str(cd.get("budget_used_pct", "0"))
            alert = cd.get("alert")
        else:
            logger.warning("cost-service returned %d for request_id=%s", cost_resp.status_code, request_id)
    except Exception as exc:
        logger.error("cost-service call failed: %s (request_id=%s)", exc, request_id)

    return UsageRecordResponse(
        usage_id=usage_row.id,
        request_id=request_id,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
        latency_ms=latency_ms,
        status=status,
        error_message=err_msg or None,
        cost_id=cost_id,
        cost_usd=cost_usd,
        tokens_today=tokens_today,
        budget_used_pct=budget_used_pct,
        alert=alert,
    )


# ── /usage/live-demo ──────────────────────────────────────────────────────────

@app.post("/usage/live-demo")
async def live_demo(
    body: UsageRecordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    """Force Ollama for a single call (optional live demo)."""
    from .simulator import call_model_ollama
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4()).replace("-", "")
    inp, out, latency_ms, status, err_msg = await call_model_ollama(
        http_client, body.model, body.agent_id, body.prompt or "Hello from live demo", False
    )
    total = inp + out
    model_obj = await _get_or_create_model(db, body.model)
    usage_row = UsageRecord(
        request_id=request_id,
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        model_id=model_obj.id,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
        latency_ms=latency_ms,
        status=status,
        error_message=err_msg or None,
        source="demo",
    )
    db.add(usage_row)
    await db.commit()
    await db.refresh(usage_row)
    return {"usage_id": usage_row.id, "status": status, "latency_ms": latency_ms, "total_tokens": total}


# ── /usage (paginated) ────────────────────────────────────────────────────────

@app.get("/usage", response_model=PaginatedUsage)
async def list_usage(
    page: int = 1,
    page_size: int = 20,
    source: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(UsageRecord).order_by(UsageRecord.created_at.desc())
    if source:
        q = q.where(UsageRecord.source == source)
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedUsage(items=items, total=total, page=page, page_size=page_size)


@app.get("/usage/agent/{agent_id}", response_model=List[UsageRecordOut])
async def usage_by_agent(agent_id: int, limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(UsageRecord)
        .where(UsageRecord.agent_id == agent_id)
        .order_by(UsageRecord.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@app.get("/usage/model/{model_name}", response_model=List[UsageRecordOut])
async def usage_by_model(model_name: str, limit: int = 50, db: AsyncSession = Depends(get_db)):
    model_obj = await db.execute(select(Model).where(Model.name == model_name))
    m = model_obj.scalar_one_or_none()
    if not m:
        raise HTTPException(404, f"Model '{model_name}' not found")
    result = await db.execute(
        select(UsageRecord)
        .where(UsageRecord.model_id == m.id)
        .order_by(UsageRecord.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@app.get("/usage/summary", response_model=UsageSummary)
async def usage_summary(db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            func.coalesce(func.avg(UsageRecord.latency_ms), 0.0),
            func.count(UsageRecord.id).filter(UsageRecord.status == "success"),
            func.count(UsageRecord.id).filter(UsageRecord.status == "failed"),
        )
    )
    row = r.one()
    return UsageSummary(
        total_requests=row[0],
        total_tokens=row[1],
        avg_latency_ms=float(row[2]),
        success_count=row[3],
        failed_count=row[4],
    )


# ── /models ───────────────────────────────────────────────────────────────────

@app.get("/models", response_model=List[ModelOut])
async def list_models(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Model).order_by(Model.id))
    return result.scalars().all()


# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "usage-service"}


@app.get("/health/deps")
async def health_deps(
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    # DB check
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"

    # cost-service check
    try:
        r = await http_client.get(f"{COST_SERVICE_URL}/health", timeout=3.0)
        cost_status = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        cost_status = f"error: {e}"

    return {
        "service": "usage-service",
        "database": db_status,
        "cost-service": cost_status,
    }
