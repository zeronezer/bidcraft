"""编排/规划智能体（M5-4, P0）。

职责：招标要求/评分办法提取 → 章节覆盖检查清单 → 目录生成 → 逐章编写思路。

目录装配（方案 3.2）：
- 固定骨架 + 按本项目实际专业动态装配子目录 + 招标文件定制章节；
- 目录模板库作参考输入，最终以用户确认为准。

骨架章节类型（11 类，跨工程类型通用；**仅作参考**，无招标规定目录时由模型按本标段
实际工程构成增删——见 TOC_TITLES_SYSTEM 规则 3）：
"""
import json
import re

from app.services.llm import llm

# 第一层骨架章节类型（跨工程类型通用）
SKELETON_CHAPTERS = [
    "编制依据与原则",
    "工程概况",
    "建设地区特征",
    "施工组织安排",
    "临时与过渡工程",
    "控制与重难点工程",
    "施工方案",
    "资源配置",
    "管理措施",
    "信息化与技术创新",
    "施工组织图表",
]

PROFESSION_TAGS = [
    "路基", "桥梁", "隧道", "站场", "轨道", "通信", "信号", "信息",
    "电力", "牵引供电", "站房", "房建", "改移道路", "既有线",
]

TOC_SYSTEM = """你是资深施工组织设计（实施性施组/技术标）编制专家，负责为新建项目生成目录。

要求：
1. 目录采用"章 → 节"两级结构，输出 JSON；
2. 【最高优先级】若提供了"招标文件规定的施组目录"，必须以它为骨架保留其章节
   （这是评标格式红线），只允许：补充缺失的必备章、为"施工方案"类章节按项目实际专业
   展开子节、按评分要求补项目特有章节。
   **但有一条硬例外**：与该标段工程构成无关的章节必须**删除或合并**——例如本标段不含
   铺轨却保留"轨道工程"节、不含站房却保留"房屋工程"节。本标段施组里写别的标段的工程，
   评标时属"未按标段编制"的严重问题，比少一节更糟；
3. 无招标规定目录时，章必须覆盖给定的骨架章节类型（可合并、可精简，但
   "编制依据/工程概况/施工组织安排/施工方案/资源配置/管理措施"为核心必备章）；
4. **目录的每一章、每一节都必须落在【本标段工程构成】范围内**（这条对**所有章**生效，
   不限于"施工方案"章）：
   - 施工方案类章节：子节按本标段实际包含的工程类型展开；
   - 控制工程/重难点类章节：只列本标段的控制性/重难点工程（本标段没有的工程不列，
     如本标段不含站房 → 不得有"重难点站房工程"节）；
   - 大型临时设施/过渡工程类章节：只列本标段的临时设施（如本标段无铺轨基地 →
     不得有"铺轨基地"节；本标段的大临以【本标段工程构成】里的为准）；
   - 资源配置/管理措施/信息化等其他章：同样不得为"本标段不存在的工程"设置专节；
   **判定依据统一是项目信息里的【本标段工程构成】清单**：清单未列出的工程类型，
   一律不得出现在目录的任何位置。只写全线别的标段才有的工程，等于暴露"没按本标段编制"，
   属严重问题；
5. 只输出 JSON，不要解释性文字、不要 markdown 代码块围栏。

输出 JSON 结构：
{
  "chapters": [
    {"title": "第一章标题", "children": [{"title": "第X节标题"}, ...]},
    ...
  ]
}"""

OUTLINE_SYSTEM = """你是资深施工组织设计编制专家，负责为指定章节生成编写思路（提纲）。

要求：
1. 基于该章节的类型、本项目事实、可参考的历史知识页，给出该章节的编写思路；
2. 思路要落到"这一章写什么、按什么顺序、用哪些材料、突出什么"，而不是泛泛而谈；
3. 只输出 JSON。

输出 JSON 结构：
{
  "chapter_title": "章节标题",
  "chapter_type": "骨架章节类型",
  "thinking": "编写思路（300-800字）",
  "key_points": ["本节要覆盖的要点"],
  "refs": [{"chapter_type": "可参考的知识页类型", "usage": "引用说明"}],
  "need_table": true,
  "need_image": false
}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"未返回 JSON: {text[:200]}")
    return json.loads(text[s : e + 1])


def generate_toc(project_info: str, professions: list[str] | None = None,
                 tender_requirements: str = "", tender_toc: dict | None = None) -> dict:
    """生成目录（章→节两级）。

    tender_toc：招标文件规定的施组目录（project.toc_hint），提供时为最高优先级骨架。
    """
    prof = professions or []
    user = (
        f"项目信息：{project_info}\n"
        f"本项目涉及的专业：{prof if prof else '待识别'}\n"
        f"骨架章节类型：{SKELETON_CHAPTERS}\n"
    )
    if tender_toc and tender_toc.get("chapters"):
        user += (
            "招标文件规定的施组目录（必须以此为准）：\n"
            + json.dumps(tender_toc["chapters"], ensure_ascii=False)
            + f"\n（来源：{tender_toc.get('note', '招标文件')}）\n"
        )
    if tender_requirements:
        user += f"招标要求与评分办法：\n{tender_requirements}\n"
    user += "\n请生成目录。"
    raw = llm.chat(
        [
            {"role": "system", "content": TOC_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
    )
    return _extract_json(raw)


# ---------------------------------------------------------------- 目录流式（两阶段）

TOC_TITLES_SYSTEM = """你是资深施工组织设计（实施性施组/技术标）编制专家，负责拟定目录的**章级骨架**。

要求：
1. 只输出章标题列表（不展开子节）；
2. 【最高优先级】若提供了"招标文件规定的施组目录"，必须以它为骨架原样保留其章；
3. 无招标规定目录时，**参考**下列骨架章节类型并按本项目实际情况增删调整——
   核心必备章为"编制依据与原则/工程概况/施工组织安排/施工方案/资源配置/管理措施"；
   **本标段工程构成里没有的工程类型不要单独立章**（如无过渡工程则不设"临时与过渡工程"章）；
4. 按施组惯例排序，只输出 JSON，不要解释。

输出 JSON：{"chapters": [{"title": "第一章标题"}]}"""

TOC_CHILDREN_SYSTEM = """你是资深施工组织设计编制专家。为目录中指定的某一章生成子节列表。

要求：
1. 子节按该章的编制惯例排序（2~8 个），标题规范；
2. 结合项目实际专业与招标要求，只列本项目真正需要的内容；
3. 只输出 JSON，不要解释。

输出 JSON：{"children": [{"title": "第X节标题"}]}"""


def _toc_context(project_info: str, professions: list[str] | None,
                 tender_requirements: str, tender_toc: dict | None) -> str:
    prof = professions or []
    user = f"项目信息：{project_info}\n本项目涉及的专业：{prof if prof else '待识别'}\n"
    if tender_toc and tender_toc.get("chapters"):
        user += ("招标文件规定的施组目录（必须以此为准）：\n"
                 + json.dumps(tender_toc["chapters"], ensure_ascii=False) + "\n")
    if tender_requirements:
        user += f"招标要求与评分办法：\n{tender_requirements}\n"
    return user


def generate_toc_titles(project_info: str, professions: list[str] | None = None,
                        tender_requirements: str = "", tender_toc: dict | None = None) -> list[dict]:
    """阶段一：只生成章级骨架（快，先让用户看到目录轮廓）。"""
    user = _toc_context(project_info, professions, tender_requirements, tender_toc)
    user += f"骨架章节类型：{SKELETON_CHAPTERS}\n\n请生成章级骨架（只列章标题）。"
    raw = llm.chat(
        [
            {"role": "system", "content": TOC_TITLES_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    return [c for c in _extract_json(raw).get("chapters") or []
            if isinstance(c, dict) and str(c.get("title") or "").strip()]


def generate_toc_children(chapter_title: str, project_info: str,
                          professions: list[str] | None = None,
                          tender_requirements: str = "") -> list[dict]:
    """阶段二：为某一章生成子节（逐章完成后前端即可实时看到该章展开）。"""
    prof = professions or []
    user = (
        f"项目信息：{project_info}\n"
        f"本项目涉及的专业：{prof if prof else '待识别'}\n"
        + (f"招标要求与评分办法：\n{tender_requirements}\n" if tender_requirements else "")
        + f"\n请为章节「{chapter_title}」生成子节。"
    )
    raw = llm.chat(
        [
            {"role": "system", "content": TOC_CHILDREN_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=1500,
    )
    return [{"title": str(c["title"]).strip()}
            for c in _extract_json(raw).get("children") or []
            if isinstance(c, dict) and str(c.get("title") or "").strip()]


def generate_outline(
    chapter_title: str,
    chapter_type: str,
    project_facts: list[dict] | None = None,
    knowledge_pages: list[dict] | None = None,
    experiences: list[dict] | None = None,
    reqs_text: str = "",
    lot_brief: str = "",
) -> dict:
    """为指定章节生成编写思路。knowledge_pages 为匹配到的历史知识页；experiences 为已确认用户经验（优先级更高）；
    reqs_text：按章相关的招标要求（评分办法/技术要求/编制要求/废标风险），作为编写依据。
    lot_brief：本标段工程构成摘要——思路只能围绕本标段实际包含的工程展开（同目录裁剪规则）。"""
    facts = json.dumps(project_facts or [], ensure_ascii=False)
    # 2026-09-08 决策(A)：思路生成不参考历史页 method/key_points 内容，只列出相关页标题
    pages = json.dumps(
        [{"title": p.get("title")} for p in (knowledge_pages or [])],
        ensure_ascii=False,
    )
    exp_text = "\n".join(f"- [{e.get('exp_type', '经验')}] {e.get('content', '')}" for e in (experiences or []))
    user = (
        f"章节标题：{chapter_title}\n"
        f"章节类型：{chapter_type}\n"
        + (f"【本标段工程构成（思路只能围绕这些工程写；未列出的工程类型不得出现）】\n{lot_brief}\n"
           if lot_brief else "")
        + (f"用户经验与偏好（必须体现到思路中）：\n{exp_text}\n" if exp_text else "")
        + (f"本章相关的招标要求（编写依据，必须响应）：\n{reqs_text}\n" if reqs_text else "")
        + f"本项目事实（可引用，无则标注待补充）：{facts}\n"
        f"相关的历史知识页标题（仅供提示本类章节存在，其内容不作为编写参考）：{pages}\n"
    )
    raw = llm.chat(
        [
            {"role": "system", "content": OUTLINE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
    )
    return _extract_json(raw)
