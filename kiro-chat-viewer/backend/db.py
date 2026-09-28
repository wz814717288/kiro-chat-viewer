"""存储与索引:SQLite + FTS5。

三张表:
- sessions:会话元数据 + 指纹(用于增量索引)
- messages:归一化消息
- messages_fts:FTS5 全文索引(仅索引 user/assistant 内容)

增量索引:比较 messages.jsonl 指纹,未变更的会话跳过;变更的先删旧数据再写入。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, parser, scanner


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                workspace_path TEXT,
                agent_mode TEXT,
                model_id TEXT,
                created_at TEXT,
                last_modified_at TEXT,
                dir TEXT,
                signature TEXT,
                message_count INTEGER DEFAULT 0,
                context_usage_max REAL,
                turn_count INTEGER DEFAULT 0,
                turn_total_ms INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                tool_denied INTEGER DEFAULT 0,
                tool_failed INTEGER DEFAULT 0,
                tool_completed INTEGER DEFAULT 0,
                credits REAL DEFAULT 0,
                approvals TEXT,
                stop_reasons TEXT,
                credits_by_day TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                seq INTEGER,
                type TEXT,
                content TEXT,
                tool_name TEXT,
                kind TEXT,
                status TEXT,
                success INTEGER,
                duration_ms INTEGER,
                timestamp TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, seq);
            CREATE INDEX IF NOT EXISTS idx_messages_tool
                ON messages(type, tool_name);

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                session_id UNINDEXED,
                message_pk UNINDEXED,
                tokenize = 'unicode61'
            );

            CREATE TABLE IF NOT EXISTS analysis (
                session_id TEXT PRIMARY KEY,
                summary TEXT,
                tags TEXT,
                source TEXT,
                created_at TEXT,
                headline TEXT,
                facts TEXT,
                lang TEXT,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                seq INTEGER,
                task TEXT,
                completed INTEGER,
                list_description TEXT,
                source_seq INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_todos_session ON todos(session_id);
            CREATE INDEX IF NOT EXISTS idx_todos_completed ON todos(completed);

            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                content TEXT,
                source TEXT,
                created_at TEXT
            );

            -- 用户对待办的手动覆盖层,独立于索引数据(reindex 不触碰),
            -- 用 (session_id, seq) 作为跨重建稳定键。
            CREATE TABLE IF NOT EXISTS todo_overrides (
                session_id TEXT,
                seq INTEGER,
                status TEXT,          -- 'done' | 'cleared'
                task_snapshot TEXT,   -- 任务文本快照,防止 seq 错位张冠李戴
                updated_at TEXT,
                PRIMARY KEY (session_id, seq)
            );
            """
        )
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """对旧库补齐新增列(CREATE TABLE IF NOT EXISTS 不会改已存在的表)。"""
    def cols(table: str) -> set:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    session_cols = {
        "context_usage_max": "REAL",
        "turn_count": "INTEGER DEFAULT 0",
        "turn_total_ms": "INTEGER DEFAULT 0",
        "error_count": "INTEGER DEFAULT 0",
        "tool_denied": "INTEGER DEFAULT 0",
        "tool_failed": "INTEGER DEFAULT 0",
        "tool_completed": "INTEGER DEFAULT 0",
        "credits": "REAL DEFAULT 0",
        "approvals": "TEXT",
        "stop_reasons": "TEXT",
        "credits_by_day": "TEXT",
    }
    have = cols("sessions")
    for name, decl in session_cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {decl}")

    msg_cols = {"kind": "TEXT", "status": "TEXT"}
    have_m = cols("messages")
    for name, decl in msg_cols.items():
        if name not in have_m:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {decl}")

    # todos.source_seq(来源 tool_result 消息位置,供前端跳转)
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "todos" in tables and "source_seq" not in cols("todos"):
        conn.execute("ALTER TABLE todos ADD COLUMN source_seq INTEGER")

    # analysis 新增 headline / facts / lang
    if "analysis" in tables:
        have_a = cols("analysis")
        for name in ("headline", "facts", "lang", "note"):
            if name not in have_a:
                conn.execute(f"ALTER TABLE analysis ADD COLUMN {name} TEXT")


def _delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        "DELETE FROM messages_fts WHERE session_id = ?", (session_id,)
    )
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM todos WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def reindex(force: bool = False) -> Dict[str, int]:
    """扫描所有会话并增量写入索引。返回统计信息。"""
    init_db()
    conn = _connect()
    stats = {"scanned": 0, "indexed": 0, "skipped": 0, "messages": 0}
    try:
        existing = {
            row["id"]: row["signature"]
            for row in conn.execute("SELECT id, signature FROM sessions")
        }

        for session_dir in scanner.find_session_dirs():
            stats["scanned"] += 1
            meta = parser.parse_session_meta(session_dir)
            if not meta:
                continue

            sig = parser.session_signature(session_dir)
            sid = meta["id"]

            if not force and existing.get(sid) == sig:
                stats["skipped"] += 1
                continue

            parsed = parser.parse_messages(session_dir)
            messages = parsed["messages"]
            mstats = parsed["stats"]
            todos = parsed.get("todos")

            import json as _json
            _delete_session(conn, sid)
            conn.execute(
                """
                INSERT INTO sessions
                (id, title, workspace_path, agent_mode, model_id,
                 created_at, last_modified_at, dir, signature, message_count,
                 context_usage_max, turn_count, turn_total_ms, error_count,
                 tool_denied, tool_failed, tool_completed,
                 credits, approvals, stop_reasons, credits_by_day)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sid,
                    meta["title"],
                    meta["workspace_path"],
                    meta["agent_mode"],
                    meta["model_id"],
                    meta["created_at"],
                    meta["last_modified_at"],
                    meta["dir"],
                    sig,
                    len(messages),
                    mstats["context_usage_max"],
                    mstats["turn_count"],
                    mstats["turn_total_ms"],
                    mstats["error_count"],
                    mstats["tool_denied"],
                    mstats["tool_failed"],
                    mstats["tool_completed"],
                    round(mstats.get("credits", 0.0), 4),
                    _json.dumps(mstats.get("approvals") or {}),
                    _json.dumps(mstats.get("stop_reasons") or {}),
                    _json.dumps(mstats.get("credits_by_day") or {}),
                ),
            )

            for m in messages:
                cur = conn.execute(
                    """
                    INSERT INTO messages
                    (session_id, seq, type, content, tool_name, kind, status,
                     success, duration_ms, timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        sid,
                        m["seq"],
                        m["type"],
                        m["content"],
                        m["tool_name"],
                        m.get("kind", ""),
                        m.get("status", ""),
                        1 if m["success"] else (0 if m["success"] is not None else None),
                        m["duration_ms"],
                        m["timestamp"],
                    ),
                )
                if m["searchable"] and m["content"]:
                    conn.execute(
                        "INSERT INTO messages_fts (content, session_id, message_pk) VALUES (?,?,?)",
                        (m["content"], sid, cur.lastrowid),
                    )

            if todos and todos.get("items"):
                src = todos.get("source_seq")
                for i, it in enumerate(todos["items"]):
                    conn.execute(
                        """
                        INSERT INTO todos (session_id, seq, task, completed, list_description, source_seq)
                        VALUES (?,?,?,?,?,?)
                        """,
                        (sid, i, it["task"], 1 if it["completed"] else 0, todos.get("description", ""), src),
                    )

            stats["indexed"] += 1
            stats["messages"] += len(messages)

        conn.commit()
    finally:
        conn.close()
    return stats


# 允许的排序字段 -> SQL 片段(白名单,防注入)
_SORT_MAP = {
    "modified_desc": "last_modified_at DESC",
    "modified_asc": "last_modified_at ASC",
    "created_desc": "created_at DESC",
    "created_asc": "created_at ASC",
    "messages_desc": "message_count DESC",
    "messages_asc": "message_count ASC",
    "title_asc": "title COLLATE NOCASE ASC",
}


def list_sessions(
    q_workspace: Optional[str] = None,
    model_id: Optional[str] = None,
    agent_mode: Optional[str] = None,
    sort: str = "modified_desc",
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    conn = _connect()
    try:
        clauses: List[str] = []
        params: List[Any] = []
        if q_workspace:
            clauses.append("workspace_path LIKE ?")
            params.append(f"%{q_workspace}%")
        if model_id:
            clauses.append("model_id = ?")
            params.append(model_id)
        if agent_mode:
            clauses.append("agent_mode = ?")
            params.append(agent_mode)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        order = _SORT_MAP.get(sort, _SORT_MAP["modified_desc"])

        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM sessions {where}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT id, title, workspace_path, agent_mode, model_id,
                   created_at, last_modified_at, message_count
            FROM sessions {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        return {
            "total": total,
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def facets() -> Dict[str, Any]:
    """返回可用于筛选的维度值:模型、模式、工作区。"""
    conn = _connect()
    try:
        def _col(col: str) -> List[Dict[str, Any]]:
            rows = conn.execute(
                f"""
                SELECT {col} AS value, COUNT(*) AS count
                FROM sessions
                WHERE {col} IS NOT NULL AND {col} != ''
                GROUP BY {col} ORDER BY count DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

        return {
            "models": _col("model_id"),
            "modes": _col("agent_mode"),
            "workspaces": _col("workspace_path"),
        }
    finally:
        conn.close()


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        s = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not s:
            return None
        msgs = conn.execute(
            """
            SELECT seq, type, content, tool_name, success, duration_ms, timestamp
            FROM messages WHERE session_id = ? ORDER BY seq
            """,
            (session_id,),
        ).fetchall()
        result = dict(s)
        result["messages"] = [dict(m) for m in msgs]
        return result
    finally:
        conn.close()


def search(query: str, limit: int = 50) -> Dict[str, Any]:
    """全文检索,返回命中消息 + 高亮片段 + 所属会话信息。"""
    conn = _connect()
    try:
        # 用 FTS5 的 snippet() 生成高亮片段;转义双引号做 phrase 查询更稳
        safe = query.replace('"', '""')
        fts_query = f'"{safe}"'
        rows = conn.execute(
            """
            SELECT
                f.session_id AS session_id,
                f.message_pk AS message_pk,
                snippet(messages_fts, 0, '<mark>', '</mark>', ' … ', 12) AS snippet,
                s.title AS title,
                s.workspace_path AS workspace_path,
                s.last_modified_at AS last_modified_at,
                m.seq AS seq,
                m.type AS type,
                m.timestamp AS timestamp
            FROM messages_fts f
            JOIN sessions s ON s.id = f.session_id
            JOIN messages m ON m.id = f.message_pk
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return {"query": query, "count": len(rows), "items": [dict(r) for r in rows]}
    finally:
        conn.close()


def stats() -> Dict[str, Any]:
    conn = _connect()
    try:
        s = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        m = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        return {"sessions": s, "messages": m, "db_path": str(config.DB_PATH)}
    finally:
        conn.close()


def get_profile() -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT content, source, created_at FROM profile WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_profile(content: str, source: str, created_at: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO profile (id, content, source, created_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content=excluded.content, source=excluded.source, created_at=excluded.created_at
            """,
            (content, source, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def list_todos(status: str = "open", show_cleared: bool = False, limit: int = 200) -> Dict[str, Any]:
    """按会话分组返回历史待办,叠加用户手动覆盖(override)。

    status: open(仅未完成) | done(仅已完成) | all(全部,不含已清除)
    show_cleared: 为 True 时额外返回已清除项(仅在此模式可见)
    有效状态 = override 优先(仅当 task 快照与当前一致),否则用索引原始 completed。
    """
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT t.session_id, t.seq, t.task, t.completed, t.list_description, t.source_seq,
                   s.title, s.workspace_path, s.last_modified_at,
                   o.status AS ov_status, o.task_snapshot AS ov_snap
            FROM todos t
            JOIN sessions s ON s.id = t.session_id
            LEFT JOIN todo_overrides o
                   ON o.session_id = t.session_id AND o.seq = t.seq
            ORDER BY s.last_modified_at DESC, t.seq ASC
            """
        ).fetchall()

        groups: Dict[str, Any] = {}
        order: List[str] = []
        tot = {"total": 0, "open": 0, "done": 0, "cleared": 0}
        sess_set = set()

        for r in rows:
            # 覆盖仅在任务文本快照一致时生效,避免 seq 错位张冠李戴
            ov = r["ov_status"] if (r["ov_status"] and r["ov_snap"] == r["task"]) else None
            cleared = ov == "cleared"
            if ov == "done":
                completed = True
            elif ov == "open":
                completed = False
            else:
                completed = bool(r["completed"])

            # 全局统计:cleared 单独计,不计入 open/done/total
            if cleared:
                tot["cleared"] += 1
            else:
                tot["total"] += 1
                tot["done" if completed else "open"] += 1
            sess_set.add(r["session_id"])

            # 按当前视图过滤
            if cleared:
                if not show_cleared:
                    continue
            else:
                if show_cleared:
                    continue  # 已清除视图只看 cleared
                if status == "open" and completed:
                    continue
                if status == "done" and not completed:
                    continue

            sid = r["session_id"]
            if sid not in groups:
                groups[sid] = {
                    "session_id": sid,
                    "title": r["title"],
                    "workspace_path": r["workspace_path"],
                    "last_modified_at": r["last_modified_at"],
                    "list_description": r["list_description"],
                    "source_seq": r["source_seq"],
                    "items": [],
                }
                order.append(sid)
            groups[sid]["items"].append({
                "seq": r["seq"],
                "task": r["task"],
                "completed": completed,
                "cleared": cleared,
                "overridden": ov is not None,
            })

        items = [groups[sid] for sid in order][:limit]
        tot["sessions"] = len(sess_set)

        return {"status": status, "show_cleared": show_cleared, "sessions": items, "totals": tot}
    finally:
        conn.close()


def set_todo_override(session_id: str, seq: int, status: str, task_snapshot: str) -> None:
    """设置/更新/移除某条待办的手动覆盖。

    status: done | cleared | open(open 表示恢复原始状态,即删除覆盖行)
    """
    conn = _connect()
    try:
        if status == "open":
            conn.execute(
                "DELETE FROM todo_overrides WHERE session_id = ? AND seq = ?",
                (session_id, seq),
            )
        else:
            conn.execute(
                """
                INSERT INTO todo_overrides (session_id, seq, status, task_snapshot, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(session_id, seq) DO UPDATE SET
                    status=excluded.status, task_snapshot=excluded.task_snapshot,
                    updated_at=excluded.updated_at
                """,
                (session_id, seq, status, task_snapshot,
                 datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()
    finally:
        conn.close()


def get_todo_task(session_id: str, seq: int) -> Optional[str]:
    """取某条待办当前的任务文本(用于写快照/校验)。"""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT task FROM todos WHERE session_id = ? AND seq = ?",
            (session_id, seq),
        ).fetchone()
        return row["task"] if row else None
    finally:
        conn.close()


def _yaml_escape(v: str) -> str:
    v = str(v or "").replace('"', '\\"')
    return f'"{v}"'


def export_session_markdown(
    session_id: str, include_tools: bool = False, front_matter: bool = True,
    session: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """把一个会话导出为 Markdown。

    include_tools: 是否包含工具调用/结果(默认否,只导出 user/assistant 对话)。
    front_matter: 是否在开头加 YAML front-matter(便于其它工具解析)。
    session: 可传入已取好的会话对象(批量导出时复用,避免重复查询)。
    """
    s = session if session is not None else get_session(session_id)
    if s is None:
        return None

    lines: List[str] = []
    if front_matter:
        lines.append("---")
        lines.append(f"title: {_yaml_escape(s['title'] or '(无标题)')}")
        lines.append(f"session_id: {_yaml_escape(s['id'])}")
        lines.append(f"workspace: {_yaml_escape(s['workspace_path'] or '')}")
        lines.append(f"agent_mode: {_yaml_escape(s['agent_mode'] or '')}")
        lines.append(f"model: {_yaml_escape(s['model_id'] or '')}")
        lines.append(f"created_at: {_yaml_escape(s['created_at'] or '')}")
        lines.append(f"updated_at: {_yaml_escape(s['last_modified_at'] or '')}")
        lines.append(f"message_count: {s['message_count']}")
        lines.append("---\n")

    lines.append(f"# {s['title'] or '(无标题)'}\n")

    for m in s["messages"]:
        t = m["type"]
        if t == "user":
            lines.append(f"## 用户\n\n{m['content']}\n")
        elif t == "assistant":
            lines.append(f"## 助手\n\n{m['content']}\n")
        elif not include_tools:
            continue
        elif t == "tool_call":
            lines.append(f"### 🔧 工具调用: {m['tool_name'] or 'tool'}\n\n```json\n{m['content']}\n```\n")
        elif t == "tool_result":
            ok = "✓" if m["success"] == 1 else ("✗" if m["success"] == 0 else "")
            lines.append(f"### 📎 工具结果 {ok}\n\n```\n{m['content']}\n```\n")

    return "\n".join(lines)


def _slug(text: str, maxlen: int = 50) -> str:
    """把标题清洗成安全的文件名片段。"""
    import re as _re
    # 仅保留 unicode 单词字符(含中文)、空白、点、连字符,其余替换为空格
    s = _re.sub(r"[^\w\s.\-]", " ", str(text or "").strip(), flags=_re.UNICODE)
    s = _re.sub(r"\s+", " ", s).strip().strip(".")
    return (s[:maxlen].strip() or "session")


def export_bundle(ids, include_tools: bool = False, bundle: str = "single"):
    """批量导出多个会话。

    bundle="single" -> 返回 (content_str, "md")   合并成一个 Markdown
    bundle="zip"    -> 返回 (zip_bytes, "zip")     每会话一个 .md 打包
    仅导出存在的会话;全部无效返回 None。
    """
    sessions = []
    for sid in ids:
        s = get_session(sid)
        if s is not None:
            sessions.append(s)
    if not sessions:
        return None

    if bundle == "zip":
        import io, zipfile
        buf = io.BytesIO()
        used = set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for s in sessions:
                md = export_session_markdown(s["id"], include_tools=include_tools, session=s)
                name = _slug(s["title"] or s["id"])
                short = str(s["id"]).replace("sess_", "")[:8]
                fname = f"{name}-{short}.md"
                n = 1
                while fname in used:
                    n += 1
                    fname = f"{name}-{short}-{n}.md"
                used.add(fname)
                zf.writestr(fname, md or "")
        return buf.getvalue(), "zip"

    # single: 合并为一个 Markdown,顶部加目录
    parts = [f"# Kiro 会话导出({len(sessions)} 个会话)\n"]
    parts.append("## 目录\n")
    for i, s in enumerate(sessions, 1):
        parts.append(f"{i}. {s['title'] or '(无标题)'}")
    parts.append("")
    for s in sessions:
        parts.append("\n---\n")
        parts.append(export_session_markdown(s["id"], include_tools=include_tools, front_matter=True, session=s) or "")
    return "\n".join(parts), "md"


def get_analysis(session_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT session_id, summary, tags, source, created_at, headline, facts, lang, note FROM analysis WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("facts"):
            import json as _json
            try:
                d["facts"] = _json.loads(d["facts"])
            except (ValueError, TypeError):
                d["facts"] = None
        return d
    finally:
        conn.close()


def save_analysis(
    session_id: str, summary: str, tags: str, source: str, created_at: str,
    headline: str = "", facts=None, lang: str = "zh", note: str = "",
) -> None:
    import json as _json
    facts_json = _json.dumps(facts, ensure_ascii=False) if facts is not None else None
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO analysis (session_id, summary, tags, source, created_at, headline, facts, lang, note)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                summary=excluded.summary, tags=excluded.tags,
                source=excluded.source, created_at=excluded.created_at,
                headline=excluded.headline, facts=excluded.facts, lang=excluded.lang,
                note=excluded.note
            """,
            (session_id, summary, tags, source, created_at, headline, facts_json, lang, note or ""),
        )
        conn.commit()
    finally:
        conn.close()


def dashboard() -> Dict[str, Any]:
    """统计仪表盘聚合数据。"""
    conn = _connect()
    try:
        totals = {
            "sessions": conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"],
            "messages": conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"],
            "user_messages": conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE type='user'"
            ).fetchone()["c"],
            "assistant_messages": conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE type='assistant'"
            ).fetchone()["c"],
            "tool_calls": conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE type='tool_call'"
            ).fetchone()["c"],
        }

        # 按天活跃度(会话创建数),取 last_modified 的日期部分
        # 每日积分消耗:按每条 usage_summary 事件的当天精确归集(跨天会话正确分散),
        # 取最近 30 天、倒序(最近在前)
        import json as _jd
        daily: Dict[str, float] = {}
        for row in conn.execute(
            "SELECT credits_by_day FROM sessions WHERE credits_by_day IS NOT NULL AND credits_by_day != ''"
        ):
            try:
                for day, c in (_jd.loads(row["credits_by_day"]) or {}).items():
                    if isinstance(c, (int, float)):
                        daily[day] = daily.get(day, 0.0) + c
            except (ValueError, TypeError):
                pass
        by_day = [
            {"day": d, "credits": round(v, 1)}
            for d, v in sorted(daily.items(), reverse=True)[:30]
        ]

        by_model = [
            dict(r)
            for r in conn.execute(
                """
                SELECT COALESCE(NULLIF(model_id,''),'(未知)') AS model, COUNT(*) AS count
                FROM sessions GROUP BY model ORDER BY count DESC
                """
            ).fetchall()
        ]

        by_mode = [
            dict(r)
            for r in conn.execute(
                """
                SELECT COALESCE(NULLIF(agent_mode,''),'(未知)') AS mode, COUNT(*) AS count
                FROM sessions GROUP BY mode ORDER BY count DESC
                """
            ).fetchall()
        ]

        by_workspace = [
            dict(r)
            for r in conn.execute(
                """
                SELECT COALESCE(NULLIF(workspace_path,''),'(无)') AS workspace, COUNT(*) AS count
                FROM sessions GROUP BY workspace ORDER BY count DESC LIMIT 15
                """
            ).fetchall()
        ]

        top_sessions = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, title, message_count, workspace_path, last_modified_at
                FROM sessions ORDER BY message_count DESC LIMIT 10
                """
            ).fetchall()
        ]

        # ---- 工具维度 ----
        # 工具调用排行(按 tool_call 计数)
        tool_top = [
            dict(r)
            for r in conn.execute(
                """
                SELECT tool_name, COUNT(*) AS calls
                FROM messages
                WHERE type='tool_call' AND tool_name != ''
                GROUP BY tool_name ORDER BY calls DESC LIMIT 15
                """
            ).fetchall()
        ]

        # 工具成功率与耗时(来自 tool_result,已带 tool_name)
        tool_perf = [
            dict(r)
            for r in conn.execute(
                """
                SELECT tool_name,
                       COUNT(*) AS runs,
                       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS ok,
                       AVG(duration_ms) AS avg_ms,
                       MAX(duration_ms) AS max_ms
                FROM messages
                WHERE type='tool_result' AND tool_name != ''
                GROUP BY tool_name
                HAVING runs >= 3
                ORDER BY runs DESC LIMIT 15
                """
            ).fetchall()
        ]
        for r in tool_perf:
            r["success_rate"] = round((r["ok"] / r["runs"]) * 100, 1) if r["runs"] else 0
            r["avg_ms"] = round(r["avg_ms"], 0) if r["avg_ms"] is not None else None

        # 按操作类型(kind)分布
        by_kind = [
            dict(r)
            for r in conn.execute(
                """
                SELECT kind, COUNT(*) AS count
                FROM messages
                WHERE type='tool_call' AND kind != ''
                GROUP BY kind ORDER BY count DESC
                """
            ).fetchall()
        ]

        # 工具状态汇总(会话级计数求和)
        st = conn.execute(
            """
            SELECT
                COALESCE(SUM(tool_completed),0) AS completed,
                COALESCE(SUM(tool_failed),0) AS failed,
                COALESCE(SUM(tool_denied),0) AS denied,
                COALESCE(SUM(error_count),0) AS errors
            FROM sessions
            """
        ).fetchone()
        tool_status = dict(st)

        # ---- 模型维度 ----
        by_model_detail = [
            dict(r)
            for r in conn.execute(
                """
                SELECT COALESCE(NULLIF(model_id,''),'(未知)') AS model,
                       COUNT(*) AS sessions,
                       SUM(message_count) AS messages,
                       ROUND(AVG(message_count),1) AS avg_messages,
                       ROUND(AVG(turn_count),1) AS avg_turns,
                       ROUND(AVG(context_usage_max),1) AS avg_ctx,
                       ROUND(AVG(NULLIF(turn_total_ms,0))/1000.0,1) AS avg_turn_secs
                FROM sessions
                GROUP BY model ORDER BY sessions DESC
                """
            ).fetchall()
        ]

        # ---- 工作区维度 ----
        by_workspace_detail = [
            dict(r)
            for r in conn.execute(
                """
                SELECT COALESCE(NULLIF(workspace_path,''),'(无)') AS workspace,
                       COUNT(*) AS sessions,
                       SUM(message_count) AS messages,
                       ROUND(AVG(context_usage_max),1) AS avg_ctx,
                       MIN(created_at) AS first_at,
                       MAX(last_modified_at) AS last_at
                FROM sessions
                GROUP BY workspace ORDER BY sessions DESC LIMIT 15
                """
            ).fetchall()
        ]

        # ---- 上下文占用 Top ----
        top_context = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, title, context_usage_max, workspace_path
                FROM sessions
                WHERE context_usage_max IS NOT NULL
                ORDER BY context_usage_max DESC LIMIT 10
                """
            ).fetchall()
        ]

        # ---- 技能使用(skill)----
        # skill 通过 disclose_context 工具激活,args.name 是技能名(存在消息 content 里)
        import re as _re
        skill_counter: Dict[str, int] = {}
        skill_sessions: Dict[str, set] = {}
        for row in conn.execute(
            """
            SELECT session_id, content FROM messages
            WHERE type='tool_call' AND tool_name='disclose_context'
            """
        ):
            m = _re.search(r'"name"\s*:\s*"([^"]+)"', row["content"] or "")
            if not m:
                continue
            name = m.group(1)
            skill_counter[name] = skill_counter.get(name, 0) + 1
            skill_sessions.setdefault(name, set()).add(row["session_id"])
        by_skill = [
            {"skill": k, "count": v, "sessions": len(skill_sessions.get(k, ()))}
            for k, v in sorted(skill_counter.items(), key=lambda x: -x[1])
        ][:15]
        skill_totals = {
            "activations": sum(skill_counter.values()),
            "distinct": len(skill_counter),
        }

        # ---- 积分成本 ----
        credits_total = conn.execute(
            "SELECT COALESCE(SUM(credits),0) AS c FROM sessions"
        ).fetchone()["c"]
        credits_by_model = [
            dict(r)
            for r in conn.execute(
                """
                SELECT COALESCE(NULLIF(model_id,''),'(未知)') AS model,
                       ROUND(SUM(credits),2) AS credits
                FROM sessions GROUP BY model HAVING credits > 0 ORDER BY credits DESC
                """
            ).fetchall()
        ]
        top_credit_sessions = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, title, ROUND(credits,2) AS credits, workspace_path, model_id
                FROM sessions WHERE credits > 0 ORDER BY credits DESC LIMIT 10
                """
            ).fetchall()
        ]

        # ---- 审批行为 & 结束原因(JSON 列,Python 汇总)----
        import json as _json2
        approvals = {"accept": 0, "always_allow": 0, "reject": 0, "always_reject": 0}
        stop_reasons = {"end_turn": 0, "aborted": 0, "cancelled": 0, "failed": 0, "error": 0}
        for row in conn.execute("SELECT approvals, stop_reasons FROM sessions"):
            for col, agg in (("approvals", approvals), ("stop_reasons", stop_reasons)):
                if row[col]:
                    try:
                        for k, v in (_json2.loads(row[col]) or {}).items():
                            if k in agg and isinstance(v, int):
                                agg[k] += v
                    except (ValueError, TypeError):
                        pass
        appr_total = sum(approvals.values())
        approvals_summary = {
            **approvals,
            "total": appr_total,
            "reject_rate": round((approvals["reject"] + approvals["always_reject"]) / appr_total * 100, 1) if appr_total else 0,
        }

        return {
            "totals": totals,
            "by_day": by_day,
            "by_model": by_model,
            "by_mode": by_mode,
            "by_workspace": by_workspace,
            "top_sessions": top_sessions,
            "tool_top": tool_top,
            "tool_perf": tool_perf,
            "by_kind": by_kind,
            "tool_status": tool_status,
            "by_model_detail": by_model_detail,
            "by_workspace_detail": by_workspace_detail,
            "top_context": top_context,
            "by_skill": by_skill,
            "skill_totals": skill_totals,
            "credits_total": round(credits_total, 2),
            "credits_by_model": credits_by_model,
            "top_credit_sessions": top_credit_sessions,
            "approvals": approvals_summary,
            "stop_reasons": stop_reasons,
        }
    finally:
        conn.close()
