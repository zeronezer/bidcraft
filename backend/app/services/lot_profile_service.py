"""标段档案服务（P0，2026-09-10）。

把「本标段包含什么」结构化成档案，作为后续取材过滤与正文生成白名单的依据。

数据来源（按权威度，可叠加，冲突以高权威为准）：
1. 《附表7 施工标段划分》表格 → **代码解析**（最准、零 LLM 成本）
2. 招标文件（附件1 各标段分阶段工期 / 图纸目录 / 评标办法前附表）→ 补里程碑
3. 指导性施组（大型临时工程及弃土场方案、施工总平面布置等）→ 补大临/过渡/弃渣场
4. 人工编辑（界面保存，直接覆盖）

档案结构（存 lot.profile JSON）：
{
  "lot_code": "CGJXZQ-10", "lot_name": "...",
  "mileage_ranges": ["DK411+553.05～DK432+423", ...],
  "main_length_km": 20.87,
  "works": {"roadbed":{"count":21,"km":4.56}, "bridge":{...}, "tunnel":{...},
            "station":{"count":1}, "box_girder_spans":461, "link_line_km":1.752,
            "adjacent_existing_line": true},
  "major_structures": ["赣州赣江铁路特大桥", ...],   ← 白名单核心（正文只许写这些）
  "temp_facilities": ["章贡梁场"],                  ← 大型临时设施
  "transition_works": [],                          ← 过渡工程
  "spoil_sites": [],                               ← 弃土（渣）场
  "milestones": [{"name","days","start","end"}],   ← 关键里程碑（界面表格编辑）
  "text": "……",                                    ← 人工编辑的一段文字（存在时优先展示）
  "provenance": {"字段": "来源"}                    ← 每项依据，供人工核对
}
（注意：相邻标段接口 / 特殊约束已按用户要求从档案移除）
"""
import json

from app.core.database import execute, query_one
from app.services.llm import llm

# 2026-09-11 用户拍板：**删除"按文件名找《附表7》再按固定列解析"这条路径**。
# 原因：表名和列序都因项目而异——不是每个项目的标段划分表都叫"附表7"（有的叫别的附表、
# 有的叫"标段一览表"，有的干脆只在正文里写一段），也不是每张表的列序都一样。写死这两样
# 就是换项目即失效（同 CGJXZQ 硬编码一个性质，这是第四处）。
# 现在档案**全部由 LLM 从项目文件原文抽取**（见 _fill_by_llm），不依赖任何特定表名/列序。


FILL_SYSTEM = """你是标段档案抽取助手。根据给出的项目文件原文，为指定标段抽取档案字段。

只输出 JSON（不要解释、不要代码围栏）：
{"mileage_ranges": ["DK起～DK止"], "major_structures": ["工点名"],
 "temp_facilities": [], "transition_works": [], "spoil_sites": [],
 "milestones": [{"name": "", "days": 0, "start": "", "end": ""}]}

**只填属于该标段的内容**；原文里查不到就留空，**严禁编造、也严禁套用其他标段**。

- mileage_ranges：该标段的起止里程，保留原文写法（如 "DK47+041～DK62+402"）。
- major_structures：该标段的主要构造物/工点（特大桥、隧道、车站、枢纽等），**用原文名称**。
  ⚠️ 只能填原文中**明确属于该标段**的。若某工点出现在"各标段一览表/全线控制性工程"里、
  或注明属其他标段，**不得填入**（填错会让正文把别家工点写成本标段的）。
- temp_facilities：该标段的大型临时设施（梁场、拌和站、填料加工站、盾构管片场、铺轨基地、施工便道等）。
- transition_works：该标段的过渡/倒接工程。spoil_sites：该标段的弃土（渣）场。
- milestones：来自工期表中**该标段**的行（施工准备/主体关键节点/静态验收/联调联试等）。

注意：本项目**不一定有《附表7 施工标段划分》表**——信息可能散落在招标公告、投标人须知、
项目概况、工程概况等章节里，请从给定原文中尽力提取，不要因为找不到某张表就整体留空。"""


ARCHIVE_DOC_MAX_CHARS = 300_000   # 喂给档案抽取的文件原文总量上限（1M 上下文绰绰有余）


def _project_docs(project_id: int) -> list[dict]:
    """项目文件的**完整原文**（供 LLM 抽档案）——不做任何关键词挑选、也**不截断**。

    超限由 `_fill_by_llm` 分批处理（逐文件分批调用再合并），所以这里返回全部文件。

    原文来源：优先 full.md（最保真、一次读取）；**docx 走对象级切章、不落 full.md**，
    回退按 file_section_index.content 拼回。两条路都要留，否则 docx 项目抽不到档案。
    """
    from pathlib import Path

    from app.core.database import query

    files = query(
        "SELECT id, file_name, parsed_path FROM project_file"
        " WHERE project_id = %s AND status = 'parsed' ORDER BY id",
        (project_id,),
    )
    docs: list[dict] = []
    total = 0
    for f in files:
        text = ""
        if f.get("parsed_path"):
            md = Path(f["parsed_path"]) / "full.md"
            if md.exists():
                try:
                    text = md.read_text(encoding="utf-8")
                except OSError as e:  # noqa: BLE001 读失败回退索引拼接
                    print(f"[标段档案] 读 full.md 失败（{f['file_name']}）: {e}")
        if not text.strip():
            secs = query(
                "SELECT content FROM file_section_index WHERE file_id = %s ORDER BY start_line",
                (f["id"],),
            )
            text = "\n".join(s["content"] for s in secs if s.get("content"))
        text = text.strip()
        if not text:
            continue
        docs.append({"file": f["file_name"], "text": text})
        total += len(text)
    if docs:
        over = "（超上限，将分批抽取）" if total > ARCHIVE_DOC_MAX_CHARS else ""
        print(f"[标段档案] 输入文件 {len(docs)} 份，共 {total} 字{over}")
    return docs


ARCHIVE_KEYS = ("mileage_ranges", "major_structures", "temp_facilities",
                "transition_works", "spoil_sites", "milestones")


def _batch_docs(docs: list[dict], max_chars: int = ARCHIVE_DOC_MAX_CHARS) -> list[list[dict]]:
    """按上限把文件切成若干批：超限时**逐文件分批调用**，而不是把后面的文件截掉丢掉。

    单份文件本身就超过上限时它独占一批（由 `_extract_batch` 内部截断），
    这样至少不会因为一份超大文件把其余文件全挤没。
    """
    batches: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for d in docs:
        t = len(d.get("text") or "")
        if cur and size + t > max_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.append(d)
        size += t
    if cur:
        batches.append(cur)
    return batches


def _merge_extracted(datas: list[dict]) -> dict:
    """多批抽取结果合并：列表字段去重拼接，里程碑按 name 去重（后到的补齐空字段）。"""
    out: dict = {}
    for key in ("mileage_ranges", "major_structures", "temp_facilities",
                "transition_works", "spoil_sites"):
        vals: list[str] = []
        for d in datas:
            v = d.get(key)
            if isinstance(v, list):
                vals.extend(str(x).strip() for x in v if str(x).strip())
        if vals:
            out[key] = list(dict.fromkeys(vals))   # 去重且保持顺序

    by_name: dict[str, dict] = {}
    for d in datas:
        for m in (d.get("milestones") or []):
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").strip()
            if not name:
                continue
            cur = by_name.get(name)
            if cur is None:
                by_name[name] = m
            else:  # 同名节点跨批出现：把各批抽到的字段互补
                for k in ("days", "start", "end"):
                    if not cur.get(k) and m.get(k):
                        cur[k] = m[k]
    if by_name:
        out["milestones"] = list(by_name.values())
    return out


def _extract_batch(profile: dict, batch: list[dict], idx: int, total: int) -> dict | None:
    """单批文件 → 档案字段（失败返回 None，不影响其他批）。"""
    body = "\n\n".join(
        f"◆ 文件：{d.get('file')}\n{d.get('text') or ''}" for d in batch
    )[:ARCHIVE_DOC_MAX_CHARS]
    tag = f"（第 {idx}/{total} 批）" if total > 1 else ""
    print(f"[标段档案] LLM 抽取输入 {len(body)} 字，来源 {len(batch)} 份文件{tag}")
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": FILL_SYSTEM},
                {"role": "user", "content": (
                    f"目标标段：{profile.get('lot_code')}"
                    f"（表中可能写作「{profile.get('lot_code', '')[-2:]}标」）"
                    f" {profile.get('lot_name')}\n"
                    f"该标段起止里程：{'、'.join(profile.get('mileage_ranges') or [])}\n"
                    f"该标段主要构造物：{'、'.join(profile.get('major_structures') or [])}\n"
                    + (f"\n注意：项目文件较多，本次只给你其中一部分{tag}。"
                       f"**只按本批原文抽取**，本批里没有的字段留空，不要猜测、不要由其他知识补全。\n"
                       if total > 1 else "")
                    + f"\n【项目文件原文】\n{body}"
                )},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            print(f"[标段档案] LLM 返回无法解析{tag}: {raw[:200]}")
            return None
        data = json.loads(raw[s : e + 1])
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001 单批失败不影响其他批
        print(f"[标段档案] LLM 抽取失败{tag}: {e}")
        return None


def _fill_by_llm(profile: dict, docs: list[dict]) -> None:
    """用 LLM 从项目文件原文抽取档案字段（就地修改 profile）。

    **这是档案的唯一来源**，不依赖任何特定表名或列序：本项目不一定有《附表7 施工标段划分》
    表（"前30页"选段、不同业主的文件都可能没有），信息可能散落在招标公告、投标人须知、
    项目概况、甚至正文段落里——所以直接**把整份文件交给模型**，让它自己找。
    用 1M 上下文 + prompt 缓存，整份文件喂进去很快且便宜。

    文件总量超过 `ARCHIVE_DOC_MAX_CHARS` 时**分批调用再合并**（而不是截断丢文件）：
    每批只给模型一部分原文、要求"本批没有的字段留空、不要猜"，最后按字段合并去重。
    """
    if not docs:
        return
    batches = _batch_docs(docs)
    datas = [d for d in (
        _extract_batch(profile, b, i + 1, len(batches)) for i, b in enumerate(batches)
    ) if d]
    if not datas:
        return
    merged = _merge_extracted(datas)

    prov = profile.setdefault("provenance", {})
    # LLM 只填**空白**字段：已有的（人工编辑、或上一轮抽取的结果）一律不动。
    for key in ARCHIVE_KEYS:
        val = merged.get(key)
        if val and not profile.get(key):
            profile[key] = val
            prov[key] = "AI 抽取（项目文件原文）"
    print(f"[标段档案] LLM 抽取完成（{len(batches)} 批）："
          f"{[k for k in ARCHIVE_KEYS if merged.get(k)]}")


def _profile_fingerprint(p: dict | None) -> str:
    """档案关键字段指纹（用于判断"档案是否变化"，决定要不要自动重标数据）。"""
    if not p:
        return ""
    return json.dumps(
        {k: p.get(k) for k in ("mileage_ranges", "major_structures", "temp_facilities",
                               "transition_works", "spoil_sites")},
        ensure_ascii=False, sort_keys=True,
    )


def build_profile(project_id: int, lot_code: str | None = None, skip_llm: bool = False,
                  auto_reextract: bool = True) -> dict | None:
    """组装并落库标段档案。lot_code 为空时取当前选定标段。

    skip_llm=True：只用《附表7》代码解析（构造物/里程/工程量），不跑 LLM 补全——
    给"全部标段"批量建档做判定依据时用（零 AI 成本）。
    auto_reextract=False：档案变更后**不自动**另起重提取任务，由调用方自己串联
    （如「重新抽取档案」任务要让"建档 → 重提取"的进度在一个任务里连续可见）。
    """
    from app.services.lot_service import selected_lot_code

    code = lot_code or selected_lot_code(project_id)
    if not code:
        return None
    row = query_one(
        "SELECT id, lot_code, lot_name FROM lot WHERE project_id = %s AND lot_code = %s",
        (project_id, code),
    )
    if not row:
        return None

    profile: dict | None = None
    sources: list[str] = []

    # 从历史档案保留已有字段（**绝不覆盖已建好的**）：曾因走过"最小档案"分支，把已建好的
    # 完整档案（里程/工程量/构造物）覆盖成残缺版。重抽取时 LLM 只填**空白**字段，
    # 已有的（无论来自人工编辑还是上一轮抽取）都不动——用户要改可在界面上直接编辑。
    old = get_profile(project_id, code) or {}
    keep = ("mileage_ranges", "main_length_km", "works", "major_structures",
            "temp_facilities", "transition_works", "spoil_sites", "milestones", "text")
    profile = {k: old[k] for k in keep if old.get(k) not in (None, [], "")}
    profile["provenance"] = dict(old.get("provenance") or {})
    if not profile.get("mileage_ranges") and not profile.get("major_structures"):
        print(f"[标段档案] 尚无档案内容，将由 LLM 从项目文件抽取：{code}")

    # lot_code / lot_name 必须在 _fill_by_llm **之前**设好：profile 是从历史重建的，
    # 首次建档时里面什么都没有、没有 lot_code，LLM 拿到的提示词会变成
    # "目标标段：None（表中可能写作「」）"——它根本不知道要抽哪个标段，只能整体返回空。
    # （此前还有《附表7》解析这条路，其解析结果自带 lot_code，所以这个顺序 bug 长期没暴露；
    #  现在 LLM 是唯一来源，这里错了就是整条链断掉。）
    profile["lot_code"] = code
    profile["lot_name"] = row["lot_name"]

    # **直接把项目文件原文交给 LLM**，不做关键词挑选（用户拍板 2026-09-11）：
    # 大模型有 1M 上下文 + prompt 缓存，整份文件喂进去又快又便宜，"哪些内容是档案信息"
    # 交给它自己判断。此前按关键词（"分阶段工期""大型临时工程"…）挑章节，不过是把硬编码
    # 从"表名"搬到了"关键词"——换个项目照样漏（里程和构造物写在招标公告里，就被漏过）。
    docs = _project_docs(project_id)
    if docs:
        sources.extend(d["file"] for d in docs[:8])
    if not skip_llm:
        _fill_by_llm(profile, docs)

    # 清掉"自动生成的空骨架文本"：get_profile 会按结构化字段现算一个 text 放进内存，
    # 而 build_profile 又把它当历史（keep 列表含 text）读回落库 → 骨架被固化。之后即使
    # LLM 抽到了里程/构造物，界面「工程构成」仍显示空骨架，看起来就是"档案没抽到"。
    # 只清骨架，**人工编辑过的 text 一字不动**（人工文本不等于这个骨架）。
    if (profile.get("text") or "").strip() == _EMPTY_TAIL:
        profile.pop("text", None)

    old_fp = _profile_fingerprint(get_profile(project_id, code))
    execute(
        "UPDATE lot SET profile = %s, profile_source = %s, profile_updated_at = NOW() WHERE id = %s",
        (json.dumps(profile, ensure_ascii=False), "；".join(dict.fromkeys(sources))[:300], row["id"]),
    )
    # 档案是"这条内容属不属于本标段"最实的判据 → 档案一变，全局参数就该按本标段重提取。
    # **首次建档同样要触发**：先传了指导性施组、勘察报告（那时还没标段，抽出来的事实没法
    # 判归属），后传招标文件才抽出标段的场景，就靠这一刀把全局参数按本标段重建。
    # （此前条件是 `old_fp and ...`，首次建档时 old_fp 为空而整段跳过，正是这条路径长期
    # 没被处理的根因。）
    #
    # 2026-09-11 用户拍板：这里走**按标段定向重提取**（fact_service.reextract_facts_for_lot），
    # 不再走"事后重标归属"——判定归属靠正则/事后打标签都被证明不可靠（换项目即失效）。
    if not skip_llm and auto_reextract and _profile_fingerprint(profile) != old_fp:
        try:
            from app.services.fact_service import start_reextract_facts

            tid = start_reextract_facts(project_id)
            print(f"[标段档案] 档案已变更 → 自动启动按标段重提取全局参数任务 #{tid}")
        except Exception as e:  # noqa: BLE001 自动重提取失败不影响建档
            print(f"[标段档案] 自动重提取启动失败: {e}")
    return profile


def all_lots_brief(project_id: int) -> str:
    """全部标段的判定判据文本（各标段里程范围 + 主要构造物 + 大临），供解析时判归属用。

    档案缺失时按《附表7》快速补建（零 AI 成本）；完全没有标段划分则返回空串。
    """
    from app.core.database import query

    lots = query("SELECT lot_code FROM lot WHERE project_id = %s ORDER BY id", (project_id,))
    if not lots:
        return ""
    lines = []
    for lot in lots:
        code = lot["lot_code"]
        prof = get_profile(project_id, code)
        if not prof:
            prof = build_profile(project_id, code, skip_llm=True)
        if not prof:
            continue
        terms = [t for t in ((prof.get("major_structures") or []) + (prof.get("temp_facilities") or []))
                 if t]
        lines.append(
            f"- {code}：里程 {'、'.join(prof.get('mileage_ranges') or []) or '（未提取）'}；"
            f"主要构造物/大临：{'、'.join(terms) or '（未提取）'}"
        )
    return "\n".join(lines)


_EMPTY_TAIL = "（上表未列出的工程类型，本标段均不包含）"


def profile_text(p: dict | None) -> str:
    """档案 → **一段文字**（界面展示/编辑用；同时是注入生成的"本标段工程构成"）。

    人工编辑过（p["text"] 存在）时以人工文本为准；否则由结构化字段拼出。
    **不含"相邻标段接口/特殊约束"**（按用户要求已从档案移除）。
    """
    if not p:
        return ""
    if (p.get("text") or "").strip():
        return p["text"].strip()
    lines = []
    if p.get("mileage_ranges"):
        lines.append("里程范围：" + "、".join(p["mileage_ranges"]))
    w = p.get("works") or {}
    seg = []
    for key, label, unit in (("roadbed", "路基", "段"), ("bridge", "桥梁", "座"),
                             ("tunnel", "隧道", "座")):
        d = w.get(key)
        if isinstance(d, dict) and (d.get("count") or d.get("km")):
            seg.append(f"{label}{d.get('count', '')}{unit}"
                       + (f"{d.get('km')}km" if d.get("km") else ""))
    if isinstance(w.get("station"), dict) and w["station"].get("count"):
        seg.append(f"车站{w['station']['count']}座（不含站房）")
    if w.get("box_girder_spans"):
        seg.append(f"箱梁预制架设{w['box_girder_spans']}孔")
    if w.get("link_line_km"):
        seg.append(f"联络线{w['link_line_km']}km")
    if w.get("track_km"):
        seg.append(f"铺轨{w['track_km']}铺轨公里")
    if w.get("adjacent_existing_line"):
        seg.append("邻近既有线施工")
    if seg:
        lines.append("工程构成：" + "；".join(seg))
    if p.get("major_structures"):
        lines.append("主要构造物：" + "、".join(p["major_structures"]))
    if p.get("temp_facilities"):
        lines.append("大型临时设施：" + "、".join(p["temp_facilities"]))
    if p.get("transition_works"):
        lines.append("过渡工程：" + "、".join(p["transition_works"]))
    if p.get("spoil_sites"):
        lines.append("弃土（渣）场：" + "、".join(p["spoil_sites"]))
    lines.append(_EMPTY_TAIL)
    return "\n".join(lines)


PARSE_SYSTEM = """把这段「标段工程构成说明」解析为结构化 JSON。只输出 JSON，不要解释、不要代码围栏：
{"mileage_ranges": ["DK起~DK止"], "main_length_km": 20.87,
 "works": {"roadbed": {"count": 21, "km": 4.56}, "bridge": {"count": 26, "km": 13.735},
           "tunnel": {"count": 6, "km": 2.578}, "station": {"count": 1},
           "box_girder_spans": 461, "link_line_km": 1.752, "track_km": 0,
           "adjacent_existing_line": false},
 "major_structures": ["工点名"], "temp_facilities": ["梁场"], "transition_works": [], "spoil_sites": []}

规则：只解析原文写到的字段；原文没写的字段省略；**严禁编造**（尤其里程、数量）。"""


def parse_profile_text(text: str) -> dict:
    """人工编辑的一段文字 → 结构化字段（AI 解析；失败返回 {}，仅保留文本）。"""
    from app.services.llm import llm

    try:
        raw = llm.chat(
            [
                {"role": "system", "content": PARSE_SYSTEM},
                {"role": "user", "content": text[:4000]},
            ],
            temperature=0.0,
            max_tokens=1200,
        )
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            return {}
        return json.loads(raw[s : e + 1])
    except Exception as e:  # noqa: BLE001 解析失败不影响保存文本
        print(f"[标段档案] 文本解析失败（仅保存文本）: {e}")
        return {}


def lot_brief(project_id: int, lot_code: str | None = None) -> str:
    """**当前标段**工程构成摘要（注入目录/思路/正文生成，约束"只写本标段有的工程"）。"""
    p = get_profile(project_id, lot_code)
    if not p:
        return ""
    head = f"标段：{p.get('lot_code', '')} {p.get('lot_name', '')}".strip()
    body = profile_text(p)
    return head + ("\n" + body if body else "")


def get_profile(project_id: int, lot_code: str | None = None) -> dict | None:
    """读取标段档案（不重建）。"""
    from app.services.lot_service import selected_lot_code

    code = lot_code or selected_lot_code(project_id)
    if not code:
        return None
    row = query_one(
        "SELECT profile, profile_source, profile_updated_at FROM lot"
        " WHERE project_id = %s AND lot_code = %s",
        (project_id, code),
    )
    if not row or not row.get("profile"):
        return None
    p = row["profile"]
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except (TypeError, ValueError):
            return None
    p["_source"] = row.get("profile_source") or ""
    p["_updated_at"] = str(row.get("profile_updated_at") or "")
    # 界面展示/编辑用的一段文字：人工编辑过就用原文，否则由结构化字段生成
    if not (p.get("text") or "").strip():
        p["text"] = profile_text(p)
    return p


def rebuild_profile_task(task_id: int, project_id: int) -> None:
    """后台任务：重新抽取标段档案（含 LLM 补全）**并按本标段重提取全局参数**。

    两步**有序、不能反**：档案给出本标段的里程与构造物，而"这条内容属不属于本标段"正是
    拿它当判据——先建档、再提取才准。重提取在这里串行跑（而不是另起一个用户看不见的后台
    任务），是为了让"抽档案"和"按标段重整全局参数"在界面上是一个进度条走到底。
    """
    from datetime import datetime

    from app.core.database import execute

    def _prog(pct: int, msg: str) -> None:
        execute("UPDATE task SET progress = %s, detail = %s, updated_at = %s WHERE id = %s",
                (int(pct), msg, datetime.now(), task_id))

    _prog(8, "读取标段划分表并补全档案（里程碑/大临）…")
    prof = build_profile(project_id, auto_reextract=False)
    if not prof:
        raise RuntimeError("未选定标段或无可用的标段划分信息")
    code = prof.get("lot_code", "")
    _prog(22, f"{code} 档案已更新，正在按本标段重提取全局参数…")
    from app.services.fact_service import reextract_facts_for_lot

    res = reextract_facts_for_lot(project_id, on_progress=lambda pct, msg: _prog(22 + pct * 0.76, msg))
    if res is None:
        _prog(100, f"完成：{code} 档案已更新（未选定标段，跳过全局参数重提取）")
    else:
        s = res.get("save") or {}
        _prog(100, (f"完成：{code} 档案已更新；全局参数重提取 {res.get('facts', 0)} 条"
                    f"（新增 {s.get('inserted', 0)}、保留人工 {s.get('kept_edited', 0)}、"
                    f"清理旧 AI 事实 {s.get('cleared', 0)}）"))


def start_rebuild(project_id: int) -> int:
    """启动"重新抽取标段档案"后台任务，返回 task_id。"""
    from app.services.task_runner import task_runner

    return task_runner.submit("lot_profile", project_id, rebuild_profile_task, project_id)


def save_profile(project_id: int, lot_code: str, text: str | None = None,
                 milestones: list | None = None, profile: dict | None = None) -> bool:
    """人工编辑保存。

    - text：编辑后的"一段文字"→ 先存文本，再用 AI 解析回结构化字段（白名单/里程匹配要用）；
    - milestones：里程碑表格直接存（人工可改）；
    - profile：兼容旧调用（整体覆盖）。
    保存时会丢弃"相邻标段接口/特殊约束"两个字段（已从档案移除）。
    """
    row = query_one(
        "SELECT id FROM lot WHERE project_id = %s AND lot_code = %s", (project_id, lot_code)
    )
    if not row:
        return False
    if profile is not None and text is None:
        p = dict(profile)
    else:
        cur = get_profile(project_id, lot_code) or {}
        p = {k: v for k, v in cur.items() if not k.startswith("_")}
        if text is not None:
            old_text = (cur.get("text") or "").strip()
            p["text"] = text
            # 文本**没变**就别再跑一次 LLM 解析：用户只改里程碑（或原样保存）时，
            # 保存应当是瞬时的，不该弹"抽取中"遮罩等模型（此前无条件解析导致保存很慢）。
            if text.strip() != old_text:
                parsed = parse_profile_text(text)
                for k in ("mileage_ranges", "main_length_km", "works", "major_structures",
                          "temp_facilities", "transition_works", "spoil_sites"):
                    if k in parsed:
                        p[k] = parsed[k]
        if milestones is not None:
            p["milestones"] = milestones
    for k in ("interfaces", "special_constraints"):  # 已移除的字段
        p.pop(k, None)
    p["lot_code"] = lot_code
    execute(
        "UPDATE lot SET profile = %s, profile_source = %s, profile_updated_at = NOW() WHERE id = %s",
        (json.dumps(p, ensure_ascii=False), "人工编辑", row["id"]),
    )
    return True


def whitelist_terms(profile: dict | None) -> list[str]:
    """档案 → 白名单词表（正文校验/行级裁剪用）：构造物 + 站场 + 大临 + 里程）。"""
    if not profile:
        return []
    terms: list[str] = []
    terms += [s for s in (profile.get("major_structures") or []) if s]
    terms += [s for s in (profile.get("temp_facilities") or []) if s]
    terms += [s for s in (profile.get("transition_works") or []) if s]
    terms += [s for s in (profile.get("spoil_sites") or []) if s]
    return list(dict.fromkeys(terms))
