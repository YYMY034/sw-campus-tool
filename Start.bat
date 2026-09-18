@echo off
rem ============================================================
rem  运动世界校园 可视化控制台 - 双击本文件直接启动
rem  单页界面：登录小条 + 一键跑步 + 实时日志
rem  若控制台已在运行，会自动复用并打开浏览器
rem  说明：本文件用于【源码版】（本目录下即是 .py 源码）。
rem        绿色免安装包内是另一份 Start.bat —— 那份会优先使用
rem        包内自带的 python\python.exe，无需本机安装 Python。
rem ============================================================
chcp 65001 >nul
title 运动世界校园 可视化控制台
cd /d "%~dp0"

set "PY="

rem 1) 优先: 常见安装位置的 Python 3.10 ~ 3.13
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if "%PY%"=="" if exist "%ProgramFiles%\Python313\python.exe" set "PY=%ProgramFiles%\Python313\python.exe"
if "%PY%"=="" if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if "%PY%"=="" if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"
if "%PY%"=="" if exist "%ProgramFiles%\Python310\python.exe" set "PY=%ProgramFiles%\Python310\python.exe"

rem 2) 最后: PATH 里的 python
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