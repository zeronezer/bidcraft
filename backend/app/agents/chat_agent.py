"""对话智能体（M5-3, P0）：意图理解 + Function Calling 工具循环。

自主度设计（方案 3.4）：ReAct 自主循环，最多 MAX_STEPS 步。
工具集：查知识页 / 查事实表 / 读目录树 / 读章节正文 / 改写章节（编写类直接落库，
对话区只回进度与摘要——见 SSE 事件 chapter_updated，前端据此刷新编辑器）。
"""
import json
import re as _re
import time

from app.core.database import execute, query, query_one
from app.services.llm import chat_llm

MAX_STEPS = 10

SUGGEST_SYSTEM = """你是施工组织设计编制助手。根据刚才的用户问题、你的回答、以及**项目当前状态**，
判断用户接下来可能想做什么。

给出 0~3 条简短的快捷追问（每条不超过 20 字，是用户可能直接发送的问题或指令）。

**只能建议"当前状态下、在对话里真能执行"的操作**：
- 目录未生成（编制阶段 idle）→ 只建议查询类（查招标要求/评分标准、查项目文件原文、查答疑补遗、
  查事实表、查知识页）；**绝不能建议写章节或生成正文**——目录都还没有；
  也**不要建议"生成目录"**（该操作只在页面右上角的按钮里，对话中不做）；
- 目录已生成、章节多为"思路已定/待写思路"（尚无正文）→ 可建议具体章节的写作（如"写第X章正文"）、
  "批量生成全部待写章节"（对话可执行）、"重新生成某章编写思路"；
- 已有部分章节写完（AI 生成待确认 / 已确认）→ 可建议"重写某章正文"、"按某要求调整某章"、
  "把某节的某项措施写详细些"；
- 任何阶段都可建议查询类（查招标要求与评分分值、查项目文件某章原文、查事实表、查知识页、
  查某章目录与正文状态）。

**绝不建议已下线或不可用的能力**：质检、进度图表、招标解析（这些已从系统移除）；
也不要建议对话做不到的操作（生成目录、在界面上确认/回退章节等）。
宁可返回空数组，也不要给一条点了没用的建议。
只输出 JSON：{"suggestions": ["...", "..."]}，不要解释、不要代码围栏。"""


def _project_env(project_id: int) -> str:
    """项目环境摘要（基础数据 + 标段 + 编制进度）：主对话环境注入与追问建议共用。"""
    proj = query_one(
        "SELECT name, project_type, description, created_at FROM project WHERE id = %s",
        (project_id,),
    ) or {}
    row = query_one("SELECT stage FROM compile_session WHERE project_id = %s", (project_id,))
    stage = (row or {}).get("stage", "idle")
    stats = {"unwritten": 0, "thought_ready": 0, "generated": 0, "confirmed": 0}
    for r in query(
        "SELECT status, COUNT(*) c FROM chapter_node WHERE project_id = %s GROUP BY status",
        (project_id,),
    ):
        if r["status"] in stats:
            stats[r["status"]] = r["c"]
    has_toc = "已生成" if sum(stats.values()) else "未生成"
    lot = query_one(
        "SELECT lot_code, lot_name FROM lot WHERE project_id = %s AND selected = 1",
        (project_id,),
    )
    lot_text = f"{lot['lot_code']}（{lot['lot_name']}）" if lot else "全线（招标文件未划分标段或未选择）"
    n_files = query_one("SELECT COUNT(*) c FROM project_file WHERE project_id = %s", (project_id,))["c"]
    return (
        f"项目名称={proj.get('name', '未知')}；工程类型={proj.get('project_type') or '未指定'}；"
        f"项目描述={proj.get('description') or '无'}；创建时间={proj.get('created_at') or '未知'}；"
        f"已上传文件 {n_files} 个；"
        f"当前标段={lot_text}；"
        f"编制阶段={stage}；目录={has_toc}；"
        f"章节：待写思路 {stats['unwritten']}、思路已确认待生成 {stats['thought_ready']}、"
        f"已生成 {stats['generated']}、已确认 {stats['confirmed']}"
    )


def _suggest_followups(messages: list[dict], project_id: int | None = None) -> list[str]:
    """回答结束后生成快捷追问建议（AI 自主决策给出 0~3 条；失败静默为空）。"""
    try:
        state = (
            "\n\n【项目当前状态（建议必须与此相符，只建议当前可行的操作）】\n"
            f"{_project_env(project_id)}"
            if project_id else ""
        )
        raw = chat_llm.chat(
            [
                {"role": "system", "content": SUGGEST_SYSTEM},
                *(m for m in messages[-8:] if m.get("role") in ("user", "assistant")),
                {"role": "user", "content": "（请基于以上对话与项目状态给出快捷追问建议）" + state},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            return []
        items = json.loads(raw[s : e + 1]).get("suggestions") or []
        return [str(x).strip()[:30] for x in items if str(x).strip()][:3]
    except Exception:  # noqa: BLE001 建议失败不影响主回答
        return []

SYSTEM_PROMPT = """你是施工组织设计编制助手，工作在「施组 AI 编制系统」的项目工作台，可调用工具查询知识库/事实表/目录/正文，也可编写或改写章节。

铁律（违反即编造）：
1. **凡涉及本项目的事实**（目录结构、章节状态/正文、招标要求与评分分值、事实表、
   文件原文、经验库），必须先调对应工具，以工具真实返回作答；工具没返回的就明说
   "没有/查不到"，严禁凭记忆、通用大纲印象或对话上文脑补编号、node_id、章节内容、分值。
   工具返回内容原样引用，不补充、不演绎；表格/评分项完整输出全部行，不得截断、省略或
   用占位符代替，也不得声称"原文未取全"（数据有缺口就指明缺口在哪）。
   read_section 返回含 body_text 时原样引用其中文字，禁止改写概括后冒充原文；问的小节
   没有独立节点、只在父章正文里的，原样引用父章对应段落并如实说明，严禁编造子节点 id。
2. **声称"已生成/已写入/已完成"必须有本轮写工具（write_chapter/rewrite_section 等）
   的成功返回背书**，并引用其返回的章节全称；写工具失败就如实说失败、核对后重试，
   禁止转头声称完成。write_chapter/rewrite_section 是同步工具：返回即已写完，明说
   "已完成、已写入编辑器"，严禁说"已提交后台/稍后查看"；write_batch/
   generate_outlines 是后台任务，返回只代表已启动，如实说"已启动，进度见顶部进度条"。
3. **疑问句只查不改**：用户问"X章生成了吗/有没有正文/写到哪了"，只用只读工具核对后
   如实回答；明确执行指令（写/生成/重写/改写/删除）才调用写工具。
4. **按编号点名章节（如"写4.3"）**：必须先 get_chapter_tree 找到标题以该编号开头的
   真实节点再调用，严禁猜 node_id；查不到或编号与标题对不上就向用户确认，不自作主张换章。
5. 固定动作映射：**生成/重新生成目录不在对话中提供**（用户要求生成目录时，明确告知"请在页面右上角点「开始编制 / 重新编制」"）；批量补无思路章节→generate_outlines；重写单章
   思路→regenerate_outline；写单章正文→write_chapter（用户本次的侧重点/补充要求经
   extra_requirements 传入）；**整本批量正文→write_batch——只有用户明确要"全部/所有/剩余
   整本/批量生成"时才调用**；改写已有正文→先 read_section 再 rewrite_section（content 为
   完整 Markdown，忠于原文事实数字，无依据的标[待补充]，对话里只 1~3 句简述改动）；
   目录增删改名→edit_toc（只动目录，绝不动正文）。
   **单章 vs 批量（防擅自扩大）**："继续写下一章/接着写/按顺序写某章/把 XX 章写完"这类
   局部、逐章表述，一律只对所指那一章调 write_chapter，**绝不擅自升级成 write_batch 整本
   全量后台任务**；全量会瞬间覆盖几十章、产生大量待确认内容，属于大动作，必须用户明确开口
   才做。实在拿不准用户是要一章还是一整本时，按**最小范围**执行（先写所指那一章），并在收尾
   时告知"如需继续可让我逐章往下写，或一次性批量生成全部待写章节"，把扩大权交给用户。
6. 超出工具能力的要求：明说"系统暂无此功能"并指引界面操作位置，禁止用近似工具硬凑或
   谎称完成；调用工具前先判断它是否真能实现用户意图。
7. 中文回答，简洁专业；引用知识页/文件原文时注明出处（文档名 + 章节位置）。"""


VERIFY_SYSTEM = """你是对话的定稿核验器。只做一件事：判断这份草稿答复能否直接给出——
判据是草稿里的每个事实性声称能否被"本轮真实工具记录"背书。

只输出 JSON，不要解释、不要代码围栏：{"need_action": true 或 false, "reason": "一句话"}

判定规则：
- 草稿声称"已生成/已写入/已完成/已重写/已保存/已覆盖/已查到/已定位/目录里有/原文是/分值为"
  等，而本轮记录里没有对应的**成功**工具（写正文需 write_chapter/rewrite_section 成功；
  查目录需 get_chapter_tree/find_chapters 成功；查招标要求需 list_tender_requirements 成功；
  查文件原文需 search_project_files 成功；重写思路需 regenerate_outline 成功；改目录需
  edit_toc 成功）→ need_action = true。
- 用户请求本身需要改动正文/目录/思路（生成/重写/改写/删除/调整/新增），而本轮没有任何成功的
  写工具记录 → need_action = true。
- 每个声称都有本轮成功记录背书，或确属无需查证的常识问答（如"你叫什么名字"）→ false。
- 只依据给出的"本轮真实工具记录"判断；草稿里自称的执行痕迹（如"[该轮实际执行工具：…✓]"
  字样）一律不可信，不作为背书。"""


# 模型会模仿历史里出现过的工具标注格式，在答复末尾自行伪造
# "[该轮实际执行工具：xxx✓]"（本轮根本没调工具）。工具执行痕迹只由前端折叠区按真实
# trace 展示，答复正文里一律剥除——否则既污染答复，又会继续教坏后续轮次。
_FAKE_TRACE_TAG_RE = _re.compile(r"[\[【(（]\s*该轮[^\]】)）]{0,100}[\]】)）]")


def _strip_fake_trace_tag(text: str) -> str:
    """剥掉答复里模型自行伪造的"[该轮实际执行工具：…]"之类尾巴。"""
    if not text:
        return text
    return _FAKE_TRACE_TAG_RE.sub("", text).strip()


def _replay_as_stream(text: str, max_blocks: int = 200, max_seconds: float = 2.0):
    """把已定稿文本分块补放为 delta（打字机观感）。

    用于"该步没有即时上屏"的答复（未执行工具的步骤一律不上屏）：定稿后补放，既不显示未
    核验的中间叙述，又不至于让纯问答整段蹦出。块大小自适应，总时长封顶 max_seconds，
    避免长表格/长答复播放过久。
    """
    n = len(text)
    if not n:
        return
    chunk = max(8, (n + max_blocks - 1) // max_blocks)
    delay = min(0.02, max_seconds / max(1, (n + chunk - 1) // chunk))
    for i in range(0, n, chunk):
        yield {"type": "delta", "content": text[i : i + chunk]}
        if delay > 0:
            time.sleep(delay)


TRIAGE_SYSTEM = """你是对话分流器。判断用户这次提问**是否需要**动工具。

只输出 JSON，不要解释、不要代码围栏：{"need_tools": true 或 false, "reason": "一句话"}

- 需要查询本项目的真实数据（目录/章节/正文/招标要求与评分/项目文件原文/事实表/经验库），
  或需要生成/重写/改写/删除/调整章节、目录、编写思路 → true
- 纯寒暄、问你是谁/你能干什么、与本项目无关的通用常识或闲聊 → false
- 拿不准、或用户消息里带有引用的目录节点/正文片段 → true"""


def _needs_tools(message: str) -> bool:
    """预判本轮是否需要工具（决定是否可走无工具的真流式路径）。

    失败一律返回 True（走安全路径：带工具 + 缓冲不上屏），绝不因判定失败漏出脏话。
    """
    try:
        raw = chat_llm.chat(
            [
                {"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user", "content": f"用户提问：{message[:400]}"},
            ],
            temperature=0.0,
            max_tokens=120,
        )
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            return True
        return bool(json.loads(raw[s : e + 1]).get("need_tools", True))
    except Exception:  # noqa: BLE001 判定失败按"需要工具"处理
        return True


def _verify_needs_action(message: str, executed: list[dict], draft: str) -> bool:
    """定稿核验：草稿是否缺少必要动作（需先真实执行再回答）。

    只做 JSON 判定，**不产出答复文本、不产出工具调用**——核验时**不带历史**（历史里的工具
    痕迹曾让核验误判为"有背书"）。核验调用失败时放行（返回 False），不阻断对话。
    """
    evidence = "；".join(
        t["name"] + ("✓" if t["ok"] else (f"✗（{t['note']}）" if t.get("note") else "✗"))
        for t in executed
    ) or "（无，一条工具都没调）"
    try:
        raw = chat_llm.chat(
            [
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": (
                    f"用户请求：{message[:400]}\n"
                    f"本轮真实工具记录：{evidence}\n"
                    f"草稿答复：{draft[:800]}"
                )},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            return False
        return bool(json.loads(raw[s : e + 1]).get("need_action"))
    except Exception:  # noqa: BLE001 核验失败不阻断对话
        return False

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_pages",
            "description": "按章节标题或关键词检索历史施组知识页（写法参考）",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_type": {"type": "string", "description": "章节标题，如 施工方案、编制依据"},
                    "keyword": {"type": "string", "description": "关键词"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_facts",
            "description": "查询本项目全局事实表（工期/人数/机械/造价等已确认事实）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_tree",
            "description": "读取项目目录树（节点 id、标题、状态）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_chapters",
            "description": "按标题或编号在当前项目目录中检索章节节点，返回真实存在的匹配（含 node_id/标题/状态）。用户问『目录里有没有 XX 章节 / 找一下 XX / 哪一章是 XX』时优先用本工具；答复只能引用其返回内容，严禁补充返回中没有的编号、标题或 node_id",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "要查找的标题关键词或编号，如『线路概况』『6.1』『施工组织机构』"}},
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_section",
            "description": "读取指定章节节点的正文内容",
            "parameters": {
                "type": "object",
                "properties": {"node_id": {"type": "integer", "description": "目录节点 id"}},
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rewrite_section",
            "description": "用改写后的完整正文覆盖指定章节（会写入数据库并带 AI 生成标记，用户在编辑器确认/回退）",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "integer"},
                    "content": {"type": "string", "description": "改写后的完整 Markdown 正文"},
                    "summary": {"type": "string", "description": "一句话说明本次改动"},
                },
                "required": ["node_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_chapter",
            "description": "生成或重新生成指定章节的正文（需该章已有编写思路）。用户说'写某章/重新生成某章/重写某章'时调用，生成前先用 get_chapter_tree 查节点 id；重新生成已确认章节时旧内容自动存为可回退版本。用户在本次对话中提出的编写侧重点/补充要求（如'重点写冬季施工''结合答疑补遗的变更'）必须通过 extra_requirements 传入",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "integer", "description": "目录节点 id"},
                    "extra_requirements": {"type": "string", "description": "用户本次对话中提出的特别要求，无则省略"},
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_batch",
            "description": "批量生成**全部**待写章节正文（后台并发，会一次覆盖几十章、生成大量待确认内容——只有用户明确要求'全部/所有/剩余整本/批量生成'时才可调用）。用户说'写下一章/接着写/按顺序写某章'等局部指令时**严禁**用本工具，只用 write_chapter 写所指那一章",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_outlines",
            "description": "为当前目录中所有尚无编写思路的章节批量生成编写思路（后台执行，会持续跟踪直到完成）。用户说'批量生成编写思路/给目录生成思路/补齐思路'时用；执行期间本对话会等待，完成后汇报真实结果。已有思路的章节不受影响",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "regenerate_outline",
            "description": "重新生成**单个**章节的编写思路并覆盖保存（该章已有思路也会重新生成覆盖；未写章节自动升为待写）。用户说'重新生成某章/某节的编写思路'、'给 1.1 重写思路/换个思路'时调用；调用前先用 get_chapter_tree 查节点 id。生成后目录树会刷新。注：批量补齐无思路章节用的是 generate_outlines，那是另一回事",
            "parameters": {
                "type": "object",
                "properties": {"node_id": {"type": "integer", "description": "目录节点 id"}},
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_toc",
            "description": "调整目录结构（新增/删除/批量重命名目录节点，仅改目录树标题与结构，绝不动任何正文）。用户要求'目录改成X/删掉某节/加个章节/标题改为1.1式编号'时用。先 get_chapter_tree 拿节点 id；重命名可一次传多个（如为各节加 1.1/1.2… 编号）。删除会连带该节及已写正文，仅当用户明确要求删除时用",
            "parameters": {
                "type": "object",
                "properties": {
                    "rename": {
                        "type": "array",
                        "description": "重命名列表 [{node_id, title}]",
                        "items": {"type": "object",
                                  "properties": {"node_id": {"type": "integer"}, "title": {"type": "string"}},
                                  "required": ["node_id", "title"]},
                    },
                    "delete": {"type": "array", "description": "删除节点 id 列表（含其子节点与正文）", "items": {"type": "integer"}},
                    "add": {"type": "array", "description": "新增节点 [{title, parent_id?}]（无 parent 为顶级章）",
                            "items": {"type": "object",
                                      "properties": {"title": {"type": "string"}, "parent_id": {"type": "integer", "description": "父节点 id，省略=顶级"}},
                                      "required": ["title"]}},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tender_requirements",
            "description": "查询招标要求清单（结构化入库：评分办法含逐项分值/技术要求/编制要求/废标风险）。用户问'评分表/评分标准/分值/招标要求'时优先用这个，比检索文件原文准确",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "可选过滤：评分办法/技术要求/编制要求/废标风险，不传返回全部",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_experiences",
            "description": "查询经验库（用户沉淀的编制偏好与写法要求，已确认条目）",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "关键词（可选，如'表格'、'字号'、'安全'）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": "列出本项目已上传的文件（名称/类别/解析状态）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_project_files",
            "description": "在项目文件原文中查询（招标文件/指导性施组/答疑补遗/勘察报告/前期策划）。返回匹配章节的完整原文（含图片）。用户问'招标文件/答疑/勘察报告里说了什么/有没有提到XX'时用这个",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查的内容，如'施工便道的要求'、'取消了哪些措施项'"},
                },
                "required": ["query"],
            },
        },
    },
]


def _tool_search_pages(args: dict) -> str:
    """检索知识页（按标题/用途匹配；项目不使用向量库，无向量兜底）。"""
    from app.services.matching import load_pages_by_ids, match_pages

    query_text = args.get("chapter_type") or args.get("keyword") or ""
    if not query_text:
        return json.dumps({"message": "请给出要查的主题或关键词"}, ensure_ascii=False)

    sem = match_pages(query_text, [], llm_judge=False, top_k=8)
    return json.dumps(
        [{"title": h["title"], "tags": h["tags"], "usage": h["usage"],
          "method": h["method_brief"]} for h in sem],
        ensure_ascii=False,
    )


def _tool_list_facts(project_id: int) -> str:
    """查询全局事实表（已按当前标段过滤：未判定/全线保留，"全线背景"带 scope 标注）。"""
    from app.services.fact_service import confirmed_facts

    return json.dumps(confirmed_facts(project_id), ensure_ascii=False)


def _flat_tree(project_id: int) -> str:
    """读目录树；树未落库但目录已生成（待确认阶段）时，返回待确认目录的真实内容。"""
    rows = query(
        "SELECT id, parent_id, title, status FROM chapter_node WHERE project_id = %s"
        " ORDER BY sort_order, id", (project_id,),
    )
    if rows:
        return json.dumps(rows, ensure_ascii=False)
    # 目录已生成、停在确认环节（尚未落库）：返回真实待确认目录，避免模型编造
    cs = query_one("SELECT stage, interrupt_json FROM compile_session WHERE project_id = %s", (project_id,))
    if cs and cs.get("stage") == "confirm_toc":
        it = cs.get("interrupt_json")
        if isinstance(it, str):
            try:
                it = json.loads(it)
            except (TypeError, ValueError):
                it = None
        toc = (it or {}).get("toc") or {}
        chapters = toc.get("chapters") or []
        if chapters:
            return json.dumps(
                {"note": "目录已生成、等待用户在顶部确认（尚未落库，无节点 id）",
                 "chapters": [{"title": c.get("title"),
                               "children": [{"title": k.get("title")} for k in (c.get("children") or [])]}
                              for c in chapters if isinstance(c, dict)]},
                ensure_ascii=False,
            )
    return json.dumps([], ensure_ascii=False)


# ---------------------------------------------------------------------------
# find_chapters：真实目录章节检索工具（供模型定位"目录里有没有某章/某编号"时引用）。
# 检索/存在性一律以真实 chapter_node 为准，模型不得补造返回中不存在的编号与 node_id。
# （曾出现 AI 回答"6.1.1 线路概况 id=284、2.1 线路概况 id=135"——本项目根本没有这些章节/节点）
# ---------------------------------------------------------------------------
_CHAPTER_STATUS_LABEL = {
    "confirmed": "已确认",
    "generated": "AI 生成待确认",
    "thought_ready": "已有编写思路",
    "unwritten": "尚未编写",
}
def _match_chapter_rows(project_id: int, kw: str) -> list[dict]:
    """按编号前缀或标题包含匹配真实节点；编号匹配做边界校验（2.1 不吞 2.1.1）。"""
    rows = query(
        "SELECT id, parent_id, title, status FROM chapter_node WHERE project_id = %s"
        " ORDER BY sort_order, id", (project_id,),
    )
    hits: list[dict] = []
    is_num = bool(_re.fullmatch(r"\d+(?:\.\d+){1,3}", kw))
    for r in rows:
        t = (r["title"] or "").strip()
        if is_num:
            if not t.startswith(kw):
                continue
            rest = t[len(kw):].lstrip()
            if rest and (rest[0].isdigit() or rest[0] == "."):
                continue  # 6.1 不应匹配 6.1.1
            hits.append(r)
        elif kw and kw in t:
            hits.append(r)
    return hits


def _chapter_choices(project_id: int, limit: int = 150) -> list[dict]:
    """真实目录节点清单（id+标题）：工具因 id 错误失败时随错误返回，让模型一步自愈，
    不再凭记忆瞎猜 node_id。"""
    rows = query(
        "SELECT id, title FROM chapter_node WHERE project_id = %s ORDER BY sort_order, id",
        (project_id,),
    )
    return [{"id": r["id"], "title": r["title"]} for r in rows[:limit]]


def _err_chapter_not_found(project_id: int) -> str:
    return json.dumps(
        {"ok": False,
         "error": "章节不存在（node_id 有误）。请从下面真实目录节点中选取正确 id 重试，禁止猜测",
         "chapters": _chapter_choices(project_id)},
        ensure_ascii=False,
    )


def _tool_find_chapters(project_id: int, keyword: str) -> str:
    """find_chapters：检索目录节点，供模型在定位类问答中引用真实结果。"""
    kws = [keyword.strip()] if keyword and keyword.strip() else []
    all_rows = query(
        "SELECT id, parent_id, title, status FROM chapter_node WHERE project_id = %s"
        " ORDER BY sort_order, id", (project_id,),
    )
    if not kws:
        return json.dumps({"ok": True, "keyword": keyword, "matches": []}, ensure_ascii=False)
    hits, seen = [], set()
    for kw in kws:
        for r in _match_chapter_rows(project_id, kw):
            if r["id"] not in seen:
                seen.add(r["id"])
                hits.append({"id": r["id"], "title": r["title"],
                             "status": _CHAPTER_STATUS_LABEL.get(r.get("status"), r.get("status"))})
    return json.dumps({"ok": True, "keyword": keyword, "matches": hits}, ensure_ascii=False)


def _abridge_args(args: dict, maxlen: int = 60) -> dict:
    """工具参数摘要（供聊天消息里的调用轨迹展示；大段正文只给字数，不落库）。"""
    out: dict = {}
    for k, v in (args or {}).items():
        if k == "content":  # 改写/写入的大段正文不进轨迹
            out[k] = f"<{len(v)} 字>" if isinstance(v, str) else "<…>"
        elif isinstance(v, str):
            out[k] = v[:maxlen] + ("…" if len(v) > maxlen else "")
        elif isinstance(v, (dict, list)):
            out[k] = str(v)[:maxlen]
        else:
            out[k] = v
    return out


def _html_to_readable(html: str) -> str:
    """HTML 正文 → 可读纯文本（供 read_section 原样呈现，保留小节结构与占位标记）。

    规则：h2/h3/h4 → 空行 + 小节标题（原样，不改写）；p → 段落文本；
    [待补充]/[图片占位]/表格占位等标记保留原文。仅用于"读给用户/AI看"，
    不做任何语义改写，杜绝模型拿 HTML 时自行概括串段。
    """
    import re as _re

    s = html or ""
    # 标题：h2/h3/h4 内容保留，标记为小标题行（不吞掉编号文字）
    s = _re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", lambda m: f"\n\n【{_re.sub(r'<[^>]+>', '', m.group(2)).strip()}】\n", s, flags=_re.S)
    # 段落与换行
    s = _re.sub(r"<p[^>]*>", "\n", s)
    s = _re.sub(r"</p>", "\n", s)
    s = _re.sub(r"<br\s*/?>", "\n", s)
    # 表格整体转成一个提示行（保留"表x-x 标题"字样；表格数据在正文里不展开）
    s = _re.sub(r"<table.*?</table>", "\n[表格]\n", s, flags=_re.S)
    # 剩余标签剥掉
    s = _re.sub(r"<[^>]+>", "", s)
    # 压缩连续空行
    s = _re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _read_section(node_id: int, project_id: int) -> str:
    row = query_one(
        "SELECT n.title, c.content AS cj FROM chapter_node n"
        " LEFT JOIN section_content c ON c.chapter_node_id = n.id"
        " WHERE n.id = %s AND n.project_id = %s", (node_id, project_id),
    )
    if not row:
        return _err_chapter_not_found(project_id)
    payload = {}
    if row.get("cj"):
        try:
            payload = json.loads(row["cj"]) if isinstance(row["cj"], str) else row["cj"]
        except (TypeError, ValueError):
            payload = {}
    body = payload.get("body", "")
    fmt = payload.get("format", "md")
    # HTML 正文转可读纯文本返回，避免模型对 HTML 自行提取/改写时串段或编造。
    # 保留原始 html 与纯文本两份：模型优先原样引用 plain，确需结构信息再用 html。
    out = {"title": row["title"], "format": fmt, "body": body}
    if fmt == "html" and body:
        out["body_text"] = _html_to_readable(body)
    return json.dumps(out, ensure_ascii=False)


def _rewrite_section(node_id: int, project_id: int, content: str, summary: str) -> str:
    """改写正文落库（存 prev_content 供回退，置 ai_generated=1）。"""
    node = query_one("SELECT id FROM chapter_node WHERE id = %s AND project_id = %s", (node_id, project_id))
    if not node:
        return _err_chapter_not_found(project_id)
    ex = query_one("SELECT content FROM section_content WHERE chapter_node_id = %s", (node_id,))
    prev_body, prev_format = "", "md"
    if ex and ex.get("content"):
        try:
            old = json.loads(ex["content"]) if isinstance(ex["content"], str) else ex["content"]
            prev_body, prev_format = old.get("body", ""), old.get("format", "md")
        except (TypeError, ValueError):
            pass
    payload = json.dumps(
        {"format": "md", "body": content, "prev_content": prev_body, "prev_format": prev_format},
        ensure_ascii=False,
    )
    if ex:
        execute(
            "UPDATE section_content SET content = %s, ai_generated = 1 WHERE chapter_node_id = %s",
            (payload, node_id),
        )
    else:
        execute(
            "INSERT INTO section_content (chapter_node_id, content, ai_generated) VALUES (%s, %s, 1)",
            (node_id, payload),
        )
    execute("UPDATE chapter_node SET status = 'generated' WHERE id = %s", (node_id,))
    return json.dumps({"ok": True, "node_id": node_id, "summary": summary}, ensure_ascii=False)


def _await_background(name: str, result: str, project_id: int):
    """后台类工具统一收尾：任务未真正完成前，不把结果交给模型，AI 不得提前收尾结束会话。

    期间持续推送进度（progress）+ toc_refresh（让目录树/状态点实时刷新），
    直到任务结束才返回最终 JSON，由模型如实汇报。
    支持：generate_outlines（轮询 task 表）；start_compile / write_batch（轮询 compile_session 阶段）。
    """
    import time as _t

    from app.services import compile_service

    try:
        rj = json.loads(result)
    except (TypeError, ValueError):
        rj = {}

    if name == "generate_outlines":
        tid = rj.get("task_id") if rj.get("started") else None
        if not tid:
            return result
        from app.services.task_runner import get_task

        last = rj.get("message", "批量生成编写思路中…")
        for _ in range(600):  # 上限约 30 分钟
            t = get_task(tid)
            if not t:
                last = "任务信息丢失，请稍后在目录栏查看"
                break
            if t["status"] == "success":
                last = t.get("detail") or "编写思路已全部生成"
                break
            if t["status"] == "failed":
                last = "思路生成失败：" + (t.get("detail") or "")
                break
            pct = t.get("progress") or 0
            detail = t.get("detail") or "批量生成编写思路中…"
            yield {"type": "progress", "content": (detail if pct == 0 else f"{detail}（{pct}%）")}
            yield {"type": "toc_refresh"}
            _t.sleep(3)
        return json.dumps({"ok": True, "started": True,
                           "total": rj.get("total", 0), "detail": last}, ensure_ascii=False)

    # start_compile / write_batch：轮询 compile_session 阶段
    stage_label = {
        "generating_toc": "正在生成目录…",
        "generating_outline": "正在生成各章编写思路…",
        "writing": "正在批量编写正文…",
    }
    if name == "write_batch":
        if not rj.get("started"):
            return result
        running = {"writing"}
        done_text = "全部待写章节正文已写完"
    else:
        return result

    last = stage_label.get(next(iter(running)), "任务进行中…")
    for _ in range(600):
        st = compile_service.get_status(project_id)
        if st.get("stage") not in running:
            break
        msg = stage_label.get(st.get("stage"), last)
        pct = int((st.get("progress") or 0) / st.get("total") * 100) if st.get("total") else None
        yield {"type": "progress", "content": (msg if pct is None else f"{msg}（{pct}%）")}
        yield {"type": "toc_refresh"}
        _t.sleep(3)
        last = msg
    st = compile_service.get_status(project_id)
    stage = st.get("stage")
    done = st.get("progress") or 0
    total_now = st.get("total") or rj.get("total") or 0
    still_running = stage in running
    if stage == "failed":
        detail = "执行失败：" + (st.get("error") or "请查看页面提示")
    elif name == "write_batch":
        detail = ((f"已写完 {done}/{total_now} 章，任务仍在进行，进度以页面顶部为准"
                   if still_running else
                   f"已写完 {done}/{total_now} 章（部分完成或已停止）")
                  if stage != "done" or done < total_now
                  else done_text)
    else:
        detail = {
            "confirm_toc": "目录已生成，正在等待你在页面顶部确认",
            "confirm_outline": "编写思路已生成，正在等待你确认",
            "outlines_ready": done_text,
            "done": done_text,
        }.get(stage)
        if detail is None:
            detail = stage_label.get(stage, "任务进行中…")
            if still_running:
                detail += "（对话已等待较久仍在进行，进度以页面顶部为准，可稍后让我继续汇报）"
    return json.dumps({"ok": True, "started": True, "stage": stage,
                       "done": done, "total": total_now, "detail": detail}, ensure_ascii=False)


def _handle_tool_calls(tool_calls: list, messages: list, project_id: int, executed: list[dict]):
    """执行一组工具调用：推进度、记录 tool_trace、处理后台等待、回填 tool 结果（yield 事件）。"""
    for tc in tool_calls:
        fn = tc.get("function") or {}
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (TypeError, ValueError):
            args = {}
        rewrite_ctx = args if name == "rewrite_section" else None
        yield {"type": "progress", "content": _progress_text(name, args)}
        try:
            result = _exec_tool(name, args, project_id)
        except Exception as e:  # noqa: BLE001 单次工具失败不掐断整个对话流
            import traceback
            traceback.print_exc()
            result = json.dumps({"ok": False, "error": f"「{name}」执行失败：{e}"}, ensure_ascii=False)
        try:
            rj = json.loads(result)
        except (TypeError, ValueError):
            rj = {}
        if isinstance(rj, list):
            rj = {"ok": True, "items": rj}
            result = json.dumps(rj, ensure_ascii=False)
        elif not isinstance(rj, dict):
            rj = {"ok": True, "value": rj}
            result = json.dumps(rj, ensure_ascii=False)
        failed = rj.get("ok") is False or rj.get("error")
        if failed:
            result = json.dumps({"ok": False, "error": rj.get("error") or "工具执行失败"}, ensure_ascii=False)
        executed.append({"name": name, "ok": not failed,
                         "note": (rj.get("error") or "")[:120] if failed else ""})
        yield {"type": "tool_trace", "tool": name, "args": _abridge_args(args),
               "ok": not failed, "note": (rj.get("error") or "")[:120] if failed else ""}
        if name in ("write_batch", "generate_outlines"):
            try:
                _rj0 = json.loads(result)
            except (TypeError, ValueError):
                _rj0 = {}
            if name != "generate_outlines" and (_rj0.get("ok") or _rj0.get("started")):
                yield {"type": "compile_started"}
            result = yield from _await_background(name, result, project_id)
        messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
        try:
            rj = json.loads(result)
        except (TypeError, ValueError):
            rj = {}
        if name in ("rewrite_section", "write_chapter") and rj.get("ok"):
            yield {"type": "chapter_updated", "node_id": rj["node_id"],
                   "content": (rewrite_ctx or {}).get("content", "")}
        elif name == "edit_toc" and rj.get("ok"):
            yield {"type": "toc_updated"}
        elif name == "regenerate_outline" and rj.get("ok"):
            yield {"type": "toc_refresh"}


def run_chat(project_id: int, message: str, refs: list[dict], history: list[dict] | None = None):
    """执行对话（模型自主决策的类 Agent 循环），逐事件 yield：
    - delta        文本增量
    - progress     工具执行进度提示
    - tool_trace   工具调用轨迹（落库供折叠展示）
    - final_answer 定稿答复（前端/落库以此为准）
    - chapter_updated / toc_updated / toc_refresh / compile_started
    """
    # 引用上下文注入（右键引用：目录节点/正文片段）
    ref_text = ""
    for r in refs or []:
        if r.get("type") == "toc_node":
            ref_text += f"\n[引用目录节点] {r.get('content') or r.get('ref')}"
        elif r.get("type") == "text_selection":
            ref_text += f"\n[引用正文片段] {str(r.get('content'))[:2000]}"

    if not project_id:
        project_id = 0
    env = (
        "\n\n【当前项目环境（回答涉及本项目、当前标段的问题时必须以此为准，不得从文件内容猜测）】\n"
        f"{_project_env(project_id)}"
        if project_id else ""
    )
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT + env},
        *(history or [])[-6:],
        {"role": "user", "content": message + (ref_text or "")},
    ]

    executed_tools: list[dict] = []  # 本轮真实执行过的工具（名称+成败），供定稿核验举证
    no_tools_prejudged: bool | None = None  # 首轮预判：True=纯文本场景（可走无工具真流式）
    for _step in range(MAX_STEPS):
        sink: dict = {}
        # 预判分流（仅首轮、且未被核验否定过）：先判"这次提问要不要用工具"。
        # - 判不需要（寒暄/能力询问/通用常识）→ 走**不带 tools** 的流式接口：模型无从调工具，
        #   这条流必然是纯文本，可放心逐字上屏（首字快，不必等整段生成完）。
        # - 判需要（或带引用上下文）→ 带 tools 但先缓冲不上屏，防"边叙述边调工具"先露脏话。
        if not executed_tools and no_tools_prejudged is None:
            no_tools_prejudged = False if (refs or _needs_tools(message)) else True
        plain_stream = bool(no_tools_prejudged) and not executed_tools
        shown = False  # 本步文本是否已上屏（决定核验否定时要不要清屏、定稿要不要补放）
        if plain_stream:
            acc = ""
            for piece in chat_llm.chat_stream(messages):
                if piece:
                    acc += piece
                    shown = True
                    yield {"type": "delta", "content": piece}
            msg = {"role": "assistant", "content": acc}
        else:
            # 本轮一旦执行过工具，后续步骤按收尾处理，逐字上屏；尚未执行工具的步骤一律不上屏
            # ——模型常在这类步骤"边叙述边调工具"，那段未经核验的叙述若即时上屏，会先显示脏内容
            # （甚至抢先声称"已完成"）再被终稿替换，用户看到的就是"先乱回复、随后又刷新正常"。
            stream_now = bool(executed_tools)
            shown = stream_now
            for evt in chat_llm.chat_stream_with_tools(messages, TOOLS, sink):
                if stream_now:
                    yield {"type": "delta", "content": evt["content"]}
            msg = sink.get("message") or {"role": "assistant", "content": ""}
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            # 该步的模型叙述（"边叙述边调工具"那段）留档为过程步骤：它不上屏，但用户展开
            # "执行过程"能看到 AI 当时怎么想的，不再静默丢弃。
            step_text = _strip_fake_trace_tag(msg.get("content") or "")
            if step_text:
                yield {"type": "step_text", "content": step_text[:4000], "rejected": False}
            # 模型决定调工具：真实执行
            messages.append(msg)
            yield from _handle_tool_calls(tool_calls, messages, project_id, executed_tools)
            continue
        # 无工具调用 → 本步文本是潜在最终答复；先定稿核验再放行
        content = _strip_fake_trace_tag((msg.get("content") or "").strip())
        # 定稿核验（每轮必跑）：只做"是否缺少必要动作"的独立判定，不产出答复文本、不产出工具
        # 调用。补做动作一律交回主模型决策——此前让核验模型直接给答复/给工具调用，出过两类
        # 事故：核验前言（"核验通过：…"）混进定稿；核验沿用草稿里编造的 node_id 去补调工具
        # （把「9.9 环境保护措施」当成"文物保护措施"重写）。
        if _verify_needs_action(message, executed_tools, content):
            # 被否决的草稿不静默丢弃：留档为"已否决"步骤（前端标 ✗ 已否决·未执行 + 置灰），
            # 再把已上屏的那段从正文区清掉（内容已在过程区，用户不会觉得"文字凭空没了"）。
            if content:
                yield {"type": "step_text", "content": content[:4000], "rejected": True}
            if shown:
                yield {"type": "draft_discard"}
            no_tools_prejudged = False  # 判错（其实是本项目数据/改动类）：下一轮强制走带工具路径
            messages.append({"role": "user", "content": (
                "（系统提示）你本轮尚未真实执行任何工具，但用户的请求需要实际改动或查询后才能"
                "回答。请先调用相应工具真实执行（涉及章节必须先查到真实 node_id，严禁凭记忆编造），"
                "拿到工具返回后再给出最终答复；禁止在未执行的情况下声称已完成。"
            )})
            continue
        content = content or "（未生成有效回答，请换个说法再试）"
        # 未上屏过的答复（带工具路径下的纯文本步）定稿后分块补放，保留打字机观感
        if not shown:
            yield from _replay_as_stream(content)
        # 定稿：前端/落库以此为准
        yield {"type": "final_answer", "content": content}
        messages.append({"role": "assistant", "content": content})
        yield {"type": "suggestions", "items": _suggest_followups(messages, project_id)}
        return
    # 超步数兜底：让模型基于已发生的一切给出最终回答
    msg = chat_llm.chat_with_tools(messages + [{"role": "user", "content": "请基于以上信息直接给出最终回答。"}], [])
    content = _strip_fake_trace_tag((msg.get("content") or "").strip()) or "（未生成有效回答，请换个说法再试）"
    yield from _replay_as_stream(content)
    yield {"type": "final_answer", "content": content}
    messages.append({"role": "assistant", "content": content})
    yield {"type": "suggestions", "items": _suggest_followups(messages, project_id)}



def _tool_start_compile(project_id: int) -> str:
    """发起编制流程（对话触发）：组装输入 + 后台线程生成目录。

    若会话标记进行中（running=1）但本进程并无对应线程在跑（进程重启后的僵尸
    会话），视为可重新发起——先复位再启动，避免"永远进行中"卡死。
    """
    import json as _json

    from app.services import compile_service

    row = query_one("SELECT stage FROM compile_session WHERE project_id = %s", (project_id,))
    stage = (row or {}).get("stage", "idle")
    working = stage in ("generating_toc", "generating_outline")
    if stage != "idle" and not working:
        # confirm 等待/已完成等阶段也可重新发起（用户在对话要求生成即覆盖重跑）
        pass
    elif working and compile_service.is_active(project_id):
        return _json.dumps(
            {"ok": False, "error": "目录/思路正在生成中（真进行中），请稍候顶部进度条走完"},
            ensure_ascii=False,
        )
    elif working and not compile_service.is_active(project_id):
        # 僵尸会话：进程重启线程已死却残留 running=1 → 复位后重发
        from app.core.database import execute

        execute(
            "UPDATE compile_session SET stage = 'failed', running = 0, error = '僵尸会话已复位，本次重新发起'"
            " WHERE project_id = %s",
            (project_id,),
        )
    inputs = compile_service.assemble_inputs(project_id)
    st = compile_service.start_compile(
        project_id, inputs["project_info"], inputs["professions"],
        inputs["tender_requirements"], inputs["facts"], inputs["tender_toc"],
    )
    return _json.dumps({"ok": True, "stage": st["stage"], "message": "目录与编写思路生成中，完成后自动落库"}, ensure_ascii=False)


def _tool_write_chapter(project_id: int, node_id: int, extra_requirements: str = "") -> str:
    """同步生成单章正文（对话触发），正文直接进编辑器。"""
    import json as _json

    from app.services.writer_service import write_chapter_once

    node = query_one(
        "SELECT title, status FROM chapter_node WHERE id = %s AND project_id = %s",
        (node_id, project_id),
    )
    if not node:
        return _err_chapter_not_found(project_id)
    if node["status"] not in ("thought_ready", "generated", "confirmed"):
        return _json.dumps(
            {"ok": False, "error": f"「{node['title']}」当前状态 {node['status']}，需先完成编写思路确认（thought_ready）才能生成正文"},
            ensure_ascii=False,
        )
    try:
        r = write_chapter_once(project_id, node_id, extra_requirements=extra_requirements)
    except ValueError as e:
        return _json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 生成本身失败要如实返回，不谎称完成
        return _json.dumps({"ok": False, "error": f"本章正文生成失败：{e}"}, ensure_ascii=False)
    # 同步工具，返回即已写完：附注引导模型如实收尾（不得说"后台/稍后/正在写"）
    r["note"] = (
        f"本章正文已完整生成并写入编辑器（{r.get('chars', 0)} 字）。"
        f"请如实告知用户已完成，并提示其到编辑器审阅后确认；不要说'已提交后台/正在生成/稍后查看'。"
    )
    return _json.dumps(r, ensure_ascii=False)


def _tool_write_batch(project_id: int) -> str:
    """批量生成全部待写章节（后台并发，可停止）。"""
    import json as _json

    from app.services.writer_service import start_batch

    return _json.dumps(start_batch(project_id), ensure_ascii=False)


def _tool_search_experiences(keyword: str) -> str:
    """查询已确认的经验条目（用户编制偏好，方案 M3-4）。"""
    sql = "SELECT exp_type, chapter_type, tags, content, status FROM experience_item WHERE status = 'confirmed'"
    params: list = []
    if keyword and keyword.strip():
        sql += " AND (content LIKE %s OR exp_type LIKE %s OR chapter_type LIKE %s)"
        params.extend([f"%{keyword.strip()}%"] * 3)
    sql += " ORDER BY id DESC LIMIT 15"
    rows = query(sql, tuple(params))
    return json.dumps(rows, ensure_ascii=False)


def _tool_list_project_files(project_id: int) -> str:
    """项目文件清单（名称/类别/解析状态）。"""
    rows = query(
        "SELECT file_name, category, status, size_bytes FROM project_file"
        " WHERE project_id = %s ORDER BY id",
        (project_id,),
    )
    cat_map = {"tender": "招标", "guiding_sod": "指导性施组", "clarification": "答疑补遗",
               "survey_report": "勘察报告", "planning": "前期策划", "drawing": "图纸",
               "other": "其他"}
    for r in rows:
        r["category"] = cat_map.get(r["category"], r["category"])
    return json.dumps(rows, ensure_ascii=False)


def _tool_search_project_files(project_id: int, query_text: str) -> str:
    """项目文件原文查询（skill 式：章节索引选取 → 完整原文，含图片引用）。

    项目文件不再入向量库（2026-09-04 决策），索引为空/失败直接返回未检索到，
    不回退向量切片（2026-09-07 清理）。
    """
    if not query_text or not query_text.strip():
        return json.dumps({"message": "请给出要查的内容"}, ensure_ascii=False)
    try:
        from app.services import file_index

        sections = file_index.search_sections(project_id, query_text)
        if sections:
            return json.dumps(
                [{"source": f"{s['file']} · {s['path']}", "content": s["text"]} for s in sections],
                ensure_ascii=False,
            )
    except Exception:  # noqa: BLE001 索引方式失败 → 直接告知未检索到
        pass
    return json.dumps({"message": "未检索到相关内容（或该文件尚未建立章节索引）"}, ensure_ascii=False)



def _tool_regenerate_outline(project_id: int, node_id: int) -> str:
    """单章重新生成编写思路并覆盖保存（已存在思路也会覆盖；unwritten 自动升 thought_ready）。"""
    import json as _json

    node = query_one(
        "SELECT id, title, parent_id, status FROM chapter_node"
        " WHERE id = %s AND project_id = %s", (node_id, project_id),
    )
    if not node:
        return _err_chapter_not_found(project_id)
    title = node["title"]
    parent_row = query_one("SELECT title FROM chapter_node WHERE id = %s", (node["parent_id"],)) if node["parent_id"] else None
    path = [parent_row["title"]] if parent_row else []

    from app.agents.orchestrate_agent import generate_outline
    from app.services.experience_service import match_experiences
    from app.services.fact_service import confirmed_facts
    from app.services.lot_profile_service import lot_brief
    from app.services.matching import load_page_summaries, match_pages
    from app.services.requirement_service import requirements_for_chapter_text
    from app.services.section_service import update_outline_by_id

    lot_brief_text = lot_brief(project_id)  # 本标段工程构成（思路不得写本标段没有的工程）

    try:
        pages = match_pages(title, path, load_page_summaries())
        outline = generate_outline(
            chapter_title=title, chapter_type="",
            project_facts=confirmed_facts(project_id), knowledge_pages=pages,
            experiences=match_experiences(title, path),
            reqs_text=requirements_for_chapter_text(project_id, title),
            lot_brief=lot_brief_text,
        )
        outline["_toc_title"] = title
        outline["_parent"] = (parent_row["title"] if parent_row else "") or ""
        outline["_page_ids"] = [p["id"] for p in pages]
    except Exception as e:  # noqa: BLE001
        return _json.dumps({"ok": False, "error": f"思路生成失败：{e}"}, ensure_ascii=False)
    if not update_outline_by_id(project_id, node_id, outline):
        return _json.dumps({"ok": False, "error": "思路保存失败"}, ensure_ascii=False)
    return _json.dumps({
        "ok": True, "node_id": node_id, "title": title,
        "thinking": (outline.get("thinking") or "")[:200],
        "note": "该章编写思路已重新生成并覆盖保存，目录树已刷新",
    }, ensure_ascii=False)


def _tool_generate_outlines(project_id: int) -> str:
    """为 unwritten 节点批量生成编写思路：启动后台任务并返回 task_id。"""
    import json as _json

    from app.services import compile_service
    from app.services.task_runner import task_runner

    total = query_one("SELECT COUNT(*) c FROM chapter_node WHERE project_id = %s AND status = 'unwritten'", (project_id,))
    n = total["c"] if total else 0
    if not n:
        return _json.dumps({"started": False, "message": "目录中所有章节都已有编写思路，无需生成"}, ensure_ascii=False)
    tid = task_runner.submit("gen_outlines", project_id,
                             compile_service.generate_pending_outlines, project_id)
    return _json.dumps({"started": True, "total": n, "task_id": tid,
                        "message": "开始为 %d 个章节生成编写思路" % n}, ensure_ascii=False)


def _tool_edit_toc(project_id: int, ops: dict) -> str:
    """目录结构编辑（edit_toc）：批量 rename/delete/add，只改目录树不碰正文。"""
    import json as _json

    from app.services.section_service import add_node, delete_node_tree, rename_node_title

    summary = {"ok": True, "renamed": 0, "deleted": 0, "added": 0, "warnings": []}
    # 重命名
    for rn in ops.get("rename") or []:
        if not rename_node_title(project_id, int(rn.get("node_id") or 0), str(rn.get("title") or "")):
            summary["warnings"].append(f"重命名失败 node={rn.get('node_id')}")
        else:
            summary["renamed"] += 1
    # 删除（含子节点与正文）
    for nid in ops.get("delete") or []:
        try:
            n = delete_node_tree(project_id, int(nid))
            summary["deleted"] += n
        except ValueError as e:
            summary["warnings"].append(str(e))
    # 新增
    for ad in ops.get("add") or []:
        try:
            pid = ad.get("parent_id")
            add_node(project_id, str(ad.get("title") or ""), int(pid) if pid is not None else None)
            summary["added"] += 1
        except ValueError as e:
            summary["warnings"].append(str(e))
    return _json.dumps(summary, ensure_ascii=False)


def _tool_list_requirements(project_id: int, category: str = "") -> str:
    """查询结构化招标要求（tender_requirement 表——评分表逐项分值在此，准确）。

    已按当前标段过滤（各标段评分口径不同：如 5 标隧道 8 分、10 标桥梁 6 分）。
    """
    from app.services.requirement_service import list_requirements

    rows = list_requirements(project_id, category)
    total = sum(r["weight"] for r in rows if r["category"] == "评分办法" and r["weight"])
    return json.dumps(
        {"count": len(rows), "评分办法分值合计": total if total else None, "requirements": rows},
        ensure_ascii=False,
    )


def _progress_text(name: str, args: dict) -> str:
    if name == "rewrite_section":
        return "正在改写章节…"
    if name == "write_chapter":
        return "正在编写章节正文…（会等正文写完，完成后我会告诉你）"
    if name == "write_batch":
        return "正在启动批量生成…"
    if name == "edit_toc":
        return "正在调整目录…"
    if name == "regenerate_outline":
        return "正在重新生成该章编写思路…"
    if name == "generate_outlines":
        return "正在批量生成编写思路…"
    if name == "search_experiences":
        return "正在查询经验库…"
    if name == "list_project_files":
        return "正在读取项目文件清单…"
    if name == "search_project_files":
        return "正在检索项目文件原文…"
    if name == "list_tender_requirements":
        return "正在查询招标要求清单…"
    if name == "read_section":
        return f"正在读取章节 #{args.get('node_id', '')}…"
    if name == "search_knowledge_pages":
        return "正在检索知识页…"
    if name == "list_facts":
        return "正在查询事实表…"
    if name == "get_chapter_tree":
        return "正在读取目录树…"
    if name == "find_chapters":
        return "正在按标题/编号检索目录章节…"
    return "处理中…"


def _exec_tool(name: str, args: dict, project_id: int) -> str:
    import logging
    import time as _time
    log = logging.getLogger("chat_tools")
    log.info(f"[工具调用] {name} args={args}")
    t0 = _time.time()
    try:
        if name == "search_knowledge_pages":
            return _tool_search_pages(args)
        if name == "list_facts":
            return _tool_list_facts(project_id)
        if name == "get_chapter_tree":
            return _flat_tree(project_id)
        if name == "find_chapters":
            return _tool_find_chapters(project_id, str(args.get("keyword", "")))
        if name == "read_section":
            return _read_section(int(args.get("node_id", 0)), project_id)
        if name == "rewrite_section":
            return _rewrite_section(
                int(args.get("node_id", 0)), project_id,
                args.get("content", ""), args.get("summary", ""),
            )
        if name == "write_chapter":
            return _tool_write_chapter(project_id, int(args.get("node_id", 0)),
                                       str(args.get("extra_requirements") or ""))
        if name == "write_batch":
            return _tool_write_batch(project_id)
        if name == "edit_toc":
            return _tool_edit_toc(project_id, args)
        if name == "regenerate_outline":
            return _tool_regenerate_outline(project_id, int(args.get("node_id", 0)))
        if name == "generate_outlines":
            return _tool_generate_outlines(project_id)
        if name == "search_experiences":
            return _tool_search_experiences(args.get("keyword", ""))
        if name == "list_project_files":
            return _tool_list_project_files(project_id)
        if name == "search_project_files":
            return _tool_search_project_files(project_id, args.get("query", ""))
        if name == "list_tender_requirements":
            return _tool_list_requirements(project_id, args.get("category", ""))
        return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
    finally:
        log.info(f"[工具完成] {name} 耗时 {_time.time()-t0:.1f}s")
