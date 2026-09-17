"""项目管理 API。"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import execute, query, query_one

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    project_type: str = ""  # 铁路/公路/房建/市政…


@router.get("")
def list_projects(keyword: str = ""):
    sql = "SELECT * FROM project"
    params: tuple = ()
    if keyword:
        sql += " WHERE name LIKE %s"
        params = (f"%{keyword}%",)
    sql += " ORDER BY updated_at DESC"
    projects = query(sql, params)
    # 附带标段摘要（卡片展示：标段代码列表 + 当前选定）
    if projects:
        ids = [p["id"] for p in projects]
        marks = ",".join(str(int(i)) for i in ids)
        lot_rows = query(
            f"SELECT project_id, lot_code, lot_name, selected FROM lot WHERE project_id IN ({marks}) ORDER BY id"
        )
        by_proj: dict = {}
        for l in lot_rows:
            by_proj.setdefault(l["project_id"], []).append(l)
        for p in projects:
            lots = by_proj.get(p["id"]) or []
            p["lots"] = [
                {"lot_code": l["lot_code"], "lot_name": l["lot_name"], "selected": bool(l["selected"])}
                for l in lots
            ]
    return projects


@router.post("")
def create_project(body: ProjectCreate):
    now = datetime.now()
    pid = execute(
        "INSERT INTO project (name, description, project_type, status, created_at, updated_at)"
        " VALUES (%s, %s, %s, 'active', %s, %s)",
        (body.name, body.description, body.project_type, now, now),
    )
    return {"id": pid}


@router.get("/{project_id}")
def get_project(project_id: int):
    item = query_one("SELECT * FROM project WHERE id = %s", (project_id,))
    if not item:
        raise HTTPException(404, "项目不存在")
    return item


@router.patch("/{project_id}")
def update_project(project_id: int, body: ProjectCreate):
    execute(
        "UPDATE project SET name = %s, description = %s, project_type = %s,"
        " updated_at = %s WHERE id = %s",
        (body.name, body.description, body.project_type, datetime.now(), project_id),
    )
    return {"ok": True}


@router.delete("/{project_id}")
def delete_project(project_id: int):
    execute("DELETE FROM project WHERE id = %s", (project_id,))
    return {"ok": True}


# ---------------------------------------------------------------- 标段（M1-2）

class LotSelect(BaseModel):
    lot_id: int | None = None  # None = 无标段划分，直接进入


@router.get("/{project_id}/lots")
def get_lots(project_id: int):
    """项目标段列表 + 当前选中。三种形态：多条→选一个 / 一条→确认 / 空→直接进入。"""
    from app.services.lot_service import list_lots

    return list_lots(project_id)


@router.post("/{project_id}/lots/select")
def select(project_id: int, body: LotSelect):
    """选定标段（多选一/单标段确认），lot_id 为空表示无标段直接进入。"""
    from app.services.lot_service import select_lot

    return select_lot(project_id, body.lot_id)


class LotExtractBody(BaseModel):
    overwrite: bool = False  # 已有标段时须置 true（前端二次确认后重传）


@router.post("/{project_id}/lots/extract")
def extract_lots(project_id: int, body: LotExtractBody | None = None):
    """从已解析的招标文件重新识别标段划分（后台任务，前端轮询 task 进度）。

    识别吃的是**已解析完成的招标文件原文**，所以未解析完直接拒绝——否则任务必然抛错，
    而 task.detail 里带的是整段 traceback，会原样冒到界面上（同 /lot-profile/rebuild 的教训）。

    已有标段时必须显式 overwrite：重建是**破坏性**的（replace_lots 清空 lot 表，而标段档案
    就存在 lot.profile 里 → 连带清掉，需重新抽取），故要求前端二次确认后重传。
    """
    from app.services.lot_service import has_parsed_tender, list_lots, start_extract_lots

    if not has_parsed_tender(project_id):
        raise HTTPException(400, "尚无解析完成的招标文件，请先上传招标文件并等待解析完成")
    cur = list_lots(project_id)
    if cur["lots"] and not (body and body.overwrite):
        return {
            "ok": False,
            "need_confirm": True,
            "message": (f"本项目已有 {len(cur['lots'])} 个标段。重新识别会清空现有标段，"
                        f"并连带清除已建立的标段档案（需重新抽取）。确定继续？"),
        }
    return {"ok": True, "task_id": start_extract_lots(project_id)}


@router.get("/{project_id}/lots/status")
def lots_status(project_id: int):
    """标段识别总览：供工作台弹窗区分四种空态。

    "没有标段"有四种互不相干的原因——**还没传招标文件 / 正在解析 / 识别任务在跑 /
    确实未划分标段**，界面必须分开说，否则用户看到"未划分标段"会以为没救，
    而正确动作可能是"去传文件"或"等解析完"。
    """
    from app.services.lot_service import list_lots

    files = query(
        "SELECT pf.id, pf.file_name, pf.status,"
        " t.id AS task_id, t.progress AS task_progress, t.detail AS task_detail"
        " FROM project_file pf"
        " LEFT JOIN task t ON t.id = (SELECT MIN(t2.id) FROM task t2"
        "   WHERE t2.ref_id = pf.id AND t2.task_type = 'extract_facts' AND t2.status = 'running')"
        " WHERE pf.project_id = %s AND pf.category = 'tender' ORDER BY pf.id",
        (project_id,),
    )
    row = query_one(
        "SELECT id, status, progress, detail FROM task"
        " WHERE task_type = 'lot_extract' AND ref_id = %s ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    cur = list_lots(project_id)
    return {
        "tender_files": files,
        "has_tender": bool(files),                                    # 传过招标文件
        "parsed": any(f["status"] == "parsed" for f in files),        # 至少一个解析完成
        "parsing": any(f["status"] != "parsed" for f in files),       # 还有没解析完的
        "running": bool(row and row["status"] == "running"),          # 识别任务进行中
        "task_id": row["id"] if row else None,
        "progress": (row["progress"] or 0) if row else 0,
        "detail": (row["detail"] or "") if row else "",
        "extracted": bool(row),                                       # 识别过（成功或失败）
        "lots": cur["lots"],
        "selected_id": cur["selected_id"],
    }


# ---------------------------------------------------------------- 标段档案（本标段包含什么）

@router.get("/{project_id}/lot-profile")
def get_lot_profile(project_id: int):
    """当前标段档案：里程范围/工程量/主要构造物/大型临时设施/过渡工程/弃土渣场/里程碑/接口/约束。

    未建立时返回 {found: false}（前端提示可重建）。
    """
    from app.services.lot_profile_service import get_profile

    prof = get_profile(project_id)
    return {"found": bool(prof), "profile": prof}


class LotProfileBody(BaseModel):
    text: str | None = None          # 编辑后的"一段文字"（自动解析回结构化字段）
    milestones: list | None = None   # 关键里程碑（表格编辑）
    profile: dict | None = None      # 兼容：整体覆盖


@router.put("/{project_id}/lot-profile")
def save_lot_profile(project_id: int, body: LotProfileBody):
    """人工编辑保存标段档案（一段文字 + 里程碑）。"""
    from app.services.lot_profile_service import save_profile
    from app.services.lot_service import selected_lot_code

    code = selected_lot_code(project_id)
    if not code:
        raise HTTPException(400, "该项目未选定标段")
    ok = save_profile(project_id, code, text=body.text,
                      milestones=body.milestones, profile=body.profile)
    return {"ok": ok}


@router.post("/{project_id}/lot-profile/rebuild")
def rebuild_lot_profile(project_id: int):
    """重新抽取标段档案（后台任务：含 LLM 补全，前端轮询 task 进度）。"""
    from app.services.lot_profile_service import start_rebuild
    from app.services.lot_service import list_lots

    # 前置校验：没有选定标段就没有"本标段档案"可建。若直接起后台任务，它必然抛
    # RuntimeError，而 task.detail 里带的是整段 traceback，会原样冒到界面上。
    lots = list_lots(project_id).get("lots") or []
    if not lots:
        raise HTTPException(400, "本项目招标文件未划分标段，无需建立标段档案")
    if not any(l.get("selected") for l in lots):
        raise HTTPException(400, "尚未选定标段，请先选择要编制档案的标段")
    return {"ok": True, "task_id": start_rebuild(project_id)}


@router.get("/{project_id}/lot-profile/rebuild-status")
def lot_profile_rebuild_status(project_id: int):
    """该项目最近一次"档案抽取"任务的状态。

    供前端在**刷新页面/重新打开弹窗**时恢复"抽取中"的遮罩（任务在服务端跑，不会因刷新中断）。
    """
    row = query_one(
        "SELECT id, status, progress, detail FROM task"
        " WHERE task_type = 'lot_profile' AND ref_id = %s ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    if not row:
        return {"running": False, "task_id": None, "status": None, "progress": 0, "detail": ""}
    return {
        "running": row["status"] == "running",
        "task_id": row["id"],
        "status": row["status"],
        "progress": row["progress"] or 0,
        "detail": row["detail"] or "",
    }


@router.post("/{project_id}/lot-relabel")
def start_lot_relabel(project_id: int):
    """启动标段归属全量重标（规则 + AI 补判定，后台任务，进度见 task）。"""
    from app.services.lot_scope_service import start_relabel

    task_id = start_relabel(project_id)
    return {"ok": True, "task_id": task_id}


# ---------------------------------------------------------------- 招标要求（覆盖检查清单）

@router.get("/{project_id}/requirements")
def get_requirements(project_id: int, category: str = ""):
    """招标要求清单（评分办法/技术要求，含建议章节与自检状态）。"""
    from app.services.requirement_service import list_requirements

    return list_requirements(project_id, category)


class ReqStatus(BaseModel):
    status: str  # pending / confirmed / ignored


@router.patch("/requirements/{req_id}/status")
def set_requirement_status(req_id: int, body: ReqStatus):
    from app.services.requirement_service import set_status

    if not set_status(req_id, body.status):
        raise HTTPException(400, "非法状态或要求不存在")
    return {"ok": True}


class ReqEdit(BaseModel):
    title: str | None = None
    content: str | None = None
    weight: float | None = None
    category: str | None = None
    suggested_chapter: str | None = None


@router.patch("/requirements/{req_id}")
def update_requirement(req_id: int, body: ReqEdit):
    """编辑招标要求（标题/内容/分值/类别/建议章节）。"""
    from app.services.requirement_service import update_requirement as _upd

    if not _upd(req_id, body.model_dump(exclude_none=True)):
        raise HTTPException(400, "没有可更新的字段或要求不存在")
    return {"ok": True}


@router.delete("/requirements/{req_id}")
def delete_requirement(req_id: int):
    """删除招标要求（AI 抽错或与本标段无关的条款）。"""
    from app.services.requirement_service import delete_requirement as _del

    if not _del(req_id):
        raise HTTPException(404, "要求不存在")
    return {"ok": True}


class ReqConfirmAllBody(BaseModel):
    project_id: int


@router.post("/requirements/confirm_all")
def confirm_all_requirements(body: ReqConfirmAllBody):
    """一键批量确认：全部待确认招标要求置为已确认。"""
    from app.services.requirement_service import confirm_all

    n = confirm_all(body.project_id)
    return {"ok": True, "confirmed": n}
