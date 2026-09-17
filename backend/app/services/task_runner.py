"""TaskRunner：异步任务执行抽象。

MVP 用线程池 + MySQL task 表记录状态/进度（任务可查询，顺便做进度展示）；
生产替换为 Celery + Redis 时只换实现类。
"""
import traceback
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app.core.database import execute, query_one


class TaskRunner(ABC):
    @abstractmethod
    def submit(self, task_type: str, ref_id: int | None, fn, *args) -> int:
        """创建任务记录并异步执行 fn(task_id, *args)。返回 task_id。"""


class ThreadPoolTaskRunner(TaskRunner):
    def __init__(self, max_workers: int = 4):
        self.pool = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, task_type: str, ref_id: int | None, fn, *args) -> int:
        task_id = execute(
            "INSERT INTO task (task_type, ref_id, status, progress, detail, created_at, updated_at)"
            " VALUES (%s, %s, 'running', 0, NULL, %s, %s)",
            (task_type, ref_id, datetime.now(), datetime.now()),
        )
        self.pool.submit(self._run, task_id, fn, *args)
        return task_id

    @staticmethod
    def _update(task_id: int, **fields) -> None:
        sets = ", ".join(f"{k} = %s" for k in fields)
        execute(
            f"UPDATE task SET {sets}, updated_at = %s WHERE id = %s",
            (*fields.values(), datetime.now(), task_id),
        )

    def _run(self, task_id: int, fn, *args) -> None:
        try:
            fn(task_id, *args)
            self._update(task_id, status="success", progress=100)
        except Exception as e:  # noqa: BLE001 任务失败落库，不中断服务
            self._update(
                task_id,
                status="failed",
                detail=f"{e}: {traceback.format_exc()[-1500:]}",
            )


# 默认实例（生产替换点：换成 CeleryTaskRunner）
task_runner: TaskRunner = ThreadPoolTaskRunner()


def get_task(task_id: int) -> dict | None:
    return query_one("SELECT * FROM task WHERE id = %s", (task_id,))
