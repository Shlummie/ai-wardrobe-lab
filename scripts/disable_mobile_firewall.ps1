$ErrorActionPreference = "Stop"
$ruleName = "AI Wardrobe Lab iPhone Intake"

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
    $existing | Remove-NetFirewallRule
    Write-Host "AI Wardrobe Lab iPhone intake firewall access was removed." -ForegroundColor Green
} else {
    Write-Host "No AI Wardrobe Lab iPhone intake firewall rule exists." -ForegroundColor Green
}
