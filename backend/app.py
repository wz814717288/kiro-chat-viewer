"""FastAPI 后端:提供会话列表 / 详情 / 全文检索 / 重建索引接口,并托管静态前端。

启动: uvicorn backend.app:app --host 127.0.0.1 --port 8765
或:   python -m backend.app
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import analyzer, config, db, kirocli, profile, settings


def _startup_diagnostics() -> None:
    """首次运行诊断:检查是否能找到 Kiro 历史数据,给出友好提示。"""
    import logging

    log = logging.getLogger("uvicorn.error")
    roots = config.get_session_roots()
    found = [r for r in roots if r.exists()]
    if not found:
        log.warning(
            "[Kiro Chat Viewer] 未找到 Kiro 历史会话目录(%s)。"
            "请确认本机已安装并使用过 Kiro;若数据在别处,可用环境变量 KIRO_HOME 指定 ~/.kiro 路径。",
            str(config.get_kiro_home() / "sessions"),
        )
        return
    # 统计一下会话数,给个直观反馈
    try:
        import backend.scanner as scanner  # noqa
    except Exception:
        from . import scanner  # type: ignore
    try:
        n = len(scanner.find_session_dirs())
        log.info("[Kiro Chat Viewer] 发现 %d 个会话目录,数据源: %s", n, ", ".join(str(p) for p in found))
        if n == 0:
            log.warning("[Kiro Chat Viewer] 会话目录存在但为空,可能是 Kiro 版本或存储布局不同。")
    except Exception as e:  # 诊断失败不应阻断启动
        log.warning("[Kiro Chat Viewer] 会话目录扫描异常: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _startup_diagnostics()
    yield


app = FastAPI(title="Kiro Chat Viewer", version="0.1.0", lifespan=lifespan)


@app.get("/api/stats")
def api_stats():
    return db.stats()


@app.post("/api/reindex")
def api_reindex(force: bool = Query(default=False)):
    return db.reindex(force=force)


@app.get("/api/facets")
def api_facets():
    return db.facets()


@app.get("/api/dashboard")
def api_dashboard():
    return db.dashboard()


@app.get("/api/todos")
def api_todos(
    status: str = Query(default="open"),
    show_cleared: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
):
    if status not in ("open", "done", "all"):
        status = "open"
    return db.list_todos(status=status, show_cleared=show_cleared, limit=limit)


@app.post("/api/todos/mark")
def api_todos_mark(
    session_id: str = Body(...),
    seq: int = Body(...),
    status: str = Body(...),
):
    if status not in ("done", "open", "cleared"):
        raise HTTPException(status_code=400, detail="invalid status")
    task = db.get_todo_task(session_id, seq)
    if task is None:
        raise HTTPException(status_code=404, detail="todo not found")
    db.set_todo_override(session_id, seq, status, task)
    return {"ok": True, "session_id": session_id, "seq": seq, "status": status}


# ---- Kiro 眼中的你 (半自动桥接) ----
@app.get("/api/profile")
def api_get_profile():
    saved = db.get_profile()
    return saved if saved else {"content": None}


@app.get("/api/profile/prompt")
def api_profile_prompt(lang: str = Query(default="zh")):
    lang = lang if lang in ("zh", "en") else "zh"
    return {"prompt": profile.build_prompt(lang)}


@app.post("/api/profile")
def api_save_profile(content: str = Body(..., embed=True)):
    text = (content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="content is empty")
    created = datetime.now(timezone.utc).isoformat()
    db.save_profile(content=text, source="bridge", created_at=created)
    return {"content": text, "source": "bridge", "created_at": created}


@app.post("/api/profile/generate")
def api_profile_generate(lang: str = Query(default="zh")):
    lang = lang if lang in ("zh", "en") else "zh"
    if not kirocli.available():
        raise HTTPException(status_code=400, detail="kiro-cli 未安装")
    if not settings.get_api_key():
        raise HTTPException(status_code=400, detail="未配置 API key")
    try:
        result = profile.generate(lang)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    db.save_profile(
        content=result["content"],
        source=result["source"],
        created_at=result["created_at"],
    )
    return result


# ---- 设置:API key 配置 ----
@app.get("/api/settings")
def api_get_settings():
    return {
        "kiro_cli_installed": kirocli.available(),
        "api_key_configured": bool(settings.get_api_key()),
        "api_key_masked": settings.masked_key(),
        "api_key_source": settings.key_source(),
        "model": settings.get_model(),
    }


@app.get("/api/settings/models")
def api_list_models():
    return {"models": kirocli.list_models()}


@app.post("/api/settings/model")
def api_set_model(model: str = Body(..., embed=True)):
    settings.set_model((model or "").strip())
    return {"ok": True, "model": settings.get_model()}


@app.post("/api/settings/api_key")
def api_set_api_key(api_key: str = Body(..., embed=True)):
    key = (api_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="api_key is empty")
    settings.set_api_key(key)
    kirocli.reset_models_cache()  # key 变更,模型列表缓存失效
    return {"ok": True, "api_key_masked": settings.masked_key()}


@app.delete("/api/settings/api_key")
def api_clear_api_key():
    settings.clear_api_key()
    kirocli.reset_models_cache()
    return {"ok": True}


@app.post("/api/settings/test")
def api_test_api_key():
    return kirocli.whoami()


@app.get("/api/sessions")
def api_sessions(
    workspace: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    sort: str = Query(default="modified_desc"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return db.list_sessions(
        q_workspace=workspace,
        model_id=model,
        agent_mode=mode,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@app.get("/api/sessions/{session_id}")
def api_session_detail(session_id: str):
    result = db.get_session(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="session not found")
    return result


@app.get("/api/sessions/{session_id}/export")
def api_session_export(
    session_id: str,
    fmt: str = Query(default="md"),
    tools: bool = Query(default=False),
):
    if fmt == "json":
        result = db.get_session(session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="session not found")
        return result
    md = db.export_session_markdown(session_id, include_tools=tools)
    if md is None:
        raise HTTPException(status_code=404, detail="session not found")
    filename = f"{session_id}.md"
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/export")
def api_export(
    ids: list = Body(..., embed=True),
    include_tools: bool = Body(default=False),
    bundle: str = Body(default="auto"),
):
    """批量导出会话。bundle: single | zip | auto(少合并、多打包)。"""
    ids = [i for i in (ids or []) if i]
    if not ids:
        raise HTTPException(status_code=400, detail="no session ids")
    if len(ids) > 200:
        raise HTTPException(status_code=400, detail="too many sessions (max 200)")
    if bundle == "auto":
        bundle = "single" if len(ids) <= 5 else "zip"
    if bundle not in ("single", "zip"):
        bundle = "single"

    result = db.export_bundle(ids, include_tools=include_tools, bundle=bundle)
    if result is None:
        raise HTTPException(status_code=404, detail="no valid sessions")
    content, kind = result
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    if kind == "zip":
        from fastapi.responses import Response
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="kiro-export-{stamp}-{len(ids)}.zip"'},
        )
    return PlainTextResponse(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="kiro-export-{stamp}-{len(ids)}.md"'},
    )


@app.get("/api/sessions/{session_id}/analysis")
def api_get_analysis(
    session_id: str,
    refresh: bool = Query(default=False),
    cached_only: bool = Query(default=False),
    lang: str = Query(default="zh"),
):
    lang = lang if lang in ("zh", "en") else "zh"
    if not refresh:
        cached = db.get_analysis(session_id)
        # 仅当缓存语言与页面语言一致时才复用;否则按当前语言重新生成
        if cached and (cached.get("lang") or "zh") == lang:
            return cached
    # 只读缓存模式:无缓存(或语言不匹配)则返回空,不触发生成
    if cached_only:
        return {"cached": False}

    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    result = analyzer.analyze(session, lang)
    db.save_analysis(
        session_id=session_id,
        summary=result["summary"],
        tags=result["tags"],
        source=result["source"],
        created_at=result["created_at"],
        headline=result.get("headline", ""),
        facts=result.get("facts"),
        lang=result.get("lang", lang),
        note=result.get("note", ""),
    )
    return result


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    return db.search(q, limit=limit)


# ---- 静态前端 ----
if config.FRONTEND_DIR.exists():
    @app.get("/")
    def index():
        return FileResponse(str(config.FRONTEND_DIR / "index.html"))

    app.mount(
        "/static",
        StaticFiles(directory=str(config.FRONTEND_DIR)),
        name="static",
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "backend.app:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
