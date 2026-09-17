"""电子表格解析（xlsx/xls 直读，不走 MinerU）。

表格是结构化数据，直接读库比"转 PDF → MinerU OCR"精确且零成本：
- 单元格数值原样保留（MinerU 转图 OCR 会把 28.5 认成 28.6，工程参数不能容忍）；
- 合并单元格值前向填充（附表的表头/标题行大量跨列合并，不填充则丢语义）；
- 每个 Sheet 输出为一段 Markdown 表格（## Sheet名 + 表格），
  产物 full.md 与 MinerU 管线同构——事实抽取、向量切片按章取材无需任何改动。

限制（防失控）：每个 Sheet 输出字符数上限 MAX_SHEET_CHARS（超出即截断该 Sheet 后续行），
另保留行列硬上限兜底（防单 Sheet 极端巨大时内存/耗时失控）。
"""
from pathlib import Path

MAX_SHEET_CHARS = 100_000  # 每 Sheet 输出 Markdown 总字符上限（防超长表撑爆下游）
MAX_ROWS = 2000            # 每 Sheet 硬行上限（正常由字符上限先截断，此值兜底）
MAX_COLS = 40              # 每 Sheet 最多列数
MAX_SHEETS = 30            # 最多 Sheet 数


def _fmt(v) -> str:
    """单元格值 → 显示文本。"""
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))  # 28.0 → 28（避免列宽爆炸）
    s = str(v).strip()
    return s.replace("|", "\\|").replace("\n", " ")


def _rows_to_markdown(rows: list[list[str]]) -> str:
    """二维表 → Markdown 表格（全空行/列压缩，行内连续重复单元格折叠）。"""
    # 去掉尾部全空行
    while rows and all(not c for c in rows[-1]):
        rows = rows[:-1]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # 合并单元格填充后标题行同值重复（含中间隔空列）：与最近一个非空值相同则折叠为空
    collapsed = []
    for r in rows:
        out: list[str] = []
        last = None
        for c in r:
            if c:
                if c == last:
                    out.append("")
                else:
                    out.append(c)
                    last = c
            else:
                out.append("")
        collapsed.append(out)
    rows = collapsed
    lines = ["| " + " | ".join(rows[0]) + " |",
             "|" + "|".join([" --- "] * width) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _row_chars(row: list[str]) -> int:
    """单行转 Markdown 的大致字符成本（含管道/空格/换行），用于字符预算截断。"""
    return sum(len(c) for c in row) + 4 * len(row) + 1


def _read_rows_budget(iterable, budget: int) -> list[list[str]]:
    """按行取数直到累计 Markdown 字符成本接近预算；返回已读行。"""
    rows: list[list[str]] = []
    used = 0
    for row in iterable:
        r = [_fmt(v) for v in row]
        used += _row_chars(r)
        if rows and used > budget:
            break  # 超过预算：停（保留已满预算内的前几行）
        rows.append(r)
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _fill_merged_xlsx(ws, values: list[list[str]]) -> None:
    """openpyxl：合并单元格区域用左上角值填充整块。"""
    for rng in ws.merged_cells.ranges:
        v = _fmt(ws.cell(rng.min_row, rng.min_col).value)
        for r in range(rng.min_row, min(rng.max_row, MAX_ROWS) + 1):
            for c in range(rng.min_col, min(rng.max_col, MAX_COLS) + 1):
                if 0 <= r - 1 < len(values) and 0 <= c - 1 < len(values[r - 1]):
                    values[r - 1][c - 1] = v


def parse_xlsx(file_path: Path) -> str:
    """xlsx → Markdown（openpyxl 直读）。每 Sheet 输出字符上限 MAX_SHEET_CHARS。"""
    from openpyxl import load_workbook

    wb = load_workbook(file_path, data_only=True, read_only=False)
    parts: list[str] = []
    for ws in wb.worksheets[:MAX_SHEETS]:
        values = _read_rows_budget(
            ws.iter_rows(min_row=1, max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True),
            MAX_SHEET_CHARS,
        )
        _fill_merged_xlsx(ws, values)
        md = _rows_to_markdown(values)
        if md:
            parts.append(f"## {ws.title}\n\n{md}")
    wb.close()
    return "\n\n".join(parts)


def _fill_merged_xls(sheet, values: list[list[str]]) -> None:
    """xlrd：merged_cells 为 (起始行, 结束行, 起始列, 结束列)，含头不含尾。"""
    nrows, ncols = len(values), (len(values[0]) if values else 0)
    for r0, r1, c0, c1 in (sheet.merged_cells or []):
        if r0 >= nrows or c0 >= ncols:
            continue
        v = values[r0][c0] if r0 < nrows and c0 < ncols else ""
        for r in range(r0, min(r1, nrows)):
            for c in range(c0, min(c1, ncols)):
                values[r][c] = v


def parse_xls(file_path: Path) -> str:
    """xls → Markdown（xlrd 直读，xlrd>=2.0 仅支持 .xls）。每 Sheet 输出字符上限 MAX_SHEET_CHARS。"""
    import xlrd

    book = xlrd.open_workbook(str(file_path), formatting_info=False)
    parts: list[str] = []
    for sheet in book.sheets()[:MAX_SHEETS]:
        ncols = min(sheet.ncols, MAX_COLS)
        values = _read_rows_budget(
            (sheet.row_values(r, 0, ncols) for r in range(min(sheet.nrows, MAX_ROWS))),
            MAX_SHEET_CHARS,
        )
        _fill_merged_xls(sheet, values)
        md = _rows_to_markdown(values)
        if md:
            parts.append(f"## {sheet.name}\n\n{md}")
    return "\n\n".join(parts)


SPREADSHEET_SUFFIXES = {".xlsx", ".xls"}


def parse_spreadsheet(file_path: Path) -> str:
    """电子表格统一入口，返回 Markdown（与 MinerU full.md 同构）。"""
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        return parse_xlsx(file_path)
    if suffix == ".xls":
        return parse_xls(file_path)
    raise ValueError(f"暂不支持的表格格式: {suffix}（仅支持 .xlsx / .xls）")
