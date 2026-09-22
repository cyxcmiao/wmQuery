@echo off
cd /d "%~dp0"

rem ============================================
rem  wmQuery build script - generates dist\wmQuery.exe
rem  IMPORTANT: keep this file ASCII-only (no Chinese text,
rem  no chcp switch). Non-ASCII chars make cmd.exe mis-parse
rem  the batch file on Chinese Windows.
rem ============================================

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/3] Checking PyInstaller...
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing via USTC mirror...
    "%PY%" -m pip install pyinstaller -i https://mirrors.ustc.edu.cn/pypi/simple
    if errorlevel 1 (
        echo Install failed. Check network and run again.
        pause
        exit /b 1
    )
)

echo [2/3] Building, please wait...
"%PY%" -m PyInstaller --noconfirm --onefile --name wmQuery --add-data "index.html:." main.py
if errorlevel 1 (
    echo Build failed. See errors above.
    pause
    exit /b 1
)

echo [3/3] Done. Output: dist\wmQuery.exe
pause
