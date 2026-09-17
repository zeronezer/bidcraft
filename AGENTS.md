# AGENTS.md

> 本文件面向 AI 编程助手（Claude Code、Codex、Cursor、Trae、Windsurf、Cline、WorkBuddy 等）。
> 如果你是被用户"把这个项目跑起来"而读到这里的，**按下面的步骤做即可**，不需要通读全部源码。

---

## 你的任务

把这个项目在用户本机跑起来，并确认前端页面能正常打开。

**在动手前，先向用户索要这 3 个必填配置项**（你无法自己生成，不要臆造占位值）：

| 配置项 | 说明 | 去哪获取 |
|---|---|---|
| LLM API Key | 阿里云百炼（DashScope）的 Key，形如 `sk-xxx` | https://bailian.console.aliyun.com → API-KEY 管理 |
| MinerU Token | 文档解析服务令牌，形如 `eyJxxx` | https://mineru.net → API 管理 → 创建 Token |
| MySQL 连接信息 | 主机 / 端口 / 用户 / 密码（默认库名 `bid_chat_db`） | 用户本机已装的 MySQL，或新建一个云 MySQL 实例 |

如果用户暂时没有百炼或 MinerU 账号，**照样可以把项目跑起来**：服务能启动、页面能打开、可以新建项目，
只是"上传文件解析"与"AI 生成"两步会失败。先跑通框架，再补齐 Key。

---

## 项目结构（先看这个，别全盘扫描）

```
bidcraft/
├─ backend/                 FastAPI 后端（Python ≥3.11）
│  ├─ requirements.txt      依赖清单
│  └─ app/
│     ├─ main.py            入口：注册 8 个路由 + 挂载 /uploads 静态目录
│     ├─ core/config.py     配置（从项目根 .env 读）
│     ├─ core/database.py   pymysql 直连 MySQL，无 ORM（thread-local 连接复用）
│     ├─ api/               REST 路由：projects/files/chat/compile/knowledge/experience/facts/sections
│     ├─ services/          业务服务：解析、LLM 客户端、知识库、编排、正文编写等
│     └─ agents/            各智能体的 prompt 与抽取逻辑（prompt 内嵌在各自文件里）
├─ frontend/                Vue 3 + Vite 前端
│  └─ src/                  views（页面）/ components（编辑器、抽屉等）/ api（接口封装）
├─ scripts/
│  ├─ check_env.py          环境自检 + 建表（先跑这个）
│  ├─ init_db.sql           建表 SQL
│  ├─ seed_demo.py          可选：灌入演示数据
│  ├─ start_dev.bat         Windows 一键启动
│  └─ start_dev.sh          macOS / Linux 一键启动
├─ docs/                    设计文档（含 思路/ 设计决策文集）
└─ .env.example             配置模板 → 复制为 .env
```

---

## 启动步骤

### 1. 检查运行时

```bash
python --version    # 需要 ≥ 3.11
node --version      # 需要 ≥ 18
```

缺哪个就提示用户安装（Windows 可用 `winget install Python.Python.3.12` / `winget install OpenJS.NodeJS`）。

### 2. 安装依赖

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm install
```

> **不需要装 Word / WPS。** docx 走 python-docx 原生解析（读 Word 段落样式），PDF 与图片才交给 MinerU。
> `docx2pdf` 仅存在于一个默认不走的兜底分支里，已列为可选依赖。

### 3. 配置 .env

在**项目根目录**（与 `backend/`、`frontend/` 同级）创建 `.env`：

```bash
cp .env.example .env
```

然后把上面向用户索要的 3 类值填进去。关键变量：

```ini
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=用户给的密码
MYSQL_DATABASE=bid_chat_db

LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=用户给的百炼Key
TEXT_MODEL=qwen3.8-flash
VISION_MODEL=qwen3.8-flash

MINERU_API_TOKEN=用户给的MinerU令牌
```

> `.env` 已在 `.gitignore` 中，不会被提交。**不要**把真实 Key 写进任何其他文件。

### 4. 建库建表

先确认 MySQL 里 `bid_chat_db` 这个库存在（不存在就 `CREATE DATABASE bid_chat_db DEFAULT CHARSET utf8mb4;`），然后：

```bash
python scripts/check_env.py --init-db
```

这个脚本会逐项自检配置并打印缺失项，然后建表。**看到报错先读它的提示**，它会告诉你缺什么、去哪补。

### 5. 启动

**一键（推荐）：**

```bash
# Windows
scripts\start_dev.bat

# macOS / Linux
bash scripts/start_dev.sh
```

**或手动起两个进程：**

```bash
# 终端 1 —— 后端
cd backend && python -m uvicorn app.main:app --reload --port 8000

# 终端 2 —— 前端
cd frontend && npm run dev
```

### 6. 验证

```bash
curl http://127.0.0.1:8000/docs     # 后端：应返回 FastAPI 文档页 HTML
```

浏览器打开 **http://127.0.0.1:5180** —— 应看到"标书匠"首页。

> 前端端口是 **5180**（不是 Vite 默认的 5173，避免与其他本地服务冲突），
> 配置在 `frontend/vite.config.js`。前端通过 Vite 代理把 `/api` 与 `/uploads` 转发到后端 8000。

### 7. 端到端确认（可选）

依次点击：新建项目 → 填项目名 → 上传一份 PDF 招标文件 → 等解析完成。
若解析成功且能看到标段/章节信息，说明 MinerU 与 LLM 两条链路都通了。

---

## 常见问题排查

| 现象 | 原因与处理 |
|---|---|
| 后端启动报 `Access denied for user` | `.env` 里 MySQL 账号密码不对 |
| 后端启动报 `Unknown database 'bid_chat_db'` | 库还没建，见步骤 4 |
| 前端页面能开但接口全 404 / 跨域报错 | 后端没起来，或不在 8000 端口；检查 `vite.config.js` 里的 proxy target |
| 前端起不来报端口被占用 | 5180 被占用，改 `frontend/vite.config.js` 的 `server.port` |
| 上传文件后一直"解析中" | MinerU Token 无效或余额不足；看后端终端日志 |
| AI 生成报 401 / 403 | 百炼 Key 无效，或该 Key 未开通对应模型 |
| MinerU 下载结果报 TLS/SSL 错误 | 本机代理污染了 CDN 的 DNS。在 `.env` 设 `MINERU_CDN_IPS=<真实IP>`（详见 `backend/app/services/mineru.py` 顶部注释） |
| docx 解析失败 | docx 走 python-docx，与 Word/WPS 无关；检查文件是否加密或为 .doc 旧格式 |

---

## 改代码时请注意

- **prompt 不在独立模板目录**，各智能体的 system prompt 以常量形式内嵌在 `backend/app/agents/*.py` 与 `backend/app/services/*.py` 里（如 `writer_agent.WRITER_SYSTEM`、`orchestrate_agent.TOC_TITLES_SYSTEM`）。
- **数据库访问无 ORM**：统一走 `app/core/database.py` 的 `query()/query_one()/execute()`，用参数化 `%s` 占位，不要拼 SQL。
- **建表/加字段必须写中文 `COMMENT`**（这是本项目的约定，见 `scripts/init_db.sql`）。
- **前端正文编辑器是唯一内容载体**：AI 生成的正文通过 SSE 流式写入 TipTap，不要另建一套正文存储。
- 新增对话工具时，在 `backend/app/agents/chat_agent.py` 的 `TOOLS` 列表加 schema，并在 `_exec_tool()` 加分发分支。
- 标段（lot）是操作的基本单位：凡是取事实、取文件、生成正文，都要按当前选定标段过滤，不要把别的标段内容混进来。

---

## 不要做的事

- ❌ 不要把 `.env`、`uploads/`、`docs/样例数据/` 之类的内容提交到 git
- ❌ 不要往仓库里放任何真实工程的招标文件、图纸、历史施组（涉密，且 `.gitignore` 已默认拦截 `*.pdf` / `*.docx` / `*.xlsx`）
- ❌ 不要臆造 API Key 或 Token 的占位值来"让它看起来能跑"

---

## 用户可能这么说

| 用户说 | 你要做 |
|---|---|
| "把项目跑起来" | 按本文档 1~6 步执行 |
| "为什么上传文件没反应" | 查 MinerU Token 与后端日志 |
| "帮我加个功能" | 先读 `docs/逆向说明` 与 `backend/app/agents/`，再动手改 |
| "这是怎么回事 / 为什么这么设计" | 读 `docs/思路/` 下的设计决策文集 |
