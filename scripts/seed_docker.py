"""
seed_docker.py - runs INSIDE the agent-service container via docker exec.
Uses the container-internal URL (localhost:8000 = agent-service itself).
"""
import asyncio, random, sys, json
import urllib.request, urllib.error
import http.client

BASE_URL = "http://localhost:8000"
TIMEOUT = 20

AGENTS = [
    {"name":"Research Agent","description":"Searches and summarizes research papers and articles","owner_team":"Research","model":"gpt-4o-mini","daily_token_budget":50000,"status":"ACTIVE"},
    {"name":"Coding Agent","description":"Writes reviews and debugs code across multiple languages","owner_team":"Engineering","model":"claude-haiku","daily_token_budget":80000,"status":"ACTIVE"},
    {"name":"Support Agent","description":"Handles customer support tickets and FAQ responses","owner_team":"Support","model":"gemini-flash","daily_token_budget":40000,"status":"ACTIVE"},
    {"name":"Summarizer","description":"Summarizes long documents meetings and reports","owner_team":"Operations","model":"llama3.1:8b","daily_token_budget":60000,"status":"ACTIVE"},
    {"name":"Data Analyst","description":"Analyzes datasets and generates insights","owner_team":"Data","model":"qwen3.5:4b","daily_token_budget":70000,"status":"ACTIVE"},
    {"name":"Legal Reviewer","description":"Reviews contracts and flags compliance issues","owner_team":"Legal","model":"gpt-4o-mini","daily_token_budget":30000,"status":"ACTIVE"},
    {"name":"loadtest-1","description":"Load-test agent using gpt-4o-mini","owner_team":"Engineering","model":"gpt-4o-mini","daily_token_budget":10000000,"status":"ACTIVE"},
    {"name":"loadtest-2","description":"Load-test agent using llama3.1:8b","owner_team":"Engineering","model":"llama3.1:8b","daily_token_budget":10000000,"status":"ACTIVE"},
    {"name":"loadtest-3","description":"Load-test agent using gemini-flash","owner_team":"Engineering","model":"gemini-flash","daily_token_budget":10000000,"status":"ACTIVE"},
]

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

def http_request(method, path, body=None):
    """Simple synchronous HTTP using stdlib only."""
    conn = http.client.HTTPConnection("localhost", 8000, timeout=TIMEOUT)
    headers = {"Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    text = resp.read().decode()
    conn.close()
    return resp.status, json.loads(text) if text else {}

def main():
    print("=== AI Ops Platform - Seeding (in-container) ===\n")

    # Wait for service
    for i in range(30):
        try:
            status, data = http_request("GET", "/health")
            if status == 200:
                print(f"agent-service is up (status={status}) OK\n")
                break
        except Exception as e:
            print(f"  Waiting ({i+1}/30): {e}")
            import time; time.sleep(2)
    else:
        print("ERROR: agent-service not reachable"); sys.exit(1)

    # Get existing agents
    _, existing_list = http_request("GET", "/agents")
    existing = {a["name"]: a for a in existing_list}
    print(f"Existing agents: {list(existing.keys())}")

    # Create agents
    print("\n-- Creating agents --")
    agent_map = {}
    for ag in AGENTS:
        name = ag["name"]
        if name in existing:
            print(f"  [skip] {name} (id={existing[name]['id']})")
            agent_map[name] = existing[name]
        else:
            status, resp = http_request("POST", "/agents", ag)
            if status in (200, 201):
                print(f"  [created] {name} id={resp['id']}")
                agent_map[name] = resp
            elif status == 409:
                print(f"  [conflict] {name} - re-fetching")
                _, ex2 = http_request("GET", "/agents")
                em2 = {a["name"]: a for a in ex2}
                agent_map[name] = em2.get(name, {})
            else:
                print(f"  [ERROR] {name} status={status} resp={resp}")

    # Refresh existing
    _, all_agents = http_request("GET", "/agents")
    existing = {a["name"]: a for a in all_agents}
    core_agents = [n for n in agent_map if not n.startswith("loadtest")]

    # 250 historical runs
    print("\n-- Running 250 historical seed requests --")
    import time, random as rnd
    rnd.seed(42)
    for i in range(250):
        ag_name = rnd.choice(core_agents)
        ag = existing.get(ag_name, agent_map.get(ag_name))
        if not ag or not ag.get("id"):
            continue
        prompt = rnd.choice(PROMPTS)
        sim_fail = rnd.random() < 0.10
        body = {"prompt": prompt, "source": "seed", "simulate_failures": sim_fail}
        try:
            status, resp = http_request("POST", f"/agents/{ag['id']}/run", body)
        except Exception as e:
            print(f"  run error at {i}: {e}")
        if (i+1) % 50 == 0:
            print(f"  ... {i+1}/250 done")
        time.sleep(0.05)

    # Reduce budgets to trigger alerts
    print("\n-- Reducing budgets to trigger alerts --")
    alert_targets = [("Support Agent", 500), ("Legal Reviewer", 800), ("Summarizer", 3000)]
    for ag_name, budget in alert_targets:
        ag = existing.get(ag_name)
        if ag:
            status, _ = http_request("PUT", f"/agents/{ag['id']}", {"daily_token_budget": budget})
            print(f"  {ag_name} -> budget={budget} (status={status})")

    # Refresh after budget changes
    _, all_agents = http_request("GET", "/agents")
    existing = {a["name"]: a for a in all_agents}

    # Extra runs on tight-budget agents to trigger alerts
    print("\n-- Extra runs to trigger budget alerts --")
    alert_agents = ["Support Agent", "Legal Reviewer", "Summarizer"]
    for i in range(30):
        ag_name = rnd.choice(alert_agents)
        ag = existing.get(ag_name)
        if not ag or not ag.get("id"):
            continue
        body = {"prompt": rnd.choice(PROMPTS), "source": "seed", "simulate_failures": False}
        try:
            http_request("POST", f"/agents/{ag['id']}/run", body)
        except Exception as e:
            print(f"  alert run error: {e}")
        time.sleep(0.05)

    print("\n=== Seeding complete ===")

if __name__ == "__main__":
    main()
