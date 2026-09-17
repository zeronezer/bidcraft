"""施组AI编制系统 - FastAPI 后端入口。

启动：uvicorn app.main:app --reload --port 8000（在 backend/ 目录下）
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, compile, experience, facts, files, knowledge, projects, sections
from app.core.database import check_connection

app = FastAPI(title="施组AI编制系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5180",
        "http://127.0.0.1:5180",
    ],
    # 开发期开放任意来源（局域网 IP / Tailscale 100.x / 穿透域名）：
    # allow_credentials=True 时不能用 allow_origins=["*"]（浏览器会拒绝），
    # 故用正则放行；正则匹配到的来源会被回显成具体的 Access-Control-Allow-Origin。
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(files.router)
app.include_router(chat.router)
app.include_router(compile.router)
app.include_router(knowledge.router)
app.include_router(experience.router)
app.include_router(facts.router)
app.include_router(sections.router)
# 进度图表 / 质检 / 招标解析三个功能已按需求移除（2026-09-10）：
# - 前端入口与抽屉、qc/charts 的后端接口一并删除；
# - 招标要求（tender_requirement）数据仍保留 —— 正文与编写思路生成仍在引用它。

# 原文件/解析产物静态服务（校对界面原文预览用；生产替换为对象存储签名 URL）
from fastapi.staticfiles import StaticFiles

from app.core.config import settings

app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")


@app.on_event("startup")
def _startup():
    """启动钩子：清理残留任务 + 配置开启时自动续跑中断的编制会话（方案 3.6 MySQL 检查点）。"""
    from app.core.config import settings

    # task 表任务跑在线程池里，随进程死亡；重启后残留的 running 永远不会完成，标记失败
    from app.core.database import get_conn

    cur = get_conn().cursor()
    try:
        cur.execute(
            "UPDATE task SET status = 'failed', detail = '服务重启，任务中断，请重新发起', updated_at = NOW()"
            " WHERE status = 'running'"
        )
        n = cur.rowcount
        # 业务单据同步复位：线程随进程死亡，parsing 状态永远不会推进，不复位则无法重新发起
        cur.execute("UPDATE knowledge_doc SET status = 'failed' WHERE status = 'parsing'")
        cur.execute("UPDATE project_file SET status = 'failed' WHERE status = 'parsing'")
    finally:
        cur.close()
    if n:
        print(f"[启动] 清理 {n} 个残留的 running 任务（标记失败）")

    if settings.compile_auto_resume:
        from app.services import compile_service

        cnt = compile_service.auto_resume()
        if cnt:
            print(f"[启动] 自动恢复 {cnt} 个中断的编制会话")
    else:
        # 未开启自动续跑：复位残留 running 的编制会话（进程重启后线程已死的僵尸），
        # 否则会被误判"进行中"永远卡住（start_compile 看到 running 会拒绝新发起）
        from app.core.database import get_conn

        cur2 = get_conn().cursor()
        try:
            cur2.execute(
                "UPDATE compile_session SET stage = 'failed', running = 0,"
                " error = '会话已复位（服务重启，线程中断），可重新发起' WHERE running = 1"
            )
            if cur2.rowcount:
                print(f"[启动] 复位 {cur2.rowcount} 个僵尸编制会话")
        finally:
            cur2.close()


@app.get("/health")
def health():
    """健康检查：验证 MySQL 连通性。"""
    try:
        mysql_version = check_connection()
        return {"status": "ok", "mysql": mysql_version}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "mysql_error": str(e)}
