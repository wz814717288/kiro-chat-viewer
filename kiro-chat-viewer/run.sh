#!/usr/bin/env bash
# Kiro Chat Viewer 一键启动 (macOS / Linux)
# 检测 Python -> 建虚拟环境 -> 装依赖 -> 探测端口 -> 启动 -> 打开浏览器
set -euo pipefail

cd "$(dirname "$0")"

# 1. 定位 Python (>=3.8)
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "错误: 未找到 Python 3.8+。请先安装 Python: https://www.python.org/downloads/" >&2
  exit 1
fi
echo "使用 Python: $($PY --version 2>&1)"

# 2. 虚拟环境
VENV=".venv"
if [ ! -d "$VENV" ]; then
  echo "创建虚拟环境 .venv ..."
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# 3. 依赖(用标记文件避免每次重装)
STAMP="$VENV/.deps-installed"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "安装依赖 ..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
  touch "$STAMP"
fi

# 4. 端口探测(被占用则自动 +1,最多试 20 个)
HOST="${KCV_HOST:-127.0.0.1}"
PORT="${KCV_PORT:-8765}"
port_in_use() { python - "$1" <<'PY'
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

# 5. 打开浏览器(延迟,等服务起来)
( sleep 2
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  fi ) >/dev/null 2>&1 &

echo "启动服务: $URL  (Ctrl+C 停止)"
exec python -m uvicorn backend.app:app --host "$HOST" --port "$PORT"
