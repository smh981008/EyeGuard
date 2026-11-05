@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先跑源码：源码永远是最新的，dist 里的 exe 只是某个时间点的打包快照。
rem 想跑打包版就直接进 dist\EyeGuard 双击 EyeGuard.exe。
where pythonw >nul 2>nul
if not errorlevel 1 (
    rem 把输出重定向到文件：pythonw 没有控制台，句柄为空时个别库会写入失败
    start "" pythonw app.py > run.log 2>&1
    exit /b
)

if exist "dist\EyeGuard\EyeGuard.exe" (
    start "" "dist\EyeGuard\EyeGuard.exe"
    exit /b
)

echo 没找到 Python，也没找到打包好的程序。
echo 请先安装 Python 3.10 以上版本，或者运行 dist\EyeGuard\EyeGuard.exe。
pause
exit /b 1
