"""docx 原生解析（python-docx）——对象级切章 + full.md 渲染。

2026-09-10 决策：docx（知识库 knowledge_doc 与项目文件 project_file）统一走 python-docx，
不再"转 md → 正则抠 # 标题"或 docx→PDF→MinerU：

- chapters_from_docx：对象级切章（知识库提炼用），按 Word 样式/图表编号建树，
  段落+表格原位归属，图表区标题(13.图表部分/13-N)归位，杜绝"转 md 吞并"。
- parse_docx：docx → full.md（项目文件/工法索引用，保留真实 # 层级供 file_index 按行取原文）。

.PDF 仍走 MinerU（无样式可读）。
已知取舍（待补）：无页码出处（MinerU 有）。
图片：段内 inline 图导出到 out_dir/images/ 并在文本插引用，由 knowledge_service
_image_index/_attach_image_urls 解析为 /uploads URL 挂到知识页 images.url（可预览）。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document

HEADING_STYLES = {
    "Heading 1": 1,
    "Heading 2": 2,
    "Heading 3": 3,
    "Heading 4": 4,
    "Heading 5": 5,
    "Heading 6": 6,
    "标题 1": 1,
    "标题 2": 2,
    "标题 3": 3,
}
TOC_STYLES = {"toc 1", "toc 2", "toc 3", "TOC 1", "TOC 2", "TOC 3", "toc"}

# 图表/章节编号标题：Subtitle / 6-正文 等样式 + 阿拉伯编号（13.图表部分 / 13-1施工…）
# 且不含制表符页码（目录页同编号行带 \t页码，正文标题无 tab）
_CHART_TITLE_RE = re.compile(r"^\s*(\d+(?:[.\-–]\d+)*)\s*[、.．]?\s*\S")
_CHART_TITLE_STYLES = {"Subtitle", "6-正文"}


@dataclass
class DocxChapter:
    """对象级切章产出的章节（字段对齐 heading_agent.ChapterV2 的消费方）。"""
    leaf_title: str            # 叶子标题原文
    title: str                 # 全路径标题："工程概况 > 线路概况"
    path: list[str]            # 祖先标题链（不含自己）
    level: int                 # Word 样式层级（Heading N 的 N）
    text: str = ""             # 该节正文（段落 + 内联表格 md）
    index: int = field(default=0)

    @property
    def body_len(self) -> int:
        """正文有效字符数（去表格分隔符与空白的粗估）。"""
        return len(re.sub(r"[\s|:—\-]+", "", self.text))


def _table_to_markdown(table) -> list[str]:
    """docx 表格 → Markdown 表格行列表（cell 压成单行、补齐列宽、整行全空跳过）。"""
    rows: list[list[str]] = []
    for row in table.rows:
        cells = []
        for c in row.cells:
            t = c.text.strip().replace("\n", " ").replace("\r", " ")
            t = " ".join(t.split())
            cells.append(t)
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |"]
    md.append("| " + " | ".join(["---"] * width) + " |")
    md.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return md


def _heading_level(style: str, text: str) -> int | None:
    """标题判定：Heading 1~6 → 样式层级；Subtitle/6-正文 + 编号文本 → 视为章级标题。

    返回 None 表示非标题。图表区标题（13.图表部分 / 13-N…）层级：
    - "13.图表部分" 编号段数 1 → 章级(1)
    - "13-1施工…"    编号段数 2 → 节级(2)
    与 Heading1=章、Heading2=节 对齐，并入同一棵树。
    """
    if style in HEADING_STYLES:
        return HEADING_STYLES[style]
    if style in _CHART_TITLE_STYLES and text and "\t" not in text:
        m = _CHART_TITLE_RE.match(text)
        if m:
            segs = re.split(r"[.\-–]", m.group(1))
            return max(1, min(len(segs), 6))
    return None


def chapters_from_docx(file_path: Path, min_text_chars: int = 60, out_dir: Path | None = None,
                       on_progress=print) -> list[DocxChapter]:
    """对象级直接切章：按 Word 样式/图表编号建树，段落+表格原位归属。

    返回 DocxChapter 列表（leaf/title 全路径/text 含内联表格 md 与图片引用）。
    正文有效字符 < min_text_chars 的纯标题空壳不产出。

    out_dir：若给，段内 inline 图片导出到 out_dir/images/，并在所属章节 text 插入
    `![图](导出文件名)` 引用——供 knowledge_service._image_index/_attach_image_urls
    解析为 /uploads URL 挂到知识页 images.url（前端可预览）。不给则只切章不导出图。
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    on_progress("[python-docx] 对象级切章（按 Word 样式）")
    doc = Document(str(Path(file_path)))

    img_dir = None
    if out_dir is not None:
        img_dir = Path(out_dir) / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
    img_counter = 0

    def export_para_images(p: Paragraph) -> str:
        """导出段内 inline 图片，返回待插入的 markdown 引用串（多图换行分隔）。"""
        nonlocal img_counter
        if img_dir is None:
            return ""
        refs = []
        from docx.oxml.ns import qn

        seen = set()
        for r in p.runs:
            for blip in r._element.findall(".//" + qn("a:blip")):
                embed = blip.get(qn("r:embed"))
                if not embed or embed in seen:
                    continue
                seen.add(embed)
                try:
                    part = p.part.related_parts[embed]
                except KeyError:
                    continue
                blob = getattr(part, "blob", None)
                if not blob:
                    continue
                ct = getattr(part, "content_type", "") or ""
                ext = {".png": ".png", ".jpg": ".jpg", "jpeg": ".jpg",
                       "gif": ".gif", "bmp": ".bmp", "webp": ".webp", "tiff": ".tiff"}.get(
                    ct.split("/")[-1], ".png")
                img_counter += 1
                fname = f"docx_{img_counter:04d}{ext}"
                (img_dir / fname).write_bytes(blob)
                refs.append(f"![图]({fname})")
        return "\n".join(refs)

    stack: list[DocxChapter] = []
    out: list[DocxChapter] = []

    def close_top():
        node = stack.pop()
        if node.body_len >= min_text_chars:
            out.append(node)

    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag.endswith("}p"):
            p = Paragraph(child, doc)
            style = p.style.name if p.style else ""
            text = p.text.strip()
            img_refs = export_para_images(p)
            if style in TOC_STYLES:
                continue  # 目录页跳过（含目录图也不导出）
            lvl = _heading_level(style, text) if text else None
            if lvl is not None:
                while stack and stack[-1].level >= lvl:
                    close_top()
                parent_titles = [n.leaf_title for n in stack]
                stack.append(DocxChapter(
                    leaf_title=text,
                    title=" > ".join([*parent_titles, text]),
                    path=parent_titles,
                    level=lvl,
                ))
                if img_refs:
                    stack[-1].text += img_refs + "\n"
            else:
                if stack:
                    if text:
                        stack[-1].text += text + "\n"
                    if img_refs:
                        stack[-1].text += img_refs + "\n"
        elif tag.endswith("}tbl"):
            tbl_md = _table_to_markdown(Table(child, doc))
            if tbl_md and stack:
                stack[-1].text += "\n".join(tbl_md) + "\n"

    while stack:
        close_top()
    for i, c in enumerate(out):
        c.index = i
    return out


def parse_docx(file_path: Path, out_dir: Path, on_progress=print, write_md: bool = False) -> dict:
    """docx → full.md（保留真实 # 层级，段落+表格原位；项目文件 / 工法索引用）。

    与 chapters_from_docx 同源遍历，但输出为行式 full.md（供 file_index 按行号取原文、
    MinerU 同构下游使用），而非对象级章节列表。标题按 _heading_level 判定转 #，
    Subtitle/6-正文 图表编号标题同样识别，杜绝"吞并"。
    返回: {"markdown", "markdown_path", "images_dir", "parser", "content_list_path": None}
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    file_path = Path(file_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = Document(str(file_path))
    lines: list[str] = []
    heading_count = 0
    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag.endswith("}p"):
            p = Paragraph(child, doc)
            style = p.style.name if p.style else ""
            text = p.text.strip()
            if not text or style in TOC_STYLES:
                continue
            lvl = _heading_level(style, text)
            if lvl is not None:
                lines.append(f"\n{'#' * lvl} {text}\n")
                heading_count += 1
            else:
                lines.append(text)
        elif tag.endswith("}tbl"):
            tbl_md = _table_to_markdown(Table(child, doc))
            if tbl_md:
                lines.extend(["\n", *tbl_md, "\n"])

    md = "\n".join(lines)
    # 2026-09-10 改造：**不再落 full.md**——章节原文随索引一起存进 file_section_index.content，
    # 取原文不再依赖文件按行切。md 文本直接返回给调用方（build_index(md_text=...)）。
    # write_md=True 时仍写盘（兼容需要实体 md 的场景，如工法浏览）。
    if write_md:
        md_path = out_dir / "full.md"
        md_path.write_text(md, encoding="utf-8")
    else:
        md_path = None
    return {
        "markdown": md,
        "markdown_path": md_path,
        "images_dir": None,
        "parser": "python-docx",
        "content_list_path": None,
    }

