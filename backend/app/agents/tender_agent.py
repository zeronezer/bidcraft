"""招标文件整体抽取（2026-09-11 用户拍板）。

**一次 LLM 调用**抽取三类信息：标段划分 + 招标要求 + 施组目录。

背景：此前解析招标文件要串行跑 4~5 次 LLM——标段识别 1 次、招标要求 4 个专项任务
（并发 2，跑 2 轮）、施组目录 1 次。这些调用全部落在进度 20%~90% 的**无进度上报空洞**里，
界面从头到尾只显示"准备建索引并逐章抽取全局参数"，看起来像卡死；实测这段要 1~2 分钟，
且其中大头是招标要求那 4 个任务各自 8192 的输出。

用户拍板：**招标文件整体交给 LLM 抽一次即可**——1M 上下文 + prompt 缓存，又快又便宜。
输入也一并改成"整份文件"（原先是头部 + 关键词窗口采样），不再靠关键词猜信息在哪。

保留 `lot_agent` / `requirement_agent` 的专项函数：对话内按需重抽某类、以及
"重新识别标段划分"（`lot_service.extract_lots_task`）仍走它们。
"""
import json
import re

from app.services.llm import llm

MAX_INPUT = 400_000    # 招标文件整体上限（1M 上下文，给输出留足余量）
MAX_TOKENS = 24_000    # 三类信息合计输出上限（原三段各自 8192）

EXTRACT_SYSTEM = """你是招标文件分析专家。请**一次性**从这份招标文件中抽取三类信息。

=== 一、标段划分（lots）===
找"标段划分/标段设置/标包划分/合同段划分"等条款：
- 整个项目只有一个合同段、未做划分 → has_lots=false，lots=[]
- 有划分 → 逐个标段抽取：
  · lot_code：标段编号（如 CGJXZQ-3、HSZQ-4、SG-1），无编号时用标段名
  · lot_name：标段名称
  · price_limit：最高投标限价（万元，纯数字，未知给 null）
  · scope：标段范围概述（里程 / 主要工程内容，100 字内）

=== 二、招标要求（requirements）===
抽取四类，每类都要尽力找全（宁多勿漏，后续有筛选环节）：
1. category="评分办法"：施工组织设计相关的技术评分项（评分项名称、分值、评分标准要点；
   表格形式的评分表逐行拆成条目）
2. category="技术要求"：技术标准、主要技术参数、验收标准等级、工期要求
3. category="编制要求"：施组编制格式要求（暗标、页数、字体、装订、盖章、章节要求）
4. category="废标风险"：否决投标/无效标/废标条款、实质性偏差条款
每条字段：title（名称）、content（内容要点，保留专业术语与关键数字，不编造）、
weight（分值，纯数字，无则 null）、suggested_chapter（建议落到施组的哪个章节，
从：%s 中选最贴切的）、source_location（章节名/条款号/表名）。
**不抽**：报价软件要求、平台操作、与施组编写无关的程序性条款。

=== 三、施组目录（toc）===
招标文件通常在「投标文件格式」或「评标办法」中规定施组应包含的目录：
- 原样提取为章→节两级结构，保持原文章节名称与顺序，不要自行增删
- 优先取"招标文件明确规定的施组目录"；找不到规定目录时，若文中出现示例目录/提纲，
  也可提取，并在 note 中说明是示例
- 只有一级章的，children 给空数组
- 确实找不到任何施组目录线索 → found=false

只输出 JSON，不要任何解释文字与代码围栏：
{"has_lots": true,
 "lots": [{"lot_code": "", "lot_name": "", "price_limit": null, "scope": ""}],
 "requirements": [{"category": "", "title": "", "content": "", "weight": null,
                   "suggested_chapter": "", "source_location": ""}],
 "toc": {"found": true, "note": "", "chapters": [{"title": "", "children": [{"title": ""}]}]}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"未返回 JSON: {text[:200]}")
    return json.loads(text[s : e + 1])


def _clean_lots(items) -> list[dict]:
    """标段的规则校验：编号去重、空壳剔除（与 lot_agent 同一套规则）。"""
    lots, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("lot_code") or "").strip()
        name = str(item.get("lot_name") or "").strip()
        if not code and not name:
            continue
        key = code or name
        if key in seen:
            continue
        seen.add(key)
        price = item.get("price_limit")
        try:
            price = float(price) if price not in (None, "", "null") else None
        except (TypeError, ValueError):
            price = None
        lots.append({
            "lot_code": code or name,
            "lot_name": name,
            "price_limit": price,
            "scope": str(item.get("scope") or "")[:500],
        })
    return lots


def _clean_requirements(items) -> list[dict]:
    """招标要求规整 + 去重（复用 requirement_agent 的 _sanitize/_dedup，保证与旧路径同构）。"""
    from app.agents.requirement_agent import TASKS, _dedup, _sanitize

    valid = {t["category"] for t in TASKS}   # 评分办法/技术要求/编制要求/废标风险
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").strip()
        if cat not in valid:
            continue          # 类别不认识的一律丢弃（避免脏数据进覆盖检查清单）
        r = _sanitize(item, cat)
        if r:
            out.append(r)
    return _dedup(out)


def _clean_toc(data) -> dict:
    """施组目录规整（与 requirement_agent.extract_tender_toc 的产出同构）。"""
    if not isinstance(data, dict):
        return {"found": False, "note": "", "chapters": []}
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
    return {
        "found": found,
        "note": str(data.get("note") or "")[:200],
        "chapters": chapters if found else [],
    }


def extract_tender_all(markdown: str, doc_name: str = "") -> dict:
    """一次调用抽取招标文件的三类信息。

    返回 {"has_lots": bool, "lots": [...], "requirements": [...], "toc": {...}}。
    输入是**整份文件**（不做关键词窗口采样）——信息可能出现在任何位置，让模型自己找。
    """
    from app.agents.requirement_agent import SUGGESTED_CHAPTERS

    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown or "")   # 去图片引用噪音
    body = text[:MAX_INPUT]
    print(f"[招标抽取] 输入 {len(body)} 字（整份文件）")
    raw = llm.chat(
        [
            {"role": "system", "content": EXTRACT_SYSTEM % SUGGESTED_CHAPTERS},
            {"role": "user", "content": f"文档：{doc_name}\n\n【招标文件原文】\n{body}"},
        ],
        temperature=0.1,
        max_tokens=MAX_TOKENS,
    )
    data = _extract_json(raw)

    lots = _clean_lots(data.get("lots"))
    reqs = _clean_requirements(data.get("requirements"))
    toc = _clean_toc(data.get("toc"))
    has_lots = bool(data.get("has_lots")) and bool(lots)
    print(f"[招标抽取] 标段 {len(lots)} 个、招标要求 {len(reqs)} 条、"
          f"施组目录 {'有' if toc['found'] else '无'}")
    return {"has_lots": has_lots, "lots": lots if has_lots else [],
            "requirements": reqs, "toc": toc}
