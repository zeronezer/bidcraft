"""一次性：把库里 format=html 的旧正文 [待补充]/[图片占位] 迁移为真实 <mark>（只跑一次，幂等）。"""
import json
import sys

sys.path.insert(0, ".")

from app.core.database import execute, query  # noqa: E402
from app.services.content_migrate import migrate_tdz_html  # noqa: E402

rows = query(
    """
    SELECT chapter_node_id, content
    FROM section_content
    WHERE JSON_UNQUOTE(JSON_EXTRACT(content,'$.format'))='html'
      AND (LOCATE('待补充', JSON_UNQUOTE(JSON_EXTRACT(content,'$.body')))>0
        OR LOCATE('图片占位', JSON_UNQUOTE(JSON_EXTRACT(content,'$.body')))>0)
    """
)
changed = 0
for r in rows:
    payload = json.loads(r["content"]) if isinstance(r["content"], str) else r["content"]
    body = payload.get("body", "")
    new_body = migrate_tdz_html(body)
    if new_body == body:
        print(f"node {r['chapter_node_id']}: 无变化（已迁移/无可迁移）")
        continue
    payload["body"] = new_body
    execute(
        "UPDATE section_content SET content = %s WHERE chapter_node_id = %s",
        (json.dumps(payload, ensure_ascii=False), r["chapter_node_id"]),
    )
    changed += 1
    print(f"node {r['chapter_node_id']}: 迁移完成（新增 <mark> 包裹）")

print(f"\n共处理 {len(rows)} 行，改写 {changed} 行")
