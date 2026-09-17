"""项目文件章节索引（skill 式按需加载，2026-09-04 决策：项目文件弃用切片 RAG）。

思路（用户提出，类 skill 的"先描述、符合再取内容"）：
1. **建索引**（解析完成后一次性）：用 markdown `#` 数量判断标题层级；
   **每个章节单独调一次 LLM** 提取「概要 + 适用范围」（50~200 字，基于该章完整原文），
   与章节全路径标题、原文行号区间一起存 `file_section_index`；
2. **用时两段式**：先把章节目录（路径+概要+适用范围）交给 AI 选取"本次需要哪些章节"
   （描述匹配，不切碎内容）→ 再按行号区间从 full.md 取**完整原文**（含表格/条款/图片，
   永不截断）交给 AI 生成结果。

索引规则（用户决策，最终版）：
- 层级用 markdown `#` 数量判断，1~3 级入索引；
- 层级下内容 < 5 字的章节直接移除（噪声标题）；
- **不合并、不拆分**：父子条目共存（选父加载时原文天然含子节，选子则只取子节，
  load_sections 去重保证不重复注入）——粒度选择权交给选章 AI。

1M 上下文下完整章节轻松装下，彻底解决扫描件大表跨切片割裂的问题。
知识库（知识页/工法）维持原有三级漏斗 + 向量兜底，不在本次改动范围。

fail-safe：索引构建/选取任何失败均静默降级为"无文件片段"，不阻断生成主流程。
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.core.database import execute, query, query_one
from app.services.llm import llm

# 入索引的标题层级（1~3 级）
INDEX_MAX_LEVEL = 3
# 层级下**正文字数**（剥离标题行与标记后）小于该值的章节直接移除（只有标题的空章节）
# 2026-09-10：原为 5 且用的是"原始 md 长度"——标题行本身（如 "## 新建长沙至赣州高速铁路江西段"）
# 就有十几字符，导致空章节照进索引，列表里出现"只有标题、17 个字"的条目。
EMPTY_SECTION_CHARS = 20
# 单章写入时最多选取的章节数
MAX_SELECT = 8
# 注入上下文的文件原文总字符上限（1M 上下文下的安全余量）
MAX_TOTAL_CHARS = 400_000
# 逐章概要提取的并发度（每章一次 LLM 调用）。
# 20 是按 TPM 留足余量定的：单章输入约数千 token + 输出上限 4000，
# 20 路并发同时在飞的 token 约二十来万，远低于模型的 500 万 TPM。
SUMMARY_CONCURRENCY = 20
SUMMARY_MAX_TOKENS = 4000

_HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

SUMMARY_SYSTEM = """你是工程文档分析专家。下面给出一篇章节的**完整原文**，请为它生成索引条目
（供后续 AI 判断"编写某内容时应否参考本章节"，描述越具体判断越准）：
- summary（50~200 字）：该章节具体讲了什么——包含哪些关键内容、哪些表格（表名）、哪些重要数据/指标/条款，
  逐项点明。章节覆盖多个对象（如多个标段 CGJXZQ-1~10、多项工程）时必须完整列举全部对象与全部要点，
  不得只概括开头或以偏概全。例如："各标段分阶段工期要求表，逐标段列出开工日期、竣工日期、阶段工期节点（含架梁开始/完成、铺轨开始等 6 个节点日期）"；
- applies_to（50~200 字）：适用范围——编写施组的什么章节/什么问题时应当参考本节，以及典型场景举例，
  同样需覆盖章节涉及的全部对象。
忠于原文，概括具体信息而不是空泛描述（禁止写"介绍了相关内容"这类无信息量的话）。
只输出 JSON：{"summary": "...", "applies_to": "..."}，不要解释、不要代码围栏。"""

# 建索引时同一次调用顺带抽取该章的全局性事实（供项目全局参数表，逐章更聚焦准确）。
# 只抽全局性硬事实；明细数据/通用规范套话不抽（与 fact_agent 门槛一致）。
SUMMARY_FACTS_SYSTEM = """你是工程文档分析专家。下面给出一篇章节的**完整原文**，请一次完成三件事：
A) 生成索引条目（供后续 AI 判断"编写某内容时应否参考本章节"，描述越具体判断越准）：
- summary（50~200 字）：该章节具体讲了什么——关键内容、表格（表名）、重要数据/指标/条款，逐项点明；
  覆盖多个对象时必须完整列举全部对象，不得以偏概全（禁止写"介绍了相关内容"这类空话）；
- applies_to（50~200 字）：编写施组的什么章节/什么问题时参考本节，覆盖章节涉及的全部对象。
B) 抽取本章出现的【全局性、约束性】事实 facts（供本项目全局参数表，正文引用时使用）：
- 只抽硬事实：总工期/开竣工、最高投标限价、标段里程与范围、桥梁/隧道/路基总规模、轨道类型、
  主要技术标准、质量验收标准等级、安全/质量目标、项目班子配置要求、地质关键条件（承载力/地下水位/不良地质）等；
- 不抽：清单逐项数量、单根桩长/桩径、逐桥跨径、混凝土配合比、钢筋规格等明细参数，以及通用规范套话；
- category 只取：工期/人员/机械/造价/地质/结构/质量标准/安全目标/其他；
- fact_key 用简短标准名且同类归一（如总工期、最高投标限价、桥梁总长）；fact_value 保留原文数字；unit 单列单位；
- confidence 0~1：数字明确 0.9+，需推断 0.6~0.8；每章最多 20 条，没有就空数组；
- 忠于本章原文；若原文没有确定性数值，宁可少抽不要臆造。
C) 判定本章内容的**标段归属** applies_lots（供"正文只写本标段内容"用）：
- ["CGJXZQ-x"]：只涉及某一个标段（出现该标段的里程/构造物/临时设施，或明确写了"X标"）；
- ["all"]：全线共性（项目名称、承包方式、总工期、技术标准、气候水系等，各标段都适用）；
- ["background"]：全线背景（如「全线控制性工程清单」「各标段工程一览」——可作背景，
  但**不是某标段的工程内容**）；
- ["multi"]：本章并列涉及多个标段的具体工程（如各标段工期表、分标段图纸目录）；
- []：判不出来就留空，**严禁默认给 ["all"]**（"判不出就标 all"会让全线内容污染每个标段）。
只输出 JSON：{"summary": "...", "applies_to": "...", "applies_lots": ["CGJXZQ-x"], "facts": [{"category": "...", "fact_key": "...", "fact_value": "...", "unit": "...", "confidence": 0.9}]}，不要解释、不要代码围栏。"""

SELECT_SYSTEM = """你是施工组织设计编制专家。给定「编写需求」和一份项目文件的章节索引
（路径 + 概要 + 适用范围），请选出编写时需要参考的**原文章节**。

规则：
1. 只按概要与适用范围判断相关性，看不到原文，选中的章节会以完整原文提供；
2. 宁多勿漏（相关就选，上下文足够大），但明显无关的不要选；
3. 需要某章全貌时选父章节（原文含子节），只需要某个具体子项时选子章节；
4. 最多输出 {max_select} 个序号；没有相关的返回空数组。
只输出 JSON：{{"ids": [1, 5, 12]}}（ids 为索引清单中第一列的序号），不要解释。"""


def _plain_chars(text: str) -> int:
    """正文字数：剥掉 HTML 标签与 markdown 标记、压缩空白后的字符数。

    用于 char_count（此前直接数原始 md 行长度，含标题、`|`、HTML 标签与空行，明显偏大）。
    """
    s = re.sub(r"<[^>]+>", "", text or "")
    s = re.sub(r"[#*`>|\-\s]+", "", s)
    return len(s)


def _iter_sections(full_md: str):
    """md 行 → 1-3 级章节列表 [{level, path, start, end, char_count}]（含子节内容）。

    层级用 markdown `#` 数量判断。**正文**（不含标题行、剥离标记）不足 EMPTY_SECTION_CHARS
    的噪声/空章节直接移除。
    """
    lines = full_md.splitlines()
    stack: list[dict] = []   # 开放中的各级节点
    done: list[dict] = []

    def close_to(level: int, end_i: int) -> None:
        while stack and stack[-1]["level"] >= level:
            node = stack.pop()
            node["end"] = end_i
            # 跳过标题行本身，只数正文（含子节内容）
            node["char_count"] = _plain_chars("\n".join(lines[node["start"] + 1:end_i]))
            done.append(node)

    for i, ln in enumerate(lines):
        m = _HEAD_RE.match(ln)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            close_to(level, i)
            path = " > ".join([s["title"] for s in stack] + [title])
            stack.append({"level": level, "title": title, "path": path, "start": i})
    close_to(1, len(lines))

    out = []
    for node in done:
        # 层级下内容 < 5 字：噪声标题，直接移除
        if not node.get("char_count") or node["char_count"] < EMPTY_SECTION_CHARS:
            continue
        if node["level"] > INDEX_MAX_LEVEL:
            continue
        out.append(node)
    return out


def build_index(file_id: int, project_id: int, parsed_path: str, on_progress=None,
                should_stop=None, fact_hint: str = "", on_chapter=None,
                md_text: str | None = None) -> int:
    """为已解析文件构建章节索引：**每个章节一次 LLM** 提取概要与适用范围（同时顺带抽取该章全局事实）。

    md_text：直接给出 markdown 文本时用它（**docx 走对象级切章、不落 full.md** 的场景）；
    为空则按老路读 parsed_path/full.md。无论哪条路，章节原文都会存进索引 content 字段，
    之后取原文不再依赖文件与行号。
    """
    if md_text is not None:
        full_md = md_text
    else:
        md_path = Path(parsed_path) / "full.md"
        if not md_path.exists():
            raise FileNotFoundError(md_path)
        full_md = md_path.read_text(encoding="utf-8")
    all_lines = full_md.splitlines()
    sections = _iter_sections(full_md)
    if not sections:
        return 0

    execute("DELETE FROM file_section_index WHERE file_id = %s", (file_id,))

    # 逐章提取（每章一次 LLM 调用，并发 3）
    entries: dict[int, dict] = {}

    def _summarize(gi: int, s: dict) -> None:
        body = "\n".join(all_lines[s["start"]:s["end"]])
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": SUMMARY_FACTS_SYSTEM},
                    {"role": "user", "content": f"章节路径：{s['path']}\n\n章节原文：\n{body}\n{fact_hint}"},
                ],
                temperature=0.1,
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            sb, eb = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[sb : eb + 1]) if sb != -1 and eb != -1 else {}
        except Exception as e:  # noqa: BLE001 单章概要失败不影响其他章节
            print(f"[索引] 章节「{s['path'][:40]}」概要提取失败: {e}")
            data = {}
        # 该章标段归属（applies_lots）：章节级判一次，fact 沿用同一归属（同章内容归属一致）
        lots = data.get("applies_lots")
        lots = [str(x) for x in lots if str(x).strip()] if isinstance(lots, list) else []
        # 该章全局事实（规范化；具体落库/归并由 fact_service 处理）
        facts = []
        for f in (data.get("facts") or []):
            if not isinstance(f, dict):
                continue
            key = str(f.get("fact_key") or "").strip()
            val = str(f.get("fact_value") or "").strip()
            if not key or not val:
                continue
            facts.append({
                "category": str(f.get("category") or "其他").strip(),
                "fact_key": key[:100],
                "fact_value": val[:200],
                "unit": str(f.get("unit") or "").strip()[:30],
                "confidence": f.get("confidence", 0.7),
                "applicable_lots": lots,  # 沿用章节归属；为空则由落库侧按"未判定"处理
            })
        entries[gi] = {
            "file_id": file_id, "project_id": project_id,
            "sec_level": s["level"], "sec_path": s["path"][:500],
            "summary": str(data.get("summary") or "")[:500],
            "applies_to": str(data.get("applies_to") or "")[:300],
            "applies_lots": json.dumps(lots, ensure_ascii=False) if lots else None,
            "start_line": s["start"], "end_line": s["end"],
            "char_count": s["char_count"],
            # 原文一并入库（自包含）：取原文不再依赖文件按行切，文件删了/重解析也不影响
            "content": body,
            "facts": facts[:20],
        }

    # 受限并发调度：并发窗口内始终只跑 SUMMARY_CONCURRENCY 个，支持中途取消
    #（文件被删除时不再提交新的 LLM，进行中的少量跑完即止）
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    completed: dict[int, dict] = {}
    done = 0
    total = len(sections)
    with ThreadPoolExecutor(max_workers=SUMMARY_CONCURRENCY) as pool:
        it = iter(enumerate(sections))
        active: dict = {}

        def _fill() -> None:
            while len(active) < SUMMARY_CONCURRENCY:
                try:
                    gi, s = next(it)
                except StopIteration:
                    return
                active[pool.submit(_summarize, gi, s)] = gi

        _fill()
        while active:
            fs, _ = wait(list(active), return_when=FIRST_COMPLETED)
            for f in fs:
                gi = active.pop(f)
                f.result()
                completed[gi] = entries[gi]
                if on_chapter:
                    on_chapter({
                        "sec_path": entries[gi].get("sec_path", ""),
                        "char_count": sections[gi].get("char_count", 0),
                        "facts": entries[gi].get("facts", []),
                    })
                done += 1
            if should_stop and should_stop():
                break
            if on_progress and (done % 5 == 0 or done == total):
                on_progress(done, total)
            elif done % 20 == 0:
                print(f"[索引] 概要提取 {done}/{total}")
            _fill()

    for gi in sorted(completed):
        en = completed[gi]
        execute(
            "INSERT INTO file_section_index (file_id, project_id, sec_level, sec_path,"
            " summary, applies_to, applies_lots, lot_evidence, start_line, end_line, char_count, content)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (en["file_id"], en["project_id"], en["sec_level"], en["sec_path"],
             en["summary"], en["applies_to"], en.get("applies_lots"),
             "解析时 AI 判定", en["start_line"], en["end_line"], en["char_count"],
             en.get("content")),
        )
    return len(completed)


def get_catalog(project_id: int) -> list[dict]:
    """项目全部文件的章节索引（联文件名；含标段归属 applies_lots）。"""
    return query(
        "SELECT fi.id, fi.file_id, fi.sec_path, fi.summary, fi.applies_to, fi.char_count,"
        " fi.start_line, fi.end_line, fi.applies_lots, pf.file_name, pf.parsed_path"
        " FROM file_section_index fi"
        " JOIN project_file pf ON pf.id = fi.file_id"
        " WHERE fi.project_id = %s AND pf.status = 'parsed'"
        " ORDER BY fi.file_id, fi.start_line",
        (project_id,),
    )


def select_sections(catalog: list[dict], need: str, max_select: int = MAX_SELECT) -> list[int]:
    """AI 按编写需求从索引中选取需要的章节（skill 式第一段，描述匹配）。

    展示给 LLM 的是稳定序号 1..N（大自增 id LLM 会抄错/编造），返回后映射回真实 id。
    """
    if not catalog or not need:
        return []
    seq_map: dict[int, int] = {}
    lines = []
    for k, c in enumerate(catalog, 1):
        seq_map[k] = c["id"]
        lines.append(
            f"{k} | {c['file_name'][:30]} | {c['sec_path'][:80]} | 概要:{c['summary'][:200]} | 适用:{c['applies_to'][:120]}"
        )
    raw = llm.chat(
        [
            {"role": "system", "content": SELECT_SYSTEM.replace("{max_select}", str(max_select))},
            {"role": "user", "content": f"【编写需求】\n{need[:1500]}\n\n【章节索引】\n" + "\n".join(lines)},
        ],
        temperature=0.1,
        max_tokens=800,
    )
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e == -1:
        return []
    seqs = json.loads(raw[s : e + 1]).get("ids") or []
    out = []
    for i in seqs:
        try:
            if int(i) in seq_map and seq_map[int(i)] not in out:
                out.append(seq_map[int(i)])
        except (TypeError, ValueError):
            continue
    return out[:max_select]


# 选章结果注入正文的默认上限（防止无关/重复片段把上下文撑爆；个别章需要更多时由模型自行限制）
MAX_SELECT_INJECT = 5
# 注入正文的单章原文字符上限（个别超长章节截断到该长度）
INJECT_MAX_CHARS_PER_SECTION = 200000


# 明显不属于"正文写作材料"的目录/章节（评标/投标/资格/须知等）——选章时过滤掉，
# 防止把评分表/投标须知当成正文材料注入导致模型照着抄套话
_NON_CONTENT_RE = re.compile(
    r"(评标|评分|评审|评标办法|开标|投标邀请|投标人须知|资格审查|资格预审|合同条款|"
    r"合同格式|投标文件|中标|候选|工程量清单|报价|工期要求表|专用设备要求|"
    r"自有产业工人|劳务|分包|保函|保证金|招标公告|招标人|招标代理|"
    r"目录|目 录|封面|扉页|版权|前言|致谢)"
)

def _is_non_content(path: str) -> bool:
    return bool(_NON_CONTENT_RE.search(path or ""))


def _select_relevant(catalog: list[dict], need: str, max_select: int = MAX_SELECT_INJECT) -> list[int]:
    """AI 按编写需求从索引中选相关章节（稳定序号防编造 id）；自动过滤评标/须知类目录。"""
    if not catalog or not need:
        return []
    # 过滤非正文内容章节（评标/投标/资格/须知等），避免模型拿这些套话扩写
    usable = [c for c in catalog if not _is_non_content(str(c.get("sec_path", "")))]
    if not usable:
        return []
    seq_map: dict[int, int] = {}
    lines = []
    for k, c in enumerate(usable, 1):
        seq_map[k] = c["id"]
        lines.append(
            f"{k} | {c['file_name'][:30]} | {c['sec_path'][:80]} | 概要:{c['summary'][:200]} | 适用:{c['applies_to'][:120]}"
        )
    raw = llm.chat(
        [
            {"role": "system", "content": SELECT_SYSTEM.replace("{max_select}", str(max_select))},
            {"role": "user", "content": f"【编写需求】\n{need[:1500]}\n\n【章节索引】\n" + "\n".join(lines)},
        ],
        temperature=0.1,
        max_tokens=800,
    )
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e == -1:
        return []
    seqs = json.loads(raw[s : e + 1]).get("ids") or []
    out = []
    for i in seqs:
        try:
            if int(i) in seq_map and seq_map[int(i)] not in out:
                out.append(seq_map[int(i)])
        except (TypeError, ValueError):
            continue
    return out[:max_select]


def load_sections(catalog: list[dict], ids: list[int]) -> list[dict]:
    """按 id 取章节完整原文（含子节；父子同时选中时只保留父，避免重复注入）。

    原文来源（2026-09-10 改造）：**优先读索引里存的 content**（自包含——文件被删或
    重解析都不影响，也不再依赖 full.md 的行号）；旧数据（未回填）回退按行从文件取。
    图片引用改写为 /uploads 可访问 URL（实在没有 parsed_path 时保留原始引用）。
    """
    by_id = {c["id"]: c for c in catalog}
    picked = [by_id[i] for i in ids if i in by_id]
    # 去重：若父章节已选，跳过其子章节（路径前缀包含）
    kept: list[dict] = []
    for c in sorted(picked, key=lambda x: x["sec_path"]):
        if any(c["sec_path"].startswith(k["sec_path"] + " > ") for k in kept):
            continue
        kept.append(c)

    out = []
    total = 0
    for c in kept:
        row = query_one(
            "SELECT fi.content AS c_content, pf.file_name, pf.parsed_path"
            " FROM file_section_index fi"
            " LEFT JOIN project_file pf ON pf.id = fi.file_id"
            " WHERE fi.id = %s",
            (c["id"],),
        )
        if not row:
            continue
        raw = row.get("c_content")
        parsed_dir = Path(row["parsed_path"]) if row.get("parsed_path") else None
        if raw is None and parsed_dir:  # 旧数据回退：按行从 full.md 取
            md_path = parsed_dir / "full.md"
            if md_path.exists():
                lines = md_path.read_text(encoding="utf-8").splitlines()
                raw = "\n".join(lines[c["start_line"]:c["end_line"]])
        if not raw:
            continue
        text = _rewrite_image_refs(raw, parsed_dir) if parsed_dir else raw
        # 单章注入上限（防止超长章节/表格把上下文撑爆；不写死中断，超了直接不再注入该章）
        if len(text) > INJECT_MAX_CHARS_PER_SECTION:
            continue
        if total + len(text) > MAX_TOTAL_CHARS:
            continue
        total += len(text)
        out.append({"id": c["id"], "file_id": c["file_id"], "file": row.get("file_name") or "",
                    "path": c["sec_path"], "text": text})
    return out


def _rewrite_image_refs(text: str, parsed_dir: Path) -> str:
    """把章节原文里的 MinerU 图片引用改写为 /uploads 可访问 URL（图文混排回复用）。"""
    from app.services.knowledge_service import _image_index

    index = _image_index(parsed_dir)
    if not index:
        return text

    def _sub(m):
        url = index.get(Path(m.group(2).strip()).name)
        alt = m.group(1)
        return f"![{alt}]({url})" if url else m.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _sub, text)


def search_sections(project_id: int, query_text: str, max_select: int = MAX_SELECT_INJECT) -> list[dict]:
    """对话查询用：按问题选取相关章节并返回完整原文（图片引用改写为可访问 URL）。

    按当前标段过滤候选（"明确属于其他标段"的章节不进候选）。
    """
    try:
        catalog = get_catalog(project_id)
        if not catalog:
            return []
        from app.services.lot_scope_service import filter_catalog_by_lot
        from app.services.lot_service import selected_lot_code

        catalog = filter_catalog_by_lot(catalog, selected_lot_code(project_id))
        if not catalog:
            return []
        ids = _select_relevant(catalog, query_text, max_select=max_select)
        return load_sections(catalog, ids)
    except Exception:  # noqa: BLE001
        return []
