"use strict";

const state = {
  offset: 0,
  limit: 50,
  total: 0,
  workspace: "",
  model: "",
  mode: "",
  sort: "modified_desc",
  activeSession: null,
  view: "list", // list | search
  hideTools: false,
  currentSession: null, // 当前详情数据缓存(用于本地开关/搜索)
  selectMode: false,    // 会话多选导出模式
  selected: new Set(),  // 已选会话 id
};

const $ = (id) => document.getElementById(id);
const esc = (s) => window.escapeHtml(s);

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(window.getLang() === "en" ? "en-US" : "zh-CN", { hour12: false });
}

// 当前主内容区的重渲染函数(语言切换时调用)
let currentRerender = null;
// 仪表盘数据缓存(供 Bento 瓷砖详情抽屉复用)
let dashData = null;

function renderPlaceholder() {
  showDetailView();
  $("detailView").innerHTML = `<div class="placeholder">${t("pickHint")}</div>`;
}

// 顶栏/侧栏等静态文案的本地化
function applyChrome() {
  document.querySelector(".brand").textContent = t("brand");
  $("searchInput").placeholder = t("searchPlaceholder");
  $("searchBtn").textContent = t("search");
  $("clearBtn").textContent = t("clear");
  $("profileBtn").textContent = t("profileBtn");
  $("todosBtn").textContent = t("todosBtn");
  $("dashBtn").textContent = t("dashBtn");
  $("settingsBtn").textContent = t("settingsBtn");
  $("reindexBtn").textContent = t("reindex");
  $("langBtn").textContent = t("langBtn");
  $("selectBtn").textContent = state.selectMode ? t("selectExit") : t("selectBtn");
  $("exportSelAll").textContent = t("exportSelAll");
  $("exportGo").textContent = t("exportGo");
  $("exportCancel").textContent = t("exportCancel");
  $("exportToolsLabel").textContent = t("exportToolsLabel");
  updateExportBar();
  $("wsFilter").placeholder = t("wsFilter");
  const sortSel = $("sortSelect");
  const sortKeys = ["sortModified", "sortCreated", "sortMsgDesc", "sortMsgAsc", "sortTitle"];
  for (let i = 0; i < sortSel.options.length && i < sortKeys.length; i++) sortSel.options[i].text = t(sortKeys[i]);
  if ($("modelFilter").options[0]) $("modelFilter").options[0].text = t("allModels");
  if ($("modeFilter").options[0]) $("modeFilter").options[0].text = t("allModes");
}

function onToggleLang() {
  window.toggleLang();
  applyChrome();
  refreshStats();
  // 侧栏
  if (state.view === "search" && $("searchInput").value.trim()) doSearch(); else loadSessions();
  // 主内容区
  if (currentRerender) currentRerender();
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function showOnly(id) {
  ["detailView", "dashView", "todosView", "profileView", "settingsView"].forEach((v) => {
    $(v).style.display = v === id ? "block" : "none";
  });
}
function showDetailView() { showOnly("detailView"); }
function showDashView() { showOnly("dashView"); }
function showTodosView() { showOnly("todosView"); }
function showProfileView() { showOnly("profileView"); }
function showSettingsView() { showOnly("settingsView"); }

// ---- 会话列表 ----
async function loadSessions() {
  state.view = "list";
  $("listTitle").textContent = t("sessionList");
  const params = new URLSearchParams({
    limit: state.limit,
    offset: state.offset,
    sort: state.sort,
  });
  if (state.workspace) params.set("workspace", state.workspace);
  if (state.model) params.set("model", state.model);
  if (state.mode) params.set("mode", state.mode);
  const data = await api(`/api/sessions?${params}`);
  state.total = data.total;
  renderSessionList(data.items);
  renderPager();
}

function renderSessionList(items) {
  const ul = $("sessionList");
  if (!items.length) {
    ul.innerHTML = `<li class="spin" style="cursor:default">${t("emptyList")}</li>`;
    return;
  }
  const sel = state.selectMode;
  ul.innerHTML = items.map((s) => {
    const checked = state.selected.has(s.id);
    const cls = [s.id === state.activeSession ? "active" : "", sel && checked ? "selected" : ""].filter(Boolean).join(" ");
    return `
    <li data-id="${esc(s.id)}" class="${cls}">
      ${sel ? `<input type="checkbox" class="sess-chk" ${checked ? "checked" : ""} tabindex="-1"/>` : ""}
      <div class="s-main">
        <div class="s-title">${esc(s.title || t("untitled"))}</div>
        <div class="meta">
          <span class="badge">${esc(s.agent_mode || "?")}</span>
          <span>${esc(s.model_id || "")}</span>
          <span>${s.message_count} ${t("msgUnit")}</span>
          <span>${fmtTime(s.last_modified_at)}</span>
        </div>
        <div class="meta">${esc(s.workspace_path || "")}</div>
      </div>
    </li>`;
  }).join("");
  ul.querySelectorAll("li[data-id]").forEach((li) => {
    li.addEventListener("click", () => {
      if (state.selectMode) toggleSelect(li.dataset.id, li);
      else openSession(li.dataset.id);
    });
  });
}

// ---- 会话多选导出 ----
function enterSelectMode() {
  state.selectMode = true;
  state.selected.clear();
  $("selectBtn").textContent = t("selectExit");
  $("selectBtn").classList.add("on");
  $("exportBar").style.display = "block";
  updateExportBar();
  if (state.view === "list") renderSessionList_current();
}

function exitSelectMode() {
  state.selectMode = false;
  state.selected.clear();
  $("selectBtn").textContent = t("selectBtn");
  $("selectBtn").classList.remove("on");
  $("exportBar").style.display = "none";
  if (state.view === "list") renderSessionList_current();
}

// 重新拉取并渲染当前列表(复用分页/筛选状态)
function renderSessionList_current() {
  loadSessions();
}

function toggleSelect(id, li) {
  if (state.selected.has(id)) state.selected.delete(id);
  else state.selected.add(id);
  if (li) {
    const on = state.selected.has(id);
    li.classList.toggle("selected", on);
    const chk = li.querySelector(".sess-chk");
    if (chk) chk.checked = on;
  }
  updateExportBar();
}

function updateExportBar() {
  const c = $("exportCount");
  if (c) c.textContent = t("exportCountFmt", state.selected.size);
}

// 全选当前列表页的会话
async function selectAllCurrent() {
  const ids = Array.from($("sessionList").querySelectorAll("li[data-id]")).map((li) => li.dataset.id);
  const allSelected = ids.length > 0 && ids.every((id) => state.selected.has(id));
  ids.forEach((id) => { if (allSelected) state.selected.delete(id); else state.selected.add(id); });
  $("sessionList").querySelectorAll("li[data-id]").forEach((li) => {
    const on = state.selected.has(li.dataset.id);
    li.classList.toggle("selected", on);
    const chk = li.querySelector(".sess-chk");
    if (chk) chk.checked = on;
  });
  updateExportBar();
}

async function exportSelected() {
  const ids = Array.from(state.selected);
  const info = $("exportCount");
  if (!ids.length) { if (info) info.textContent = t("exportNone"); return; }
  const btn = $("exportGo");
  btn.disabled = true; btn.textContent = t("exporting");
  try {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids,
        include_tools: $("exportTools").checked,
        bundle: "auto",
      }),
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    // 从 Content-Disposition 解析文件名
    let filename = "kiro-export";
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^;"]+)"?/i);
    if (m) filename = decodeURIComponent(m[1]);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (info) info.textContent = t("exportDone");
  } catch (e) {
    if (info) info.textContent = t("exportFail") + e.message;
  } finally {
    btn.disabled = false; btn.textContent = t("exportGo");
  }
}

function renderPager() {
  const pager = $("pager");
  if (state.view !== "list") { pager.innerHTML = ""; return; }
  const from = state.total === 0 ? 0 : state.offset + 1;
  const to = Math.min(state.offset + state.limit, state.total);
  pager.innerHTML = `
    <button class="ghost" id="prevBtn" ${state.offset === 0 ? "disabled" : ""}>${t("prevPage")}</button>
    <span class="stats">${from}-${to} / ${state.total}</span>
    <button class="ghost" id="nextBtn" ${to >= state.total ? "disabled" : ""}>${t("nextPage")}</button>`;
  const prev = $("prevBtn"), next = $("nextBtn");
  if (prev) prev.onclick = () => { state.offset = Math.max(0, state.offset - state.limit); loadSessions(); };
  if (next) next.onclick = () => { state.offset += state.limit; loadSessions(); };
}

// ---- 会话详情 ----
async function openSession(id, scrollToSeq, returnView) {
  // 记录来源视图(从待办/统计进入时,提供返回入口);从列表/搜索进入则清空
  state.returnView = returnView || null;
  showDetailView();
  state.activeSession = id;
  document.querySelectorAll("#sessionList li").forEach((li) =>
    li.classList.toggle("active", li.dataset.id === id));
  const s = await api(`/api/sessions/${encodeURIComponent(id)}`);
  state.currentSession = s;
  currentRerender = () => renderDetail();
  renderDetail(scrollToSeq);
}

function renderDetail(scrollToSeq) {
  const s = state.currentSession;
  if (!s) return;
  const parts = [];
  parts.push(`
    <div class="detail-head">
      <h2 class="detail-title">${state.returnView ? `<button class="ghost back-btn" id="backBtn">${state.returnView === "todos" ? t("backTodos") : t("backDash")}</button> ` : ""}${esc(s.title || t("untitled"))}</h2>
      <div class="meta">
        <span class="badge">${esc(s.agent_mode || "?")}</span>
        <span>${t("model")} ${esc(s.model_id || "")}</span>
        <span>${s.message_count} ${t("messages")}</span>
        <span>${t("created")} ${fmtTime(s.created_at)}</span>
        <span>${t("updated")} ${fmtTime(s.last_modified_at)}</span>
      </div>
      <div class="meta">${esc(s.workspace_path || "")}</div>
    </div>
    <div class="toolbar-actions">
          <!-- 左:状态过滤(Toggle 胶囊) -->
          <label class="tb-toggle ${state.hideTools ? "on" : ""}">
            <input type="checkbox" id="hideToolsChk" ${state.hideTools ? "checked" : ""}/>
            <span class="tb-toggle-track"><span class="tb-toggle-knob"></span></span>
            <span class="tb-toggle-label">${t("hideTools")}</span>
          </label>

          <!-- 中:会话内查找 -->
          <div class="tb-search">
            <svg class="tb-search-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="9" cy="9" r="6"/><path d="M14 14l3.5 3.5" stroke-linecap="round"/></svg>
            <input id="inSearch" type="text" placeholder="${t("inSearchPlaceholder")}" />
            <span id="inSearchInfo" class="tb-search-info"></span>
          </div>

          <!-- 右:核心操作 -->
          <div class="tb-right">
            <div class="tb-segment">
              <button id="inPrev" title="上一个">
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 12.5l4.5-4.5 4.5 4.5"/></svg>
              </button>
              <span class="tb-segment-div"></span>
              <button id="inNext" title="下一个">
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 7.5l4.5 4.5 4.5-4.5"/></svg>
              </button>
            </div>
            <button class="tb-primary" id="analyzeBtn">${t("analyze")}</button>
            <a class="tb-secondary" href="/api/sessions/${encodeURIComponent(s.id)}/export?fmt=md" target="_blank">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3v9m0 0l-3.2-3.2M10 12l3.2-3.2"/><path d="M4.5 14.5v1a1.5 1.5 0 001.5 1.5h8a1.5 1.5 0 001.5-1.5v-1"/></svg>
              ${t("exportShort")}
            </a>
          </div>
        </div>
    </div>
    <div id="analysisPanel" class="analysis-panel" style="display:none"></div>`);

  parts.push(`<div id="msgContainer">`);
  for (const m of s.messages) {
    if (m.type === "user" || m.type === "assistant") {
      parts.push(`
        <div class="msg ${m.type}" data-seq="${m.seq}">
          <div class="msg-role">${m.type === "user" ? t("user") : "Kiro"} · ${fmtTime(m.timestamp)}</div>
          <div class="msg-body md">${window.renderMarkdown(m.content)}</div>
        </div>`);
    } else if (m.type === "tool_call") {
      parts.push(`
        <details class="msg tool ${state.hideTools ? "hidden-tool" : ""}" data-seq="${m.seq}">
          <summary>🔧 ${t("toolCall")}: ${esc(m.tool_name || "tool")}</summary>
          <pre>${esc(m.content)}</pre>
        </details>`);
    } else if (m.type === "tool_result") {
      const ok = m.success === 1 ? "✓" : (m.success === 0 ? "✗" : "");
      parts.push(`
        <details class="msg tool ${state.hideTools ? "hidden-tool" : ""}" data-seq="${m.seq}">
          <summary>📎 ${t("toolResult")} ${ok} ${m.duration_ms != null ? `(${m.duration_ms}ms)` : ""}</summary>
          <pre>${esc(m.content)}</pre>
        </details>`);
    }
  }
  parts.push(`</div>`);

  const view = $("detailView");
  view.innerHTML = parts.join("");
  view.scrollTop = 0;

  bindDetailToolbar();

  if (scrollToSeq != null) {
    const el = view.querySelector(`[data-seq="${scrollToSeq}"]`);
    if (el) {
      if (el.tagName === "DETAILS") el.open = true;  // 工具消息(如 todo_list)展开
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("hit");
    }
  }
}

// 会话内查找:高亮 + 上下跳转
let inMatches = [];
let inIdx = -1;
function clearInHighlights() {
  document.querySelectorAll("#msgContainer .inmark").forEach((el) => {
    el.replaceWith(document.createTextNode(el.textContent));
  });
}
function runInSearch(term) {
  clearInHighlights();
  inMatches = []; inIdx = -1;
  const info = $("inSearchInfo");
  if (!term) { info.textContent = ""; return; }
  const container = $("msgContainer");
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const lc = term.toLowerCase();
  const toWrap = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement.closest("details.hidden-tool")) continue;
    if (node.nodeValue.toLowerCase().includes(lc)) toWrap.push(node);
  }
  toWrap.forEach((textNode) => {
    const frag = document.createDocumentFragment();
    const text = textNode.nodeValue;
    let idx = 0, lower = text.toLowerCase(), pos;
    while ((pos = lower.indexOf(lc, idx)) !== -1) {
      if (pos > idx) frag.appendChild(document.createTextNode(text.slice(idx, pos)));
      const mark = document.createElement("span");
      mark.className = "inmark";
      mark.textContent = text.slice(pos, pos + term.length);
      frag.appendChild(mark);
      inMatches.push(mark);
      idx = pos + term.length;
    }
    if (idx < text.length) frag.appendChild(document.createTextNode(text.slice(idx)));
    textNode.parentNode.replaceChild(frag, textNode);
  });
  info.textContent = inMatches.length ? t("inMatchN", inMatches.length) : t("inNoMatch");
  if (inMatches.length) gotoMatch(0);
}
function gotoMatch(i) {
  if (!inMatches.length) return;
  inMatches.forEach((m) => m.classList.remove("current"));
  inIdx = (i + inMatches.length) % inMatches.length;
  const cur = inMatches[inIdx];
  cur.classList.add("current");
  cur.scrollIntoView({ behavior: "smooth", block: "center" });
  $("inSearchInfo").textContent = `${inIdx + 1}/${inMatches.length}`;
}

function bindDetailToolbar() {
  const back = $("backBtn");
  if (back) back.onclick = () => {
    const rv = state.returnView;
    state.returnView = null;
    if (rv === "todos") loadTodos();
    else if (rv === "dash") loadDashboard();
  };

  const chk = $("hideToolsChk");
  if (chk) chk.onchange = () => { state.hideTools = chk.checked; renderDetail(); };
  const inp = $("inSearch");
  if (inp) {
    let t;
    inp.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => runInSearch(inp.value.trim()), 250); });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); gotoMatch(e.shiftKey ? inIdx - 1 : inIdx + 1); }
    });
  }
  const prev = $("inPrev"), next = $("inNext");
  if (prev) prev.onclick = () => gotoMatch(inIdx - 1);
  if (next) next.onclick = () => gotoMatch(inIdx + 1);

  const ab = $("analyzeBtn");
  if (ab) ab.onclick = () => loadAnalysis(state.currentSession.id, false);
  // 打开会话时仅展示已缓存的分析(不主动生成)
  showCachedAnalysis(state.currentSession.id);
}

async function showCachedAnalysis(id) {
  try {
    const a = await api(`/api/sessions/${encodeURIComponent(id)}/analysis?cached_only=true&lang=${window.getLang()}`);
    if (a && a.summary) { $("analysisPanel").style.display = "block"; renderAnalysis(a); }
  } catch (e) { /* ignore */ }
}

async function loadAnalysis(id, refresh) {
  const panel = $("analysisPanel");
  const btn = $("analyzeBtn");
  if (!panel) return;
  panel.style.display = "block";
  panel.innerHTML = `<span class="spin">${t("analyzing")}</span>`;
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (btn) btn.disabled = true;
  try {
    const a = await api(`/api/sessions/${encodeURIComponent(id)}/analysis?refresh=${refresh ? "true" : "false"}&lang=${window.getLang()}`);
    renderAnalysis(a);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    panel.innerHTML = `<span class="bad-text">${t("analysisFail")}${esc(e.message)}</span>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderAnalysis(a) {
  const panel = $("analysisPanel");
  if (!panel) return;
  const tags = (a.tags || "").split(",").map((t) => t.trim()).filter(Boolean);
  const src = a.source || "";
  const srcClass = src === "kiro-cli" ? "src-kiro" : (src.startsWith("llm") ? "src-llm" : "src-local");
  const f = a.facts || {};
  panel.innerHTML = `
    <div class="analysis-head">
      <strong>${t("analysisTitle")}</strong>
      <span class="src-badge ${srcClass}">${esc(src)}</span>
      ${a.created_at ? `<span class="stats">${fmtTime(a.created_at)}</span>` : ""}
      <button class="ghost" id="reAnalyzeBtn" style="margin-left:auto">${t("regen")}</button>
    </div>
    ${a.note ? `<div class="analysis-note">⚠️ ${t("analysisDegraded")}${esc(a.note)}</div>` : ""}
    ${a.headline ? `<div class="analysis-headline">${esc(a.headline)}</div>` : ""}
    <div class="analysis-summary md">${window.renderMarkdown(a.summary || "")}</div>
    ${tags.length ? `<div class="analysis-tags">${tags.map((tg) => `<span class="tag">${esc(tg)}</span>`).join("")}</div>` : ""}
    ${renderFacts(f)}`;
  const rb = $("reAnalyzeBtn");
  if (rb) rb.onclick = () => loadAnalysis(state.currentSession.id, true);
}

function renderFacts(f) {
  if (!f || (!(f.files || []).length && !(f.tools || []).length && !(f.commands || []).length)) return "";
  const blocks = [];
  if (f.counts) {
    blocks.push(`<div class="fact-row"><span class="fact-k">${t("factStats")}</span>
      <span class="fact-v">${t("msgUnit")} ${f.counts.total} · ${t("user")} ${f.counts.user} · Kiro ${f.counts.assistant} · ${t("dToolCalls")} ${f.counts.tool_call}</span></div>`);
  }
  if ((f.tools || []).length) {
    blocks.push(`<div class="fact-row"><span class="fact-k">${t("factTools")}</span>
      <span class="fact-v">${f.tools.map((x) => `<span class="chip">${esc(x.name)} <em>${x.count}</em></span>`).join("")}</span></div>`);
  }
  if ((f.files || []).length) {
    blocks.push(`<div class="fact-row"><span class="fact-k">${t("factFiles")}</span>
      <span class="fact-v">${f.files.map((x) => `<span class="chip mono">${esc(x)}</span>`).join("")}</span></div>`);
  }
  if ((f.commands || []).length) {
    blocks.push(`<div class="fact-row"><span class="fact-k">${t("factCmds")}</span>
      <span class="fact-v">${f.commands.map((x) => `<code>${esc(x)}</code>`).join(" ")}</span></div>`);
  }
  return `<div class="fact-card"><div class="fact-title">${t("factTitle")}</div>${blocks.join("")}</div>`;
}

// ---- 全文搜索 ----
async function doSearch() {
  const q = $("searchInput").value.trim();
  if (!q) { loadSessions(); return; }
  state.view = "search";
  currentRerender = renderPlaceholder;
  $("listTitle").textContent = t("searchResults");
  $("pager").innerHTML = "";
  const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=100`);
  renderSearchResults(data);
}

function renderSearchResults(data) {
  const ul = $("sessionList");
  if (!data.items.length) {
    ul.innerHTML = `<li class="spin" style="cursor:default">${esc(t("noMatchFor", data.query))}</li>`;
    return;
  }
  ul.innerHTML = data.items.map((r, i) => `
    <li data-idx="${i}" data-sid="${esc(r.session_id)}" data-seq="${r.seq}">
      <div class="s-title">${esc(r.title || t("untitled"))}</div>
      <div class="result-snippet">${r.snippet}</div>
      <div class="meta"><span class="badge">${esc(r.type)}</span><span>${fmtTime(r.timestamp)}</span></div>
    </li>`).join("");
  ul.querySelectorAll("li[data-sid]").forEach((li) => {
    li.addEventListener("click", () => {
      ul.querySelectorAll("li").forEach((x) => x.classList.remove("active"));
      li.classList.add("active");
      openSession(li.dataset.sid, parseInt(li.dataset.seq, 10));
    });
  });
}

// ---- 统计仪表盘 ----
async function loadDashboard() {
  showDashView();
  currentRerender = loadDashboard;
  const view = $("dashView");
  view.innerHTML = `<div class="placeholder">${t("updating")}</div>`;
  // 打开统计页时自动增量重建,确保数据最新(仅重扫变更过的会话,通常很快)
  try {
    await api("/api/reindex", { method: "POST" });
    await refreshStats();
  } catch (e) { /* 重建失败不阻断展示 */ }
  dashData = await api("/api/dashboard");
  renderBento(view, dashData);
}

// 迷你竖直柱(每日积分瓷砖)
function miniBars(pairs) {
  if (!pairs.length) return `<div class="stats">${t("noData")}</div>`;
  const max = Math.max(...pairs.map((p) => p[1])) || 1;
  const bars = pairs.map(([label, v]) =>
    `<div class="mini-bar" title="${esc(String(label))}: ${v}" style="height:${Math.max(4, v / max * 100)}%"></div>`).join("");
  const mmdd = (d) => String(d).slice(5);   // "2026-09-13" -> "09-13"
  const mid = pairs[Math.floor((pairs.length - 1) / 2)][0];
  const axis = `<div class="mini-axis">
    <span>${esc(mmdd(pairs[0][0]))}</span>
    <span>${esc(mmdd(mid))}</span>
    <span>${esc(mmdd(pairs[pairs.length - 1][0]))}</span>
  </div>`;
  return `<div class="mini-bars">${bars}</div>${axis}`;
}

// 瓷砖内紧凑条形(标签+条+值)
function miniRows(pairs) {
  if (!pairs.length) return `<div class="stats">${t("noData")}</div>`;
  const max = Math.max(...pairs.map((p) => p[1])) || 1;
  return `<div class="mini-rows">` + pairs.map(([label, v]) =>
    `<div class="mini-row"><span class="mr-l" title="${esc(String(label))}">${esc(String(label))}</span>
      <span class="mr-t"><span class="mr-f" style="width:${(v / max * 100).toFixed(1)}%"></span></span>
      <span class="mr-v">${v}</span></div>`).join("") + `</div>`;
}

function kpiTile(num, label, cls) {
  const c = cls === "accent" ? "kpi-accent" : (cls === "green" ? "kpi-green" : "");
  return `<div class="tile t-kpi"><div class="kpi-num ${c}">${num}</div><div class="kpi-label">${label}</div></div>`;
}

function renderBento(view, d) {
  const tot = d.totals;
  const ts = d.tool_status || {};
  const succ = (ts.completed + ts.failed) ? Math.round(ts.completed / (ts.completed + ts.failed) * 100) : 0;
  const ap = d.approvals || {};
  const sr = d.stop_reasons || {};
  const skillTop = (d.by_skill || []).slice(0, 3).map((x) => x.skill);
  const toolTop = (d.tool_top || []).slice(0, 3);
  const daily = (d.by_day || []).slice(0, 30).reverse();   // 倒序数据反转为时间正序

  const tiles = [];
  tiles.push(`<div class="tile t-2x2 tile-link" data-detail="daily">
    <div class="tile-head"><h3>${t("dDaily")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    ${miniBars(daily.map((x) => [x.day, x.credits || 0]))}
  </div>`);
  tiles.push(kpiTile((d.credits_total || 0).toFixed(0), t("cCreditsTotal"), "accent"));
  tiles.push(kpiTile(succ + "%", t("thSucc"), "green"));
  tiles.push(kpiTile(tot.sessions, `${t("dSessions")} · ${Math.round(tot.messages / 1000)}k ${t("dMessages")}`));
  tiles.push(kpiTile(tot.tool_calls, t("dToolCalls")));
  tiles.push(`<div class="tile t-2x2 tile-link" data-detail="cost">
    <div class="tile-head"><h3>${t("chartCreditsModel")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    ${miniRows((d.credits_by_model || []).slice(0, 4).map((x) => [x.model, x.credits]))}
  </div>`);
  tiles.push(`<div class="tile t-2x1 tile-link" data-detail="stop">
    <div class="tile-head"><h3>${t("dimStop")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    <div class="tile-inline">
      <span><b class="c-green">${sr.end_turn || 0}</b> ${t("stopEnd")}</span>
      <span><b class="c-orange">${sr.aborted || 0}</b> ${t("stopAborted")}</span>
      <span><b class="c-red">${sr.error || 0}</b> ${t("stopError")}</span>
    </div>
  </div>`);
  tiles.push(`<div class="tile t-2x1 tile-link" data-detail="models">
    <div class="tile-head"><h3>${t("dimModel")} · ${t("dimWs")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    ${miniRows((d.by_model || []).slice(0, 3).map((x) => [x.model, x.count]))}
  </div>`);
  tiles.push(`<div class="tile tile-link" data-detail="tools">
    <div class="tile-head"><h3>${t("dimTool")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    <div class="tile-mini-list">${toolTop.map((x) => `<div>${esc(x.tool_name)} <em>${x.calls}</em></div>`).join("")}</div>
  </div>`);
  tiles.push(`<div class="tile tile-link" data-detail="skills">
    <div class="tile-head"><h3>${t("dimSkill")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    <div class="tile-mini-list">${skillTop.map((s) => `<div>${esc(s)}</div>`).join("") || `<span class="stats">${t("noData")}</span>`}</div>
  </div>`);
  tiles.push(`<div class="tile t-2x1 tile-link" data-detail="approval">
    <div class="tile-head"><h3>${t("dimApproval")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    <div class="stats">${t("apAccept")} ${ap.accept || 0} · ${t("apAlways")} ${ap.always_allow || 0} · ${t("apRejectRate")} ${ap.reject_rate || 0}%</div>
  </div>`);
  tiles.push(`<div class="tile tile-link" data-detail="context">
    <div class="tile-head"><h3>${t("dimCtx")}</h3><span class="tile-more">${t("detailArrow")}</span></div>
    <div class="tile-mini-list">${(d.top_context || []).slice(0, 3).map((s) => `<div>${s.context_usage_max != null ? s.context_usage_max.toFixed(0) + "% " : ""}${esc((s.title || t("untitled")).slice(0, 16))}</div>`).join("")}</div>
  </div>`);

  view.innerHTML = `<div class="bento">${tiles.join("")}</div>`;
  view.querySelectorAll(".tile-link[data-detail]").forEach((el) => {
    el.addEventListener("click", () => openDashDetail(el.dataset.detail));
  });
}

function topList(items, valFn, subFn) {
  if (!items || !items.length) return `<div class="stats">${t("noData")}</div>`;
  return `<div class="top-list">` + items.map((s) =>
    `<div class="top-item" data-sid="${esc(s.id)}">
       <span class="top-count">${valFn(s)}</span>
       <span class="top-title">${esc(s.title || t("untitled"))}</span>
       <span class="top-ws">${esc(subFn(s))}</span>
     </div>`).join("") + `</div>`;
}

// ---- 详情抽屉 ----
function openDashDetail(key) {
  const d = dashData;
  if (!d) return;
  let title = "", body = "";
  if (key === "daily") {
    title = t("dDaily");
    body = barChart(t("dDaily"), (d.by_day || []).map((x) => [x.day, x.credits || 0]));
  } else if (key === "cost") {
    title = t("dimCost");
    body = `<div class="cards"><div class="card"><div class="card-num">${(d.credits_total || 0).toFixed(2)}</div><div class="card-label">${t("cCreditsTotal")}</div></div></div>`
      + barChart(t("chartCreditsModel"), (d.credits_by_model || []).map((x) => [x.model, x.credits]))
      + topList(d.top_credit_sessions, (s) => (s.credits || 0).toFixed(1), (s) => `${s.model_id || ""} · ${shortenPath(s.workspace_path)}`);
  } else if (key === "tools") {
    title = t("dimTool");
    const s = d.tool_status || {};
    body = `<div class="cards">
      <div class="card"><div class="card-num">${s.completed || 0}</div><div class="card-label">${t("cCompleted")}</div></div>
      <div class="card"><div class="card-num">${s.failed || 0}</div><div class="card-label">${t("cFailed")}</div></div>
      <div class="card"><div class="card-num">${s.denied || 0}</div><div class="card-label">${t("cDenied")}</div></div>
      <div class="card"><div class="card-num">${s.errors || 0}</div><div class="card-label">${t("cErrors")}</div></div></div>`
      + barChart(t("chartToolTop"), (d.tool_top || []).map((x) => [x.tool_name, x.calls]))
      + barChart(t("chartKind"), (d.by_kind || []).map((x) => [x.kind, x.count]))
      + table(t("tblToolPerf"), [t("thTool"), t("thRuns"), t("thSucc"), t("thAvg"), t("thMax")],
          (d.tool_perf || []).map((x) => [x.tool_name, x.runs, `${x.success_rate}%`, x.avg_ms != null ? `${x.avg_ms}ms` : "-", x.max_ms != null ? `${x.max_ms}ms` : "-"]));
  } else if (key === "models") {
    title = `${t("dimModel")} · ${t("dimWs")}`;
    body = barChart(t("chartModelUse"), (d.by_model || []).map((x) => [x.model, x.count]))
      + barChart(t("chartMode"), (d.by_mode || []).map((x) => [x.mode, x.count]))
      + table(t("tblModelCmp"), [t("thModel"), t("thSess"), t("thMsg"), t("thAvgMsg"), t("thAvgTurn"), t("thAvgCtx"), t("thAvgSecs")],
          (d.by_model_detail || []).map((x) => [x.model, x.sessions, x.messages, x.avg_messages, x.avg_turns, x.avg_ctx != null ? x.avg_ctx : "-", x.avg_turn_secs != null ? x.avg_turn_secs : "-"]))
      + barChart(t("chartWsTop"), (d.by_workspace || []).map((x) => [shortenPath(x.workspace), x.count, x.workspace]))
      + table(t("tblWs"), [t("thWs"), t("thSess"), t("thMsg"), t("thAvgCtx"), t("thFirst"), t("thLast")],
          (d.by_workspace_detail || []).map((x) => [shortenPath(x.workspace), x.sessions, x.messages, x.avg_ctx != null ? x.avg_ctx : "-", (x.first_at || "").slice(0, 10), (x.last_at || "").slice(0, 10)]));
  } else if (key === "skills") {
    title = t("dimSkill");
    const st = d.skill_totals || {};
    body = (d.by_skill || []).length
      ? `<div class="stats" style="margin-bottom:10px">${t("skillTotals", st.activations || 0, st.distinct || 0)}</div>`
        + barChart(t("chartSkill"), d.by_skill.map((x) => [x.skill, x.count]))
        + table(t("dimSkill"), [t("thSkill"), t("thSkillCount"), t("thSkillSess")], d.by_skill.map((x) => [x.skill, x.count, x.sessions]))
      : `<div class="stats">${t("skillNone")}</div>`;
  } else if (key === "approval") {
    title = t("dimApproval");
    const a = d.approvals || {};
    body = `<div class="cards">
      <div class="card"><div class="card-num">${a.accept || 0}</div><div class="card-label">${t("apAccept")}</div></div>
      <div class="card"><div class="card-num">${a.always_allow || 0}</div><div class="card-label">${t("apAlways")}</div></div>
      <div class="card"><div class="card-num">${a.reject || 0}</div><div class="card-label">${t("apReject")}</div></div>
      <div class="card"><div class="card-num">${a.always_reject || 0}</div><div class="card-label">${t("apAlwaysReject")}</div></div>
      <div class="card"><div class="card-num">${a.reject_rate || 0}%</div><div class="card-label">${t("apRejectRate")}</div></div></div>`;
  } else if (key === "stop") {
    title = t("dimStop");
    const sr = d.stop_reasons || {};
    body = barChart(t("dimStop"), [[t("stopEnd"), sr.end_turn || 0], [t("stopAborted"), sr.aborted || 0], [t("stopCancelled"), sr.cancelled || 0], [t("stopFailed"), sr.failed || 0], [t("stopError"), sr.error || 0]].filter((x) => x[1] > 0));
  } else if (key === "context") {
    title = t("dimCtx");
    body = `<h3 class="dash-sub">${t("topCtx")}</h3>` + topList(d.top_context, (s) => s.context_usage_max != null ? s.context_usage_max.toFixed(1) + "%" : "-", (s) => shortenPath(s.workspace_path))
      + `<h3 class="dash-sub">${t("topMsg")}</h3>` + topList(d.top_sessions, (s) => s.message_count, (s) => shortenPath(s.workspace_path));
  }

  const drawer = document.createElement("div");
  drawer.className = "drawer-backdrop";
  drawer.innerHTML = `<div class="drawer">
    <div class="drawer-head"><h2>${esc(title)}</h2><button class="ghost" id="drawerClose">✕</button></div>
    <div class="drawer-body">${body}</div>
  </div>`;
  document.body.appendChild(drawer);
  document.body.style.overflow = "hidden";  // 锁背景滚动
  requestAnimationFrame(() => drawer.classList.add("open"));
  const close = () => {
    drawer.classList.remove("open");
    document.body.style.overflow = "";
    document.removeEventListener("keydown", onKey);
    setTimeout(() => drawer.remove(), 250);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  drawer.addEventListener("click", (e) => { if (e.target === drawer) close(); });
  drawer.querySelector("#drawerClose").onclick = close;
  drawer.querySelectorAll(".top-item[data-sid]").forEach((el) => {
    el.addEventListener("click", () => { close(); openSession(el.dataset.sid, null, "dash"); });
  });
}

function shortenPath(p) {
  if (!p) return "";
  const parts = p.split("/");
  return parts.length > 3 ? ".../" + parts.slice(-2).join("/") : p;
}

function barChart(title, pairs) {
  if (!pairs.length) return `<div class="chart"><h3>${esc(title)}</h3><div class="stats">${t("noData")}</div></div>`;
  const max = Math.max(...pairs.map((p) => p[1]));
  const rows = pairs.map(([label, val, full]) => `
    <div class="bar-row">
      <div class="bar-label" title="${esc(String(full != null ? full : label))}">${esc(String(label))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(val / max * 100).toFixed(1)}%"></div></div>
      <div class="bar-val">${val}</div>
    </div>`).join("");
  return `<div class="chart"><h3>${esc(title)}</h3>${rows}</div>`;
}

function table(title, headers, rows) {
  if (!rows.length) return `<div class="chart"><h3>${esc(title)}</h3><div class="stats">${t("noData")}</div></div>`;
  const head = headers.map((h) => `<th>${esc(String(h))}</th>`).join("");
  const body = rows.map((r) =>
    `<tr>${r.map((c) => `<td>${esc(String(c))}</td>`).join("")}</tr>`).join("");
  return `<div class="chart"><h3>${esc(title)}</h3>
    <table class="dtable"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// ---- 待办视图 ----
let todoStatus = "open";
let todoShowCleared = false;

function todoIcon(it) {
  if (it.cleared) return `<span style="font-size:14px;line-height:1">🗑</span>`;
  if (it.completed) return `<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="9" fill="#007AFF"/><path d="M5.8 10.3l2.6 2.6L14.2 7.3" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  return `<svg viewBox="0 0 20 20" fill="none" stroke="#007AFF" stroke-width="1.25"><circle cx="10" cy="10" r="8.2"/></svg>`;
}

function todoItemActions(sid, it) {
  const btn = (status, label, cls) =>
    `<button class="ghost todo-act ${cls || ""}" data-sid="${esc(sid)}" data-seq="${it.seq}" data-act="${status}">${label}</button>`;
  if (it.cleared) return btn("open", t("tRestore"));
  if (it.completed) return btn("open", t("tUndo")) + btn("cleared", t("tClearItem"), "clear-act");
  return btn("done", t("tMarkDone"), "done-act") + btn("cleared", t("tClearItem"), "clear-act");
}

async function loadTodos() {
  showTodosView();
  currentRerender = loadTodos;
  const view = $("todosView");
  view.innerHTML = `<div class="placeholder">${t("loading")}</div>`;
  const d = await api(`/api/todos?status=${todoStatus}&show_cleared=${todoShowCleared}&limit=500`);
  const tot = d.totals;
  const parts = [];
  parts.push(`<div class="todos-head">
    <h2 class="dash-title">${t("todosTitle")}</h2>
    <div class="todos-filter">
      <button class="ghost ${!todoShowCleared && todoStatus === "open" ? "active" : ""}" data-st="open">${t("tOpen")} (${tot.open})</button>
      <button class="ghost ${!todoShowCleared && todoStatus === "all" ? "active" : ""}" data-st="all">${t("tAll")} (${tot.total})</button>
      <button class="ghost ${!todoShowCleared && todoStatus === "done" ? "active" : ""}" data-st="done">${t("tDone")} (${tot.done})</button>
      <button class="ghost ${todoShowCleared ? "active" : ""}" id="clearedToggle">${t("showCleared")} (${tot.cleared})</button>
    </div>
  </div>`);
  parts.push(`<div class="stats" style="margin-bottom:12px">${t("todosFrom", tot.sessions)}</div>`);

  if (!d.sessions.length) {
    parts.push(`<div class="placeholder">${t("noTodos")}</div>`);
  } else {
    for (const g of d.sessions) {
      const openCount = g.items.filter((i) => !i.completed).length;
      parts.push(`<div class="todo-group">
        <div class="todo-group-head" data-sid="${esc(g.session_id)}">
          <span class="todo-group-title">${esc(g.title || t("untitled"))}</span>
          <span class="badge">${openCount} ${t("unfinished")} / ${g.items.length}</span>
          <span class="top-ws">${esc(shortenPath(g.workspace_path))} · ${fmtTime(g.last_modified_at)}</span>
        </div>
        ${g.list_description ? `<div class="todo-desc">${esc(g.list_description)}</div>` : ""}
        <ul class="todo-items">
          ${g.items.map((it) => `<li class="${it.cleared ? "cleared" : (it.completed ? "done" : "open")}">
            <span class="chk">${todoIcon(it)}</span>
            <span class="todo-text todo-jump" data-sid="${esc(g.session_id)}" data-src="${g.source_seq != null ? g.source_seq : ""}" title="${t("jumpToSession")}">${esc(it.task)}</span>
            <span class="todo-actions">${todoItemActions(g.session_id, it)}</span>
          </li>`).join("")}
        </ul>
      </div>`);
    }
  }

  view.innerHTML = parts.join("");
  view.querySelectorAll(".todos-filter button[data-st]").forEach((b) => {
    b.onclick = () => { todoShowCleared = false; todoStatus = b.dataset.st; loadTodos(); };
  });
  const ct = view.querySelector("#clearedToggle");
  if (ct) ct.onclick = () => { todoShowCleared = !todoShowCleared; loadTodos(); };
  view.querySelectorAll(".todo-act[data-act]").forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      markTodo(b.dataset.sid, parseInt(b.dataset.seq, 10), b.dataset.act);
    };
  });
  view.querySelectorAll(".todo-jump").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      const src = el.dataset.src === "" ? null : parseInt(el.dataset.src, 10);
      openSession(el.dataset.sid, src, "todos");
    };
  });
  view.querySelectorAll(".todo-group-head[data-sid]").forEach((el) => {
    el.onclick = () => openSession(el.dataset.sid, null, "todos");
  });
}

async function markTodo(sid, seq, status) {
  try {
    await api("/api/todos/mark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, seq, status }),
    });
    loadTodos();
  } catch (e) {
    alert(t("markFail") + e.message);
  }
}

// ---- Kiro 眼中的你(半自动桥接)----
let profileCanAuto = false;
async function loadProfile() {
  showProfileView();
  currentRerender = loadProfile;
  const view = $("profileView");
  view.innerHTML = `<div class="placeholder">${t("loading")}</div>`;
  const [p, s] = await Promise.all([api("/api/profile"), api("/api/settings").catch(() => ({}))]);
  profileCanAuto = !!(s.kiro_cli_installed && s.api_key_configured);
  renderProfile(p);
}

function autoBtnHtml(label) {
  if (profileCanAuto) return `<button id="autoGenBtn">⚡ ${label}(kiro-cli)</button>`;
  return `<button id="autoGenBtn" class="ghost" disabled title="${t("needConfig")}">⚡ ${label}(${t("needConfig")})</button>`;
}

function renderProfile(p) {
  const view = $("profileView");
  const parts = [`<h2 class="dash-title">${t("profileTitle")}</h2>`];

  if (p && p.content) {
    parts.push(`<div class="stats" style="margin-bottom:10px">${t("genAt")} ${fmtTime(p.created_at)} · ${t("analysisSource")}: ${esc(p.source || "")}</div>`);
    parts.push(`<div class="profile-card md">${window.renderMarkdown(p.content)}</div>`);
    parts.push(`<div class="bridge-actions" style="margin-top:14px">
      ${autoBtnHtml(t("autoRegen"))}
      <button class="ghost" id="regenBtn">${t("bridgeUpdate")}</button>
      <span id="autoInfo" class="stats"></span>
    </div>`);
  } else {
    parts.push(`<div class="stats" style="margin-bottom:12px">${t("profileEmpty")}</div>`);
    parts.push(`<div class="bridge-actions" style="margin-bottom:16px">${autoBtnHtml(t("autoGenFull"))}<span id="autoInfo" class="stats"></span></div>`);
    parts.push(bridgeBlock());
  }
  view.innerHTML = parts.join("");
  bindProfile();
}

async function autoGenerateProfile() {
  const btn = $("autoGenBtn");
  const info = $("autoInfo");
  if (btn) { btn.disabled = true; btn.textContent = t("genRunning"); }
  if (info) info.textContent = t("genCalling");
  try {
    const r = await api("/api/profile/generate?lang=" + window.getLang(), { method: "POST" });
    renderProfile(r);
  } catch (e) {
    if (info) info.textContent = t("genFail") + e.message;
    if (btn) { btn.disabled = false; btn.textContent = t("retry"); }
  }
}

function bridgeBlock() {
  return `
    <div class="bridge">
      <div class="bridge-step">
        <div class="bridge-head"><span class="step-num">1</span> ${t("bridgeStep1")}</div>
        <textarea id="promptBox" class="bridge-ta" readonly placeholder="${t("genPromptLoad")}"></textarea>
        <div class="bridge-actions">
          <button class="ghost" id="genPromptBtn">${t("genPrompt")}</button>
          <button class="ghost" id="copyPromptBtn">${t("copy")}</button>
          <span id="copyInfo" class="stats"></span>
        </div>
        <div class="stats">${t("bridgeHint1")}</div>
      </div>
      <div class="bridge-step">
        <div class="bridge-head"><span class="step-num">2</span> ${t("bridgeStep2")}</div>
        <textarea id="answerBox" class="bridge-ta" placeholder="${t("pasteHint")}"></textarea>
        <div class="bridge-actions">
          <button id="saveProfileBtn">${t("saveProfile")}</button>
          <span id="saveInfo" class="stats"></span>
        </div>
      </div>
    </div>`;
}

function bindProfile() {
  const auto = $("autoGenBtn");
  if (auto && !auto.disabled) auto.onclick = autoGenerateProfile;

  const regen = $("regenBtn");
  if (regen) regen.onclick = () => {
    const view = $("profileView");
    view.innerHTML = `<h2 class="dash-title">${t("profileTitle")}</h2>
      <div class="stats" style="margin-bottom:12px">${t("regenNote")}</div>` + bridgeBlock();
    bindProfile();
  };

  const gen = $("genPromptBtn");
  if (gen) gen.onclick = async () => {
    gen.disabled = true; gen.textContent = t("saving");
    try {
      const r = await api("/api/profile/prompt?lang=" + window.getLang());
      $("promptBox").value = r.prompt;
    } catch (e) { alert(t("genFail") + e.message); }
    finally { gen.disabled = false; gen.textContent = t("genPrompt"); }
  };

  const copy = $("copyPromptBtn");
  if (copy) copy.onclick = async () => {
    const box = $("promptBox");
    if (!box.value) { await $("genPromptBtn").onclick(); }
    box.select();
    try {
      await navigator.clipboard.writeText(box.value);
      $("copyInfo").textContent = t("copied");
    } catch (e) {
      document.execCommand("copy");
      $("copyInfo").textContent = t("copied");
    }
    setTimeout(() => { const c = $("copyInfo"); if (c) c.textContent = ""; }, 2000);
  };

  const save = $("saveProfileBtn");
  if (save) save.onclick = async () => {
    const content = $("answerBox").value.trim();
    if (!content) { $("saveInfo").textContent = t("empty"); return; }
    save.disabled = true; save.textContent = t("saving");
    try {
      await api("/api/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      await loadProfile();
    } catch (e) {
      $("saveInfo").textContent = t("saveFail") + e.message;
      save.disabled = false; save.textContent = t("saveProfile");
    }
  };
}

// ---- 设置(API key)----
async function loadSettings() {
  showSettingsView();
  currentRerender = loadSettings;
  const view = $("settingsView");
  view.innerHTML = `<div class="placeholder">${t("loading")}</div>`;
  const s = await api("/api/settings");
  // 先立即渲染页面(模型下拉标记为加载中),避免被 kiro-cli 拉模型列表阻塞
  const canListModels = s.kiro_cli_installed && s.api_key_configured;
  renderSettings(s, null, canListModels);
  // 再异步拉取模型列表,回来后只更新下拉
  if (canListModels) {
    try {
      const models = (await api("/api/settings/models")).models || [];
      fillModelSelect(s, models);
    } catch (e) {
      fillModelSelect(s, []);
    }
  }
}

function modelSectionHtml(s, models, loading) {
  // loading: 正在异步拉取;models 为数组时渲染下拉;都不是则不显示该区块
  let inner;
  if (loading) {
    inner = `<div class="stats">${t("modelsLoading")}</div>`;
  } else if (models && models.length) {
    inner = `<div class="stats" style="margin-bottom:8px">${t("modelHint")}</div>
      <div class="bridge-actions">
        <select id="modelSelect" class="filters">
          <option value="">${t("cliDefault")}</option>
          ${models.map((m) => `<option value="${esc(m.id)}" ${m.id === s.model ? "selected" : ""}>${esc(m.name)}</option>`).join("")}
        </select>
        <button id="saveModelBtn">${t("saveModel")}</button>
        <span id="modelInfo" class="stats"></span>
      </div>`;
  } else {
    // 拉取完成但列表为空(CLI 报错/无权限)
    inner = `<div class="stats">${t("modelsEmpty")}</div>`;
  }
  return `<div class="chart" id="modelSection"><h3>${t("modelSel")}</h3>${inner}</div>`;
}

function bindSaveModel(s) {
  const saveModel = $("saveModelBtn");
  if (!saveModel) return;
  saveModel.onclick = async () => {
    const model = $("modelSelect").value;
    saveModel.disabled = true; saveModel.textContent = t("saving");
    try {
      await api("/api/settings/model", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      $("modelInfo").innerHTML = `<span class="ok-text">${t("saved")}</span>`;
    } catch (e) {
      $("modelInfo").textContent = t("saveFail") + e.message;
    } finally {
      saveModel.disabled = false; saveModel.textContent = t("saveModel");
    }
  };
}

// 异步拉取模型列表回来后,替换模型区并重新绑定
function fillModelSelect(s, models) {
  const sec = $("modelSection");
  if (!sec) return;
  sec.outerHTML = modelSectionHtml(s, models, false);
  bindSaveModel(s);
}

function renderSettings(s, models, loadingModels) {
  const view = $("settingsView");
  const cliOk = s.kiro_cli_installed;
  const keyOk = s.api_key_configured;
  const srcLabel = { settings: t("srcSettings"), env: t("srcEnv"), none: t("srcNone") }[s.api_key_source] || s.api_key_source;

  view.innerHTML = `
    <h2 class="dash-title">${t("settingsTitle")}</h2>

    <div class="chart">
      <h3>${t("cliStatus")}</h3>
      <div class="setting-row">
        <span>${t("cliInstall")}</span>
        <span class="badge ${cliOk ? "ok" : "bad"}">${cliOk ? t("installed") : t("notInstalled")}</span>
      </div>
      <div class="setting-row">
        <span>${t("apiKey")}</span>
        <span class="badge ${keyOk ? "ok" : "bad"}">${keyOk ? `${t("configured")}(${esc(s.api_key_masked)})· ${t("source")}:${srcLabel}` : t("notConfigured")}</span>
      </div>
      ${cliOk ? "" : `<div class="stats">${t("cliMissing")}</div>`}
    </div>

    <div class="chart">
      <h3>${t("configKey")}</h3>
      <div class="stats" style="margin-bottom:8px">${t("keyHint")}</div>
      <div class="bridge-actions">
        <input id="keyInput" type="password" class="key-input" placeholder="ksk_..." />
        <button id="saveKeyBtn">${t("save")}</button>
        <button class="ghost" id="testKeyBtn">${t("testAuth")}</button>
        <button class="ghost" id="clearKeyBtn">${t("clear")}</button>
      </div>
      <div id="keyInfo" class="stats" style="margin-top:8px"></div>
    </div>

    ${(loadingModels || (models && models.length)) ? modelSectionHtml(s, models, loadingModels) : ""}`;

  bindSaveModel(s);

  $("saveKeyBtn").onclick = async () => {
    const key = $("keyInput").value.trim();
    if (!key) { $("keyInfo").textContent = t("enterKey"); return; }
    const btn = $("saveKeyBtn"); btn.disabled = true; btn.textContent = t("saving");
    try {
      await api("/api/settings/api_key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key }),
      });
      $("keyInput").value = "";
      await loadSettings();
    } catch (e) {
      $("keyInfo").textContent = t("saveFail") + e.message;
      btn.disabled = false; btn.textContent = t("save");
    }
  };

  $("testKeyBtn").onclick = async () => {
    const info = $("keyInfo"); info.textContent = t("testing");
    try {
      const r = await api("/api/settings/test", { method: "POST" });
      info.innerHTML = r.ok
        ? `<span class="ok-text">${t("authOk")}</span> — ${esc(r.output || "")}`
        : `<span class="bad-text">✗ ${esc(r.error || r.output || t("authFail"))}</span>`;
    } catch (e) { info.textContent = t("testFail") + e.message; }
  };

  $("clearKeyBtn").onclick = async () => {
    await api("/api/settings/api_key", { method: "DELETE" });
    await loadSettings();
  };
}

// ---- 顶栏 ----
async function refreshStats() {
  try {
    const s = await api("/api/stats");
    $("stats").textContent = t("statsFmt", s.sessions, s.messages);
  } catch (e) { /* ignore */ }
}

async function loadFacets() {
  try {
    const f = await api("/api/facets");
    const mf = $("modelFilter");
    f.models.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.value; o.textContent = `${m.value} (${m.count})`;
      mf.appendChild(o);
    });
    const df = $("modeFilter");
    f.modes.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.value; o.textContent = `${m.value} (${m.count})`;
      df.appendChild(o);
    });
  } catch (e) { /* ignore */ }
}

async function reindex() {
  const btn = $("reindexBtn");
  btn.disabled = true; btn.textContent = t("reindexing");
  try {
    await api("/api/reindex", { method: "POST" });
    await refreshStats();
    state.offset = 0;
    await loadSessions();
  } catch (e) {
    alert(t("indexFail") + e.message);
  } finally {
    btn.disabled = false; btn.textContent = t("reindex");
  }
}

function bind() {
  $("searchBtn").onclick = doSearch;
  $("searchInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  $("clearBtn").onclick = () => { $("searchInput").value = ""; state.offset = 0; loadSessions(); };
  $("reindexBtn").onclick = reindex;
  $("dashBtn").onclick = loadDashboard;
  $("todosBtn").onclick = loadTodos;
  $("profileBtn").onclick = loadProfile;
  $("settingsBtn").onclick = loadSettings;
  $("langBtn").onclick = onToggleLang;
  $("toggleSidebar").onclick = () => {
    $("sessionList").closest(".sidebar").classList.toggle("collapsed");
  };

  $("selectBtn").onclick = () => { if (state.selectMode) exitSelectMode(); else enterSelectMode(); };
  $("exportSelAll").onclick = selectAllCurrent;
  $("exportCancel").onclick = exitSelectMode;
  $("exportGo").onclick = exportSelected;

  $("modelFilter").onchange = (e) => { state.model = e.target.value; state.offset = 0; loadSessions(); };
  $("modeFilter").onchange = (e) => { state.mode = e.target.value; state.offset = 0; loadSessions(); };
  $("sortSelect").onchange = (e) => { state.sort = e.target.value; state.offset = 0; loadSessions(); };

  let t;
  $("wsFilter").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { state.workspace = e.target.value.trim(); state.offset = 0; loadSessions(); }, 300);
  });
}

async function main() {
  bind();
  applyChrome();
  currentRerender = renderPlaceholder;
  renderPlaceholder();
  await refreshStats();
  await loadFacets();
  applyChrome(); // facets 加载后刷新下拉首项文案
  await loadSessions();
  if (state.total === 0) await reindex();
}

main();
