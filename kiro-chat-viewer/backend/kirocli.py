"""封装对本地 kiro-cli 的调用。

- available(): kiro-cli 是否在 PATH
- whoami(): 用当前 key 测试认证
- chat(prompt): 无头模式生成文本

调用时把配置的 API key 注入子进程环境变量 KIRO_API_KEY。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import settings

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _clean(s: str) -> str:
    """去掉 ANSI 转义,便于把 stderr 作为可读错误信息返回。"""
    return _ANSI_RE.sub("", s or "").strip()

# 已知的候选安装位置(macOS),不依赖服务进程的 PATH
_CANDIDATES = [
    Path.home() / ".local/bin/kiro-cli",
    Path("/Applications/Kiro CLI.app/Contents/MacOS/kiro-cli"),
]


def resolve_bin() -> Optional[str]:
    """定位 kiro-cli 可执行文件:先查 PATH,再查已知安装位置。"""
    found = shutil.which("kiro-cli")
    if found:
        return found
    for c in _CANDIDATES:
        if c.exists() and os.access(str(c), os.X_OK):
            return str(c)
    return None


def available() -> bool:
    return resolve_bin() is not None


def _env() -> Dict[str, str]:
    env = os.environ.copy()
    key = settings.get_api_key()
    if key:
        env["KIRO_API_KEY"] = key
    return env


def whoami(timeout: int = 20) -> Dict[str, Any]:
    bin_path = resolve_bin()
    if not bin_path:
        return {"ok": False, "error": "kiro-cli 未安装"}
    if not settings.get_api_key():
        return {"ok": False, "error": "未配置 API key"}
    try:
        p = subprocess.run(
            [bin_path, "whoami"],
            capture_output=True, text=True, timeout=timeout, env=_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "kiro-cli whoami 超时"}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
    ok = ("Authenticated" in out) or ("Email" in out)
    return {"ok": ok, "output": out[:300]}


# 模型列表短期缓存:kiro-cli 拉一次要几秒,进设置页时避免每次重跑
_MODELS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_MODELS_TTL = 300  # 秒


def reset_models_cache() -> None:
    """清空模型列表缓存(API key 变更后调用)。"""
    _MODELS_CACHE["ts"] = 0.0
    _MODELS_CACHE["data"] = None


def list_models(force: bool = False) -> List[Dict[str, Any]]:
    """返回可用模型列表 [{id, name, description}]。失败返回空列表。

    结果缓存 _MODELS_TTL 秒;force=True 可绕过缓存强制刷新。
    """
    import time

    now = time.time()
    if not force and _MODELS_CACHE["data"] is not None and (now - _MODELS_CACHE["ts"]) < _MODELS_TTL:
        return _MODELS_CACHE["data"]

    bin_path = resolve_bin()
    if not bin_path:
        return []
    try:
        p = subprocess.run(
            [bin_path, "chat", "--list-models", "--format", "json"],
            capture_output=True, text=True, timeout=20, env=_env(),
        )
        data = json.loads(p.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        # 失败不写缓存,下次仍会重试
        return []
    out = []
    for m in data.get("models", []):
        out.append({
            "id": m.get("model_id") or m.get("model_name"),
            "name": m.get("model_name") or m.get("model_id"),
            "description": m.get("description", ""),
        })
    _MODELS_CACHE["ts"] = now
    _MODELS_CACHE["data"] = out
    return out


def chat(prompt: str, model: Optional[str] = None, timeout: int = 240) -> str:
    """无头模式跑一次对话,返回 stdout 文本。失败抛 RuntimeError。"""
    bin_path = resolve_bin()
    if not bin_path:
        raise RuntimeError("kiro-cli 未安装")
    if not settings.get_api_key():
        raise RuntimeError("未配置 API key")

    model = model or settings.get_model()
    args = [bin_path, "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        args += ["--model", model]
    args.append(prompt)

    try:
        p = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=_env(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("kiro-cli 调用超时")

    # kiro-cli 的 stdout 带 ANSI 颜色码和响应前缀 "> ",需清理
    text = _clean(p.stdout)
    if text.startswith("> "):
        text = text[2:].lstrip()
    if text:
        return text

    # stdout 为空:从 stderr 提取真实原因
    err = _clean(p.stderr)
    low = err.lower()
    if "request limit reached" in low or "monthly request limit" in low or "used all your free requests" in low:
        raise RuntimeError(
            "已达本月免费额度上限,AI 分析不可用(额度通常在下月初重置)。"
            "可改用外部 LLM(配置 KCV_LLM_API_BASE / KCV_LLM_API_KEY),或升级 Kiro 订阅。"
        )
    if "invalid model" in low or "validationexception" in low:
        raise RuntimeError(
            f"模型 '{model or '默认'}' 不可用(账号无权限或模型 ID 无效)。请在「设置」里更换模型。"
        )
    if p.returncode != 0:
        raise RuntimeError((err or "kiro-cli 调用失败")[:500])
    raise RuntimeError("kiro-cli 未返回内容。" + (f"stderr: {err[:300]}" if err else ""))
