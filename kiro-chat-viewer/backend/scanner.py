"""扫描器:在 Kiro 会话根目录下定位所有会话目录。

一个会话目录的判定标准:同时(或至少)包含 session.json,通常还有 messages.jsonl。
返回去重后的会话目录列表。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from . import config


def find_session_dirs() -> List[Path]:
    """返回所有会话目录(含 session.json 的目录),已去重并按修改时间倒序。"""
    seen = set()
    dirs: List[Path] = []

    for root in config.get_session_roots():
        if not root.exists():
            continue
        # session.json 是会话目录的稳定标志
        for meta in root.glob("**/session.json"):
            d = meta.parent
            key = str(d.resolve())
            if key in seen:
                continue
            seen.add(key)
            dirs.append(d)

    # 按 messages.jsonl(优先)或目录本身的 mtime 倒序,最近的排前面
    def _mtime(d: Path) -> float:
        msg = d / "messages.jsonl"
        target = msg if msg.exists() else d
        try:
            return target.stat().st_mtime
        except OSError:
            return 0.0

    dirs.sort(key=_mtime, reverse=True)
    return dirs
