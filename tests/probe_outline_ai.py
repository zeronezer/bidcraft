"""实测：AI 一次性处理标题列表推断层级的可行性与耗时。

输入：从 full.md 提取的标题行 + 该标题下的正文字数
输出：AI 给出的层级树 + 是否保留标记（封面/目录/空壳 → keep=false）
目的：为"300 页文档标题分层是否需要分批"提供量化依据。
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.llm import llm  # noqa: E402

ROOT = Path(r"D:/AI-claudecode/biaoshubianzhi")
MD = ROOT / "uploads" / "parsed" / "6" / "full.md"

SYSTEM = """你是施工组织设计文档结构分析专家。
下面给出一份施组文档的【标题列表】，每行格式为「序号 | 标题 | 该标题下正文字数」。

请你完成两件事：
1. 推断每个标题的真实层级 level（1=章, 2=节, 3=小节, 4=更细）。
   依据优先级：① 编号格式（"1、"→1级, "1.1"→2级, "2.1.1"→3级, "（一）"→1级, "（1）"→3级）；
   ② 无编号时按标题语义与前后编号的归属关系推断。
2. 判断该标题是否需要提炼为知识页 keep：
   - keep=false：封面、目录、封皮、正本/副本、纯页码行、无正文的空壳父标题（正文字数<50）
   - keep=true：有实质内容的章节标题

只输出 JSON 数组，不要解释、不要代码围栏：
[{"i": 序号, "level": 层级, "title": "原标题", "keep": true或false, "note": "简要理由(10字内)"}]"""


def extract_headings(md: str) -> list[dict]:
    lines = md.splitlines()
    idx = [i for i, l in enumerate(lines) if re.match(r"^#{1,6}\s+", l)]
    out = []
    for n, i in enumerate(idx):
        end = idx[n + 1] if n + 1 < len(idx) else len(lines)
        title = re.sub(r"^#{1,6}\s+", "", lines[i]).strip()
        body = "\n".join(lines[i + 1 : end]).strip()
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        out.append({"i": n, "title": title, "body_len": len(body.strip())})
    return out


def main() -> None:
    md = MD.read_text(encoding="utf-8")
    heads = extract_headings(md)
    print("提取到标题数: %d" % len(heads), flush=True)
    print("有正文(>=50字)的标题: %d" % sum(1 for h in heads if h["body_len"] >= 50), flush=True)

    payload = "\n".join(
        "%d | %s | %d" % (h["i"], h["title"], h["body_len"]) for h in heads
    )
    user = "标题列表（共 %d 个）：\n%s" % (len(heads), payload)
    print("输入字符数: %d" % len(user), flush=True)

    t0 = time.time()
    raw = llm.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        temperature=0.1,
    )
    dt = time.time() - t0
    print("\n=== AI 调用耗时: %.1f 秒, 输出 %d 字符 ===" % (dt, len(raw)), flush=True)

    s, e = raw.find("["), raw.rfind("]")
    if s == -1 or e == -1:
        print("未返回数组，原文前 500 字：\n%s" % raw[:500], flush=True)
        return
    data = json.loads(raw[s : e + 1])
    print("解析条目数: %d / %d" % (len(data), len(heads)), flush=True)

    from collections import Counter

    print("层级分布:", dict(Counter(d.get("level") for d in data)), flush=True)
    print("keep=true: %d, keep=false: %d" % (
        sum(1 for d in data if d.get("keep")), sum(1 for d in data if not d.get("keep"))
    ), flush=True)
    print("\n--- 前 25 条结果 ---", flush=True)
    for d in data[:25]:
        print("  L%s %-5s %-40s %s" % (
            d.get("level"), "保留" if d.get("keep") else "剔除",
            (d.get("title") or "")[:38], d.get("note", "")
        ), flush=True)

    out = ROOT / "tests" / "_outline_probe_result.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n完整结果已存: %s" % out, flush=True)


if __name__ == "__main__":
    main()
