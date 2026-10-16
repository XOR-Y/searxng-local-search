@echo off
chcp 65001 >nul
title SearXNG
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run setup first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" run.py

echo.
echo SearXNG stopped.
pause
