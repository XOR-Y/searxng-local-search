@echo off
rem ============================================================
rem  searx.bat - search entry point
rem
rem  Thin wrapper around searx.py, using the Python bundled in
rem  python_embed\ (created by setup.bat). Everything is resolved
rem  relative to this file, so the folder can be moved anywhere.
rem
rem  Usage:
rem    searx.bat "keyword" --lang zh-CN -n 5
rem    searx.bat --status
rem ============================================================

setlocal
chcp 65001 >nul
if exist "%~dp0config.bat" call "%~dp0config.bat"

if not exist "%~dp0python_embed\python.exe" (
  echo [ERROR] python_embed not found. Please run setup.bat first.
  exit /b 1
)

if "%~1"=="" (
  echo Usage: searx.bat "keyword" [--lang zh-CN] [-n 5]
  echo        searx.bat --status
  echo.
  echo Full option list:  searx.bat --help
  exit /b 2
)

"%~dp0python_embed\python.exe" "%~dp0searx.py" %*
set "RC=%errorlevel%"
exit /b %RC%
