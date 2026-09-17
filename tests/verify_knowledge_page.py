"""知识页提炼首轮验证（决定方案成败的关键验证，M3-1）。

流程：解析历史实施性施组 → 按标题切章 → 选章节 → LLM 提炼知识页 → 输出 JSON 供人工评审。

用法：
  # 1) 先看能切出哪些章节（默认解析前 60 页，省解析配额）
  python tests/verify_knowledge_page.py --doc "docs/样例数据/历史施组/技术标.pdf" --pages 1-60 --list

  # 2) 对指定章节提炼知识页
  python tests/verify_knowledge_page.py --doc "docs/样例数据/历史施组/技术标.pdf" --pages 1-60 --chapter 工程概况

  # 3) 复用已解析结果（不重复调用 MinerU）
  python tests/verify_knowledge_page.py --doc "docs/样例数据/历史施组/技术标.pdf" --reuse --chapter 施工方案

输出：tests/output/<文档名>_<章节>.json
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents.knowledge_agent import extract_knowledge_page, save_knowledge_page  # noqa: E402
from app.agents.parser_agent import parse_document, split_chapters  # noqa: E402


def resolve_doc_path(doc_arg: str) -> Path:
    p = Path(doc_arg)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def get_markdown(doc_path: Path, pages: str | None, reuse: bool):
    """解析或复用已解析结果，返回 (markdown, parsed_dir)。"""
    parsed_dir = PROJECT_ROOT / "uploads" / "parsed" / doc_path.stem[:40]
    md_path = parsed_dir / "full.md"

    if reuse and md_path.exists():
        print(f"[复用] 已解析结果: {md_path}")
        return md_path.read_text(encoding="utf-8"), parsed_dir

    page_ranges = [pages] if pages else None
    print(f"[解析] {doc_path.name}  页范围: {pages or '全文'}（首次解析需等待，结果会缓存）")
    result = parse_document(doc_path, parsed_dir, page_ranges=page_ranges)
    return result["markdown"], parsed_dir


def pick_chapters(chapters, keyword: str):
    if not keyword:
        return chapters
    return [c for c in chapters if keyword in c.title]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True, help="历史施组文件路径（相对项目根或绝对）")
    ap.add_argument("--pages", default="", help="解析页范围，如 1-60（省配额，PDF 有效）")
    ap.add_argument("--chapter", default="", help="章节标题关键字（为空则取第一个）")
    ap.add_argument("--all", action="store_true", help="提炼全部章节（成本较高）")
    ap.add_argument("--list", action="store_true", help="仅列出切分出的章节")
    ap.add_argument("--reuse", action="store_true", help="复用已解析的 Markdown")
    ap.add_argument("--max-chars", type=int, default=20000, help="单章最大输入字符数")
    args = ap.parse_args()

    doc_path = resolve_doc_path(args.doc)
    if not doc_path.exists():
        raise SystemExit(f"文件不存在: {doc_path}")

    markdown, parsed_dir = get_markdown(doc_path, args.pages or None, args.reuse)
    print(f"[解析] Markdown {len(markdown)} 字符，目录: {parsed_dir}")

    chapters = split_chapters(markdown)
    print(f"[切章] 共 {len(chapters)} 个章节（1~2 级标题）")

    if args.list or not (args.chapter or args.all):
        for c in chapters[:60]:
            print(f"  L{c.level}  {c.title[:50]:52s} {c.char_count:>7d} 字")
        if not chapters:
            print("  未识别到章节标题，可检查解析结果的标题格式")
        print("\n提示：用 --chapter <关键字> 指定要提炼的章节")
        return

    targets = chapters if args.all else pick_chapters(chapters, args.chapter)
    if not targets:
        raise SystemExit(f"未匹配到章节: {args.chapter}（可用 --list 查看）")

    out_dir = PROJECT_ROOT / "tests" / "output"
    for ch in targets:
        print(f"\n[提炼] {ch.title}（{ch.char_count} 字）…")
        text = ch.text[: args.max_chars]
        page = extract_knowledge_page(
            chapter_title=ch.title,
            chapter_text=text,
            doc_name=doc_path.name,
            source_location=f"pages={args.pages or 'all'}",
        )
        safe = "".join(c for c in ch.title if c not in r'\/:*?"<>|')[:30]
        out_path = save_knowledge_page(page, out_dir / f"{doc_path.stem[:20]}_{safe}.json")

        print(f"[完成] 章节类型: {page['chapter_type']}  标签: {page.get('tags')}")
        print(f"       要点 {len(page.get('key_points', []))} 条 / 表格 {len(page.get('tables', []))} 个"
              f" / 图片 {len(page.get('images', []))} 张 / 参考数字 {len(page.get('numbers', []))} 个")
        print(f"       方法说明预览: {page.get('method', '')[:120]}…")
        print(f"[输出] {out_path}")
        if len(targets) == 1:
            print("\n--- 知识页 JSON ---")
            print(json.dumps(page, ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
