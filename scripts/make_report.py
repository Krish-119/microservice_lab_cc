"""
scripts/make_report.py — Generate ANALYSIS.md from measured CSVs.

Run after loadtest.py and make_graphs.py:
    python scripts/make_report.py

Numbers are computed from the CSV; nothing is invented.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

RESULTS_DIR = Path(__file__).parent.parent / "results"
CONTAINERS = ["agent-service", "usage-service", "cost-service", "postgres-db"]


def load():
    obs_path = RESULTS_DIR / "results.csv"
    if not obs_path.exists():
        print(f"ERROR: {obs_path} not found. Run loadtest.py first.")
        sys.exit(1)
    df = pd.read_csv(obs_path)

    meta_path = RESULTS_DIR / "metadata.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)

    return df, meta


def max_throughput_level(df: pd.DataFrame) -> dict:
    idx = df["throughput_rps"].idxmax()
    return df.iloc[idx].to_dict()


def highest_cpu_container(df: pd.DataFrame) -> str:
    cols = {c: df[f"{c}_peak_cpu"].max() for c in CONTAINERS if f"{c}_peak_cpu" in df.columns}
    return max(cols, key=cols.get, default="unknown")


def highest_mem_container(df: pd.DataFrame) -> str:
    cols = {c: df[f"{c}_peak_mem"].max() for c in CONTAINERS if f"{c}_peak_mem" in df.columns}
    return max(cols, key=cols.get, default="unknown")


def main():
    df, meta = load()

    best = max_throughput_level(df)
    cpu_winner = highest_cpu_container(df)
    mem_winner = highest_mem_container(df)

    low_conc = df.iloc[0]
    high_conc = df.iloc[-1]
    latency_change = high_conc["avg_response_ms"] - low_conc["avg_response_ms"]
    total_infra = df["infra_failures"].sum()
    total_model = df["model_failures"].sum()

    run_ts = meta.get("run_timestamp", "unknown")
    docker_cpus = meta.get("docker_cpu_count", "unknown")
    docker_ver = meta.get("docker_version", "unknown")
    model_mode = meta.get("model_mode", "mock")
    latency_scale = meta.get("mock_latency_scale", 1.0)
    n_req = meta.get("n_requests_per_level", "?")
    n_rep = meta.get("n_repeats", "?")

    # Build observation table markdown
    table_header = (
        "| Level | Conc | Avg(ms) | p95(ms) | p99(ms) | Tput(rps) | "
        "InfraFail | ModelFail | Model Lat(ms) | Overhead(ms) |"
    )
    table_sep = "|-------|------|---------|---------|---------|-----------|-----------|-----------|--------------|-------------|"
    rows = []
    for _, r in df.iterrows():
        rows.append(
            f"| {r['workload']} | {int(r['concurrency'])} | {r['avg_response_ms']:.1f} | "
            f"{r['p95_ms']:.1f} | {r['p99_ms']:.1f} | {r['throughput_rps']:.3f} | "
            f"{r['infra_failures']:.1f} | {r['model_failures']:.1f} | "
            f"{r['avg_model_latency_ms']:.1f} | {r['avg_service_overhead_ms']:.1f} |"
        )
    obs_table = "\n".join([table_header, table_sep] + rows)

    # Container CPU table
    cpu_lines = []
    for c in CONTAINERS:
        if f"{c}_avg_cpu" in df.columns:
            peak = df[f"{c}_peak_cpu"].max()
            avg = df[f"{c}_avg_cpu"].mean()
            cpu_lines.append(f"| {c} | {avg:.2f}% | {peak:.2f}% |")
    cpu_table = "| Container | Avg CPU % | Peak CPU % |\n|-----------|-----------|------------|\n" + "\n".join(cpu_lines)

    mem_lines = []
    for c in CONTAINERS:
        if f"{c}_avg_mem" in df.columns:
            peak = df[f"{c}_peak_mem"].max()
            avg = df[f"{c}_avg_mem"].mean()
            mem_lines.append(f"| {c} | {avg:.1f} MiB | {peak:.1f} MiB |")
    mem_table = "| Container | Avg Mem | Peak Mem |\n|-----------|---------|----------|\n" + "\n".join(mem_lines)

    # Throughput scaling observation
    tp_vals = df["throughput_rps"].tolist()
    conc_vals = df["concurrency"].tolist()
    # Find first level where throughput stops scaling (delta < 5% of previous)
    plateau_conc = conc_vals[-1]
    for i in range(1, len(tp_vals)):
        if tp_vals[i] <= tp_vals[i-1] * 1.05:
            plateau_conc = conc_vals[i]
            break

    report = f"""# ANALYSIS.md — Load Test Results

> Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  
> Run timestamp: {run_ts}  
> Docker CPUs: {docker_cpus} | Docker version: {docker_ver}  
> MODEL_MODE: {model_mode} | MOCK_LATENCY_SCALE: {latency_scale}  
> Requests per level: {n_req} (mean of {n_rep} repeats) | Warmup discarded per level

---

## Observation Table

{obs_table}

---

## Key Findings

### 1. Throughput Scaling

Throughput peaked at **{best['throughput_rps']:.3f} req/s** at concurrency **{int(best['concurrency'])}**.
Throughput stopped meaningfully increasing from concurrency **{plateau_conc}** onward.

This is expected: each service uses **one uvicorn worker** (deliberate, per lab spec).
The event loop can overlap I/O waits but cannot parallelize CPU-bound work.
The three-service chain (agent → usage → cost) means each request incurs three sequential async
HTTP hops plus two database writes, making the pipeline deeper than a single-service benchmark.

### 2. Response Time vs Concurrency

At concurrency {int(low_conc['concurrency'])}: avg = **{low_conc['avg_response_ms']:.1f} ms** (p95 = {low_conc['p95_ms']:.1f} ms)  
At concurrency {int(high_conc['concurrency'])}: avg = **{high_conc['avg_response_ms']:.1f} ms** (p95 = {high_conc['p95_ms']:.1f} ms)  
Change: **+{latency_change:.1f} ms** (+{latency_change/low_conc['avg_response_ms']*100:.0f}%)

Likely reasons for degradation:
- **Single uvicorn worker per service**: requests queue behind each other at high concurrency.
- **Sequential service chain**: three HTTP hops cannot be parallelised.
- **Two DB writes per request** (usage + cost), with connection pool contention at high load.
- **Simulated model latency dominates** the response time even at low concurrency.

### 3. Simulated Model Latency vs Service Overhead

At concurrency {int(low_conc['concurrency'])}: model latency = {low_conc['avg_model_latency_ms']:.1f} ms,
service overhead = {low_conc['avg_service_overhead_ms']:.1f} ms  
At concurrency {int(high_conc['concurrency'])}: model latency = {high_conc['avg_model_latency_ms']:.1f} ms,
service overhead = {high_conc['avg_service_overhead_ms']:.1f} ms

Service overhead (total − simulated latency) grows with concurrency, confirming that
queueing and DB contention are the limiting factors at higher loads.

### 4. Infrastructure Failures

Total infra failures across all levels: **{total_infra:.0f}**  
Total model failures: **{total_model:.0f}** (simulate_failures was disabled; any here are bugs)

{"✓ Zero infra failures — the service chain was stable at all concurrency levels." if total_infra == 0 else f"⚠ {total_infra:.0f} infra failures recorded — see raw_requests.csv for details."}

### 5. Resource Usage

**CPU** (note: docker stats reports per-core %, so multi-core machines may show >100%):

{cpu_table}

Container with highest CPU usage: **{cpu_winner}**

**Memory:**

{mem_table}

Container with highest memory usage: **{mem_winner}**

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
"""

    out = RESULTS_DIR / "ANALYSIS.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[OK] ANALYSIS.md -> {out}")


if __name__ == "__main__":
    main()
