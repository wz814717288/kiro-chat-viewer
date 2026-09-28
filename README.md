# Kiro 历史对话查看器 (kiro-chat-viewer)

一个本地 Web 服务,读取 Kiro IDE 存储在本地的历史对话,提供**列表浏览、会话详情、全文检索**。
数据只读,默认只绑定 `127.0.0.1`,不上传任何内容。

## 数据来源

```
~/.kiro/sessions/<workspace-hash>/<session-id>/
    ├── session.json     # 会话元数据:标题、工作区、模型、时间
    └── messages.jsonl   # 逐行事件:user / assistant / tool_call / tool_result
```

索引存放在本项目 `data/index.db`(SQLite + FTS5 全文索引),不会写回 Kiro 数据。

## 目录结构

```
kiro-chat-viewer/
├── backend/
│   ├── config.py    # 路径与运行配置
│   ├── scanner.py   # 定位会话目录
│   ├── parser.py    # 解析 session.json / messages.jsonl(逐行)
│   ├── db.py        # SQLite + FTS5,增量索引与查询
│   ├── analyzer.py  # 会话分析(可插拔 LLM,本地启发式降级)
│   └── app.py       # FastAPI 接口 + 静态前端托管
├── frontend/        # 原生 JS 单页界面,无需构建
│   ├── index.html
│   ├── style.css
│   ├── markdown.js  # 自包含 Markdown 渲染 + 代码高亮
│   └── app.js
├── requirements.txt
└── README.md
```

## 运行

### 前置条件

- Python 3.8+(可选:用一键脚本时,本机没有可用 Python 会自动用 [uv](https://docs.astral.sh/uv/) 下载独立 Python 到项目内 `.python/`,首次需联网,约 100MB)
- 本机已安装并使用过 Kiro(存在 `~/.kiro/sessions` 历史数据)
- (可选)kiro-cli + API key —— 仅「一键自动生成画像」需要;没有则用手动桥接

### 获取代码

```bash
git clone https://github.com/wz814717288/kiro-chat-viewer.git
cd kiro-chat-viewer
```

### 一键启动(推荐)

脚本会自动定位 Python(依次尝试 `KCV_PYTHON`、项目内 `.python/`、`python3.13`…`python3.8`、`python3`、`python`;都不可用时自动安装)、创建虚拟环境、安装依赖、探测可用端口、启动服务并打开浏览器。已有 `.venv` 不可用时会自动重建。网络受限可设置 `PIP_INDEX_URL` / `UV_PYTHON_INSTALL_MIRROR` 镜像,见 `.env.example`。

```bash
# macOS / Linux(在 kiro-chat-viewer 目录下)
./run.sh
```

```bat
REM Windows(在 kiro-chat-viewer 目录下)
run.bat
```

浏览器会自动打开 http://127.0.0.1:8765 (端口被占用时自动顺延)。

### 手动启动(备选)

```bash
pip install -r requirements.txt
python -m backend.app
# 或: uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

首次访问若索引为空会自动建索引;之后点「重建索引」做增量更新(仅重扫变更过的会话)。启动日志会打印发现的会话数;若提示"未找到 Kiro 历史会话目录",说明本机没有 Kiro 数据或版本/存储布局不同(当前读取 IDE v2 的 `~/.kiro/sessions`)。

可选配置见 `.env.example`。

## 功能

- **浏览**:会话列表,支持按模型 / 模式筛选,按更新时间、创建时间、消息数、标题排序,工作区关键词过滤,分页。
- **详情**:对话按用户/Kiro 气泡展示,内容用内置 Markdown 渲染器渲染(代码块高亮),工具调用/结果可折叠;支持「隐藏工具消息」开关、会话内查找(高亮 + 上下跳转)。
- **全文检索**:FTS5 全文索引(unicode61 分词,中文可搜),命中高亮片段,点击跳转到对应消息。
- **导出**:单会话导出为 Markdown 或 JSON。
- **统计仪表盘**:
  - 概览:会话/消息总量、每日活跃度
  - 工具维度:调用排行、成功率与平均/最大耗时、操作类型(read/edit/execute…)分布、完成/失败/拒绝/连接错误汇总
  - 模型维度:使用分布、各模型对比(会话数、均消息、均轮次、均上下文占用、均处理时长)
  - 工作区维度:会话数 Top、各项目画像(消息量、上下文占用、时间跨度)
  - 上下文占用最高的会话、消息最多的会话
  - 指标数据来自对 `messages.jsonl` 中 `tool_call`/`tool_result`(按 toolCallId 关联)、`session_metadata`(上下文占用、错误)、`turn_start`/`turn_end`(轮次耗时)事件的解析
- **会话分析**:为单会话生成摘要 + 主题标签,结果缓存。默认本地启发式(离线),配置 LLM 后可用大模型生成。
- **历史待办**:从各会话的 `todo_list` 记录中还原任务清单(取最后一份非空快照),跨会话汇总未完成/已完成任务,可按状态筛选,点击回到原会话。支持**手动标记完成 / 撤销 / 清除(软删除)**——这些操作存在独立的 `todo_overrides` 覆盖层,不改 Kiro 原始数据,reindex 后依然保留;可用「显示已清除」查看并恢复清除项。
- **你的数字画像**:聚合全部历史(时间习惯、项目/工具偏好、待办完成率、高频主题、真实提问样本)生成一段分析 Prompt。两种生成方式:
  - 一键自动:在「设置」里配置 Kiro API key 并安装 kiro-cli 后,服务后台调用 `kiro-cli` 用 Kiro 自己的模型生成,全程本地。
  - 半自动桥接:复制 Prompt 到 Kiro IDE 对话生成画像,再把回答贴回保存。
  - 生成语言跟随界面语言;输出不含开场白(从标题开始)。
- **中英文切换**:默认英文界面;顶栏语言按钮(中 / EN)切换界面展示语言,并同步影响画像的生成语言。偏好存于浏览器 localStorage。
- **设置**:Web 界面配置 Kiro API key(ksk_)与生成用模型。key 仅存本机 `data/settings.json`(0600 权限、被 .gitignore 忽略、界面只显示打码值),供 kiro-cli 调用使用。也可改用环境变量 `KIRO_API_KEY`。模型从 `kiro-cli --list-models` 拉取,注意账号可能只对部分模型有权限(遇到「模型不可用」就在设置里换一个)。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/stats` | 索引统计 |
| POST | `/api/reindex?force=false` | 增量/全量重建索引 |
| GET  | `/api/facets` | 可筛选的模型/模式/工作区维度值 |
| GET  | `/api/dashboard` | 统计聚合数据 |
| GET  | `/api/todos?status=open\|done\|all&show_cleared=` | 历史待办(按会话分组,叠加手动覆盖) |
| POST | `/api/todos/mark` | 手动标记待办 done / open(撤销/恢复) / cleared |
| GET  | `/api/profile` | 已保存的「你的数字画像」 |
| GET  | `/api/profile/prompt` | 生成用于桥接的分析 Prompt |
| POST | `/api/profile` | 保存桥接回来的画像内容 |
| POST | `/api/profile/generate` | 用本地 kiro-cli 自动生成画像 |
| GET  | `/api/settings` | kiro-cli 安装状态 / API key 是否已配置(打码) |
| POST | `/api/settings/api_key` | 保存 Kiro API key |
| DELETE | `/api/settings/api_key` | 清除已保存的 key |
| POST | `/api/settings/test` | 用当前 key 测试 kiro-cli 认证 |
| GET  | `/api/settings/models` | 列出 kiro-cli 可用模型 |
| POST | `/api/settings/model` | 保存自动生成使用的模型 |
| GET  | `/api/sessions?workspace=&model=&mode=&sort=&limit=&offset=` | 会话列表(筛选/排序/分页) |
| GET  | `/api/sessions/{id}` | 单会话完整对话 |
| GET  | `/api/sessions/{id}/export?fmt=md\|json` | 导出会话 |
| GET  | `/api/sessions/{id}/analysis?refresh=&cached_only=` | 会话分析(摘要+标签) |
| GET  | `/api/search?q=&limit=` | 全文检索,返回高亮片段 |

### 排序取值(sort)

`modified_desc`(默认)、`created_desc`、`messages_desc`、`messages_asc`、`title_asc`

### 启用 LLM 分析(可选)

不配置时使用本地启发式分析(离线)。配置以下环境变量后,分析改用 OpenAI 兼容接口:

```bash
export KCV_LLM_API_BASE=https://api.openai.com/v1
export KCV_LLM_API_KEY=sk-xxx
export KCV_LLM_MODEL=gpt-4o-mini   # 可选,默认 gpt-4o-mini
```

## 在 Kiro IDE 中使用

服务启动后,在 Kiro 命令面板执行 `Simple Browser: Show`,输入 `http://127.0.0.1:8765` 即可在编辑器标签页内嵌打开。

## 环境变量

- `KIRO_HOME`:覆盖 `~/.kiro` 路径
- `KCV_HOST` / `KCV_PORT`:服务监听地址/端口(默认 127.0.0.1:8765)
- `KCV_DB`:索引数据库路径

## 后续规划

- Kiro/LLM 分析:会话摘要、主题标签、跨会话统计
- 语义检索:embedding + 向量库
- 打包为 Kiro 扩展(Webview),实现点击跳转文件、按当前工作区过滤
