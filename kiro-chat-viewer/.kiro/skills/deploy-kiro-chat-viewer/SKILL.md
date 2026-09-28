---
name: deploy-kiro-chat-viewer
description: Deploys and starts the local Kiro Chat Viewer web app (this repo). Use when the user asks to run, start, launch, deploy, set up, or open the Kiro chat history viewer / 历史对话查看器 on this machine.
---

# Deploy & Start Kiro Chat Viewer

## Overview
This repo is a local web app (FastAPI backend + static vanilla-JS frontend) that reads the user's local Kiro chat history from `~/.kiro/sessions`, and provides browsing, full-text search, stats, todos, and an AI-generated "Your Digital Profile". This skill tells you how to bring it up on any machine and confirm it works.

Data is read-only from `~/.kiro`; the app never writes back to Kiro. The index and any API key live under `data/` (git-ignored).

## Prerequisites (verify first)
1. Python 3.8+ available (`python3 --version` or `python --version`).
2. This is the repo root (contains `run.sh`, `requirements.txt`, and the `backend/` folder).
3. The user has used Kiro before, so `~/.kiro/sessions` exists. If missing, the app still starts but shows no data — tell the user.
4. Optional, only for one-click profile generation: `kiro-cli` installed and an API key. Without it, the profile bridge still works.

## Steps

### 1. Prefer the one-click launcher
The launcher creates a venv, installs pinned deps, finds a free port, starts the server, and opens the browser.

- macOS / Linux:
  ```bash
  ./run.sh
  ```
- Windows:
  ```bat
  run.bat
  ```

This is a long-running server. Start it in the background (do NOT block on it), then verify. If a run tool offers a "background process" mode, use it. Otherwise append ` &` on macOS/Linux.

### 2. Fallback: manual start (if scripts are unavailable or fail)
```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

### 3. Verify it is up
- Check the endpoint returns 200 and real numbers:
  ```bash
  curl -s http://127.0.0.1:8765/api/stats
  ```
  Expect JSON like `{"sessions": <n>, "messages": <m>, ...}`.
- Check the startup log line `[Kiro Chat Viewer] 发现 N 个会话目录`. If it warns that no session dir was found, the user's Kiro data is elsewhere or absent.
- The default URL is `http://127.0.0.1:8765`. If port 8765 was busy, `run.sh` picked the next free port — read the actual port from the launcher output / logs and report that URL.

### 4. Report to the user
Give them the exact URL. Mention that in Kiro IDE they can also open it via the command palette: `Simple Browser: Show`.

## Configuration notes
- Optional env vars are documented in `.env.example` (KIRO_HOME, KCV_HOST, KCV_PORT, KCV_DB, KIRO_API_KEY, KCV_LLM_*). Defaults work out of the box.
- The Kiro API key is best set in the web UI under "⚙️ 设置 / Settings" rather than an env var. It is stored only in `data/settings.json` (git-ignored, chmod 600). Never print or commit the key.
- Keep the server bound to `127.0.0.1`. Do not expose it on `0.0.0.0` — the history may contain secrets.

## Troubleshooting
- "python not found" or version < 3.8 → ask the user to install Python 3.8+.
- Dependency install fails → ensure network access to PyPI; retry `pip install -r requirements.txt` inside the venv.
- Port already in use → `run.sh` auto-increments; for manual start set `KCV_PORT` to a free port.
- Empty session list → confirm `~/.kiro/sessions` exists; if the user is on an older Kiro whose data lives in `globalStorage/kiro.kiroagent`, the current parser does not read that layout yet.
- "One-click generate" fails with "模型不可用/Invalid model" → the account lacks access to the selected model; pick another in Settings.
- Session analysis silently falls back to a weak local summary → the Kiro account may have hit its monthly request limit (the analysis panel now shows this note); wait for reset, switch model, or configure an external LLM (`KCV_LLM_*`).
- After code changes to the backend, restart the server (static frontend changes are served fresh, no restart needed).

## Do not
- Do not modify anything under `~/.kiro/` — it is the read-only source of truth.
- Do not commit `data/` (index.db, settings.json) — it is git-ignored and may contain the API key.
