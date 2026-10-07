"""
scripts/make_graphs.py — Generate performance graphs from load test results.

Run after loadtest.py:
    python scripts/make_graphs.py

Produces 7 PNG files in results/
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

RESULTS_DIR = Path(__file__).parent.parent / "results"
CONTAINERS = ["agent-service", "usage-service", "cost-service", "postgres-db"]
COLORS = ["#6366f1", "#22d3ee", "#f59e0b", "#22c55e"]


def load_data():
    obs_path = RESULTS_DIR / "results.csv"
    if not obs_path.exists():
        print(f"ERROR: {obs_path} not found. Run loadtest.py first.")
        sys.exit(1)
    df = pd.read_csv(obs_path)
    return df


def style_ax(ax, title: str, xlabel: str, ylabel: str):
    ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=9)


def save(fig, name: str):
    out = RESULTS_DIR / name
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out}")


def main():
    print("=== Generating graphs ===\n")
    df = load_data()

    conc = df["concurrency"].tolist()

    # 1. Concurrency vs Avg Response Time
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(conc, df["avg_response_ms"], marker="o", color=COLORS[0], linewidth=2, label="Avg")
    ax.plot(conc, df["p95_ms"], marker="s", color=COLORS[1], linewidth=2, linestyle="--", label="p95")
    ax.plot(conc, df["p99_ms"], marker="^", color=COLORS[2], linewidth=2, linestyle=":", label="p99")
    ax.legend()
    ax.set_xticks(conc)
    style_ax(ax, "Concurrency vs Response Time", "Concurrency (workers)", "Response Time (ms)")
    save(fig, "01_response_time.png")

    # 2. Concurrency vs Throughput
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(conc, df["throughput_rps"], marker="o", color=COLORS[0], linewidth=2)
    ax.fill_between(conc, df["throughput_rps"], alpha=0.15, color=COLORS[0])
    ax.set_xticks(conc)
    style_ax(ax, "Concurrency vs Throughput", "Concurrency (workers)", "Throughput (requests/sec)")
    save(fig, "02_throughput.png")

    # 3. CPU % per container vs concurrency
    fig, ax = plt.subplots(figsize=(9, 5))
    for i, c in enumerate(CONTAINERS):
        col = f"{c}_avg_cpu"
        if col in df.columns:
            ax.plot(conc, df[col], marker="o", color=COLORS[i % len(COLORS)],
                    linewidth=2, label=c)
    ax.legend(loc="upper left")
    ax.set_xticks(conc)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
    style_ax(ax, "CPU Usage per Container vs Concurrency",
             "Concurrency (workers)", "Avg CPU % (per-core, may exceed 100%)")
    save(fig, "03_cpu_per_container.png")

    # 4. Memory per container vs concurrency
    fig, ax = plt.subplots(figsize=(9, 5))
    for i, c in enumerate(CONTAINERS):
        col = f"{c}_avg_mem"
        if col in df.columns:
            ax.plot(conc, df[col], marker="o", color=COLORS[i % len(COLORS)],
                    linewidth=2, label=c)
    ax.legend(loc="upper left")
    ax.set_xticks(conc)
    style_ax(ax, "Memory Usage per Container vs Concurrency",
             "Concurrency (workers)", "Avg Memory (MiB)")
    save(fig, "04_memory_per_container.png")

    # 5. Bar chart: which container used the most CPU and memory
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    peak_cpu = {c: df[f"{c}_peak_cpu"].max() if f"{c}_peak_cpu" in df.columns else 0 for c in CONTAINERS}
    peak_mem = {c: df[f"{c}_peak_mem"].max() if f"{c}_peak_mem" in df.columns else 0 for c in CONTAINERS}

    ax = axes[0]
    ax.bar(list(peak_cpu.keys()), list(peak_cpu.values()), color=COLORS)
    ax.set_title("Peak CPU % per Container", fontweight="bold")
    ax.set_ylabel("Peak CPU %")
    ax.tick_params(axis="x", rotation=20)
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    ax.bar(list(peak_mem.keys()), list(peak_mem.values()), color=COLORS)
    ax.set_title("Peak Memory (MiB) per Container", fontweight="bold")
    ax.set_ylabel("Peak Memory (MiB)")
    ax.tick_params(axis="x", rotation=20)
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)

    save(fig, "05_peak_resources.png")

    # 6. p95 latency vs concurrency
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar([str(c) for c in conc], df["p95_ms"], color=COLORS[0], alpha=0.85, width=0.5)
    style_ax(ax, "p95 Response Latency vs Concurrency",
             "Concurrency (workers)", "p95 Response Time (ms)")
    save(fig, "06_p95_latency.png")

    # 7. Simulated model latency vs service overhead (stacked bar)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar([str(c) for c in conc], df["avg_model_latency_ms"],
           label="Simulated Model Latency", color=COLORS[0], alpha=0.85, width=0.5)
    overhead = df["avg_service_overhead_ms"].clip(lower=0)
    ax.bar([str(c) for c in conc], overhead, bottom=df["avg_model_latency_ms"],
           label="Service Overhead", color=COLORS[1], alpha=0.85, width=0.5)
    ax.legend()
    style_ax(ax, "Model Latency vs Service Overhead (stacked)",
             "Concurrency (workers)", "Time (ms)")
    save(fig, "07_latency_breakdown.png")

    print(f"\n[OK] 7 graphs saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
