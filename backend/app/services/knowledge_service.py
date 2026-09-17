"""知识提炼管线（M3-1 核心链路）：知识库文档 → 解析 → 章节切分 → 提炼知识页 → 入库。

- .pdf/图片统一走 MinerU（full.md + content_list 页码）；.docx 走 python-docx 直读样式
  （2026-09-09 决策：docx 直读保留真实多级 # 标题，切章改纯规则分层——见 heading_agent）。
- 标题切分：heading_agent.split_chapters_v2（纯规则：# 建树 或 编号降级）→ keep 判定 → 知识章节；
- 知识页 title 存全路径（"工程概况 > 2.3 主要工程内容"），正文只按叶子标题块切分；
- 不使用章节类型分类（用户决策：匹配按标题路径，类型字段留空兼容旧表结构）。

由 task_runner 异步执行，任务进度写 task 表。章节提炼并发执行（提炼并发度 EXTRACT_CONCURRENCY）。
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.agents.heading_agent import ChapterV2, split_chapters_v2
from app.agents.knowledge_agent import extract_knowledge_page
from app.agents.parser_agent import parse_document
from app.core.config import settings
from app.core.database import execute, query_one

EXTRACT_CONCURRENCY = 20     # 逐章提炼并发度（模型 tpm 500 万，20 并发仍有余量；限流由 llm 指数退避兜底）


def _set_task_progress(task_id: int, progress: int, detail: str = "") -> None:
    """更新 task 表进度（供前端轮询展示）。"""
    if detail:
        execute(
            "UPDATE task SET progress = %s, detail = %s, updated_at = NOW() WHERE id = %s",
            (progress, detail, task_id),
        )
    else:
        execute("UPDATE task SET progress = %s, updated_at = NOW() WHERE id = %s", (progress, task_id))


def _md_has_hierarchy(markdown: str) -> bool:
    """探测 md 是否带真实多级 # 标题（python-docx 产物有 L3+；MinerU 几乎全 ## 无）。"""
    import re as _re

    max_lv, deep = 0, 0
    for ln in (markdown or "").splitlines():
        m = _re.match(r"^(#{1,6})\s+\S", ln)
        if m:
            lv = len(m.group(1))
            max_lv = max(max_lv, lv)
            if lv >= 3:
                deep += 1
    return max_lv >= 3 and deep >= 3


def _page_index(content_list_path: str | None) -> list[tuple[int, str]]:
    """从 MinerU content_list 构建 [(page_idx, 文本)]，用于章节→页码定位。"""
    from pathlib import Path

    if not content_list_path:
        return []
    p = Path(content_list_path)
    if not p.exists():
        return []
    try:
        blocks = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    out = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("page_idx") is not None:
            text = (b.get("text") or "").strip()
            if text:
                out.append((int(b["page_idx"]), text))
    return out


def _norm(s: str) -> str:
    import re as _re

    return _re.sub(r"\s+", "", s)


def _count_words(text: str) -> int:
    """预计字数：该章原文有效字数（去图片引用与 markdown 标记，作为生成篇幅参考）。"""
    import re as _re

    t = _re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")   # 图片引用
    t = _re.sub(r"^#{1,6}\s+", "", t, flags=_re.M)          # 标题井号
    t = _re.sub(r"[\s|:\-—]+", "", t)                        # 空白与表格分隔符
    return len(t)


def _clean_ref_text(text: str) -> str:
    """历史原文参考：去掉图片引用行、压掉多余空行后**全文保留**（不设长度上限，入库存完整原文；
    AI 注入时由 writer_agent 再按需截断，两者解耦）。"""
    import re as _re

    lines = []
    for ln in (text or "").splitlines():
        if _re.search(r"!\[[^\]]*\]\([^)]*\)", ln):
            continue
        lines.append(ln.rstrip())
    # 压掉连续 3 行以上空行
    out, blank = [], 0
    for ln in lines:
        if ln.strip():
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank <= 1:
                out.append(ln)
    return "\n".join(out).strip()


def backfill_page_ref_text(page_ids: list[int] | None = None, force: bool = False) -> int:
    """历史内容参考回填：为知识页 content 补齐 ref_text（历史章节原文）。

    force=False：仅补还没有 ref_text 的页；force=True：全部重算为**完整原文**
    （用于旧版曾截断 8000 字后升级为全文存储时重灌）。标题定位：知识页 title 的
    叶子（如 "1.2 编制范围"）→ 在 md 中匹配 `#* 标题`，正文取该标题到下一个
    同级/上级标题之间的内容。
    """
    import re as _re

    from app.core.database import execute, query

    sql = (
        "SELECT kp.id, kp.title, kp.content, kd.parsed_path FROM knowledge_page kp"
        " JOIN knowledge_doc kd ON kd.id = kp.doc_id"
    )
    params: list = []
    if page_ids:
        marks = ",".join(str(int(i)) for i in page_ids)
        sql += f" WHERE kp.id IN ({marks})"
    rows = query(sql, tuple(params))
    updated = 0
    for r in rows:
        try:
            content = json.loads(r["content"]) if isinstance(r["content"], str) else (r["content"] or {})
        except (TypeError, ValueError):
            content = {}
        need = force or not content.get("ref_text")
        if need:
            leaf = str(r["title"] or "").split(" > ")[-1].strip()
            ref = _clean_ref_text(_extract_section_text(r.get("parsed_path"), leaf))
            if ref and ref != content.get("ref_text"):
                content["ref_text"] = ref
                execute("UPDATE knowledge_page SET content = %s WHERE id = %s",
                        (json.dumps(content, ensure_ascii=False), r["id"]))
                updated += 1
    return updated


def _extract_section_text(parsed_path: str | None, leaf: str) -> str:
    """在源文档 full.md 中按叶子标题提取章节原文（标题匹配去空白后相等优先，其次包含）。"""
    import re as _re

    from pathlib import Path

    if not parsed_path:
        return ""
    md_path = Path(parsed_path) / "full.md"
    if not md_path.exists():
        return ""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    key = _re.sub(r"\s+", "", leaf or "")
    if not key:
        return ""
    # 标题行索引（level, 去空白标题）
    heads: list[tuple[int, int, str]] = []  # (行号, level, 规范化标题)
    for i, ln in enumerate(lines):
        m = _re.match(r"^(#{1,6})\s+(.*?)\s*$", ln)
        if m and m.group(2).strip():
            heads.append((i, len(m.group(1)), _re.sub(r"\s+", "", m.group(2))))
    # 找与叶子标题最匹配的标题行（精确优先，其次标题包含叶子）
    hit = None
    for i, lv, h in heads:
        if h == key:
            hit = (i, lv)
            break
    if hit is None:
        for i, lv, h in heads:
            if key in h or h in key:
                hit = (i, lv)
                break
    if hit is None:
        return ""
    start, lv = hit
    end = len(lines)
    for i, hl, _h in heads:
        if i > start and hl <= lv:
            end = i
            break
    return "\n".join(lines[start + 1:end])


_img_index_cache: dict[str, dict[str, str]] = {}


def _image_index(out_dir: Path) -> dict[str, str]:
    """解析目录下全部图片的 文件名 → /uploads URL 索引（按目录缓存）。

    MinerU 分块解析时原图位于 part_N/images/ 下，full.md 里的引用是相对路径，
    按文件名全目录索引匹配最稳。
    """
    key = str(out_dir)
    if key in _img_index_cache:
        return _img_index_cache[key]
    index: dict[str, str] = {}
    if Path(out_dir).exists():
        for p in Path(out_dir).rglob("*"):
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                try:
                    index.setdefault(p.name, f"/uploads/{p.relative_to(settings.uploads_dir).as_posix()}")
                except ValueError:
                    continue
    _img_index_cache[key] = index
    return index


def _attach_image_urls(images: list[dict], chapter_text: str, out_dir) -> list[dict]:
    """把章节原文里的图片引用解析为可访问 URL，按顺序挂到提炼出的 images 条目上。

    MinerU 把切出的原图存放在解析目录（分块时在 part_N/images/）下，原文章节里
    以 ![](images/xx.jpg) 引用。挂接后前端可真实预览/放大；未匹配到的条目保持无 url。
    """
    import re as _re

    refs = _re.findall(r"!\[[^\]]*\]\(([^)]+)\)", chapter_text or "")
    if not refs or not images:
        return images
    index = _image_index(out_dir)
    urls: list[str] = []
    for r in refs:
        url = index.get(Path(r.strip()).name)
        if url:
            urls.append(url)
    for i, img in enumerate(images):
        if isinstance(img, dict) and i < len(urls):
            img.setdefault("url", urls[i])
    return images


def _locate_pages(index: list[tuple[int, str]], title: str) -> str:
    """按标题文本在页索引里找起始页（页码从 1 起计）。"""
    if not index:
        return ""
    nt = _norm(title)
    if not nt:
        return ""
    for page, text in index:
        ne = _norm(text)
        if ne == nt or ne.startswith(nt[:20]) or nt.startswith(ne[:20]):
            return f"P{page + 1}"
    return ""


def process_doc(task_id: int, doc_id: int) -> None:
    """解析 + 提炼单个知识库文档：历史施组→知识页；工艺工法→整篇概况索引。"""
    doc = query_one("SELECT * FROM knowledge_doc WHERE id = %s", (doc_id,))
    if not doc:
        return

    execute("UPDATE knowledge_doc SET status = 'parsing' WHERE id = %s", (doc_id,))
    _set_task_progress(task_id, 5, "解析中")

    try:
        is_method = doc["doc_category"] == "method"

        file_path = settings.uploads_dir / doc["file_path"]
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {doc['file_path']}")
        out_dir = settings.uploads_dir / "parsed" / str(doc_id)
        is_docx = file_path.suffix.lower() == ".docx"

        # 2) 工艺工法（method）→ 整篇概况索引（一工法一条，正文编写时选工法取整文注入）
        #    不再逐章提炼结构化条目（2026-09-04 用户决策：工法文档=一个完整工艺）
        if is_method:
            from app.services.method_index import build as build_method_overview

            ov = build_method_overview(doc_id, str(out_dir))
            execute(
                "UPDATE knowledge_doc SET status = 'parsed', parsed_path = %s WHERE id = %s",
                (str(out_dir), doc_id),
            )
            _set_task_progress(task_id, 100, f"完成：已建立工法概况「{ov['name']}」")
            return

        # 历史施组（sod）路径，按来源分两套：
        #  - .docx → 对象级直接切章（docx_parser.chapters_from_docx，绕开 md）：
        #    Word 样式边界切章、图表区(13-N)归位、表格内联，杜绝"转 md 吞并"。
        #    重新提炼总是从源 docx 重切（对象切章快、无 MinerU/网络），不复用旧 md 产物。
        #  - .pdf / 其他 → MinerU → full.md → split_chapters_v2（纯规则 # 建树 / 编号降级）。
        _set_task_progress(task_id, 12, "切分章节中")
        chapters = []
        parsed = {"content_list_path": None}
        if is_docx:
            import shutil as _shutil
            from app.services.docx_parser import chapters_from_docx

            out_dir.mkdir(parents=True, exist_ok=True)
            # 清理上次解析产物（images 目录防孤儿残留；docx 路线不产 full.md，删除无妨）
            old_img = out_dir / "images"
            if old_img.exists():
                _shutil.rmtree(old_img, ignore_errors=True)
            (out_dir / "full.md").unlink(missing_ok=True)
            chapters = chapters_from_docx(
                file_path, out_dir=out_dir,
                on_progress=lambda m: _set_task_progress(task_id, 12, str(m)[:120]),
            )
            print(f"[解析] doc {doc_id} 对象级切章完成：{len(chapters)} 章（python-docx，绕 md）")
        else:
            # 复用优先级：① 自己此前的解析产物（重新提炼场景，含人工校对修正的 full.md）
            #             ② 其他同 MD5 记录的解析产物（重复上传场景）
            from app.services.parse_cache import ensure_md5, find_reusable

            md5 = ensure_md5("knowledge_doc", doc_id, file_path, doc.get("file_md5"))
            own = Path(doc["parsed_path"]) if doc.get("parsed_path") else None
            reused_dir = own if (own and (own / "full.md").exists()) \
                else find_reusable(md5, exclude_knowledge_id=doc_id)
            if reused_dir is not None:
                out_dir = reused_dir
                markdown = (out_dir / "full.md").read_text(encoding="utf-8")
                cl_path = out_dir / "content_list.json"
                parsed = {"markdown": markdown,
                          "content_list_path": str(cl_path) if cl_path.exists() else None}
                _set_task_progress(task_id, 18, "命中同文件已解析结果（MD5 相同），直接复用")
                print(f"[解析复用] doc {doc_id} ← {out_dir}")
            else:
                parsed = parse_document(file_path, out_dir,
                                        on_progress=lambda m: _set_task_progress(task_id, 10, str(m)[:120]))
                markdown = parsed.get("markdown", "")
            from app.agents.heading_agent import split_chapters_v2

            chapters = split_chapters_v2(
                markdown, on_progress=lambda m: _set_task_progress(task_id, 12, str(m)[:120])
            )

        if not chapters:
            chapters = [ChapterV2(leaf_title=doc["file_name"], title=doc["file_name"],
                                  path=[], level=1, text="")]
        # 全量提炼（2026-09-09 移除 120 章上限：整册应完整提炼，截断会让后半文档知识页缺失）
        total = len(chapters)
        _set_task_progress(task_id, 25, f"知识章节 {total} 个，开始提炼")

        # 3) 并发逐章提炼 + 入库（标题路径作主键；PDF 解析带页码出处）
        page_index = _page_index(parsed.get("content_list_path"))

        def _locate(ch):
            """页码定位：先按叶子标题，再按最近父级标题。"""
            located = _locate_pages(page_index, ch.leaf_title)
            if not located and ch.path:
                located = _locate_pages(page_index, ch.path[-1])
            return located

        def _extract_one(item):
            ch, idx = item
            page = extract_knowledge_page(
                chapter_title=ch.title,
                chapter_text=ch.text,
                doc_name=doc["file_name"],
                source_location=_locate(ch),
            )
            _attach_image_urls(page.get("images", []), ch.text, out_dir)
            return idx, ch, page

        count = 0
        with ThreadPoolExecutor(max_workers=EXTRACT_CONCURRENCY) as pool:
            futures = {pool.submit(_extract_one, (ch, i)): i
                       for i, ch in enumerate(chapters)}
            for fut in as_completed(futures):
                idx, ch, page = fut.result()
                execute(
                    "INSERT INTO knowledge_page (doc_id, chapter_type, title, tags, content,"
                    " source_location, status, created_at, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, 'draft', NOW(), NOW())",
                    (
                        doc_id,
                        "",  # 不使用章节类型（匹配按标题路径），列保留兼容旧表
                        ch.title,  # 全路径标题："工程概况 > 2.3 主要工程内容和数量"
                        json.dumps(page.get("tags", []), ensure_ascii=False),
                        json.dumps(
                            {
                                "category": page.get("category", ""),
                                "method": page.get("method", ""),
                                "key_points": page.get("key_points", []),
                                "tables": page.get("tables", []),
                                "images": page.get("images", []),
                                "usage": page.get("usage", ""),
                                "numbers": page.get("numbers", []),
                                "estimate_words": _count_words(ch.text),
                                "ref_text": _clean_ref_text(ch.text),
                            },
                            ensure_ascii=False,
                        ),
                        page.get("source", {}).get("location", ""),
                    ),
                )
                count += 1
                _set_task_progress(task_id, 25 + int(count / total * 70), f"已提炼 {count}/{total} 章")

        # 4) 完成
        execute(
            "UPDATE knowledge_doc SET status = 'parsed', parsed_path = %s WHERE id = %s",
            (str(out_dir), doc_id),
        )
        _set_task_progress(task_id, 100, f"完成，共提炼 {count} 个知识页")

        # 5) 完成（本项目不使用向量库，无检索索引同步步骤）
    except Exception as e:  # noqa: BLE001 失败落库，任务置为 failed
        execute("UPDATE knowledge_doc SET status = 'failed' WHERE id = %s", (doc_id,))
        raise
