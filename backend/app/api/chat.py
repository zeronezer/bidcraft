"""对话 API（SSE 流式）：接入对话智能体（M5-3，Function Calling 工具循环）。

会话与消息落 chat_session / chat_message 表（M4-3 上下文记忆）：
- 不传 session_id 时复用项目最近一个会话（没有则新建）；
- 用户消息先落库，最近 6 条历史注入对话上下文；助手回复在流结束后落库。
"""
import json
import re

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.chat_agent import run_chat
from app.core.database import execute, query, query_one

router = APIRouter(prefix="/api/chat", tags=["chat"])

HISTORY_LIMIT = 6  # 注入上下文的历史消息条数（与 chat_agent 截断一致）

# 对话纠偏指令特征（M3-4 触发点）：带引用上下文的纠偏/偏好类表达
CORRECTION_RE = re.compile(r"不要|别再|不应该|不对|改成|应该|重新写|避免|去掉|不要出现")


class ChatRequest(BaseModel):
    session_id: int | None = None
    project_id: int | None = None
    message: str
    refs: list[dict] = []  # 右键引用上下文 {type, ref, content}


def _get_or_create_session(project_id: int, session_id: int | None) -> int:
    if session_id:
        return session_id
    row = query_one(
        "SELECT id FROM chat_session WHERE project_id = %s ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    if row:
        return row["id"]
    return execute("INSERT INTO chat_session (project_id) VALUES (%s)", (project_id,))


def _load_history(session_id: int) -> list[dict]:
    """注入上下文的历史消息。

    注意（2026-09-10 回退）：曾给 assistant 历史附 `[该轮实际执行工具：xxx✓]` 标注做"背书"，
    结果模型把它当成一种可模仿的格式**自己伪造**（本轮一个工具都没调，却在输出里写
    `[该轮实际执行工具：find_chapters✓]`），反而加重了编造与核验误判。故恢复为纯内容历史，
    不再注入任何工具标注。
    """
    rows = query(
        f"SELECT role, content FROM chat_message WHERE session_id = %s"
        f" ORDER BY id DESC LIMIT {HISTORY_LIMIT}",
        (session_id,),
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


@router.get("/history")
def history(project_id: int, limit: int = 40, before_id: int = 0):
    """项目最近会话的消息列表（工作台打开时回填对话区）。

    懒加载（2026-09-11）：limit 默认取最近 40 条（20 问 20 答）；传 before_id 时返回
    比该消息更早的一批（用于向上滚动加载历史）。返回 has_more 供前端判断是否还有更早。
    """
    row = query_one(
        "SELECT id FROM chat_session WHERE project_id = %s ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    if not row:
        return {"session_id": None, "messages": [], "has_more": False}
    sid = row["id"]
    where, params = "session_id = %s", [sid]
    if before_id:
        where += " AND id < %s"
        params.append(before_id)
    # 多取 1 条判断是否还有更早
    rows = query(
        f"SELECT id, role, content, trace, created_at FROM chat_message WHERE {where}"
        f" ORDER BY id DESC LIMIT {int(limit) + 1}",
        tuple(params),
    )
    has_more = len(rows) > int(limit)
    rows = rows[: int(limit)]

    def _trace_of(raw):
        if not raw:
            return None
        if isinstance(raw, (list, dict)):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    return {
        "session_id": sid,
        "messages": [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "trace": _trace_of(r.get("trace")),
                "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r.get("created_at") else None,
            }
            for r in reversed(rows)  # 升序返回，符合展示顺序
        ],
        "has_more": has_more,
    }


@router.delete("/history")
def clear_history(project_id: int):
    """清空项目对话（清空上下文）：删除全部消息并新建空会话，后续对话从零开始。"""
    execute(
        "DELETE FROM chat_message WHERE session_id IN"
        " (SELECT id FROM chat_session WHERE project_id = %s)",
        (project_id,),
    )
    execute("DELETE FROM chat_session WHERE project_id = %s", (project_id,))
    return {"ok": True}


@router.post("/stream")
def chat_stream(body: ChatRequest):
    """流式对话：SSE 事件 delta / progress / chapter_updated / done。"""
    project_id = body.project_id or 0
    session_id = _get_or_create_session(project_id, body.session_id)
    history = _load_history(session_id)
    execute(
        "INSERT INTO chat_message (session_id, role, content, refs) VALUES (%s, 'user', %s, %s)",
        (session_id, body.message, json.dumps(body.refs, ensure_ascii=False)),
    )

    # 经验沉淀（M3-4）：带引用的纠偏指令 → 异步提炼写法偏好
    if body.refs and CORRECTION_RE.search(body.message):
        from app.services.experience_service import distill_chat_task
        from app.services.task_runner import task_runner

        chapter_title = next(
            (str(r.get("content") or "")[:300] for r in body.refs if r.get("type") == "toc_node"),
            "",
        )
        task_runner.submit("distill_exp", session_id, distill_chat_task,
                           project_id, chapter_title, body.message, body.refs)

    def gen():
        buf: list[str] = []
        final_text: str | None = None  # 定稿答复（final_answer）：落库以此为准，丢弃流式草稿/纠偏中间过程
        trace: list[dict] = []  # 工具调用轨迹（tool_trace）：随消息落库，前端折叠展示
        try:
            for evt in run_chat(project_id, body.message, body.refs, history):
                if evt.get("type") == "delta":
                    buf.append(evt.get("content") or "")
                elif evt.get("type") == "final_answer":
                    final_text = evt.get("content") or ""
                elif evt.get("type") == "tool_trace":
                    trace.append({
                        "kind": "tool",
                        "tool": evt.get("tool"),
                        "args": evt.get("args"),
                        "ok": bool(evt.get("ok")),
                        "note": evt.get("note") or "",
                    })
                elif evt.get("type") == "step_text":
                    # 模型叙述（含被定稿核验否决的草稿）：作为过程步骤留档，前端折叠展示
                    trace.append({
                        "kind": "text",
                        "content": evt.get("content") or "",
                        "rejected": bool(evt.get("rejected")),
                    })
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            # 正常结束 / 中途异常 / 客户端中止（点停止、切页面）都要落库已生成部分
            try:
                reply = ((final_text or "").strip() if final_text else "".join(buf).strip())
                if reply:
                    execute(
                        "INSERT INTO chat_message (session_id, role, content, trace) VALUES (%s, 'assistant', %s, %s)",
                        (session_id, reply,
                         json.dumps(trace, ensure_ascii=False) if trace else None),
                    )
            except Exception:  # noqa: BLE001 落库失败不影响响应收尾
                pass
        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
