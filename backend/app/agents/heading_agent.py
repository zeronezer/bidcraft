"""标题切章（纯规则，无 AI）。

切章流程：
1. extract_headings    提取标题行（记录 # 深度 hash_level）+ 各标题正文与字数
2. md_has_real_hierarchy  判定标题是否带真实多级 #（python-docx 产物）
3. 分层二选一：
   - 真实 # 层级文档 → _reparent_by_hash：父 = 最近 # 深度更浅的标题（# 即层级）
   - 平层/无层级文档（MinerU 全 ##）→ calibrate：多级阿拉伯编号规则降级
     （"2.1.3"→父"2.1"；编号定不出则平层，宁平层不误挂）
4. _fill_levels         按 parent 链回填树深度
5. mark_keep_drop       程序判定 keep（正文≥60字或含表/图即产页）/ drop（封面目录整块丢弃）
6. build_chapter_tree   按章节切分正文，keep=true 的标题带父级路径产知识章节

2026-09-09：彻底去掉 AI 判父子（原 ai_assign_parents）。原因：docx 改走 python-docx
直读样式后产出真实 # 层级，# 深度即层级，无需 LLM；旧 MinerU 平层产物由编号规则降级。
"""
import re
from dataclasses import dataclass, field

MIN_LEAF_CHARS = 60   # 正文文字量阈值：达到即产知识页（用户定：60 字）

# 多级阿拉伯编号："2.1.3 主要工程…" → "2.1.3"
MULTI_NUM = re.compile(r"^(\d+(?:\.\d+)+)\s*[、．.]?\s*")
# 单级阿拉伯编号："1、工程概况" / "1 工程概况"
SINGLE_NUM = re.compile(r"^(\d+)\s*[、．.]\s*")

# 封面/目录类标题（整个节点连同正文丢弃）
DROP_TITLE_PATTERNS = [
    re.compile(r"^目\s*录$"),
    re.compile(r"^(正本|副本|封皮|封面|标书封面)$"),
]
# 目录页正文特征：点号/省略号引导行
TOC_DOTS = re.compile(r"\.{4,}|…{3,}|·{4,}")

# 表格/图片特征（正文含任一即产知识页，即使文字量不足 60）
IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
TABLE_MD_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
TABLE_HTML_RE = re.compile(r"<table[\s>]", re.I)


@dataclass
class Heading:
    i: int                      # 文档序号
    title: str                  # 标题文本
    body: str                   # 该标题下、下一标题前的正文（保留图片引用供提炼）
    parent: int | None = None   # 父序号（None=未定）
    level: int = 1              # 树深度（1 起，校准后回填）
    hash_level: int = 0         # 该标题在 md 中的 # 深度（0=非 # 标题，纯规则分层用）
    keep: bool = True           # 是否产知识页
    drop: bool = False          # 封面/目录：节点连同正文整体丢弃
    n_tables: int = 0           # 正文中的表格数（md/HTML 表）
    n_images: int = 0           # 正文中的图片引用数

    @property
    def body_len(self) -> int:
        """正文字数（不含图片引用标记）。"""
        return len(IMG_RE.sub("", self.body).strip())


@dataclass
class ChapterV2:
    """叶子章节（承载正文的知识单元）。"""
    leaf_title: str          # 叶子标题原文
    title: str               # 全路径标题："工程概况 > 2.3 主要工程内容和数量"
    path: list[str]          # 祖先标题链（不含自己，不含 drop 节点）
    level: int
    text: str                # 叶子正文
    index: int = field(default=0)


def extract_headings(markdown: str) -> list[Heading]:
    """从 md 提取标题行及各标题下的正文块。"""
    lines = markdown.splitlines()
    idx = [i for i, l in enumerate(lines) if re.match(r"^#{1,6}\s+\S", l)]
    heads: list[Heading] = []
    for n, start in enumerate(idx):
        end = idx[n + 1] if n + 1 < len(idx) else len(lines)
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", lines[start])
        title = m.group(2).strip() if m else re.sub(r"^#{1,6}\s+", "", lines[start]).strip()
        hash_level = len(m.group(1)) if m else 0
        body = "\n".join(lines[start + 1 : end]).strip()
        heads.append(
            Heading(
                i=n,
                title=title,
                body=body,
                hash_level=hash_level,
                n_tables=1 if (TABLE_MD_RE.search(body) or TABLE_HTML_RE.search(body)) else 0,
                n_images=len(IMG_RE.findall(body)),
            )
        )
    return heads


def _reparent_by_hash(headings: list[Heading]) -> None:
    """纯规则父级归属（真实多级 # 文档）：父 = 最近一个 # 深度更浅的标题。

    仅适用于 md 标题带真实层级（python-docx 按样式产出）；MinerU 全 ## 平层时
    所有标题同深，不会误挂（见 md_has_real_hierarchy）。
    """
    for h in headings:
        if h.hash_level <= 0:
            continue
        parent = None
        for prev in reversed(headings[: h.i]):
            if prev.hash_level > 0 and prev.hash_level < h.hash_level:
                parent = prev.i
                break
        h.parent = parent


def md_has_real_hierarchy(headings: list[Heading]) -> bool:
    """标题是否带真实 # 层级（python-docx 产物：出现 L3+ 深标题）。

    MinerU 产物几乎全 ##（max<=2）→ False，退回编号规则（calibrate）降级。
    """
    max_lv = max((h.hash_level for h in headings), default=0)
    deep = sum(1 for h in headings if h.hash_level >= 3)
    return max_lv >= 3 and deep >= 3


def _numbering_key(title: str) -> str | None:
    """提取章节编号："2.1.3 xx"→"2.1.3"，"1、编制依据"→"1"；无则 None。

    注意：单级只认顿号分隔（"1、章"）——"1.地层岩性"这类"数字.文字"是节内
    列表条目，不是章节编号，返回 None 交给 AI 判断归属。
    """
    t = title.strip()
    m = MULTI_NUM.match(t)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)\s*、", t)
    if m:
        return m.group(1)
    return None


def _fill_levels(headings: list[Heading]) -> None:
    """按 parent 链回填树深度（parent < i 保证无环）。"""
    for h in headings:
        depth, p, guard = 1, h.parent, 0
        while p is not None and guard < 20:
            depth += 1
            p = headings[p].parent if 0 <= p < len(headings) else None
            guard += 1
        h.level = depth


def calibrate(headings: list[Heading]) -> None:
    """编号规则校准（仅用于无真实 # 层级的平层文档降级）+ 树深度回填。

    - 多级编号标题：parent = 编号去掉最后一段的最近前序标题（如 2.1.3 → 2.1）；
      查不到父编号则逐级放宽（挂到更短前缀），都无则置根。
    - 无编号标题保持为根（平层降级，宁平层不误挂）。
    """
    # 编号 → 最近出现该编号的标题序号
    by_num: dict[str, int] = {}
    for h in headings:
        key = _numbering_key(h.title)
        if key:
            parts = key.split(".")
            # 逐级放宽：2.1.3 先找 2.1，再找 2
            parent_idx = None
            for k in range(len(parts) - 1, 0, -1):
                cand = ".".join(parts[:k])
                if cand in by_num:
                    parent_idx = by_num[cand]
                    break
            if parent_idx is not None:
                h.parent = parent_idx
            else:
                h.parent = None  # 顶层章
            by_num[key] = h.i
    _fill_levels(headings)


def mark_keep_drop(headings: list[Heading]) -> None:
    """程序判定 keep / drop（不依赖 AI）。"""
    for h in headings:
        t = h.title.strip()
        # drop：目录/封面标题，或目录页正文（点号引导行）
        if any(p.search(t) for p in DROP_TITLE_PATTERNS) or _is_toc_body(h.body):
            h.drop = True
            h.keep = False
            continue
        # drop：文档开头无编号的短正文标题（封面/扉页特征，含密级、投标单位等信息，
        # 正文可达两三百字，阈值须放宽）
        if h.i <= 2 and h.body_len < 300 and not MULTI_NUM.match(t) and not SINGLE_NUM.match(t):
            h.drop = True
            h.keep = False
            continue
        # keep 判定（用户规则）：文字 ≥60 字，或正文含表格/图片，任一满足即产知识页。
        # 这样"短正文但带供货表/示意图"的父节点不会被误丢。
        h.keep = h.body_len >= MIN_LEAF_CHARS or h.n_tables > 0 or h.n_images > 0


def _is_toc_body(text: str) -> bool:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if len(lines) < 8:
        return False
    dots = sum(1 for l in lines if TOC_DOTS.search(l))
    return dots >= 3 or dots / len(lines) > 0.3


def build_chapter_tree(headings: list[Heading]) -> list[ChapterV2]:
    """切分知识章节：所有 keep=true 的节点产知识页。

    - title 为全路径（祖先链 > 本标题），drop 节点不进路径；
    - 父节点自己也带正文时（如"2.2 主要技术标准"下既有总述又有子节），
      父节点同样产知识页——正文不丢，路径已体现层级。
    """
    children: dict[int, list[int]] = {}
    for h in headings:
        if h.drop:
            continue
        if h.parent is not None and 0 <= h.parent < h.i and not headings[h.parent].drop:
            children.setdefault(h.parent, []).append(h.i)

    def path_of(i: int) -> list[str]:
        chain, p, guard = [], headings[i].parent, 0
        while p is not None and guard < 50:
            node = headings[p] if 0 <= p < len(headings) else None
            if node is None or node.drop:
                break
            chain.append(node.title)
            p = node.parent
            guard += 1
        return list(reversed(chain))

    out: list[ChapterV2] = []
    for h in headings:
        if h.drop or not h.keep:
            continue
        path = path_of(h.i)
        out.append(
            ChapterV2(
                leaf_title=h.title,
                title=" > ".join([*path, h.title]),
                path=path,
                level=h.level,
                text=h.body,
                index=h.i,
            )
        )
    return out


def split_chapters_v2(markdown: str, on_progress=print) -> list[ChapterV2]:
    """切章入口（纯规则，无 AI）：md → 标题树 → 叶子章节（带父级路径）。

    - 带真实 # 层级（python-docx 产物）→ 按 # 深度定父子（_reparent_by_hash）；
    - 平层/无层级（MinerU 全 ##）→ 编号规则降级（calibrate，编号能定则定，定不出平层）。
    """
    heads = extract_headings(markdown)
    if not heads:
        return []
    on_progress(f"[分层] 提取标题 {len(heads)} 个")
    if md_has_real_hierarchy(heads):
        _reparent_by_hash(heads)
        _fill_levels(heads)
        on_progress("[分层] 真实 # 层级文档：按 # 深度建树")
    else:
        calibrate(heads)
        on_progress("[分层] 平层文档：编号规则降级建树")
    mark_keep_drop(heads)
    chapters = build_chapter_tree(heads)
    n_drop = sum(1 for h in heads if h.drop)
    n_shell = sum(1 for h in heads if not h.drop and not h.keep)
    on_progress(
        f"[分层] 知识章节 {len(chapters)} 个（剔除封面目录 {n_drop}、空壳 {n_shell}）"
    )
    return chapters
