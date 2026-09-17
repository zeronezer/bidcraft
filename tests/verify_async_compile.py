"""编排异步流程验证：start → status(等待确认目录) → resume → status(等待确认思路) → resume → status(完成)。

验证 compile_service 的异步状态机流转。
用法：python tests/verify_async_compile.py
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services import compile_service  # noqa: E402


def wait_stage(project_id, target_stages, timeout=180):
    """轮询直到 stage 进入目标集合，返回最终 status。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = compile_service.get_status(project_id)
        if s["stage"] in target_stages:
            return s
        if s["stage"] in ("failed", "done"):
            return s
        time.sleep(2)
    raise TimeoutError(f"等待 stage {target_stages} 超时，当前 {compile_service.get_status(project_id)}")


def main():
    pid = 1001
    project_info = "新建宜昌至涪陵高速铁路重庆段站前7标，正线长约44公里，含桥梁、隧道、路基、站场工程"

    print("[1] start → 应进入 确认目录 阶段")
    compile_service.start_compile(pid, project_info, professions=["桥梁", "隧道", "路基"], tender_requirements="")
    s = wait_stage(pid, {"confirm_toc", "done", "failed"})
    print(f"    stage={s['stage']}")
    if s["stage"] == "confirm_toc":
        toc = s["interrupt"].get("toc", {})
        print(f"    目录章节数: {len(toc.get('chapters', []))}")

    print("[2] resume 确认目录 → 应进入 确认思路 阶段")
    compile_service.resume(pid, {"action": "confirm"})
    s = wait_stage(pid, {"confirm_outline", "done", "failed"}, timeout=300)
    print(f"    stage={s['stage']}")
    if s["stage"] == "confirm_outline":
        outlines = s["interrupt"].get("outlines", [])
        print(f"    思路条数: {len(outlines)}")

    print("[3] resume 确认思路 → 应进入 完成 阶段（生成正文，可能较久）")
    compile_service.resume(pid, {"action": "confirm"})
    s = wait_stage(pid, {"done", "failed"}, timeout=600)
    print(f"    stage={s['stage']}")
    if s["stage"] == "done":
        chapters = s["result"].get("chapters", []) if s["result"] else []
        print(f"    生成正文 {len(chapters)} 章")

    print("\n✅ 异步编排流程验证完成")


if __name__ == "__main__":
    main()
