"""招标要求提取（M5-1 重解析管线 / M5-6 质检前置）：招标文件 → 结构化要求 + 覆盖清单。

专项任务化提取：
不是一次调用抽所有要求，而是**多个专项任务各抽一类**，并发执行后合并去重——
每个任务有专属关键词窗口与提示词，覆盖面和准确率都远高于单次抽取：

1. 评分办法（技术评分项：名称/分值/标准/出处，含分值合计自检）；
2. 技术要求（施组应满足的技术标准、规范、参数、工期节点）；
3. 编制与格式要求（暗标规定、页数/字体/装订、目录构成、签字盖章等格式红线）；
4. 废标风险项（与施组编写相关的否决/无效投标条款）。

每条给出 suggested_chapter（建议落到施组哪个章节）→ 即"章节覆盖检查清单"的原料，
编排目录生成时注入（tender_requirements），质检智能体逐项对照打分。
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.services.llm import llm

MAX_INPUT = 60_000
MAX_TOKENS = 8192

SUGGESTED_CHAPTERS = ("编制依据与原则/工程概况/建设地区特征/施工组织安排/临时与过渡工程/"
                      "控制与重难点工程/施工方案/资源配置/管理措施/信息化与技术创新/施工组织图表")


def _build_input(markdown: str, keywords: str, head_chars: int = 30_000,
                 n_windows: int = 16, window: int = 2_500) -> str:
    """文档头部 + 关键词上下文窗口。

    窗口采样在全文**均匀分布**而非取前 N 个——招标文件里关键词前面的命中常是目录页
    （如"表4-1 xx评分表....104"），真正的评分表/条款在文档中后部，均匀采样才盖得住。
    """
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    head = text[:head_chars]
    matches = list(re.finditer(keywords, text))
    if len(matches) > n_windows:
        # 均匀采样 n_windows 个位置（保底含最后一个命中，通常是正文末尾的附表）
        step = len(matches) / n_windows
        picked = [matches[min(int(i * step), len(matches) - 1)] for i in range(n_windows)]
    else:
        picked = matches
    windows: list[str] = []
    for m in picked:
        s, e = max(0, m.start() - window // 3), m.start() + window
        seg = text[s:e]
        if seg.strip() and not any(seg[:200] == w[:200] for w in windows):
            windows.append(seg)
        if len(windows) >= n_windows:
            break
    body = head + "\n\n……（后文要求相关片段）……\n\n" + "\n---\n".join(windows)
    return body[:MAX_INPUT]


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"未返回 JSON: {text[:200]}")
    return json.loads(text[s : e + 1])


# ---------------------------------------------------------------- 专项任务定义

def _req_prompt(category_desc: str, focus: str) -> str:
    return f"""你是招标文件分析专家。从招标文件文本中专项抽取「{category_desc}」。

抽取范围与要点：
{focus}

通用要求：
1. 忠于原文，保留专业术语与关键句，不编造；宁多勿漏（这一步宁可多报，后续人工确认环节会筛）；
2. 每条给出 suggested_chapter：该要求建议落到施组的哪个章节
   （从：{SUGGESTED_CHAPTERS} 中选最贴切的）；
3. source_location 尽量给出（章节名/条款号/表名）；
4. 通用套话、与施组编写完全无关的条目（如报价软件要求、平台操作）不抽；
5. 只输出 JSON，不要解释、不要代码围栏。

输出格式：
{{"requirements": [
  {{"title": "要求/评分项名称", "weight": null或分值数字,
    "content": "评分标准或要求内容要点", "suggested_chapter": "施工方案",
    "source_location": "评标办法第3条"}}
]}}"""


TASKS = [
    {
        "key": "scoring",
        "category": "评分办法",
        "prompt": _req_prompt(
            "施工组织设计相关的评分办法（技术评分项）",
            "识别「评分办法/评标办法/评分标准/评审标准/技术评分/技术标评分」相关内容；"
            "逐项提取：评分项名称（保留原文专业术语）、weight=分值（纯数字，无明确分值给 null）、"
            "评分标准原文要点、出处。表格形式的评分表逐行拆成条目。无明确分值区间说明的按最接近分值记录。",
        ),
        "keywords": r"评分|评审|评标|分值|打分|附表",
        "head_chars": 30_000, "n_windows": 16, "window": 2_500,
    },
    {
        "key": "tech",
        "category": "技术要求",
        "prompt": _req_prompt(
            "技术要求与技术标准",
            "识别「技术要求/技术标准/技术规范/主要技术标准/工程特点/建设条件」相关内容；"
            "提取施组编写必须响应的技术性约束：设计时速/轨道类型等主要技术标准、"
            "工期节点要求、安全质量目标指标、重点工程与技术难点、验收标准等级等。",
        ),
        "keywords": r"技术标准|技术要求|主要技术|规范|验收标准|工期",
        "head_chars": 40_000, "n_windows": 14, "window": 2_500,
    },
    {
        "key": "format",
        "category": "编制要求",
        "prompt": _req_prompt(
            "施组编制与格式要求",
            "识别「编制要求/编制说明/投标文件格式/技术标格式/暗标/施工组织设计应包括」相关内容；"
            "提取对施组文件本身的硬性规定：应包含的章节内容、页数限制、字体字号版式、"
            "暗标要求（不得出现投标人名称等）、装订份数、图表要求、签字盖章要求等。",
        ),
        "keywords": r"编制要求|暗标|施工组织设计|投标文件格式|页数|字体|装订|盖章",
        "head_chars": 35_000, "n_windows": 14, "window": 2_500,
    },
    {
        "key": "reject",
        "category": "废标风险",
        "prompt": _req_prompt(
            "与施工组织设计编写相关的废标/否决风险项",
            "识别「否决投标/无效投标/废标/重大偏差/实质性响应/不予受理」相关条款中，"
            "与施组（技术标）编写有关的：如未按规定的施组目录编制、暗标出现标识、"
            "工期/质量目标未响应、缺少必备章节或图表等。每条说明风险点与规避要点。",
        ),
        "keywords": r"否决|无效|废标|偏差|实质性|拒绝",
        "head_chars": 25_000, "n_windows": 12, "window": 2_200,
    },
]


def _run_task(task: dict, markdown: str, doc_name: str) -> list[dict]:
    body = _build_input(markdown, task["keywords"],
                        head_chars=task["head_chars"], n_windows=task["n_windows"],
                        window=task["window"])
    raw = llm.chat(
        [
            {"role": "system", "content": task["prompt"]},
            {"role": "user", "content": f"文档：{doc_name}\n\n文本：\n{body}"},
        ],
        temperature=0.1,
        max_tokens=MAX_TOKENS,
    )
    data = _extract_json(raw)
    out = []
    for item in data.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        r = _sanitize(item, task["category"])
        if r:
            out.append(r)
    return out


def _sanitize(item: dict, category: str) -> dict | None:
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "").strip()
    if not title or not content:
        return None
    weight = item.get("weight")
    if isinstance(weight, str):
        weight = weight.strip()
        weight = float(weight) if re.fullmatch(r"\d+(\.\d+)?", weight) else None
    elif not isinstance(weight, (int, float)):
        weight = None
    return {
        "category": category,
        "title": title[:300],
        "content": content[:2000],
        "weight": weight,
        "suggested_chapter": str(item.get("suggested_chapter") or "")[:300],
        "source_location": str(item.get("source_location") or "")[:200],
    }


def _dedup(reqs: list[dict]) -> list[dict]:
    """合并去重：同 (category, title 相似) 保留内容更长的一条。"""
    seen: dict[tuple, dict] = {}
    for r in reqs:
        key = (r["category"], re.sub(r"[\s（）()：:、，,.。]", "", r["title"])[:40])
        old = seen.get(key)
        if not old or len(r["content"]) > len(old["content"]):
            seen[key] = r
    return list(seen.values())


def extract_requirements(markdown: str, doc_name: str = "") -> dict:
    """专项任务并发抽取招标要求，返回 {"requirements": [...], "check_note": str}。"""
    results: list[list[dict]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_run_task, t, markdown, doc_name): t for t in TASKS}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001 单任务失败不拖垮整体
                print(f"[招标要求] 任务 {t['key']} 失败: {e}")
    reqs = _dedup([r for lst in results for r in lst])

    # 归类规则：带分值的条目一律归「评分办法」（评分表的分项会被多个专项任务
    # 各自捕获，按分值统一归类才能保证"查评分办法"看到完整的评分表）
    for r in reqs:
        if r["weight"] is not None:
            r["category"] = "评分办法"

    # 自检：评分条目分值合计
    score_items = [r for r in reqs if r["category"] == "评分办法" and r["weight"] is not None]
    check_note = ""
    if score_items:
        total = sum(r["weight"] for r in score_items)
        check_note = f"程序核对：评分条目 {len(score_items)} 项，分值合计 {total:g} 分（请与招标文件技术标总分核对）"

    return {"requirements": reqs, "check_note": check_note}


# ---------------------------------------------------------------- 招标文件施组目录

TOC_KW = re.compile(r"施工组织设计|投标文件格式|技术标.*目录|施组")
TOC_HEAD_CHARS = 30_000

TOC_SYSTEM = """你是招标文件分析专家。招标文件通常在「投标文件格式」或「评标办法」中规定
投标人编制的施工组织设计应包含的目录/章节组成。

请从文本中找出该规定目录，原样提取为章→节两级结构。

规则：
1. 优先取招标文件明确规定的施组目录（格式红线，评标依据）；找不到规定目录时，
   若文本中出现"施工组织设计"的示例目录/提纲，也可提取并在 note 中说明是示例；
2. 保持原文章节名称与顺序，不要自行增删；
3. 只有一级章的，children 给空数组；
4. 确实找不到任何施组目录线索 → found=false；
5. 只输出 JSON，不要解释、不要代码围栏。

输出格式：
{"found": true, "note": "来源说明（如：取自'第八章 投标文件格式'）",
 "chapters": [{"title": "第一章 编制依据", "children": [{"title": "第一节 xxx"}]}]}"""


def extract_tender_toc(markdown: str, doc_name: str = "") -> dict:
    """从招标文件提取规定的施组目录（v0.2：目录生成优先取招标文件）。

    返回 {"found": bool, "note": str, "chapters": [...]}。
    """
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    head = text[:TOC_HEAD_CHARS]
    windows: list[str] = []
    for m in list(TOC_KW.finditer(text))[:30]:
        s, e = max(0, m.start() - 400), m.start() + 2400
        seg = text[s:e]
        if not any(seg[:200] == w[:200] for w in windows):
            windows.append(seg)
        if len(windows) >= 12:
            break
    body = (head + "\n\n……\n\n" + "\n---\n".join(windows))[:MAX_INPUT]

    raw = llm.chat(
        [
            {"role": "system", "content": TOC_SYSTEM},
            {"role": "user", "content": f"文档：{doc_name}\n\n文本：\n{body}"},
        ],
        temperature=0.1,
        max_tokens=8192,
    )
    data = _extract_json(raw)

    chapters = []
    for ch in data.get("chapters") or []:
        if not isinstance(ch, dict) or not str(ch.get("title") or "").strip():
            continue
        children = [
            {"title": str(c["title"]).strip()}
            for c in (ch.get("children") or [])
            if isinstance(c, dict) and str(c.get("title") or "").strip()
        ]
        chapters.append({"title": str(ch["title"]).strip(), "children": children})
    found = bool(data.get("found")) and bool(chapters)
    return {"found": found, "note": str(data.get("note") or ""), "chapters": chapters if found else []}
