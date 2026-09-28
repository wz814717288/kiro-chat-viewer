"""单会话分析:生成摘要与主题标签。

两种来源,自动降级:
1. LLM(可选):配置了 OpenAI 兼容接口的环境变量时启用,用标准库 urllib 调用,
   不引入额外依赖。
   - KCV_LLM_API_BASE  例: https://api.openai.com/v1
   - KCV_LLM_API_KEY   API Key
   - KCV_LLM_MODEL     模型名,默认 gpt-4o-mini
2. 本地启发式:无 LLM 配置时的降级方案。不联网,基于对话内容抽取
   首个问题、涉及的工具/文件、高频词生成粗略摘要与标签。

结果由调用方(db)缓存。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import kirocli


def _llm_configured() -> bool:
    return bool(os.environ.get("KCV_LLM_API_KEY") and os.environ.get("KCV_LLM_API_BASE"))


# ---- kiro-cli 分析(优先,复用本地 Kiro 模型)----
def _parse_ai_output(text: str) -> Dict[str, str]:
    """从模型输出里分离出「一句话结论」「标签」与正文 Markdown。

    识别行:  一句话/Headline: xxx    标签/Tags: a, b, c
    其余作为正文(含关键决策、遗留问题、踩坑等小节)。
    """
    headline = ""
    tags = ""
    body: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        m_h = re.match(r"^(?:\*{0,2})(?:一句话(?:结论)?|Headline|TL;?DR)(?:\*{0,2})\s*[:：]\s*(.+)$", s, re.IGNORECASE)
        m_t = re.match(r"^(?:\*{0,2})(?:标签|Tags?)(?:\*{0,2})\s*[:：]\s*(.+)$", s, re.IGNORECASE)
        if m_h and not headline:
            headline = m_h.group(1).strip().strip("`*").strip()
            continue
        if m_t and not tags:
            parts = re.split(r"[,,、\s]+", m_t.group(1).strip())
            tags = ", ".join(p.strip("#*` ").strip() for p in parts if p.strip("#*` ").strip())
            continue
        body.append(ln)
    return {"headline": headline, "tags": tags, "summary": "\n".join(body).strip()}


# ---- 确定性事实提取(不依赖 AI,本地即可算)----
_FILE_RE = re.compile(r"[\w./\-]+\.(?:py|js|ts|tsx|jsx|go|java|rb|rs|c|cpp|h|md|json|ya?ml|toml|sql|sh|css|html|proto|txt|vue|php)\b")


def _extract_facts(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从消息里提取客观事实:涉及文件、工具使用、命令、统计。"""
    files = Counter()
    for m in messages:
        if m["type"] in ("user", "assistant"):
            for mt in _FILE_RE.findall(m.get("content") or ""):
                if len(mt) <= 120:
                    files[mt] += 1

    tools = Counter(
        m.get("tool_name") for m in messages
        if m["type"] == "tool_call" and m.get("tool_name")
    )

    # 命令:从 execute_bash / runCommand 的 tool_call 内容里粗略抽第一段
    commands = []
    for m in messages:
        if m["type"] == "tool_call" and (m.get("tool_name") in ("execute_bash", "runCommand")):
            c = (m.get("content") or "")
            mm = re.search(r'"command"\s*:\s*"([^"]{1,80})', c)
            if mm:
                commands.append(mm.group(1))
    cmd_top = [c for c, _ in Counter(commands).most_common(6)]

    counts = Counter(m["type"] for m in messages)
    return {
        "files": [f for f, _ in files.most_common(10)],
        "tools": [{"name": t, "count": c} for t, c in tools.most_common(8)],
        "commands": cmd_top,
        "counts": {
            "total": len(messages),
            "user": counts.get("user", 0),
            "assistant": counts.get("assistant", 0),
            "tool_call": counts.get("tool_call", 0),
        },
    }


def _analyze_kiro_cli(title: str, dialog: str, lang: str):
    """返回 (result | None, error_msg | None)。失败时把原因带出,便于前端提示。"""
    if not kirocli.available() or not kirocli.settings.get_api_key():
        return None, None
    if lang == "en":
        prompt = (
            "Below is a conversation between a developer and an AI coding assistant. "
            "In English, write a concise analysis in Markdown. Start with these two lines:\n"
            "`Headline: <one sentence takeaway>`\n"
            "`Tags: tag1, tag2, tag3` (3-6 topic/tech tags)\n"
            "Then the body with these sections (omit a section if truly empty):\n"
            "### Summary\n(2-4 sentences on what was done and the outcome)\n"
            "### Key decisions\n(short bullets)\n"
            "### Open issues / next steps\n(short bullets)\n"
            "### Pitfalls & fixes\n(short bullets: errors hit and how they were resolved)\n"
            "Do NOT add any preamble before the Headline line.\n\n"
            f"Title: {title}\n\nConversation:\n{dialog}"
        )
    else:
        prompt = (
            "下面是一段开发者与 AI 编程助手的对话。请用中文,用 Markdown 写一份分析。开头先输出两行:\n"
            "`一句话: <一句话结论>`\n"
            "`标签: 标签1, 标签2, 标签3`(3-6 个主题/技术标签)\n"
            "然后是正文,包含以下小节(某节确实为空则省略该节):\n"
            "### 概述\n(2-4 句话:这次做了什么、结论是什么)\n"
            "### 关键决策\n(简短要点)\n"
            "### 遗留问题 / 下一步\n(简短要点)\n"
            "### 踩坑与解决\n(简短要点:遇到的报错/问题及如何解决)\n"
            "不要在「一句话」之前写任何开场白。\n\n"
            f"标题: {title}\n\n对话:\n{dialog}"
        )
    try:
        text = kirocli.chat(prompt)
    except RuntimeError as e:
        return None, str(e)
    parsed = _parse_ai_output(text)
    if not parsed["summary"]:
        return None, "kiro-cli 未返回有效内容"
    return {
        "summary": parsed["summary"],
        "headline": parsed["headline"],
        "tags": parsed["tags"],
        "source": "kiro-cli",
    }, None


def _collect_dialog(
    messages: List[Dict[str, Any]], max_chars: int = 16000, lang: str = "zh"
) -> str:
    """把 user/assistant 消息拼成用于分析的文本。

    针对长会话做了两点改进,避免"只看到开头"导致总结片面:
    1. 每条消息单独截断(per_msg_cap),防止某条超长消息挤掉后续所有内容;
    2. 超出预算时,同时采样会话「开头」和「结尾」,中间用占位标记省略,
       让模型既能看到初始目标,也能看到最终结果/结论。
    """
    def label(t: str) -> str:
        if lang == "en":
            return "User" if t == "user" else "Assistant"
        return "用户" if t == "user" else "助手"

    per_msg_cap = 1500  # 单条消息上限,超出截断
    ellipsis = " …"
    items: List[str] = []
    for m in messages:
        if m["type"] not in ("user", "assistant"):
            continue
        text = (m.get("content") or "").strip()
        if not text:
            continue
        if len(text) > per_msg_cap:
            text = text[:per_msg_cap] + ellipsis
        items.append(f"[{label(m['type'])}] {text}")

    if not items:
        return ""

    joined = "\n".join(items)
    if len(joined) <= max_chars:
        return joined

    # 超预算:头 60% + 尾 40% 双向采样,中间省略
    omitted = ("\n\n[… middle of the conversation omitted …]\n\n"
               if lang == "en" else "\n\n[… 中间省略部分对话 …]\n\n")
    budget = max(0, max_chars - len(omitted))
    head_budget = int(budget * 0.6)
    tail_budget = budget - head_budget

    head: List[str] = []
    hsize = 0
    for it in items:
        need = len(it) + 1
        if hsize + need > head_budget:
            break
        head.append(it)
        hsize += need

    tail: List[str] = []
    tsize = 0
    head_count = len(head)
    for it in reversed(items[head_count:]):  # 只从未被 head 覆盖的部分取尾
        need = len(it) + 1
        if tsize + need > tail_budget:
            break
        tail.append(it)
        tsize += need
    tail.reverse()

    return "\n".join(head) + omitted + "\n".join(tail)


# ---- LLM 分析 ----
def _analyze_llm(title: str, dialog: str, lang: str = "zh") -> Optional[Dict[str, Any]]:
    base = os.environ["KCV_LLM_API_BASE"].rstrip("/")
    key = os.environ["KCV_LLM_API_KEY"]
    model = os.environ.get("KCV_LLM_MODEL", "gpt-4o-mini")

    if lang == "en":
        prompt = (
            "Below is a conversation between a developer and an AI coding assistant. "
            "In English, write a concise analysis in Markdown. Start with two lines:\n"
            "`Headline: <one sentence takeaway>`\n"
            "`Tags: tag1, tag2, tag3` (3-6 topic/tech tags)\n"
            "Then the body with sections (omit a section if empty): "
            "### Summary / ### Key decisions / ### Open issues / next steps / ### Pitfalls & fixes. "
            "Do NOT add any preamble.\n\n"
            f"Title: {title}\n\nConversation:\n{dialog}"
        )
    else:
        prompt = (
            "下面是一段开发者与 AI 编程助手的对话。请用中文,用 Markdown 写一份分析。开头先输出两行:\n"
            "`一句话: <一句话结论>`\n"
            "`标签: 标签1, 标签2, 标签3`(3-6 个主题/技术标签)\n"
            "然后正文包含小节(为空则省略):### 概述 / ### 关键决策 / ### 遗留问题 / 下一步 / ### 踩坑与解决。"
            "不要写开场白。\n\n"
            f"标题: {title}\n\n对话:\n{dialog}"
        )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        parsed = _parse_ai_output(content)
        if not parsed["summary"]:
            return None
        return {
            "summary": parsed["summary"],
            "headline": parsed["headline"],
            "tags": parsed["tags"],
            "source": f"llm:{model}",
        }
    except Exception:
        return None


# ---- 本地启发式降级 ----
_STOP = set(
    "the a an and or of to in is are for on with this that it be as at by we you i "
    "的 了 是 在 我 你 和 也 有 就 不 都 与 及 请 一个 这个 可以 需要 如果 这样 目前".split()
)


def _analyze_local(
    title: str, messages: List[Dict[str, Any]], meta: Dict[str, Any], lang: str = "zh"
) -> Dict[str, Any]:
    users = [m for m in messages if m["type"] == "user"]
    first_q = (users[0]["content"].strip().splitlines()[0][:120] if users else "")

    tools = Counter(
        m.get("tool_name") for m in messages if m["type"] == "tool_call" and m.get("tool_name")
    )
    tool_str = ", ".join(f"{t}×{c}" for t, c in tools.most_common(5))

    # 抽取引用到的文件路径
    files = set()
    for m in messages:
        if m["type"] in ("user", "assistant"):
            for mt in re.findall(r"[\w./-]+\.\w{1,5}", m.get("content") or ""):
                if "/" in mt or mt.endswith((".py", ".js", ".ts", ".go", ".md", ".json")):
                    files.add(mt)
    file_list = list(files)[:8]

    # 高频词做标签
    words = Counter()
    for m in messages:
        if m["type"] in ("user", "assistant"):
            for w in re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}", m.get("content") or ""):
                lw = w.lower()
                if lw not in _STOP:
                    words[lw] += 1
    tags = [w for w, _ in words.most_common(6)]

    counts = Counter(m["type"] for m in messages)
    summary_parts = []
    if lang == "en":
        if first_q:
            summary_parts.append(f"Opening question: {first_q}.")
        summary_parts.append(
            f"{len(messages)} messages (user {counts.get('user',0)}, assistant {counts.get('assistant',0)}, tool calls {counts.get('tool_call',0)})."
        )
        if tool_str:
            summary_parts.append(f"Main tools: {tool_str}.")
    else:
        if first_q:
            summary_parts.append(f"起始问题:{first_q}。")
        summary_parts.append(
            f"共 {len(messages)} 条消息(用户 {counts.get('user',0)}、助手 {counts.get('assistant',0)}、工具调用 {counts.get('tool_call',0)})。"
        )
        if tool_str:
            summary_parts.append(f"主要工具:{tool_str}。")

    return {
        "summary": " ".join(summary_parts),
        "headline": (first_q or title or "").strip()[:80],
        "tags": ", ".join(tags),
        "source": "local",
    }


def analyze(session: Dict[str, Any], lang: str = "zh") -> Dict[str, Any]:
    """对一个会话(含 messages)生成分析结果。

    优先级:kiro-cli(本地 Kiro 模型)> 外部 LLM > 本地启发式降级。
    """
    lang = lang if lang in ("zh", "en") else "zh"
    messages = session.get("messages", [])
    title = session.get("title", "")
    dialog = _collect_dialog(messages, lang=lang)
    result: Optional[Dict[str, Any]] = None
    ai_error = ""  # AI 分析失败原因(降级到 local 时透传给前端)
    if dialog and kirocli.available() and kirocli.settings.get_api_key():
        result, ai_error = _analyze_kiro_cli(title, dialog, lang)
    if result is None and _llm_configured() and dialog:
        result = _analyze_llm(title, dialog, lang)
    if result is None:
        result = _analyze_local(title, messages, session, lang)
        if ai_error:  # 本可用 AI 却失败,明确告知,避免"静默降级"被当成分析质量差
            result["note"] = ai_error

    result.setdefault("headline", "")
    result["lang"] = lang                          # 记录生成语言,供缓存按语言匹配
    result["facts"] = _extract_facts(messages)   # 确定性事实,不依赖 AI
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    result["session_id"] = session["id"]
    return result
