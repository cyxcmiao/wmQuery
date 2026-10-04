@echo off
REM ============================================================
REM  Build wmQuery into a single-file exe (PyInstaller onefile).
REM  Run this script on a machine WITH Python installed (dev box).
REM  Output: dist\wmQuery.exe  -- ship this one file to end users,
REM  no Python environment needed on their side.
REM
REM  Bundled into the exe:
REM    index.html        (web page)
REM    cache/            (scenes etc., from build_stage\cache, staged by
REM                       stage_cache.py; emoji test scene file excluded)
REM    relic/            (wiki page archive for relic lookup)
REM    items_cache.json  (item list cache, pre-warmed data)
REM  The exe extracts cache/, relic/, items_cache.json next to itself
REM  ONLY on first run (marker file .seed_done), later runs never
REM  re-seed, so deleted scene files stay deleted (true persistence).
REM  To refresh bundled data again, delete .seed_done next to the exe.
REM
REM  NOTE: keep this file pure ASCII with CRLF line endings.
REM  Chinese chars or "chcp 65001" break cmd parsing on zh-CN Windows.
REM ============================================================

cd /d "%~dp0"

REM Prefer project venv python, fall back to system python
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/4] Checking PyInstaller...
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
echo [2/4] Staging cache folder (drop emoji test scene)...
"%PY%" stage_cache.py
if errorlevel 1 (
    echo.
    echo FAILED to stage cache. See message above.
    pause
    exit /b 1
)

echo.
echo [3/4] Building dist\wmQuery.exe ...
"%PY%" -m PyInstaller --onefile --noconfirm --clean --name wmQuery ^
    --add-data=index.html:. ^
    --add-data=build_stage/cache:cache ^
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
echo [4/4] Build OK.
echo   Output : dist\wmQuery.exe
echo   Share  : copy this single exe to users, double-click to run.
echo   Tip    : to update bundled data, refresh it in dev mode then
echo            re-run this script.
echo.
pause
