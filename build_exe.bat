@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================
rem  wmQuery 一键打包脚本
rem  打包结果：dist\wmQuery.exe（单文件，可直接发给别人用）
rem ============================================

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/3] 检查 PyInstaller...
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo 未安装 PyInstaller，正在通过中科大镜像安装...
    "%PY%" -m pip install pyinstaller -i https://mirrors.ustc.edu.cn/pypi/simple
    if errorlevel 1 (
        echo PyInstaller 安装失败，请检查网络后重试
        pause
        exit /b 1
    )
)

echo [2/3] 开始打包，请稍候...
"%PY%" -m PyInstaller --noconfirm --onefile --name wmQuery --add-data "index.html:." main.py
if errorlevel 1 (
    echo 打包失败，请查看上方错误信息
    pause
    exit /b 1
)

echo [3/3] 打包完成，文件位置：dist\wmQuery.exe
pause
