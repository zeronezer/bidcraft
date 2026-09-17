"""为已有目录树批量生成编写思路（不重跑目录）：遍历 chapter_node，对每个无思路节点
调 generate_outline 并 save_outline 落库（状态升 thought_ready）。

用途：目录已在库、思路未生成（中断/未跑）时补跑，避免重新生成目录。
用法：python tools/generate_outlines.py <project_id>
"""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, '.')
from app.core.database import query, query_one
from app.agents.orchestrate_agent import generate_outline
from app.services.experience_service import match_experiences
from app.services.matching import load_page_summaries, match_pages
from app.services.fact_service import confirmed_facts
from app.services.section_service import save_outline

CONCURRENCY = 4


def main(project_id: int) -> None:
    rows = query(
        "SELECT id, title, parent_id, status FROM chapter_node WHERE project_id = %s ORDER BY sort_order, id",
        (project_id,),
    )
    if not rows:
        print('无目录树'); return
    # 只处理未生成思路的（unwritten）；已 thought_ready/generated/confirmed 保留
    todo = [r for r in rows if r['status'] == 'unwritten']
    print(f'节点 {len(rows)}，待生成思路 {len(todo)}')
    if not todo:
        print('全部已有思路'); return

    # 父标题映射（知识页/经验匹配的 path 上下文）
    title_of = {r['id']: r['title'] for r in rows}
    facts = confirmed_facts(project_id)
    summaries = load_page_summaries()

    def _one(r: dict) -> None:
        parent = title_of.get(r['parent_id']) if r['parent_id'] else None
        path = [parent] if parent else []
        pages = match_pages(r['title'], path, summaries)
        exp = match_experiences(r['title'], path)
        outline = generate_outline(
            chapter_title=r['title'], chapter_type='',
            project_facts=facts, knowledge_pages=pages, experiences=exp,
        )
        outline['_toc_title'] = r['title']
        outline['_parent'] = parent or ''
        outline['_page_ids'] = [p['id'] for p in pages]
        save_outline(project_id, r['title'], outline)

    done = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futs = {pool.submit(_one, r): r for r in todo}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                print(f'  失败 {futs[fut]["title"][:30]}: {e}')
            done += 1
            if done % 10 == 0:
                print(f'  进度 {done}/{len(todo)}')
    ok = query_one(
        "SELECT COUNT(*) c FROM chapter_node WHERE project_id = %s AND outline IS NOT NULL AND JSON_LENGTH(outline) > 0",
        (project_id,),
    )['c']
    print(f'完成：已有思路节点 {ok}/{len(rows)}')


if __name__ == '__main__':
    main(int(sys.argv[1]))
