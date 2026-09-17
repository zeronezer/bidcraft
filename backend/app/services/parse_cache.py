"""解析结果复用（MD5 缓存）：同一文件不重复走 MinerU。

原理：上传时计算文件内容 MD5 存库（knowledge_doc / project_file），
解析前先查是否已有**同 MD5 且已解析**的记录——命中则直接复用其解析产物目录
（full.md / content_list.json），跳过 MinerU（省时间与云端配额）。

开关：.env `REUSE_PARSED_BY_MD5=false` 可整体关闭（默认开）。
跨表复用：同一个文件既传过知识库又传过项目文件区，也可互相复用解析结果。
"""
import hashlib
from pathlib import Path

from app.core.config import settings
from app.core.database import execute, query_one


def file_md5(path: Path) -> str:
    """计算文件内容 MD5（32 位十六进制小写）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_md5(table: str, row_id: int, path: Path, stored: str | None) -> str:
    """取记录的 MD5：库里有就用，没有则现算并回填（存量数据兼容）。"""
    if stored:
        return stored
    if not Path(path).exists():
        return ""
    md5 = file_md5(path)
    execute(f"UPDATE {table} SET file_md5 = %s WHERE id = %s", (md5, row_id))
    return md5


def _parsed_dir_valid(parsed_path: str | None) -> Path | None:
    """parsed_path 指向的解析产物是否可用（full.md 存在）。"""
    if not parsed_path:
        return None
    d = Path(parsed_path)
    return d if (d / "full.md").exists() else None


def find_reusable(md5: str, exclude_knowledge_id: int | None = None,
                  exclude_project_file_id: int | None = None) -> Path | None:
    """查找同 MD5 且已解析成功的记录，返回可复用的解析产物目录（无则 None）。

    两张表都查（跨表复用）；解析产物目录被清理过的视为不可复用。
    """
    if not settings.reuse_parsed_by_md5 or not md5:
        return None

    krow = query_one(
        "SELECT id, parsed_path FROM knowledge_doc"
        " WHERE file_md5 = %s AND status = 'parsed' AND id != COALESCE(%s, -1)"
        " ORDER BY id DESC LIMIT 1",
        (md5, exclude_knowledge_id),
    )
    if krow:
        d = _parsed_dir_valid(krow["parsed_path"])
        if d:
            return d

    prow = query_one(
        "SELECT id, parsed_path FROM project_file"
        " WHERE file_md5 = %s AND status = 'parsed' AND id != COALESCE(%s, -1)"
        " ORDER BY id DESC LIMIT 1",
        (md5, exclude_project_file_id),
    )
    if prow:
        d = _parsed_dir_valid(prow["parsed_path"])
        if d:
            return d
    return None
