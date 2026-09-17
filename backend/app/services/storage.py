"""StorageService：文件存储抽象。

MVP 用本地目录 uploads/；生产替换为 MinIO/OSS 时只需新增实现类，业务代码不动。
"""
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

from app.core.config import settings


class StorageService(ABC):
    @abstractmethod
    def save(self, data: bytes, filename: str, subdir: str = "") -> dict:
        """保存文件，返回 {path, url, size} 等元信息。"""

    @abstractmethod
    def read(self, path: str) -> bytes:
        """按存储路径读取文件内容。"""

    @abstractmethod
    def delete(self, path: str) -> None:
        """删除文件。"""


class LocalStorageService(StorageService):
    """本地目录存储：uploads/<subdir>/<yyyyMM>/<uuid>_<filename>"""

    def __init__(self, base_dir: Path | None = None):
        self.base = base_dir or settings.uploads_dir

    def _resolve(self, path: str) -> Path:
        p = (self.base / path).resolve()
        if not p.is_relative_to(self.base.resolve()):
            raise ValueError("非法存储路径")
        return p

    def save(self, data: bytes, filename: str, subdir: str = "") -> dict:
        from datetime import datetime

        rel_dir = Path(subdir) / datetime.now().strftime("%Y%m")
        target_dir = self.base / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{uuid4().hex[:8]}_{filename}"
        target = target_dir / safe_name
        target.write_bytes(data)
        rel_path = str((rel_dir / safe_name).as_posix())
        return {"path": rel_path, "size": len(data)}

    def read(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def delete(self, path: str) -> None:
        p = self._resolve(path)
        if p.exists():
            p.unlink()


# 默认实例（生产替换点：换成 MinIOStorageService 等）
storage: StorageService = LocalStorageService()
