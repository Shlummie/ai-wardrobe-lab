$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $projectRoot "run"
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $runDir, $logDir | Out-Null
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & (Join-Path $PSScriptRoot "setup.ps1")
}

if (-not (Test-Path -LiteralPath "dist\server\index.js")) {
    & "C:\Program Files\nodejs\npm.cmd" run build
}

function Test-Url([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-AIWardrobeFrontend([int]$Port) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port" -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content -match '<title>AI Wardrobe Lab'
    } catch {
        return $false
    }
}

$webPort = 3000
if ((Test-Url "http://127.0.0.1:3000") -and -not (Test-AIWardrobeFrontend 3000)) {
    $webPort = 3001
    if ((Test-Url "http://127.0.0.1:3001") -and -not (Test-AIWardrobeFrontend 3001)) {
        throw "Ports 3000 and 3001 are already occupied by other local services. Stop one or set AIWL_MOBILE_WEB_PORT to a free port."
    }
}
$webUrl = "http://127.0.0.1:$webPort"
$env:AIWL_MOBILE_WEB_PORT = [string]$webPort

if (-not (Test-Url "http://127.0.0.1:8765/api/health")) {
    $backend = Start-Process -FilePath (Join-Path $projectRoot ".venv\Scripts\python.exe") `
        -ArgumentList @("-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDir "backend.out.log") `
        -RedirectStandardError (Join-Path $logDir "backend.err.log")
    @{ pid = $backend.Id; started = $backend.StartTime.ToString("o") } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir "backend.json")
}

if (-not (Test-AIWardrobeFrontend $webPort)) {
    $frontend = Start-Process -FilePath "C:\WINDOWS\System32\cmd.exe" `
        -ArgumentList @("/d", "/c", "`"C:\Program Files\nodejs\npm.cmd`" run start:lan -- --port $webPort") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDir "frontend.out.log") `
        -RedirectStandardError (Join-Path $logDir "frontend.err.log")
    @{ pid = $frontend.Id; started = $frontend.StartTime.ToString("o"); port = $webPort } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir "frontend.json")
}

$deadline = (Get-Date).AddSeconds(35)
while ((Get-Date) -lt $deadline) {
    if ((Test-Url "http://127.0.0.1:8765/api/health") -and (Test-AIWardrobeFrontend $webPort)) {
        Start-Process $webUrl
        Write-Host "AI Wardrobe Lab is ready at $webUrl" -ForegroundColor Green
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

throw "AI Wardrobe Lab did not become ready. Check the files in $logDir."
