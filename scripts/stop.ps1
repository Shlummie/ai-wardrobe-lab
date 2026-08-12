$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $projectRoot "run"
$failures = @()

foreach ($name in @("frontend", "backend")) {
    $recordPath = Join-Path $runDir "$name.json"
    if (-not (Test-Path -LiteralPath $recordPath)) { continue }
    $record = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if ($process) {
        $recordedStart = [datetime]::Parse([string]$record.started)
        if ([math]::Abs(($process.StartTime - $recordedStart).TotalSeconds) -lt 3) {
            $taskkillOutput = & taskkill.exe /PID $process.Id /T /F 2>&1
            if ($LASTEXITCODE -ne 0) {
                $failures += "$name PID $($process.Id): $($taskkillOutput -join ' ')"
                continue
            }
        }
    }
    Remove-Item -LiteralPath $recordPath -Force
}

if ($failures.Count -gt 0) {
    throw "One or more local process trees could not be stopped. Re-run Stop_AI_Wardrobe_Lab.cmd with the same Windows permissions used to start it. $($failures -join ' | ')"
}

Write-Host "AI Wardrobe Lab local processes are stopped." -ForegroundColor Green
