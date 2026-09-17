"""正文遗留标记 → 真实 <mark> 迁移（一次性数据修复，2026-09-09）。

背景：`[待补充]/[图片占位]` 的标红样式此前由前端 ProseMirror Decoration（只读装饰 .tdbz）
在**渲染层**提供——文档里存的是纯文本 token，显示才标红。现改为真实、可编辑的高亮 mark
（Highlight 扩展 `<mark style="background-color:#fde8e8">`），与工具栏"文字底色·警示红"
同源：用户可直接清除/改色，清除后保存为纯文本 token 并持久不红。

因此库里 **format=html** 的旧正文需要一次性把纯文本（或旧 `<span class="tdbz">` 包裹）的
token 改写为真实 `<mark>` 落库，否则重开后因不再有 Decoration 而不标红。
- format=md 的行**不需要**迁移：AI/编写思路内容每次打开由前端 md 分支包 mark（"AI 重写就重新红"），
  保存成 HTML 时 mark 才落库。
"""
import re

# 待补充/图片占位 token（与前端 wrapTdzAsMark 同一规则）
TOKEN_RE = re.compile(r"\[待补充\]|\(待补充\)|【待补充】|\[图片占位[^\]]*\]")
MARK_SEG = re.compile(r"<mark\b[^>]*>[\s\S]*?</mark>")

# 与工具栏"文字底色·警示红"一致，也即 Tiptap Highlight(multicolor) 的规范序列化（data-color + style）
MARK_TAG = '<mark data-color="#fde8e8" style="background-color: #fde8e8; color: inherit">'


def migrate_tdz_html(html: str) -> str:
    """把 HTML 正文里的纯文本 token 包成真实 <mark>；已在 <mark> 内的跳过（幂等）。

    旧 `<span class="tdbz">token</span>` 一并归一为 <mark>。
    """
    if not html:
        return html
    # 1) 旧 span 包裹 → mark（span 是 Decoration 时代的编辑器层产物，若曾落库则归一）
    html = re.sub(
        r'<span\b[^>]*class="tdbz"[^>]*>([^<]*)</span>',
        lambda m: MARK_TAG + m.group(1) + "</mark>",
        html,
    )
    # 2) 其余纯文本 token → mark；先保护已处于 <mark> 内的片段避免嵌套
    out, last = [], 0
    for m in MARK_SEG.finditer(html):
        out.append(_wrap_plain(html[last:m.start()]))
        out.append(m.group(0))  # 已 mark 包裹的原样保留
        last = m.end()
    out.append(_wrap_plain(html[last:]))
    return "".join(out)


def _wrap_plain(seg: str) -> str:
    if not seg:
        return ""
    return TOKEN_RE.sub(lambda m: MARK_TAG + m.group(0) + "</mark>", seg)
