"""
agent-service – main FastAPI application
"""
import logging
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db, init_db
from .models import Agent
from .schemas import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
    CostInfo,
    DashboardOverview,
    RunRequest,
    RunResponse,
    UsageInfo,
)

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | agent-service | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

USAGE_SERVICE_URL = os.environ.get("USAGE_SERVICE_URL", "http://usage-service:8000")
COST_SERVICE_URL = os.environ.get("COST_SERVICE_URL", "http://cost-service:8000")

DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")

# Shared httpx client
_http_client: Optional[httpx.AsyncClient] = None

# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="agent-service", version="1.0.0")

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
    logger.info("agent-service startup complete")


@app.on_event("shutdown")
async def shutdown():
    if _http_client:
        await _http_client.aclose()


def get_http_client() -> httpx.AsyncClient:
    return _http_client


# ── /agents CRUD ──────────────────────────────────────────────────────────────

@app.post("/agents", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Agent).where(Agent.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Agent '{body.name}' already exists")
    agent = Agent(**body.model_dump())
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    logger.info("created agent id=%d name=%s", agent.id, agent.name)
    return agent


@app.get("/agents", response_model=List[AgentOut])
async def list_agents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).order_by(Agent.id))
    return result.scalars().all()


@app.get("/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return agent


@app.put("/agents/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: int, body: AgentUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(agent, field, value)
    await db.commit()
    await db.refresh(agent)
    return agent


@app.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    await db.delete(agent)
    await db.commit()


# ── /agents/{id}/run ──────────────────────────────────────────────────────────

@app.post("/agents/{agent_id}/run", response_model=RunResponse)
async def run_agent(
    agent_id: int,
    body: RunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    t_total_start = time.perf_counter()

    # Lookup agent
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    if agent.status == "INACTIVE":
        raise HTTPException(409, f"Agent '{agent.name}' is INACTIVE")

    # Generate request_id and propagate via header
    request_id = str(uuid.uuid4()).replace("-", "")
    headers = {"x-request-id": request_id, "Content-Type": "application/json"}

    logger.info(
        "run | request_id=%s agent_id=%d agent=%s model=%s source=%s",
        request_id, agent_id, agent.name, agent.model, body.source,
    )

    t_usage_start = time.perf_counter()

    # Call usage-service
    try:
        usage_resp = await http_client.post(
            f"{USAGE_SERVICE_URL}/usage/record",
            json={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "model": agent.model,
                "daily_token_budget": agent.daily_token_budget,
                "prompt": body.prompt or "",
                "source": body.source,
                "simulate_failures": body.simulate_failures,
            },
            headers=headers,
        )
        usage_chain_ms = (time.perf_counter() - t_usage_start) * 1000

        logger.info(
            "usage-call | request_id=%s status=%d duration_ms=%.1f target=usage-service",
            request_id, usage_resp.status_code, usage_chain_ms,
        )

        if usage_resp.status_code not in (200, 201):
            raise HTTPException(
                502,
                f"usage-service returned {usage_resp.status_code}: {usage_resp.text[:200]}",
            )

        ud = usage_resp.json()

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(503, "usage-service timed out — downstream not reachable")
    except httpx.ConnectError:
        raise HTTPException(503, "usage-service connection refused — service may be down")
    except Exception as exc:
        logger.error("usage-service call failed: %s (request_id=%s)", exc, request_id)
        raise HTTPException(502, f"usage-service error: {exc}")

    total_ms = (time.perf_counter() - t_total_start) * 1000

    usage_info = UsageInfo(
        usage_id=ud.get("usage_id"),
        input_tokens=ud.get("input_tokens", 0),
        output_tokens=ud.get("output_tokens", 0),
        total_tokens=ud.get("total_tokens", 0),
        latency_ms=ud.get("latency_ms", 0),
        status=ud.get("status", "success"),
        error_message=ud.get("error_message"),
    )

    cost_info = CostInfo(
        cost_id=ud.get("cost_id"),
        cost_usd=str(ud.get("cost_usd", "0")),
        tokens_today=ud.get("tokens_today"),
        budget_used_pct=str(ud.get("budget_used_pct", "0")),
        alert=ud.get("alert"),
    )

    return RunResponse(
        request_id=request_id,
        trace=["agent-service", "usage-service", "cost-service"],
        agent=AgentOut.model_validate(agent),
        usage=usage_info,
        cost=cost_info,
        timings_ms={
            "total": round(total_ms, 1),
            "usage_chain": round(usage_chain_ms, 1),
        },
    )


# ── /dashboard/overview ───────────────────────────────────────────────────────

@app.get("/dashboard/overview")
async def dashboard_overview(
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    # Agents from own DB
    agents_result = await db.execute(select(Agent).order_by(Agent.id))
    agents = agents_result.scalars().all()

    # Analytics from cost-service
    cost_overview = {"total_requests": 0, "total_tokens": 0, "total_cost_usd": "0", "failed_model_calls": 0}
    cost_models = []
    cost_agents = []
    alerts = []

    try:
        r = await http_client.get(f"{COST_SERVICE_URL}/analytics/overview", timeout=5.0)
        if r.status_code == 200:
            cost_overview = r.json()
    except Exception:
        pass

    try:
        r = await http_client.get(f"{COST_SERVICE_URL}/analytics/models", timeout=5.0)
        if r.status_code == 200:
            cost_models = r.json()
    except Exception:
        pass

    try:
        r = await http_client.get(f"{COST_SERVICE_URL}/analytics/agents", timeout=5.0)
        if r.status_code == 200:
            cost_agents = r.json()
    except Exception:
        pass

    try:
        r = await http_client.get(f"{COST_SERVICE_URL}/alerts?limit=20", timeout=5.0)
        if r.status_code == 200:
            alerts = r.json()
    except Exception:
        pass

    # Usage summary for failed model calls
    usage_summary = {}
    try:
        r = await http_client.get(f"{USAGE_SERVICE_URL}/usage/summary", timeout=5.0)
        if r.status_code == 200:
            usage_summary = r.json()
    except Exception:
        pass

    return {
        "agents": [AgentOut.model_validate(a).model_dump() for a in agents],
        "total_requests": cost_overview.get("total_requests", 0),
        "total_tokens": cost_overview.get("total_tokens", 0),
        "total_cost_usd": str(cost_overview.get("total_cost_usd", "0")),
        "failed_model_calls": usage_summary.get("failed_count", 0),
        "cost_per_model": cost_models,
        "tokens_per_agent": cost_agents,
        "recent_alerts": alerts,
    }


# ── Dashboard HTML ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-service"}


@app.get("/health/deps")
async def health_deps(
    db: AsyncSession = Depends(get_db),
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"

    try:
        r = await http_client.get(f"{USAGE_SERVICE_URL}/health", timeout=3.0)
        usage_status = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        usage_status = f"error: {e}"

    try:
        r = await http_client.get(f"{COST_SERVICE_URL}/health", timeout=3.0)
        cost_status = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        cost_status = f"error: {e}"

    return {
        "service": "agent-service",
        "database": db_status,
        "usage-service": usage_status,
        "cost-service": cost_status,
    }
