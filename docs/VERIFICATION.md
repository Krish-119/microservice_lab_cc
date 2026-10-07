# Verification Checklist — AI Agent & Model Operations Platform

**Generated:** 2026-10-06  
**Environment:** Windows 11, Docker Desktop (WSL2 v29.8.0), Python 3.13 Host / Python 3.11 Container  
**Testing Modality:** Actual execution against running Docker containers on `localhost` and internal bridge network.

---

## 1. Core Architectural Constraints

| Constraint / Requirement | Status | Verification Details |
|---|---|---|
| Exactly 3 application microservices | **VERIFIED** | `agent-service`, `usage-service`, `cost-service` built and running. PostgreSQL is infrastructure (`postgres-db`). |
| Database-per-service pattern | **VERIFIED** | 3 independent databases (`agentdb`, `usagedb`, `costdb`) created via `db/init.sql` with isolated owners (`agentuser`, `usageuser`, `costuser`). No cross-DB foreign keys. |
| Container naming & image tags | **VERIFIED** | Images `lab/agent-service:1.0`, `lab/usage-service:1.0`, `lab/cost-service:1.0`. Containers: `agent-service`, `usage-service`, `cost-service`, `postgres-db`. |
| Internal Compose networking | **VERIFIED** | Bridge network `ai-ops-net`. Services communicate exclusively via DNS names `http://usage-service:8000` and `http://cost-service:8000`. |
| Single uvicorn worker per service | **VERIFIED** | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1` enforced across all 3 Dockerfiles. |
| Non-blocking async SQLAlchemy 2.0 | **VERIFIED** | `create_async_engine` + `asyncpg` with connection pools `pool_size=10, max_overflow=10` per service. |
| Single shared `httpx.AsyncClient` | **VERIFIED** | Lifespan context initializes one shared client per service with 10s default timeout. |

---

## 2. Service Endpoints & Health Checks

| Check / Endpoint | Status | Verification Output |
|---|---|---|
| `GET http://localhost:8001/health` | **VERIFIED** | Returns `200 {"status":"ok","service":"agent-service"}` |
| `GET http://localhost:8002/health` | **VERIFIED** | Returns `200 {"status":"ok","service":"usage-service"}` |
| `GET http://localhost:8003/health` | **VERIFIED** | Returns `200 {"status":"ok","service":"cost-service"}` |
| `GET http://localhost:8001/health/deps` | **VERIFIED** | Returns `200 {"service":"agent-service","database":"ok","usage-service":"ok","cost-service":"ok"}` |
| `GET http://localhost:8002/health/deps` | **VERIFIED** | Returns `200 {"service":"usage-service","database":"ok","cost-service":"ok"}` |
| `GET http://localhost:8003/health/deps` | **VERIFIED** | Returns `200 {"service":"cost-service","database":"ok"}` |
| `GET http://localhost:8001/` (Dashboard HTML) | **VERIFIED** | Returns `200 OK` (16,823 bytes static single-page application). |
| `GET http://localhost:8001/dashboard/overview` | **VERIFIED** | Aggregates all agents, total requests (6,882+), token sums, and alerts across backend APIs. |

---

## 3. End-to-End Execution & Request Tracing

| Flow Step | Status | Verification Proof |
|---|---|---|
| Request initiation | **VERIFIED** | `POST http://localhost:8001/agents/1/run` returns full structured JSON with `request_id`, `trace`, `agent`, `usage`, `cost`, and `timings_ms`. |
| Synchronous chain | **VERIFIED** | `agent-service` -> `usage-service` -> `cost-service` completed in ~130ms. |
| Logical reference linkage | **VERIFIED** | Returned payload contains matching `usage.usage_id=281` and `cost.cost_id=281`. |
| Distributed `request_id` propagation | **VERIFIED** | Executed `docker compose logs` for `request_id=5737481aca84413ba0370cf6b282ddab`. Appears identically with duration and status in `agent-service`, `usage-service`, and `cost-service` logs. |

---

## 4. Database & SQL Queries

| Database | Status | Verification Details |
|---|---|---|
| PostgreSQL host port publishing | **VERIFIED** | `Test-NetConnection localhost -Port 5432` returns `TcpTestSucceeded: True`. Connectible by pgAdmin 4 using superuser `postgres`. |
| Table schema creation | **VERIFIED** | Verified through `psql -c "\dt"`: `agents` in `agentdb`; `models`, `usage_records` in `usagedb`; `pricing`, `costs`, `alerts` in `costdb`. |
| Seeding reference models & pricing | **VERIFIED** | 5 models registered (`qwen3.5:4b`, `llama3.1:8b`, `gpt-4o-mini`, `claude-haiku`, `gemini-flash`). Pricing rows properly reflect local models at $0.00 and cloud models at illustrative rates. |
| Viva queries execution | **VERIFIED** | All queries in `db/viva_queries.sql` (Q1 through Q15) executed cleanly against actual seeded data. |
| Soft budget & alert deduplication | **VERIFIED** | Soft budgets allow continued execution beyond 100%. `ON CONFLICT DO NOTHING` on `(agent_id, alert_date, level)` prevents spamming the alerts table. |

---

## 5. Failure Handling & Resilience

| Scenario | Status | Behavior Observed |
|---|---|---|
| `cost-service` stopped | **VERIFIED** | Model call still executes and records in `usagedb`. System returns `200 OK` with `cost_id: null` (graceful degradation, usage row preserved). |
| `usage-service` stopped | **VERIFIED** | `agent-service` returns clean `503 Service Unavailable` with explanatory JSON error message. No process crash. |
| Recovery | **VERIFIED** | Restarting `usage-service` restores normal end-to-end operation immediately without restarting `agent-service`. |

---

## 6. Load Test & Resource Utilization

| Concurrency Level | Target Concurrency | Measured Avg Latency | Measured p95 Latency | Measured Throughput | Measured Infra Failures |
|---|---|---|---|---|---|
| W1 | 1 worker | 108.2 ms | 134.6 ms | 9.240 req/s | 0 |
| W2 | 2 workers | 110.2 ms | 134.8 ms | 18.090 req/s | 0 |
| W3 | 4 workers | 109.2 ms | 135.4 ms | 36.340 req/s | 0 |
| W4 | 8 workers | 111.4 ms | 138.9 ms | 70.680 req/s | 0 |
| W5 | 16 workers | 121.4 ms | 150.5 ms | 127.640 req/s | 0 |

- **Total Requests Measured:** 6,000 requests (400 per level x 3 repeats x 5 levels), excluding warmup.
- **Infrastructure Failures at all levels:** **0 (0.0%)**.
- **Docker Resource Monitoring:** 752 raw samples captured in `results/docker_stats_raw.csv`. Peak CPU: `usage-service` (62.52%), `cost-service` (51.01%), `agent-service` (48.75%), `postgres-db` (32.61%). Memory usage remained stable (<106 MiB for Postgres, <72 MiB for services).
- **Artifacts Generated:** 7 PNG graphs generated via `make_graphs.py`; markdown report generated via `make_report.py`.
