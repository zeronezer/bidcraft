"""目录树与章节正文读写 API（M4-4 / M4-5 持久化）。

路由前缀：/api/sections
- GET    /{project_id}/tree                 读目录树（嵌套，含 id/status）
- PUT    /{project_id}/tree                 整树保存（AI 目录确认/模板导入）
- POST   /{project_id}/nodes                新增节点
- PATCH  /nodes/{node_id}                   重命名/状态/排序
- DELETE /nodes/{node_id}                   删除（递归含子节点与正文）
- GET    /content/{node_id}                 读单章正文
- PUT    /content/{node_id}                 保存单章正文（用户编辑，ai_generated 清零）
- POST   /content/{node_id}/confirm         确认 AI 内容
- POST   /content/{node_id}/revert          回退到 AI 生成前

表：chapter_node（status: unwritten/thought_ready/generated/confirmed）
    section_content（content JSON {format: md|html, body, prev_content?}, ai_generated）
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import execute, query, query_one
from app.services.section_service import get_tree

router = APIRouter(prefix="/api/sections", tags=["sections"])

VALID_STATUS = {"unwritten", "thought_ready", "generated", "confirmed"}


class TreeBody(BaseModel):
    tree: list[dict]  # [{title, children:[...]}]


class NodeCreate(BaseModel):
    title: str
    parent_id: int | None = None
    sort_order: int | None = None
    lot_id: int | None = None


class NodePatch(BaseModel):
    title: str | None = None
    status: str | None = None
    sort_order: int | None = None
    parent_id: int | None = None


class ContentBody(BaseModel):
    format: str = "html"   # html | md
    body: str = ""


# ---------------------------------------------------------------- 目录树

@router.get("/{project_id}/tree")
def read_tree(project_id: int):
    return get_tree(project_id)


@router.put("/{project_id}/tree")
def write_tree(project_id: int, body: TreeBody):
    """整树保存（幂等 upsert 标题匹配，前端"确认目录"时用）。"""
    from app.services.section_service import tree_empty, _insert_nodes

    if tree_empty(project_id):
        n = _insert_nodes(project_id, body.tree, None, None)
        return {"ok": True, "inserted": n, "tree": get_tree(project_id)}

    # 已有树：展平后逐条按 (parent,title) upsert，保留已有节点正文
    existing_rows = query(
        "SELECT id, parent_id, title FROM chapter_node WHERE project_id = %s", (project_id,)
    )
    existing_key = {}  # (parent_title|None, title) -> id
    id_title = {r["id"]: r["title"] for r in existing_rows}
    for r in existing_rows:
        pt = id_title.get(r["parent_id"]) if r["parent_id"] else None
        existing_key[(pt, r["title"])] = r["id"]

    wanted_keys = set()

    def walk(nodes, parent_title):
        for i, n in enumerate(nodes or []):
            title = (n.get("title") or "").strip()
            if not title:
                continue
            key = (parent_title, title)
            wanted_keys.add(key)
            nid = existing_key.get(key)
            if nid is None:
                pid = existing_key.get((None, parent_title)) if parent_title else None
                # 父节点也可能是更深路径，简化：按标题全局找父
                if parent_title and pid is None:
                    row = query_one(
                        "SELECT id FROM chapter_node WHERE project_id = %s AND title = %s LIMIT 1",
                        (project_id, parent_title),
                    )
                    pid = row["id"] if row else None
                nid = execute(
                    "INSERT INTO chapter_node (project_id, parent_id, title, sort_order, status)"
                    " VALUES (%s, %s, %s, %s, 'unwritten')",
                    (project_id, pid, title, i),
                )
                existing_key[key] = nid
            else:
                execute("UPDATE chapter_node SET sort_order = %s WHERE id = %s", (i, nid))

    walk(body.tree, None)

    # 删除新树中不存在的旧节点
    removed = 0
    all_rows = query("SELECT id, title, parent_id FROM chapter_node WHERE project_id = %s", (project_id,))
    id2title = {r["id"]: r["title"] for r in all_rows}
    titles_in_tree = set()

    def collect(nodes):
        for n in nodes or []:
            if n.get("title"):
                titles_in_tree.add(n["title"].strip())
            collect(n.get("children"))

    collect(body.tree)
    for r in all_rows:
        if r["title"] not in titles_in_tree:
            execute("DELETE FROM section_content WHERE chapter_node_id = %s", (r["id"],))
            execute("DELETE FROM chapter_node WHERE id = %s", (r["id"],))
            removed += 1

    return {"ok": True, "removed": removed, "tree": get_tree(project_id)}


@router.post("/{project_id}/nodes")
def create_node(project_id: int, body: NodeCreate):
    sort = body.sort_order
    if sort is None and body.parent_id:
        row = query_one(
            "SELECT COUNT(*) c FROM chapter_node WHERE project_id = %s AND parent_id = %s",
            (project_id, body.parent_id),
        )
        sort = row["c"]
    elif sort is None:
        row = query_one(
            "SELECT COUNT(*) c FROM chapter_node WHERE project_id = %s AND parent_id IS NULL",
            (project_id,),
        )
        sort = row["c"]
    nid = execute(
        "INSERT INTO chapter_node (project_id, lot_id, parent_id, title, sort_order, status)"
        " VALUES (%s, %s, %s, %s, %s, 'unwritten')",
        (project_id, body.lot_id, body.parent_id, body.title, sort),
    )
    return {"id": nid}


def _collect_descendant_ids(node_id: int, acc: list[int]) -> None:
    acc.append(node_id)
    for r in query("SELECT id FROM chapter_node WHERE parent_id = %s", (node_id,)):
        _collect_descendant_ids(r["id"], acc)


@router.patch("/nodes/{node_id}")
def patch_node(node_id: int, body: NodePatch):
    if not query_one("SELECT id FROM chapter_node WHERE id = %s", (node_id,)):
        raise HTTPException(404, "章节不存在")
    if body.title is not None:
        execute("UPDATE chapter_node SET title = %s WHERE id = %s", (body.title, node_id))
    if body.sort_order is not None:
        execute("UPDATE chapter_node SET sort_order = %s WHERE id = %s", (body.sort_order, node_id))
    if body.parent_id is not None:
        execute("UPDATE chapter_node SET parent_id = %s WHERE id = %s", (body.parent_id, node_id))
    if body.status:
        if body.status not in VALID_STATUS:
            raise HTTPException(400, f"非法状态: {body.status}")
        execute("UPDATE chapter_node SET status = %s WHERE id = %s", (body.status, node_id))
    return {"ok": True}


@router.delete("/nodes/{node_id}")
def delete_node(node_id: int):
    if not query_one("SELECT id FROM chapter_node WHERE id = %s", (node_id,)):
        raise HTTPException(404, "章节不存在")
    ids: list[int] = []
    _collect_descendant_ids(node_id, ids)
    for nid in ids:
        execute("DELETE FROM section_content WHERE chapter_node_id = %s", (nid,))
    execute(
        f"DELETE FROM chapter_node WHERE id IN ({','.join(map(str, ids))})"
    )
    return {"ok": True, "deleted": len(ids)}


# ---------------------------------------------------------------- 正文

def _load_section(node_id: int) -> dict:
    row = query_one(
        "SELECT n.id, n.title, n.status, c.content AS cj, c.ai_generated, c.updated_at AS saved_at"
        " FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id = n.id WHERE n.id = %s",
        (node_id,),
    )
    if not row:
        raise HTTPException(404, "章节不存在")
    payload = {}
    if row.get("cj"):
        try:
            payload = json.loads(row["cj"]) if isinstance(row["cj"], str) else row["cj"]
        except (TypeError, ValueError):
            payload = {}
    return {
        "node_id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "found": bool(payload.get("body")),
        "content": {
            "format": payload.get("format", "html"),
            "body": payload.get("body", ""),
            # 本次 AI 生成的取材摘要（经验/知识页/项目文件片段/工法/事实条数），前端"取材来源"弹窗用
            "used": payload.get("used"),
        },
        "ai_generated": bool(row.get("ai_generated")),
        "saved_at": str(row.get("saved_at") or ""),
    }


@router.get("/content/{node_id}")
def read_content(node_id: int):
    return _load_section(node_id)


@router.get("/used-detail")
def used_detail(project_id: int, kind: str, item_id: int):
    """取材来源条目详情：点"取材来源"弹窗里的某条 → 看该条的原文/全文。

    kind: experience（经验全文）| file（项目文件章节原文）| knowledge（知识页原文）| method（工法）
    """
    if kind == "experience":
        row = query_one(
            "SELECT exp_type, chapter_type, content FROM experience_item WHERE id = %s",
            (item_id,),
        )
        if not row:
            raise HTTPException(404, "经验条目不存在")
        return {"title": f"[{row['exp_type']}] {row['chapter_type'] or '全局经验（所有章节）'}",
                "text": row.get("content") or ""}

    if kind == "file":
        row = query_one(
            "SELECT sec_path FROM file_section_index WHERE id = %s AND project_id = %s",
            (item_id, project_id),
        )
        if not row:
            raise HTTPException(404, "章节不存在")
        from app.services.file_index import get_catalog, load_sections

        secs = load_sections(get_catalog(project_id), [item_id])
        return {"title": row["sec_path"], "text": (secs[0]["text"] if secs else "")}

    if kind == "knowledge":
        from app.services.matching import load_pages_by_ids

        pages = load_pages_by_ids([item_id])
        if not pages:
            raise HTTPException(404, "知识页不存在")
        p = pages[0]
        return {"title": p.get("title") or "",
                "text": p.get("ref_text") or p.get("desc") or ""}

    if kind == "method":
        row = query_one("SELECT name, text FROM method_entry WHERE id = %s", (item_id,))
        if not row:
            raise HTTPException(404, "工法不存在")
        return {"title": row.get("name") or "", "text": row.get("text") or ""}

    raise HTTPException(400, "未知的取材类型")


@router.put("/content/{node_id}")
def put_content(node_id: int, body: ContentBody):
    """用户保存正文：覆盖内容，AI 标记清除（用户已审），状态升 confirmed。

    经验沉淀（M3-4）：若覆盖前是 AI 生成内容且发生实质修改，异步触发 diff 提炼。
    """
    row = query_one("SELECT id, project_id, title FROM chapter_node WHERE id = %s", (node_id,))
    if not row:
        raise HTTPException(404, "章节不存在")
    # 保存前快照（经验提炼用）
    old = query_one("SELECT content, ai_generated FROM section_content WHERE chapter_node_id = %s", (node_id,))
    old_body, was_ai = "", False
    if old and old.get("content"):
        try:
            old_p = json.loads(old["content"]) if isinstance(old["content"], str) else old["content"]
            old_body = old_p.get("body", "")
        except (TypeError, ValueError):
            pass
        was_ai = bool(old.get("ai_generated"))

    payload = json.dumps({"format": body.format, "body": body.body}, ensure_ascii=False)
    if old:
        execute(
            "UPDATE section_content SET content = %s, ai_generated = 0 WHERE chapter_node_id = %s",
            (payload, node_id),
        )
    else:
        execute(
            "INSERT INTO section_content (chapter_node_id, content, ai_generated) VALUES (%s, %s, 0)",
            (node_id, payload),
        )
    execute("UPDATE chapter_node SET status = 'confirmed' WHERE id = %s", (node_id,))

    if was_ai and old_body and old_body.strip() != body.body.strip():
        from app.services.experience_service import distill_edit_task
        from app.services.task_runner import task_runner

        task_runner.submit("distill_exp", node_id, distill_edit_task,
                           row["project_id"], row["title"], old_body, body.body)
    r2 = query_one("SELECT updated_at FROM section_content WHERE chapter_node_id = %s", (node_id,))
    return {"ok": True, "saved_at": str(r2["updated_at"]) if r2 else ""}


@router.post("/content/{node_id}/confirm")
def confirm_content(node_id: int):
    """用户确认 AI 生成内容：ai_generated 标记清除，状态 confirmed。"""
    if not query_one("SELECT id FROM chapter_node WHERE id = %s", (node_id,)):
        raise HTTPException(404, "章节不存在")
    execute("UPDATE section_content SET ai_generated = 0 WHERE chapter_node_id = %s", (node_id,))
    execute("UPDATE chapter_node SET status = 'confirmed' WHERE id = %s", (node_id,))
    return {"ok": True}


@router.post("/content/{node_id}/revert")
def revert_content(node_id: int):
    """回退到 AI 生成前内容（无旧内容则清空回到未写）。

    经验沉淀（M3-4）：回退 = 用户否决 AI 写法，异步提炼纠偏规则。
    """
    row = query_one(
        "SELECT c.id, c.content AS cj FROM section_content c WHERE c.chapter_node_id = %s",
        (node_id,),
    )
    if not row:
        raise HTTPException(404, "该章节尚无正文")
    node = query_one("SELECT project_id, title FROM chapter_node WHERE id = %s", (node_id,))
    try:
        payload = json.loads(row["cj"]) if isinstance(row["cj"], str) else (row["cj"] or {})
    except (TypeError, ValueError):
        payload = {}
    rejected_body = payload.get("body", "")
    prev = payload.pop("prev_content", "")
    fmt = payload.pop("prev_format", "md")
    new_payload = json.dumps({"format": fmt, "body": prev}, ensure_ascii=False)
    execute(
        "UPDATE section_content SET content = %s, ai_generated = 0 WHERE chapter_node_id = %s",
        (new_payload, node_id),
    )
    execute(
        "UPDATE chapter_node SET status = %s WHERE id = %s",
        ("confirmed" if prev else "unwritten", node_id),
    )
    if node and rejected_body.strip():
        from app.services.experience_service import distill_reject_task
        from app.services.task_runner import task_runner

        task_runner.submit("distill_exp", node_id, distill_reject_task,
                           node["project_id"], node["title"], rejected_body)
    return {"ok": True}


@router.post("/nodes/{node_id}/regenerate_outline")
def regenerate_outline(node_id: int):
    """重新生成单个章节的编写思路——后台异步任务，立即返回 task_id。

    前端拿 task_id 轮询 /knowledge/tasks/{task_id}；刷新页面后任务在服务端线程继续，
    可由 GET /sections/{project_id}/outline_tasks 恢复跟踪。
    """
    from app.services.task_runner import task_runner

    row = query_one(
        "SELECT id, project_id FROM chapter_node WHERE id = %s", (node_id,),
    )
    if not row:
        raise HTTPException(404, "章节不存在")
    from app.services import compile_service

    task_id = task_runner.submit(
        "regen_node_outline", node_id,
        compile_service.regenerate_node_outline, row["project_id"], node_id,
    )
    return {"task_id": task_id, "node_id": node_id}


@router.get("/{project_id}/outline_tasks")
def running_outline_tasks(project_id: int):
    """返回该项目所有进行中的"思路生成"任务（供前端刷新后恢复跟踪）。

    - regen_node_outline：单章重新生成（ref_id=node_id）
    - gen_outlines：批量补思路（ref_id=project_id）
    """
    rows = query(
        "SELECT id, task_type, ref_id, progress, detail FROM task"
        " WHERE status = 'running' AND (task_type = 'regen_node_outline' OR task_type = 'gen_outlines')"
        " ORDER BY id",
    )
    out = []
    for t in rows:
        if t["task_type"] == "regen_node_outline":
            n = query_one(
                "SELECT id, title, project_id FROM chapter_node WHERE id = %s", (t["ref_id"],),
            )
            if not n or n["project_id"] != project_id:
                continue
            out.append({"task_id": t["id"], "kind": "node", "node_id": n["id"],
                        "title": n["title"], "progress": t["progress"], "detail": t["detail"]})
        elif t["task_type"] == "gen_outlines" and t["ref_id"] == project_id:
            out.append({"task_id": t["id"], "kind": "batch",
                        "progress": t["progress"], "detail": t["detail"]})
    return {"tasks": out}


# ---------------------------------------------------------------- 目录批量编辑（粘贴文字生成/替换目录，支持任意层级）

class TocTextBody(BaseModel):
    text: str


_TOC_PARSE_SYSTEM = """你是施组目录结构化助手。输入是招标/施组文档摘录（可能含页码、说明性文字、图表清单、省略号）。
任务：抽取**目录标题行**并按层级整理为嵌套 JSON 树。

规则：
1. 层级判定依据标题编号层级：一级章（如 1.、第一章、（一））、二级节（1.1）、三级小节（1.1.1），
   更深层级同样按编号归入父级；无编号但明显是子标题的按语义归入最近父级；
2. **忽略**：叙述性/说明性整句（如"除采用文字表述外…""所附图表为示例，投标人可根据项目特点增删修改…"）；
   页码与行内孤号（如 334、335）；省略号"……"；
   图/表/附表清单条目（形如"6-1 施工总平面布置示意图""6-20 质量保证体系图"这类 N-M 编号的附图附表清单）；
3. 标题后的括号备注（如"（与各标段施工组织设计评分内容匹配）"）保留在标题内，不拆成独立标题；
4. 只输出 JSON 树，标题格式如下（children 可省略为空数组）：
{"chapters": [{"title": "一级章标题", "children": [{"title": "二级节标题", "children": [{"title": "三级小节标题"}]}]}]}
宁缺毋滥：识别不了或非标题的行一律丢弃。"""


def _clean_toc_lines(text: str) -> str:
    """去除明显噪声行（页码、纯省略号、空行），降低 LLM 负担。"""
    import re

    keep = []
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if re.fullmatch(r"[…。·_~.\-—]{2,}", ln):
            continue
        if re.fullmatch(r"[…。·_~.\-—]{2,}", ln):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _sanitize_tree(items: list) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        t = str(it.get("title") or "").strip()
        if not t:
            continue
        node = {"title": t}
        kids = _sanitize_tree(it.get("children"))
        if kids:
            node["children"] = kids
        out.append(node)
    return out


def _parse_toc_text(text: str) -> list[dict]:
    """清洗后文本 → 嵌套目录树（任意层级）。"""
    from app.services.llm import llm

    cleaned = _clean_toc_lines(text)
    raw = llm.chat(
        [{"role": "system", "content": _TOC_PARSE_SYSTEM},
         {"role": "user", "content": "文档摘录：\n" + cleaned[:20000]}],
        temperature=0.0, max_tokens=8000,
    )
    s, e = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[s:e + 1]) if s != -1 and e != -1 else {}
    chapters = _sanitize_tree(data.get("chapters"))
    return chapters


def _count_tree(nodes: list[dict]) -> int:
    n = 0
    for nd in nodes or []:
        n += 1 + _count_tree(nd.get("children"))
    return n


def _paths(nodes: list[dict], prefix: tuple = ()) -> set[tuple]:
    ps = set()
    for nd in nodes or []:
        cur = prefix + (nd["title"],)
        ps.add(cur)
        ps |= _paths(nd.get("children"), cur)
    return ps


def _node_chains(project_id: int) -> tuple[list[dict], set[tuple]]:
    """现树扁平行 + 每条现树节点的标题链（自根）。"""
    from app.services.section_service import get_tree

    flat: dict[int, dict] = {}
    roots = get_tree(project_id)

    def walk(nodes, parent=None):
        for nd in nodes:
            kid_ids = [c["id"] for c in (nd.get("children") or [])]
            flat[nd["id"]] = {"id": nd["id"], "title": nd["title"], "parent": parent, "children": kid_ids,
                              "status": nd.get("status")}
            walk(nd.get("children", []), nd["id"])
    walk(roots)
    # 由根自下而上算链
    title_by = {i: r["title"] for i, r in flat.items()}
    chains: list[dict] = []
    for nid, r in flat.items():
        chain = []
        cur = nid
        while cur is not None:
            chain.insert(0, title_by[cur])
            cur = flat[cur]["parent"]
        r["chain"] = tuple(chain)
        chains.append(r)
    return flat, chains, roots


def _desc_ids(node_id: int, acc: list[int]) -> None:
    acc.append(node_id)
    for r in query("SELECT id FROM chapter_node WHERE parent_id = %s", (node_id,)):
        _desc_ids(r["id"], acc)


def _has_body(ids: list[int]) -> bool:
    if not ids:
        return False
    marks = ",".join(map(str, ids))
    r = query_one(f"SELECT COUNT(*) c FROM section_content WHERE chapter_node_id IN ({marks})")
    return bool(r and r["c"])


def _plan(project_id: int, tree: list[dict]) -> dict:
    """计算删除列表（含正文标记）与新增数。"""
    flat, chains, roots = _node_chains(project_id)
    expected = _paths(tree)
    # 删除：现树某条完整链不在期望中 → 收集（父已在删除集的，跳过避免重复）
    dels: list[dict] = []
    del_ids: set[int] = set()
    for r in chains:
        if r["chain"] in expected:
            continue
        # 祖先是否已被标删（若祖先也删，子孙随之删）
        anc = r["parent"]
        ancestor_del = False
        while anc is not None:
            if anc in del_ids:
                ancestor_del = True
                break
            anc = flat[anc]["parent"]
        if ancestor_del:
            continue
        acc: list[int] = []
        _desc_ids(r["id"], acc)
        del_ids.add(r["id"])
        dels.append({"id": r["id"], "title": r["title"], "has_body": _has_body(acc)})
    # 新增：期望链在现树不存在的节点数
    existing = {r["chain"] for r in chains}
    additions = 0
    for ch in tree:
        additions += _count_missing(ch, existing)
    return {"deletions": dels, "delete_with_body": sum(1 for d in dels if d["has_body"]),
            "additions": additions, "node_count": _count_tree(tree)}


def _count_missing(node: dict, existing: set[tuple], prefix: tuple = ()) -> int:
    cur = prefix + (node["title"],)
    n = 0
    if cur not in existing:
        n += 1 + _count_tree(node.get("children"))
        return n
    for c in (node.get("children") or []):
        n += _count_missing(c, existing, cur)
    return n


@router.post("/{project_id}/toc/preview")
def toc_preview(project_id: int, body: TocTextBody):
    """解析目录文本并给出变更预览（不动库）。"""
    tree = _parse_toc_text(body.text)
    plan = _plan(project_id, tree)
    return {"chapters": tree, **plan}


def _find_child_id(project_id: int, parent_id, title: str):
    row = query_one(
        "SELECT id FROM chapter_node WHERE project_id = %s AND parent_id <=> %s AND title = %s LIMIT 1",
        (project_id, parent_id, title),
    )
    return row["id"] if row else None


def _ensure_tree(project_id: int, nodes: list[dict], parent_id, stats: dict) -> None:
    """按序确保节点存在：已有同名不重建（保留思路/正文），缺则补，并向下补 children。"""
    for nd in nodes or []:
        nid = _find_child_id(project_id, parent_id, nd["title"])
        if nid is None:
            nid = execute(
                "INSERT INTO chapter_node (project_id, parent_id, title, sort_order, status)"
                " VALUES (%s, %s, %s, 0, 'unwritten')",
                (project_id, parent_id, nd["title"]),
            )
            stats["added"] += 1
        _ensure_tree(project_id, nd.get("children"), nid, stats)


@router.post("/{project_id}/toc/apply")
def toc_apply(project_id: int, body: TocTextBody):
    """执行目录替换：删除期望外节点（含正文），再补齐期望树（同名保留思路与正文）。"""
    from app.services.section_service import delete_node_tree

    tree = _parse_toc_text(body.text)
    if not tree:
        # 解析不到任何章节 → 拒绝替换，防止用户误粘贴非目录文字把目录清空
        return {"ok": False, "error": "未能从文字中解析出任何章节，未执行任何替换"}
    plan = _plan(project_id, tree)
    deleted = deleted_with_body = 0
    for d in plan["deletions"]:
        if d["has_body"]:
            deleted_with_body += 1
        delete_node_tree(project_id, d["id"])
        deleted += 1
    stats = {"added": 0}
    _ensure_tree(project_id, tree, None, stats)
    return {"ok": True, "deleted": deleted, "deleted_with_body": deleted_with_body,
            "added": stats["added"], "node_count": _count_tree(tree)}


# ---------------------------------------------------------------- 按章生成（SSE）

class GenerateBody(BaseModel):
    project_id: int


@router.post("/content/{node_id}/generate")
def generate_content(node_id: int, body: GenerateBody):
    """单章生成正文（SSE 流式）：chapter_start → chapter_delta* → chapter_done。

    AI 内容直接流式写入编辑器（M4-7），对话区不参与；完成后落库待用户确认/回退。
    """
    from fastapi.responses import StreamingResponse

    from app.services.writer_service import generate_chapter_events

    def gen():
        try:
            for evt in generate_chapter_events(body.project_id, node_id):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'chapter_error', 'content': str(e)}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{project_id}/generate_outlines")
def start_generate_outlines(project_id: int):
    """对现有目录中尚未生成思路（unwritten）的节点批量生成编写思路（后台任务）。

    不重跑目录生成；进度写 task 表（与知识提炼共用 taskStatus 轮询）。
    """
    from app.services import compile_service
    from app.services.task_runner import task_runner

    task_id = task_runner.submit(
        "gen_outlines", project_id,
        compile_service.generate_pending_outlines, project_id,
    )
    return {"task_id": task_id}


class OutlineBody(BaseModel):
    outline: dict


@router.put("/nodes/{node_id}/outline")
def put_node_outline(node_id: int, body: OutlineBody):
    """手动保存章节编写思路（改 outline，unwritten 升 thought_ready）。"""
    from app.services.section_service import update_outline_by_id

    if not update_outline_by_id(0, node_id, body.outline):
        raise HTTPException(404, "章节不存在")
    return {"ok": True}
