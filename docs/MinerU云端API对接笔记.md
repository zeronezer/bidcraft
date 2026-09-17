# MinerU 云端 API 对接笔记

官方文档：https://mineru.net/apiManage/docs
补充参考（输出文件字段说明）：https://opendatalab.github.io/MinerU/reference/output_files/

本项目用的是**精准解析 API v4**（需 Token），不是免登录的 Agent 轻量 API。

## 支持格式与限制

| 格式 | 是否支持 | 说明 |
|---|---|---|
| PDF | 是 | ≤200MB，**≤200 页**（超限需按页切块） |
| doc / docx | 是 | Office 系列均支持，**同样受 ≤200 页上限约束**（MinerU 内部按页处理） |
| ppt / pptx / xls / xlsx | 是 | 同上，200 页上限 |
| 图片 png/jpg/jpeg/jp2/webp/gif/bmp | 是 | |
| HTML | 是 | 必须指定 `model_version=MinerU-HTML` |

- 批量单次 ≤50 个文件
- 每日额度：每账号 1000 页最高优先级，超出降优先级
- 鉴权：Header `Authorization: Bearer <token>`（注意 Bearer 后必须有空格）

## 关键请求参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `model_version` | `pipeline` | 可选 `pipeline` / **`vlm`（官方推荐）** / `MinerU-HTML` |
| `is_ocr` | false | 是否启用 OCR，仅对 pipeline/vlm 有效 |
| `enable_table` | true | 表格识别 |
| `enable_formula` | true | 公式识别 |
| `page_ranges` | 无 | 页码范围，如 `"2,4-6"`、`"2--2"`（倒数第二页）。**非 PDF 不要传** |
| `language` | ch | 语言 |
| `extra_formats` | 无 | 额外导出 docx/html/latex |

## 结果结构

`full_zip_url` 压缩包内（非 HTML）：
- `full.md` — Markdown 全文
- `**_content_list.json` — 内容列表（含 page_idx、text_level 等）
- `layout.json` / `**_model.json` — 中间结果

HTML 源解析结果不同：只有 `full.md` + `main.html`，**无 content_list.json**。

## 调用流程（本地文件批量）

```
POST /api/v4/file-urls/batch   申请上传链接 → batch_id + file_urls
PUT  <file_url>                上传文件（自动提交）
GET  /api/v4/extract-results/batch/{batch_id}   轮询 state
GET  <full_zip_url>            下载结果 ZIP
```

state: `waiting-file` / `pending` / `running` / `converting` / `done` / `failed`

## 本项目当前实现与待改进

代码位置：`backend/app/services/mineru.py`

已实现：
- PDF 按 200 页切块（`split_page_ranges`），多块 md 拼接、content_list 合并
- CDN 域名被本机代理 DNS 污染，已用固定 IP + SNI 绕过（`CDN_REAL_IPS`）
- httpx 全程 `trust_env=False` 绕系统代理

待改进 / 已确认问题：
1. **`model_version` 用的是 `pipeline`，官方推荐 `vlm`** — 实测 pipeline 输出的 md 标题全部是 `##`，
   95 个标题零层级区分，真实层级（1、1.1、2.1.1）全部丢失。建议对比 vlm 模式是否保留层级。
2. **docx 走不通**：`parse_pdf` 开头就调 `count_pages`（pypdf 读页数），docx 是 zip 格式直接崩溃
   （`invalid pdf header: b'PK\x03\x04'`）。需为非 PDF 加分支：跳过页数统计与 page_ranges，整份上传。
3. **页码出处在分页切块时会错乱**：多块 content_list 直接 extend，page_idx 可能重复/
   不连续，超大 PDF（>200 页）的页码定位不可靠。
4. `page_ranges` 对 docx/xlsx 等无意义，不能传。

## 实测记录

- **docx 统一转 PDF 方案（已验证，采纳）**：`docx2pdf` 依赖 Word COM，本机无 Word 但有 WPS
  （KWPS.Application 12.0，注册了 Word.Application 兼容 ProgID），`convert()` 直接成功：
  36.7KB docx → 125.4KB PDF / 13 页，耗时 4.6s。页数用 pypdf 读取。
  依赖：`docx2pdf` + `pywin32`（已装入托管 venv）。
- docx 直传 MinerU：API 接受且能解析（14s），但 content_list 的 page_idx 全为 0（无页码概念），
  且 page_idx 无法用于出处定位 → 放弃直传，统一转 PDF。
- 模型上下文（qwen3.8-flash）：40 万字符输入 = 230,364 token，正常返回，31s。
  10 万字符 ≈ 5.8 万 token。=> 按章节切分（单章 10 万字内不分块）完全可行。
- AI 标题分层实测（95 标题 / 66 页文档）：26s，95/95 全解析，keep 判断精准
  （封面/目录/39 个空壳全剔除）；但绝对层级被封面噪音带偏 → 需编号规则校准绝对层级，
  AI 只负责 keep 判定 + 无编号标题的归属。

## 统一解析管线（定稿方案）

```
docx ──docx2pdf(WPS COM)──> PDF ─┐
                                 ├─> MinerU（≤200页/块切块上传）──> full.md
PDF ─────────────────────────────┘
        │
        ├─ 提取标题行 + 各标题下正文字数
        ├─ AI 分层 + keep 判定（分批，~100 标题/批；输入带编号+正文字数）
        ├─ 编号规则校准绝对层级（"1、"=1级 "1.1"=2级 "2.1.1"=3级）
        ├─ 剔除 keep=false（封面/目录/空壳父标题）
        └─ 按叶子章节切分（单章 ≤10 万字不分块）→ 逐章提炼知识页
```

---

## 本项目最终选型：docx 先转 PDF，再统一走 PDF 流程

### 为什么不让 MinerU 直接吃 docx

实测 docx 直传 MinerU 可以解析成功，但有两个硬伤：
1. `content_list.page_idx` **全为 0** —— docx 无页码概念，出处定位失效；
2. 无法预知页数，一旦文档超过 200 页，无法按页切块（MinerU 对 docx 同样有 200 页上限）。

### 最终链路

```
docx ──docx2pdf(WPS COM)──> PDF ──┐
                                   ├──> pypdf 数页 ──> >200页按页切块 ──> MinerU ──> full.md
PDF ───────────────────────────────┘                                            │
                                                                                 ↓
                                                               AI 推断层级 → 章节树 → 提炼
```

### 环境依赖（已安装）

- `docx2pdf`（依赖 pywin32 + 本机 Office/WPS 的 COM 接口）
- `pdfplumber`（备用：校验转换质量、提取表格）
- 本机无 LibreOffice、无 Microsoft Word，**只有 WPS Office**——
  实测 WPS 提供兼容的 COM 接口，`docx2pdf.convert()` 可直接调用，**无需额外配置**。
- 实测：36.7 KB docx → 125.4 KB PDF，13 页，耗时 7.5 秒。

### 注意

- 转换是同步阻塞的，大文档（上百页）可能耗时几十秒，需放在后台任务线程里跑。
- COM 调用会短暂拉起 WPS 进程，服务端场景需注意并发与进程残留；
  单机 MVP 阶段可接受，生产环境建议改用 LibreOffice headless。
- 转换后的 PDF 再交给 MinerU，页码与原生 PDF 一致，出处定位逻辑可完全复用。
