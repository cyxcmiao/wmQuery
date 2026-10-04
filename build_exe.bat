@echo off
REM ============================================================
REM  Build wmQuery into a single-file exe (PyInstaller onefile).
REM  Run this script on a machine WITH Python installed (dev box).
REM  Output: dist\wmQuery.exe  -- ship this one file to end users,
REM  no Python environment needed on their side.
REM
REM  Bundled into the exe:
REM    index.html        (web page)
REM    cache/            (query history, scenes, relics cache)
REM    relic/            (wiki page archive for relic lookup)
REM    items_cache.json  (item list cache, pre-warmed data)
REM  On first run the exe extracts cache/, relic/, items_cache.json
REM  next to itself (existing files are never overwritten), then
REM  all reads/writes happen next to the exe and persist.
REM
REM  NOTE: keep this file pure ASCII with CRLF line endings.
REM  Chinese chars or "chcp 65001" break cmd parsing on zh-CN Windows.
REM ============================================================

cd /d "%~dp0"

REM Prefer project venv python, fall back to system python
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/3] Checking PyInstaller...
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found, installing from mirror...
    "%PY%" -m pip install -i https://mirrors.ustc.edu.cn/pypi/simple pyinstaller
    if errorlevel 1 (
        echo.
        echo FAILED to install PyInstaller. Check your network and retry.
        pause
        exit /b 1
    )
)

echo.
echo [2/3] Building dist\wmQuery.exe ...
"%PY%" -m PyInstaller --onefile --noconfirm --clean --name wmQuery ^
    --add-data=index.html:. ^
    --add-data=cache:cache ^
    --add-data=relic:relic ^
    --add-data=items_cache.json:. ^
    main.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED. See messages above.
    pause
    exit /b 1
)

echo.
echo [3/3] Build OK.
echo   Output : dist\wmQuery.exe
echo   Share  : copy this single exe to users, double-click to run.
echo   Tip    : to update bundled data, refresh it in dev mode then
echo            re-run this script.
echo.
pause
