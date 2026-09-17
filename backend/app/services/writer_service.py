"""正文生成服务（按章触发，方案 3.3 v0.2 决策）。

编排状态机到"确认思路"为止；正文生成独立于状态机：

- 单章：generate_chapter_events() 生成器，SSE 逐段推送 chapter_delta，
  前端流式写入编辑器（M4-7）；完成后落库（prev_content 供回退，ai_generated=1）。
- 批量：start_batch() 后台线程并发 BATCH_CONCURRENCY 循环消费 thought_ready 章节；
  stop_batch() 置停止标记——进行中的章节写完后停，未开始的跳过，已写内容保留。
- 批量进度写 compile_session（stage=writing, progress/total），复用前端编制进度轮询。

每章上下文 = chapter_node.outline（确认的思路，含 _page_ids）+ 已确认事实
+ 匹配知识页全文 + 同级章节标题（避免跨节重复，方案 3.10 第 2 条）。
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from app.agents.writer_agent import write_chapter, write_chapter_stream
from app.core.database import close_thread_conn, query, query_one
from app.services.matching import load_pages_by_ids
from app.services.section_service import persist_one

BATCH_CONCURRENCY = 10  # 批量生成并发度（模型 tpm 500 万，10 并发合适；限流由 llm 指数退避兜底）

# 批量停止标记（进程内；进程重启后批量不自动续，已写章节保留，重新发起即可）
_stop_flags: dict[int, threading.Event] = {}
_batch_running: set[int] = set()


def _confirmed_facts(project_id: int) -> list[dict]:
    """已确认事实（按当前选定标段过滤，统一走 fact_service.confirmed_facts）。"""
    from app.services.fact_service import confirmed_facts

    return confirmed_facts(project_id)


# 章节主题 → 注入的事实类别（不相关类别一律剔除，避免商务/评标类噪声干扰正文）
# 未列出的章默认只保留 结构/其他（这两类才是技术标准/工程概况类内容）
_FACT_KEEP_BY_THEME = {
    "技术标准": {"结构", "其他"},
    "工程概况": {"结构", "其他", "地质"},
    "线路概况": {"结构", "其他", "地质"},
    "编制依据": {"质量标准", "安全目标", "其他"},
    "编制范围": {"结构", "其他"},
    "工程特点": {"结构", "其他", "地质", "安全目标"},
    "控制工程": {"结构", "其他", "地质", "工期"},
    "施工方案": {"结构", "其他", "地质", "工期", "机械"},
    "施工组织": {"工期", "机械", "人员", "其他"},
    "资源配置": {"机械", "人员", "其他"},
    "管理措施": {"安全目标", "质量标准", "其他"},
}


def _filter_facts_for_chapter(facts: list[dict], title: str) -> list[dict]:
    """按本章主题过滤事实类别：只保留与该章相关的事实，避免评标/商务/资格审查类噪声
    干扰正文（它们可能诱导模型写套话或错误地把资格条件当技术标准）。"""
    keep = None
    for key, cats in _FACT_KEEP_BY_THEME.items():
        if key in title:
            keep = cats
            break
    if keep is None:
        keep = {"结构", "其他"}  # 默认只保留工程类
    return [f for f in facts if f.get("category") in keep]


def _parallel_take(tasks: dict[str, callable]) -> dict:
    """并发执行若干彼此独立的"取材"任务，返回 {名字: 结果}。

    - 每路丢进线程池独立线程跑（DB 为 thread-local，各自建连互不干扰）；
    - 任一路失败只告警并回退空值，不拖垮整章生成（与工法/文件片段一路的 fail-safe 一致）；
    - 线程用完即 close_thread_conn() 关连接，防并发建连堆积。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _run(label: str, fn: callable):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 单路取材失败不回退整章
            print(f"[单章取材] {label} 失败: {e}")
            return None
        finally:
            try:
                close_thread_conn()
            except Exception:  # noqa: BLE001
                pass

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures = {pool.submit(_run, k, fn): k for k, fn in tasks.items()}
        got = {}
        for fut in futures:
            got[futures[fut]] = fut.result()
    return got


def _used_summary(ctx: dict) -> dict:
    """本次生成的取材摘要（随正文落库，供页面展示"这一章用了哪些经验与素材"）。

    只存摘要不存全文：经验留内容片段，知识页/文件片段只留标题，避免正文行膨胀。
    """
    return {
        "experiences": [
            {"id": e.get("id"), "type": e.get("exp_type") or "经验",
             "chapter": e.get("chapter_type") or "",
             "content": (e.get("content") or "")[:400]}
            for e in (ctx.get("experiences") or [])
        ],
        "knowledge": [{"id": k.get("id"), "title": k.get("title") or ""}
                      for k in (ctx.get("knowledge") or [])],
        "files": [{"id": r.get("id"), "title": r.get("title") or ""}
                  for r in (ctx.get("file_refs") or [])],
        "methods": [{"id": m.get("id"), "name": m.get("name") or ""}
                    for m in (ctx.get("methods") or [])],
        "facts_n": len(ctx.get("facts") or []),
        "lot": bool(ctx.get("lot_info")),
    }


def _chapter_context(project_id: int, node_id: int) -> dict:
    """组装单章生成上下文。无思路则报错（前端引导先确认思路）。

    取材并发化（2026-09-07）：知识页/经验/工法/项目文件片段/标段/事实彼此独立，
    并行取材，把首字前延迟从"串行多路 LLM 求和"压到"最慢一路"。
    """
    node = query_one(
        "SELECT id, title, parent_id, outline, status FROM chapter_node"
        " WHERE id = %s AND project_id = %s",
        (node_id, project_id),
    )
    if not node:
        raise ValueError("章节不存在")
    outline = node.get("outline")
    if isinstance(outline, str):
        try:
            outline = json.loads(outline)
        except (TypeError, ValueError):
            outline = None
    if not outline:
        raise ValueError("该章节尚无编写思路，请先完成「编写思路」确认")

    siblings = [
        r["title"]
        for r in query(
            "SELECT title FROM chapter_node WHERE project_id = %s AND id != %s"
            " AND (parent_id <=> %s) ORDER BY sort_order",
            (project_id, node_id, node["parent_id"]),
        )
    ]
    parent_row = query_one("SELECT title FROM chapter_node WHERE id = %s",
                           (node["parent_id"],)) if node["parent_id"] else None
    parent_titles = [parent_row["title"]] if parent_row else []
    thinking = outline.get("thinking") or ""
    need = f"{node['title']}\n{thinking[:600]}"
    page_ids = outline.get("_page_ids") or []

    from app.services.experience_service import match_experiences
    from app.services.lot_service import selected_lot_info
    from app.services.method_index import match_methods
    from app.services.requirement_service import requirements_for_chapter_text

    # 先取经验（纯 DB+dice，无 LLM）：经验里若写了"参考某项目文档某章节"，
    # 其内容要参与下面的文件片段选章，故在并发取材前先算出来（A 任务）。
    experiences = match_experiences(node["title"], parent_titles)

    # 当前标段档案（本标段工程范围白名单：里程/主要构造物/大临）——生成时约束"只写本标段工点"
    from app.services.lot_profile_service import get_profile

    lot_profile = get_profile(project_id)

    # 六路独立取材并行（同一"编写需求"；工法/项目文件各含一次 LLM 选序）
    got = _parallel_take({
        "facts": lambda: _filter_facts_for_chapter(_confirmed_facts(project_id), node["title"]),
        "lot": lambda: selected_lot_info(project_id),
        "knowledge": lambda: load_pages_by_ids(page_ids),
        "methods": lambda: match_methods(need),
        # 经验内容并入取材需求：经验/思路里"参考某文件某章节"的指引可被选章 AI 命中
        "file_refs": lambda: _match_file_snippets(project_id, node["title"], outline, experiences, lot_profile),
        # 招标要求（评分办法/技术要求/编制要求/废标风险）作为编写依据
        "reqs": lambda: requirements_for_chapter_text(project_id, node["title"]),
    })
    return {
        "node": node,
        "outline": outline,
        "facts": got.get("facts") or [],
        "knowledge": got.get("knowledge") or [],
        "siblings": siblings,
        "experiences": experiences,
        "methods": got.get("methods") or [],
        "file_refs": got.get("file_refs") or [],
        "lot_info": got.get("lot") or "",
        "reqs": got.get("reqs") or "",
        # 当前标段档案（里程/构造物/大临）→ 生成白名单，约束正文只写本标段工点
        "lot_profile": lot_profile,
    }


def _match_file_snippets(project_id: int, title: str, outline: dict,
                         experiences: list[dict] | None = None,
                         lot_profile: dict | None = None) -> list[dict]:
    """按章取材（skill 式）：章节索引选取 → 完整原文加载（不切片不截断）。

    AI 先看「章节目录+概要+适用范围」选相关章节，再按行号取完整原文——
    大表/跨页条款不再被切碎。项目文件不再入向量库（2026-09-04 决策），
    索引为空/失败时直接返回空，不回退向量切片（2026-09-07 清理）。

    experiences（2026-09-08，A 任务）：已匹配经验并入取材需求——经验/编写思路里
    写的"本章应参考某项目文档的某章节"能驱动选章 AI 命中对应文件章节原文并注入，
    不再只是把一句"去参考XX"的文字丢给模型却拿不到对应内容。
    """
    try:
        from app.services import file_index
        from app.services.lot_scope_service import (
            clip_text_by_terms,
            filter_catalog_by_lot,
            parse_lots,
        )
        from app.services.lot_profile_service import whitelist_terms
        from app.services.lot_service import selected_lot_code

        catalog = file_index.get_catalog(project_id)
        # 按当前标段过滤候选：**明确属于其他标段**的章节不进候选（未判定/全线/multi 保留）
        catalog = filter_catalog_by_lot(catalog, selected_lot_code(project_id))
        if catalog:
            parts = [f"{title}"]
            exp_txt = "\n".join(
                f"- [{e.get('exp_type', '经验')}] {e.get('content', '')}"
                for e in (experiences or [])
            )
            if exp_txt:
                # 明确标注：经验里点名的"文件·章节"就是本次编写要参考的对象（最高优先命中）
                parts.append("用户经验指引（其中写到的\"参考某文件某章节\"须优先选中对应章节原文）：\n" + exp_txt[:1100])
            th = (outline.get('thinking') or '').strip()
            if th:
                parts.append("编写思路：\n" + th[:600])
            need = "\n".join(parts)
            ids = file_index._select_relevant(catalog, need, max_select=file_index.MAX_SELECT_INJECT)
            if not ids:
                return []
            sections = file_index.load_sections(catalog, ids)
            # "含多标段"的整表章节（附件1 工期表 / 附表7 / 图纸目录等）：按本标段词表**行级裁剪**，
            # 只留本标段相关行（与其上下文），避免把其他标段的工点/里程整段喂给正文生成
            multi_paths = {
                c["sec_path"] for c in catalog
                if "multi" in parse_lots(c.get("applies_lots"))
            }
            terms = whitelist_terms(lot_profile) if lot_profile else []
            out = []
            for s in sections:
                txt = s["text"]
                if terms and s.get("path") in multi_paths:
                    txt = clip_text_by_terms(txt, terms)
                out.append({"id": s.get("id"), "title": f"{s['file']} · {s['path']}", "chunk": txt})
            return out
    except Exception:  # noqa: BLE001 索引方式失败 → 该章不注入文件片段
        return []
    return []


# ---------------------------------------------------------------- 单章（SSE 流式）

def generate_chapter_events(project_id: int, node_id: int):
    """单章生成事件流：chapter_prepare → chapter_start → chapter_delta* → done / chapter_error。

    首字延迟优化（2026-09-07）：此前取材（工法/项目文件/知识页匹配）在 SSE 首事件前
    串行阻塞 10s+，前端空白。现先立即推 chapter_prepare（前端马上显示"整理素材中"），
    取材本身已并发化；随后 chapter_start 进入正文流式。

    客户端断开（切页面/关浏览器）触发 GeneratorExit：已生成的部分内容照样落库，
    不让几分钟的生成白跑——用户回来看到半成品，可重新生成或自行续写。
    """
    title = ""
    row = query_one("SELECT title FROM chapter_node WHERE id = %s AND project_id = %s",
                    (node_id, project_id))
    if row:
        title = row["title"]
    yield {"type": "chapter_prepare", "node_id": node_id, "title": title}
    try:
        ctx = _chapter_context(project_id, node_id)
    except ValueError as e:
        yield {"type": "chapter_error", "content": str(e)}
        return

    yield {"type": "chapter_start", "node_id": node_id, "title": ctx["node"]["title"]}
    # 调试开关：把实际发给模型的上下文随流返回（前端 console.log 排查）
    from app.core.config import settings as _cfg

    if getattr(_cfg, "gen_debug_context", False):
        from app.agents.writer_agent import WRITER_SYSTEM, _build_context

        try:
            dbg = _build_context(
                outline=ctx["outline"], facts=ctx["facts"], knowledge=ctx["knowledge"],
                siblings=ctx["siblings"], experiences=ctx["experiences"],
                methods=ctx["methods"], file_refs=ctx["file_refs"], lot_info=ctx["lot_info"],
                lot_profile=ctx.get("lot_profile"),
            )
            yield {"type": "debug_context", "node_id": node_id,
                   "content": f"{WRITER_SYSTEM}\n\n=== 用户上下文 ===\n\n{dbg}"}
        except Exception as e:  # noqa: BLE001
            yield {"type": "debug_context", "content": f"（上下文组装失败：{e}）"}
    buf: list[str] = []
    try:
        for chunk in write_chapter_stream(
            outline=ctx["outline"],
            facts=ctx["facts"],
            knowledge=ctx["knowledge"],
            siblings=ctx["siblings"],
            experiences=ctx["experiences"],
            methods=ctx["methods"],
            file_refs=ctx["file_refs"],
            lot_info=ctx["lot_info"],
            reqs=ctx["reqs"],
            lot_profile=ctx.get("lot_profile"),
        ):
            buf.append(chunk)
            yield {"type": "chapter_delta", "node_id": node_id, "content": chunk}
    except GeneratorExit:
        # SSE 客户端断开：落库部分内容后让生成器正常关闭
        if "".join(buf).strip():
            try:
                persist_one(project_id, node_id, "".join(buf), used=_used_summary(ctx))
                print(f"[单章生成] 客户端断开，已保留部分内容 node={node_id}（{len(''.join(buf))} 字）")
            except Exception:  # noqa: BLE001
                pass
        raise
    except Exception as e:  # noqa: BLE001
        yield {"type": "chapter_error", "node_id": node_id, "content": str(e)}
        return
    persist_one(project_id, node_id, "".join(buf), used=_used_summary(ctx))
    yield {"type": "chapter_done", "node_id": node_id}


# ---------------------------------------------------------------- 单章同步（对话工具调用）

def write_chapter_once(project_id: int, node_id: int, extra_requirements: str = "") -> dict:
    """单章同步生成（对话智能体的 write_chapter 工具用）：生成 + 落库 + 返回摘要。

    与流式单章生成共用同一套上下文组装与硬规则；对话区只回进度与摘要，
    正文直接进编辑器（方案 3.5 分工边界）。
    extra_requirements：对话中用户提出的本次编写特别要求（写入上下文最高优先级）。
    """
    ctx = _chapter_context(project_id, node_id)
    content = write_chapter(
        outline=ctx["outline"], facts=ctx["facts"],
        knowledge=ctx["knowledge"], siblings=ctx["siblings"],
        experiences=ctx["experiences"], methods=ctx["methods"],
        file_refs=ctx["file_refs"], lot_info=ctx["lot_info"],
        extra_requirements=extra_requirements,
        reqs=ctx["reqs"],
        lot_profile=ctx.get("lot_profile"),
    )
    persist_one(project_id, node_id, content, used=_used_summary(ctx))
    return {"ok": True, "node_id": node_id, "title": ctx["node"]["title"],
            "chars": len(content)}


# ---------------------------------------------------------------- 批量（可停止）

def _pending_nodes(project_id: int) -> list[dict]:
    return query(
        "SELECT id, title FROM chapter_node WHERE project_id = %s AND status = 'thought_ready'"
        " ORDER BY sort_order, id",
        (project_id,),
    )


def start_batch(project_id: int) -> dict:
    """批量生成：并发消费 thought_ready 章节。返回 {started, total}；进行中重复调用返回现状。"""
    from app.services import compile_service

    if project_id in _batch_running:
        return {"started": False, "message": "批量生成已在进行中"}
    nodes = _pending_nodes(project_id)
    if not nodes:
        return {"started": False, "message": "没有待生成的章节（需先确认编写思路）"}

    stop = threading.Event()
    _stop_flags[project_id] = stop
    _batch_running.add(project_id)
    total = len(nodes)
    compile_service.update_progress(project_id, stage="writing", progress=0, total=total)

    def _write(node: dict) -> bool:
        if stop.is_set():
            return False
        ctx = _chapter_context(project_id, node["id"])
        content = write_chapter(
            outline=ctx["outline"], facts=ctx["facts"],
            knowledge=ctx["knowledge"], siblings=ctx["siblings"],
            experiences=ctx["experiences"], methods=ctx["methods"],
            file_refs=ctx["file_refs"], lot_info=ctx["lot_info"],
            reqs=ctx["reqs"],
            lot_profile=ctx.get("lot_profile"),
        )
        persist_one(project_id, node["id"], content, used=_used_summary(ctx))
        return True

    def _run():
        done = 0
        try:
            with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as pool:
                futures = [pool.submit(_write, n) for n in nodes]
                for fut in futures:
                    try:
                        if fut.result():
                            done += 1
                    except Exception as e:  # noqa: BLE001 单章失败不中断批量
                        print(f"[批量生成] 章节失败: {e}")
                    compile_service.update_progress(project_id, stage="writing",
                                                    progress=done, total=total)
            stopped = stop.is_set()
            compile_service.update_progress(
                project_id,
                stage="outlines_ready" if stopped or done < total else "done",
                progress=done, total=total,
            )
            print(f"[批量生成] {'中途停止' if stopped else '完成'} {done}/{total}")
        finally:
            _batch_running.discard(project_id)
            _stop_flags.pop(project_id, None)

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "total": total}


def stop_batch(project_id: int) -> dict:
    """中途停止：进行中的章节写完后停，未开始的跳过。"""
    flag = _stop_flags.get(project_id)
    if not flag:
        return {"stopped": False, "message": "当前没有进行中的批量生成"}
    flag.set()
    return {"stopped": True}


def rewrite_chapters(project_id: int, node_ids: list[int], task_id: int | None = None) -> dict:
    """按**指定章节**批量重新生成正文（存量越标正文重写用）。

    - 现有 start_batch 只跑 status='thought_ready' 的章节，覆盖不到"已生成/已确认"的章节；
    - 覆盖式重写：旧正文自动存 prev_content（编辑器可回退），AI 标记与状态照常置位；
    - 进度写 compile_session（stage=writing，前端顶部进度条可见），task_id 非空时同时写 task 表。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.services import compile_service

    if not node_ids:
        return {"started": False, "message": "未指定章节"}
    marks = ",".join(["%s"] * len(node_ids))
    nodes = query(
        f"SELECT id, title FROM chapter_node WHERE project_id = %s AND id IN ({marks})"
        " ORDER BY sort_order, id",
        (project_id, *node_ids),
    )
    if not nodes:
        return {"started": False, "message": "未找到有效章节"}
    if project_id in _batch_running:
        return {"started": False, "message": "已有批量生成在进行中，请稍后再试"}

    stop = threading.Event()
    _stop_flags[project_id] = stop
    _batch_running.add(project_id)
    total = len(nodes)
    compile_service.update_progress(project_id, stage="writing", progress=0, total=total)

    def _prog(done: int, detail: str = "") -> None:
        compile_service.update_progress(project_id, stage="writing", progress=done, total=total)
        if task_id:
            try:
                execute("UPDATE task SET progress = %s, detail = %s, updated_at = NOW() WHERE id = %s",
                        (int(done / total * 100) if total else 0, detail[:200] or None, task_id))
            except Exception:  # noqa: BLE001 进度写失败不影响生成
                pass

    def _write(node: dict) -> bool:
        if stop.is_set():
            return False
        ctx = _chapter_context(project_id, node["id"])
        content = write_chapter(
            outline=ctx["outline"], facts=ctx["facts"],
            knowledge=ctx["knowledge"], siblings=ctx["siblings"],
            experiences=ctx["experiences"], methods=ctx["methods"],
            file_refs=ctx["file_refs"], lot_info=ctx["lot_info"],
            reqs=ctx["reqs"], lot_profile=ctx.get("lot_profile"),
        )
        persist_one(project_id, node["id"], content, used=_used_summary(ctx))
        return True

    def _run() -> None:
        done = 0
        try:
            with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as pool:
                futures = [pool.submit(_write, n) for n in nodes]
                for fut in as_completed(futures):
                    try:
                        if fut.result():
                            done += 1
                    except Exception as e:  # noqa: BLE001 单章失败不中断整批
                        print(f"[指定重写] 单章失败: {e}")
                    _prog(done, f"已重写 {done}/{total} 章")
            compile_service.update_progress(project_id, stage="done", progress=done, total=total)
            if task_id:
                execute("UPDATE task SET status = 'success', progress = 100, detail = %s,"
                        " updated_at = NOW() WHERE id = %s",
                        (f"已重写 {done}/{total} 章", task_id))
            print(f"[指定重写] 完成 {done}/{total}")
        except Exception as e:  # noqa: BLE001
            compile_service.update_progress(project_id, stage="failed", error=str(e))
            if task_id:
                execute("UPDATE task SET status = 'failed', detail = %s, updated_at = NOW() WHERE id = %s",
                        (str(e)[:500], task_id))
        finally:
            _batch_running.discard(project_id)
            _stop_flags.pop(project_id, None)

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "total": total, "titles": [n["title"] for n in nodes]}


def start_rewrite_task(task_id: int, project_id: int, node_ids: list[int]) -> None:
    """task_runner 入口：按指定章节重写（后台任务版，进度写 task 表）。"""
    rewrite_chapters(project_id, node_ids, task_id=task_id)
