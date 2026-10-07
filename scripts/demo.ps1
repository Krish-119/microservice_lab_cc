## AI Ops Platform - Demo Script
## Maps to the 5 checkpoints in the lab manual.
## Usage: .\scripts\demo.ps1

param(
    [string]$AgentId = "1"   # Override the default agent ID for demos
)

function Pause-Demo {
    param([string]$Msg = "Press Enter to continue...")
    Write-Host "`n$Msg" -ForegroundColor Yellow
    Read-Host
}

function Print-Section {
    param([string]$Title)
    Write-Host "`n" -NoNewline
    Write-Host ("=" * 65) -ForegroundColor Magenta
    Write-Host "  $Title" -ForegroundColor Magenta
    Write-Host ("=" * 65) -ForegroundColor Magenta
}

Write-Host @"

  ╔══════════════════════════════════════════════════════╗
  ║   AI Agent & Model Operations Platform — DEMO        ║
  ║   Maps to Checkpoints 1–5                            ║
  ╚══════════════════════════════════════════════════════╝

"@ -ForegroundColor Cyan

# ── CHECKPOINT 1: Health & API ──────────────────────────────────────────────
Print-Section "CHECKPOINT 1: Health & individual service APIs"

Write-Host "`n--- agent-service (port 8001) ---" -ForegroundColor Cyan
Invoke-RestMethod -Uri "http://localhost:8001/health" | ConvertTo-Json
Write-Host "`n/health/deps:"
Invoke-RestMethod -Uri "http://localhost:8001/health/deps" | ConvertTo-Json
Write-Host "`nGET /agents (all agents):"
Invoke-RestMethod -Uri "http://localhost:8001/agents" | ConvertTo-Json -Depth 3

Pause-Demo "--- usage-service (port 8002) ---"
Invoke-RestMethod -Uri "http://localhost:8002/health" | ConvertTo-Json
Write-Host "`n/health/deps:"
Invoke-RestMethod -Uri "http://localhost:8002/health/deps" | ConvertTo-Json
Write-Host "`nGET /models:"
Invoke-RestMethod -Uri "http://localhost:8002/models" | ConvertTo-Json

Pause-Demo "--- cost-service (port 8003) ---"
Invoke-RestMethod -Uri "http://localhost:8003/health" | ConvertTo-Json
Write-Host "`n/health/deps:"
Invoke-RestMethod -Uri "http://localhost:8003/health/deps" | ConvertTo-Json
Write-Host "`nGET /pricing:"
Invoke-RestMethod -Uri "http://localhost:8003/pricing" | ConvertTo-Json

Write-Host "`nDocs (Swagger UI):" -ForegroundColor DarkGray
Write-Host "  http://localhost:8001/docs  (agent-service)" -ForegroundColor DarkGray
Write-Host "  http://localhost:8002/docs  (usage-service)" -ForegroundColor DarkGray
Write-Host "  http://localhost:8003/docs  (cost-service)" -ForegroundColor DarkGray

# ── CHECKPOINT 2: Docker images & ps ────────────────────────────────────────
Pause-Demo
Print-Section "CHECKPOINT 2: Docker images and container status"

Write-Host "`ndocker images:" -ForegroundColor Cyan
docker images | Select-String -Pattern "lab/|REPOSITORY"

Write-Host "`ndocker compose ps:" -ForegroundColor Cyan
docker compose -f "$PSScriptRoot\..\docker-compose.yml" ps

# ── CHECKPOINT 3: Network + request_id trace ─────────────────────────────────
Pause-Demo
Print-Section "CHECKPOINT 3: Network inspect + request_id trace"

Write-Host "`ndocker network inspect ai-ops-net (containers only):" -ForegroundColor Cyan
docker network inspect ai-ops-net --format "{{range .Containers}}{{.Name}} {{end}}"

Write-Host "`nPosting a /run request to agent id=$AgentId..." -ForegroundColor Cyan
$body = @{ prompt = "Explain gradient descent in one paragraph."; source = "demo" } | ConvertTo-Json
$resp = Invoke-RestMethod -Uri "http://localhost:8001/agents/$AgentId/run" `
    -Method Post -ContentType "application/json" -Body $body
$resp | ConvertTo-Json -Depth 5

$rid = $resp.request_id
Write-Host "`nrequest_id = $rid" -ForegroundColor Yellow
Write-Host "`nSearching all service logs for this request_id..." -ForegroundColor Cyan
docker compose -f "$PSScriptRoot\..\docker-compose.yml" logs --tail=200 | `
    Select-String $rid

# ── CHECKPOINT 4: Load test ───────────────────────────────────────────────────
Pause-Demo
Print-Section "CHECKPOINT 4: Load test (--quick smoke test)"

Write-Host "Running quick load test (50 requests per level, 1 repeat)..." -ForegroundColor Cyan
python "$PSScriptRoot\loadtest.py" --quick

Write-Host "`nLive docker stats (one sample):" -ForegroundColor Cyan
docker stats --no-stream agent-service usage-service cost-service postgres-db

# ── CHECKPOINT 5: Dashboard, results, graphs ──────────────────────────────────
Pause-Demo
Print-Section "CHECKPOINT 5: Dashboard, results, graphs"

Write-Host "Dashboard at: http://localhost:8001/" -ForegroundColor Green
Write-Host "Opening in browser..." -ForegroundColor Cyan
Start-Process "http://localhost:8001/"

Write-Host "`nResults directory:" -ForegroundColor Cyan
Get-ChildItem "$PSScriptRoot\..\results" | Format-Table Name, Length, LastWriteTime

Write-Host "`nGET /analytics/overview (cost-service):" -ForegroundColor Cyan
Invoke-RestMethod -Uri "http://localhost:8003/analytics/overview" | ConvertTo-Json

Write-Host "`nGET /alerts (cost-service):" -ForegroundColor Cyan
Invoke-RestMethod -Uri "http://localhost:8003/alerts" | ConvertTo-Json -Depth 3

Write-Host @"

  ╔══════════════════════════════════════════════════════╗
  ║   Demo complete!                                     ║
  ║   See results/ for CSVs, PNGs, and ANALYSIS.md      ║
  ╚══════════════════════════════════════════════════════╝

"@ -ForegroundColor Green
