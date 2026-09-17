"""编写智能体（M5-5, P0）。

职责：逐章生成正文，流式写入编辑器（不进对话框）。

硬约束（方案 3.3）：
- 依据优先：上下文 = 章节思路 + 该章知识页 + 事实子集（按节注入）+ 同级章节信息；
- 数字必须来自全局事实表或知识页出处，无依据标 [待补充]；
- 历史图有则引用原图，无则流程/组织类图用 Mermaid 占位，其余留 [图片占位]；
- 提示词缓存（方案 3.10）：system 稳定规则 → 大而稳定上下文前置 → 任务差异放最后。
"""
import json
import re

from app.services.llm import llm

WRITER_SYSTEM = """你是资深施工组织设计（实施性施组/技术标）编制专家，负责撰写章节正文。

硬性规则（必须严格遵守）：
1. 只能基于提供的材料撰写：本项目事实、本项目文件原文片段、工艺工法条目；
   历史知识页与【历史原文参考】可借鉴其行文、通用表述与结构（见规则7），但**项目特定数据一律不得沿用**；
2. 所有数字（工期/人数/机械/造价/里程/工程量等）必须来自「本项目事实」或「本项目文件原文」；
   两者都找不到的数值一律标注 [待补充]，**禁止**借用历史知识页/历史原文里的数值（那是别的项目，不适用本项目）；来自项目文件原文的数字须在括号内标注出处文件名；
3. 冲突裁决：本项目文件原文 > 本项目事实表 > 历史知识页/工法（本项目文件是本项目的上层约束，指导性施组章节内容必须保持一致）；
4. 输出 Markdown 正文，段落结构清晰；标题用 ## 三级起（正文内的小节用 ###）。
   **篇幅由【篇幅要求】给定；若参考历史是短表/清单类内容，则尽量模仿其紧凑表格形式排布，禁止为凑字数加导语、备注列或无关扩写；**
5. 不输出任何解释性文字、不输出"以下是正文"之类的引导语，直接输出正文内容；
   若本章思路标注"需表格/需配图"：表格按【建议表格】给结构填；需配图处按【建议图片】放 [图片占位：图名/用途]；
6. 不要重复其他章节已写的内容（同级章节信息已提供，注意区分边界）；
7. **历史参考：通用文字可借鉴，项目数据严禁照搬**：【历史原文参考】【历史知识页】可以借鉴其行文结构、
   专业措辞、组织顺序，其**通用性文字表述**（通用施工工艺描述、通用质量/安全/环保措施、通用管理要求等
   不依赖具体项目的文字）可改写甚至直接沿用；但其中一切**项目特定且可量化的内容**——项目名、标段、
   里程桩号、日期工期、工程量与数量、造价、人员机械台数、工点名称、单位与指标——**全部属于历史项目，
   严禁照搬进本项目正文**——本项目一律以【本项目事实】【本项目文件原文】【本项目标段】为准；
   本项目缺依据就写 [待补充]，不得拿历史数据顶替；
8. 正文默认即为所选标段内容，**不要**在每章反复写"本标段/第X标段"之类强调；措辞自然，
   仅"编制范围/工程概况"等确需交代起止里程/工点时点名标段即可。"""


def _build_context(outline: dict, facts: list[dict], knowledge: list[dict], siblings: list[str],
                   experiences: list[dict] | None = None, methods: list[dict] | None = None,
                   file_refs: list[dict] | None = None, lot_info: str = "",
                   extra_requirements: str = "", reqs: str = "",
                   lot_profile: dict | None = None) -> str:
    """组装编写上下文（稳定上下文前置，利于命中缓存）。

    本次特别要求（对话触发生成时用户在对话里提出的侧重点）最优先；
    经验条目（用户偏好级，已确认）次之；项目文件原文片段（按章取材）排在事实之后。
    lot_profile：当前标段档案（里程/构造物/大临等）→ 生成"白名单"段，约束正文只写本标段工点。
    """
    parts = []
    # 标段白名单放最前：这是"只能写什么"的硬边界，比其他素材优先级更高
    if lot_profile:
        seg = []
        if lot_profile.get("lot_code"):
            seg.append(f"标段：{lot_profile['lot_code']} {lot_profile.get('lot_name', '')}".strip())
        if lot_profile.get("mileage_ranges"):
            seg.append("里程范围：" + "、".join(lot_profile["mileage_ranges"]))
        terms = [t for t in (lot_profile.get("major_structures") or []) if t]
        terms += [t for t in (lot_profile.get("temp_facilities") or []) if t]
        if terms:
            seg.append("本标段工程清单（**正文只允许出现下列工点**）：" + "、".join(terms))
        if lot_profile.get("special_constraints"):
            seg.append("本标段特殊约束：" + "、".join(lot_profile["special_constraints"]))
        parts.append(
            "【本标段工程范围（白名单，硬边界）】\n" + "\n".join(seg) + "\n"
            "**严禁**把清单之外的工点写进本标段正文（尤其其他标段的隧道/桥梁/站场）；"
            "项目文件里出现其他标段的内容时，只能当作全线背景、不得描述成本标段的工程。"
        )
    if extra_requirements:
        parts.append("【用户本次特别要求（最高优先级，必须重点落实）】\n" + extra_requirements[:1000])
    # 稳定上下文前置
    if lot_info:
        parts.append(
            "【本项目标段（数据取材范围，不必每章强调）】\n"
            "整本施组即该标段的技术标，正文默认即为本标段内容，评标方已知晓——"
            "**不要**在每章反复写\"本标段/第X标段\"之类强调，措辞自然省略（可用\"本线/本项目\"等），"
            "仅\"编制范围/工程概况\"等确需交代起止里程或工点时才点名。标段信息主要用于取材："
            "检索本项目文件/事实时只取本标段对应里程范围与工点的数据。\n"
            f"{lot_info}"
        )
    if experiences:
        parts.append("【用户经验与偏好（最高优先级，必须遵守）】\n" + "\n".join(
            f"- [{e.get('exp_type', '经验')}] {e.get('content', '')}" for e in experiences
        ))
    if facts:
        # 按标段范围分组：本标段/全线共通/未判定 一组；"全线背景"（含 multi）另立一组并警示，
        # 防止把"全线控制性工程=井冈山、武功山…"这类背景当成"本标段控制性工程"照搬进正文。
        mine = [f for f in facts if f.get("scope") != "background"]
        bg = [f for f in facts if f.get("scope") == "background"]
        strip = lambda fs: [{k: v for k, v in f.items() if k != "scope"} for f in fs]  # noqa: E731
        if mine:
            parts.append("【本项目事实（必须严格遵守）】\n" + json.dumps(strip(mine), ensure_ascii=False))
        if bg:
            parts.append(
                "【全线背景资料（**不是本标段内容**，严禁写成「本标段…」）】\n"
                "以下为全线层面的信息（如全线控制性/重难点工程清单），只可用于『全线概况』类背景描述；"
                "**不得**把它们当作本标段的工程内容，也不得据此展开本标段的重难点分析：\n"
                + json.dumps(strip(bg), ensure_ascii=False)
            )
    if reqs:
        parts.append(
            "【招标要求（编写依据：评分办法/技术要求/编制要求/废标风险，必须响应，不得遗漏）】\n"
            + reqs
        )
    if file_refs:
        parts.append("【本项目文件原文片段（招标/指导性施组/答疑/勘察/策划，可引用数字须标注出处文件名）】\n" + "\n".join(
            f"◆ 出处：{r.get('title', '')}\n{r.get('chunk', '')}" for r in file_refs
        ))
    # （2026-09-07 决策：知识页的 method/key_points 暂不注入正文生成，避免历史"编制方式/要点"
    #  干扰；风格参考只用下方「历史原文参考」ref_text。）
    # 历史参考（ref_text）仅作"风格/布局"示范
    exemplar = next((k for k in (knowledge or []) if (k.get("ref_text") or "").strip()), None)
    if exemplar:
        parts.append(
            "【历史原文参考：通用文字可借鉴，项目数据严禁照搬】\n"
            f"以下为历史项目《{exemplar.get('title', '')}》章节原文（全文）。可借鉴其行文风格、专业措辞、"
            f"组织顺序与**通用性文字表述**（通用工艺/措施/管理要求的措辞可改写甚至直接沿用）。"
            f"但其中一切**项目特定且可量化的内容**——项目名/标段/里程桩号/日期工期/工程量与数量/造价/"
            f"人员机械台数/工点名称/单位与指标——**全部属于该历史项目，严禁照搬进本项目正文**；"
            f"本项目数据一律以【本项目事实】与【本项目文件原文】为准，缺依据就标 [待补充]。\n\n"
            f"{str(exemplar.get('ref_text') or '')}"
        )
    # 本章应配的表格/图片（来自知识页详情的表格结构与图片说明，只给"配什么"，不复制历史数据）
    tbl_items = []
    img_items = []
    for k in (knowledge or []):
        for t in k.get("tables") or []:
            t_name = str(t.get("title") or t.get("name") or "").strip()
            if not t_name:
                continue
            desc = str(t.get("description") or "").strip()
            cols = t.get("columns") or []
            col_txt = "、".join(str(c.get("name") or c) for c in cols if (c.get("name") if isinstance(c, dict) else c))
            tbl_items.append(f"- 表「{t_name}」{('：' + desc) if desc else ''}{('（表头：' + col_txt + '）') if col_txt else ''}")
        for im in k.get("images") or []:
            cap = str(im.get("caption") or im.get("title") or "").strip()
            if not cap:
                continue
            content = str(im.get("content") or im.get("description") or "").strip()
            img_items.append(f"- 图「{cap}」{('：' + content) if content else ''}")
    if tbl_items:
        parts.append("【本章建议使用的表格（仅列应配什么表与怎么填，不复制历史数据）】\n" + "\n".join(tbl_items[:6]))
    if img_items:
        parts.append("【本章建议使用的图片/图示（仅列图名与用途，用于在需配图处放 [图片占位：图名/用途]）】\n" + "\n".join(img_items[:6]))
    if methods:
        # 工法整篇注入（含文字/工艺流程/表格/图片），施工方法内容据此编写
        parts.append("【工艺工法（施工方法素材，施工方法内容优先据此编写，可引用其中的工序/参数）】\n" + "\n\n".join(
            f"◆ 工法：{m.get('name')}\n{m.get('text', '')}" for m in methods
        ))
    if siblings:
        parts.append("【同级章节（避免重复，写别的内容）】\n" + "\n".join(f"- {s}" for s in siblings))
    # 篇幅硬约束：从参考知识页取预计字数，显式放到任务最末尾（模型易忽略埋在 JSON 里的 estimate_words）
    ref_len = next((k.get("estimate_words") for k in (knowledge or []) if k.get("estimate_words")), None)
    if ref_len:
        if ref_len < 800:  # 短表/清单类：以"覆盖内容点"为目标，不按字数硬撑
            len_line = (
                f"【篇幅要求】本章参考历史页内容很短（约 {ref_len} 字，多为表格/清单类），"
                f"请**按其覆盖的要点齐全地写**，不要为了凑篇幅加导语、备注列或无关解释。"
            )
        else:
            len_line = (
                f"【篇幅要求】参考知识页预计本章约 {ref_len} 字。请把正文控制在该字数附近（约 ±30%），"
                f"宁精勿滥：参考页很短就写短，严禁空泛扩写、注水或罗列冗余来凑篇幅。"
            )
    else:
        len_line = "【篇幅要求】无明确参考篇幅，内容写完整即可，不要空泛凑字。"
    # 任务差异放最后
    # 编写思路回归正文生成（仅作"数据/来源/写法指引"——说明本章要写什么、数据从哪取、
    # 怎么组织；结构布局由历史参考示范，不把思路当目录模板照抄）
    thinking = (outline.get('thinking') or '').strip()
    kp = outline.get('key_points') or []
    if thinking or kp:
        parts.append(
            "【本章编写思路（内容指引：要写什么、数据从哪取、怎么组织；不是结构模板）】\n"
            f"{thinking}\n"
            + ("\n".join(f"- {p}" for p in kp) if kp else "")
        )
    parts.append(
        f"{len_line}\n"
        f"\n请撰写章节「{outline.get('chapter_title', '')}」的正文。"
    )
    return "\n\n".join(parts)


def write_chapter(outline: dict, facts: list[dict] | None = None, knowledge: list[dict] | None = None, siblings: list[str] | None = None, experiences: list[dict] | None = None, methods: list[dict] | None = None, file_refs: list[dict] | None = None, lot_info: str = "", extra_requirements: str = "", reqs: str = "", lot_profile: dict | None = None) -> str:
    """生成章节正文，返回 Markdown 文本。lot_profile=当前标段档案（白名单约束）。"""
    context = _build_context(outline, facts or [], knowledge or [], siblings or [], experiences or [], methods or [], file_refs or [], lot_info, extra_requirements, reqs=reqs, lot_profile=lot_profile)
    return llm.chat(
        [
            {"role": "system", "content": WRITER_SYSTEM},
            {"role": "user", "content": context},
        ],
        temperature=0.4,
        max_tokens=50000,
    )


def write_chapter_stream(outline: dict, facts: list[dict] | None = None, knowledge: list[dict] | None = None, siblings: list[str] | None = None, experiences: list[dict] | None = None, methods: list[dict] | None = None, file_refs: list[dict] | None = None, lot_info: str = "", reqs: str = "", lot_profile: dict | None = None):
    """流式生成章节正文（SSE 场景用），逐段 yield 文本增量。"""
    context = _build_context(outline, facts or [], knowledge or [], siblings or [], experiences or [], methods or [], file_refs or [], lot_info, reqs=reqs, lot_profile=lot_profile)
    yield from llm.chat_stream(
        [
            {"role": "system", "content": WRITER_SYSTEM},
            {"role": "user", "content": context},
        ]
    )
