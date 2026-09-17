"""标段识别（M1-2 / M5-1）：招标文件 → 标段划分抽取。

三种结果（方案 M1-2）：
- 多标段：lots 多条 → 用户选择一个进入；
- 单标段：lots 恰好一条 → 展示确认后进入；
- 无标段划分：has_lots=false / lots 空 → 跳过选择直接进入。

实现：LLM 抽取 + 规则校验（编号去重、空壳剔除）。标段划分条款通常在
招标公告/投标人须知前附表（文档前部），故取"头部 + '标段'关键词窗口"喂模型。
"""
import json
import re

from app.services.llm import llm

HEAD_CHARS = 20_000      # 文档头部（招标公告/须知前附表多在前部）
KW_WINDOW = 1_500        # "标段"关键词上下文窗口
KW_MAX_WINDOWS = 10      # 最多取多少个关键词窗口
MAX_INPUT = 40_000       # 喂给 LLM 的总上限

SYSTEM_PROMPT = """你是招标文件分析专家。从招标文件文本中识别标段（标包）划分情况。

规则：
1. 找"标段划分/标段设置/标包划分/合同段划分"等条款；
2. 整个项目只有一个合同段、未做划分 → has_lots=false, lots=[]；
3. 有划分 → 逐个标段抽取：
   - lot_code：标段编号（如 CGJXZQ-3、SG-1），无编号用标段名；
   - lot_name：标段名称；
   - price_limit：最高投标限价（万元，纯数字，未知给 null）；
   - scope：标段范围概述（里程/主要工程内容，100 字内）；
4. 只输出 JSON，不要解释性文字、不要代码围栏。

输出格式：
{"has_lots": true, "lots": [{"lot_code": "CGJXZQ-3", "lot_name": "站前工程3标", "price_limit": null, "scope": "..."}]}"""


def _build_input(markdown: str) -> str:
    """头部 + '标段'关键词窗口拼接，控制在 MAX_INPUT 内。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)  # 去图片引用噪音
    head = text[:HEAD_CHARS]
    windows: list[str] = []
    for m in list(re.finditer(r"标段", text))[: KW_MAX_WINDOWS * 3]:
        s, e = max(0, m.start() - KW_WINDOW // 2), m.start() + KW_WINDOW
        seg = text[s:e]
        if seg.strip() and seg not in windows:
            windows.append(seg)
        if len(windows) >= KW_MAX_WINDOWS:
            break
    body = head + "\n\n……（后文标段相关片段）……\n\n" + "\n---\n".join(windows)
    return body[:MAX_INPUT]


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"未返回 JSON: {text[:200]}")
    return json.loads(text[s : e + 1])


def extract_lots(markdown: str, doc_name: str = "") -> dict:
    """抽取标段划分，返回 {"has_lots": bool, "lots": [...]}（已规则校验）。"""
    raw = llm.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"文档：{doc_name}\n\n文本：\n{_build_input(markdown)}"},
        ],
        temperature=0.1,
    )
    data = _extract_json(raw)

    # 规则校验：编号去重、空壳剔除
    lots, seen = [], set()
    for item in data.get("lots") or []:
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

    has_lots = bool(data.get("has_lots")) and bool(lots)
    return {"has_lots": has_lots, "lots": lots if has_lots else []}
