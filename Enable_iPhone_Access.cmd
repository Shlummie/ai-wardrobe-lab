@echo off
title Enable AI Wardrobe Lab iPhone Access
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\enable_mobile_firewall.ps1"
if errorlevel 1 (
  echo.
  echo Could not enable iPhone access.
  pause
  exit /b 1
)
echo.
pause
