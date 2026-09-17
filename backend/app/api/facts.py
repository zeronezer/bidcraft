"""全局事实表 API：项目级硬事实的查询与人工确认。对应表 global_fact。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import execute, query, query_one

router = APIRouter(prefix="/api/facts", tags=["facts"])


def _row_to_dict(row: dict) -> dict:
    conf = row.get("confidence")
    if conf is not None:
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 1.0
    return {
        "id": row["id"],
        "category": row.get("category", ""),
        "fact_key": row["fact_key"],
        "fact_value": row["fact_value"],
        "unit": row.get("unit", ""),
        "source_location": row.get("source_location", "") or "",
        "confidence": conf,
        "applicable_lots": row.get("applicable_lots"),
        "status": row["status"],
    }


@router.get("/project/{project_id}")
def list_facts(project_id: int, category: str = ""):
    """项目全局事实（已按当前标段过滤）。

    只返回**与本标段相关**的：本标段专属 / 全线共性(all) / 全线背景(background) / 多标段(multi)
    / 未判定——**明确属于其他标段的不返回**（那些与本次编制无关，展示出来只会造成误解）。

    只返回 `status != 'expired'`：失效行是被新版本取代的旧值（答疑补遗重抽、按标段重提取），
    展示出来会新旧混淆。此前漏了这个条件，"忽略"一条事实（置 expired）后它仍留在列表里，
    等于忽略无效——也导致按标段重提取清理掉的错误归属仍显示在界面上。
    """
    from app.services.lot_scope_service import parse_lots
    from app.services.lot_service import selected_lot_code

    sql = "SELECT * FROM global_fact WHERE project_id = %s AND status != 'expired'"
    params: list = [project_id]
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY id DESC"
    rows = query(sql, tuple(params))
    lot_code = selected_lot_code(project_id)
    out = []
    for r in rows:
        if lot_code:
            lots = parse_lots(r.get("applicable_lots"))
            if lots and not any(x in lots for x in ("all", "background", "multi", lot_code)):
                continue  # 明确属于其他标段 → 不展示
        d = _row_to_dict(r)
        lots = parse_lots(r.get("applicable_lots"))
        if lots and lot_code and lot_code in lots:
            d["scope"] = "mine"
        elif lots and ("background" in lots or "multi" in lots):
            d["scope"] = "background"
        elif lots and "all" in lots:
            d["scope"] = "all"
        else:
            d["scope"] = "unknown"
        out.append(d)
    return out


@router.post("/project/{project_id}/reextract")
def reextract_facts(project_id: int):
    """按当前选定标段**重新提取**全局参数（后台任务，前端轮询 task 进度）。

    与「标段档案 → 重新抽取」的区别：那个是"建档 + 重提取"两步；这个**只重提取事实**、
    不动档案——档案已经建好、只想把全局参数按标段重刷一遍时用它。

    前置校验：未选定标段直接 400。否则任务必然抛 RuntimeError，而 task.detail 里带的是
    整段 traceback，会原样冒到界面上（同 /lot-profile/rebuild 踩过的坑）。
    """
    from app.services.fact_service import start_reextract_facts
    from app.services.lot_service import selected_lot_code

    if not selected_lot_code(project_id):
        raise HTTPException(400, "尚未选定标段，无法按标段重新提取")
    return {"ok": True, "task_id": start_reextract_facts(project_id)}


@router.post("/{fact_id}/confirm")
def confirm(fact_id: int):
    if not query_one("SELECT id FROM global_fact WHERE id = %s", (fact_id,)):
        raise HTTPException(404, "事实不存在")
    execute("UPDATE global_fact SET status = 'confirmed' WHERE id = %s", (fact_id,))
    return {"ok": True}


class BatchConfirmBody(BaseModel):
    project_id: int


@router.post("/confirm_all")
def confirm_all(body: BatchConfirmBody):
    """一键批量确认：项目全部待确认事实置为已确认（低置信度条目建议先人工核对）。"""
    from app.core.database import get_conn

    c = get_conn().cursor()
    try:
        c.execute(
            "UPDATE global_fact SET status = 'confirmed'"
            " WHERE project_id = %s AND status = 'pending'",
            (body.project_id,),
        )
        n = c.rowcount
    finally:
        c.close()
    return {"ok": True, "confirmed": n}


@router.patch("/{fact_id}")
def update(fact_id: int, body: dict):
    if not query_one("SELECT id FROM global_fact WHERE id = %s", (fact_id,)):
        raise HTTPException(404, "事实不存在")
    # edited=1：标记人工改过。「按标段重提取全局参数」会整批刷新 AI 事实，
    # 但人工改过的一条都不动（用户拍板：重提取保留人工修正）。
    execute(
        "UPDATE global_fact SET fact_value = %s, unit = %s, status = 'confirmed', edited = 1"
        " WHERE id = %s",
        (body.get("fact_value", ""), body.get("unit", ""), fact_id),
    )
    return {"ok": True}


@router.delete("/{fact_id}")
def delete_fact(fact_id: int):
    """删除事实（AI 抽错或与本项目无关的条目，直接删掉）。"""
    if not query_one("SELECT id FROM global_fact WHERE id = %s", (fact_id,)):
        raise HTTPException(404, "事实不存在")
    execute("DELETE FROM global_fact WHERE id = %s", (fact_id,))
    return {"ok": True}


@router.post("/{fact_id}/ignore")
def ignore(fact_id: int):
    if not query_one("SELECT id FROM global_fact WHERE id = %s", (fact_id,)):
        raise HTTPException(404, "事实不存在")
    execute("UPDATE global_fact SET status = 'expired' WHERE id = %s", (fact_id,))
    return {"ok": True}
