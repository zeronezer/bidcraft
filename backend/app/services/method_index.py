"""工艺工法概况索引（2026-09-04 重构，用户决策：一工法一个文档，不再提炼结构化条目）。

思路：与项目文件章节索引同模式——
1. 解析完成后，对工法**整篇全文**提取一条「概况 + 适用范围」存 method_index（一工法一条）；
2. 编写正文章节时，先把各工法概况交给 AI 选"需要哪个工法" → 再取该工法**整篇文档**
   （含文字/工艺流程/表格/图片，图片改写为可访问 URL）注入正文上下文。

1M 上下文下整篇工法文档轻松装下，写作拿到的是完整工艺而非被拆碎的条目。
"""
import json
import re
from pathlib import Path

from app.core.database import execute, query, query_one
from app.services.llm import llm

MAX_SELECT = 10         # 单章最多注入工法数
MAX_TOTAL_CHARS = 400_000  # 注入工法整文总字符上限

OVERVIEW_SYSTEM = """你是施工工艺技术专家。下面给出一篇工艺工法文档的**完整原文**，请生成该工法的索引条目
（供后续 AI 判断"编写哪个施工方案子节时应参考此工法"，描述越具体判断越准）：
- name：工法规范名称（如"钻孔灌注桩施工工艺"、"CRTSⅢ型无砟轨道板铺设施工工法"），简短；
- summary（50~200 字）：该工法做什么、适用什么结构/部位、核心工艺流程与关键要点，
  逐项点明（含涉及的主要工序、关键控制、配套设备/表）；
- applies_to（50~200 字）：适用范围——编写施组哪些章节/问题时应当参考本工法，
  以及典型触发场景（如"编写桥梁钻孔桩基础施工方案、桩基质量保证措施时参考"）。
忠于原文，概括具体信息而不是空泛描述。
只输出 JSON：{"name": "...", "summary": "...", "applies_to": "..."}，不要解释、不要代码围栏。"""

SELECT_SYSTEM = """你是施工组织设计编制专家。给定「编写需求」和一份工艺工法清单（名称+概况+适用范围），
选出编写时需要参考的**工法**。选中的工法会以整篇原文提供。

规则：
1. 只按概况与适用范围判断，宁多勿漏（相关就选，上下文足够大），明显无关不选；
2. 最多输出 {max_select} 个序号；没有相关返回空数组。
只输出 JSON：{{"ids": [1, 3]}}（ids 为清单第一列序号），不要解释。"""


def build(doc_id: int, parsed_path: str) -> dict:
    """为一个工法文档提取概况并写入 method_index（一工法一条，upsert）。返回写入记录。"""
    md_path = Path(parsed_path) / "full.md"
    if not md_path.exists():
        raise FileNotFoundError(md_path)
    full = md_path.read_text(encoding="utf-8")
    raw = llm.chat(
        [
            {"role": "system", "content": OVERVIEW_SYSTEM},
            {"role": "user", "content": f"工法文档全文：\n{full}"},
        ],
        temperature=0.1,
        max_tokens=2000,
    )
    s, e = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[s : e + 1]) if s != -1 and e != -1 else {}
    name = str(data.get("name") or "").strip()
    if not name:
        row = query_one("SELECT file_name FROM knowledge_doc WHERE id = %s", (doc_id,))
        name = (row or {}).get("file_name", f"工法{doc_id}").rsplit(".", 1)[0]
    execute(
        "DELETE FROM method_index WHERE doc_id = %s", (doc_id,),
    )
    execute(
        "INSERT INTO method_index (doc_id, name, summary, applies_to) VALUES (%s, %s, %s, %s)",
        (doc_id, name[:300], str(data.get("summary") or "")[:600], str(data.get("applies_to") or "")[:400]),
    )
    return {"doc_id": doc_id, "name": name}


def catalog() -> list[dict]:
    """全部已解析工法文档的概况清单（联文档名）。"""
    return query(
        "SELECT mi.doc_id, mi.name, mi.summary, mi.applies_to, kd.file_name, kd.parsed_path"
        " FROM method_index mi JOIN knowledge_doc kd ON kd.id = mi.doc_id"
        " WHERE kd.doc_category = 'method' AND kd.status = 'parsed' AND kd.parsed_path IS NOT NULL"
        " ORDER BY mi.id",
    )


def select(catalog: list[dict], need: str, max_select: int = MAX_SELECT) -> list[int]:
    """AI 按编写需求从工法概况中选取需要的工法 doc_id（稳定序号防编造 id）。"""
    if not catalog or not need:
        return []
    seq_map: dict[int, int] = {}
    lines = []
    for k, c in enumerate(catalog, 1):
        seq_map[k] = c["doc_id"]
        lines.append(f"{k} | {c['name'][:40]} | 概况:{c['summary'][:200]} | 适用:{c['applies_to'][:120]}")
    raw = llm.chat(
        [
            {"role": "system", "content": SELECT_SYSTEM.replace("{max_select}", str(max_select))},
            {"role": "user", "content": f"【编写需求】\n{need[:1500]}\n\n【工法清单】\n" + "\n".join(lines)},
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


def load(catalog: list[dict], doc_ids: list[int]) -> list[dict]:
    """按工法取**整篇文档**原文（图片引用改写为可访问 URL）。"""
    by_id = {c["doc_id"]: c for c in catalog}
    out = []
    total = 0
    for did in doc_ids:
        c = by_id.get(did)
        if not c or not c.get("parsed_path"):
            continue
        md_path = Path(c["parsed_path"]) / "full.md"
        if not md_path.exists():
            continue
        text = md_path.read_text(encoding="utf-8")
        # 图片引用改写为 /uploads URL
        from app.services.file_index import _rewrite_image_refs

        text = _rewrite_image_refs(text, md_path.parent)
        if total + len(text) > MAX_TOTAL_CHARS:
            continue
        total += len(text)
        out.append({"name": c["name"], "file": c["file_name"], "path": c["name"], "text": text})
    return out


def match_methods(need: str) -> list[dict]:
    """写正文用：按本章需求选工法并取整文。fail-safe 返回空。"""
    try:
        cat = catalog()
        if not cat:
            return []
        ids = select(cat, need)
        return load(cat, ids)
    except Exception:  # noqa: BLE001
        return []
