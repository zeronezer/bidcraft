"""招标要求服务：tender_requirement 表读写（覆盖检查清单）。

- 招标文件解析后按 source_file 整体替换（重解析不产生重复条目）；
- 编排组装时注入目录生成（tender_requirements）；
- 质检智能体按此清单逐项对照正文覆盖度。
"""
import json

from app.core.database import execute, query


def replace_requirements(project_id: int, source_file: str, reqs: list[dict]) -> int:
    """按来源文件整体替换要求条目，返回写入条数。"""
    execute(
        "DELETE FROM tender_requirement WHERE project_id = %s AND source_file = %s",
        (project_id, source_file),
    )
    n = 0
    for r in reqs:
        execute(
            "INSERT INTO tender_requirement (project_id, category, title, content, weight,"
            " suggested_chapter, source_file, source_location, status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')",
            (
                project_id, r["category"], r["title"], r["content"], r.get("weight"),
                r.get("suggested_chapter", ""), source_file, r.get("source_location", ""),
            ),
        )
        n += 1
    return n


def _keep_for_lot(row: dict, lot_code: str | None) -> bool:
    """招标要求是否适用于当前标段：未判定/全线/背景/多标段 保留；明确属其他标段的剔除。

    各标段评分口径不同（如 5 标隧道方案 8 分、10 标桥梁 6 分），故注入前要按标段过滤。
    """
    if not lot_code:
        return True
    from app.services.lot_scope_service import parse_lots

    lots = parse_lots(row.get("applicable_lots"))
    if lots and not any(x in lots for x in ("all", "multi", "background", lot_code)):
        return False
    return True


def _lot_filter(rows: list[dict], project_id: int) -> list[dict]:
    from app.services.lot_service import selected_lot_code

    code = selected_lot_code(project_id)
    if not code:
        return rows
    return [r for r in rows if _keep_for_lot(r, code)]


def update_requirement(req_id: int, fields: dict) -> bool:
    """编辑招标要求（标题/内容/分值/类别/建议章节）。"""
    allowed = ("title", "content", "weight", "category", "suggested_chapter")
    sets = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not sets:
        return False
    if not query_one("SELECT id FROM tender_requirement WHERE id = %s", (req_id,)):
        return False
    execute(
        f"UPDATE tender_requirement SET {', '.join(f'{k} = %s' for k in sets)} WHERE id = %s",
        (*sets.values(), req_id),
    )
    return True


def delete_requirement(req_id: int) -> bool:
    """删除招标要求（AI 抽错或与本标段无关的条款，直接删掉）。"""
    if not query_one("SELECT id FROM tender_requirement WHERE id = %s", (req_id,)):
        return False
    execute("DELETE FROM tender_requirement WHERE id = %s", (req_id,))
    return True


def list_requirements(project_id: int, category: str = "") -> list[dict]:
    sql = (
        "SELECT id, category, title, content, weight, suggested_chapter,"
        " source_file, source_location, status, created_at, applicable_lots"
        " FROM tender_requirement WHERE project_id = %s"
    )
    params: list = [project_id]
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY category, id"
    rows = query(sql, tuple(params))
    rows = _lot_filter(rows, project_id)  # 按当前标段过滤（各标段评分口径不同）
    for r in rows:
        w = r.get("weight")
        r["weight"] = float(w) if w is not None else None
    return rows


def set_status(req_id: int, status: str) -> bool:
    """设置招标要求状态（confirmed/ignored/pending）。

    注意：不能拿 execute() 的返回值判断成败——UPDATE 的 lastrowid 为 0，
    会被 bool() 当失败（此前的"非法状态或要求不存在"报错就是这么来的）。
    """
    if status not in ("pending", "confirmed", "ignored"):
        return False
    if not query_one("SELECT id FROM tender_requirement WHERE id = %s", (req_id,)):
        return False
    execute("UPDATE tender_requirement SET status = %s WHERE id = %s", (status, req_id))
    return True


def confirm_all(project_id: int) -> int:
    """一键批量确认：项目全部待确认的招标要求置为已确认（ignored 保留）。"""
    from app.core.database import get_conn

    c = get_conn().cursor()
    try:
        c.execute(
            "UPDATE tender_requirement SET status = 'confirmed'"
            " WHERE project_id = %s AND status = 'pending'",
            (project_id,),
        )
        return c.rowcount
    finally:
        c.close()


# 正文/思路引用依据的类别（评分办法/技术要求/废标条款/投标资格属技术标红线与硬性约束，须作为编写依据）
# 商务报价/合同条款与施组正文无关，不纳入。
REQUIREMENT_WRITE_CATEGORIES = {"评分办法", "技术要求", "编制要求", "废标风险"}


def requirements_for_chapter(project_id: int, title: str, limit: int = 8) -> list[dict]:
    """按章取相关招标要求（评分/技术/编制/废标四类）：按 suggested_chapter 命中 + 类别过滤，
    无命中返回空；confirmed 优先，pending 其次。"""
    rows = query(
        "SELECT id, category, title, content, weight, suggested_chapter, source_location, status,"
        " applicable_lots"
        " FROM tender_requirement WHERE project_id = %s AND status != 'ignored'"
        " ORDER BY FIELD(status, 'confirmed', 'pending'), id",
        (project_id,),
    )
    rows = _lot_filter(rows, project_id)
    rows = [r for r in rows if r["category"] in REQUIREMENT_WRITE_CATEGORIES]
    t = (title or "").strip()
    hits = [r for r in rows if t and t in (r.get("suggested_chapter") or "")]
    if not hits:
        # 回退：按类别给该章相关（如"2.2 主要技术标准" → 技术要求）
        if "技术标准" in t or "技术要求" in t:
            hits = [r for r in rows if r["category"] == "技术要求"]
        elif "评分" in t or "编制" in t:
            hits = [r for r in rows if r["category"] in ("评分办法", "编制要求")]
        elif "废标" in t or "否决" in t or "无效" in t:
            hits = [r for r in rows if r["category"] == "废标风险"]
        else:
            hits = [r for r in rows if r["category"] == "评分办法"]
    return hits[:limit]


def requirements_for_chapter_text(project_id: int, title: str, limit: int = 8) -> str:
    """供正文/思路注入：按章相关招标要求转简洁文本。"""
    rows = requirements_for_chapter(project_id, title, limit=limit)
    if not rows:
        return ""
    lines = []
    for r in rows:
        w = f"（{r['weight']:g} 分）" if r.get("weight") is not None else ""
        ch = f" → 建议章节：{r['suggested_chapter']}" if r.get("suggested_chapter") else ""
        lines.append(f"- [{r['category']}]{w} {r['title']}{ch}\n  {r['content']}")
    return "\n".join(lines)


def requirements_text(project_id: int, limit: int = 30) -> str:
    """编排注入用文本：已确认优先、pending 其次，ignored 不入。每条含建议章节。"""
    rows = query(
        "SELECT category, title, weight, suggested_chapter, status, applicable_lots"
        " FROM tender_requirement"
        " WHERE project_id = %s AND status != 'ignored'"
        " ORDER BY FIELD(status, 'confirmed', 'pending'), category, id",
        (project_id,),
    )
    rows = _lot_filter(rows, project_id)[:limit]
    lines = []
    for r in rows:
        w = f"（{r['weight']:g} 分）" if r.get("weight") is not None else ""
        ch = f" → 建议章节：{r['suggested_chapter']}" if r.get("suggested_chapter") else ""
        lines.append(f"- [{r['category']}]{w} {r['title']}{ch}")
    return "\n".join(lines)
