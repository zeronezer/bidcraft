# 贡献指南

感谢你有兴趣参与 BidCraft 标书匠。

无论是提 Issue、修 bug、加功能、改文档、翻译，还是分享你的使用经验，**都同样有价值**。

---

## 目录

- [我可以做什么](#我可以做什么)
- [提交 Issue](#提交-issue)
- [开发流程](#开发流程)
- [开发环境](#开发环境)
- [代码约定](#代码约定)
- [常见改动指引](#常见改动指引)
- [不要提交的内容](#不要提交的内容)

---

## 我可以做什么

| 类型 | 说明 |
|---|---|
| 🐛 **报 Bug** | 最有价值。请带上复现步骤、报错信息、环境版本 |
| 💡 **提需求** | 说清"你现在怎么做的、卡在哪、希望变成什么样"，比说"加个 XX 功能"有用得多 |
| 📝 **改文档** | 文档里的错别字、过时描述、说不清的地方，直接 PR |
| 🌐 **翻译** | 英文文档需要持续维护；其他语种也欢迎 |
| 🔧 **写代码** | 见 [常见改动指引](#常见改动指引) |
| 📣 **分享** | 写使用心得、做视频、在公司内推广，都是贡献 |
| 🧪 **用真实场景测试** | 拿你手头的项目跑一遍，把问题报回来——这类反馈含金量最高 |

**不接受的**：往仓库里提交真实工程资料（招标文件、图纸、历史施组等），
无论是否脱敏。详见 [不要提交的内容](#不要提交的内容)。

---

## 提交 Issue

### 报 Bug 时请带上

```
1. 环境：操作系统 / Python 版本 / Node 版本 / MySQL 版本
2. 使用哪个模型（TEXT_MODEL 的值）
3. 复现步骤：从哪一步开始、点了什么、输入了什么
4. 期望结果 vs 实际结果
5. 后端终端日志（有报错的话）
6. 如果是 AI 生成质量问题，请附上「取材来源」截图（编辑器里可展开）
```

第 6 条特别重要：生成质量问题往往不是 prompt 的问题，而是**匹配到了错误的素材**。
带上取材来源，能大幅缩短定位时间。

### 提需求时请说明

- 你要解决的实际问题是什么（不要直接说解决方案）
- 你现在是怎么绕过这个问题的
- 这个需求多久遇到一次

---

## 开发流程

```bash
# 1. Fork 本仓库，然后克隆你的 fork
git clone https://github.com/<你的用户名>/bidcraft.git
cd bidcraft

# 2. 关联上游，方便同步
git remote add upstream https://github.com/zeronezer/bidcraft.git

# 3. 建分支（分支名要能看出在做什么）
git checkout -b feat/outline-drag-sort
git checkout -b fix/lot-filter-miss

# 4. 开发 + 本地验证

# 5. 提交
git add .
git commit -m "feat: 目录树支持拖拽排序"

# 6. 同步上游（避免冲突）
git fetch upstream && git rebase upstream/main

# 7. 推送并发起 PR
git push origin feat/outline-drag-sort
```

### 提交信息规范

采用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat:     新功能
fix:      修 bug
docs:     文档
refactor: 重构（不改变行为）
style:    格式（不影响代码运行）
perf:     性能优化
test:     测试
chore:    构建/工具链
```

示例：

```
feat: 对话支持批量重写指定范围的章节
fix: 多标段项目下事实未按标段过滤导致串味
docs: 补充 MinerU token 过期的排查说明
refactor: 抽取上下文组装逻辑到独立模块
```

### PR 要求

- **一个 PR 只做一件事**。顺手改的无关内容请拆成另一个 PR。
- 说明**为什么**这么改，而不只是改了什么。
- 涉及 UI 的改动请附截图。
- 涉及生成效果的改动，请附**改动前后的对比**（同一章节生成两次）。
- 提交前跑一遍 `python scripts/check_env.py`，确认没有引入配置问题。

---

## 开发环境

配置步骤见 [README 快速开始](README.md#快速开始)。开发时的两个便利：

```bash
# 后端：改动自动重载
cd backend && python -m uvicorn app.main:app --reload --port 8000

# 前端：HMR，改完立刻看到
cd frontend && npm run dev
```

**调试生成效果**的利器：在 `.env` 里打开

```ini
GEN_DEBUG_CONTEXT=true
```

它会把逐章生成时**实际发给模型的完整上下文**随 SSE 返回并打印到浏览器控制台。
排查"为什么生成的内容不对"时，先看这个——十有八九是上下文里塞错了素材。

---

## 代码约定

### 后端（Python）

- **PEP 8**，行宽 100，类型标注尽量补
- **数据库访问统一走 `app/core/database.py`** 的 `query()` / `query_one()` / `execute()`
  - ⚠️ **必须参数化 `%s`，禁止拼接 SQL**
- **建表 / 加字段必须写中文 `COMMENT`**（本项目约定，见 `scripts/init_db.sql`）
- prompt 以常量形式内嵌在各自的 `agents/*.py` 或 `services/*.py` 里，**没有独立模板目录**
  - 命名习惯：`XXX_SYSTEM`
  - 改动 prompt 时请顺带在 docstring 里写清"为什么这么改"
- 新增外部服务调用请放在 `services/` 下，并在 `core/config.py` 加配置项

### 前端（Vue 3）

- **组合式 API + `<script setup>`**
- 组件放 `src/components/`，页面放 `src/views/`
- 接口调用统一走 `src/api/index.js`，不要在组件里直接写 `axios`
- 样式遵循既有设计基调（深墨蓝侧边 + 工程蓝主色，见 `.impeccable.md`）

### 通用

- **不要引入新的重型依赖**，除非有充分理由。这个项目刻意保持轻量：
  无 ORM、无 Redis、无向量库、无 Docker 依赖。
- **不要在代码注释里写日期版本的"决策记录"**——那些内容属于 `docs/` 和 `思路/`。
- 注释解释**为什么**，不解释**是什么**。

---

## 常见改动指引

| 你想做的事 | 从哪里入手 |
|---|---|
| **改 AI 生成质量** | `backend/app/agents/writer_agent.py` 的 `WRITER_SYSTEM`；上下文组装在 `_build_context` |
| **加一个对话工具** | ① `chat_agent.py` 的 `TOOLS` 加 schema ② `_exec_tool()` 加分发 ③ 实现 `_tool_xxx` ④ `_progress_text` 加文案 |
| **改知识页提炼效果** | `backend/app/agents/knowledge_agent.py` |
| **改检索匹配逻辑** | `backend/app/services/matching.py`（三级漏斗，阈值常量 `SIM_HIGH` / `SIM_LOW`） |
| **改目录生成规则** | `backend/app/agents/orchestrate_agent.py`（`TOC_TITLES_SYSTEM` / `TOC_CHILDREN_SYSTEM`） |
| **改标段过滤** | `backend/app/services/lot_scope_service.py` + `fact_service.confirmed_facts` |
| **改经验学习** | `backend/app/agents/experience_agent.py`（三个提炼入口） |
| **加文档解析格式** | `backend/app/agents/parser_agent.py` → `parse_document` 加分支 |
| **改数据表** | `scripts/init_db.sql`（记得中文 COMMENT）+ `check_env.py` 的 `migrate_db` 补迁移 |
| **改界面** | `frontend/src/views/` 与 `frontend/src/components/` |

### 改 prompt 的正确姿势

prompt 是这个项目的核心资产。改之前请：

1. **先复现问题**，确认确实是 prompt 的问题（打开 `GEN_DEBUG_CONTEXT` 看实际上下文）
2. **准备对照组**：拿同一份材料、同一个章节，改动前后各生成一次
3. **小步改**：一次只改一条规则，否则无法归因
4. **在 PR 里附对比**：改了什么规则、生成结果差在哪

---

## 不要提交的内容

**这是硬性要求，违反的 PR 会被直接关闭。**

| 不要提交 | 原因 |
|---|---|
| `.env` 或任何含密钥的文件 | 泄密 |
| `uploads/` 下的任何文件 | 运行产物，且含真实资料 |
| 真实工程的招标文件、图纸、历史施组、工法 | **涉密 + 版权风险** |
| 任何真实项目名、单位名、内网地址、个人联系方式 | 隐私与合规 |
| `docs/样例数据/` | 同上 |

`.gitignore` 已默认拦截 `*.pdf` / `*.docx` / `*.doc` / `*.xlsx` / `*.xls` / `.env*` / `uploads/`。

**如果你需要提交示例文件**：

- 用**虚构**内容，不要脱敏真实文件（脱敏常有遗漏）
- 用 `git add -f` 强制添加绕过 `.gitignore`
- 文件尽量小（< 100KB）

**提交前请自查**：

```bash
# 检查暂存区里有没有不该提交的东西
git diff --cached --name-only

# 扫描敏感信息
git diff --cached -U0 | grep -inE "sk-[a-zA-Z0-9]{20,}|eyJ[A-Za-z0-9]{10,}|192\.168\.|10\.[0-9]+\.|password\s*="
```

---

## 关于署名

本项目采用 [Apache-2.0](LICENSE)。你贡献的代码会以同样协议发布，版权归你所有，
但会在 `NOTICE` 中体现项目整体署名。

如果你在自己的产品里用了这个项目，请按 [`ATTRIBUTION.md`](ATTRIBUTION.md) 注明来源。

---

## 最后

**不需要"准备好完美的 PR"再提交。**

一个说清楚问题的 Issue、一段改好的文档、一句"这里我没看懂"，
都比沉默更有价值。这个项目是从"受够了熬夜改标书"开始的，
每一个愿意花时间参与的人，都在让它变得更有用。

谢谢。
