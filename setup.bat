@echo off
rem ============================================================
rem  local_search - one-time environment setup
rem
rem  What it does:
rem    1. downloads the official "Python embeddable package"
rem    2. unpacks it into  .\python_embed\
rem    3. rewrites python313._pth so pip and site-packages work
rem    4. installs pip + everything listed in requirements.txt
rem
rem  Result: a self-contained Python that lives inside this
rem          folder. No system Python, no venv, nothing installed
rem          globally, no dependency on any IDE-managed runtime.
rem
rem  Re-running is safe - already-finished steps are skipped.
rem
rem  Env vars you may override:
rem    PYTHON_MIRROR      (unused placeholder for custom mirrors)
rem    PIP_INDEX_URL      pip index, e.g. a domestic mirror
rem    LOCAL_SEARCH_PROXY proxy used for pip / python.org,
rem                       default http://127.0.0.1:7897
rem    NO_PAUSE=1         suppress the final "press any key"
rem ============================================================

setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYVER=3.13.12"
set "EMBED=%~dp0python_embed"
set "ZIP=%~dp0python-embed.zip"
set "MINZIP=10000000"

if not defined LOCAL_SEARCH_PROXY set "LOCAL_SEARCH_PROXY=http://127.0.0.1:7897"

echo.
echo  ==========================================================
echo    local_search - environment setup
echo    Python %PYVER% embeddable  --^>  python_embed\
echo  ==========================================================
echo.

rem ---------- 0) already good? ----------
if exist "%EMBED%\python.exe" (
  "%EMBED%\python.exe" -c "import httpx,trafilatura,readability,markdownify" >nul 2>&1
  if not errorlevel 1 (
    echo  [SKIP] python_embed is already set up.
    goto :verify
  )
  echo  [INFO] python_embed exists but dependencies are incomplete. Reinstalling.
)

rem ---------- 1) offline package first ----------
if not exist "%EMBED%\python.exe" if exist "%~dp0python_embed_offline.zip" (
  echo  [1/6] Offline package found, extracting ...
  powershell -NoProfile -Command "Expand-Archive -Force '%~dp0python_embed_offline.zip' '%~dp0'"
)

rem ---------- 2) download ----------
if not exist "%EMBED%\python.exe" (
  echo  [2/6] Downloading Python %PYVER% embeddable ...
  if exist "%ZIP%" del /f /q "%ZIP%"
  call :try "https://mirrors.huaweicloud.com/python/%PYVER%/python-%PYVER%-embed-amd64.zip" ""
  if not exist "%ZIP%" call :try "https://mirrors.aliyun.com/python-release/windows/python-%PYVER%-embed-amd64.zip" ""
  if not exist "%ZIP%" call :try "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip" "-x %LOCAL_SEARCH_PROXY%"
  if not exist "%ZIP%" call :try "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip" ""
  if not exist "%ZIP%" goto :nodownload
  echo  [3/6] Extracting ...
  powershell -NoProfile -Command "Expand-Archive -Force '%ZIP%' '%EMBED%'"
  del /f /q "%ZIP%"
)

if not exist "%EMBED%\python.exe" goto :nodownload

rem ---------- 4) rewrite ._pth (critical) ----------
echo  [4/6] Writing python313._pth ...
>"%EMBED%\python313._pth" echo python313.zip
>>"%EMBED%\python313._pth" echo .
>>"%EMBED%\python313._pth" echo import site

rem ---------- probe proxy (pip only; mirrors stay direct) ----------
rem Probe a TINY response (first byte only). Do NOT probe /simple/ - that
rem index page is tens of MB and will always time out.
set "PIP_PROXY="
curl.exe -s -f -r 0-0 --max-time 8 -o nul -x "%LOCAL_SEARCH_PROXY%" https://pypi.org/ >nul 2>&1 && set "PIP_PROXY=%LOCAL_SEARCH_PROXY%"
set "PIPNET="
if defined PIP_PROXY set "PIPNET=--proxy %PIP_PROXY%"
if defined PIP_INDEX_URL set "PIPNET=%PIPNET% --index-url %PIP_INDEX_URL%"
if defined PIP_PROXY (echo  [INFO] pip will use proxy %PIP_PROXY%) else (echo  [INFO] pip will connect directly)

rem ---------- 5) pip ----------
if not exist "%EMBED%\Lib\site-packages\pip" (
  echo  [5/6] Installing pip ...
  if exist "%EMBED%\get-pip.py" del /f /q "%EMBED%\get-pip.py"
  curl.exe -fL --max-time 180 -o "%EMBED%\get-pip.py" https://bootstrap.pypa.io/get-pip.py
  if not exist "%EMBED%\get-pip.py" curl.exe -fL --max-time 180 -x "%LOCAL_SEARCH_PROXY%" -o "%EMBED%\get-pip.py" https://bootstrap.pypa.io/get-pip.py
  if not exist "%EMBED%\get-pip.py" goto :nodownload
  "%EMBED%\python.exe" "%EMBED%\get-pip.py" --no-warn-script-location %PIPNET%
  del /f /q "%EMBED%\get-pip.py"
)

rem ---------- 6) dependencies ----------
echo  [6/6] Installing dependencies from requirements.txt ...
"%EMBED%\python.exe" -m pip install --no-warn-script-location %PIPNET% -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo  [WARN] Full install failed. Retrying with core packages only ...
  "%EMBED%\python.exe" -m pip install --no-warn-script-location %PIPNET% httpx==0.28.1 trafilatura==2.2.0 readability-lxml==0.9 markdownify==1.2.3
)

rem ---------- verify ----------
:verify
echo.
echo  ---------- verify ----------
"%EMBED%\python.exe" -c "import httpx,trafilatura,readability,markdownify;print('  [OK] core deps')"
"%EMBED%\python.exe" -c "import playwright;print('  [OK] playwright')" 2>nul || echo  [WARN] playwright missing - fetch.bat --render unavailable
echo.
echo  Done. Try:
echo      searx.bat "keyword" --lang zh-CN -n 5
echo      fetch.bat "https://example.com" --max 4000
echo.
if not defined NO_PAUSE pause
exit /b 0

rem ============================================================
:nodownload
echo.
echo  [ERROR] Could not obtain the Python %PYVER% embeddable package.
echo.
echo  Fix it one of these ways, then re-run this script:
echo    1. Make sure your network / Clash proxy is up.
echo    2. Download this file by hand:
echo         python-%PYVER%-embed-amd64.zip
echo       and save it next to this script as:
echo         python_embed_offline.zip
echo       (do NOT unpack it - the script will handle that)
echo.
if not defined NO_PAUSE pause
exit /b 1

rem ============================================================
:try
rem %1 = url   %2 = extra curl args (may be empty)
curl.exe -fL --connect-timeout 15 --max-time 600 %~2 -o "%ZIP%" "%~1"
if not exist "%ZIP%" exit /b 1
set "SZ="
for %%A in ("%ZIP%") do set "SZ=%%~zA"
if not defined SZ exit /b 1
if %SZ% LSS %MINZIP% (
  echo        [WARN] %~1 returned only %SZ% bytes, discarding.
  del /f /q "%ZIP%"
)
exit /b 0
