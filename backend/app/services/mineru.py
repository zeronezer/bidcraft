"""MinerU 云端解析客户端（精准解析 API v4）。

本地文件流程：申请上传链接 → PUT 上传 → 自动提交 → 轮询结果 → 下载 ZIP。
  POST /api/v4/file-urls/batch          申请上传链接（返回 batch_id + file_urls）
  PUT  <file_url>                       上传文件（单次任务上限 200 页，故按页切块）
  GET  /api/v4/extract-results/batch/{batch_id}  轮询（state: waiting-file/pending/running/converting/done/failed）
ZIP 内关键文件：full.md（Markdown）、*_content_list.json（结构化内容列表，含图片/表格）
"""
import io
import json
import shutil
import socket
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import settings

MAX_PAGES_PER_TASK = 200  # MinerU 单任务页数上限
POLL_INTERVAL = 5
POLL_TIMEOUT = 1800

# 结果下载走的 CDN 域名可能被本机代理的 DNS 污染（解析到 198.18.x.x 之类的假 IP）导致 TLS 失败。
# 默认不做任何干预，走系统正常 DNS；若确实遇到该问题，在 .env 里用 MINERU_CDN_IPS 填写
# 一个或多个真实 IP（逗号分隔），下载时会把该域名临时钉到这些 IP。
# SNI 仍用原域名，证书校验不受影响。可用 DoH 重查：https://dns.google/resolve?name=cdn-mineru.openxlab.org.cn
CDN_HOST = "cdn-mineru.openxlab.org.cn"
CDN_REAL_IPS = [ip.strip() for ip in settings.mineru_cdn_ips.split(",") if ip.strip()]


def _client(timeout: float = 60) -> httpx.Client:
    """httpx 客户端：trust_env=False 绕过系统代理，全走直连。"""
    return httpx.Client(transport=httpx.HTTPTransport(trust_env=False), timeout=timeout, follow_redirects=True)


def _get_real_addrinfo(host, port, family=0, type_=0, proto=0, flags=0):
    """getaddrinfo 包装：CDN 域名返回固定真实 IP，其余走系统解析。"""
    if host == CDN_HOST:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in CDN_REAL_IPS]
    return _real_getaddrinfo(host, port, family, type_, proto, flags)


_real_getaddrinfo = socket.getaddrinfo


def _download_and_extract(zip_url: str, out_dir: Path) -> dict:
    """下载结果 ZIP 并解压。若配置了 CDN 真实 IP，下载期间把该域名钉到这些 IP。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    host = httpx.URL(zip_url).host
    patch_needed = host == CDN_HOST and bool(CDN_REAL_IPS)
    if patch_needed:
        socket.getaddrinfo = _get_real_addrinfo
    try:
        with _client(timeout=600) as c:
            r = c.get(zip_url)
        r.raise_for_status()
    finally:
        if patch_needed:
            socket.getaddrinfo = _real_getaddrinfo
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(out_dir)
    md_files = sorted(out_dir.rglob("full.md"))
    md = md_files[0].read_text(encoding="utf-8") if md_files else ""
    return {"dir": out_dir, "markdown": md}


@dataclass
class ParseResult:
    markdown_path: Path
    content_list_path: Path | None = None
    images_dir: Path | None = None
    parts: list[Path] = field(default_factory=list)
    markdown: str = ""

    @property
    def char_count(self) -> int:
        return len(self.markdown)


class MinerUClient:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        self.token = token or settings.mineru_api_token
        self.base = (base_url or settings.mineru_api_base).rstrip("/")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def split_page_ranges(total_pages: int, max_pages: int = MAX_PAGES_PER_TASK) -> list[str]:
        """把总页数切成 <=max_pages 的区间列表，如 361 → ['1-200', '201-361']"""
        return [
            f"{s}-{min(s + max_pages - 1, total_pages)}"
            for s in range(1, total_pages + 1, max_pages)
        ]

    def _request_upload_urls(self, files: list[dict], **opts) -> dict:
        """files: [{"name": "xxx.pdf", "page_ranges": "1-200", "data_id": "..."}]"""
        body = {"files": files, "language": "ch", "enable_table": True, **opts}
        with _client() as c:
            r = c.post(f"{self.base}/file-urls/batch", headers=self._headers(), json=body)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"申请上传链接失败: {data}")
        return data["data"]

    @staticmethod
    def _upload(file_url: str, data: bytes) -> None:
        with _client(timeout=600) as c:
            r = c.put(file_url, content=data)
        if r.status_code != 200:
            raise RuntimeError(f"上传文件失败: HTTP {r.status_code}")

    def _poll_batch(self, batch_id: str, expect: int) -> list[dict]:
        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            with _client(timeout=30) as c:
                r = c.get(f"{self.base}/extract-results/batch/{batch_id}", headers=self._headers())
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise RuntimeError(f"查询结果失败: {data}")
            results = data["data"]["extract_result"]
            if all(x["state"] in ("done", "failed") for x in results):
                return results
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"解析超时（{POLL_TIMEOUT}s），batch_id={batch_id}")

    def parse_pdf(
        self,
        file_path: Path,
        out_dir: Path,
        page_ranges: list[str] | None = None,
        model_version: str | None = None,
        is_ocr: bool = False,
        on_progress=print,
    ) -> ParseResult:
        """解析 PDF，返回结果。page_ranges 为空则整份（>200 页自动切块）。

        model_version 缺省取 settings.mineru_model_version（默认 pipeline，可 .env 覆盖）。
        """
        file_path = Path(file_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        model_version = model_version or settings.mineru_model_version

        if not page_ranges:
            total = self.count_pages(file_path)
            page_ranges = self.split_page_ranges(total)
        on_progress(f"[MinerU] 待解析页块: {page_ranges}")

        stem = file_path.stem[:40]
        files = [
            {
                "name": f"{stem}_{i}.pdf",
                "page_ranges": pr,
                "data_id": f"{stem}_{i}",
                "is_ocr": is_ocr,
            }
            for i, pr in enumerate(page_ranges)
        ]
        return self._run_batch(files, file_path.read_bytes(), out_dir, on_progress,
                               model_version=model_version)

    def parse_image(
        self,
        file_path: Path,
        out_dir: Path,
        on_progress=print,
        model_version: str | None = None,
    ) -> ParseResult:
        """解析单张图片（图纸/扫描页；MinerU 支持jpg/jpeg/png，内置 OCR）。"""
        file_path = Path(file_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        model_version = model_version or settings.mineru_model_version
        on_progress(f"[MinerU] 解析图片: {file_path.name}")

        stem = file_path.stem[:40]
        files = [{"name": file_path.name[:60], "data_id": stem}]
        return self._run_batch(files, file_path.read_bytes(), out_dir, on_progress,
                               model_version=model_version)

    def _run_batch(self, files: list[dict], payload: bytes, out_dir: Path,
                   on_progress=print, model_version: str = "pipeline") -> ParseResult:
        """提交一批文件（页块/图片）→ 上传 → 轮询 → 下载合并为 full.md。"""
        out_dir = Path(out_dir)
        up = self._request_upload_urls(files, model_version=model_version)
        batch_id, urls = up["batch_id"], up["file_urls"]

        for url in urls:
            self._upload(url, payload)
        print(f"[MinerU] 提交批次 batch={batch_id} 块数={len(urls)}（时间 {time.strftime('%H:%M:%S')}）")
        on_progress(f"[MinerU] 已上传 {len(urls)} 个文件块，等待解析…")

        results = self._poll_batch(batch_id, len(urls))

        merged_md: list[str] = []
        content_list: list[dict] = []
        parts: list[Path] = []
        for i, res in enumerate(sorted(results, key=lambda x: x.get("data_id", ""))):
            if res["state"] != "done":
                on_progress(f"[MinerU] 页块 {res.get('data_id')} 失败: {res.get('err_msg')}")
                continue
            part_dir = out_dir / f"part_{i}"
            extracted = _download_and_extract(res["full_zip_url"], part_dir)
            merged_md.append(extracted["markdown"])
            parts.append(part_dir)
            for p in part_dir.rglob("*_content_list.json"):
                content_list.extend(json.loads(p.read_text(encoding="utf-8")))
            on_progress(f"[MinerU] 页块 {i + 1}/{len(urls)} 完成")

        md_path = out_dir / "full.md"
        md_path.write_text("\n\n".join(merged_md), encoding="utf-8")

        cl_path = None
        if content_list:
            cl_path = out_dir / "content_list.json"
            cl_path.write_text(
                json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        markdown = md_path.read_text(encoding="utf-8")
        on_progress(f"[MinerU] 解析完成，Markdown {len(markdown)} 字符 → {md_path}")
        return ParseResult(
            markdown_path=md_path,
            content_list_path=cl_path,
            parts=parts,
            markdown=markdown,
        )

    @staticmethod
    def count_pages(file_path: Path) -> int:
        """读 PDF 页数：优先 PyMuPDF，缺失则用 pypdf。"""
        try:
            import fitz

            with fitz.open(file_path) as doc:
                return len(doc)
        except ImportError:
            from pypdf import PdfReader

            return len(PdfReader(str(file_path)).pages)

    def parse_file(self, file_path: Path, out_dir: Path, on_progress=print,
                   model_version: str | None = None) -> dict:
        """统一解析入口：docx/doc 先转 PDF，再与 PDF 走同一条 MinerU 流程。

        返回 {"markdown", "markdown_path", "content_list_path", "parser", "converted_from"?}
        """
        from app.services.doc_convert import convert_to_pdf

        file_path = Path(file_path)
        suffix = file_path.suffix.lower()

        if suffix in {".docx", ".doc"}:
            on_progress(f"[解析] {suffix} 先转 PDF（docx2pdf）")
            pdf_path = convert_to_pdf(file_path, out_dir)
            result = self.parse_pdf(pdf_path, out_dir, on_progress=on_progress,
                                    model_version=model_version)
            return {
                "markdown": result.markdown,
                "markdown_path": result.markdown_path,
                "content_list_path": result.content_list_path,
                "parser": "mineru",
                "converted_from": suffix.lstrip("."),
            }
        if suffix == ".pdf":
            result = self.parse_pdf(file_path, out_dir, on_progress=on_progress,
                                    model_version=model_version)
            return {
                "markdown": result.markdown,
                "markdown_path": result.markdown_path,
                "content_list_path": result.content_list_path,
                "parser": "mineru",
            }
        if suffix in {".jpg", ".jpeg", ".png"}:
            result = self.parse_image(file_path, out_dir, on_progress=on_progress,
                                      model_version=model_version)
            return {
                "markdown": result.markdown,
                "markdown_path": result.markdown_path,
                "content_list_path": result.content_list_path,
                "parser": "mineru",
            }
        raise ValueError(f"暂不支持的格式: {suffix}（仅支持 .pdf / .docx / .doc / 图片）")

    def cleanup_parts(self, result: ParseResult) -> None:
        """合并后可删除分页临时目录，只保留 full.md/content_list.json。"""
        for p in result.parts:
            shutil.rmtree(p, ignore_errors=True)


# 默认实例
mineru = MinerUClient()
