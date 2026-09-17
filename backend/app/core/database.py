"""MySQL 连接管理（MVP 阶段直接用 pymysql，无 ORM；生产可平滑替换 SQLAlchemy）。

性能关键：若 MySQL 部署在另一台主机（如局域网机器或云数据库），新建连接要付 TCP
握手 + 认证的往返代价（实测数百 ms/请求级）。因此按**线程复用**连接（thread-local）：
- FastAPI 同步端点在线程池中执行，一个线程同一时刻只服务一个请求，线程内串行复用安全；
- task_runner / 编排等后台线程各自持有独立连接；
- 取连接前 ping(reconnect=True)，MySQL wait_timeout 断线自动恢复。
"""
import threading

import pymysql

from app.core.config import settings

_local = threading.local()


def get_conn() -> pymysql.connections.Connection:
    """获取当前线程的数据库连接（DictCursor，自动提交；断线自动重连）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:  # noqa: BLE001 连接已死，重建
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            _local.conn = None
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    _local.conn = conn
    return conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple = ()) -> dict | None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    """执行写语句，返回 lastrowid。"""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.lastrowid


def check_connection() -> str:
    """健康检查：返回 MySQL 版本号。"""
    cur = get_conn().cursor()
    try:
        cur.execute("SELECT VERSION()")
        return cur.fetchone()["VERSION()"]
    finally:
        cur.close()


def close_thread_conn() -> None:
    """关闭当前线程连接（长任务线程结束时调用，防连接泄漏；不调也只占一条）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        _local.conn = None
