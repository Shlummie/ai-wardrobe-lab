$ErrorActionPreference = "Stop"
$ruleName = "AI Wardrobe Lab iPhone Intake"
$ports = @("3000", "3001", "8765")

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Set-NetFirewallRule -Enabled True -Profile Private -Direction Inbound -Action Allow
    $existing | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $ports
    $existing | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress LocalSubnet
} else {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description "Same-LAN access to the temporary AI Wardrobe Lab iPhone intake page." `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $ports `
        -Profile Private `
        -RemoteAddress LocalSubnet | Out-Null
}

Write-Host "iPhone intake access is enabled for Private networks and the local subnet only." -ForegroundColor Green
Write-Host "If the phone still cannot connect, confirm Windows labels the current Wi-Fi as Private." -ForegroundColor Yellow
