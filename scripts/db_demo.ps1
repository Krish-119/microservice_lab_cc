## AI Ops Platform - Database Demo
## Runs key SQL queries via docker exec as a fallback if pgAdmin is not available.
## Usage: .\scripts\db_demo.ps1

param(
    [string]$PgUser = "postgres",
    [string]$PgPass = "supersecret"
)

$env:PGPASSWORD = $PgPass

function Run-Query {
    param([string]$DB, [string]$User, [string]$Query, [string]$Label)
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    Write-Host "Database: $DB" -ForegroundColor DarkGray
    docker exec -e PGPASSWORD=$PgPass postgres-db `
        psql -U $User -d $DB -c $Query
}

Write-Host "=== AI Ops Platform - Database Demo ===" -ForegroundColor Green
Write-Host "Connecting via docker exec to postgres-db container`n"

# Q1: All agents
Run-Query -DB "agentdb" -User "agentuser" `
    -Query "SELECT id, name, model, owner_team, status, daily_token_budget FROM agents ORDER BY id;" `
    -Label "Q1: All Agents (agentdb)"

# Q2: Agent count by status
Run-Query -DB "agentdb" -User "agentuser" `
    -Query "SELECT status, COUNT(*) FROM agents GROUP BY status;" `
    -Label "Q2: Agents by Status (agentdb)"

# Q3: All models
Run-Query -DB "usagedb" -User "usageuser" `
    -Query "SELECT id, name, provider, is_local FROM models ORDER BY id;" `
    -Label "Q3: Model Registry (usagedb)"

# Q4: Total tokens per agent
Run-Query -DB "usagedb" -User "usageuser" `
    -Query "SELECT agent_name, SUM(total_tokens) AS total_tokens, COUNT(*) AS requests FROM usage_records WHERE status='success' GROUP BY agent_name ORDER BY total_tokens DESC;" `
    -Label "Q4: Total Tokens per Agent (usagedb)"

# Q5: Usage per model with JOIN
Run-Query -DB "usagedb" -User "usageuser" `
    -Query "SELECT m.name, m.provider, COUNT(ur.id) AS requests, SUM(ur.total_tokens) AS tokens FROM usage_records ur JOIN models m ON ur.model_id = m.id WHERE ur.status='success' GROUP BY m.name, m.provider ORDER BY tokens DESC NULLS LAST;" `
    -Label "Q5: Usage per Model with JOIN (usagedb)"

# Q6: Failed vs success
Run-Query -DB "usagedb" -User "usageuser" `
    -Query "SELECT status, COUNT(*) FROM usage_records GROUP BY status;" `
    -Label "Q6: Success vs Failed (usagedb)"

# Q7: Usage by source
Run-Query -DB "usagedb" -User "usageuser" `
    -Query "SELECT source, COUNT(*) AS requests, SUM(total_tokens) AS tokens FROM usage_records GROUP BY source ORDER BY requests DESC;" `
    -Label "Q7: Usage by Source (usagedb)"

# Q8: Pricing table
Run-Query -DB "costdb" -User "costuser" `
    -Query "SELECT model, input_price_per_1k, output_price_per_1k, currency FROM pricing ORDER BY model;" `
    -Label "Q8: Pricing Table (costdb)"

# Q9: Cost per model
Run-Query -DB "costdb" -User "costuser" `
    -Query "SELECT p.model, COUNT(c.id) AS requests, SUM(c.total_tokens) AS tokens, ROUND(SUM(c.cost_usd)::numeric, 6) AS total_cost_usd FROM costs c JOIN pricing p ON c.pricing_id = p.id GROUP BY p.model ORDER BY total_cost_usd DESC;" `
    -Label "Q9: Cost per Model (costdb)"

# Q10: Cost per agent
Run-Query -DB "costdb" -User "costuser" `
    -Query "SELECT agent_name, COUNT(*) AS requests, SUM(total_tokens) AS tokens, ROUND(SUM(cost_usd)::numeric, 6) AS total_cost_usd FROM costs GROUP BY agent_name ORDER BY total_cost_usd DESC;" `
    -Label "Q10: Cost per Agent (costdb)"

# Q11: Recent alerts
Run-Query -DB "costdb" -User "costuser" `
    -Query "SELECT agent_name, level, budget_used_pct, tokens_today, daily_token_budget, alert_date FROM alerts ORDER BY created_at DESC LIMIT 10;" `
    -Label "Q11: Recent Alerts (costdb)"

# Q12: Alert count by level
Run-Query -DB "costdb" -User "costuser" `
    -Query "SELECT level, COUNT(*) FROM alerts GROUP BY level ORDER BY level;" `
    -Label "Q12: Alert Count by Level (costdb)"

Write-Host "`n=== Database Demo Complete ===" -ForegroundColor Green
