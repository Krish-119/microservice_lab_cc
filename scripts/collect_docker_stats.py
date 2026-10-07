"""
collect_docker_stats.py - Host-side docker stats sampler.
Runs on host and writes CSV to results/docker_stats_raw.csv
Stops when results/STOP_STATS file appears or after MAX_SECONDS.
"""
import csv
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONTAINERS = ["agent-service", "usage-service", "cost-service", "postgres-db"]
RESULTS_DIR = Path(__file__).parent.parent / "results"
OUT_FILE = RESULTS_DIR / "docker_stats_raw.csv"
STOP_FILE = RESULTS_DIR / "STOP_STATS"
MAX_SECONDS = int(os.environ.get("STATS_MAX_SECONDS", "3600"))


def parse_bytes(s):
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


def sample():
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
            mem_used = mem_str.split("/")[0].strip() if "/" in mem_str else mem_str
            mem_mib = parse_bytes(mem_used)
            rows.append({
                "timestamp": ts,
                "container": name.strip(),
                "cpu_pct": cpu_pct,
                "mem_mib": round(mem_mib, 2),
                "label": os.environ.get("STATS_LABEL", "running"),
            })
        return rows
    except Exception as e:
        print(f"stats error: {e}", flush=True)
        return []


def main():
    STOP_FILE.unlink(missing_ok=True)
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    print(f"Stats collector starting -> {OUT_FILE}", flush=True)

    with open(OUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "container", "cpu_pct", "mem_mib", "label"])
        writer.writeheader()

        t_start = time.time()
        n_samples = 0

        while True:
            if STOP_FILE.exists():
                print(f"STOP file detected. Collected {n_samples} samples.", flush=True)
                break
            if time.time() - t_start > MAX_SECONDS:
                print(f"Max time {MAX_SECONDS}s reached. Stopping.", flush=True)
                break

            rows = sample()
            if rows:
                writer.writerows(rows)
                f.flush()
                n_samples += len(rows)

            if n_samples % 40 == 0 and n_samples > 0:
                print(f"  {n_samples} samples collected...", flush=True)

            # docker stats --no-stream itself takes ~1-2s; no extra sleep needed


if __name__ == "__main__":
    main()
