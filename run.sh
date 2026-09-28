#!/usr/bin/env bash
# Kiro Chat Viewer 一键启动 (macOS / Linux)
# 定位 Python(找不到则用 uv 自动装到项目内) -> 建虚拟环境 -> 装依赖 -> 探测端口 -> 启动 -> 打开浏览器
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

# 项目内自带的工具与 Python(均已 gitignore)
TOOLS_DIR="$ROOT/.tools"
export UV_PYTHON_INSTALL_DIR="$ROOT/.python"
MANAGED_PY_VERSION="${KCV_PYTHON_VERSION:-3.12}"

# 检查某个解释器是否可用且 >=3.8。
# stdin 接 /dev/null 并加 10 秒超时:macOS 未同意 Xcode license 时 /usr/bin/python3 会卡在交互提示。
py_ok() {
  [ -n "$1" ] || return 1
  "$1" -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' </dev/null >/dev/null 2>&1 &
  local pid=$! i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge 50 ]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; return 1; fi
    sleep 0.2
  done
  wait "$pid"
}

# 定位 uv:优先项目内 .tools/uv,其次系统 PATH
find_uv() {
  if [ -x "$TOOLS_DIR/uv" ]; then echo "$TOOLS_DIR/uv"
  elif command -v uv >/dev/null 2>&1; then command -v uv
  fi
}

# 项目内已安装的托管 Python(不查系统)
managed_python() {
  local uv; uv="$(find_uv)"
  [ -n "$uv" ] && [ -d "$UV_PYTHON_INSTALL_DIR" ] || return 0
  UV_PYTHON_PREFERENCE=only-managed "$uv" python find "$MANAGED_PY_VERSION" 2>/dev/null || true
}

# 下载 uv 到 .tools/,再用它把独立 Python 装到 .python/。
# 进度输出走 stderr,stdout 只输出最终解释器路径(供调用方捕获)。
install_managed_python() {
  local uv; uv="$(find_uv)"
  if [ -z "$uv" ]; then
    echo "未找到可用的 Python 3.8+,将自动下载 uv 并安装 Python $MANAGED_PY_VERSION 到项目目录(仅首次,需联网)..." >&2
    mkdir -p "$TOOLS_DIR"
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$TOOLS_DIR" sh >&2
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$TOOLS_DIR" sh >&2
    else
      echo "错误: 需要 curl 或 wget 来下载 uv。也可手动安装 Python 3.8+: https://www.python.org/downloads/" >&2
      return 1
    fi
    uv="$TOOLS_DIR/uv"
  fi
  echo "安装 Python $MANAGED_PY_VERSION 到 .python/ ..." >&2
  "$uv" python install "$MANAGED_PY_VERSION" >&2
  UV_PYTHON_PREFERENCE=only-managed "$uv" python find "$MANAGED_PY_VERSION"
}

# 1. 定位 Python:KCV_PYTHON > 已有虚拟环境 > 项目内托管 Python > 系统 python3.x > 自动安装
VENV=".venv"
VENV_PY="$VENV/bin/python"
if [ -d "$VENV" ] && ! py_ok "$VENV_PY"; then
  echo "现有 .venv 不可用(可能是从别处复制来的),重新创建 ..."
  rm -rf "$VENV"
fi

if [ ! -d "$VENV" ]; then
  PY=""
  for c in "${KCV_PYTHON:-}" "$(managed_python)" \
           python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3 python; do
    [ -n "$c" ] || continue
    if [ "${c#/}" = "$c" ]; then c="$(command -v "$c" 2>/dev/null || true)"; fi
    if py_ok "$c"; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then
    PY="$(install_managed_python || true)"
    if ! py_ok "$PY"; then
      echo "错误: 自动安装 Python 失败。请手动安装 Python 3.8+: https://www.python.org/downloads/" >&2
      exit 1
    fi
  fi
  echo "使用 Python: $PY ($("$PY" --version 2>&1))"
  echo "创建虚拟环境 .venv ..."
  "$PY" -m venv "$VENV"
fi
echo "虚拟环境: $("$VENV_PY" --version 2>&1)"

# 2. 依赖(用标记文件避免每次重装;镜像可通过 PIP_INDEX_URL 指定)
STAMP="$VENV/.deps-installed"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "安装依赖 ..."
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r requirements.txt
  touch "$STAMP"
fi

# 3. 端口探测(被占用则自动 +1,最多试 20 个)
HOST="${KCV_HOST:-127.0.0.1}"
PORT="${KCV_PORT:-8765}"
port_in_use() { "$VENV_PY" - "$1" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
r = s.connect_ex(("127.0.0.1", int(sys.argv[1])))
s.close()
sys.exit(0 if r == 0 else 1)
PY
}
tries=0
while port_in_use "$PORT"; do
  PORT=$((PORT+1)); tries=$((tries+1))
  if [ "$tries" -ge 20 ]; then echo "错误: 找不到可用端口" >&2; exit 1; fi
done
export KCV_HOST="$HOST" KCV_PORT="$PORT"
URL="http://$HOST:$PORT"

# 4. 打开浏览器(延迟,等服务起来)
( sleep 2
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  fi ) >/dev/null 2>&1 &

echo "启动服务: $URL  (Ctrl+C 停止)"
exec "$VENV_PY" -m uvicorn backend.app:app --host "$HOST" --port "$PORT"
