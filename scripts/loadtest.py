"""
scripts/loadtest.py — Load test for the AI Ops Platform.

Usage:
    python scripts/loadtest.py                  # full test
    python scripts/loadtest.py --quick          # 50 requests, 1 repeat per level
    python scripts/loadtest.py --requests 200   # custom request count per level
    python scripts/loadtest.py --repeats 2      # 2 repeats per concurrency level

Targets POST http://localhost:8001/agents/{id}/run (full chain).
Never enables simulate_failures.
"""
import argparse
import asyncio
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import httpx

BASE_URL = "http://localhost:8001"
RESULTS_DIR = Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]

PROMPTS = [
    "Summarize the key findings of this research paper.",
    "Write a Python function to sort a list of dictionaries.",
    "What is the refund policy for our premium plan?",
    "Summarize last quarter's earnings call transcript.",
    "Analyze the sales trends for Q3 2025.",
    "Review this NDA clause for compliance risks.",
    "Explain the concept of gradient descent.",
    "Generate unit tests for this authentication module.",
    "What are common causes of churn in SaaS products?",
    "Draft an executive summary for the attached report.",
    "Debug this SQL query returning incorrect results.",
    "Create a data visualization plan for this dataset.",
    "Translate this legal clause to plain English.",
    "What is the average latency in our current architecture?",
    "Suggest improvements to our onboarding flow.",
    "Generate a weekly status update template.",
    "Analyze competitor pricing strategies.",
    "Review this API documentation for completeness.",
    "What are the risks of this contract clause?",
    "Help me write a product requirements document.",
]


# ── Docker stats monitoring ───────────────────────────────────────────────────

CONTAINERS = ["agent-service", "usage-service", "cost-service", "postgres-db"]


def _parse_bytes(s: str) -> float:
    """Convert docker stats memory string to MiB."""
    s = s.strip()
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", s, re.IGNORECASE)
    if not m:
        return 0.0
    val, unit = float(m.group(1)), m.group(2).upper()
    if "GIB" in unit or "GB" in unit:
        return val * 1024
    if "MIB" in unit or "MB" in unit:
        return val
    if "KIB" in unit or "KB" in unit:
        return val / 1024
    return val


def _sample_docker_stats() -> List[dict]:
    """Single snapshot of docker stats for monitored containers."""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}},{{.CPUPerc}},{{.MemUsage}}"] + CONTAINERS,
            capture_output=True, text=True, timeout=15,
        )
        rows = []
        ts = datetime.now(timezone.utc).isoformat()
        for line in result.stdout.strip().splitlines():
            parts = line.split(",", 2)
            if len(parts) < 3:
                continue
            name, cpu_str, mem_str = parts
            cpu_pct = float(cpu_str.replace("%", "").strip() or 0)
            mem_used = mem_str.split("/")[0].strip() if "/" in mem_str else mem_str.strip()
            mem_mib = _parse_bytes(mem_used)
            rows.append({
                "timestamp": ts,
                "container": name.strip(),
                "cpu_pct": cpu_pct,
                "mem_mib": mem_mib,
            })
        return rows
    except Exception as e:
        print(f"  [warn] docker stats error: {e}")
        return []


class StatsMonitor:
    def __init__(self):
        self.samples: List[dict] = []
        self._running = False
        self._task = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task:
            await self._task

    async def _loop(self):
        while self._running:
            rows = await asyncio.get_event_loop().run_in_executor(None, _sample_docker_stats)
            self.samples.extend(rows)
            await asyncio.sleep(0)  # yield; docker stats call takes 1-2s itself


# ── Single request ────────────────────────────────────────────────────────────

async def make_request(
    client: httpx.AsyncClient,
    agent_id: int,
    prompt: str,
) -> dict:
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}/agents/{agent_id}/run",
            json={"prompt": prompt, "source": "loadtest", "simulate_failures": False},
            timeout=30.0,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if r.status_code not in (200, 201):
            return {
                "status": "INFRA_FAILURE",
                "response_ms": elapsed_ms,
                "model_latency_ms": 0,
                "error": f"HTTP {r.status_code}",
            }

        body = r.json()

        # Classify
        usage = body.get("usage", {})
        cost = body.get("cost", {})
        usage_id = usage.get("usage_id")
        cost_id = cost.get("cost_id")
        usage_status = usage.get("status", "")
        model_latency = usage.get("latency_ms", 0)

        if usage_id is None or cost_id is None:
            return {
                "status": "INFRA_FAILURE",
                "response_ms": elapsed_ms,
                "model_latency_ms": model_latency,
                "error": "incomplete chain (missing usage_id or cost_id)",
            }

        if usage_status == "failed":
            return {
                "status": "MODEL_FAILURE",
                "response_ms": elapsed_ms,
                "model_latency_ms": model_latency,
                "error": usage.get("error_message", ""),
            }

        return {
            "status": "SUCCESS",
            "response_ms": elapsed_ms,
            "model_latency_ms": model_latency,
            "error": "",
        }

    except httpx.TimeoutException:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"status": "INFRA_FAILURE", "response_ms": elapsed_ms, "model_latency_ms": 0, "error": "timeout"}
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"status": "INFRA_FAILURE", "response_ms": elapsed_ms, "model_latency_ms": 0, "error": str(exc)}


# ── Worker pool (closed-loop, N workers) ──────────────────────────────────────

async def run_level(
    agents: List[dict],
    concurrency: int,
    n_requests: int,
    prompt_idx: int = 0,
) -> List[dict]:
    """Run n_requests requests with N concurrent workers (closed-loop)."""
    results = []
    semaphore = asyncio.Semaphore(concurrency)
    prompt_counter = [prompt_idx]

    async def worker():
        agent_id = agents[len(results) % len(agents)]["id"]
        prompt = PROMPTS[prompt_counter[0] % len(PROMPTS)]
        prompt_counter[0] += 1
        async with semaphore:
            async with httpx.AsyncClient(timeout=30.0) as client:
                return await make_request(client, agent_id, prompt)

    tasks = [asyncio.create_task(worker()) for _ in range(n_requests)]
    for i, coro in enumerate(asyncio.as_completed(tasks)):
        r = await coro
        results.append(r)
    return results


async def run_concurrent_level(
    agents: List[dict],
    concurrency: int,
    n_requests: int,
) -> List[dict]:
    """Closed-loop: N workers each issuing requests back-to-back."""
    results = []
    lock = asyncio.Lock()
    counter = [0]

    async def worker_loop():
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                async with lock:
                    idx = counter[0]
                    if idx >= n_requests:
                        return
                    counter[0] += 1
                agent = agents[idx % len(agents)]
                prompt = PROMPTS[idx % len(PROMPTS)]
                r = await make_request(client, agent["id"], prompt)
                async with lock:
                    results.append(r)

    workers = [asyncio.create_task(worker_loop()) for _ in range(concurrency)]
    await asyncio.gather(*workers)
    return results


# ── Statistics ────────────────────────────────────────────────────────────────

def _percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    data = sorted(data)
    idx = max(0, int(len(data) * p / 100) - 1)
    return data[min(idx, len(data) - 1)]


def compute_stats(results: List[dict], wall_clock_s: float) -> dict:
    all_ms = [r["response_ms"] for r in results]
    success = [r for r in results if r["status"] == "SUCCESS"]
    model_fail = [r for r in results if r["status"] == "MODEL_FAILURE"]
    infra_fail = [r for r in results if r["status"] == "INFRA_FAILURE"]
    success_ms = [r["response_ms"] for r in success]
    model_latencies = [r["model_latency_ms"] for r in success]

    throughput = len(success) / wall_clock_s if wall_clock_s > 0 else 0

    avg_model_lat = sum(model_latencies) / len(model_latencies) if model_latencies else 0
    avg_resp = sum(all_ms) / len(all_ms) if all_ms else 0
    overhead = avg_resp - avg_model_lat

    return {
        "total": len(results),
        "success": len(success),
        "model_failures": len(model_fail),
        "infra_failures": len(infra_fail),
        "avg_ms": round(avg_resp, 1),
        "p50_ms": round(_percentile(all_ms, 50), 1),
        "p95_ms": round(_percentile(all_ms, 95), 1),
        "p99_ms": round(_percentile(all_ms, 99), 1),
        "max_ms": round(max(all_ms) if all_ms else 0, 1),
        "throughput_rps": round(throughput, 3),
        "avg_model_latency_ms": round(avg_model_lat, 1),
        "avg_service_overhead_ms": round(overhead, 1),
        "wall_clock_s": round(wall_clock_s, 2),
    }


def aggregate_container_stats(samples: List[dict], containers: List[str]) -> dict:
    """Avg and peak CPU/mem per container from samples."""
    out = {}
    for c in containers:
        rows = [s for s in samples if s["container"] == c]
        if not rows:
            out[c] = {"avg_cpu": 0, "peak_cpu": 0, "avg_mem_mib": 0, "peak_mem_mib": 0}
            continue
        cpus = [r["cpu_pct"] for r in rows]
        mems = [r["mem_mib"] for r in rows]
        out[c] = {
            "avg_cpu": round(sum(cpus) / len(cpus), 2),
            "peak_cpu": round(max(cpus), 2),
            "avg_mem_mib": round(sum(mems) / len(mems), 2),
            "peak_mem_mib": round(max(mems), 2),
        }
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="AI Ops Platform Load Test")
    parser.add_argument("--quick", action="store_true", help="50 requests, 1 repeat (smoke test)")
    parser.add_argument("--requests", type=int, default=400, help="Measured requests per level per repeat")
    parser.add_argument("--warmup", type=int, default=20, help="Warmup requests per level")
    parser.add_argument("--repeats", type=int, default=3, help="Repeats per concurrency level")
    args = parser.parse_args()

    if args.quick:
        n_requests = 50
        n_repeats = 1
        n_warmup = 10
    else:
        n_requests = args.requests
        n_repeats = args.repeats
        n_warmup = args.warmup

    print(f"=== AI Ops Load Test ===")
    print(f"Levels: {CONCURRENCY_LEVELS}, requests={n_requests}, repeats={n_repeats}, warmup={n_warmup}\n")

    # Get load-test agents
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{BASE_URL}/agents", timeout=10.0)
            r.raise_for_status()
            all_agents = r.json()
        except Exception as e:
            print(f"ERROR: Cannot reach agent-service: {e}")
            sys.exit(1)

    lt_agents = [a for a in all_agents if a["name"].startswith("loadtest")]
    if not lt_agents:
        print("ERROR: No loadtest-* agents found. Run seed.py first.")
        sys.exit(1)
    print(f"Load-test agents: {[a['name'] for a in lt_agents]}\n")

    # Docker info
    try:
        r2 = subprocess.run(
            ["docker", "info", "--format", "{{.NCPU}}"],
            capture_output=True, text=True, timeout=10,
        )
        n_cpu = int(r2.stdout.strip() or 0)
    except Exception:
        n_cpu = 0

    try:
        r3 = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=10,
        )
        docker_version = r3.stdout.strip()
    except Exception:
        docker_version = "unknown"

    model_mode = "mock"  # read from env if available

    # Collect idle baseline (10 seconds)
    print("Recording idle baseline (10 seconds)…")
    monitor = StatsMonitor()
    await monitor.start()
    await asyncio.sleep(10)
    idle_samples = list(monitor.samples)
    monitor.samples.clear()

    idle_stats = aggregate_container_stats(idle_samples, CONTAINERS)
    print(f"  Idle samples collected: {len(idle_samples)}")

    # Results storage
    observation_rows = []
    all_runs = []
    raw_requests = []
    docker_stats_raw = list(idle_samples)
    docker_stats_raw_tagged = [{"level": "idle", **s} for s in idle_samples]

    ts_start = datetime.now(timezone.utc).isoformat()

    for concurrency in CONCURRENCY_LEVELS:
        label = f"W{CONCURRENCY_LEVELS.index(concurrency)+1}"
        print(f"\n{'='*60}")
        print(f"Level {label}: concurrency={concurrency}")

        repeat_results = []

        for rep in range(1, n_repeats + 1):
            print(f"  Repeat {rep}/{n_repeats}")

            # Warmup
            print(f"    Warmup ({n_warmup} requests)…")
            await run_concurrent_level(lt_agents, concurrency, n_warmup)

            # Measured run
            print(f"    Measuring ({n_requests} requests)…")
            monitor.samples.clear()
            await monitor.start()

            t_wall_start = time.perf_counter()
            results = await run_concurrent_level(lt_agents, concurrency, n_requests)
            wall_s = time.perf_counter() - t_wall_start

            await monitor.stop()
            monitor = StatsMonitor()  # reset for next level

            level_stats_raw = list(monitor.samples) if hasattr(monitor, 'samples') else []
            stats = compute_stats(results, wall_s)
            container_stats = aggregate_container_stats(level_stats_raw, CONTAINERS)

            print(f"    Results: {stats['success']} OK | {stats['infra_failures']} infra-fail | "
                  f"avg={stats['avg_ms']}ms p95={stats['p95_ms']}ms throughput={stats['throughput_rps']}rps")

            if stats['infra_failures'] > 0:
                failures = [r for r in results if r['status'] == 'INFRA_FAILURE'][:3]
                print(f"    ⚠ Infra failures: {[f['error'] for f in failures]}")

            run_entry = {
                "level": label,
                "concurrency": concurrency,
                "repeat": rep,
                **stats,
                **{f"{c}_avg_cpu": container_stats.get(c, {}).get("avg_cpu", 0) for c in CONTAINERS},
                **{f"{c}_peak_cpu": container_stats.get(c, {}).get("peak_cpu", 0) for c in CONTAINERS},
                **{f"{c}_avg_mem": container_stats.get(c, {}).get("avg_mem_mib", 0) for c in CONTAINERS},
                **{f"{c}_peak_mem": container_stats.get(c, {}).get("peak_mem_mib", 0) for c in CONTAINERS},
            }
            all_runs.append(run_entry)
            repeat_results.append(run_entry)

            # Raw requests
            for req in results:
                raw_requests.append({
                    "level": label,
                    "concurrency": concurrency,
                    "repeat": rep,
                    **req,
                })

            # Tag docker stats
            for s in level_stats_raw:
                docker_stats_raw_tagged.append({"level": label, "repeat": rep, **s})

        # Aggregate repeats for observation table
        avg = lambda key: round(sum(r[key] for r in repeat_results) / len(repeat_results), 2)

        obs = {
            "workload": label,
            "concurrency": concurrency,
            "repeats": n_repeats,
            "avg_response_ms": avg("avg_ms"),
            "p95_ms": avg("p95_ms"),
            "p99_ms": avg("p99_ms"),
            "max_ms": avg("max_ms"),
            "throughput_rps": avg("throughput_rps"),
            "infra_failures": avg("infra_failures"),
            "model_failures": avg("model_failures"),
            "avg_model_latency_ms": avg("avg_model_latency_ms"),
            "avg_service_overhead_ms": avg("avg_service_overhead_ms"),
        }
        for c in CONTAINERS:
            obs[f"{c}_avg_cpu"] = avg(f"{c}_avg_cpu")
            obs[f"{c}_peak_cpu"] = avg(f"{c}_peak_cpu")
            obs[f"{c}_avg_mem"] = avg(f"{c}_avg_mem")
            obs[f"{c}_peak_mem"] = avg(f"{c}_peak_mem")

        observation_rows.append(obs)
        print(f"\n  {label} summary: avg={obs['avg_response_ms']}ms p95={obs['p95_ms']}ms "
              f"tput={obs['throughput_rps']}rps infra_fail={obs['infra_failures']}")

        if concurrency < CONCURRENCY_LEVELS[-1]:
            print("  Sleeping 10s between levels…")
            await asyncio.sleep(10)

    # ── Write results ──────────────────────────────────────────────────────────
    print("\n\nWriting results…")

    # Observation table
    obs_path = RESULTS_DIR / "results.csv"
    if observation_rows:
        with open(obs_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=observation_rows[0].keys())
            w.writeheader()
            w.writerows(observation_rows)
        print(f"  results.csv → {obs_path}")

    # All runs
    runs_path = RESULTS_DIR / "runs_all.csv"
    if all_runs:
        with open(runs_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_runs[0].keys())
            w.writeheader()
            w.writerows(all_runs)
        print(f"  runs_all.csv → {runs_path}")

    # Raw requests
    raw_path = RESULTS_DIR / "raw_requests.csv"
    if raw_requests:
        with open(raw_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=raw_requests[0].keys())
            w.writeheader()
            w.writerows(raw_requests)
        print(f"  raw_requests.csv → {raw_path}")

    # Docker stats
    dsr_path = RESULTS_DIR / "docker_stats_raw.csv"
    if docker_stats_raw_tagged:
        with open(dsr_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=docker_stats_raw_tagged[0].keys())
            w.writeheader()
            w.writerows(docker_stats_raw_tagged)
        print(f"  docker_stats_raw.csv → {dsr_path}")

    # Metadata
    meta = {
        "run_timestamp": ts_start,
        "docker_cpu_count": n_cpu,
        "docker_version": docker_version,
        "model_mode": model_mode,
        "mock_latency_scale": float(os.environ.get("MOCK_LATENCY_SCALE", "1.0")),
        "concurrency_levels": CONCURRENCY_LEVELS,
        "n_requests_per_level": n_requests,
        "n_repeats": n_repeats,
        "n_warmup": n_warmup,
        "quick_mode": args.quick,
    }
    meta_path = RESULTS_DIR / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  metadata.json → {meta_path}")

    print("\n=== Load test complete ✓ ===")
    print(f"\nObservation table:")
    print(f"{'Level':<6} {'Conc':>4} {'Avg(ms)':>8} {'p95(ms)':>8} {'Tput(rps)':>10} {'InfraFail':>10} {'ModelFail':>10}")
    for obs in observation_rows:
        print(f"{obs['workload']:<6} {obs['concurrency']:>4} {obs['avg_response_ms']:>8.1f} "
              f"{obs['p95_ms']:>8.1f} {obs['throughput_rps']:>10.3f} "
              f"{obs['infra_failures']:>10.1f} {obs['model_failures']:>10.1f}")


if __name__ == "__main__":
    asyncio.run(main())
