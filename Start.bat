@echo off
rem ============================================================
rem  运动世界校园 可视化控制台 - 双击本文件直接启动
rem  单页界面：登录小条 + 一键跑步 + 实时日志
rem  若控制台已在运行，会自动复用并打开浏览器
rem ============================================================
chcp 65001 >nul
title 运动世界校园 可视化控制台
cd /d "%~dp0"

set "PY="

rem 1) 优先: 托管 Python (含 Crypto/PIL/numpy)
if exist "%USERPROFILE%\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe" (
    set "PY=%USERPROFILE%\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
)
if "%PY%"=="" if exist "%USERPROFILE%\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe" (
    set "PY=%USERPROFILE%\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe"
)

rem 2) 其次: 系统 Python 3.14
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
)

rem 3) 最后: PATH 里的 python
if "%PY%"=="" set "PY=python"

echo 运动世界校园 控制台启动中...
echo 若浏览器未自动打开，请手动访问: http://127.0.0.1:8765
echo 关闭本窗口即停止控制台
echo.
"%PY%" "%~dp0gui.py" --port 8765
if errorlevel 1 (
    echo.
    echo [错误] 启动失败, 请确认已安装 Python 3.10+ 及依赖:
    echo        pip install pycryptodome Pillow numpy
    pause
)