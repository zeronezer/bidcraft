"""经验库 API：对话/编写过程沉淀的经验条目。对应表 experience_item。"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import execute, query, query_one

router = APIRouter(prefix="/api/experience", tags=["experience"])

VALID_TYPES = ("编写经验", "表述偏好", "纠偏规则")


class ExperienceUpdate(BaseModel):
    exp_type: str | None = Field(None, max_length=20)
    chapter_type: str | None = Field(None, max_length=200)
    tags: list[str] | None = None
    content: str | None = Field(None, max_length=2000)


def _item_to_dict(row: dict) -> dict:
    tags = row.get("tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (TypeError, ValueError):
            tags = []
    source = row.get("source")
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except (TypeError, ValueError):
            source = {}
    return {
        "exp_id": f"exp-{row['id']}",
        "id": row["id"],
        "exp_type": row["exp_type"],
        "chapter_type": row["chapter_type"],
        "tags": tags or [],
        "content": row["content"],
        "source": source or {},
        "status": row["status"],
    }


@router.get("")
def list_experience(status: str = "", keyword: str = ""):
    """经验条目列表。status: 空=全部 | pending | confirmed"""
    sql = "SELECT * FROM experience_item"
    where, params = [], []
    if status:
        where.append("status = %s")
        params.append(status)
    if keyword:
        where.append("(content LIKE %s OR chapter_type LIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    items = [_item_to_dict(r) for r in query(sql, tuple(params))]

    # 补项目名称：来源只写"项目 #3"没意义，写项目名才知道这条经验是从哪个工程沉淀的。
    # 一次性查完（不用逐条查，避免 N+1）。
    pids = {i["source"].get("project_id") for i in items if i["source"].get("project_id")}
    names: dict = {}
    if pids:
        marks = ",".join(str(int(p)) for p in pids)
        for r in query(f"SELECT id, name FROM project WHERE id IN ({marks})"):
            names[r["id"]] = r["name"]
    for i in items:
        pid = (i.get("source") or {}).get("project_id")
        i["source"]["project_name"] = names.get(pid, "") if pid else ""
    return items


@router.post("/{item_id}/confirm")
def confirm(item_id: int):
    if not query_one("SELECT id FROM experience_item WHERE id = %s", (item_id,)):
        raise HTTPException(404, "经验条目不存在")
    execute("UPDATE experience_item SET status = 'confirmed' WHERE id = %s", (item_id,))
    return {"ok": True}


@router.put("/{item_id}")
def edit_experience(item_id: int, body: ExperienceUpdate):
    """编辑经验条目：标签/内容/类型/适用章节均可改，改完即已确认状态。"""
    if not query_one("SELECT id FROM experience_item WHERE id = %s", (item_id,)):
        raise HTTPException(404, "经验条目不存在")
    sets, params = [], []
    if body.exp_type is not None:
        if body.exp_type not in VALID_TYPES:
            raise HTTPException(400, f"exp_type 取值必须为 {'/'.join(VALID_TYPES)}")
        sets.append("exp_type = %s")
        params.append(body.exp_type)
    if body.chapter_type is not None:
        sets.append("chapter_type = %s")
        params.append((body.chapter_type or "").strip()[:200])
    if body.content is not None:
        if not (body.content or "").strip():
            raise HTTPException(400, "内容不能为空")
        sets.append("content = %s")
        params.append(body.content.strip())
    if body.tags is not None:
        sets.append("tags = %s")
        params.append(json.dumps(body.tags, ensure_ascii=False))
    if not sets:
        return {"ok": True}
    sets.append("status = 'confirmed'")
    params.extend([item_id])
    execute(f"UPDATE experience_item SET {', '.join(sets)} WHERE id = %s", tuple(params))
    return {"ok": True}


@router.delete("/{item_id}")
def remove(item_id: int):
    if not query_one("SELECT id FROM experience_item WHERE id = %s", (item_id,)):
        raise HTTPException(404, "经验条目不存在")
    execute("DELETE FROM experience_item WHERE id = %s", (item_id,))
    return {"ok": True}
