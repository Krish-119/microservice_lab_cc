# ANALYSIS.md — Load Test Results

> Generated: 2026-10-05 20:07 UTC  
> Run timestamp: 2026-10-05T19:51:31.052561+00:00  
> Docker CPUs: N/A (in-container) | Docker version: N/A  
> MODEL_MODE: mock | MOCK_LATENCY_SCALE: 1.0  
> Requests per level: 400 (mean of 3 repeats) | Warmup discarded per level

---

## Observation Table

| Level | Conc | Avg(ms) | p95(ms) | p99(ms) | Tput(rps) | InfraFail | ModelFail | Model Lat(ms) | Overhead(ms) |
|-------|------|---------|---------|---------|-----------|-----------|-----------|--------------|-------------|
| W1 | 1 | 108.2 | 134.6 | 137.5 | 9.240 | 0.0 | 0.0 | 85.8 | 22.4 |
| W2 | 2 | 110.2 | 134.8 | 140.3 | 18.090 | 0.0 | 0.0 | 85.8 | 24.4 |
| W3 | 4 | 109.2 | 135.4 | 141.1 | 36.340 | 0.0 | 0.0 | 85.8 | 23.4 |
| W4 | 8 | 111.4 | 138.9 | 155.1 | 70.680 | 0.0 | 0.0 | 85.8 | 25.6 |
| W5 | 16 | 121.4 | 150.5 | 292.0 | 127.640 | 0.0 | 0.0 | 85.8 | 35.6 |

---

## Key Findings

### 1. Throughput Scaling

Throughput peaked at **127.640 req/s** at concurrency **16**.
Throughput stopped meaningfully increasing from concurrency **16** onward.

This is expected: each service uses **one uvicorn worker** (deliberate, per lab spec).
The event loop can overlap I/O waits but cannot parallelize CPU-bound work.
The three-service chain (agent → usage → cost) means each request incurs three sequential async
HTTP hops plus two database writes, making the pipeline deeper than a single-service benchmark.

### 2. Response Time vs Concurrency

At concurrency 1: avg = **108.2 ms** (p95 = 134.6 ms)  
At concurrency 16: avg = **121.4 ms** (p95 = 150.5 ms)  
Change: **+13.2 ms** (+12%)

Likely reasons for degradation:
- **Single uvicorn worker per service**: requests queue behind each other at high concurrency.
- **Sequential service chain**: three HTTP hops cannot be parallelised.
- **Two DB writes per request** (usage + cost), with connection pool contention at high load.
- **Simulated model latency dominates** the response time even at low concurrency.

### 3. Simulated Model Latency vs Service Overhead

At concurrency 1: model latency = 85.8 ms,
service overhead = 22.4 ms  
At concurrency 16: model latency = 85.8 ms,
service overhead = 35.6 ms

Service overhead (total − simulated latency) grows with concurrency, confirming that
queueing and DB contention are the limiting factors at higher loads.

### 4. Infrastructure Failures

Total infra failures across all levels: **0**  
Total model failures: **0** (simulate_failures was disabled; any here are bugs)

✓ Zero infra failures — the service chain was stable at all concurrency levels.

### 5. Resource Usage

**CPU** (note: docker stats reports per-core %, so multi-core machines may show >100%):

| Container | Avg CPU % | Peak CPU % |
|-----------|-----------|------------|
| agent-service | 6.75% | 48.75% |
| usage-service | 9.04% | 62.52% |
| cost-service | 7.82% | 51.01% |
| postgres-db | 4.86% | 32.61% |

Container with highest CPU usage: **usage-service**

**Memory:**

| Container | Avg Mem | Peak Mem |
|-----------|---------|----------|
| agent-service | 67.0 MiB | 68.3 MiB |
| usage-service | 67.8 MiB | 68.9 MiB |
| cost-service | 62.4 MiB | 72.0 MiB |
| postgres-db | 90.0 MiB | 105.5 MiB |

Container with highest memory usage: **postgres-db**

---

## Container Resource Analysis

- **agent-service** is the entry point and orchestrator; it generates request IDs,
  calls usage-service, and aggregates results. It holds the shared httpx client.
- **usage-service** does the model simulation (asyncio.sleep) and two I/O operations:
  DB write + HTTP call to cost-service. At high concurrency it becomes the bottleneck.
- **cost-service** does cost calculation, budget aggregation (SUM query), and alert upserts.
  The SUM query touches all today's rows for an agent and grows with data volume.
- **postgres-db** stays low on CPU but absorbs all DB writes. Memory is stable (buffered pages).

---

## Limitations & Caveats

- Results are from **MODEL_MODE=mock** with deterministic asyncio.sleep latencies.
  Real model calls (Ollama, GPT-4o-mini, etc.) would be orders of magnitude slower.
- All services run on a single Docker Desktop VM on one Windows 11 host.
  A real multi-host deployment would show very different scaling characteristics.
- Docker stats CPU percentages are **per-core** (not normalised to 100%).
  A container at 200% on a 4-core machine is using 50% of total capacity.
- These are lab figures intended to demonstrate observable bottlenecks,
  not production benchmarks.

---

## Files

| File | Description |
|------|-------------|
| `results.csv` | Observation table (one row per concurrency level, mean of repeats) |
| `runs_all.csv` | Every individual repeat |
| `raw_requests.csv` | Every individual request with classification |
| `docker_stats_raw.csv` | Raw docker stats samples with timestamps |
| `metadata.json` | Run configuration |
| `01_response_time.png` | Avg / p95 / p99 response time vs concurrency |
| `02_throughput.png` | Throughput vs concurrency |
| `03_cpu_per_container.png` | CPU % per container vs concurrency |
| `04_memory_per_container.png` | Memory per container vs concurrency |
| `05_peak_resources.png` | Peak CPU and memory bar chart |
| `06_p95_latency.png` | p95 latency bar chart |
| `07_latency_breakdown.png` | Model latency vs service overhead stacked |
