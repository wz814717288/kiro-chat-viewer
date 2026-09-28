@echo off
REM Kiro Chat Viewer 一键启动 (Windows)
REM 定位 Python(找不到则用 uv 自动装到项目内) -> 建虚拟环境 -> 装依赖 -> 启动 -> 打开浏览器
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"

REM 项目内自带的工具与 Python(均已 gitignore)
set "TOOLS_DIR=%ROOT%\.tools"
set "UV_PYTHON_INSTALL_DIR=%ROOT%\.python"
if not defined KCV_PYTHON_VERSION set "KCV_PYTHON_VERSION=3.12"

set "VENV=%ROOT%\.venv"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

REM 1. 现有虚拟环境不可用(例如从别处复制来的)则重建
if not exist "%VENV%" goto :no_venv
call :py_ok "%VENV_PY%"
if not errorlevel 1 goto :have_venv
echo 现有 .venv 不可用, 重新创建 ...
rmdir /s /q "%VENV%"

:no_venv
REM 2. 定位 Python: 依次尝试 KCV_PYTHON, 项目内托管 Python, py -3, python, python3, 最后自动安装
set "PY="
if defined KCV_PYTHON call :try "%KCV_PYTHON%"
if not defined PY call :managed_python
if not defined PY call :try py -3
if not defined PY call :try python
if not defined PY call :try python3
if not defined PY call :install_managed
if not defined PY (
  echo 错误: 未找到 Python 3.8+, 自动安装也失败。请手动安装: https://www.python.org/downloads/
  exit /b 1
)
echo 使用 Python: %PY%
echo 创建虚拟环境 .venv ...
%PY% -m venv "%VENV%"
if errorlevel 1 (
  echo 错误: 创建虚拟环境失败
  exit /b 1
)

:have_venv
"%VENV_PY%" --version

REM 3. 依赖(镜像可通过 PIP_INDEX_URL 指定)
if not exist "%VENV%\.deps-installed" (
  echo 安装依赖 ...
  "%VENV_PY%" -m pip install --quiet --upgrade pip
  "%VENV_PY%" -m pip install --quiet -r requirements.txt
  if errorlevel 1 (
    echo 错误: 依赖安装失败
    exit /b 1
  )
  echo done> "%VENV%\.deps-installed"
)

REM 4. 端口与地址
if not defined KCV_HOST set "KCV_HOST=127.0.0.1"
if not defined KCV_PORT set "KCV_PORT=8765"
set "URL=http://%KCV_HOST%:%KCV_PORT%"

REM 5. 延迟打开浏览器并启动服务
start "" /b cmd /c "timeout /t 2 >nul & start "" %URL%"
echo 启动服务: %URL%  (Ctrl+C 停止)
"%VENV_PY%" -m uvicorn backend.app:app --host %KCV_HOST% --port %KCV_PORT%
exit /b %errorlevel%


REM ---------------- 子过程 ----------------

REM 检查解释器可用且 >=3.8。参数为完整命令, 如 "C:\x\python.exe" 或 py -3
:py_ok
%* -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" <nul >nul 2>&1
exit /b %errorlevel%

REM 可用则记入 PY
:try
call :py_ok %*
if not errorlevel 1 set "PY=%*"
exit /b 0

REM 定位 uv: 优先项目内 .tools\uv.exe, 其次 PATH
:find_uv
set "UV="
if exist "%TOOLS_DIR%\uv.exe" set "UV=%TOOLS_DIR%\uv.exe"
if defined UV exit /b 0
for /f "delims=" %%U in ('where uv 2^>nul') do if not defined UV set "UV=%%U"
exit /b 0

REM 项目内已安装的托管 Python(不查系统)
:managed_python
call :find_uv
if not defined UV exit /b 0
if not exist "%UV_PYTHON_INSTALL_DIR%" exit /b 0
set "UV_PYTHON_PREFERENCE=only-managed"
for /f "delims=" %%P in ('""%UV%" python find %KCV_PYTHON_VERSION% 2>nul"') do call :try "%%P"
set "UV_PYTHON_PREFERENCE="
exit /b 0

REM 下载 uv 到 .tools\, 再用它把独立 Python 装到 .python\
:install_managed
call :find_uv
if defined UV goto :install_python
echo 未找到可用的 Python 3.8+, 将自动下载 uv 并安装 Python %KCV_PYTHON_VERSION% 到项目目录, 仅首次, 需联网 ...
if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"
powershell -NoProfile -ExecutionPolicy ByPass -Command "$env:UV_UNMANAGED_INSTALL='%TOOLS_DIR%'; irm https://astral.sh/uv/install.ps1 | iex"
if not exist "%TOOLS_DIR%\uv.exe" (
  echo 错误: 下载 uv 失败
  exit /b 0
)
set "UV=%TOOLS_DIR%\uv.exe"
:install_python
echo 安装 Python %KCV_PYTHON_VERSION% 到 .python\ ...
"%UV%" python install %KCV_PYTHON_VERSION%
if errorlevel 1 exit /b 0
call :managed_python
exit /b 0
