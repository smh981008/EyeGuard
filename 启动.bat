@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "dist\EyeGuard\EyeGuard.exe" (
    start "" "dist\EyeGuard\EyeGuard.exe"
    exit /b
)

where pythonw >nul 2>nul
if errorlevel 1 (
    echo 没找到 Python，请先安装 Python 3.10 以上版本。
    pause
    exit /b 1
)

start "" pythonw app.py
