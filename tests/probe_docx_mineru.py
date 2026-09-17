"""验证 docx 走 MinerU 全流程：上传 → 解析 → 下载，重点确认 content_list 是否有 page_idx。

绕过 mineru.parse_pdf（它开头就 count_pages，docx 会崩），直接调底层 API。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.mineru import _download_and_extract, mineru  # noqa: E402

ROOT = Path(r"D:/AI-claudecode/biaoshubianzhi")


def build_sample_docx(path: Path) -> None:
    """生成一个多页、带多级标题的测试 docx。"""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(12)

    doc.add_heading("某某铁路实施性施工组织设计", level=0)
    doc.add_paragraph("（本文件仅用于 MinerU docx 解析能力验证）")

    sections = [
        ("1 编制依据及编制范围", ["1.1 编制依据", "1.2 编制范围"]),
        ("2 工程概况", ["2.1 线路概况", "2.2 主要技术标准", "2.3 主要工程内容和数量"]),
        ("3 建设项目所在地区特征", ["3.1 自然特征", "3.2 交通运输情况"]),
        ("4 施工组织安排", ["4.1 施工总体目标", "4.2 施工组织机构"]),
    ]
    body = (
        "本标段线路全长 25.5km，设计时速 350km/h，主要工程内容包括路基、桥涵、隧道及轨道工程。"
        "施工总工期 540 日历天，计划开工日期 2025 年 3 月 1 日，竣工日期 2026 年 8 月 31 日。"
        "沿线地形以丘陵为主，地质条件复杂，需重点控制软土路基沉降与桥梁桩基施工质量。"
    )
    for i, (ch, subs) in enumerate(sections, 1):
        doc.add_heading(ch, level=1)
        doc.add_paragraph(body * 12)  # 撑页数，确保多页
        for j, sub in enumerate(subs, 1):
            doc.add_heading(sub, level=2)
            doc.add_paragraph(f"{sub} 的具体内容。" + body * 6)
        if i % 2 == 0:
            doc.add_page_break()
    doc.save(path)
    print("已生成测试文件: %s (%.1f KB)" % (path.name, path.stat().st_size / 1024), flush=True)


def main() -> None:
    fp = ROOT / "uploads" / "_probe_sample.docx"
    build_sample_docx(fp)

    print("申请上传链接（不传 page_ranges）...", flush=True)
    up = mineru._request_upload_urls(
        [{"name": fp.name, "data_id": "docx0"}], model_version="pipeline"
    )
    batch_id, urls = up["batch_id"], up["file_urls"]
    print("batch_id=%s urls=%d" % (batch_id, len(urls)), flush=True)

    payload = fp.read_bytes()
    for url in urls:
        mineru._upload(url, payload)
    print("已上传 %.1f KB，等待解析..." % (len(payload) / 1024), flush=True)

    results = mineru._poll_batch(batch_id, len(urls))
    out = ROOT / "uploads" / "parsed" / "_docx_probe"
    for res in results:
        print("state=%s err=%s" % (res["state"], res.get("err_msg")), flush=True)
        if res["state"] != "done":
            continue
        extracted = _download_and_extract(res["full_zip_url"], out)
        md = extracted["markdown"]
        print("\n=== Markdown 字符数: %d ===" % len(md), flush=True)
        print(md[:900], flush=True)

        for p in sorted(out.rglob("*_content_list.json")):
            blocks = json.loads(p.read_text(encoding="utf-8"))
            print("\n=== content_list: %d 块 (%s) ===" % (len(blocks), p.name), flush=True)
            keys = set()
            for b in blocks[:80]:
                if isinstance(b, dict):
                    keys.update(b.keys())
            print("字段: %s" % sorted(keys), flush=True)
            has_page = [b for b in blocks if isinstance(b, dict) and b.get("page_idx") is not None]
            print("带 page_idx 的块: %d / %d" % (len(has_page), len(blocks)), flush=True)
            if has_page:
                print("page_idx 范围: %s ~ %s" % (
                    min(b["page_idx"] for b in has_page),
                    max(b["page_idx"] for b in has_page),
                ), flush=True)
            titled = [b for b in blocks if isinstance(b, dict) and b.get("text_level")][:12]
            print("--- 标题块 ---", flush=True)
            for b in titled:
                print("  page=%s level=%s | %s" % (
                    b.get("page_idx"), b.get("text_level"), (b.get("text") or "")[:44]
                ), flush=True)
            break


if __name__ == "__main__":
    main()
