@echo off
title Disable AI Wardrobe Lab iPhone Access
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\disable_mobile_firewall.ps1"
if errorlevel 1 (
  echo.
  echo Could not disable iPhone access.
  pause
  exit /b 1
)
echo.
pause
