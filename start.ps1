param(
    [switch]$Build,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

function Test-DockerReady {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    cmd /c "docker info --format {{.ServerVersion}} >nul 2>&1"
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

Write-Host "=== Private Collaborative Computing: Distributed System Startup ===" -ForegroundColor Cyan

# 1. Ensure .env exists
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env" -Force
    Write-Host "[1/4] Created .env from .env.example" -ForegroundColor Green
}

# 2. Ensure Docker Desktop is running
$dockerOk = Test-DockerReady
if (-not $dockerOk) {
    Write-Host "[2/4] Starting Docker Desktop..." -ForegroundColor Yellow
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    cmd /c "docker desktop start >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        $desktopExe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (Test-Path $desktopExe) {
            Start-Process -FilePath $desktopExe
        }
    }
    $ErrorActionPreference = $prev

    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        if (Test-DockerReady) {
            $dockerOk = $true
            break
        }
    }
}

if (-not $dockerOk) {
    Write-Error "Docker engine did not become ready. Please start Docker Desktop and retry."
    exit 1
}
Write-Host "[2/4] Docker engine is ready" -ForegroundColor Green

# 3. Start the 4-container distributed system (node-1, node-2, node-3, coordinator)
Write-Host "[3/4] Starting 4-container cluster (node-1, node-2, node-3, coordinator)..." -ForegroundColor Yellow
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
if ($Build) {
    docker compose up -d --build
} else {
    docker compose up -d
}
$composeRc = $LASTEXITCODE
$ErrorActionPreference = $prev
if ($composeRc -ne 0) {
    Write-Error "Failed to start Docker Compose services (exit code $composeRc)."
    exit 1
}

# 4. Wait for Coordinator + all 3 Client Nodes to be reachable
Write-Host "[4/4] Waiting for all 4 nodes to come online..." -ForegroundColor Yellow
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8501/api/cluster" -TimeoutSec 3
        if ($resp.reachable_nodes -eq 3) {
            $ready = $true
            break
        }
    } catch {
        # Still starting
    }
    Start-Sleep -Seconds 1
}

if ($ready) {
    Write-Host ""
    Write-Host "System is READY!" -ForegroundColor Green
    Write-Host "  Coordinator Console : http://localhost:8501" -ForegroundColor White
    Write-Host "  Client Node 1 Portal: http://localhost:9101" -ForegroundColor Gray
    Write-Host "  Client Node 2 Portal: http://localhost:9102" -ForegroundColor Gray
    Write-Host "  Client Node 3 Portal: http://localhost:9103" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Stop anytime with: .\stop.bat  (or: docker compose down)" -ForegroundColor DarkGray
    if (-not $NoBrowser) {
        Start-Process "http://localhost:8501"
    }
} else {
    Write-Warning "Containers started, but cluster health check did not reach 3/3 nodes in 30s. Check 'docker compose logs'."
}
