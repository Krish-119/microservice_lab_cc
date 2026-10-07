"""
scripts/seed.py — Idempotent seed for the AI Ops Platform.

Uses the REAL REST APIs (agent-service on localhost:8001).
Run from project root:
    python scripts/seed.py

Skips anything that already exists (name-based dedup).
"""
import asyncio
import random
import sys
import time

import httpx

BASE_URL = "http://localhost:8001"
TIMEOUT = 20.0

# ── Agent definitions ─────────────────────────────────────────────────────────
AGENTS = [
    {
        "name": "Research Agent",
        "description": "Searches and summarizes research papers and articles",
        "owner_team": "Research",
        "model": "gpt-4o-mini",
        "daily_token_budget": 50_000,
        "status": "ACTIVE",
    },
    {
        "name": "Coding Agent",
        "description": "Writes, reviews, and debugs code across multiple languages",
        "owner_team": "Engineering",
        "model": "claude-haiku",
        "daily_token_budget": 80_000,
        "status": "ACTIVE",
    },
    {
        "name": "Support Agent",
        "description": "Handles customer support tickets and FAQ responses",
        "owner_team": "Support",
        "model": "gemini-flash",
        "daily_token_budget": 40_000,
        "status": "ACTIVE",
    },
    {
        "name": "Summarizer",
        "description": "Summarizes long documents, meetings, and reports",
        "owner_team": "Operations",
        "model": "llama3.1:8b",
        "daily_token_budget": 60_000,
        "status": "ACTIVE",
    },
    {
        "name": "Data Analyst",
        "description": "Analyzes datasets and generates insights",
        "owner_team": "Data",
        "model": "qwen3.5:4b",
        "daily_token_budget": 70_000,
        "status": "ACTIVE",
    },
    {
        "name": "Legal Reviewer",
        "description": "Reviews contracts and flags compliance issues",
        "owner_team": "Legal",
        "model": "gpt-4o-mini",
        "daily_token_budget": 30_000,
        "status": "ACTIVE",
    },
    # Load-test agents (large budgets, never hit alert thresholds during load test)
    {
        "name": "loadtest-1",
        "description": "Load-test agent using gpt-4o-mini",
        "owner_team": "Engineering",
        "model": "gpt-4o-mini",
        "daily_token_budget": 10_000_000,
        "status": "ACTIVE",
    },
    {
        "name": "loadtest-2",
        "description": "Load-test agent using llama3.1:8b",
        "owner_team": "Engineering",
        "model": "llama3.1:8b",
        "daily_token_budget": 10_000_000,
        "status": "ACTIVE",
    },
    {
        "name": "loadtest-3",
        "description": "Load-test agent using gemini-flash",
        "owner_team": "Engineering",
        "model": "gemini-flash",
        "daily_token_budget": 10_000_000,
        "status": "ACTIVE",
    },
]

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


async def get_existing_agents(client: httpx.AsyncClient) -> dict:
    r = await client.get(f"{BASE_URL}/agents", timeout=TIMEOUT)
    r.raise_for_status()
    return {a["name"]: a for a in r.json()}


async def create_agent(client: httpx.AsyncClient, agent_def: dict, existing: dict) -> dict:
    name = agent_def["name"]
    if name in existing:
        print(f"  [skip] Agent '{name}' already exists (id={existing[name]['id']})")
        return existing[name]
    r = await client.post(f"{BASE_URL}/agents", json=agent_def, timeout=TIMEOUT)
    if r.status_code == 409:
        print(f"  [skip] Agent '{name}' conflict (race)")
        # refetch
        r2 = await client.get(f"{BASE_URL}/agents", timeout=TIMEOUT)
        return {a["name"]: a for a in r2.json()}.get(name, {})
    r.raise_for_status()
    a = r.json()
    print(f"  [created] Agent '{name}' id={a['id']}")
    return a


async def run_agent(client: httpx.AsyncClient, agent_id: int, agent_name: str,
                    prompt: str, simulate_failures: bool = False) -> dict:
    r = await client.post(
        f"{BASE_URL}/agents/{agent_id}/run",
        json={"prompt": prompt, "source": "seed", "simulate_failures": simulate_failures},
        timeout=TIMEOUT,
    )
    return r.json() if r.status_code == 200 else {"error": r.status_code}


async def main():
    print("=== AI Ops Platform — Seeding ===\n")
    async with httpx.AsyncClient() as client:
        # Wait for service to be ready (use stdlib urllib — works in all PowerShell contexts)
        import urllib.request as _urllib
        ready = False
        for i in range(30):
            try:
                with _urllib.urlopen(f"{BASE_URL}/health", timeout=5) as resp:
                    if resp.status == 200:
                        print("agent-service is up ✓\n")
                        ready = True
                        break
            except Exception:
                pass
            print(f"  Waiting for agent-service… ({i+1}/30)")
            await asyncio.sleep(2)
        if not ready:
            print("ERROR: agent-service not reachable on port 8001")
            sys.exit(1)

        # Create agents
        print("── Creating agents ──")
        existing = await get_existing_agents(client)
        agent_map = {}  # name -> agent dict
        for ag_def in AGENTS:
            ag = await create_agent(client, ag_def, existing)
            agent_map[ag_def["name"]] = ag

        # Refresh existing
        existing = await get_existing_agents(client)

        # ── Historical runs (~250 total) ──────────────────────────────────────
        print("\n── Running historical seed requests (~250 runs) ──")
        core_agents = [name for name in agent_map if not name.startswith("loadtest")]
        total_runs = 0

        for i in range(250):
            ag_name = random.choice(core_agents)
            ag = existing.get(ag_name, agent_map.get(ag_name))
            if not ag:
                continue
            prompt = random.choice(PROMPTS)
            # ~10% simulate_failures for some failed rows
            sim_fail = random.random() < 0.10
            result = await run_agent(client, ag["id"], ag_name, prompt, sim_fail)
            total_runs += 1
            if total_runs % 25 == 0:
                print(f"  … {total_runs}/250 runs done")
            await asyncio.sleep(0.05)  # gentle pacing

        print(f"\n  Total historical runs: {total_runs}")

        # ── Reduce budgets to trigger alerts ──────────────────────────────────
        print("\n── Reducing budgets of 3 agents to trigger WARNING/CRITICAL alerts ──")

        alert_targets = [
            ("Support Agent",  500),    # will go CRITICAL
            ("Legal Reviewer", 800),    # will go CRITICAL
            ("Summarizer",     3000),   # will go WARNING
        ]
        for ag_name, new_budget in alert_targets:
            ag = existing.get(ag_name)
            if not ag:
                print(f"  [skip] {ag_name} not found")
                continue
            r = await client.put(
                f"{BASE_URL}/agents/{ag['id']}",
                json={"daily_token_budget": new_budget},
                timeout=TIMEOUT,
            )
            print(f"  Set {ag_name} budget → {new_budget} tokens (status={r.status_code})")

        # Refresh
        existing = await get_existing_agents(client)

        # Run a few more requests on the reduced-budget agents to trigger alerts
        print("\n── Running extra requests to trigger budget alerts ──")
        alert_agents = ["Support Agent", "Legal Reviewer", "Summarizer"]
        for _ in range(30):
            ag_name = random.choice(alert_agents)
            ag = existing.get(ag_name)
            if not ag:
                continue
            prompt = random.choice(PROMPTS)
            result = await run_agent(client, ag["id"], ag_name, prompt, False)
            await asyncio.sleep(0.05)
        print("  Done")

        print("\n=== Seeding complete ✓ ===")
        print("Tip: check /health/deps on all services and view the dashboard at http://localhost:8001/")


if __name__ == "__main__":
    asyncio.run(main())
