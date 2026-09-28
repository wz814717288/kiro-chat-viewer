"""解析器:把一个会话目录解析成结构化数据。

- session.json  -> 会话元数据(标题、工作区、模型、时间等)
- messages.jsonl -> 逐行事件流,归一化成消息列表

关键点:messages.jsonl 是 JSONL(每行一个 JSON),必须逐行解析。
文件末尾可能出现半行(Kiro 正在写入),需容错跳过。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 参与展示/检索的消息类型;控制类事件(turn_start/turn_end/session_metadata)忽略
DISPLAY_TYPES = {"user", "assistant", "tool_call", "tool_result"}
# 参与全文检索的类型(工具输出通常噪声大、体积大,默认不进全文索引)
SEARCHABLE_TYPES = {"user", "assistant"}
# 视为"最终态"的工具状态(用于去重时优先保留)
_TERMINAL_STATUS = {"completed", "failed", "denied"}


def _parse_ts(ts: str) -> Optional[datetime]:
    """解析 ISO 时间戳(形如 2026-03-19T08:58:46.795Z)。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_session_meta(session_dir: Path) -> Optional[Dict[str, Any]]:
    """解析 session.json,返回归一化的会话元数据。失败返回 None。"""
    meta_file = session_dir / "session.json"
    if not meta_file.exists():
        return None
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None

    workspace_paths = data.get("workspacePaths") or []
    workspace = workspace_paths[0] if workspace_paths else ""

    return {
        "id": data.get("id") or session_dir.name,
        "title": (data.get("title") or "").strip(),
        "workspace_path": workspace,
        "workspace_paths": workspace_paths,
        "agent_mode": data.get("agentMode") or "",
        "model_id": data.get("modelId") or "",
        "created_at": data.get("createdAt") or "",
        "last_modified_at": data.get("lastModifiedAt") or "",
        "dir": str(session_dir),
    }


def _extract_content(msg_type: str, payload: Dict[str, Any]) -> str:
    """按类型提取可读文本内容。"""
    if msg_type in ("user", "assistant"):
        content = payload.get("content")
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if msg_type == "tool_call":
        tool = payload.get("toolName") or payload.get("actionType") or "tool"
        args = payload.get("args")
        args_str = json.dumps(args, ensure_ascii=False) if args is not None else ""
        return f"{tool} {args_str}".strip()
    if msg_type == "tool_result":
        content = payload.get("content")
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return ""


def _empty_stats() -> Dict[str, Any]:
    return {
        "context_usage_max": None,
        "turn_count": 0,
        "turn_total_ms": 0,
        "error_count": 0,
        "tool_denied": 0,
        "tool_failed": 0,
        "tool_completed": 0,
        # 积分成本(usage_summary.promptTurnSummaries[].usage 求和)
        "credits": 0.0,
        # 每日积分明细(按 usage_summary 事件当天 UTC 归集):{date: credits}
        "credits_by_day": {},
        # 工具审批行为(interaction_resolved.selectedOption 计数)
        "approvals": {"accept": 0, "always_allow": 0, "reject": 0, "always_reject": 0},
        # 会话结束原因(turn_end.stopReason 计数)
        "stop_reasons": {"end_turn": 0, "aborted": 0, "cancelled": 0, "failed": 0, "error": 0},
    }


# interaction_resolved.selectedOption -> 归一化键
_APPROVAL_MAP = {
    "accept": "accept",
    "always-accept": "always_allow",
    "reject": "reject",
    "always-reject": "always_reject",
}


def parse_messages(session_dir: Path) -> Dict[str, Any]:
    """逐行解析 messages.jsonl。

    返回 {"messages": [...], "stats": {...}}:
    - messages: 归一化后的可展示消息(tool_call/tool_result 已按 toolCallId 去重,
      tool_result 补齐了 tool_name/kind)。
    - stats: 会话级聚合(上下文占用峰值、轮次数与总耗时、错误数、工具状态计数)。
    """
    msg_file = session_dir / "messages.jsonl"
    if not msg_file.exists():
        return {"messages": [], "stats": _empty_stats()}

    # 第一遍:读入所有事件
    events: List[Dict[str, Any]] = []
    try:
        with msg_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 容忍写入中的半行/损坏行
    except (OSError, UnicodeDecodeError):
        return {"messages": [], "stats": _empty_stats()}

    stats = _empty_stats()

    # 工具调用/结果按 toolCallId 归并(同一调用可能有多条状态流转事件)
    tool_calls: Dict[str, Dict[str, Any]] = {}
    tool_results: Dict[str, Dict[str, Any]] = {}
    # 轮次计时:executionId -> turn_start 时间
    turn_starts: Dict[str, datetime] = {}
    # 待办:按顺序收集所有 todo_list 结果 (toolCallId, content),最后取最新的非空清单
    todo_contents: List[tuple] = []

    for e in events:
        p = e.get("payload") or {}
        t = p.get("type", "")
        ts = e.get("timestamp") or ""

        if t == "tool_call":
            tid = p.get("toolCallId") or e.get("id") or ""
            entry = tool_calls.get(tid) or {"first_ts": ts}
            entry["tool_name"] = p.get("toolName") or p.get("actionType") or entry.get("tool_name") or "tool"
            entry["kind"] = p.get("kind") or entry.get("kind") or ""
            # 状态:最终态优先,否则保留最后出现
            new_status = p.get("status") or ""
            if new_status in _TERMINAL_STATUS or "status" not in entry:
                entry["status"] = new_status
            entry["args"] = p.get("args")
            tool_calls[tid] = entry

        elif t == "tool_result":
            tid = p.get("toolCallId") or ""
            content = p.get("content")
            tool_results[tid] = {
                "content": content,
                "success": p.get("success"),
                "duration_ms": p.get("durationMs"),
                "ts": ts,
            }
            # 若对应的是 todo_list 调用,收集结果内容
            call = tool_calls.get(tid)
            if call and call.get("tool_name") == "todo_list" and isinstance(content, str):
                todo_contents.append((tid, content))

        elif t == "session_metadata":
            key = p.get("key")
            if key == "contextUsage":
                pct = (p.get("value") or {}).get("usagePercentage")
                if isinstance(pct, (int, float)):
                    cur = stats["context_usage_max"]
                    stats["context_usage_max"] = pct if cur is None else max(cur, pct)
            elif key == "displayError":
                stats["error_count"] += 1

        elif t == "turn_start":
            eid = p.get("executionId") or e.get("id") or ""
            dt = _parse_ts(ts)
            if eid and dt:
                turn_starts[eid] = dt

        elif t == "turn_end":
            eid = p.get("executionId") or ""
            stats["turn_count"] += 1
            reason = p.get("stopReason")
            if reason in stats["stop_reasons"]:
                stats["stop_reasons"][reason] += 1
            start = turn_starts.get(eid)
            end = _parse_ts(ts)
            if start and end:
                delta = (end - start).total_seconds() * 1000.0
                if delta >= 0:
                    stats["turn_total_ms"] += int(delta)

        elif t == "usage_summary":
            day = ts[:10]  # 事件当天(UTC)
            for s in (p.get("promptTurnSummaries") or []):
                u = s.get("usage")
                if isinstance(u, (int, float)):
                    stats["credits"] += float(u)
                    if day:
                        stats["credits_by_day"][day] = stats["credits_by_day"].get(day, 0.0) + float(u)

        elif t == "interaction_resolved":
            opt = p.get("selectedOption")
            key = _APPROVAL_MAP.get(opt)
            if key:
                stats["approvals"][key] += 1

    # 工具状态计数(去重后)
    for c in tool_calls.values():
        st = c.get("status")
        if st == "denied":
            stats["tool_denied"] += 1
        elif st == "failed":
            stats["tool_failed"] += 1
        elif st == "completed":
            stats["tool_completed"] += 1

    # 第二遍:按原始顺序产出去重后的消息
    messages: List[Dict[str, Any]] = []
    seq = 0
    seen_calls = set()
    seen_results = set()
    result_seq_by_tid: Dict[str, int] = {}  # tool_result 的 toolCallId -> 消息 seq

    for e in events:
        p = e.get("payload") or {}
        t = p.get("type", "")
        if t not in DISPLAY_TYPES:
            continue
        ts = e.get("timestamp") or ""

        if t in ("user", "assistant"):
            messages.append({
                "seq": seq, "type": t, "content": _extract_content(t, p),
                "tool_name": "", "kind": "", "status": "",
                "success": None, "duration_ms": None, "timestamp": ts,
                "searchable": True,
            })
            seq += 1

        elif t == "tool_call":
            tid = p.get("toolCallId") or e.get("id") or ""
            if tid in seen_calls:
                continue
            seen_calls.add(tid)
            c = tool_calls.get(tid, {})
            tool = c.get("tool_name", "tool")
            args = c.get("args")
            args_str = json.dumps(args, ensure_ascii=False) if args is not None else ""
            messages.append({
                "seq": seq, "type": t, "content": f"{tool} {args_str}".strip(),
                "tool_name": tool, "kind": c.get("kind", ""), "status": c.get("status", ""),
                "success": None, "duration_ms": None, "timestamp": ts,
                "searchable": False,
            })
            seq += 1

        elif t == "tool_result":
            tid = p.get("toolCallId") or ""
            if tid in seen_results:
                continue
            seen_results.add(tid)
            result_seq_by_tid[tid] = seq
            r = tool_results.get(tid, {})
            content = r.get("content")
            call = tool_calls.get(tid, {})
            messages.append({
                "seq": seq, "type": t,
                "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                "tool_name": call.get("tool_name", ""), "kind": call.get("kind", ""), "status": "",
                "success": r.get("success"), "duration_ms": r.get("duration_ms"), "timestamp": ts,
                "searchable": False,
            })
            seq += 1

    # 取最后一条能解析出非空任务列表的清单(会话结尾常把清单清空,故不能只取最后一条)
    todos = None
    for tid, content in reversed(todo_contents):
        parsed = _parse_todos(content)
        if parsed:
            # 记录该清单来源的 tool_result 消息位置,供前端跳转
            parsed["source_seq"] = result_seq_by_tid.get(tid)
            todos = parsed
            break

    return {"messages": messages, "stats": stats, "todos": todos}


def _parse_todos(content: Optional[str]) -> Optional[Dict[str, Any]]:
    """把 todo_list 的结果 JSON 解析成结构化待办清单。

    结果形如 {"tasks":[{"id","task_description","completed"}],"description":...}。
    返回 {"description", "total", "open", "done", "items":[{"task","completed"}]}。
    """
    if not content:
        return None
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None

    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return None

    items: List[Dict[str, Any]] = []
    done = 0
    for t in tasks:
        if not isinstance(t, dict):
            continue
        desc = (t.get("task_description") or "").strip()
        if not desc:
            continue
        completed = bool(t.get("completed"))
        if completed:
            done += 1
        items.append({"task": desc, "completed": completed})

    if not items:
        return None

    return {
        "description": (data.get("description") or "").strip(),
        "total": len(items),
        "open": len(items) - done,
        "done": done,
        "items": items,
    }


def session_signature(session_dir: Path) -> str:
    """messages.jsonl 的 (mtime, size) 指纹,用于增量索引判断是否变更。"""
    msg_file = session_dir / "messages.jsonl"
    try:
        st = msg_file.stat()
        return f"{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return "0:0"
