"""LangGraph 编排状态机：目录生成 → 用户确认 → 编写思路 → 用户确认（正文按章触发）。

自主度设计（方案 3.4）：
- 业务里程碑（目录确认/思路确认）用 interrupt() 写死人工把关；
- 环节内部由 LLM 自主决定（目录装配、思路生成、正文取材）。

持久化（方案 3.6）：checkpointer 用 PyMySQLSaver（MySQL，与业务库同库）。
pymysql 连接非线程安全，故每次 invoke 在调用线程内新建连接与 graph 实例，
interrupt/resume 靠 thread_id 在 MySQL 检查点续接，进程重启不丢状态。

正文生成不在本状态机内（2026-09-03 决策）：确认思路后流程结束，
正文由 services/writer_service 按章触发（单章流式 / 批量并发可停止）。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypedDict

import pymysql
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.orchestrate_agent import generate_outline
from app.core.config import settings

# 思路生成并发度（模型 tpm 500 万，20 并发仍有余量；限流由 llm 指数退避兜底）
OUTLINE_CONCURRENCY = 20


class CompileState(TypedDict):
    project_id: int
    project_info: str              # 项目名/工程类型/标段概况
    professions: list[str]         # 本项目涉及的专业
    tender_requirements: str       # 招标要求与评分办法
    tender_toc: dict               # 招标文件规定的施组目录（有则最高优先级，方案 v0.2）
    facts: list[dict]              # 全局事实子集（已确认）

    toc: dict                      # 生成的目录
    toc_confirmed: bool            # 目录是否确认
    outlines: list[dict]           # 逐章编写思路（含 _page_ids 匹配的知识页）
    outline_confirmed: bool


# ---- 节点 ----

def _flatten_toc(toc: dict) -> list[dict]:
    """把章→节树展平为章节列表，每项 {title, level, parent?}。"""
    flat = []
    for ch in toc.get("chapters", []):
        flat.append({"title": ch["title"], "level": 1})
        for sub in ch.get("children", []):
            flat.append({"title": sub["title"], "level": 2, "parent": ch["title"]})
    return flat


def node_generate_toc(state: CompileState) -> dict:
    """两阶段流式生成目录：先出章骨架（秒级可见），再并发逐章展开子节，
    每章完成即写 partial 快照——前端目录栏实时"长出来"，用户可看生成进度。
    生成完成后**自动落库**（v0.2 决策：不设确认环节，用户在目录树上自行调整）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.agents.orchestrate_agent import generate_toc_children, generate_toc_titles
    from app.services import compile_service

    project_id = state["project_id"]
    titles = generate_toc_titles(
        state["project_info"], state["professions"],
        state["tender_requirements"], state.get("tender_toc") or None,
    )
    if not titles:
        raise ValueError("目录章骨架生成失败（空结果）")
    # 阶段一快照：章骨架立即可见
    toc = {"chapters": [{"title": t["title"], "children": []} for t in titles]}
    compile_service.update_progress(project_id, total=len(titles))
    compile_service.save_partial_toc(project_id, toc)

    # 阶段二：并发逐章展开子节，每章完成即推快照
    def _children(ch):
        ch["children"] = generate_toc_children(
            ch["title"], state["project_info"], state["professions"],
            state["tender_requirements"],
        )
        return ch

    with ThreadPoolExecutor(max_workers=OUTLINE_CONCURRENCY) as pool:
        futures = {pool.submit(_children, ch): ch for ch in toc["chapters"]}
        done = 0
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001 单章子节失败不阻断（保留空子节）
                print(f"[目录] 子节生成失败: {e}")
            done += 1
            compile_service.update_progress(project_id, progress=done)
            compile_service.save_partial_toc(project_id, toc)

    # 落库：**先清空旧目录树（连同已写正文与思路）再写入新目录**。
    # 2026-09-10 用户拍板：「重新生成目录」＝真正的重来，前端已二次确认并明确告知
    # "已编写内容将全部清空"。此前 save_tree 在已有目录时直接跳过（写入 0 个节点），
    # 导致新目录不生效、思路却按新目录跑一遍，出现"目录来回切换"的怪象。
    try:
        from app.services.section_service import clear_tree, save_tree

        clear_tree(project_id)
        n = save_tree(project_id, toc.get("chapters", []))
        print(f"[目录落库] 写入 {n} 个节点")
    except Exception as e:  # noqa: BLE001 落库失败不阻断流程
        print(f"[目录落库失败] {e}")
    return {"toc": toc, "toc_confirmed": True}


def node_generate_outlines(state: CompileState) -> dict:
    """逐章生成编写思路，并发执行避免串行过慢。

    知识页匹配（matching 三级漏斗）在此进行：每章匹配结果记入 outline._page_ids，
    正文生成按 _page_ids 直接取用。生成后**自动落库**（v0.2 决策：无确认环节）。
    """
    from app.services.experience_service import match_experiences
    from app.services.lot_profile_service import lot_brief
    from app.services.matching import load_page_summaries, match_pages

    flat = _flatten_toc(state["toc"])
    summaries = load_page_summaries()
    # 本标段工程构成：思路只能围绕本标段实际包含的工程展开（与目录裁剪同规则）
    lot_text = lot_brief(state["project_id"]) if state.get("project_id") else ""

    def _one(item: dict) -> dict:
        path = [item["parent"]] if item.get("parent") else []
        pages = match_pages(item["title"], path, summaries)
        outline = generate_outline(
            chapter_title=item["title"],
            chapter_type="",  # 章节类型字典已废弃，匹配按标题路径
            project_facts=state["facts"],
            knowledge_pages=pages,
            experiences=match_experiences(item["title"], path),
            lot_brief=lot_text,
        )
        outline["_toc_title"] = item["title"]
        outline["_parent"] = item.get("parent", "")
        outline["_level"] = item["level"]
        outline["_page_ids"] = [p["id"] for p in pages]
        return outline

    outlines: list[dict] = [None] * len(flat)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=OUTLINE_CONCURRENCY) as pool:
        futures = {pool.submit(_one, item): i for i, item in enumerate(flat)}
        for fut in as_completed(futures):
            i = futures[fut]
            outlines[i] = fut.result()
            print(f"[思路] {i + 1}/{len(flat)} {flat[i]['title'][:30]}")

    # 思路自动落库（v0.2 决策：不设确认环节）
    try:
        from app.services.section_service import save_outline

        for o in outlines or []:
            save_outline(state["project_id"], o.get("_toc_title", ""), o)
        print(f"[思路落库] {len(outlines)} 章状态置 thought_ready")
    except Exception as e:  # noqa: BLE001
        print(f"[思路落库失败] {e}")
    return {"outlines": outlines, "outline_confirmed": True}


def build_compile_graph() -> StateGraph:
    """编译编制状态机（v0.2 决策：无人工确认 interrupt，目录/思路生成后自动落库，
    用户在目录树与对话中自行调整）。"""
    g = StateGraph(CompileState)

    g.add_node("generate_toc", node_generate_toc)
    g.add_node("generate_outlines", node_generate_outlines)

    g.add_edge(START, "generate_toc")
    g.add_edge("generate_toc", "generate_outlines")
    g.add_edge("generate_outlines", END)

    return g


# ---- 执行入口（MySQL 检查点，每 invoke 独立连接，线程安全） ----

_saver_ready = False  # saver.setup() 每进程只需一次（CREATE TABLE IF NOT EXISTS）


def _invoke(payload, thread_id: str) -> dict:
    """以 MySQL 检查点执行/恢复状态机。payload 为输入 dict、Command(resume=...) 或 None。"""
    global _saver_ready
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        saver = PyMySQLSaver(conn)
        if not _saver_ready:
            saver.setup()
            _saver_ready = True
        graph = build_compile_graph().compile(checkpointer=saver)
        return graph.invoke(payload, {"configurable": {"thread_id": thread_id}})
    finally:
        conn.close()


def _extract_interrupt(result: dict) -> tuple[dict, bool, dict | None]:
    """从 invoke 结果中拆出 (state, interrupted, interrupt_value)。"""
    interrupts = result.pop("__interrupt__", None) if isinstance(result, dict) else None
    if interrupts:
        # 取第一个中断点的 value（可能是 dict 或任意类型）
        first = interrupts[0]
        value = first.value if hasattr(first, "value") else first
        return result, True, value
    return result, False, None


def new_compile(thread_id: str, project_id: int, project_info: str, professions: list[str], tender_requirements: str = "", facts: list[dict] | None = None, tender_toc: dict | None = None) -> dict:
    """启动一次编制流程。会运行到第一个 interrupt（确认目录）停住。

    thread_id 由调用方生成（每次新 run 换新 id，避免复用旧检查点状态残留）。
    返回：{state, interrupted, interrupt}。interrupted=True 时 interrupt 为中断点值。
    """
    result = _invoke(
        {
            "project_id": project_id,
            "project_info": project_info,
            "professions": professions,
            "tender_requirements": tender_requirements,
            "tender_toc": tender_toc or {},
            "facts": facts or [],
            "toc": {},
            "toc_confirmed": False,
            "outlines": [],
            "outline_confirmed": False,
        },
        thread_id,
    )
    state, interrupted, value = _extract_interrupt(result)
    return {"state": state, "interrupted": interrupted, "interrupt": value}


def resume_compile(thread_id: str, approval: dict) -> dict:
    """在 interrupt 处恢复，传入用户确认/修改结果。"""
    result = _invoke(Command(resume=approval), thread_id)
    state, interrupted, value = _extract_interrupt(result)
    return {"state": state, "interrupted": interrupted, "interrupt": value}


def resume_interrupted(thread_id: str) -> dict:
    """进程重启后续跑未完成的状态机（从最近检查点继续，非 interrupt 恢复）。"""
    result = _invoke(None, thread_id)
    state, interrupted, value = _extract_interrupt(result)
    return {"state": state, "interrupted": interrupted, "interrupt": value}
