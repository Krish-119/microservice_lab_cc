"""
loadtest_docker.py - Runs INSIDE Docker network via docker exec on a temp python container.
Targets http://agent-service:8000 (internal Docker name).

This is equivalent to loadtest.py but works around Windows background-task
networking limitations where localhost port-forwarding is unavailable.

Usage (from host):
  docker run --rm --network ai-ops-net \
    -v "$(pwd)/scripts:/scripts" \
    -v "$(pwd)/results:/results" \
    python:3.11-slim \
    bash -c "pip install httpx -q && python /scripts/loadtest_docker.py [--quick] [--requests N] [--repeats N]"
"""
import argparse
import asyncio
import csv
import json
import os
import sys
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL = os.environ.get("AGENT_SERVICE_URL", "http://agent-service:8000")
RESULTS_DIR = Path("/results")
RESULTS_DIR.mkdir(exist_ok=True)

CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]

PROMPTS = [
    "Summarize the key findings of this research paper.",
    "Write a Python function to sort a list of dictionaries.",
    "What is the refund policy for our premium plan?",
    "Summarize last quarters earnings call transcript.",
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


async def make_request(client, agent_id, prompt):
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}/agents/{agent_id}/run",
            json={"prompt": prompt, "source": "loadtest", "simulate_failures": False},
            timeout=30.0,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if r.status_code not in (200, 201):
            return {"status": "INFRA_FAILURE", "response_ms": elapsed_ms, "model_latency_ms": 0, "error": f"HTTP {r.status_code}"}
        body = r.json()
        usage = body.get("usage", {})
        cost = body.get("cost", {})
        usage_id = usage.get("usage_id")
        cost_id = cost.get("cost_id")
        usage_status = usage.get("status", "")
        model_latency = usage.get("latency_ms", 0)
        if usage_id is None or cost_id is None:
            return {"status": "INFRA_FAILURE", "response_ms": elapsed_ms, "model_latency_ms": model_latency, "error": "incomplete chain"}
        if usage_status == "failed":
            return {"status": "MODEL_FAILURE", "response_ms": elapsed_ms, "model_latency_ms": model_latency, "error": usage.get("error_message", "")}
        return {"status": "SUCCESS", "response_ms": elapsed_ms, "model_latency_ms": model_latency, "error": ""}
    except httpx.TimeoutException:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"status": "INFRA_FAILURE", "response_ms": elapsed_ms, "model_latency_ms": 0, "error": "timeout"}
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"status": "INFRA_FAILURE", "response_ms": elapsed_ms, "model_latency_ms": 0, "error": str(exc)}


async def run_concurrent_level(agents, concurrency, n_requests):
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


def _pct(data, p):
    if not data:
        return 0.0
    data = sorted(data)
    idx = max(0, int(len(data) * p / 100) - 1)
    return data[min(idx, len(data) - 1)]


def compute_stats(results, wall_s):
    all_ms = [r["response_ms"] for r in results]
    success = [r for r in results if r["status"] == "SUCCESS"]
    model_fail = [r for r in results if r["status"] == "MODEL_FAILURE"]
    infra_fail = [r for r in results if r["status"] == "INFRA_FAILURE"]
    model_latencies = [r["model_latency_ms"] for r in success]
    throughput = len(success) / wall_s if wall_s > 0 else 0
    avg_model_lat = sum(model_latencies) / len(model_latencies) if model_latencies else 0
    avg_resp = sum(all_ms) / len(all_ms) if all_ms else 0
    return {
        "total": len(results),
        "success": len(success),
        "model_failures": len(model_fail),
        "infra_failures": len(infra_fail),
        "avg_ms": round(avg_resp, 1),
        "p50_ms": round(_pct(all_ms, 50), 1),
        "p95_ms": round(_pct(all_ms, 95), 1),
        "p99_ms": round(_pct(all_ms, 99), 1),
        "max_ms": round(max(all_ms) if all_ms else 0, 1),
        "throughput_rps": round(throughput, 3),
        "avg_model_latency_ms": round(avg_model_lat, 1),
        "avg_service_overhead_ms": round(avg_resp - avg_model_lat, 1),
        "wall_clock_s": round(wall_s, 2),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    n_requests = 50 if args.quick else args.requests
    n_repeats = 1 if args.quick else args.repeats
    n_warmup = 10 if args.quick else args.warmup

    print(f"=== AI Ops Load Test ===")
    print(f"target={BASE_URL} levels={CONCURRENCY_LEVELS} requests={n_requests} repeats={n_repeats} warmup={n_warmup}\n")

    # Get load-test agents
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE_URL}/agents", timeout=10.0)
        all_agents = r.json()
    lt_agents = [a for a in all_agents if a["name"].startswith("loadtest")]
    if not lt_agents:
        print("ERROR: no loadtest-* agents found. Run seed first.")
        sys.exit(1)
    print(f"Load-test agents: {[a['name'] for a in lt_agents]}\n")

    ts_start = datetime.now(timezone.utc).isoformat()
    observation_rows = []
    all_runs = []
    raw_requests = []

    for concurrency in CONCURRENCY_LEVELS:
        label = f"W{CONCURRENCY_LEVELS.index(concurrency)+1}"
        print(f"\n{'='*55}")
        print(f"Level {label}: concurrency={concurrency}")
        repeat_results = []

        for rep in range(1, n_repeats + 1):
            print(f"  Repeat {rep}/{n_repeats}")
            print(f"    Warmup ({n_warmup})...", end=" ", flush=True)
            await run_concurrent_level(lt_agents, concurrency, n_warmup)
            print("done")
            print(f"    Measuring ({n_requests})...", end=" ", flush=True)

            t_wall = time.perf_counter()
            results = await run_concurrent_level(lt_agents, concurrency, n_requests)
            wall_s = time.perf_counter() - t_wall

            stats = compute_stats(results, wall_s)
            print(f"done — {stats['success']} OK | {stats['infra_failures']} infra-fail | avg={stats['avg_ms']}ms p95={stats['p95_ms']}ms tput={stats['throughput_rps']}rps")

            if stats['infra_failures'] > 0:
                fails = [r for r in results if r['status'] == 'INFRA_FAILURE'][:3]
                print(f"    !! Infra failures: {[f['error'] for f in fails]}")

            run_entry = {"level": label, "concurrency": concurrency, "repeat": rep, **stats}
            all_runs.append(run_entry)
            repeat_results.append(run_entry)

            for req in results:
                raw_requests.append({"level": label, "concurrency": concurrency, "repeat": rep, **req})

        avg = lambda key: round(sum(r[key] for r in repeat_results) / len(repeat_results), 2)
        obs = {
            "workload": label, "concurrency": concurrency, "repeats": n_repeats,
            "avg_response_ms": avg("avg_ms"), "p95_ms": avg("p95_ms"),
            "p99_ms": avg("p99_ms"), "max_ms": avg("max_ms"),
            "throughput_rps": avg("throughput_rps"),
            "infra_failures": avg("infra_failures"), "model_failures": avg("model_failures"),
            "avg_model_latency_ms": avg("avg_model_latency_ms"),
            "avg_service_overhead_ms": avg("avg_service_overhead_ms"),
            # docker stats: not available from inside container, filled with 0
            **{f"{c}_{m}": 0.0 for c in ["agent-service","usage-service","cost-service","postgres-db"] for m in ["avg_cpu","peak_cpu","avg_mem","peak_mem"]},
        }
        observation_rows.append(obs)
        print(f"  {label} summary: avg={obs['avg_response_ms']}ms p95={obs['p95_ms']}ms tput={obs['throughput_rps']}rps infra_fail={obs['infra_failures']}")

        if concurrency < CONCURRENCY_LEVELS[-1]:
            print("  Sleeping 10s...")
            await asyncio.sleep(10)

    # Write CSVs
    if observation_rows:
        with open(RESULTS_DIR / "results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=observation_rows[0].keys()); w.writeheader(); w.writerows(observation_rows)
    if all_runs:
        with open(RESULTS_DIR / "runs_all.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_runs[0].keys()); w.writeheader(); w.writerows(all_runs)
    if raw_requests:
        with open(RESULTS_DIR / "raw_requests.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=raw_requests[0].keys()); w.writeheader(); w.writerows(raw_requests)

    meta = {
        "run_timestamp": ts_start, "docker_cpu_count": "N/A (in-container)",
        "docker_version": "N/A", "model_mode": "mock", "mock_latency_scale": 1.0,
        "concurrency_levels": CONCURRENCY_LEVELS, "n_requests_per_level": n_requests,
        "n_repeats": n_repeats, "n_warmup": n_warmup, "quick_mode": args.quick,
    }
    with open(RESULTS_DIR / "metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n=== Load test complete ===")
    print(f"\n{'Level':<6} {'Conc':>4} {'Avg(ms)':>8} {'p95(ms)':>8} {'Tput(rps)':>10} {'InfraFail':>10}")
    for obs in observation_rows:
        print(f"{obs['workload']:<6} {obs['concurrency']:>4} {obs['avg_response_ms']:>8.1f} {obs['p95_ms']:>8.1f} {obs['throughput_rps']:>10.3f} {obs['infra_failures']:>10.1f}")

if __name__ == "__main__":
    asyncio.run(main())
