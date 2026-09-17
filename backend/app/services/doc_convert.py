"""docx/doc → PDF 转换（docx2pdf，Windows 走 Office/WPS COM）。

- 本机无 Word，但有 WPS：WPS 注册了 Word.Application 兼容 ProgID，
  docx2pdf.convert() 直接可用（实测 4.6s / 13 页）。
- 转换目的：统一走 MinerU PDF 流程（docx 直传 MinerU 无 page_idx 页码，
  转 PDF 后页码/切块/出处定位全部共用一套逻辑）。
"""
from pathlib import Path


def convert_to_pdf(src: Path, out_dir: Path) -> Path:
    """把 docx/doc 转为 PDF，返回生成的 PDF 路径。

    转换产物与源文件同 stem，放在 out_dir 下。
    docx2pdf 内部 Dispatch("Word.Application")；WPS 环境自动兼容。
    """
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src.stem}.pdf"

    from docx2pdf import convert

    convert(str(src), str(dst))
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"docx 转 PDF 失败: {src.name}")
    return dst
