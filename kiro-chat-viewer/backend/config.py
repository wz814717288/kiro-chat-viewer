"""全局配置:数据源路径与运行参数。

只读访问 Kiro 本地会话数据,严禁写回 ~/.kiro/sessions。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

# 服务默认只绑定本地回环,避免把含敏感信息的对话暴露到网络
HOST = os.environ.get("KCV_HOST", "127.0.0.1")
PORT = int(os.environ.get("KCV_PORT", "8765"))

# 索引数据库存放在本项目目录下(不污染 Kiro 数据)
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("KCV_DB", str(BASE_DIR / "data" / "index.db")))

# 前端静态资源目录
FRONTEND_DIR = BASE_DIR / "frontend"


def get_kiro_home() -> Path:
    """~/.kiro 根目录,支持 KIRO_HOME 覆盖。"""
    kiro_home = os.environ.get("KIRO_HOME")
    return Path(kiro_home) if kiro_home else Path.home() / ".kiro"


def get_session_roots() -> List[Path]:
    """返回所有存放 IDE v2 会话的根目录。

    结构: <root>/<workspace-hash>/<session-id>/{session.json, messages.jsonl}
    """
    roots: List[Path] = []
    v2 = get_kiro_home() / "sessions"
    if v2.exists():
        roots.append(v2)
    return roots
