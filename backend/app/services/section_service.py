"""章节树与正文服务（M4-4 / M4-5 持久化层）。

- save_tree：目录确认后把章节树落库（幂等：项目已有树则跳过）
- persist_chapters：正文生成完成后按标题匹配落 section_content
- get_tree：从扁平行组装嵌套树
供 api/sections.py（HTTP）与 compile_service（流程节点）共同调用。
"""
import json

from app.core.database import execute, query, query_one

# 章节状态机：unwritten → thought_ready → generated → confirmed
NODE_STATUSES = ("unwritten", "thought_ready", "generated", "confirmed")


def get_tree(project_id: int, lot_id: int | None = None) -> list[dict]:
    """获取项目目录树（嵌套结构，含节点 id 与状态）。"""
    if lot_id:
        rows = query(
            "SELECT * FROM chapter_node WHERE project_id = %s AND (lot_id = %s OR lot_id IS NULL)"
            " ORDER BY sort_order, id", (project_id, lot_id),
        )
    else:
        rows = query(
            "SELECT * FROM chapter_node WHERE project_id = %s ORDER BY sort_order, id",
            (project_id,),
        )
    nodes = {}
    for r in rows:
        o = r.get("outline")
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except (TypeError, ValueError):
                o = None
        nodes[r["id"]] = {
            "id": r["id"], "title": r["title"], "status": r["status"],
            "outline": o or None,   # 编写思路（thought_ready 后有），前端选中章节展示
            "children": [],
        }
    roots = []
    for r in rows:
        node = nodes[r["id"]]
        if r["parent_id"] and r["parent_id"] in nodes:
            nodes[r["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    return roots


def tree_empty(project_id: int) -> bool:
    return not query_one("SELECT id FROM chapter_node WHERE project_id = %s LIMIT 1", (project_id,))


def clear_tree(project_id: int) -> int:
    """清空项目目录树**连同各章正文**（返回删除的节点数）。

    用于「重新生成目录」：旧目录、编写思路与已写正文全部丢弃，属不可恢复操作
    （前端已在点击时二次确认并明确告知清空）。
    """
    rows = query("SELECT id FROM chapter_node WHERE project_id = %s", (project_id,))
    ids = [r["id"] for r in rows]
    if ids:
        marks = ",".join(["%s"] * len(ids))
        execute(f"DELETE FROM section_content WHERE chapter_node_id IN ({marks})", tuple(ids))
    execute("DELETE FROM chapter_node WHERE project_id = %s", (project_id,))
    if ids:
        print(f"[目录清空] 项目 {project_id} 删除 {len(ids)} 个节点及其正文")
    return len(ids)


def save_tree(project_id: int, chapters: list[dict], lot_id: int | None = None) -> int:
    """把嵌套章节树落库，返回写入节点数。已有树时跳过（用户编辑以库为准）。"""
    if tree_empty(project_id):
        return _insert_nodes(project_id, chapters, None, lot_id)
    return 0


def _insert_nodes(project_id: int, chapters: list[dict], parent_id: int | None, lot_id: int | None) -> int:
    count = 0
    for idx, ch in enumerate(chapters):
        nid = execute(
            "INSERT INTO chapter_node (project_id, lot_id, parent_id, title, sort_order, status)"
            " VALUES (%s, %s, %s, %s, %s, 'unwritten')",
            (project_id, lot_id, parent_id, ch.get("title", ""), idx),
        )
        count += 1
        kids = ch.get("children") or []
        if kids:
            count += _insert_nodes(project_id, kids, nid, lot_id)
    return count


def find_node_by_title(project_id: int, title: str) -> dict | None:
    return query_one(
        "SELECT * FROM chapter_node WHERE project_id = %s AND title = %s ORDER BY id LIMIT 1",
        (project_id, title),
    )


def save_outline(project_id: int, title: str, outline: dict) -> bool:
    """思路确认后落库：outline JSON 存 chapter_node.outline，状态升 thought_ready。

    已 generated/confirmed 的节点不回退状态（保留用户进度），只更新思路。
    """
    if not title:
        return False
    node = find_node_by_title(project_id, title)
    if not node:
        return False
    execute(
        "UPDATE chapter_node SET outline = %s,"
        " status = IF(status = 'unwritten', 'thought_ready', status) WHERE id = %s",
        (json.dumps(outline, ensure_ascii=False), node["id"]),
    )
    return True


def persist_one(project_id: int, node_id: int, body_md: str, used: dict | None = None) -> bool:
    """按节点 id 写入单章正文（按章生成/改写共用）。存 prev_content 供回退，置 ai_generated=1。

    used：本次生成取材摘要（经验/知识页/项目文件片段/工法/事实条数），随正文一起落库，
    供页面展示"这一章用了哪些经验与素材"；不传则不写该键（如用户手工编辑保存后即无）。
    """
    node = query_one(
        "SELECT id FROM chapter_node WHERE id = %s AND project_id = %s", (node_id, project_id)
    )
    if not node:
        return False
    ex = query_one("SELECT content FROM section_content WHERE chapter_node_id = %s", (node_id,))
    prev_body, prev_format = "", "md"
    if ex and ex.get("content"):
        try:
            old = json.loads(ex["content"]) if isinstance(ex["content"], str) else ex["content"]
            prev_body, prev_format = old.get("body", ""), old.get("format", "md")
        except (TypeError, ValueError):
            pass
    payload_obj = {"format": "md", "body": body_md, "prev_content": prev_body, "prev_format": prev_format}
    if used:
        payload_obj["used"] = used
    payload = json.dumps(payload_obj, ensure_ascii=False)
    if ex:
        execute(
            "UPDATE section_content SET content = %s, ai_generated = 1 WHERE chapter_node_id = %s",
            (payload, node_id),
        )
    else:
        execute(
            "INSERT INTO section_content (chapter_node_id, content, ai_generated) VALUES (%s, %s, 1)",
            (node_id, payload),
        )
    execute(
        "UPDATE chapter_node SET status = IF(status = 'confirmed', 'confirmed', 'generated') WHERE id = %s",
        (node_id,),
    )
    return True


def persist_chapters(project_id: int, chapters: list[dict]) -> int:
    """把生成结果 [{title, content, ...}] 按标题匹配写入 section_content。

    - 找不到同名节点时按标题模糊匹配（去掉 第X节/章 前缀、包含关系）
    - content 存 {"format":"md","body":...}，ai_generated 置 1（待用户确认）
    """
    import re

    saved = 0
    for ch in chapters:
        title = (ch.get("title") or "").strip()
        if not title:
            continue
        node = find_node_by_title(project_id, title)
        if not node:
            stripped = re.sub(r"^第[一二三四五六七八九十百]+[章节]\s*", "", title)
            rows = query(
                "SELECT * FROM chapter_node WHERE project_id = %s AND (title LIKE %s OR %s LIKE CONCAT('%', title, '%'))"
                " ORDER BY id LIMIT 1", (project_id, f"%{stripped}%", stripped),
            )
            node = rows[0] if rows else None
        if not node:
            continue
        ex = query_one(
            "SELECT content FROM section_content WHERE chapter_node_id = %s", (node["id"],)
        )
        prev_body, prev_format = "", "md"
        if ex and ex.get("content"):
            try:
                old = json.loads(ex["content"]) if isinstance(ex["content"], str) else ex["content"]
                prev_body, prev_format = old.get("body", ""), old.get("format", "md")
            except (TypeError, ValueError):
                pass
        content = {"format": "md", "body": ch.get("content", ""),
                   "prev_content": prev_body, "prev_format": prev_format}
        if ex:
            execute(
                "UPDATE section_content SET content = %s, ai_generated = 1 WHERE chapter_node_id = %s",
                (json.dumps(content, ensure_ascii=False), node["id"]),
            )
        else:
            execute(
                "INSERT INTO section_content (chapter_node_id, content, ai_generated) VALUES (%s, %s, 1)",
                (node["id"], json.dumps(content, ensure_ascii=False)),
            )
        if node["status"] != "confirmed":
            execute("UPDATE chapter_node SET status = 'generated' WHERE id = %s", (node["id"],))
        saved += 1
    return saved


# ---------------------------------------------------------------- 目录结构编辑（对话 edit_toc 用，只改树不碰正文）

def rename_node_title(project_id: int, node_id: int, title: str) -> bool:
    """重命名目录节点（仅改标题，不动正文）。"""
    if not title or not title.strip():
        return False
    cur = query_one(
        "SELECT id FROM chapter_node WHERE id = %s AND project_id = %s", (node_id, project_id)
    )
    if not cur:
        return False
    execute("UPDATE chapter_node SET title = %s WHERE id = %s", (title.strip(), node_id))
    return True


def add_node(project_id: int, title: str, parent_id: int | None = None, sort_order: int | None = None) -> int:
    """新增目录节点（未写状态），返回 node id。"""
    if parent_id:
        p = query_one(
            "SELECT id FROM chapter_node WHERE id = %s AND project_id = %s", (parent_id, project_id)
        )
        if not p:
            raise ValueError("父节点不存在")
    if sort_order is None:
        row = query_one(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 m FROM chapter_node WHERE project_id = %s"
            " AND parent_id <=> %s",
            (project_id, parent_id),
        )
        sort_order = row["m"] if row else 0
    return execute(
        "INSERT INTO chapter_node (project_id, parent_id, title, sort_order, status)"
        " VALUES (%s, %s, %s, %s, 'unwritten')",
        (project_id, parent_id, title.strip() or "新章节", sort_order),
    )


def _descendant_ids(node_id: int, acc: list[int]) -> None:
    acc.append(node_id)
    for r in query("SELECT id FROM chapter_node WHERE parent_id = %s", (node_id,)):
        _descendant_ids(r["id"], acc)


def delete_node_tree(project_id: int, node_id: int) -> int:
    """删除目录节点及其全部子节点与正文，返回删除节点数。"""
    cur = query_one(
        "SELECT id FROM chapter_node WHERE id = %s AND project_id = %s", (node_id, project_id)
    )
    if not cur:
        raise ValueError("章节不存在")
    ids: list[int] = []
    _descendant_ids(node_id, ids)
    for nid in ids:
        execute("DELETE FROM section_content WHERE chapter_node_id = %s", (nid,))
    execute(
        f"DELETE FROM chapter_node WHERE id IN ({','.join(map(str, ids))})"
    )
    return len(ids)


def update_outline_by_id(project_id: int, node_id: int, outline: dict) -> bool:
    """手动编辑章节编写思路并保存（改写 outline；unwritten 升 thought_ready）。"""
    cur = query_one(
        "SELECT id FROM chapter_node WHERE id = %s AND project_id = %s", (node_id, project_id)
    )
    if not cur:
        return False
    execute(
        "UPDATE chapter_node SET outline = %s,"
        " status = IF(status = 'unwritten', 'thought_ready', status) WHERE id = %s",
        (json.dumps(outline, ensure_ascii=False), node_id),
    )
    return True
