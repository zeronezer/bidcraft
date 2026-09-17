"""标段服务：lot 表的读写（M1-2）。

- replace_lots：招标文件重解析后整体替换项目标段（保留已选状态的 best-effort 恢复）；
- select_lot：多标段选一个 / 单标段确认 / 传 None 表示"无标段直接进入"；
- 章节树的 lot_id 维度（按标段隔离目录）留待后续，当前选择只影响编制输入组装。
"""
import json

from app.core.database import execute, query, query_one


def replace_lots(project_id: int, lots: list[dict]) -> int:
    """整体替换项目标段，返回写入条数。按 lot_code 恢复之前的选中状态。"""
    prev = query("SELECT lot_code, selected FROM lot WHERE project_id = %s", (project_id,))
    prev_selected = {r["lot_code"] for r in prev if r.get("selected")}
    execute("DELETE FROM lot WHERE project_id = %s", (project_id,))
    n = 0
    for l in lots:
        execute(
            "INSERT INTO lot (project_id, lot_code, lot_name, price_limit, extra, selected)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                project_id, l["lot_code"], l["lot_name"], l.get("price_limit"),
                json.dumps({"scope": l.get("scope", "")}, ensure_ascii=False),
                1 if l["lot_code"] in prev_selected else 0,
            ),
        )
        n += 1
    return n


def list_lots(project_id: int) -> dict:
    rows = query(
        "SELECT id, lot_code, lot_name, price_limit, extra, selected"
        " FROM lot WHERE project_id = %s ORDER BY id",
        (project_id,),
    )
    for r in rows:
        extra = r.get("extra")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (TypeError, ValueError):
                extra = {}
        r["scope"] = (extra or {}).get("scope", "")
        r.pop("extra", None)
        r["selected"] = bool(r.get("selected"))
    selected = next((r["id"] for r in rows if r["selected"]), None)
    return {"lots": rows, "selected_id": selected}


def select_lot(project_id: int, lot_id: int | None) -> dict:
    """选定标段（lot_id=None 表示无标段直接进入，清空选择）。"""
    execute("UPDATE lot SET selected = 0 WHERE project_id = %s", (project_id,))
    if lot_id:
        execute(
            "UPDATE lot SET selected = 1 WHERE id = %s AND project_id = %s",
            (lot_id, project_id),
        )
    return list_lots(project_id)


def selected_lot_info(project_id: int) -> str:
    """当前选定标段的文本描述（编制输入组装用），未选定返回空串。"""
    rows = query(
        "SELECT lot_code, lot_name, extra FROM lot WHERE project_id = %s AND selected = 1",
        (project_id,),
    )
    if not rows:
        return ""
    r = rows[0]
    scope = ""
    try:
        scope = (json.loads(r["extra"]) if isinstance(r["extra"], str) else r["extra"] or {}).get("scope", "")
    except (TypeError, ValueError):
        pass
    return f"当前标段：{r['lot_code']} {r['lot_name']}" + (f"（{scope}）" if scope else "")


def selected_lot_code(project_id: int) -> str | None:
    """当前选定标段的代码（事实/图表/质检按标段过滤用），未选定返回 None。"""
    r = query_one(
        "SELECT lot_code FROM lot WHERE project_id = %s AND selected = 1 LIMIT 1",
        (project_id,),
    )
    return r["lot_code"] if r else None


# --------------------------------------------------- 标段识别（独立触发，2026-09-11）

# 招标文件解析完成前不能识别标段：识别吃的是解析产物（原文），不是上传的二进制。
TENDER_PARSED_SQL = (
    "SELECT id FROM project_file WHERE project_id = %s AND category = 'tender'"
    " AND status = 'parsed' LIMIT 1"
)


def has_parsed_tender(project_id: int) -> bool:
    """是否有已解析完成的招标文件（标段识别的**前置条件**，供接口与前端分支用）。"""
    return bool(query_one(TENDER_PARSED_SQL, (project_id,)))


def tender_source_text(project_id: int, max_chars: int = 80000) -> tuple[str, str] | None:
    """已解析招标文件的原文（供"识别标段划分"用），返回 (文本, 来源文件名)；无则 None。

    优先直读 full.md（最保真、一次读取）；**docx 走对象级切章、不落 full.md**
    （见 parse 改动），故回退按章节索引 content 拼回原文——两条路都必须留着，
    否则 docx 项目点"识别标段"会直接失败。
    """
    from pathlib import Path

    files = query(
        "SELECT id, file_name, parsed_path FROM project_file"
        " WHERE project_id = %s AND category = 'tender' AND status = 'parsed' ORDER BY id",
        (project_id,),
    )
    if not files:
        return None
    parts: list[str] = []
    names: list[str] = []
    total = 0
    for f in files:
        text = ""
        if f.get("parsed_path"):
            md = Path(f["parsed_path"]) / "full.md"
            if md.exists():
                try:
                    text = md.read_text(encoding="utf-8")
                except OSError as e:  # noqa: BLE001 读失败退回索引拼接
                    print(f"[标段识别] 读 full.md 失败（{f['file_name']}）: {e}")
        if not text.strip():
            secs = query(
                "SELECT content FROM file_section_index WHERE file_id = %s ORDER BY start_line",
                (f["id"],),
            )
            text = "\n".join(s["content"] for s in secs if s.get("content"))
        if not text.strip():
            continue
        parts.append(text[: max_chars - total])
        names.append(f["file_name"])
        total += len(parts[-1])
        if total >= max_chars:
            break
    if not parts:
        return None
    print(f"[标段识别] 原文来源：{'、'.join(names)}，共 {total} 字")
    return "\n\n".join(parts), "、".join(names)


def extract_lots_task(task_id: int, project_id: int) -> None:
    """后台任务：从已解析的招标文件**重新识别**标段划分（进度写 task 表）。

    走后台是因为要跑 LLM，且前端刷新页面不该中断（task 表可恢复轮询）。

    注意：本任务不做破坏性保护——`replace_lots` 会清空 lot 表（**标段档案就存在
    lot.profile 里，会连带清掉**）。保护由接口层负责：已有标段时必须显式 overwrite
    二次确认（见 api/projects.py 的 /lots/extract）。
    """
    from datetime import datetime

    from app.core.database import execute as _ex

    def _prog(pct: int, msg: str) -> None:
        _ex("UPDATE task SET progress = %s, detail = %s, updated_at = %s WHERE id = %s",
            (int(pct), msg, datetime.now(), task_id))

    _prog(10, "读取招标文件原文…")
    src = tender_source_text(project_id)
    if not src:
        raise RuntimeError("未找到已解析完成的招标文件，请先上传招标文件并等待解析完成")
    text, file_name = src
    _prog(35, f"AI 识别标段划分中（原文 {len(text)} 字）…")
    from app.agents.lot_agent import extract_lots

    result = extract_lots(text, file_name)
    lots = result.get("lots") or []
    _prog(80, f"写入标段（{len(lots)} 个）…")
    n = replace_lots(project_id, lots)
    _prog(100, f"完成：识别到 {n} 个标段，请选择要编制的标段"
        if n else "完成：未识别到标段划分——本工作台将基于全线编制")


def start_extract_lots(project_id: int) -> int:
    """启动"重新识别标段划分"后台任务，返回 task_id。"""
    from app.services.task_runner import task_runner

    return task_runner.submit("lot_extract", project_id, extract_lots_task, project_id)
