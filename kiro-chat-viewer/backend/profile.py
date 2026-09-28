"""用户画像("Kiro 眼中的你"):聚合全局历史数据,生成用于桥接的分析 prompt。

半自动桥接流程:
1. 前端点按钮 -> 调 build_prompt() 拿到拼好的 prompt
2. 用户把 prompt 粘进 Kiro IDE 对话,让 IDE 里的 Kiro 生成画像
3. 用户把回答贴回前端 -> save_profile 存库

时间戳为 UTC(源数据带 Z),小时分布按 UTC 统计。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from . import db, kirocli


def gather(conn) -> Dict[str, Any]:
    """从索引库聚合画像所需的统计数据与样本提问。"""
    def one(sql: str, params=()):
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else {}

    def many(sql: str, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    totals = one(
        """
        SELECT COUNT(*) AS sessions,
               MIN(created_at) AS first_at,
               MAX(last_modified_at) AS last_at
        FROM sessions
        """
    )
    msg_totals = one(
        """
        SELECT
            SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS user_msgs,
            SUM(CASE WHEN type='assistant' THEN 1 ELSE 0 END) AS assistant_msgs,
            SUM(CASE WHEN type='tool_call' THEN 1 ELSE 0 END) AS tool_calls
        FROM messages
        """
    )
    # 用户提问平均长度、中文占比
    style = one(
        """
        SELECT AVG(LENGTH(content)) AS avg_len
        FROM messages WHERE type='user'
        """
    )

    by_hour = many(
        """
        SELECT CAST(substr(timestamp, 12, 2) AS INTEGER) AS hour, COUNT(*) AS c
        FROM messages
        WHERE type='user' AND length(timestamp) >= 13
        GROUP BY hour ORDER BY hour
        """
    )
    by_weekday = many(
        """
        SELECT strftime('%w', substr(timestamp,1,10)) AS wd, COUNT(*) AS c
        FROM messages
        WHERE type='user' AND length(timestamp) >= 10
        GROUP BY wd ORDER BY wd
        """
    )
    top_workspaces = many(
        """
        SELECT COALESCE(NULLIF(workspace_path,''),'(无)') AS ws, COUNT(*) AS c
        FROM sessions GROUP BY ws ORDER BY c DESC LIMIT 8
        """
    )
    top_models = many(
        """
        SELECT COALESCE(NULLIF(model_id,''),'(未知)') AS model, COUNT(*) AS c
        FROM sessions GROUP BY model ORDER BY c DESC LIMIT 8
        """
    )
    top_tools = many(
        """
        SELECT tool_name, COUNT(*) AS c
        FROM messages WHERE type='tool_call' AND tool_name != ''
        GROUP BY tool_name ORDER BY c DESC LIMIT 10
        """
    )
    tool_status = one(
        """
        SELECT COALESCE(SUM(tool_completed),0) AS completed,
               COALESCE(SUM(tool_failed),0) AS failed,
               COALESCE(SUM(tool_denied),0) AS denied
        FROM sessions
        """
    )
    todos = one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) AS done
        FROM todos
        """
    )
    ctx = one("SELECT AVG(context_usage_max) AS avg_ctx, MAX(context_usage_max) AS max_ctx FROM sessions")

    # 主题标签(来自已生成的单会话分析)
    tags = many(
        """
        SELECT tags FROM analysis WHERE tags IS NOT NULL AND tags != ''
        """
    )
    tag_counter: Dict[str, int] = {}
    for row in tags:
        for tg in (row["tags"] or "").split(","):
            tg = tg.strip()
            if tg:
                tag_counter[tg] = tag_counter.get(tg, 0) + 1
    top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:15]

    # 样本提问:每个较近会话取首条用户消息的首行,截断
    samples_rows = conn.execute(
        """
        SELECT m.content
        FROM messages m
        JOIN sessions s ON s.id = m.session_id
        WHERE m.type='user'
        ORDER BY s.last_modified_at DESC
        LIMIT 400
        """
    ).fetchall()
    seen = set()
    samples: List[str] = []
    for r in samples_rows:
        line = (r["content"] or "").strip().splitlines()[0] if r["content"] else ""
        line = line[:100].strip()
        if line and line not in seen:
            seen.add(line)
            samples.append(line)
        if len(samples) >= 40:
            break

    return {
        "totals": totals,
        "msg_totals": msg_totals,
        "avg_user_len": round(style.get("avg_len") or 0, 0),
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "top_workspaces": top_workspaces,
        "top_models": top_models,
        "top_tools": top_tools,
        "tool_status": tool_status,
        "todos": todos,
        "ctx": ctx,
        "top_tags": top_tags,
        "samples": samples,
    }


_WD_NAMES_ZH = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
_WD_NAMES_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def build_prompt(lang: str = "zh") -> str:
    """拼装用于桥接给 Kiro 的分析 prompt。lang: zh | en。"""
    conn = db._connect()
    try:
        d = gather(conn)
    finally:
        conn.close()

    t = d["totals"]
    mt = d["msg_totals"]
    ts = d["tool_status"]
    td = d["todos"]
    ctx = d["ctx"]

    todo_total = td.get("total") or 0
    todo_done = td.get("done") or 0
    todo_rate = round(todo_done / todo_total * 100, 1) if todo_total else 0

    ws_str = "、".join(f"{w['ws'].split('/')[-1] or w['ws']}({w['c']})" for w in d["top_workspaces"][:6])
    model_str = "、".join(f"{m['model']}({m['c']})" for m in d["top_models"][:6])
    tool_str = "、".join(f"{x['tool_name']}({x['c']})" for x in d["top_tools"])
    tag_str = "、".join(f"{k}({v})" for k, v in d["top_tags"]) or "-"

    hours_sorted = sorted(d["by_hour"], key=lambda x: -x["c"])[:4]
    wd_sorted = sorted(d["by_weekday"], key=lambda x: -x["c"])[:3]

    lines: List[str] = []
    if lang == "en":
        hours_str = ", ".join(f"{h['hour']:02d}:00({h['c']})" for h in hours_sorted) or "n/a"
        wd_str = ", ".join(f"{_WD_NAMES_EN[int(w['wd'])]}({w['c']})" for w in wd_sorted) or "n/a"
        lines.append(
            "You are Kiro, an AI coding assistant. Below are statistics of all my (your user's) "
            "historical sessions in Kiro. Write a first-person piece titled \"Your Digital Profile\" — "
            "a portrait and honest assessment of this developer. Requirements: be observant, make "
            "personality judgments, ground everything in the data below, and include five sections: "
            "[Work Habits], [Strengths & Focus Areas], [Collaboration Style], [What's Commendable], "
            "and [Areas to Improve]. Be sincere, specific and warm; avoid empty platitudes.\n"
            "IMPORTANT: Output ONLY the piece itself. The very first line must be the heading "
            "\"# Your Digital Profile\". Do NOT write any preamble, opening remark, or lead-in sentence.\n"
        )
        lines.append("## Overview")
        lines.append(f"- Sessions: {t.get('sessions', 0)}; time span: {(t.get('first_at') or '')[:10]} ~ {(t.get('last_at') or '')[:10]}")
        lines.append(f"- My prompts: {mt.get('user_msgs', 0)}; your replies: {mt.get('assistant_msgs', 0)}; tool calls: {mt.get('tool_calls', 0)}")
        lines.append(f"- Avg prompt length: ~{int(d['avg_user_len'])} chars")
        lines.append("")
        lines.append("## Time habits (UTC)")
        lines.append(f"- Most active hours: {hours_str}")
        lines.append(f"- Most active weekdays: {wd_str}")
        lines.append("")
        lines.append("## Projects & tools")
        lines.append(f"- Top workspaces: {ws_str}")
        lines.append(f"- Models used: {model_str}")
        lines.append(f"- Most-triggered tools: {tool_str}")
        lines.append(f"- Tool runs: completed {ts.get('completed',0)}, failed {ts.get('failed',0)}, denied by me {ts.get('denied',0)}")
        lines.append(f"- Avg context peak: {round(ctx.get('avg_ctx') or 0,1)}%, max {round(ctx.get('max_ctx') or 0,1)}%")
        lines.append("")
        lines.append("## Follow-through (todos)")
        lines.append(f"- {todo_total} historical todos, {todo_done} done, completion rate {todo_rate}%")
        lines.append("")
        lines.append("## Focus topics (frequent tags)")
        lines.append(f"- {tag_str}")
        lines.append("")
        lines.append("## Samples of my real prompts")
        for s in d["samples"]:
            lines.append(f"- {s}")
        lines.append("")
        lines.append("Now write the piece, starting directly with the heading. No preamble.")
    else:
        hours_str = "、".join(f"{h['hour']:02d}点({h['c']})" for h in hours_sorted) or "无数据"
        wd_str = "、".join(f"{_WD_NAMES_ZH[int(w['wd'])]}({w['c']})" for w in wd_sorted) or "无数据"
        lines.append(
            "你是 Kiro,一个 AI 编程助手。下面是我(你的用户)在 Kiro 里全部历史会话的统计数据。"
            "请你以第一人称、用中文,写一份「你的数字画像」——对这位开发者的画像与评价。"
            "要求:有观察、有性格判断、有依据(引用下面的数据),包含【工作习惯】【擅长与关注领域】"
            "【协作风格】【值得称赞的地方】【可以改进的地方】五个部分,语气真诚、具体、带点人情味,不要空话套话。\n"
            "重要:只输出正文本身,第一行必须是标题「# 你的数字画像」。不要写任何开场白、前言或引入句。\n"
        )
        lines.append("## 总体")
        lines.append(f"- 会话总数: {t.get('sessions', 0)};时间跨度: {(t.get('first_at') or '')[:10]} ~ {(t.get('last_at') or '')[:10]}")
        lines.append(f"- 我的提问数: {mt.get('user_msgs', 0)};你的回复数: {mt.get('assistant_msgs', 0)};工具调用: {mt.get('tool_calls', 0)}")
        lines.append(f"- 我提问的平均长度: 约 {int(d['avg_user_len'])} 字符")
        lines.append("")
        lines.append("## 时间习惯 (UTC)")
        lines.append(f"- 最活跃时段: {hours_str}")
        lines.append(f"- 最活跃星期: {wd_str}")
        lines.append("")
        lines.append("## 项目与工具")
        lines.append(f"- 主要工作区: {ws_str}")
        lines.append(f"- 常用模型: {model_str}")
        lines.append(f"- 最常触发的工具: {tool_str}")
        lines.append(f"- 工具执行: 完成 {ts.get('completed',0)}、失败 {ts.get('failed',0)}、被我拒绝 {ts.get('denied',0)}")
        lines.append(f"- 平均上下文占用峰值: {round(ctx.get('avg_ctx') or 0,1)}%,最高 {round(ctx.get('max_ctx') or 0,1)}%")
        lines.append("")
        lines.append("## 执行力(待办)")
        lines.append(f"- 历史待办 {todo_total} 条,已完成 {todo_done} 条,完成率 {todo_rate}%")
        lines.append("")
        lines.append("## 关注主题(高频标签)")
        lines.append(f"- {tag_str}")
        lines.append("")
        lines.append("## 我的部分真实提问(样本)")
        for s in d["samples"]:
            lines.append(f"- {s}")
        lines.append("")
        lines.append("请基于以上数据,直接从标题开始写正文,不要任何开场白。")

    return "\n".join(lines)


def _strip_preamble(text: str) -> str:
    """去掉正文前的开场白:从第一个 Markdown 标题(# 开头)起保留。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#"):
            return "\n".join(lines[i:]).strip()
    return text.strip()


def generate(lang: str = "zh") -> Dict[str, Any]:
    """用本地 kiro-cli 自动生成画像。失败抛 RuntimeError。"""
    prompt = build_prompt(lang)
    text = _strip_preamble(kirocli.chat(prompt))
    return {
        "content": text,
        "source": "kiro-cli",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
