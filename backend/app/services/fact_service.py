"""事实抽取服务：项目文件（招标/指导性施组/答疑）→ 解析 → 抽取 → 落库 global_fact。

- 上传接口自动触发（后台线程）；
- 同 (category, fact_key) 若值变化：旧行置 expired、version+1 插新 pending 行（答疑补遗重抽场景）；
- 抽取结果一律 pending，需人工确认。
"""
import json
import re

from app.agents.parser_agent import parse_document
from app.core.config import settings
from app.core.database import execute, query, query_one

FACT_CATEGORIES = ["工期", "人员", "机械", "造价", "地质", "结构", "质量标准", "安全目标", "其他"]

# 触发事实抽取的文件类别（勘察报告是地质/水文事实最权威来源；前期策划含工期/资源目标；
# 图纸含全局性设计参数——明细尺寸不进事实表，走切片向量检索按章取材）
FACT_TRIGGER_CATEGORIES = {"tender", "guiding_sod", "clarification", "survey_report", "planning", "drawing"}

# 解析中止标记：文件被删除时置位，建索引/后续步骤据此停止（删除后不再继续烧 MinerU/LLM）
import threading as _threading

_parse_cancel: dict[int, _threading.Event] = {}


def cancel_parse(file_id: int) -> None:
    _parse_cancel.setdefault(file_id, _threading.Event()).set()


def _parse_cancelled(file_id: int) -> bool:
    ev = _parse_cancel.get(file_id)
    return bool(ev and ev.is_set())


def _set_progress(task_id: int, progress: int, detail: str = "") -> None:
    if detail:
        execute("UPDATE task SET progress = %s, detail = %s, updated_at = NOW() WHERE id = %s",
                (progress, detail, task_id))
    else:
        execute("UPDATE task SET progress = %s, updated_at = NOW() WHERE id = %s", (progress, task_id))


def _strip_page_noise(markdown: str) -> str:
    return re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)


def upsert_facts(project_id: int, facts: list[dict], doc_name: str) -> tuple[int, int]:
    """落库事实。返回 (新增/更新数, expired数)。同 key 值变化则旧行 expired、插新版本。"""
    inserted = expired = 0
    for f in facts:
        old = query_one(
            "SELECT id, fact_value, version FROM global_fact"
            " WHERE project_id = %s AND category = %s AND fact_key = %s AND status != 'expired'"
            " ORDER BY version DESC LIMIT 1",
            (project_id, f["category"], f["fact_key"]),
        )
        if old and str(old["fact_value"]).strip() == str(f["fact_value"]).strip():
            # 值未变：跳过（保留人工确认状态）
            continue
        if old:
            execute("UPDATE global_fact SET status = 'expired' WHERE id = %s", (old["id"],))
            expired += 1
            new_version = (old["version"] or 1) + 1
        else:
            new_version = 1
        loc = f["source_location"]
        # 逐章抽取时 loc 已是"文件名 · 章节"，不重复拼文件名；旧整篇抽取的页码类（P..）也不拼
        if doc_name and not loc.startswith("P") and doc_name not in loc:
            loc = f"{doc_name} {loc}".strip()[:200]
        execute(
            "INSERT INTO global_fact (project_id, category, fact_key, fact_value, unit,"
            " source_file, source_location, confidence, applicable_lots, status, version)"
            # 2026-09-10 用户拍板：移除人工确认——事实抽取即生效（直接 confirmed），人工仍可编辑
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s)",
            (project_id, f["category"], f["fact_key"], f["fact_value"], f["unit"],
             doc_name, loc, f["confidence"],
             # 未判定写 []（存疑），**不写 ["all"]**——"判不出就标 all"正是老 bug 的根源
             json.dumps(f.get("applicable_lots") or [], ensure_ascii=False), new_version),
        )
        inserted += 1
    return inserted, expired


def confirmed_facts(project_id: int) -> list[dict]:
    """已确认事实（生成注入唯一入口），按**当前选定标段**过滤：

    - 未判定（空/NULL，判不出来）→ **保守放行**：不能把"判不出"当成"不属于本标段"，
      否则会丢掉大量内容（scope=unknown）；
    - 含 "all" → 全线共性（气候/水系/总工期等），注入（scope=all）；
    - 含 "background" / "multi" → 全线背景（如"全线控制性工程清单"），注入但标注
      scope=background——提示词会要求**不得写成"本标段…"**；
    - 具体标段代码 → 仅当含当前标段时注入（scope=mine）；明确属于其他标段的不注入。
    """
    from app.services.lot_service import selected_lot_code

    lot_code = selected_lot_code(project_id)
    rows = query(
        "SELECT category, fact_key, fact_value, unit, applicable_lots FROM global_fact"
        " WHERE project_id = %s AND status = 'confirmed' ORDER BY category, id",
        (project_id,),
    )
    out = []
    for r in rows:
        lots = r.get("applicable_lots")
        if isinstance(lots, str):
            try:
                import json as _json

                lots = _json.loads(lots)
            except (TypeError, ValueError):
                lots = None
        lots = [str(x) for x in (lots or []) if x]
        if lots and "all" not in lots and "background" not in lots and "multi" not in lots:
            if not lot_code or lot_code not in lots:
                continue  # 明确属于其他标段 → 不注入
        if lots and lot_code and lot_code in lots:
            scope = "mine"
        elif lots and ("background" in lots or "multi" in lots):
            scope = "background"
        elif lots and "all" in lots:
            scope = "all"
        else:
            scope = "unknown"
        out.append({
            "category": r["category"], "key": r["fact_key"],
            "value": r["fact_value"], "unit": r.get("unit", ""),
            "scope": scope,
        })
    return out


def process_file(task_id: int, project_id: int, file_id: int) -> None:
    """解析单个项目文件并抽取事实落库。"""
    # 清除历史中止标记（本次为新一次解析/重新解析，不应立刻被取消）
    _parse_cancel.pop(file_id, None)
    row = query_one("SELECT * FROM project_file WHERE id = %s", (file_id,))
    if not row:
        return
    execute("UPDATE project_file SET status = 'parsing' WHERE id = %s", (file_id,))
    _set_progress(task_id, 5, "解析文档中")
    try:
        file_path = settings.uploads_dir / row["file_path"]
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {row['file_path']}")
        out_dir = settings.uploads_dir / "parsed" / f"project_{project_id}" / str(file_id)

        # 同 MD5 文件此前已解析过 → 直接复用解析产物，跳过 MinerU（省时间与配额）。
        # .docx 走 python-docx 保留真实 # 层级，不复用旧 MinerU 平层产物 → 一律重解析。
        from app.services.parse_cache import ensure_md5, find_reusable

        md5 = ensure_md5("project_file", file_id, file_path, row.get("file_md5"))
        is_docx = file_path.suffix.lower() == ".docx"
        reused_dir = None
        if not is_docx:
            # 复用优先级：① 自己此前的解析产物（重新解析场景）② 其他同 MD5 记录
            own = Path(row["parsed_path"]) if row.get("parsed_path") else None
            reused_dir = own if (own and (own / "full.md").exists()) \
                else find_reusable(md5, exclude_project_file_id=file_id)
        if reused_dir is not None:
            out_dir = reused_dir
            parsed = {"markdown": "", "content_list_path": None}
            parsed["markdown"] = (out_dir / "full.md").read_text(encoding="utf-8")
            _set_progress(task_id, 15, "命中同文件已解析结果（MD5 相同），直接复用")
            print(f"[解析] file {file_id} 复用解析产物，不提交 MinerU ← {out_dir}")
        else:
            print(f"[解析] file {file_id} 提交解析（docx走python-docx={'是' if is_docx else '否'}）")
            parsed = parse_document(
                file_path, out_dir,
                docx_mode="python_docx" if is_docx else "mineru",
                on_progress=lambda m: _set_progress(task_id, 8, str(m)[:120]),
            )
        markdown = _strip_page_noise(parsed.get("markdown", ""))
        # 只抽取目录前的正文主体，跳过封面目录噪音段落前若干字符（MinerU 已去部分）
        _set_progress(task_id, 20, f"文档解析完成（约 {len(markdown)} 字），准备建索引并逐章抽取全局参数")

        # 招标文件：**一次 LLM 调用**整体抽取（标段划分 + 招标要求 + 施组目录）。
        #
        # 2026-09-11 用户拍板：此前这里是串行 4~5 次调用（标段识别 1 + 招标要求 4 任务
        # 并发 2 跑 2 轮 + 目录 1），全部落在进度 20%~90% 这个**无进度上报的空洞**里，
        # 界面从头到尾只显示"准备建索引并逐章抽取全局参数"，看起来像卡死（实测 1~2 分钟，
        # 大头是招标要求那 4 个任务各自的 8192 输出）。改成一次调用，且输入是整份文件
        # （不再做关键词窗口采样——信息可能在任何位置，让模型自己找）。
        lots_for_facts: list[dict] | None = None
        lot_msg = ""
        if row["category"] == "tender":
            _set_progress(task_id, 30, "正在整体抽取招标文件（标段划分 / 招标要求 / 施组目录）…")
            try:
                from app.agents.tender_agent import extract_tender_all
                from app.services.lot_service import replace_lots
                from app.services.requirement_service import replace_requirements

                res = extract_tender_all(markdown, row["file_name"])
                n = replace_lots(project_id, res["lots"])
                lots_for_facts = res["lots"] or None
                lot_msg = f"；标段识别：{f'{n} 个标段' if n else '无标段划分'}"
                n_req = replace_requirements(project_id, row["file_name"], res["requirements"])
                lot_msg += f"；招标要求 {n_req} 条"
                if res["toc"]["found"]:
                    execute(
                        "UPDATE project SET toc_hint = %s WHERE id = %s",
                        (json.dumps(res["toc"], ensure_ascii=False), project_id),
                    )
                    lot_msg += f"；识别到招标规定目录 {len(res['toc']['chapters'])} 章"
            except Exception as e:  # noqa: BLE001 招标抽取失败不影响后续建索引
                lot_msg = f"；招标文件抽取失败: {e}"
            _set_progress(task_id, 55, f"招标文件抽取完成{lot_msg}")

        # （2026-09-07：全局事实改为随建索引逐章提取，见下方 on_chapter 收集→归并→落库；
        #  不再对整篇文档一次性抽取，避免长文档截断/上下文不足导致漏抽或张冠李戴。）

        # 逐章建立章节索引；同一 LLM 步骤顺带抽取该章全局事实（逐章聚焦更准，来源记"文件 · 章节"）。
        fact_hint = ""
        from app.agents.fact_agent import DOC_TYPE_HINTS as _DOC_HINTS

        if row.get("category"):
            fact_hint += _DOC_HINTS.get(row["category"], "")
        # 标段判据（各标段里程范围 + 主要构造物/大临）→ 让抽取时就判对标段归属，
        # 不再"判不出就默认 all"（老 bug：全线控制性工程被标 all 后注入每个标段）
        try:
            from app.services.lot_profile_service import all_lots_brief

            brief = all_lots_brief(project_id)
            if brief:
                fact_hint += (
                    "\n【本项目标段划分（判定归属的依据）】\n" + brief +
                    "\n判定规则：内容只涉及某一个标段的（出现该标段的里程/构造物/大临，或明确写了"
                    "\"X标\"）→ applies_lots=[\"CGJXZQ-x\"]；全线共性（项目名称/承包方式/总工期/"
                    "技术标准/气候水系）→ [\"all\"]；全线背景（如「全线控制性工程清单」「各标段一览」，"
                    "可用作背景但**不是某标段的工程内容**）→ [\"background\"]；一条内容并列涉及"
                    "多个标段的具体工程 → [\"multi\"]；**确实判不出来就留空 []，严禁默认 all**。"
                )
            elif lots_for_facts:
                lots_txt = "、".join(f"{l.get('lot_code', '')}（{l.get('lot_name', '')}）"
                                     for l in lots_for_facts)
                fact_hint += (
                    f"\n- 本项目标段划分：{lots_txt}。某条事实若只适用于某标段，"
                    f"facts 元素里给出 applicable_lots=[标段代码]；**判不出来留空，不要默认 all**。"
                )
        except Exception as e:  # noqa: BLE001 判据构造失败不影响解析
            print(f"[标段判据] 构造失败: {e}")
        chapter_facts: list[dict] = []

        def _on_chapter(ch: dict) -> None:
            sec = str(ch.get("sec_path") or "").strip()
            loc = f"{row['file_name']} · {sec}" if sec else str(row["file_name"])
            for f in ch.get("facts") or []:
                f["source_location"] = loc[:200]
                chapter_facts.append(f)

        _set_progress(task_id, 90, "正在建立章节索引（含逐章抽取全局参数）…")
        try:
            from app.services import file_index

            def _ix_progress(done: int, total: int) -> None:
                pct = 90 + int(done / total * 9) if total else 99
                _set_progress(task_id, min(pct, 99), f"正在建立章节索引（AI 概要）{done}/{total}")

            n_sec = file_index.build_index(
                file_id, project_id, str(out_dir),
                on_progress=_ix_progress,
                should_stop=lambda: _parse_cancelled(file_id),
                fact_hint=fact_hint,
                on_chapter=_on_chapter,
                # docx 走 python-docx 对象级切章、**不落 full.md** → 直接把 md 文本交给索引
                md_text=(parsed.get("markdown") if not parsed.get("markdown_path") else None),
            )
            print(f"[章节索引] file {file_id} 建立 {n_sec} 节")
        except Exception as e:  # noqa: BLE001 索引失败不影响主流程
            print(f"[章节索引失败] {e}")
        if _parse_cancelled(file_id):
            raise RuntimeError("解析已中止（文件已被删除）")

        # 逐章事实 → 规范化 → 按 (category,fact_key) 归并去重 → 落库（pending 待人工确认）
        inserted = expired = 0
        try:
            from app.agents import fact_agent

            san = [x for x in (fact_agent._sanitize(f) for f in chapter_facts) if x]
            # 按 (category,fact_key) 归并去重（同 key 取置信度高），不做全局条数封顶
            merged = fact_agent._key_merge([san]) if san else []
            if merged:
                inserted, expired = upsert_facts(project_id, merged, row["file_name"])
                print(f"[逐章事实] file {file_id} 归并 {len(merged)} 条（增/更 {inserted}，失效 {expired}）")
        except Exception as e:  # noqa: BLE001 事实落库失败不影响文件 parsed
            print(f"[逐章事实落库失败] {e}")

        # 全部收尾完成，才置 parsed 与 100%（此前 status 保持 running，界面显示真实进行中）
        execute(
            "UPDATE project_file SET status = 'parsed', parsed_path = %s WHERE id = %s",
            (str(out_dir), file_id),
        )
        _set_progress(task_id, 100, f"完成：新增/更新 {inserted} 条，失效 {expired} 条（待人工确认）{lot_msg}")

        # 标段档案：解析完成后按需构建/刷新（附表7 等标段划分文件后传时自动更新档案）
        # 仅当"尚无档案"或"本文件含标段划分信息"时重建，避免每个文件都跑一次 LLM 补全
        try:
            fname = row["file_name"] or ""
            from app.services import lot_profile_service
            from app.services.lot_service import selected_lot_code

            if selected_lot_code(project_id) and (
                lot_profile_service.get_profile(project_id) is None
                or "标段划分" in fname or "附表7" in fname
            ):
                prof = lot_profile_service.build_profile(project_id)
                if prof:
                    print(f"[标段档案] 已更新 {prof.get('lot_code')}")
        except Exception as e:  # noqa: BLE001 档案失败不影响解析结果
            print(f"[标段档案更新失败] {e}")
    except Exception as e:  # noqa: BLE001
        execute("UPDATE project_file SET status = 'failed' WHERE id = %s", (file_id,))
        raise


# ------------------------------------------- 按标段定向重提取全局参数（2026-09-11）
#
# 背景：此前事实归属是"先全项目抽一遍、再给每条打 applicable_lots 标签"，判定靠
# ① 规则快筛（judge_text 把标段前缀硬编码成 CGJXZQ，换项目即失效）
# ② 章节级 AI 判定（整表章节判成 ["multi"]，事实再沿用该值）。
# 结果："HSZQ-4/5标计划竣工日期" 在选了 HSZQ-4 的项目里被标成 multi → 界面显示"全线"。
#
# 用户拍板（2026-09-11）：**不靠正则识别本标段，而是拿着用户选定的标段，去项目文件里
# 用 LLM 重新提取**——把"归属"从【事后打标签】变成【提取时的上下文条件】，
# AI 一边读原文一边就知道只关心本标段。适用文档格式千变万化（HSZQ-4 / 4标 / 第四标段 /
# SG-1…），正则只能覆盖见过的那一种，语义理解才稳。

LOT_FACT_SYSTEM = """你是施工组织设计投标资料分析专家。本项目**只编制一个标段**（下称「本标段」）。

【本标段】{lot}
{profile}

【本项目其他标段（其内容一律不抽）】{others}

从文本片段中抽取「编制本标段实施性施组时必须引用、答错会影响评标」的硬事实，分两类：

A. 本标段专属 → applicable_lots = ["{code}"]
   出现本标段的里程/主要构造物/大型临时设施，或明确写了本标段编号的内容。
B. 全线共性 → applicable_lots = ["all"]
   各标段都适用的：项目名称、承包方式、总工期、技术标准、气候水系、全线性质量/安全目标。

**关键规则（务必严格执行）**：
1. 只属于其他标段的内容（如"{other_sample}标…"）**一律不抽**。
2. 一条内容**并列涉及多个标段**时（如"HSZQ-4/5标计划竣工日期 2029年10月31日"）：
   只要其中含本标段就抽出来，且 **fact_key 必须写明是本标段的**（如"{code}标计划竣工日期"）、
   applicable_lots = ["{code}"]；并列的其他标段只作背景，**不得**出现在 fact_key 或 fact_value 里。
3. 拿不准归属时：**只有确认是全线共性才标 ["all"]，否则宁可不抽**。
   （"判不出就标 all"会让其他标段的内容注入本标段正文——这是老 bug 的根源，严禁重犯。）

抽取门槛（宁缺毋滥）：
- 只抽【全局性、约束性】事实：工期/开竣工日期、最高投标限价、里程、桥梁/隧道总长、
  质量标准等级、安全目标、项目班子配置要求等。
- 不抽【明细数据】：工程量清单逐项数量、单根桩长/桩径、逐座桥跨径、混凝土配合比、钢筋规格。
- 不抽【通用规范条文】：各类"应符合 GB/规范要求"的套话。

类别（只从下列选，不符合归"其他"）：{categories}

要求：
1. fact_key 用简短标准名（如"总工期""最高投标限价"），同类归一到相同 key；
2. fact_value 保留原文数字与单位，unit 单独给单位；
3. confidence 为 0~1：数字明确给 0.9+，需推断给 0.6~0.8；
4. 本片段最多输出 {max_facts} 条，只挑最重要的；
5. 只输出 JSON 数组，不要任何解释文字与代码围栏。

输出格式：
[{{"category": "工期", "fact_key": "{code}标计划竣工日期", "fact_value": "2029年10月31日",
  "unit": "", "source_location": "第二章 投标人须知", "confidence": 0.95,
  "applicable_lots": ["{code}"]}}]"""

MAX_CHAPTER_CHARS = 8000   # 单章喂给 LLM 的上限（超长章节截断，避免撑爆上下文）
REEXTRACT_CONCURRENCY = 20  # 按标段重提取的逐章并发（与 file_index 的逐章索引同量级）


def lot_fact_context(project_id: int) -> dict | None:
    """本标段上下文（定向重提取的判据）；未选定标段返回 None。

    档案（里程/构造物/大临）是判"这条属不属于本标段"最实的依据，
    所以**先建档、再重提取**——顺序不能反（同 lot_scope_service 依赖 profile 的道理）。
    """
    from app.services.lot_service import selected_lot_code

    code = selected_lot_code(project_id)
    if not code:
        return None
    row = query_one(
        "SELECT lot_name FROM lot WHERE project_id = %s AND lot_code = %s", (project_id, code)
    )
    prof = None
    profile_txt = ""
    try:
        from app.services.lot_profile_service import get_profile, profile_text

        prof = get_profile(project_id, code)
        # 只有档案真的含"里程/构造物/大临"才算有用——空档案的 profile_text 会返回
        # "（上表未列出的工程类型，本标段均不包含）"这类骨架文字，喂给模型反而误导，
        # 不如明说"档案未建立，仅依据原文判断"。
        if prof and (prof.get("mileage_ranges") or prof.get("major_structures")
                     or prof.get("temp_facilities")):
            profile_txt = profile_text(prof)
    except Exception as e:  # noqa: BLE001 档案缺失不阻塞（只是判据弱一些）
        print(f"[定向重提取] 读取标段档案失败: {e}")
    others = [r["lot_code"] for r in query(
        "SELECT lot_code FROM lot WHERE project_id = %s AND lot_code != %s ORDER BY id",
        (project_id, code),
    )]
    return {
        "code": code,
        "name": (row or {}).get("lot_name") or code,
        "profile": profile_txt,
        "others": others,
    }


def _reextract_one(ctx: dict, ch: dict) -> list[dict]:
    """单章抽取（带本标段上下文）。失败返回空列表，不影响其他章。"""
    from app.agents.fact_agent import MAX_FACTS_PER_CHUNK, _extract_json_array
    from app.services.llm import llm

    system = LOT_FACT_SYSTEM.format(
        lot=f"{ctx['code']} {ctx['name']}",
        profile=(ctx["profile"] or "（档案未建立，请仅依据原文判断）"),
        others="、".join(ctx["others"]) or "（无）",
        other_sample=(ctx["others"][0] if ctx["others"] else "HSZQ-5"),
        code=ctx["code"],
        categories="、".join(FACT_CATEGORIES),
        max_facts=MAX_FACTS_PER_CHUNK,
    )
    loc = f"{ch.get('file_name') or ''} · {ch.get('sec_path') or ''}".strip(" ·")
    body = (ch.get("content") or "")[:MAX_CHAPTER_CHARS]
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"章节路径：{ch.get('sec_path')}\n\n原文：\n{body}"},
            ],
            temperature=0.1,
        )
        out = []
        for f in _extract_json_array(raw):
            if not isinstance(f, dict):
                continue
            f["source_location"] = loc[:200]
            f["source_file"] = ch.get("file_name") or ""
            out.append(f)
        return out
    except Exception as e:  # noqa: BLE001 单章失败不影响整批
        print(f"[定向重提取] 章节「{str(ch.get('sec_path'))[:40]}」抽取失败: {e}")
        return []


def save_reextracted_facts(project_id: int, facts: list[dict]) -> dict:
    """重提取结果落库：**人工改过的保留不动**，其余 AI 事实整批刷新。

    用户拍板（2026-09-11）：重提取保留人工修正。所以 edited=1 的条目全程不动
    （既不覆盖、也不置 expired）。未被本次抽到的旧 AI 事实（edited=0）一律失效——
    它们多半正是"判错标段"的残留（如 HSZQ-4/5标… ["multi"]），不清就修不好界面。
    """
    stat = {"kept_edited": 0, "inserted": 0, "unchanged": 0, "relotted": 0,
            "expired": 0, "cleared": 0}
    new_keys: set[tuple] = set()

    from app.services.lot_scope_service import parse_lots

    for f in facts:
        cat, key = f["category"], f["fact_key"]
        new_keys.add((cat, key))
        olds = query(
            "SELECT id, fact_value, version, applicable_lots, IFNULL(edited, 0) AS edited"
            " FROM global_fact"
            " WHERE project_id = %s AND category = %s AND fact_key = %s AND status != 'expired'"
            " ORDER BY version DESC",
            (project_id, cat, key),
        )
        if any(o.get("edited") for o in olds):
            stat["kept_edited"] += 1     # 人工改过 → 一条都不动
            continue
        new_lots = json.dumps(sorted(f.get("applicable_lots") or []), ensure_ascii=False)
        same_val = any(str(o["fact_value"]).strip() == str(f["fact_value"]).strip() for o in olds)
        same_lots = any(
            json.dumps(sorted(parse_lots(o.get("applicable_lots"))), ensure_ascii=False) == new_lots
            for o in olds
        )
        if same_val and same_lots:
            stat["unchanged"] += 1       # 值和归属都没变 → 不产生新版本
            continue
        if same_val:
            # 值没变但**归属变了**（如多标段的 multi → 本标段/all）：就地修正归属。
            # 不能走"值相同就跳过"——旧归属正是错的（"HSZQ-4/5标…"标着 multi），
            # 跳过它就等于这条永远修不好。
            for o in olds:
                execute("UPDATE global_fact SET applicable_lots = %s WHERE id = %s",
                        (new_lots, o["id"]))
                stat["relotted"] += 1
            continue
        new_version = max((o["version"] or 1) for o in olds) + 1 if olds else 1
        for o in olds:
            execute("UPDATE global_fact SET status = 'expired' WHERE id = %s", (o["id"],))
            stat["expired"] += 1
        execute(
            "INSERT INTO global_fact (project_id, category, fact_key, fact_value, unit,"
            " source_file, source_location, confidence, applicable_lots, status, edited, version)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', 0, %s)",
            (project_id, cat, key, f["fact_value"], f.get("unit") or "",
             (f.get("source_file") or "")[:500], (f.get("source_location") or "")[:200],
             f.get("confidence", 0.7),
             json.dumps(f.get("applicable_lots") or [], ensure_ascii=False), new_version),
        )
        stat["inserted"] += 1

    # 清理：本次没抽到、且非人工的旧 AI 事实 → 失效（清掉判错标段的残留）
    for r in query(
        "SELECT id, category, fact_key FROM global_fact"
        " WHERE project_id = %s AND status != 'expired' AND IFNULL(edited, 0) = 0",
        (project_id,),
    ):
        if (r["category"], r["fact_key"]) not in new_keys:
            execute("UPDATE global_fact SET status = 'expired' WHERE id = %s", (r["id"],))
            stat["cleared"] += 1
    return stat


def reextract_facts_for_lot(project_id: int, on_progress=None,
                            max_chapters: int = 400) -> dict | None:
    """以**当前选定标段**为主体，从项目文件原文重新提取全局参数。

    on_progress(pct, msg)：0~100 进度回调（供任务串进「建档 → 重提取」的进度条）。
    未选定标段返回 None。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.agents import fact_agent

    ctx = lot_fact_context(project_id)
    if not ctx:
        return None

    def _p(pct: int, msg: str) -> None:
        if on_progress:
            on_progress(int(pct), msg)

    _p(3, f"读取项目文件章节（本标段：{ctx['code']} {ctx['name']}）…")
    chapters = query(
        "SELECT fi.sec_path, fi.content, pf.file_name FROM file_section_index fi"
        " JOIN project_file pf ON pf.id = fi.file_id"
        " WHERE fi.project_id = %s AND fi.content IS NOT NULL AND fi.content != ''"
        " ORDER BY fi.file_id, fi.start_line",
        (project_id,),
    )
    # 只按"有没有实质内容"筛（跳过封面/落款这类零碎片段）。
    # **注意不要用 file_index._is_non_content**：那个正则排除的是"评标/投标/须知"等章节，
    # 服务的是**正文取材**（不该拿投标人须知去写施组正文）——而全局参数恰恰全在这些章节里，
    # 用了它会把"第二章 投标人须知"整章滤掉，重提取直接抽到 0 条（已踩过）。
    chapters = [c for c in chapters if len((c.get("content") or "").strip()) >= 200]
    if not chapters:
        _p(100, "没有可用的章节原文（请先上传并解析项目文件）")
        return {"chapters": 0, "facts": 0, "save": {}}

    # 超长章节分块，别截断——招标文件的长章节里关键约束常在后半段
    units: list[dict] = []
    for c in chapters:
        content = c.get("content") or ""
        if len(content) <= MAX_CHAPTER_CHARS:
            units.append(c)
        else:
            for k in range(0, len(content), MAX_CHAPTER_CHARS):
                units.append({**c, "content": content[k:k + MAX_CHAPTER_CHARS]})
    if len(units) > max_chapters:
        print(f"[定向重提取] 章节块 {len(units)} 个，按上限取前 {max_chapters} 个")
        units = units[:max_chapters]

    _p(8, f"逐章提取全局参数（共 {len(units)} 块，带本标段上下文）…")
    results: list = [None] * len(units)
    done = 0
    with ThreadPoolExecutor(max_workers=REEXTRACT_CONCURRENCY) as pool:
        futures = {pool.submit(_reextract_one, ctx, c): i for i, c in enumerate(units)}
        for fut in as_completed(futures):
            i = futures[fut]
            items = fut.result()
            if items:
                results[i] = items
            done += 1
            if done % 3 == 0 or done == len(units):
                _p(8 + 82 * done / len(units), f"逐章提取全局参数 {done}/{len(units)} 块")
    batches = [b for b in results if b]
    if not batches:
        _p(100, f"未提取到属于本标段（{ctx['code']}）的全局参数")
        return {"chapters": len(units), "facts": 0, "save": {}}

    merged = fact_agent._key_merge([b for b in batches])
    sanitized = [x for x in (fact_agent._sanitize(f) for f in merged) if x]
    # 规范化归属：**定向提取语境下只有两种合法值**——本标段 or 全线共性。
    # AI 仍可能按老习惯吐出 multi/background（一律归本标段：这条是从"本标段视角"抽的），
    # 但**明确只属于其他标段的必须丢弃，绝不能归成本标段**——"HSZQ-1标总工期"在 ASZQ-4
    # 的档案里会变成刺眼的错误（提示词要求不抽，AI 未必听话，这里兜住）。
    others = set(ctx["others"])
    kept: list[dict] = []
    dropped = 0
    for f in sanitized:
        lots = [x for x in (f.get("applicable_lots") or []) if x]
        if "all" in lots:
            f["applicable_lots"] = ["all"]
        elif lots and all(x in others for x in lots):
            dropped += 1
            continue
        else:
            f["applicable_lots"] = [ctx["code"]]
        kept.append(f)
    sanitized = kept
    if dropped:
        print(f"[定向重提取] 丢弃 {dropped} 条明确属于其他标段的条目")
    _p(92, f"归并落库（{len(sanitized)} 条）…")
    stat = save_reextracted_facts(project_id, sanitized)
    _p(100, (f"完成：本标段 {ctx['code']} 重提取 {len(sanitized)} 条"
             f"（新增 {stat['inserted']}、修正归属 {stat['relotted']}、未变 {stat['unchanged']}、"
             f"保留人工 {stat['kept_edited']}、清理旧 AI 事实 {stat['cleared']}）"))
    return {"chapters": len(chapters), "facts": len(sanitized), "save": stat}


def reextract_facts_task(task_id: int, project_id: int) -> None:
    """后台任务：按标段定向重提取全局参数（进度写 task 表）。"""
    res = reextract_facts_for_lot(
        project_id,
        on_progress=lambda pct, msg: _set_progress(task_id, pct, msg),
    )
    if res is None:
        raise RuntimeError("尚未选定标段，无法按标段重提取全局参数")


def start_reextract_facts(project_id: int) -> int:
    """启动"按标段定向重提取全局参数"后台任务，返回 task_id。"""
    from app.services.task_runner import task_runner

    return task_runner.submit("lot_fact_reextract", project_id, reextract_facts_task, project_id)
