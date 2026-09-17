"""演示样例数据填充脚本（开发期验证前后端联通用）。

- 为 global_fact 表补充 category 字段（若缺失）
- 向 knowledge_doc / knowledge_page / experience_item / global_fact 插入样例数据

执行：python scripts/seed_demo.py
"""
import json
import re
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict:
    env = {}
    for line in open(ROOT / ".env", encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def main():
    env = load_env()
    conn = pymysql.connect(
        host=env["MYSQL_HOST"], port=int(env["MYSQL_PORT"]),
        user=env["MYSQL_USER"], password=env["MYSQL_PASSWORD"],
        database=env["MYSQL_DATABASE"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    )

    with conn, conn.cursor() as cur:
        # 1) 补 global_fact.category 字段
        cur.execute("SHOW COLUMNS FROM global_fact")
        cols = [r["Field"] for r in cur.fetchall()]
        if "category" not in cols:
            cur.execute("ALTER TABLE global_fact ADD COLUMN category VARCHAR(32) DEFAULT '' AFTER lot_id")
            print("[1] 已为 global_fact 补充 category 字段")
        else:
            print("[1] global_fact.category 已存在")

        # 1b) knowledge_doc 旧表用 doc_type，统一为 doc_category
        cur.execute("SHOW COLUMNS FROM knowledge_doc")
        kd_cols = [r["Field"] for r in cur.fetchall()]
        if "doc_category" not in kd_cols and "doc_type" in kd_cols:
            cur.execute("ALTER TABLE knowledge_doc CHANGE COLUMN doc_type doc_category VARCHAR(30) DEFAULT 'sod'")
            print("[1b] 已把 knowledge_doc.doc_type 改名为 doc_category")
        elif "doc_category" in kd_cols:
            print("[1b] knowledge_doc.doc_category 已存在")

        # 2) 知识库文档（历史施组 sod / 工艺工法 method）
        cur.execute("SELECT COUNT(*) c FROM knowledge_doc")
        if cur.fetchone()["c"] == 0:
            docs = [
                ("高速公路某合同段实施性施组.pdf", "uploads/knowledge/demo/sod1.pdf", "sod"),
                ("某房建项目施工组织设计.docx", "uploads/knowledge/demo/sod2.docx", "sod"),
                ("高速公路路基桥隧施组.pdf", "uploads/knowledge/demo/sod3.pdf", "sod"),
                ("钻孔灌注桩施工工艺.docx", "uploads/knowledge/demo/m1.docx", "method"),
                ("CRTSⅢ型无砟轨道施工工法.pdf", "uploads/knowledge/demo/m2.pdf", "method"),
            ]
            doc_ids = []
            for name, path, cat in docs:
                cur.execute(
                    "INSERT INTO knowledge_doc (file_name, file_path, doc_category, status, created_at)"
                    " VALUES (%s, %s, %s, 'parsed', NOW())", (name, path, cat),
                )
                doc_ids.append(cur.lastrowid)
            print(f"[2] 插入 {len(docs)} 条知识库文档, ids={doc_ids}")
        else:
            cur.execute("SELECT id, doc_category FROM knowledge_doc ORDER BY id")
            rows = cur.fetchall()
            doc_ids = {r["doc_category"]: r["id"] for r in rows}
            sod_ids = [r["id"] for r in rows if r["doc_category"] == "sod"]
            doc_ids = sod_ids[:1] if sod_ids else [1]
            print(f"[2] 知识库文档已存在 {len(rows)} 条，跳过")

        # 3) 知识页
        cur.execute("SELECT COUNT(*) c FROM knowledge_page")
        if cur.fetchone()["c"] == 0:
            cur.execute("SELECT id FROM knowledge_doc WHERE doc_category='sod' ORDER BY id LIMIT 1")
            sod_doc = cur.fetchone()
            sod_id = sod_doc["id"] if sod_doc else 1
            pages = [
                ("施工方案", "桥梁钻孔灌注桩施工方案", ["铁路", "桥梁"],
                 "先列编制依据，再按'工艺原理→工艺流程→操作要点→质量控制→安全措施'组织，配合工艺流程图表达",
                 ["桩基成孔垂直度控制", "水下混凝土灌注导管埋深", "泥浆性能指标"],
                 [{"title": "泥浆性能指标表", "content_md": "..."}],
                 [{"url": "", "caption": "钻孔灌注桩施工流程图"}],
                 "适用于桥梁桩基、钻孔灌注桩类施工方案章节", "P45、52"),
                ("施工方案", "CRTSⅢ型无砟轨道施工", ["铁路", "轨道"],
                 "按'底座板施工→轨道板铺设→自密实混凝土灌注→精调'工序展开",
                 ["轨道板精调精度", "自密实混凝土配合比"], [],
                 [{"url": "", "caption": "无砟轨道施工工艺流程图"}],
                 "适用于轨道工程、无砟轨道施工方案章节", "P1、5"),
                ("施工组织安排", "施工总平面布置与区段划分", ["铁路"],
                 "按'总体施工顺序→区段划分→施工平面布置→临时设施'组织",
                 ["大临工程布置（梁场/拌和站）", "施工区段划分原则"], [],
                 [{"url": "", "caption": "施工总平面布置图"}],
                 "适用于施工组织安排章节", "P12、18"),
                ("工程概况", "工程概况编制范式", ["铁路"],
                 "先列项目基本情况，再列主要技术标准与工程数量表",
                 ["工程数量汇总表", "主要技术标准清单"],
                 [{"title": "主要工程数量表", "content_md": "..."}], [],
                 "适用于工程概况章节", "P3、6"),
                ("编制依据与原则", "编制依据清单范式", ["铁路", "公路"],
                 "列招标文件、规范、图纸、现场踏勘报告等依据，按效力排序",
                 ["招标文件及补遗", "现行技术规范", "现场踏勘资料"], [], [],
                 "适用于编制依据与原则章节", "P1、2"),
                ("控制与重难点工程", "重难点工程分析与对策", ["铁路", "桥梁"],
                 "识别控制性工程，逐项分析重难点并提出针对性对策",
                 ["控制性工程识别", "重难点对策表"],
                 [{"title": "重难点工程对策表", "content_md": "..."}], [],
                 "适用于控制与重难点工程章节", "P30、35"),
            ]
            for ct, title, tags, method, kp, tables, images, usage, loc in pages:
                cur.execute(
                    "INSERT INTO knowledge_page (doc_id, chapter_type, title, tags, content,"
                    " source_location, status, created_at, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, 'confirmed', NOW(), NOW())",
                    (sod_id, ct, title, json.dumps(tags, ensure_ascii=False),
                     json.dumps({"method": method, "key_points": kp, "tables": tables,
                                 "images": images, "usage": usage}, ensure_ascii=False), loc),
                )
            print(f"[3] 插入 {len(pages)} 条知识页")
        else:
            print("[3] 知识页已存在，跳过")

        # 4) 经验条目
        cur.execute("SELECT COUNT(*) c FROM experience_item")
        if cur.fetchone()["c"] == 0:
            exps = [
                ("writing", "施工部署", ["房建", "高层住宅"],
                 "施工部署章节优先按'总体安排 + 分区流水 + 穿插'组织，避免大段口号式表述",
                 {"project_id": 1, "diff_summary": "用户删除口号段落，改为分区流水描述"}, "pending"),
                ("correction", "施工方案", ["铁路", "桥梁"],
                 "桥梁施工方案中，桩基部分必须明确泥浆比重与成孔垂直度两个指标",
                 {"project_id": 1, "diff_summary": "用户纠偏：桩基章节漏了泥浆比重"}, "pending"),
                ("compile", "工程概况", ["铁路"],
                 "工程概况章节目录层级固定为：基本情况 → 技术标准 → 工程数量表",
                 {"project_id": 1, "diff_summary": "用户调整目录合并冗余子节"}, "pending"),
                ("expression", "管理措施", ["房建"],
                 "质量保证措施表述偏好：每条先写'管理动作'再写'技术标准'",
                 {"project_id": 2, "diff_summary": "用户重写质量管理章节表述"}, "confirmed"),
                ("writing", "施工进度计划", ["铁路"],
                 "进度计划章节优先用横道图表达总进度，文字仅说明关键线路与里程碑",
                 {"project_id": 2, "diff_summary": "用户要求进度计划以横道图为主"}, "confirmed"),
            ]
            for typ, ct, tags, content, src, status in exps:
                cur.execute(
                    "INSERT INTO experience_item (exp_type, chapter_type, tags, content, source,"
                    " status, created_at) VALUES (%s, %s, %s, %s, %s, %s, NOW())",
                    (typ, ct, json.dumps(tags, ensure_ascii=False), content,
                     json.dumps(src, ensure_ascii=False), status),
                )
            print(f"[4] 插入 {len(exps)} 条经验条目")
        else:
            print("[4] 经验条目已存在，跳过")

        # 5) 全局事实
        cur.execute("SELECT COUNT(*) c FROM global_fact")
        if cur.fetchone()["c"] == 0:
            facts = [
                ("工期", "总工期", "540", "天", "招标文件 P12 第3.2条", 0.95, "pending"),
                ("人员", "项目高峰人数", "380", "人", "指导性施组 P8", 0.72, "pending"),
                ("机械", "塔吊数量", "3", "台", "施工组织设计 P45", 0.88, "pending"),
                ("造价", "合同总造价", "12.6", "亿元", "招标文件 P3", 0.90, "confirmed"),
                ("质量标准", "工程质量目标", "优良", "", "招标文件 P5", 0.85, "confirmed"),
                ("结构", "桥梁桩基直径", "1.5", "m", "图纸 G-02", 0.68, "pending"),
                ("安全目标", "安全生产目标", "无较大及以上事故", "", "招标文件 P6", 0.93, "pending"),
            ]
            for cat, key, val, unit, loc, conf, status in facts:
                cur.execute(
                    "INSERT INTO global_fact (project_id, category, fact_key, fact_value, unit,"
                    " source_location, confidence, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (1, cat, key, val, unit, loc, conf, status),
                )
            print(f"[5] 插入 {len(facts)} 条全局事实")
        else:
            print("[5] 全局事实已存在，跳过")

    print("完成。")


if __name__ == "__main__":
    main()
