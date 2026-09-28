@echo off
REM Kiro Chat Viewer 一键启动 (Windows)
REM 检测 Python -> 建虚拟环境 -> 装依赖 -> 启动 -> 打开浏览器
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 1. 定位 Python
set "PY="
for %%C in (python py) do (
  if not defined PY (
    %%C -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" 1>nul 2>nul
    if not errorlevel 1 set "PY=%%C"
  )
)
if not defined PY (
  echo 错误: 未找到 Python 3.8+。请先安装: https://www.python.org/downloads/
  exit /b 1
)
echo 使用 Python: & %PY% --version

REM 2. 虚拟环境
if not exist ".venv" (
  echo 创建虚拟环境 .venv ...
  %PY% -m venv .venv
)
call ".venv\Scripts\activate.bat"

REM 3. 依赖
if not exist ".venv\.deps-installed" (
  echo 安装依赖 ...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
  echo done> ".venv\.deps-installed"
)

REM 4. 端口与地址
if not defined KCV_HOST set "KCV_HOST=127.0.0.1"
if not defined KCV_PORT set "KCV_PORT=8765"
set "URL=http://%KCV_HOST%:%KCV_PORT%"

REM 5. 延迟打开浏览器并启动服务
start "" /b cmd /c "timeout /t 2 >nul & start "" %URL%"
echo 启动服务: %URL%  (Ctrl+C 停止)
python -m uvicorn backend.app:app --host %KCV_HOST% --port %KCV_PORT%
