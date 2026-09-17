"""编制会话管理：LangGraph 状态机的异步封装，状态持久化到 compile_session 表。

- start：后台线程跑目录生成，立即返回；跑到 interrupt（确认目录）挂起，
  图状态存 MySQL 检查点（PyMySQLSaver），会话状态存 compile_session 表；
- status：前端轮询，从 DB 读——进程重启不丢，配合配置 compile_auto_resume 可自动续跑；
- resume：用户确认后按 thread_id 从检查点恢复，直到下一个 interrupt 或完成。

自动恢复策略（auto_resume，启动时由 main.py 按配置调用）：
- 停在 confirm_toc / confirm_outline 的会话：不自动跑（人工把关节点不自动跳过），
  状态本就在 DB 里，前端轮询即可继续显示"等待确认"；
- 停在 generating_toc / generating_outline（跑到一半进程没了）的：从检查点自动续跑。
"""
import json
import threading
import time
from app.agents.compile_graph import new_compile, resume_compile, resume_interrupted
from app.core.database import execute, query, query_one

# 阶段枚举
STAGE_TOC = "generating_toc"           # 生成目录中
STAGE_CONFIRM_TOC = "confirm_toc"      # 等待确认目录
STAGE_OUTLINE = "generating_outline"   # 生成思路中
STAGE_CONFIRM_OUTLINE = "confirm_outline"  # 等待确认思路
STAGE_READY = "outlines_ready"         # 思路已确认，可按章生成正文（正文走 writer_service）
STAGE_FAILED = "failed"

_WORKING_STAGES = (STAGE_TOC, STAGE_OUTLINE)

# 本进程当前活跃的编制线程（project_id 集）。用于区分"真在进行中"与"僵尸会话"
# （进程重启/线程崩溃后 compile_session 残留 running=1 却无线程在跑）。
_active: set[int] = set()


def is_active(project_id: int) -> bool:
    return project_id in _active


def assemble_inputs(project_id: int) -> dict:
    """编制输入后端组装（前端只传 project_id，其余从 DB 取）：

    - project_info：项目名/工程类型/描述 + 已上传文件清单 + 当前标段 + **本标段工程构成**
      （目录与正文据此裁剪：本标段没有的工程类型不生成章节）；
    - facts：全局事实，**按当前标段过滤**（本标段专属 + 全线共性注入，明确属其他标段的不注入）。
      （2026-09-10 起事实改为"抽取即 confirmed"，已无 pending 环节，故这里取全部生效事实。）
    - professions：从项目描述与事实中扫描专业标签词（路基/桥梁/隧道…）。
    """
    from app.services.lot_service import selected_lot_info
    from app.services.matching import PROFESSION_TAGS

    proj = query_one("SELECT * FROM project WHERE id = %s", (project_id,)) or {}
    files = query(
        "SELECT file_name, category FROM project_file WHERE project_id = %s ORDER BY id",
        (project_id,),
    )
    file_lines = "、".join(f["file_name"] for f in files) or "（未上传）"
    lot_info = selected_lot_info(project_id)
    # 标段档案摘要（本标段工程构成）→ 目录生成据此裁剪：本标段没有的工程类型不生成章节
    lot_detail = ""
    try:
        from app.services.lot_profile_service import lot_brief

        lot_detail = lot_brief(project_id)
    except Exception:  # noqa: BLE001 档案缺失不影响编制
        lot_detail = ""
    project_info = (
        f"项目名称：{proj.get('name', '')}\n"
        f"工程类型：{proj.get('project_type', '') or '未指定'}\n"
        + (lot_info + "\n" if lot_info else "")
        + (f"【本标段工程构成（目录与正文必须按此裁剪）】\n{lot_detail}\n" if lot_detail else "")
        + f"项目描述：{proj.get('description', '') or '无'}\n"
        f"已上传资料：{file_lines}"
    )

    fact_rows = query(
        "SELECT category, fact_key, fact_value, unit FROM global_fact"
        " WHERE project_id = %s AND status = 'confirmed' ORDER BY category, id",
        (project_id,),
    )
    from app.services.fact_service import confirmed_facts

    facts = confirmed_facts(project_id)

    scan_text = (proj.get("description") or "") + " " + " ".join(
        f"{r['fact_key']}{r['fact_value']}" for r in fact_rows
    )
    professions = [t for t in PROFESSION_TAGS if t in scan_text]
    if proj.get("project_type") and proj["project_type"] not in professions:
        professions.insert(0, proj["project_type"])

    # 招标要求（评分办法/技术要求 + 建议章节）注入目录生成
    from app.services.requirement_service import requirements_text

    # 招标文件规定的施组目录（project.toc_hint，目录生成最高优先级）
    toc_hint = proj.get("toc_hint")
    if isinstance(toc_hint, str):
        try:
            toc_hint = json.loads(toc_hint)
        except (TypeError, ValueError):
            toc_hint = None

    return {
        "project_info": project_info,
        "facts": facts,
        "professions": professions,
        "tender_requirements": requirements_text(project_id),
        "tender_toc": toc_hint or {},
    }


# ---------------------------------------------------------------- 会话状态（DB）

def _save(project_id: int, **fields) -> None:
    """upsert compile_session 行。fields 值里的 dict/list 自动 JSON 序列化。

    用 UPDATE-first（行由 start_compile 先建，绝大多数调用都是更新）；
    仅当行不存在（极少）才 INSERT，此时兜底带上 thread_id，避免
    NOT NULL 无默认导致 1364。
    """
    from app.core.database import get_conn

    if not fields:
        return
    ser = {}
    for k, v in fields.items():
        ser[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    conn = get_conn()
    cur = conn.cursor()
    try:
        # pymysql rowcount 对 UPDATE 返回的是"被改变行数"（值不变则 0），不能据此判行是否存在，
        # 先 EXISTS 判断走 UPDATE 还是 INSERT 兜底
        cur.execute("SELECT 1 FROM compile_session WHERE project_id = %s", (project_id,))
        exists = cur.fetchone() is not None
        if exists:
            sets = ", ".join(f"{k} = %s" for k in ser)
            cur.execute(
                f"UPDATE compile_session SET {sets} WHERE project_id = %s",
                (*ser.values(), project_id),
            )
        else:
            tid = ser.pop("thread_id", None) or f"project-{project_id}-{int(time.time() * 1000)}"
            cols = ["project_id", "thread_id", *ser.keys()]
            marks = ", ".join(["%s"] * len(cols))
            cur.execute(
                f"INSERT INTO compile_session ({', '.join(cols)}) VALUES ({marks})",
                (project_id, tid, *ser.values()),
            )
    finally:
        cur.close()


def update_progress(project_id: int, stage: str | None = None,
                    progress: int | None = None, total: int | None = None) -> None:
    """供 writer_service 等外部模块更新进度（批量生成复用编制会话的进度通道）。"""
    fields: dict = {}
    if stage is not None:
        fields["stage"] = stage
    if progress is not None:
        fields["progress"] = progress
    if total is not None:
        fields["total"] = total
    if fields:
        _save(project_id, **fields)


def save_partial_toc(project_id: int, toc: dict) -> None:
    """目录流式：保存生成中的部分目录快照（前端轮询实时渲染"长出来"的目录）。"""
    _save(project_id, partial_json=toc)


def get_status(project_id: int) -> dict:
    row = query_one("SELECT * FROM compile_session WHERE project_id = %s", (project_id,))
    if not row:
        return {"stage": "idle", "progress": 0, "total": 0, "interrupt": None,
                "result": None, "error": None, "partial": None}

    def _json(v):
        if isinstance(v, str) and v:
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return None
        return v

    return {
        "stage": row["stage"],
        "progress": row["progress"] or 0,
        "total": row["total"] or 0,
        "interrupt": _json(row.get("interrupt_json")),
        "result": _json(row.get("result_json")),
        "partial": _json(row.get("partial_json")),
        "error": row.get("error"),
        "updated_at": str(row.get("updated_at")),
    }


# ---------------------------------------------------------------- 流程控制

def _finish_from_result(project_id: int, r: dict) -> None:
    """根据一次 invoke 结果落会话状态。

    2026-09-10 用户拍板：**除章节正文外移除人工确认**——目录/思路生成完不再停在
    confirm_toc / confirm_outline 等人点确认，而是自动确认后继续往下跑（无人值守）。
    """
    if r["interrupted"]:
        it = r["interrupt"]
        if isinstance(it, dict) and it.get("type") == "confirm_outlines":
            _save(project_id, stage=STAGE_CONFIRM_OUTLINE, interrupt_json=it, running=0)
            _auto_confirm(project_id, {"action": "confirm", "outlines": it.get("outlines")})
        else:
            toc = it.get("toc") if isinstance(it, dict) else it
            _save(project_id, stage=STAGE_CONFIRM_TOC,
                  interrupt_json={"type": "confirm_toc", "toc": toc}, running=0)
            _auto_confirm(project_id, {"action": "confirm", "toc": toc})
    else:
        # 状态机跑完（思路已确认落库），进入"可按章生成"
        _save(project_id, stage=STAGE_READY, interrupt_json=None,
              result_json={"ready": True}, running=0)


def _auto_confirm(project_id: int, approval: dict) -> None:
    """自动确认（代替用户在页面点"确认目录/确认思路"）。

    状态机步进有限（目录 → 思路 → 就绪），每次自动确认都会推进一段，不会原地打转；
    万一自动确认本身失败，只记录日志——会话仍停在 confirm_* 阶段，用户可手动确认。
    """
    try:
        print(f"[自动确认] 项目 {project_id} 自动通过确认节点：{approval.get('action')}")
        resume(project_id, approval)
    except Exception as e:  # noqa: BLE001 自动确认失败不阻断（退回人工确认）
        print(f"[自动确认失败] 项目 {project_id}: {e}")


def start_compile(project_id: int, project_info: str, professions: list[str],
                  tender_requirements: str = "", facts: list[dict] | None = None,
                  knowledge_pages: dict | None = None, tender_toc: dict | None = None) -> dict:
    """启动编制流程（后台线程），立即返回。每次新 run 换新 thread_id，避免旧检查点残留。"""
    thread_id = f"project-{project_id}-{int(time.time() * 1000)}"
    _save(project_id, thread_id=thread_id, stage=STAGE_TOC, progress=0, total=0,
          interrupt_json=None, result_json=None, error=None, running=1)

    def _run(project_id=project_id):
        _active.add(project_id)
        try:
            r = new_compile(thread_id, project_id, project_info, professions,
                            tender_requirements, facts or [], tender_toc or {})
            _finish_from_result(project_id, r)
        except Exception as e:  # noqa: BLE001
            _save(project_id, stage=STAGE_FAILED, error=str(e), running=0)
        finally:
            _active.discard(project_id)

    threading.Thread(target=_run, daemon=True).start()
    return get_status(project_id)


def resume(project_id: int, approval: dict) -> dict:
    """用户确认后，后台线程继续跑。approval 如 {"action":"confirm", "toc": {...}}。"""
    row = query_one("SELECT thread_id, stage FROM compile_session WHERE project_id = %s",
                    (project_id,))
    if not row:
        return get_status(project_id)
    thread_id = row["thread_id"]
    if row["stage"] == STAGE_CONFIRM_TOC:
        _save(project_id, stage=STAGE_OUTLINE, running=1)

    def _run():
        _active.add(project_id)
        try:
            r = resume_compile(thread_id, approval)
            _finish_from_result(project_id, r)
        except Exception as e:  # noqa: BLE001
            _save(project_id, stage=STAGE_FAILED, error=str(e), running=0)
        finally:
            _active.discard(project_id)

    threading.Thread(target=_run, daemon=True).start()
    return get_status(project_id)


def auto_resume() -> int:
    """启动时续跑中断会话（配置 compile_auto_resume=true 时由 main.py 调用）。

    confirm_* 阶段不处理（等人工）；generating_* 阶段从 MySQL 检查点续跑。
    返回续跑的会话数。
    """
    rows = query(
        "SELECT project_id, thread_id FROM compile_session WHERE stage IN (%s, %s)",
        _WORKING_STAGES,
    )
    for row in rows:
        pid, tid = row["project_id"], row["thread_id"]

        def _run(project_id=pid, thread_id=tid):
            try:
                print(f"[编制恢复] 项目 {project_id} 从检查点续跑（thread {thread_id}）")
                r = resume_interrupted(thread_id)
                _finish_from_result(project_id, r)
            except Exception as e:  # noqa: BLE001
                _save(project_id, stage=STAGE_FAILED, error=f"恢复失败: {e}", running=0)

        threading.Thread(target=_run, daemon=True).start()
    return len(rows)


def generate_pending_outlines(task_id: int, project_id: int) -> int:
    """对现有目录树中尚未有思路（unwritten）的节点批量生成编写思路并落库。

    目录已存在时不重跑目录生成，只补思路（tools/generate_outlines.py 的异步化）。
    后台线程执行，进度写入 task 表。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.agents.orchestrate_agent import generate_outline
    from app.services.experience_service import match_experiences
    from app.services.fact_service import confirmed_facts
    from app.services.lot_profile_service import lot_brief
    from app.services.matching import load_page_summaries, match_pages
    from app.services.requirement_service import requirements_for_chapter_text
    from app.services.section_service import save_outline

    # 本标段工程构成：思路只能围绕本标段实际包含的工程展开
    lot_text = lot_brief(project_id)

    def _prog(p, detail=""):
        try:
            execute("UPDATE task SET progress=%s, detail=%s, updated_at=NOW() WHERE id=%s",
                    (p, detail[:200] or None, task_id))
        except Exception:
            pass

    rows = query("SELECT id, title, parent_id, status FROM chapter_node"
                 " WHERE project_id = %s ORDER BY sort_order, id", (project_id,))
    todo = [r for r in rows if r["status"] == "unwritten"]
    _prog(5, "待生成思路 %d 章" % len(todo))
    if not todo:
        _prog(100, "全部节点已有思路")
        return 0

    title_of = {r["id"]: r["title"] for r in rows}
    facts = confirmed_facts(project_id)
    summaries = load_page_summaries()

    def _one(r):
        parent = title_of.get(r["parent_id"]) if r["parent_id"] else None
        path = [parent] if parent else []
        pages = match_pages(r["title"], path, summaries)
        outline = generate_outline(
            chapter_title=r["title"], chapter_type="",
            project_facts=facts, knowledge_pages=pages,
            experiences=match_experiences(r["title"], path),
            lot_brief=lot_text,
        )
        outline["_toc_title"] = r["title"]
        outline["_parent"] = parent or ""
        outline["_page_ids"] = [pp["id"] for pp in pages]
        save_outline(project_id, r["title"], outline)

    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_one, r): r for r in todo}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                print("[补思路] 失败 %s: %s" % (futs[fut]["title"][:30], e))
            done += 1
            if done % 10 == 0 or done == len(todo):
                _prog(5 + int(done / len(todo) * 90), "编写思路 %d/%d" % (done, len(todo)))
    _prog(100, "完成：为 %d 章生成编写思路" % done)
    return done


def regenerate_node_outline(task_id: int, project_id: int, node_id: int) -> dict:
    """单章重新生成编写思路（后台任务，进度写 task 表）。

    供章节「重新生成」按钮 + 刷新恢复使用：match_pages → generate_outline → update_outline_by_id。
    进度：5 检索素材 / 40 生成中 / 90 落库 / 100 完成；失败把任务置 failed + detail。
    """
    from app.agents.orchestrate_agent import generate_outline
    from app.services.experience_service import match_experiences
    from app.services.fact_service import confirmed_facts
    from app.services.lot_profile_service import lot_brief
    from app.services.matching import load_page_summaries, match_pages
    from app.services.requirement_service import requirements_for_chapter_text
    from app.services.section_service import update_outline_by_id

    lot_text = lot_brief(project_id)  # 本标段工程构成（思路不得写本标段没有的工程）

    def _prog(p, detail=""):
        try:
            execute("UPDATE task SET progress=%s, detail=%s, updated_at=NOW() WHERE id=%s",
                    (p, (detail or "")[:200] or None, task_id))
        except Exception:  # noqa: BLE001
            pass

    node = query_one("SELECT id, title, parent_id, status FROM chapter_node"
                     " WHERE id = %s AND project_id = %s", (node_id, project_id))
    if not node:
        _prog(100, "章节不存在")
        return {"ok": False, "error": "章节不存在"}

    title = node["title"]
    parent_title = None
    if node.get("parent_id"):
        pr = query_one("SELECT title FROM chapter_node WHERE id = %s", (node["parent_id"],))
        parent_title = pr["title"] if pr else None
    path = [parent_title] if parent_title else []

    try:
        _prog(5, "检索本章知识页/经验素材…")
        pages = match_pages(title, path, load_page_summaries())
        _prog(40, "AI 生成编写思路中…")
        outline = generate_outline(
            chapter_title=title, chapter_type="",
            project_facts=confirmed_facts(project_id), knowledge_pages=pages,
            experiences=match_experiences(title, path),
            reqs_text=requirements_for_chapter_text(project_id, title),
            lot_brief=lot_text,
        )
        outline["_toc_title"] = title
        outline["_parent"] = parent_title or ""
        outline["_page_ids"] = [p["id"] for p in pages]
        _prog(90, "落库…")
        if not update_outline_by_id(project_id, node_id, outline):
            _prog(100, "思路保存失败")
            return {"ok": False, "error": "思路保存失败"}
    except Exception as e:  # noqa: BLE001 失败把任务置 failed
        _prog(100, f"思路生成失败：{e}")
        return {"ok": False, "error": f"思路生成失败：{e}"}

    _prog(100, f"「{title}」编写思路已重新生成")
    return {"ok": True, "node_id": node_id, "title": title,
            "thinking": (outline.get("thinking") or "")[:200],
            "key_points": (outline.get("key_points") or [])[:5],
            "need_table": bool(outline.get("need_table")),
            "need_image": bool(outline.get("need_image")),
            "page_ids": outline.get("_page_ids") or []}
