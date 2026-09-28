"""本地设置存取:目前用于保存 Kiro API key。

安全约定:
- 存到本项目 data/settings.json,文件权限设为 0600
- 该文件被 .gitignore 忽略,不进版本库
- 接口层只返回打码后的 key,绝不回传明文,也不写日志
- 优先使用界面配置的 key,其次回退到环境变量 KIRO_API_KEY
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from . import config

SETTINGS_PATH = config.BASE_DIR / "data" / "settings.json"


def _load() -> Dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(data: Dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass


def get_api_key() -> str:
    """返回可用的 API key:界面配置优先,其次环境变量。"""
    stored = _load().get("kiro_api_key")
    if stored:
        return stored
    return os.environ.get("KIRO_API_KEY", "")


def set_api_key(key: str) -> None:
    data = _load()
    data["kiro_api_key"] = key.strip()
    _save(data)


def clear_api_key() -> None:
    data = _load()
    data.pop("kiro_api_key", None)
    _save(data)


def get_model() -> str:
    """kiro-cli 使用的模型 id;未配置返回空(由 CLI 用默认)。"""
    return _load().get("kiro_model", "")


def set_model(model: str) -> None:
    data = _load()
    data["kiro_model"] = model.strip()
    _save(data)


def key_source() -> str:
    """key 的来源:settings(界面配置)/ env(环境变量)/ none。"""
    if _load().get("kiro_api_key"):
        return "settings"
    if os.environ.get("KIRO_API_KEY"):
        return "env"
    return "none"


def masked_key() -> str:
    """打码后的 key,仅用于界面展示,绝不返回明文。"""
    key = get_api_key()
    if not key:
        return ""
    if len(key) > 12:
        return f"{key[:8]}…{key[-4:]}"
    return f"{key[:3]}…"
