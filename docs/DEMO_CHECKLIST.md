# Demo Checklist — AI Agent & Model Operations Platform

Step-by-step checklist for lab demonstration and viva evaluation.

---

### 1. Verify Platform & Containers
- Run `docker compose ps`
- Confirm all 4 containers (`agent-service`, `usage-service`, `cost-service`, `postgres-db`) are **Up (healthy)**.
- Confirm only 3 application images exist (`docker images | Select-String "lab/"`).

### 2. Service Health & Swagger Docs
- Show individual health checks:
  - `curl.exe http://localhost:8001/health`
  - `curl.exe http://localhost:8002/health`
  - `curl.exe http://localhost:8003/health`
- Show dependency health check demonstrating service-to-service reachability:
  - `curl.exe http://localhost:8001/health/deps`
- Open Swagger UI for any service:
  - `http://localhost:8001/docs` (agent-service)
  - `http://localhost:8002/docs` (usage-service)
  - `http://localhost:8003/docs` (cost-service)

### 3. Web Dashboard
- Open browser to `http://localhost:8001/`
- Demonstrate:
  - Agent table with live status and budget progress bars.
  - Overall system metrics (total requests, tokens, spend, failed calls).
  - Cost per model and tokens per agent visual charts.
  - Recent CRITICAL budget alerts.
  - Click **"Run"** on any agent in the table to execute an ad-hoc prompt via modal.

### 4. Database Isolation & pgAdmin
- Open **pgAdmin 4** on Windows:
  - Connect to `localhost:5432`, Maintenance DB `postgres`, Username `postgres`, Password from `.env` (`supersecret`).
  - Demonstrate 3 isolated databases: `agentdb` (owner `agentuser`), `usagedb` (owner `usageuser`), `costdb` (owner `costuser`).
  - Highlight: No cross-database foreign keys or joins (architectural database-per-service pattern).
- Or run automated DB verification:
  - `.\scripts\db_demo.ps1` (runs 12 core SQL queries across all 3 DBs).

### 5. Execute End-to-End Request & Request Tracing
- Send a run request from PowerShell:
  ```powershell
  $body = '{"prompt":"Explain gradient descent in detail","source":"demo"}'
  Invoke-RestMethod -Method Post -Uri "http://localhost:8001/agents/1/run" -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 4
  ```
- Demonstrate in returned JSON:
  - Full synchronous trace: `["agent-service", "usage-service", "cost-service"]`.
  - Non-null `usage.usage_id` and `cost.cost_id` (matching logical reference).
  - Calculated `cost_usd`, `tokens_today`, and `budget_used_pct`.
- Demonstrate distributed tracing across containers:
  - Grab the returned `request_id`.
  - Run `docker compose logs | Select-String "<request_id>"` to prove the exact same `request_id` passed through all 3 services.

### 6. Budget Alerts & Soft Enforcement
- Show that budget enforcement is **soft** (runs never fail even at >100% budget).
- Query `costdb.alerts` to show alerts triggered at >=80% (`WARNING`) and >=100% (`CRITICAL`):
  ```powershell
  docker exec postgres-db psql -U costuser -d costdb -c "SELECT agent_name, level, budget_used_pct, alert_date FROM alerts;"
  ```
- Mention deduplication: `UNIQUE (agent_id, alert_date, level)` with `ON CONFLICT DO NOTHING` prevents alert spam.

### 7. Benchmark Results & Graph Presentation
- Open the 7 generated charts in `results/`:
  - `01_response_time.png` — Latency vs concurrency.
  - `02_throughput.png` — Throughput scaling from 9 to 127 req/s.
  - `03_cpu_per_container.png` & `04_memory_per_container.png` — Resource profiles.
  - `05_peak_resources.png` — Peak CPU / memory by container.
  - `06_p95_latency.png` — Tail latency distribution.
  - `07_latency_breakdown.png` — Model execution vs service overhead breakdown.
- Explain the 1 → 2 → 4 → 8 → 16 scaling dynamics:
  - Single uvicorn worker per service makes bottlenecks transparent and explainable.
  - Near-linear throughput scaling through concurrency 16 as async event loop overlaps I/O.
  - Response time increases modestly from ~108ms to ~121ms avg due to event-loop scheduling and database pool contention.
  - **Zero infrastructure failures** across all 6,000 evaluated benchmark requests.
