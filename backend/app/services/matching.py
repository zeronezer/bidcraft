"""知识页匹配服务：目录节点 → 可参考知识页（三级漏斗）。

章节类型字典废弃后（用户决策），知识页以「全路径标题」标识，匹配改为：

  第一级：粗分类/专业标签过滤 —— 目录节点路径中出现的专业词（路基/桥梁/隧道…）
          与知识页 tags 求交集，缩小候选池；节点无专业语境时用全池（不过滤）；
  第二级：标题相似度 —— 叶子标题去编号前缀后做字符 bigram Dice 系数，
          全路径逐级命中加分；≥ SIM_HIGH 直接命中；
  第三级：模糊带 [SIM_LOW, SIM_HIGH) 的候选交 LLM 按「标题 + usage」终判
          （方案 3.10 第 5 条：由 AI 按条目使用方式判断是否引用），
          每章最多一次 LLM 调用，无模糊候选则不调。

产出供两处使用：编排流程（生成思路时注入 refs，正文生成直接取用）、
对话智能体 search_knowledge_pages 工具。
"""
import json
import re

from app.core.database import query
from app.services.llm import llm

SIM_HIGH = 0.80   # 直接命中
SIM_LOW = 0.35    # 低于此值不进候选
TOP_K = 10        # 每章最多引用知识页数

# 编号前缀：「2.3 xx」「1、xx」「第X章 xx」
_NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)*\s*[、．.]?\s*")
_CN_PREFIX = re.compile(r"^第[一二三四五六七八九十百零]+[章节篇]\s*[、．.]?\s*")
# 编号级章节标题（5.2 / 5.2.2 / 2.3.1 …）：仅这类"真章节标题"才参与路径级命中计分，
# 伪叶标题（⑴布局 / ⑵实施方案）不带编号，不参与，防大范围误中。
_NUM_HEAD = re.compile(r"^\d+(?:\.\d+)*\s*")

# 与 heading/knowledge_agent 保持一致的专业标签表（第一级过滤用）
PROFESSION_TAGS = [
    "路基", "桥梁", "隧道", "站场", "轨道", "通信", "信号", "信息",
    "电力", "牵引供电", "站房", "房建", "改移道路", "既有线",
]

JUDGE_SYSTEM = """你是施工组织设计编制专家。给定一个待编写章节（含层级路径）和若干候选历史知识页，
判断哪些知识页对该章节的编写有实际参考价值（写法/结构/表格可借鉴）。

判断标准：
- 章节主题相同或相近（如"施工进度计划" ↔ "进度安排"）；
- 或属于同一专业场景的相邻主题（如"桥梁下部结构施工" ↔ "墩身施工工艺"）；
- 主题明显无关的剔除，宁缺毋滥。

只输出 JSON 数组（候选编号），不要解释、不要代码围栏：
[0, 2]"""


def normalize_title(title: str) -> str:
    """标题规范化：去编号前缀与空白，用于精准匹配与相似度计算。"""
    t = (title or "").strip()
    t = _CN_PREFIX.sub("", t)
    t = _NUM_PREFIX.sub("", t)
    return re.sub(r"\s+", "", t)


def leaf_of(full_path: str) -> str:
    """全路径标题取叶子：'工程概况 > 2.3 主要工程内容' → '2.3 主要工程内容'。"""
    return (full_path or "").split(" > ")[-1].strip()


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _dice(a: str, b: str) -> float:
    """bigram Dice 系数，中文短标题相似度够用且零依赖。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    A, B = _bigrams(a), _bigrams(b)
    inter = len(A & B)
    return 2 * inter / (len(A) + len(B)) if (A or B) else 0.0


def _path_professions(path_titles: list[str]) -> set[str]:
    """目录节点路径里出现的专业标签词。"""
    joined = " ".join(path_titles)
    return {t for t in PROFESSION_TAGS if t in joined}


def load_page_summaries() -> list[dict]:
    """读全部知识页摘要（匹配用，不读大字段）。

    返回 [{id, title, leaf, tags, category, usage, method_brief}]
    """
    rows = query(
        "SELECT id, title, tags, content FROM knowledge_page WHERE status != 'deprecated'"
    )
    pages = []
    for r in rows:
        tags = r.get("tags")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (TypeError, ValueError):
                tags = []
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        pages.append({
            "id": r["id"],
            "title": r["title"] or "",
            "leaf": leaf_of(r["title"] or ""),
            "tags": tags or [],
            "category": content.get("category", ""),
            "usage": content.get("usage", ""),
            "method_brief": (content.get("method") or "")[:200],
        })
    return pages


def load_pages_by_ids(ids: list[int]) -> list[dict]:
    """按 id 取知识页完整内容（正文生成注入用）。"""
    if not ids:
        return []
    marks = ",".join(str(int(i)) for i in ids)
    rows = query(f"SELECT id, title, tags, content FROM knowledge_page WHERE id IN ({marks})")
    out = []
    for r in rows:
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        out.append({
            "id": r["id"],
            "title": r["title"],
            "method": content.get("method", ""),
            "key_points": content.get("key_points", []),
            "tables": content.get("tables", []),
            "images": content.get("images", []),
            "usage": content.get("usage", ""),
            "estimate_words": content.get("estimate_words"),
            "ref_text": content.get("ref_text", ""),
        })
    return out


def _llm_judge(node_path: str, candidates: list[tuple[int, dict, float]]) -> list[dict]:
    """第三级：模糊候选交 LLM 终判。candidates 为 [(序号, page, score)]。"""
    if not candidates:
        return []
    lines = [
        f"{i}. 标题：{p['title']} | 用途：{p['usage'][:100]}"
        for i, (idx, p, _s) in enumerate(candidates)
    ]
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": f"待编写章节：{node_path}\n\n候选知识页：\n" + "\n".join(lines)},
            ],
            temperature=0.1,
        )
        s, e = raw.find("["), raw.rfind("]")
        picked = json.loads(raw[s : e + 1]) if s != -1 and e != -1 else []
        out = []
        for i in picked:
            if isinstance(i, int) and 0 <= i < len(candidates):
                out.append(candidates[i][1])
        return out
    except Exception:  # noqa: BLE001 LLM 终判失败时保守放行模糊候选
        return [p for _idx, p, _s in candidates]


def match_pages(
    node_title: str,
    node_path: list[str] | None = None,
    pages: list[dict] | None = None,
    top_k: int = TOP_K,
    llm_judge: bool = True,
) -> list[dict]:
    """为一个目录节点匹配知识页。node_path 为祖先标题链（不含自身）。

    返回命中的知识页摘要列表（含 score），按相关度降序。
    """
    if pages is None:
        pages = load_page_summaries()
    if not pages:
        return []

    node_path = node_path or []
    target = normalize_title(node_title)
    if not target:
        return []

    # 第一级：专业标签过滤（节点路径有专业语境才过滤）
    profs = _path_professions([*node_path, node_title])
    pool = [p for p in pages if profs & set(p["tags"])] if profs else pages
    if not pool:  # 标签打得稀疏时回退全池
        pool = pages

    # 第二级：标题相似度（叶子为主 + 路径编号级标题参与命中）
    # 知识页按全路径标题存储（父节 > 子节），只比叶子会把"叶是 ⑴布局/实施方案"这类
    # 子页全判 0——如"5.2 小临工程"下 15 张子页全漏，反被同章无关的"5.1 大临工程"命中。
    # 故路径中带编号的真章节段（5.2 / 5.2.2…，_NUM_HEAD 判定，排除 ⑴⑵ 伪叶）也参与计分，
    # 取该页所有编号段的最高 Dice；非编号段（无章节语义的纯描述词）不参与，防大范围误中。
    def _best_dice(p: dict) -> float:
        best = _dice(target, normalize_title(p["leaf"]))
        segs = str(p.get("title") or "").split(" > ")
        for seg in segs[:-1]:  # 非叶子的父/祖章节段
            if _NUM_HEAD.match(seg.strip()):
                d = _dice(target, normalize_title(seg))
                if d > best:
                    best = d
        return best

    scored: list[tuple[dict, float]] = []
    for p in pool:
        s = _best_dice(p)
        # 祖先链标题命中知识页路径：整条路径越像越相关
        for anc in node_path:
            if normalize_title(anc) and normalize_title(anc) in normalize_title(p["title"]):
                s = min(1.0, s + 0.15)
        if s >= SIM_LOW:
            scored.append((p, s))
    scored.sort(key=lambda x: x[1], reverse=True)

    hits = [p for p, s in scored if s >= SIM_HIGH][:top_k]
    ambiguous = [(i, p, s) for i, (p, s) in enumerate(scored) if SIM_LOW <= s < SIM_HIGH]

    # 第三级：模糊带 LLM 终判（补齐到 top_k）
    rest = top_k - len(hits)
    if ambiguous and rest > 0 and llm_judge:
        judged = _llm_judge(" > ".join([*node_path, node_title]), ambiguous[:8])
        hits.extend(judged[:rest])
    elif ambiguous and rest > 0:
        hits.extend(p for _i, p, _s in ambiguous[:rest])

    # 兜底（V1）：三级漏斗零命中时走向量检索补召回（方案 1.2"轻检索兜底"）
    if not hits:
        hits = _vector_fallback("page", [*node_path, node_title], top_k)
        if hits:
            return load_pages_by_ids([h["ref_id"] for h in hits])

    return hits[:top_k]


def match_for_toc(flat_toc: list[dict], llm_judge: bool = True) -> dict[str, list[dict]]:
    """批量匹配：展平的目录 [{title, level, parent?}] → {标题路径: [知识页]}。

    知识页摘要只读一次；键为全路径标题（父 > 子），与章节标识一致。
    """
    pages = load_page_summaries()
    result: dict[str, list[dict]] = {}
    for item in flat_toc:
        path = [item["parent"]] if item.get("parent") else []
        key = " > ".join([*path, item["title"]])
        result[key] = match_pages(item["title"], path, pages, llm_judge=llm_judge)
    return result


# ---------------------------------------------------------------- 工法条目匹配
# 与知识页同用三级漏斗，但匹配对象是工法名称（"钻孔灌注桩施工工艺"）：
# 第一级 applies_to 标签按节点路径专业词过滤；第二级标题 Dice；
# 第三级模糊候选复用 _llm_judge（用 apply_condition 当 usage 终判）。

METHOD_TOP_K = 3  # 每章最多注入工法条目数


def load_method_summaries() -> list[dict]:
    """读全部工法条目摘要（匹配用）。"""
    rows = query("SELECT id, name, applies_to, content FROM method_entry WHERE status != 'deprecated'")
    out = []
    for r in rows:
        applies = r.get("applies_to")
        if isinstance(applies, str):
            try:
                applies = json.loads(applies)
            except (TypeError, ValueError):
                applies = []
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        out.append({
            "id": r["id"],
            "name": r["name"] or "",
            "applies_to": applies or [],
            "apply_condition": content.get("apply_condition", ""),
            "process": content.get("process", []),
            "key_controls": content.get("key_controls", []),
        })
    return out


def load_methods_by_ids(ids: list[int]) -> list[dict]:
    """按 id 取工法条目完整内容（正文生成注入用）。"""
    if not ids:
        return []
    marks = ",".join(str(int(i)) for i in ids)
    rows = query(f"SELECT id, name, applies_to, content FROM method_entry WHERE id IN ({marks})")
    out = []
    for r in rows:
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        applies = r.get("applies_to")
        if isinstance(applies, str):
            try:
                applies = json.loads(applies)
            except (TypeError, ValueError):
                applies = []
        out.append({
            "id": r["id"],
            "name": r["name"],
            "applies_to": applies or [],
            "apply_condition": content.get("apply_condition", ""),
            "process": content.get("process", []),
            "key_controls": content.get("key_controls", []),
            "equipment": content.get("equipment", []),
            "quality_points": content.get("quality_points", []),
            "tables": content.get("tables", []),
        })
    return out


def match_methods(
    node_title: str,
    node_path: list[str] | None = None,
    entries: list[dict] | None = None,
    top_k: int = METHOD_TOP_K,
    llm_judge: bool = True,
) -> list[dict]:
    """为一个目录节点匹配工法条目（"施工方案"类章节用）。

    返回命中的工法摘要列表，按相关度降序；模糊带复用 _llm_judge 终判。
    """
    if entries is None:
        entries = load_method_summaries()
    if not entries:
        return []

    node_path = node_path or []
    target = normalize_title(node_title)
    if not target:
        return []

    # 第一级：节点路径专业词 ∩ 工法 applies_to 标签
    profs = _path_professions([*node_path, node_title])
    pool = [m for m in entries if profs & set(m["applies_to"])] if profs else entries
    if not pool:
        pool = entries

    # 第二级：工法名称与节点标题的 Dice 相似度；applies_to 命中路径加分
    joined_path = " ".join(node_path)
    scored: list[tuple[dict, float]] = []
    for m in pool:
        s = _dice(target, normalize_title(m["name"]))
        if any(t and t in joined_path for t in m["applies_to"]):
            s = min(1.0, s + 0.15)
        if s >= SIM_LOW:
            scored.append((m, s))
    scored.sort(key=lambda x: x[1], reverse=True)

    hits = [m for m, s in scored if s >= SIM_HIGH][:top_k]
    ambiguous = [(i, m, s) for i, (m, s) in enumerate(scored) if SIM_LOW <= s < SIM_HIGH]

    rest = top_k - len(hits)
    if ambiguous and rest > 0 and llm_judge:
        # 复用知识页终判：工法条目以 name 当 title、apply_condition 当 usage
        pseudo = [{**m, "title": m["name"], "usage": m["apply_condition"]} for _i, m, _s in ambiguous[:8]]
        judged = _llm_judge(" > ".join([*node_path, node_title]), [(i, p, 0) for i, p in enumerate(pseudo)])
        hits.extend(judged[:rest])
    elif ambiguous and rest > 0:
        hits.extend(m for _i, m, _s in ambiguous[:rest])

    # 兜底（V1）：漏斗零命中时走向量检索补召回
    if not hits:
        fb = _vector_fallback("method", [*node_path, node_title], top_k)
        if fb:
            return load_methods_by_ids([h["ref_id"] for h in fb])

    return hits[:top_k]


def _vector_fallback(ref_type: str, path_titles: list[str], top_k: int) -> list[dict]:
    """向量兜底：本项目已移除向量库，恒返回空（保留接口签名以兼容调用方）。"""
    return []
