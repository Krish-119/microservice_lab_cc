-- =============================================================
-- VIVA QUERIES - AI Ops Platform
-- Each query labelled with the DATABASE it must run in
-- Run via: docker exec postgres-db psql -U <user> -d <db> -c "<query>"
-- Or paste in pgAdmin Query Tool connected to the correct database
-- =============================================================

-- ============================================================
-- DATABASE: agentdb
-- ============================================================

-- Q1: All agents with their assigned model and status
-- \c agentdb
SELECT id, name, model, owner_team, status, daily_token_budget, created_at
FROM agents
ORDER BY id;

-- Q2: Agents grouped by status (ACTIVE vs INACTIVE)
-- \c agentdb
SELECT status, COUNT(*) AS agent_count
FROM agents
GROUP BY status
ORDER BY status;

-- Q3: Agents ordered by daily_token_budget (largest first)
-- \c agentdb
SELECT name, model, owner_team, daily_token_budget, status
FROM agents
ORDER BY daily_token_budget DESC;

-- ============================================================
-- DATABASE: usagedb
-- ============================================================

-- Q4: Total tokens consumed per agent (all time)
-- \c usagedb
SELECT agent_id, agent_name,
       SUM(input_tokens)  AS total_input_tokens,
       SUM(output_tokens) AS total_output_tokens,
       SUM(total_tokens)  AS total_tokens,
       COUNT(*)           AS request_count
FROM usage_records
WHERE status = 'success'
GROUP BY agent_id, agent_name
ORDER BY total_tokens DESC;

-- Q5: Total usage per model with JOIN to models table
-- \c usagedb
SELECT m.name AS model_name, m.provider, m.is_local,
       COUNT(ur.id)           AS request_count,
       SUM(ur.total_tokens)   AS total_tokens,
       AVG(ur.latency_ms)     AS avg_latency_ms
FROM usage_records ur
JOIN models m ON ur.model_id = m.id
WHERE ur.status = 'success'
GROUP BY m.name, m.provider, m.is_local
ORDER BY total_tokens DESC NULLS LAST;

-- Q6: Average latency per model (ms)
-- \c usagedb
SELECT m.name AS model_name,
       ROUND(AVG(ur.latency_ms), 2) AS avg_latency_ms,
       MIN(ur.latency_ms)           AS min_latency_ms,
       MAX(ur.latency_ms)           AS max_latency_ms,
       COUNT(*)                     AS request_count
FROM usage_records ur
JOIN models m ON ur.model_id = m.id
WHERE ur.status = 'success'
GROUP BY m.name
ORDER BY avg_latency_ms;

-- Q7: Failed vs successful calls (all models, all agents)
-- \c usagedb
SELECT status,
       COUNT(*)         AS call_count,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM usage_records
GROUP BY status
ORDER BY status;

-- Q8: Usage breakdown by source (seed / demo / loadtest)
-- \c usagedb
SELECT source,
       COUNT(*)               AS request_count,
       SUM(total_tokens)      AS total_tokens,
       AVG(latency_ms)        AS avg_latency_ms
FROM usage_records
GROUP BY source
ORDER BY request_count DESC;

-- ============================================================
-- DATABASE: costdb
-- ============================================================

-- Q9: Cost per model (JOIN costs + pricing)
-- \c costdb
SELECT p.model AS model_name,
       COUNT(c.id)            AS request_count,
       SUM(c.total_tokens)    AS total_tokens,
       ROUND(SUM(c.cost_usd)::numeric, 6) AS total_cost_usd,
       ROUND(AVG(c.cost_usd)::numeric, 6) AS avg_cost_per_request
FROM costs c
JOIN pricing p ON c.pricing_id = p.id
GROUP BY p.model
ORDER BY total_cost_usd DESC;

-- Q10: Cost per agent (all time)
-- \c costdb
SELECT agent_id, agent_name,
       COUNT(*)               AS request_count,
       SUM(total_tokens)      AS total_tokens,
       ROUND(SUM(cost_usd)::numeric, 6) AS total_cost_usd
FROM costs
GROUP BY agent_id, agent_name
ORDER BY total_cost_usd DESC;

-- Q11: Agents currently above 80% of their daily token budget
-- (read from alerts table which stores budget_used_pct at time of alert)
-- \c costdb
SELECT agent_id, agent_name, level, budget_used_pct,
       tokens_today, daily_token_budget, alert_date
FROM alerts
WHERE budget_used_pct >= 80
ORDER BY budget_used_pct DESC;

-- Q12: All alerts (recent first)
-- \c costdb
SELECT agent_id, agent_name, level, budget_used_pct,
       tokens_today, daily_token_budget, alert_date, message, created_at
FROM alerts
ORDER BY created_at DESC
LIMIT 20;

-- Q13: Alert count by level (WARNING vs CRITICAL)
-- \c costdb
SELECT level, COUNT(*) AS alert_count
FROM alerts
GROUP BY level
ORDER BY level;

-- Q14: Highest-cost request (single call)
-- \c costdb
SELECT c.request_id, c.agent_name, p.model,
       c.total_tokens, ROUND(c.cost_usd::numeric, 6) AS cost_usd, c.created_at
FROM costs c
JOIN pricing p ON c.pricing_id = p.id
ORDER BY c.cost_usd DESC
LIMIT 5;

-- Q15: Daily cost trend (last 7 days)
-- \c costdb
SELECT DATE(created_at AT TIME ZONE 'Asia/Kolkata') AS cost_date,
       COUNT(*)               AS requests,
       SUM(total_tokens)      AS total_tokens,
       ROUND(SUM(cost_usd)::numeric, 6) AS total_cost_usd
FROM costs
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY cost_date
ORDER BY cost_date DESC;
