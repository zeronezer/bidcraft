"""经验提炼智能体（M3-4）：编写/对话过程沉淀"这个用户/这类项目怎么写才对"。

触发点（由调用方异步触发，本模块只负责提炼）：
1. edit_diff：用户修改 AI 生成内容并保存 → 对比修改前后 diff 提炼写法规则；
2. reject：用户回退 AI 生成内容（否决）→ 提炼"避免项"规则；
3. chat_correction：对话纠偏指令（带引用的"不要这样写/应该…"）→ 提炼偏好规则。

边界（方案 M3-4）：沉淀的是可复用的写法规则与偏好，不是本项目的事实数字
（事实归 global_fact 管，不进经验库）；拿不准是否可复用时宁缺毋滥。
"""
import json
import re

from app.services.llm import llm

SYSTEM_PROMPT = """你是施工组织设计编制经验提炼专家。从用户的修改/否决/纠偏行为中，
提炼出【可复用于后续项目】的写法规则或偏好。

严格边界：
- 只提炼写法规则（结构安排、表述方式、详略取舍、图表使用偏好）；
- 不提炼本项目的事实数字（工期/人数/造价等，那些归全局事实表）；
- 修改如果只是纠正本项目具体信息（如改了里程数字），没有通用写法价值 → 返回 null；
- 表述要可执行：不说"用户不喜欢"，要说"施工部署章应优先按'总体安排+分区流水'组织，避免口号式表述"。

exp_type 取值（三选一）：
- 编写经验：章节内容组织方式（写什么、按什么顺序）与具体写法技巧（详略、表格/图的使用）；
- 表述偏好：语言风格偏好（如"避免口号式表述"）；
- 纠偏规则：明确的禁止项/必须项（如"不要出现 XX"）。

只输出 JSON（无提炼价值时输出 null），不要解释、不要代码围栏：
{
  "exp_type": "编写经验|表述偏好|纠偏规则",
  "content": "经验条目内容（可执行、可复用，100字内）",
  "confidence": 0.8
}"""


def _extract_json_or_null(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    if text.lower() in ("null", "无", ""):
        return None
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return None
    try:
        data = json.loads(text[s : e + 1])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("content") or "").strip():
        return None
    if data.get("exp_type") not in ("编写经验", "表述偏好", "纠偏规则"):
        data["exp_type"] = "编写经验"
    data["content"] = str(data["content"])[:500]
    return data


def _rough_diff(before: str, after: str, limit: int = 3000) -> str:
    """粗 diff：前后文各截取 + 长度变化说明（精确 diff 交 LLM 判断，避免大文本全喂）。"""
    if before == after:
        return ""
    b = (before or "")[:limit]
    a = (after or "")[:limit]
    return (
        f"【AI 原文（{len(before or '')} 字）】\n{b}\n\n"
        f"【用户修改后（{len(after or '')} 字）】\n{a}"
    )


def distill_from_edit(chapter_title: str, before: str, after: str, project_id: int) -> dict | None:
    """用户修改 AI 内容保存 → 提炼写法经验。"""
    diff = _rough_diff(before, after)
    if not diff:
        return None
    raw = llm.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"场景：用户修改了 AI 生成的章节「{chapter_title}」并保存。\n\n{diff}"},
        ],
        temperature=0.2,
    )
    item = _extract_json_or_null(raw)
    if item:
        item["chapter_type"] = chapter_title  # 全路径标题（章节类型字典已废弃）
        item["source"] = {"project_id": project_id, "kind": "edit_diff",
                          "diff_summary": f"{len(before or '')}字 → {len(after or '')}字"}
    return item


def distill_from_reject(chapter_title: str, rejected: str, project_id: int) -> dict | None:
    """用户回退（否决）AI 生成内容 → 提炼避免项。"""
    if not (rejected or "").strip():
        return None
    raw = llm.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"场景：用户整体否决了 AI 生成的章节「{chapter_title}」（回退到生成前）。\n"
                f"请判断被否决内容的写法上有什么应避免的问题，提炼为纠偏规则。\n\n"
                f"【被否决的内容（节选）】\n{rejected[:3000]}"
            )},
        ],
        temperature=0.2,
    )
    item = _extract_json_or_null(raw)
    if item:
        item["exp_type"] = "纠偏规则"
        item["chapter_type"] = chapter_title
        item["source"] = {"project_id": project_id, "kind": "reject"}
    return item


def distill_from_chat(chapter_title: str, user_message: str, refs: list[dict], project_id: int) -> dict | None:
    """对话纠偏指令 → 提炼偏好规则。"""
    ref_text = ""
    for r in refs or []:
        ref_text += f"\n[引用{r.get('type')}] {str(r.get('content'))[:500]}"
    raw = llm.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"场景：用户在对话中对章节「{chapter_title or '（未指明）'}」给出纠偏指令。\n"
                f"用户指令：{user_message}{ref_text}\n\n"
                f"请提炼为可复用的写法规则或偏好。"
            )},
        ],
        temperature=0.2,
    )
    item = _extract_json_or_null(raw)
    if item:
        item["chapter_type"] = chapter_title or ""
        item["source"] = {"project_id": project_id, "kind": "chat_correction",
                          "message": user_message[:200]}
    return item
