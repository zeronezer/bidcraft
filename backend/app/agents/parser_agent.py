"""解析智能体（M5-1, P0）：文档解析 + 章节切分。

解析路线（**2026-09-10 现行**）：按"有没有样式可读"分流，不是按文件新旧

- **PDF / 扫描件 / 图片 → MinerU 云端 API**（内置 OCR，>200 页自动切块）。
  这三类没有可读样式，只能靠 OCR + 版面还原，并顺带拿到页码出处。
- **docx（全部场景，项目文件与知识库）→ python-docx 原生解析**，不转 PDF：
  * 知识库历史施组：`chapters_from_docx` 对象级切章（按 Word 样式建树，
    段落/表格/图片原位归属），直接产出章节列表，绕开 markdown 中转；
  * 项目文件 / 工法：`parse_docx` 产出 full.md，保留真实多级 `#` 层级，
    供 file_index 按行取原文。
  * 取舍：放弃 docx 的页码出处（PDF 仍有）。docx 资料以内容参考为主，
    真正需要精确页码的招标文件/图纸绝大多数本身就是 PDF。
  * 历史：曾短暂改为"docx → docx2pdf → MinerU"统一链路，2026-09-10 推翻——
    docx2pdf 依赖本机 Office/WPS（COM 调用），部署门槛高且会主动丢结构信息。

- 章节切分：split_chapters_v2（heading_agent，AI 重建标题树）为现行管线；
  下方 split_chapters 为旧版纯规则切分，仅测试引用
"""
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.mineru import mineru

PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}  # 图片图纸/扫描页（MinerU 内置 OCR）
SPREADSHEET_SUFFIXES = {".xlsx", ".xls"}    # 电子表格（直读，不经 MinerU）


@dataclass
class Chapter:
    title: str
    level: int
    start_line: int
    end_line: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


# Markdown 标题（MinerU 已识别正文标题为 # 形式，最可靠）
HASH_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# 中文/数字编号标题（docx2python 兜底；MinerU 未给 # 时用）
CN_HEADING = re.compile(r"^第[一二三四五六七八九十百]+[章节篇][、.\s]*(.*)$")
NUM_HEADING = re.compile(r"^(\d+(?:\.\d+){0,3})\s*[、．.]\s*(\S.{0,60})$")
# 目录行特征：含一串点号（目录里的页号引导点），非正文标题，需跳过
TOC_DOTS = re.compile(r"\.{4,}")

# 封面/目录类标题（非正文，切章时跳过）
SKIP_TITLES = {"目 录", "目录", "正本", "副本"}


def _num_level(text: str) -> int | None:
    """从标题文本的编号推断层级，返回层级或 None（非章节标题）。

    规则：
    - '1、xxx'（数字+顿号）→ 一级章
    - '1.1 xxx' / '1.1.1 xxx'（多级点号）→ 按点号深度计层
    - '1.地层岩性' / '2.《书名》'（单点号 + 直接跟文字）→ 正文列表，None
    """
    m = re.match(r"^(\d+(?:\.\d+)*)\s*([、．.\s])(.*)$", text)
    if not m:
        return None
    num, sep, rest = m.group(1), m.group(2), m.group(3)
    has_dot = "." in num
    if sep in ("、",) or has_dot:
        # 顿号分隔（1、）或 多级点号（1.1）都是章节
        return num.count(".") + 1
    # 单点号 + 空格/文字：正文列表，非章节
    return None


def detect_heading(line: str, use_text_heuristics: bool = True) -> tuple[int, str] | None:
    """识别标题行，返回 (层级, 标题文本)。

    use_text_heuristics=False 时只用 `#` 标题（文档已标准化的情况）；
    True 时额外启用纯文本编号标题兜底（docx2python 等未标 # 的旧解析）。
    """
    line = line.strip()
    if not line or len(line) > 80:
        return None
    if TOC_DOTS.search(line):
        return None
    m = HASH_HEADING.match(line)
    if m:
        title = m.group(2).strip()
        if title in SKIP_TITLES:
            return None
        # MinerU 常把多级标题都标成 ##，用编号深度纠正真实层级
        level = _num_level(title) or len(m.group(1))
        return level, title
    if not use_text_heuristics:
        return None
    m = CN_HEADING.match(line)
    if m:
        return 1, line
    m = NUM_HEADING.match(line)
    if m:
        return _num_level(line) or (m.group(1).count(".") + 1), line
    return None


def split_chapters(markdown: str, min_level: int = 1, max_level: int = 2) -> list[Chapter]:
    """按标题切分章节。默认取 1~2 级（章 / 节）。

    策略：文档里只要存在 `#` 标题（MinerU / python-docx 都已标准化输出），
    就只用 `#` 标题切章，避免纯文本兜底正则误判正文编号列表；
    仅当完全没有 `#` 标题时，才回退到纯文本编号标题识别。
    """
    lines = markdown.splitlines()
    has_hash = any(HASH_HEADING.match(l) for l in lines)

    heads: list[tuple[int, int, str]] = []  # (line_no, level, title)
    for i, line in enumerate(lines):
        hit = detect_heading(line, use_text_heuristics=not has_hash)
        if hit and min_level <= hit[0] <= max_level:
            heads.append((i, hit[0], hit[1]))

    chapters: list[Chapter] = []
    for idx, (ln, level, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        text = "\n".join(lines[ln:end]).strip()
        if text:
            chapters.append(Chapter(title=title, level=level, start_line=ln, end_line=end, text=text))
    return chapters


def parse_document(
    file_path: Path,
    out_dir: Path,
    page_ranges: list[str] | None = None,
    is_ocr: bool = False,
    on_progress=print,
    docx_mode: str = "mineru",
) -> dict:
    """解析文档，返回 {"markdown", "markdown_path", "content_list_path", "parser"}。

    - PDF/图片：统一 MinerU（docx_mode 无关）。
    - .docx 两种路线（docx_mode）：
      * "python_docx"（**现行默认，docx 都走这条**）：直读 Word 段落样式，
        产出真实多级 # 标题，供 file_index 建树与按行取原文；无页码出处。
      * "mineru"（**已废弃路线，仅作兜底保留**）：docx2pdf 转 PDF 后走 MinerU。
        依赖本机 Office/WPS，且会把 docx 原有结构拍平，2026-09-10 起不再使用。
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix in DOCX_SUFFIXES:
        if docx_mode == "python_docx":
            from app.services.docx_parser import parse_docx

            on_progress(f"[解析] python-docx 样式解析（保留标题层级）: {file_path.name}")
            r = parse_docx(file_path, out_dir, on_progress=on_progress)
            return {
                "markdown": r["markdown"],
                "markdown_path": r["markdown_path"],
                "images_dir": r.get("images_dir"),
                "parser": "python-docx",
                "content_list_path": None,
            }
        on_progress(f"[解析] docx 转 PDF 后走 MinerU: {file_path.name}")
        result = mineru.parse_file(file_path, out_dir, on_progress=on_progress)
        return {
            "markdown": result["markdown"],
            "markdown_path": result["markdown_path"],
            "images_dir": None,
            "parser": "mineru",
            "content_list_path": result.get("content_list_path"),
        }
    if suffix in PDF_SUFFIXES:
        on_progress(f"[解析] MinerU 解析: {file_path.name}")
        result = mineru.parse_pdf(
            file_path,
            out_dir,
            page_ranges=page_ranges,
            is_ocr=is_ocr,
            on_progress=on_progress,
        )
        return {
            "markdown": result.markdown,
            "markdown_path": result.markdown_path,
            "images_dir": None,
            "parser": "mineru",
            "content_list_path": result.content_list_path,
        }
    if suffix in IMAGE_SUFFIXES:
        on_progress(f"[解析] MinerU 解析图片: {file_path.name}")
        result = mineru.parse_image(file_path, out_dir, on_progress=on_progress)
        return {
            "markdown": result.markdown,
            "markdown_path": result.markdown_path,
            "images_dir": None,
            "parser": "mineru",
            "content_list_path": result.content_list_path,
        }
    if suffix in SPREADSHEET_SUFFIXES:
        # 表格直读（精确无损，不走 MinerU），产物 full.md 与 MinerU 管线同构
        from app.services.spreadsheet import parse_spreadsheet

        on_progress(f"[解析] 表格直读: {file_path.name}")
        markdown = parse_spreadsheet(file_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / "full.md"
        md_path.write_text(markdown, encoding="utf-8")
        return {
            "markdown": markdown,
            "markdown_path": md_path,
            "images_dir": None,
            "parser": "spreadsheet",
            "content_list_path": None,
        }
    raise ValueError(f"暂不支持的格式: {suffix}（仅支持 .pdf / .docx / 图片 / .xlsx/.xls）")
