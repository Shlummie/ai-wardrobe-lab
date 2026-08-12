$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "backend\requirements.txt"
if (-not (Test-Path -LiteralPath "node_modules")) {
    & "C:\Program Files\nodejs\npm.cmd" ci --ignore-scripts --no-audit --no-fund
}

& "C:\Program Files\nodejs\npm.cmd" run build

Write-Host "AI Wardrobe Lab local dependencies are ready." -ForegroundColor Green
