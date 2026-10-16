@echo off
rem ============================================================
rem  fetch.bat - fetch the full text of a web page
rem
rem  Thin wrapper around fetch.py, using the Python bundled in
rem  python_embed\ (created by setup.bat). Everything is resolved
rem  relative to this file, so the folder can be moved anywhere.
rem
rem  Usage:
rem    fetch.bat "https://example.com" --max 4000
rem    fetch.bat "https://example.com" --render      (real browser)
rem    fetch.bat --login                             (log in once)
rem ============================================================

setlocal
chcp 65001 >nul
if exist "%~dp0config.bat" call "%~dp0config.bat"

if not exist "%~dp0python_embed\python.exe" (
  echo [ERROR] python_embed not found. Please run setup.bat first.
  exit /b 1
)

if "%~1"=="" (
  echo Usage: fetch.bat "https://..." [--max 4000] [--render] [--login]
  echo.
  echo Full option list:  fetch.bat --help
  exit /b 2
)

"%~dp0python_embed\python.exe" "%~dp0fetch.py" %*
set "RC=%errorlevel%"
exit /b %RC%
