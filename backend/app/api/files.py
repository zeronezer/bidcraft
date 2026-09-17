"""项目文件上传管理 API。"""
import hashlib

from fastapi import APIRouter, HTTPException, UploadFile

from app.core.database import execute, query, query_one
from app.services.fact_service import process_file
from app.services.storage import storage
from app.services.task_runner import task_runner

router = APIRouter(prefix="/api/projects/{project_id}/files", tags=["files"])

ALLOWED_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".xlsx", ".xls"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 可解析并触发事实抽取的后缀（表格直读也触发：附表即数据事实）
PARSEABLE_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".xlsx", ".xls"}


@router.post("/image")
async def upload_editor_image(project_id: int, file: UploadFile):
    """编辑器图片上传（正文插图）：存入 uploads/editor/，返回可访问 URL 供 <img> 引用。"""
    suffix = ("." + file.filename.rsplit(".", 1)[-1].lower()) if "." in file.filename else ""
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(400, f"不支持的图片类型: {suffix}")
    data = await file.read()
    meta = storage.save(data, file.filename, subdir=f"projects/{project_id}/editor")
    return {"ok": True, "url": f"/uploads/{meta['path']}", "name": file.filename}


@router.post("")
async def upload_file(project_id: int, file: UploadFile, category: str = "other"):
    """上传项目文件（招标文件/指导性施组/参考图纸/答疑补遗/踏勘报告/前期策划…）。

    category: tender | guiding_sod | drawing | clarification | survey_report | planning | other
    上传招标/指导性施组/答疑/勘察/前期策划类文件后自动触发后台事实抽取，
    解析完成后切片入向量库供编写正文时按章取材。
    """
    suffix = ("." + file.filename.rsplit(".", 1)[-1].lower()) if "." in file.filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"不支持的文件类型: {suffix}")

    data = await file.read()
    meta = storage.save(data, file.filename, subdir=f"projects/{project_id}")
    file_hash = hashlib.md5(data).hexdigest()
    fid = execute(
        "INSERT INTO project_file (project_id, file_name, file_path, file_type,"
        " category, size_bytes, status, file_md5, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, NOW())",
        (project_id, file.filename, meta["path"], suffix.lstrip("."), category, meta["size"], file_hash),
    )
    task_id = None
    # 所有可解析类别统一触发：解析 → 建章节索引 → 逐章抽取全局参数（不再按类别过滤）
    if suffix in PARSEABLE_SUFFIXES:
        task_id = task_runner.submit(
            "extract_facts", fid, process_file, project_id, fid,
        )
    return {"id": fid, "task_id": task_id, **meta}


@router.get("")
def list_files(project_id: int):
    """项目文件清单；运行中的解析任务一并带回（task_id/progress/detail），
    前端刷新重进后可据此恢复进度轮询。"""
    return query(
        "SELECT pf.id, pf.file_name, pf.file_type, pf.category, pf.size_bytes,"
        " pf.status, pf.created_at,"
        " t.id AS task_id, t.status AS task_status, t.progress AS task_progress,"
        " t.detail AS task_detail"
        " FROM project_file pf"
        " LEFT JOIN task t ON t.id = (SELECT MIN(t2.id) FROM task t2"
        "   WHERE t2.ref_id = pf.id AND t2.task_type = 'extract_facts' AND t2.status = 'running')"
        " WHERE pf.project_id = %s ORDER BY pf.created_at DESC, pf.id DESC",
        (project_id,),
    )


@router.delete("/{file_id}")
def delete_file(project_id: int, file_id: int):
    """删除项目文件：文件行 + 章节索引 + 本文件的解析目录与原文件。

    解析目录仅当为该文件自有的规范目录（uploads/parsed/project_{pid}/{fid}）时才删除；
    若 parsed_path 指向复用/共享的其他解析产物则不动，避免误删他文件。
    进行中的解析任务一并标记失败（防止线程写回已删文件）。
    """
    import shutil

    from app.core.config import settings

    row = query_one(
        "SELECT * FROM project_file WHERE id = %s AND project_id = %s", (file_id, project_id),
    )
    if not row:
        # 幂等：可能已被删除/正在删除，返回成功避免前端反复报"文件不存在"
        return {"ok": True, "already": True}
    # 中止仍在跑的解析/索引线程（防止删了文件后台还继续烧 MinerU/LLM）
    from app.services.fact_service import cancel_parse

    cancel_parse(file_id)
    # 停掉该文件进行中的解析任务
    execute(
        "UPDATE task SET status = 'failed', detail = '文件已删除，任务取消', updated_at = NOW()"
        " WHERE ref_id = %s AND task_type = 'extract_facts' AND status = 'running'",
        (file_id,),
    )
    # 本文件自有解析目录（仅当是规范目录时删除）
    own_dir = settings.uploads_dir / "parsed" / f"project_{project_id}" / str(file_id)
    if own_dir.exists():
        shutil.rmtree(own_dir, ignore_errors=True)
    # 原上传文件
    if row.get("file_path"):
        try:
            storage.delete(row["file_path"])
        except Exception:  # noqa: BLE001 文件缺失不影响删除
            pass
    # 章节索引（避免残留出现在文件索引里）
    execute("DELETE FROM file_section_index WHERE file_id = %s", (file_id,))
    execute("DELETE FROM project_file WHERE id = %s", (file_id,))
    return {"ok": True}


@router.post("/{file_id}/reparse")
def reparse_file(project_id: int, file_id: int):
    """重新解析并抽取事实（pending/failed/已删除任务中断的文件可重跑，无需重传）。"""
    row = query_one(
        "SELECT id, status FROM project_file WHERE id = %s AND project_id = %s", (file_id, project_id),
    )
    if not row:
        raise HTTPException(404, "文件不存在")
    running = query_one(
        "SELECT id FROM task WHERE ref_id = %s AND task_type = 'extract_facts' AND status = 'running'",
        (file_id,),
    )
    if running:
        return {"ok": False, "message": "该文件正在解析中，无需重复发起"}
    execute("UPDATE project_file SET status = 'pending' WHERE id = %s", (file_id,))
    task_id = task_runner.submit("extract_facts", file_id, process_file, project_id, file_id)
    return {"ok": True, "task_id": task_id}


@router.get("/{file_id}/sections")
def file_sections(project_id: int, file_id: int):
    """文件的章节索引（章节名/概要/适用范围/字数——file_section_index，人工查看用）。"""
    if not query_one(
        "SELECT id FROM project_file WHERE id = %s AND project_id = %s",
        (file_id, project_id),
    ):
        raise HTTPException(404, "文件不存在")
    return query(
        "SELECT id, sec_path, summary, applies_to, char_count, start_line, end_line"
        " FROM file_section_index WHERE file_id = %s ORDER BY start_line",
        (file_id,),
    )


@router.get("/{file_id}/sections/{index_id}/content")
def file_section_content(project_id: int, file_id: int, index_id: int):
    """按索引取章节完整原文（含子节与图片引用，不截断）。"""
    from pathlib import Path

    from app.services import file_index

    row = query_one(
        "SELECT fi.*, pf.parsed_path, pf.file_name FROM file_section_index fi"
        " JOIN project_file pf ON pf.id = fi.file_id"
        " WHERE fi.id = %s AND fi.file_id = %s AND fi.project_id = %s",
        (index_id, file_id, project_id),
    )
    if not row or not row.get("parsed_path"):
        raise HTTPException(404, "章节索引不存在")
    catalog = [{**row, "parsed_path": row["parsed_path"]}]
    # 优先用索引里存的原文（自包含）——docx 走对象级切章、不再落 full.md，必须走这条路；
    # 旧数据（未回填 content）回退按行从 full.md 取。
    text = row.get("content")
    if not text:
        md_path = Path(row["parsed_path"]) / "full.md"
        if not md_path.exists():
            raise HTTPException(404, "解析产物不存在（该文件未保留原文）")
        lines = md_path.read_text(encoding="utf-8").splitlines()
        text = "\n".join(lines[row["start_line"]:row["end_line"]])
    if row.get("parsed_path"):
        from app.services.file_index import _rewrite_image_refs

        text = _rewrite_image_refs(text, Path(row["parsed_path"]))
    return {"sec_path": row["sec_path"], "file_name": row["file_name"], "text": text}
