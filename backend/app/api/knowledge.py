"""知识库 API：知识库文档（原料仓）+ 章节知识页（成品）+ 章节类型统计。

对应表：knowledge_doc / knowledge_page / method_index
两类知识：sod=历史施组库（→ 章节知识页），method=工艺工法库（→ 工法条目）
"""
import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.database import execute, query, query_one
from app.services.knowledge_service import process_doc
from app.services.storage import storage
from app.services.task_runner import get_task, task_runner

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

ALLOWED_SUFFIXES = {".pdf", ".docx"}


# ---------------------------------------------------------------- 文档（原料仓）

@router.get("/docs")
def list_docs(category: str = "", keyword: str = ""):
    """知识库文档列表。category: sod | method"""
    sql = "SELECT id, file_name, doc_category, status, created_at FROM knowledge_doc"
    where, params = [], []
    if category:
        where.append("doc_category = %s")
        params.append(category)
    if keyword:
        where.append("file_name LIKE %s")
        params.append(f"%{keyword}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    docs = query(sql, tuple(params))
    # 附带知识页数量（sod）或工法条目数量（method）
    for d in docs:
        if d["doc_category"] == "method":
            d["pageCount"] = query_one(
                "SELECT COUNT(*) c FROM method_index WHERE doc_id = %s", (d["id"],)
            )["c"]
        else:
            d["pageCount"] = query_one(
                "SELECT COUNT(*) c FROM knowledge_page WHERE doc_id = %s", (d["id"],)
            )["c"]
        d["kind"] = d.pop("doc_category")
    return docs


@router.post("/docs")
async def upload_doc(file: UploadFile, category: str = "sod"):
    """上传知识库文档（历史施组 sod / 工艺工法 method），自动触发后台解析+知识提炼。"""
    suffix = ("." + file.filename.rsplit(".", 1)[-1].lower()) if "." in file.filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"不支持的文件类型: {suffix}")

    data = await file.read()
    meta = storage.save(data, file.filename, subdir="knowledge")
    file_hash = hashlib.md5(data).hexdigest()
    did = execute(
        "INSERT INTO knowledge_doc (file_name, file_path, doc_category, status, file_md5, created_at)"
        " VALUES (%s, %s, %s, 'pending', %s, NOW())",
        (file.filename, meta["path"], category, file_hash),
    )
    # 异步提炼管线（M3-1）：解析 → 章节切分 → 逐章提炼 → 入库
    task_id = task_runner.submit("extract_knowledge", did, process_doc, did)
    return {"id": did, "task_id": task_id, **meta}


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: int):
    row = query_one("SELECT file_path FROM knowledge_doc WHERE id = %s", (doc_id,))
    if not row:
        raise HTTPException(404, "文档不存在")
    execute("DELETE FROM knowledge_doc WHERE id = %s", (doc_id,))
    execute("DELETE FROM knowledge_page WHERE doc_id = %s", (doc_id,))
    execute("DELETE FROM method_index WHERE doc_id = %s", (doc_id,))
    try:
        storage.delete(row["file_path"])
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


@router.post("/docs/{doc_id}/reprocess")
def reprocess_doc(doc_id: int, keep_pages: bool = False):
    """重新解析提炼（不删文件）。keep_pages=False 时清空该文档旧知识页重新生成。"""
    row = query_one("SELECT id, status FROM knowledge_doc WHERE id = %s", (doc_id,))
    if not row:
        raise HTTPException(404, "文档不存在")
    if row["status"] == "parsing":
        raise HTTPException(409, "该文档正在解析中")
    if not keep_pages:
        execute("DELETE FROM knowledge_page WHERE doc_id = %s", (doc_id,))
    task_id = task_runner.submit("extract_knowledge", doc_id, process_doc, doc_id)
    return {"ok": True, "task_id": task_id}


@router.get("/tasks/running")
def running_tasks():
    """正在运行的提炼任务（页面切换回来时恢复行内进度轮询用）。"""
    rows = query(
        "SELECT id, ref_id, progress, detail FROM task"
        " WHERE task_type = 'extract_knowledge' AND status = 'running' ORDER BY id DESC"
    )
    return [
        {"task_id": r["id"], "ref_id": r["ref_id"], "progress": r["progress"], "detail": r["detail"]}
        for r in rows
    ]


@router.get("/tasks/{task_id}")
def task_status(task_id: int):
    """提炼任务进度（前端轮询）。"""
    t = get_task(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return {
        "task_id": t["id"],
        "ref_id": t["ref_id"],
        "status": t["status"],
        "progress": t["progress"],
        "detail": t["detail"],
    }


# ---------------------------------------------------------------- 章节类型统计

@router.get("/chapter-types")
def chapter_types():
    """知识页分组计数（按动态粗分类 category，v0.2 起章节类型枚举已废弃）。"""
    rows = query(
        "SELECT JSON_UNQUOTE(JSON_EXTRACT(content, '$.category')) AS cat, COUNT(*) c"
        " FROM knowledge_page GROUP BY cat"
    )
    return [{"name": r["cat"] or "未分类", "count": r["c"]} for r in rows]


# ---------------------------------------------------------------- 解析校对（M2-3）

@router.get("/docs/{doc_id}/parsed")
def get_parsed(doc_id: int):
    """解析校对数据：原文预览 URL（PDF；docx 用转换后的 PDF）+ 解析 markdown。"""
    from pathlib import Path

    from app.core.config import settings

    row = query_one("SELECT * FROM knowledge_doc WHERE id = %s", (doc_id,))
    if not row:
        raise HTTPException(404, "文档不存在")

    # 原文预览：pdf 直接用原件；docx 用解析目录里转换出的 PDF
    source_url = ""
    if row["file_path"].lower().endswith(".pdf"):
        source_url = f"/uploads/{row['file_path']}"
    elif row.get("parsed_path"):
        parsed_dir = Path(row["parsed_path"])
        if parsed_dir.is_absolute() and parsed_dir.exists():
            pdfs = sorted(parsed_dir.glob("*.pdf"))
            if pdfs:
                rel = pdfs[0].relative_to(settings.uploads_dir).as_posix()
                source_url = f"/uploads/{rel}"

    markdown = ""
    md_path = ""
    if row.get("parsed_path"):
        p = Path(row["parsed_path"]) / "full.md"
        if p.exists():
            markdown = p.read_text(encoding="utf-8")
            md_path = str(p)
    return {
        "doc_id": doc_id,
        "doc_name": row["file_name"],
        "status": row["status"],
        "source_url": source_url,
        "markdown": markdown,
    }


class ParsedBody(BaseModel):
    markdown: str


@router.put("/docs/{doc_id}/parsed")
def save_parsed(doc_id: int, body: ParsedBody):
    """保存人工校对后的 markdown（full.md），之后可重新提炼。"""
    from pathlib import Path

    row = query_one("SELECT parsed_path FROM knowledge_doc WHERE id = %s", (doc_id,))
    if not row:
        raise HTTPException(404, "文档不存在")
    if not row.get("parsed_path"):
        raise HTTPException(400, "该文档尚未解析")
    p = Path(row["parsed_path"]) / "full.md"
    p.write_text(body.markdown, encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------- 工艺工法（2026-09-04 重构：一工法一条概况，整文注入）

@router.get("/methods")
def list_methods():
    """工法概况清单（一工法一条：名称/概况/适用范围/来源文件）。"""
    from app.services.method_index import catalog

    return catalog()


@router.get("/methods/{doc_id}/content")
def get_method_content(doc_id: int):
    """单个工法整篇原文（含文字/工艺流程/表格/图片，图片引用改写为可访问 URL）。"""
    from pathlib import Path

    from app.core.database import query_one

    row = query_one("SELECT file_name, parsed_path FROM knowledge_doc WHERE id = %s AND doc_category = 'method'", (doc_id,))
    if not row or not row.get("parsed_path"):
        raise HTTPException(404, "工法文档不存在或未解析")
    md_path = Path(row["parsed_path"]) / "full.md"
    if not md_path.exists():
        raise HTTPException(404, "工法解析产物不存在")
    from app.services.file_index import _rewrite_image_refs

    text = _rewrite_image_refs(md_path.read_text(encoding="utf-8"), md_path.parent)
    return {"doc_id": doc_id, "file_name": row["file_name"], "text": text}


# ---------------------------------------------------------------- 向量兜底检索（V1）
# 本项目已移除向量库（2026-09-08）：接口删除。


# ---------------------------------------------------------------- 知识页（成品）

def _page_to_dict(row: dict) -> dict:
    """把 knowledge_page 行组装成前端期望的扁平结构。"""
    tags = row.get("tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (TypeError, ValueError):
            tags = []
    content = row.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            content = {}
    content = content or {}
    return {
        "page_id": f"kp-{row['id']}",
        "id": row["id"],
        "doc_id": row["doc_id"],
        "chapter_type": row["chapter_type"],
        "category": content.get("category", ""),  # 动态粗分类（v0.2 起替代章节类型分组）
        "title": row["title"],
        "tags": tags or [],
        "method": content.get("method", ""),
        "key_points": content.get("key_points", []),
        "tables": content.get("tables", []),
        "images": content.get("images", []),
        "usage": content.get("usage", ""),
        "estimate_words": content.get("estimate_words"),
        "source": {"doc": row.get("doc_name", ""), "location": row.get("source_location", "")},
    }


@router.get("/pages")
def list_pages(chapter_type: str = "", keyword: str = "", ids: str = "", doc_id: int = 0):
    """知识页列表（轻量；doc15 全量含超大 content 时 SQL filesort 会超 sort buffer，故不排序）。

    顺序：doc_id 过滤后按主键序（提炼即按章节插入序）；前端树自行排序。
    """
    sql = (
        "SELECT p.*, d.file_name AS doc_name FROM knowledge_page p"
        " LEFT JOIN knowledge_doc d ON p.doc_id = d.id"
    )
    where, params = [], []
    if ids:
        # 按逗号分隔 id 过滤（如思路里引用的知识页，用来取标题）
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        if id_list:
            marks = ",".join(["%s"] * len(id_list))
            where.append(f"p.id IN ({marks})")
            params.extend(id_list)
    if doc_id:
        where.append("p.doc_id = %s")
        params.append(doc_id)
    if chapter_type:
        where.append("p.chapter_type = %s")
        params.append(chapter_type)
    if keyword:
        where.append("(p.title LIKE %s OR p.chapter_type LIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    # 不做 ORDER BY：content 含超大 ref_text 的页 filesort 会超 sort_buffer_size(256KB)
    return [_page_to_dict(r) for r in query(sql, tuple(params))]


@router.get("/pages/{page_id}")
def get_page(page_id: int):
    row = query_one(
        "SELECT p.*, d.file_name AS doc_name FROM knowledge_page p"
        " LEFT JOIN knowledge_doc d ON p.doc_id = d.id WHERE p.id = %s",
        (page_id,),
    )
    if not row:
        raise HTTPException(404, "知识页不存在")
    d = _page_to_dict(row)
    content = row.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            content = {}
    content = content or {}
    # 详情单独回传历史原文参考（体积较大，列表不下发）
    d["ref_text"] = content.get("ref_text", "")
    d["numbers"] = content.get("numbers", [])
    return d


@router.put("/pages/{page_id}")
def update_page(page_id: int, body: dict):
    """编辑知识页：保存 schema 字段修订（保留未编辑的 estimate_words/ref_text/numbers/category）。"""
    old = query_one("SELECT content FROM knowledge_page WHERE id = %s", (page_id,))
    old_content: dict = {}
    if old and old.get("content"):
        try:
            old_content = json.loads(old["content"]) if isinstance(old["content"], str) else old["content"]
        except (TypeError, ValueError):
            old_content = {}
    old_content = old_content or {}
    tags = body.get("tags") or []
    content = {
        "category": body.get("category", old_content.get("category", "")),
        "method": body.get("method", ""),
        "key_points": body.get("key_points") or [],
        "tables": body.get("tables") or [],
        "images": body.get("images") or [],
        "usage": body.get("usage", ""),
        # 不可编辑字段原样保留；ref_text 可显式传（可选）覆盖
        "numbers": old_content.get("numbers", []),
        "estimate_words": old_content.get("estimate_words"),
        "ref_text": body.get("ref_text") if body.get("ref_text") is not None else old_content.get("ref_text", ""),
    }
    execute(
        "UPDATE knowledge_page SET title = %s, tags = %s, content = %s,"
        " status = 'confirmed', updated_at = NOW() WHERE id = %s",
        (body.get("title", ""), json.dumps(tags, ensure_ascii=False),
         json.dumps(content, ensure_ascii=False), page_id),
    )
    return {"ok": True}
