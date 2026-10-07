# AI Agent & Model Operations Platform

A complete microservices-based management, observability, and operations platform for organizations managing multiple AI agents and language models. Tracks agent registry, model assignments, token usage, execution latency, success/failure rates, dollar-cost estimations, and soft daily token budgets with automated alerts.

---

## Architecture Overview

The system strictly adheres to an independent three-microservice architecture with a dedicated database-per-service isolation model.

```
                         [pgAdmin 4 on Windows Host]
                                      │
                               (localhost:5432)
                                      ▼
                       ┌─────────────────────────────┐
                       │    postgres-db (Port 5432)   │
                       │  ├─ agentdb (agentuser)     │
                       │  ├─ usagedb (usageuser)     │
                       │  └─ costdb  (costuser)      │
                       └──────────────┬──────────────┘
                                      │
           ┌──────────────────────────┼──────────────────────────┐
           │ (asyncpg pool)           │ (asyncpg pool)           │ (asyncpg pool)
           ▼                          ▼                          ▼
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
│    agent-service     │──►│    usage-service     │──►│     cost-service     │
│  (Port 8001 -> 8000) │   │  (Port 8002 -> 8000) │   │  (Port 8003 -> 8000) │
│  - Agent Registry    │   │  - Model Execution   │   │  - Pricing Engine    │
│  - Orchestration     │   │  - Usage Tracking    │   │  - Cost Calculation  │
│  - HTML UI Dashboard │   │  - Mock / Ollama     │   │  - Budget Alerts     │
└──────────────────────┘   └──────────────────────┘   └──────────────────────┘
           ▲
           │ HTTP (POST /agents/{id}/run)
       [ Client ]
```

### Request Flow
1. **Client** calls `agent-service` (`POST /agents/{id}/run`).
2. `agent-service` verifies the agent exists and is `ACTIVE`, generates a unique `request_id`, and propagates it via the `X-Request-ID` header.
3. `agent-service` forwards the execution request to `usage-service` (`POST http://usage-service:8000/usage/record`).
4. `usage-service` executes or simulates model invocation, records the token metrics and latency in `usagedb.usage_records`, and calls `cost-service` (`POST http://cost-service:8000/cost/calculate`).
5. `cost-service` calculates dollar costs based on model pricing, records the row in `costdb.costs`, checks the agent's accumulated daily token usage for today (Asia/Kolkata timezone), and upserts a budget alert (`WARNING` at >=80%, `CRITICAL` at >=100%) in `costdb.alerts`.
6. The combined JSON trace containing `request_id`, execution trace, agent details, usage record, and cost details returns to the client.

---

## Services & Ports

| Service | Host Port | Container Port | Purpose | Swagger UI |
|---|---|---|---|---|
| **agent-service** | `8001` | `8000` | Registry, Orchestration, Dashboard | `http://localhost:8001/docs` |
| **usage-service** | `8002` | `8000` | Model execution, Usage recording | `http://localhost:8002/docs` |
| **cost-service** | `8003` | `8000` | Cost calculation, Budget alerts | `http://localhost:8003/docs` |
| **postgres-db** | `5432` | `5432` | Multi-database storage | Connectible via pgAdmin |

---

## Database Design: Database-Per-Service

A deliberate engineering design decision in this project is strict **database-per-service isolation**:
- `agentdb` is exclusively owned by `agentuser`.
- `usagedb` is exclusively owned by `usageuser`.
- `costdb` is exclusively owned by `costuser`.

PostgreSQL does not support cross-database foreign keys or joins. Thus, references between services (such as `usage_id` inside `costdb.costs`, or `agent_id` inside `usagedb.usage_records`) are stored as **logical IDs**. Real foreign keys are enforced strictly within each database boundary (e.g., `usage_records.model_id -> models.id` and `costs.pricing_id -> pricing.id`).

---

## Getting Started

### Prerequisites
- Docker Desktop with WSL2 on Windows 11
- PowerShell 7+ or Windows PowerShell 5.1
- Python 3.10+ (for local scripts)

### Setup & Run
1. Clone the repository and navigate to the directory:
   ```powershell
   cd C:\Users\ariha\Documents\ai-ops-platform
   ```
2. Verify `.env` matches your preferred credentials (defaults match `.env.example`).
3. Start the entire platform from clean state:
   ```powershell
   docker compose down -v
   docker compose up --build -d
   ```
4. Verify all containers are healthy:
   ```powershell
   docker compose ps
   ```

### Accessing the Web Dashboard
Open your browser and navigate to:
```
http://localhost:8001/
```
The vanilla JS + Chart.js dashboard displays:
- Agent table with live status and direct "Run Agent" trigger modal.
- Daily budget progress bars.
- Real-time token consumption and estimated spend summaries.
- Cost per model and tokens per agent visual charts.
- Active system budget alerts.

---

## pgAdmin 4 Connection (Windows)

PostgreSQL runs inside Docker and exposes port `5432` to the Windows host. To inspect the databases via pgAdmin 4 installed on Windows:

1. Open **pgAdmin 4**.
2. Right-click **Servers** > **Register** > **Server...**
3. Configure the **General** tab:
   - **Name**: `AI Ops Platform (Docker)`
4. Configure the **Connection** tab:
   - **Host name/address**: `localhost`
   - **Port**: `5432` (or the configured `POSTGRES_HOST_PORT`)
   - **Maintenance database**: `postgres`
   - **Username**: `postgres`
   - **Password**: `supersecret` (from `.env`)
5. Click **Save**.
6. Expand **Databases** to access `agentdb`, `usagedb`, and `costdb`.

---

## Seed Data & Demo Scripts

### Seeding Historical Data
To populate 9 agents, 250 historical runs, and trigger budget alerts:
```powershell
docker exec agent-service python /app/seed_docker.py
```

### Automated Multi-Checkpoint Demo
Run the PowerShell demo covering health, docker status, request tracing, and analytics:
```powershell
.\scripts\demo.ps1
```

### Database Query Verification (Viva)
Run the SQL verification script:
```powershell
.\scripts\db_demo.ps1
```
Or execute the 15 queries in `db/viva_queries.sql` directly using `psql` or pgAdmin.

---

## Performance Benchmark & Load Testing

The platform was benchmarked targeting the full end-to-end endpoint (`POST /agents/{id}/run`) with concurrency levels `W1` (1), `W2` (2), `W3` (4), `W4` (8), and `W5` (16) using single-worker uvicorn services.

### Actually Measured Results

| Concurrency | Avg Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Throughput (req/s) | Infra Failures |
|---|---|---|---|---|---|
| **1** | 108.2 | 134.6 | 137.5 | 9.24 | **0** |
| **2** | 110.2 | 134.8 | 140.3 | 18.09 | **0** |
| **4** | 109.2 | 135.4 | 141.1 | 36.34 | **0** |
| **8** | 111.4 | 138.9 | 155.1 | 70.68 | **0** |
| **16** | 121.4 | 150.5 | 292.0 | 127.64 | **0** |

- **Total Requests Measured**: 6,000 requests (excluding warmup).
- **Total Infrastructure Failures**: **0**.
- **Peak CPU Observed**: `usage-service` (62.5%), `cost-service` (51.0%), `agent-service` (48.8%), `postgres-db` (32.6%).
- Generated graphs and complete analysis report are located in `results/`.

---

## Viva Q&A Cheat Sheet

**Q: Why are there 3 separate databases in a single PostgreSQL container instead of 3 PostgreSQL containers?**  
*A: Running 3 separate containers adds unnecessary resource overhead (~300MB RAM each) in a lab environment. A single PostgreSQL instance hosting 3 distinct databases with independent users and permissions achieves identical logical separation and architectural decoupling without wasting system memory.*

**Q: How do you handle joins across databases?**  
*A: We deliberately do not perform cross-database joins. Each service is fully autonomous and owns its data. Data required across services is passed via REST payloads, and cross-service linkages use logical identifiers (`agent_id`, `usage_id`).*

**Q: Why use single-worker Uvicorn?**  
*A: In production, multiple workers scale throughput across CPU cores. In a college lab, single-worker uvicorn makes concurrency limits, queueing delays, and event loop bottlenecks transparent, measurable, and easy to explain in graphs.*

**Q: What happens if cost-service fails mid-chain?**  
*A: The system implements graceful degradation. The usage record is preserved in `usagedb`, while the response returns `cost: null`. If `usage-service` goes down, `agent-service` returns a clean `503 Service Unavailable`.*

**Q: How are budget alert duplicate spams prevented?**  
*A: `costdb.alerts` has a unique constraint `UNIQUE (agent_id, alert_date, level)`. Upserts use PostgreSQL's `ON CONFLICT DO NOTHING`, guaranteeing that an agent exceeding its budget only triggers one alert per level per calendar day.*
