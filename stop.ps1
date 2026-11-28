$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

Write-Host "Stopping Private Collaborative Computing cluster..." -ForegroundColor Yellow
docker compose down
Write-Host "Stopped." -ForegroundColor Green
