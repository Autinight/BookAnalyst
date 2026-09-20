@echo off
title BookAnalyst
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-desktop.ps1" %*
if errorlevel 1 (
    echo.
    echo Please review the error message above.
    pause
)
