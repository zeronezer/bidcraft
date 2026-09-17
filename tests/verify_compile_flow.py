"""编排状态机端到端验证：目录生成 → 确认 → 思路 → 确认 → 正文。

验证 LangGraph 的 interrupt() 人工确认机制是否按预期工作：
1. invoke 首次会停在"确认目录"中断点；
2. resume 传入确认结果后继续，停在"确认思路"中断点；
3. 再次 resume 后跑完正文生成。

用法：python tests/verify_compile_flow.py
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents.compile_graph import new_compile, resume_compile  # noqa: E402


def main():
    project_info = "新建宜昌至涪陵高速铁路重庆段站前7标，正线长约44公里，含桥梁、隧道、路基、站场工程"
    professions = ["路基", "桥梁", "隧道", "站场"]
    tender_requirements = "招标文件要求：施工组织设计需覆盖施工方案、资源配置、管理措施；重点考核施工组织安排的合理性"

    print("=" * 60)
    print("[Step 1] 启动编制流程，应停在「确认目录」中断点")
    print("=" * 60)
    r1 = new_compile(
        project_id=999,
        project_info=project_info,
        professions=professions,
        tender_requirements=tender_requirements,
        facts=[],
        knowledge_pages={},
    )
    print(f"interrupted={r1['interrupted']}")
    if r1["interrupted"]:
        print(f"中断类型: {r1['interrupt'].get('type')}")
        print(f"目录章节数: {len(r1['interrupt'].get('toc', {}).get('chapters', []))}")
    else:
        state = r1["state"]
        print(f"目录：{json.dumps(state.get('toc', {}), ensure_ascii=False)[:500]}")
        print("⚠ 未在确认目录处中断（interrupt 未生效），跳过后续")
        return

    print("\n" + "=" * 60)
    print("[Step 2] 用户确认目录，恢复后应停在「确认思路」中断点")
    print("=" * 60)
    r2 = resume_compile(999, {"action": "confirm"})
    print(f"interrupted={r2['interrupted']}")
    if r2["interrupted"]:
        outlines = r2["interrupt"].get("outlines", [])
        print(f"中断类型: {r2['interrupt'].get('type')}，思路 {len(outlines)} 条")
        for o in outlines[:5]:
            print(f"  - {o.get('_toc_title', o.get('chapter_title', ''))}: {o.get('thinking', '')[:50]}…")
    else:
        outlines = r2["state"].get("outlines", []) if r2["state"] else []
        print(f"思路 {len(outlines)} 条")
        for o in outlines[:5]:
            print(f"  - {o.get('_toc_title', '')}: {o.get('thinking', '')[:60]}…")

    print("\n" + "=" * 60)
    print("[Step 3] 用户确认思路，恢复后应跑完正文生成")
    print("=" * 60)
    r3 = resume_compile(999, {"action": "confirm"})
    print(f"interrupted={r3['interrupted']}")
    chapters = r3["state"].get("chapters", []) if r3["state"] else []
    print(f"生成正文 {len(chapters)} 章")
    for c in chapters[:3]:
        print(f"  - {c['title']}: {len(c['content'])} 字")
        print("    预览: " + c["content"][:100].replace("\n", " ") + "…")

    print("\n✅ 编排状态机验证完成")


if __name__ == "__main__":
    main()
