<template>
  <div class="tiptap-editor" :class="{ 'gen-locked': generating }">
    <!-- 工具栏 -->
    <div v-if="editor" class="toolbar">
      <button v-for="btn in toolbarButtons" :key="btn.label" class="tb-btn"
        :class="{ active: btn.isActive?.() }"
        :title="btn.label" @click.prevent="btn.action">
        {{ btn.icon }}
      </button>
      <span class="tb-sep" />
      <!-- 文字颜色：A；点击弹 8 色块面板 -->
      <div class="tb-color-wrap">
        <button class="tb-btn tb-color" :class="{ on: hasTextColor }" title="文字颜色"
          @click.prevent="togglePalette('color')">
          <span class="tc-letter" :style="{ color: fgActive || undefined }">A</span>
        </button>
        <div v-if="openPalette === 'color'" class="tb-palette">
          <div class="tp-label">文字颜色</div>
          <button v-for="c in FG_COLORS" :key="c.name" class="tp-swatch"
            :style="{ background: c.color }" :title="c.name" @click="applyColor(c.color)" />
          <button class="tp-clear" title="清除颜色" @click="clearColor">无</button>
        </div>
      </div>
      <!-- 文字底色（高亮）：▣ 点击弹 8 色块面板 -->
      <div class="tb-bg-wrap">
        <button class="tb-btn tb-bg" :class="{ on: hasHighlight }" title="文字底色（高亮）"
          @click.prevent="togglePalette('bg')">
          <span class="tc-letter" :style="{ background: bgActive || undefined }">▣</span>
        </button>
        <div v-if="openPalette === 'bg'" class="tb-palette">
          <div class="tp-label">文字底色</div>
          <button v-for="c in BG_COLORS" :key="c.name" class="tp-swatch"
            :style="{ background: c.color }" :title="c.name" @click="applyBg(c.color)" />
          <button class="tp-clear" title="清除底色" @click="clearColor">无</button>
        </div>
      </div>
      <button class="tb-btn" title="清除文字颜色/底色" @click.prevent="clearColor">清除</button>
      <span class="tb-sep" />
      <button class="tb-btn" title="表格" @click.prevent="insertTable">▦</button>
      <span class="tb-sep" />
      <!-- 图片上传（隐藏 input，由工具栏"插入图片"触发） -->
      <input ref="imageInput" type="file" accept="image/*" hidden @change="onImagePicked" />
      <span class="tb-save">
        <el-button
          v-if="canGenerate" size="small" type="warning" plain
          :loading="generating" :disabled="saving"
          @click="emit('generate')"
        >▶ 生成本章</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">保存</el-button>
      </span>
    </div>

    <!-- 编辑器内容区 -->
    <editor-content :editor="editor" class="editor-content" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import { Node, mergeAttributes } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Image from '@tiptap/extension-image'
import TextAlign from '@tiptap/extension-text-align'
import TextStyle from '@tiptap/extension-text-style'
import Color from '@tiptap/extension-color'
import Highlight from '@tiptap/extension-highlight'
import Table from '@tiptap/extension-table'
import TableRow from '@tiptap/extension-table-row'
import TableCell from '@tiptap/extension-table-cell'
import TableHeader from '@tiptap/extension-table-header'
import { marked } from 'marked'
import { VueNodeViewRenderer } from '@tiptap/vue-3'
import MermaidView from './MermaidView.vue'
import { uploadEditorImage } from '../api'

// Mermaid 图块节点：code 存源码（data-code），NodeView 内渲染 SVG / 编辑源码。
// 序列化为 <div class="mermaid-block" data-code="...">，与 markdown ```mermaid 双向转换。
const Mermaid = Node.create({
  name: 'mermaid',
  group: 'block',
  atom: true,
  addAttributes() {
    return {
      code: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-code') || '',
        renderHTML: (attrs) => ({ 'data-code': attrs.code }),
      },
    }
  },
  parseHTML() {
    return [{ tag: 'div.mermaid-block' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { class: 'mermaid-block' })]
  },
  addNodeView() {
    return VueNodeViewRenderer(MermaidView)
  },
})

const props = defineProps({
  modelValue: { type: String, default: '' },  // markdown 内容
  saving: { type: Boolean, default: false },
  projectId: { type: Number, default: null }, // 图表嵌入/插入选择器用
  // 工作台正文场景：是否显示"生成本章"，以及是否正 AI 生成中（生成中禁自动保存，避免覆盖 AI 流式内容）
  canGenerate: { type: Boolean, default: false },
  generating: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'save', 'generate'])

const editor = useEditor({
  content: markdownToHtml(props.modelValue),
  extensions: [
    StarterKit,
    TextStyle,
    Color,
    Highlight.configure({ multicolor: true }),
    Mermaid,
    Image.configure({ inline: false, allowBase64: false }),
    TextAlign.configure({ types: ['heading', 'paragraph'] }),
    Table.configure({ resizable: true }),
    TableRow,
    TableHeader,
    TableCell,
  ],
  editorProps: {
    attributes: { class: 'tiptap-content' },
    handleClick(view, pos) {
      // 点击图片：循环缩放 100% / 75% / 50%
      try {
        const dom = view.dom.querySelectorAll('img')
        const imgs = Array.from(dom)
        const target = imgs.find(img => {
          try { return view.posAtDOM(img) === pos } catch { return false }
        })
        if (target) {
          const cur = target.getAttribute('data-width') || '100%'
          const order = ['100%', '75%', '50%']
          const next = order[(order.indexOf(cur) + 1) % order.length]
          editor.value?.chain().focus().updateAttributes('image', { 'data-width': next }).run()
          return true
        }
      } catch { /* ignore */ }
      return false
    },
  },
})

// 粘贴图片：从剪贴板读取图片文件，上传后插入编辑器
async function handlePasteImage(file) {
  if (!props.projectId) return
  try {
    const { data } = await uploadEditorImage(props.projectId, file)
    editor.value?.chain().focus().setImage({ src: data.url, alt: file.name || '' }).run()
  } catch (e) {
    // 上传失败静默，避免打断编辑
    console.error('图片上传失败', e)
  }
}

// markdown → HTML（用 marked 转换，供 TipTap 加载）
// 语义规则（2026-09-09 定稿）：
// - AI/编写思路内容（md 格式）：出现 [待补充]/[图片占位] 一律包成真实 <mark> 底色（与工具栏
//   「文字底色·警示红 #fde8e8」同款），Highlight 扩展解析为**可编辑的 mark**——用户可直接清除/改色。
//   保存为 HTML 时 mark 随之落库，即"AI 重写就重新红"。
// - 已存储的 HTML（format=html，以 < 开头）：**不再自动补红**，是否标红以内容自身为准——
//   用户清除 mark 后保存的纯文本 token 重开保持不红（清除持久）；真实 <mark> 在重开时照常显示。
function markdownToHtml(md) {
  if (!md) return '<p></p>'
  if (/^\s*</.test(md)) return unwrapStrikethrough(md)
  try {
    return wrapTdzAsMark(
      mermaidBlocksToNodes(unwrapBlockquotes(marked.parse(protectTilde(md)))),
    )
  } catch {
    return '<p></p>'
  }
}
// 里程/标段区间的单个波浪号（如 DK235+900~DK236+610）若原样进 marked，GFM 会把成对的 `~`
// 误配成删除线 → 输出 <del>、正文里波浪号消失。正文不使用删除线语法（工具栏亦无此按钮），
// 故载入前把 ~ 一律转成 HTML 实体，marked 不解析、Tiptap 渲染回字面 ~。
function protectTilde(md) {
  return md.split('~').join('&#126;')
}
// 剥掉删除线标签只留文字：兜底清理存量/误生成的 <del><s><strike>（正文不应有删除线）。
function unwrapStrikethrough(html) {
  if (!html) return html
  return html.replace(/<\/?(?:del|s|strike)[^>]*>/gi, '')
}
// 生成正文不使用"引用"样式：把 AI markdown 的 `>` 引用块解开为普通段落（内容保留、样式去除）。
// （工具栏已移除"引用"；存量 HTML 里的 <blockquote> 由 CSS 渲染为普通段落兜底。）
function unwrapBlockquotes(html) {
  if (!html) return html
  return html.replace(/<blockquote[^>]*>([\s\S]*?)<\/blockquote>/g, (_m, inner) => inner)
}
// 把纯文本的 [待补充]/[图片占位] 包成真实 <mark>；已处于 <mark> 内的跳过，避免嵌套双红。
const TDZ_RE = /\[待补充\]|\(待补充\)|【待补充】|\[图片占位[^\]]*\]/g
const MARK_SEG = /<mark\b[^>]*>[\s\S]*?<\/mark>/g
function wrapTdzAsMark(html) {
  if (!html) return html
  let out = ''
  let last = 0
  let m
  MARK_SEG.lastIndex = 0
  const wrap = (seg) =>
    (seg || '').replace(TDZ_RE, (tok) => `<mark style="background-color: #fde8e8">${tok}</mark>`)
  while ((m = MARK_SEG.exec(html)) !== null) {
    out += wrap(html.slice(last, m.index))
    out += m[0] // 已 mark 包裹的原样保留
    last = m.index + m[0].length
  }
  return out + wrap(html.slice(last))
}

// ```mermaid 代码块 → mermaid 节点（marked 产出 <pre><code class="language-mermaid">）
function mermaidBlocksToNodes(html) {
  return html.replace(
    /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g,
    (_m, code) => `<div class="mermaid-block" data-code="${code}"></div>`,
  )
}

// mermaid 节点 → ```mermaid（存回 markdown 格式时还原；Word 导出同理）
function htmlToMarkdown(html) {
  return html.replace(
    /<div class="mermaid-block" data-code="([\s\S]*?)"><\/div>/g,
    (_m, code) => '\n```mermaid\n' + code + '\n```\n',
  )
}

// 工具栏按钮
const toolbarButtons = [
  { label: '一级标题', icon: 'H1', action: () => editor.value?.chain().focus().toggleHeading({ level: 1 }).run(), isActive: () => editor.value?.isActive('heading', { level: 1 }) },
  { label: '二级标题', icon: 'H2', action: () => editor.value?.chain().focus().toggleHeading({ level: 2 }).run(), isActive: () => editor.value?.isActive('heading', { level: 2 }) },
  { label: '三级标题', icon: 'H3', action: () => editor.value?.chain().focus().toggleHeading({ level: 3 }).run(), isActive: () => editor.value?.isActive('heading', { level: 3 }) },
  { label: '加粗', icon: 'B', action: () => editor.value?.chain().focus().toggleBold().run(), isActive: () => editor.value?.isActive('bold') },
  { label: '斜体', icon: 'I', action: () => editor.value?.chain().focus().toggleItalic().run(), isActive: () => editor.value?.isActive('italic') },
  { label: '无序列表', icon: '•', action: () => editor.value?.chain().focus().toggleBulletList().run(), isActive: () => editor.value?.isActive('bulletList') },
  { label: '有序列表', icon: '1.', action: () => editor.value?.chain().focus().toggleOrderedList().run(), isActive: () => editor.value?.isActive('orderedList') },
  { label: '左对齐', icon: '⇤', action: () => editor.value?.chain().focus().setTextAlign('left').run(), isActive: () => editor.value?.isActive({ textAlign: 'left' }) },
  { label: '居中', icon: '⇹', action: () => editor.value?.chain().focus().setTextAlign('center').run(), isActive: () => editor.value?.isActive({ textAlign: 'center' }) },
  { label: '右对齐', icon: '⇥', action: () => editor.value?.chain().focus().setTextAlign('right').run(), isActive: () => editor.value?.isActive({ textAlign: 'right' }) },
  { label: '清除格式', icon: '⌫', action: () => editor.value?.chain().focus().unsetAllMarks().clearNodes().run() },
  { label: '插入图片', icon: '🖼', action: pickImage },
  { label: '撤销', icon: '↶', action: () => editor.value?.chain().focus().undo().run() },
  { label: '重做', icon: '↷', action: () => editor.value?.chain().focus().redo().run() },
]

const imageInput = ref(null)
function pickImage() {
  imageInput.value?.click()
}
async function onImagePicked(e) {
  const file = e.target.files?.[0]
  if (!file) return
  await handlePasteImage(file)
  e.target.value = ''
}

function insertTable() {
  editor.value?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()
}

// ---------- 文字颜色 / 底色（8 常用色块面板；警示红与 [待补充] 底色一致，视觉统一） ----------
// 常用文字颜色
const FG_COLORS = [
  { name: '默认黑', color: '#1f2937' },
  { name: '深红（强调）', color: '#c0392b' },
  { name: '橙色', color: '#e67e22' },
  { name: '绿（正确/通过）', color: '#1e8449' },
  { name: '蓝（信息）', color: '#2f5bd8' },
  { name: '紫', color: '#7d3c98' },
  { name: '棕', color: '#8a5a12' },
  { name: '灰', color: '#6b7280' },
]
// 常用底色（含与 [待补充] 相同的警示红 #fde8e8）
const BG_COLORS = [
  { name: '警示红（待补充）', color: '#fde8e8' },
  { name: '黄（提醒）', color: '#fef9c3' },
  { name: '绿（通过）', color: '#dcf5e7' },
  { name: '蓝（信息）', color: '#dbeafe' },
  { name: '橙（警告）', color: '#fde8d6' },
  { name: '紫', color: '#f3e6fb' },
  { name: '粉', color: '#fce7f0' },
  { name: '浅灰', color: '#eceef1' },
]
const openPalette = ref(null) // 'color' | 'bg' | null
function togglePalette(which) {
  openPalette.value = openPalette.value === which ? null : which
}
// 读取选区当前颜色/底色（用于按钮指示）；无则空
const fgActive = computed(() => {
  try { return editor.value?.getAttributes('textStyle').color || '' } catch { return '' }
})
const bgActive = computed(() => {
  try { return editor.value?.getAttributes('highlight').color || '' } catch { return '' }
})
const hasTextColor = computed(() => !!fgActive.value)
const hasHighlight = computed(() => !!bgActive.value)
function applyColor(color) {
  editor.value?.chain().focus().setColor(color).run()
  openPalette.value = null
}
function applyBg(color) {
  editor.value?.chain().focus().setHighlight({ color }).run()
  openPalette.value = null
}
function clearColor() {
  editor.value?.chain().focus().unsetColor().unsetHighlight().run()
  openPalette.value = null
}
// 点击面板外关闭
function onDocClick(e) {
  if (openPalette.value && !e.target.closest('.tb-color-wrap, .tb-bg-wrap')) openPalette.value = null
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))

// 自动保存：用户编辑停顿 2.2s 自动落库（生成中/程序加载时不触发，避免覆盖 AI 流式内容）
let autoTimer = null
function scheduleAutosave() {
  if (props.generating) return
  clearTimeout(autoTimer)
  autoTimer = setTimeout(() => {
    const ed = editor.value
    // 仅当编辑器处于用户焦点（手动输入）才自动保存——程序 setContent（切章/流式）不触发
    if (!ed || !ed.isFocused || props.generating) return
    const html = ed.getHTML() || ''
    emit('save', html, true)  // auto=true：父端静默保存，不弹提示
  }, 2200)
}

watch(editor, (ed) => {
  if (!ed) return
  ed.on('update', scheduleAutosave)
  // 粘贴图片：编辑器捕获剪贴板图片 → 上传后插入
  ed.view.dom.addEventListener('paste', (ev) => {
    const items = ev.clipboardData?.items || []
    for (const it of items) {
      if (it.type && it.type.startsWith('image/')) {
        const file = it.getAsFile()
        if (file) {
          ev.preventDefault()
          handlePasteImage(file)
          return
        }
      }
    }
  })
})

// 生成中锁只读：AI 流式写入期间禁止用户编辑正文（生成完成/失败后恢复可编辑）
watch([() => props.generating, editor], () => {
  editor.value?.setEditable(!props.generating)
}, { immediate: true })

function save() {
  // 手动保存
  const html = editor.value?.getHTML() || ''
  emit('save', html, false)
}

// 外部 modelValue 变化时同步（如切换到另一章节）
watch(() => props.modelValue, (nv) => {
  const cur = editor.value?.getHTML()
  if (editor.value && nv !== cur) {
    editor.value.commands.setContent(markdownToHtml(nv))
  }
})

onBeforeUnmount(() => {
  if (autoTimer) clearTimeout(autoTimer)
  editor.value?.destroy()
})
</script>

<style scoped>
.tiptap-editor { display: flex; flex-direction: column; height: 100%; }

/* 生成中只读态：工具栏禁用点击、正文不可选中（setEditable 已关，双保险） */
.tiptap-editor.gen-locked .toolbar .tb-btn,
.tiptap-editor.gen-locked .toolbar .tb-save .el-button { pointer-events: none; opacity: .5; }
.tiptap-editor.gen-locked .editor-content :deep(.tiptap-content) { cursor: progress; user-select: none; }
.toolbar {
  display: flex; align-items: center; gap: 2px; padding: 6px 10px;
  border-bottom: 1px solid var(--line); flex-wrap: wrap; background: var(--card);
}
.tb-btn {
  min-width: 28px; height: 28px; border: 1px solid transparent; border-radius: var(--radius-s);
  background: transparent; cursor: pointer; font-size: 12px; color: var(--text-2);
  display: inline-flex; align-items: center; justify-content: center;
  font-family: var(--font-num); transition: all 0.12s ease-out;
}
.tb-btn:hover { background: var(--paper-warm); color: var(--text); }
.tb-btn.active { background: var(--brand-soft); color: var(--brand); border-color: #c4d6f5; font-weight: 700; }
/* 文字颜色/底色按钮 + 8 色块弹出面板 */
.tb-color-wrap, .tb-bg-wrap { position: relative; display: inline-flex; }
.tb-color .tc-letter, .tb-bg .tc-letter {
  font-size: 13px; font-weight: 700; line-height: 1; padding: 1px 3px; border-radius: 3px; color: var(--text);
}
.tb-color .tc-letter { text-decoration: underline; }
.tb-bg .tc-letter { border: 1px solid #d0d5dd; }
.tb-color.on, .tb-bg.on { background: var(--brand-soft); border-color: #c4d6f5; }
.tb-palette {
  position: absolute; top: 32px; z-index: 40; width: 188px; padding: 8px;
  background: #fff; border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  box-shadow: 0 6px 20px rgba(0,0,0,0.12);
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px;
}
.tb-bg-wrap .tb-palette { left: auto; right: 0; }
.tp-label { font-size: 11px; color: var(--text-3); grid-column: 1 / -1; margin-bottom: 2px; }
.tp-swatch {
  height: 24px; border: 1px solid #e0e4ea; border-radius: 4px; cursor: pointer;
  padding: 0; transition: transform 0.1s, box-shadow 0.1s;
}
.tp-swatch:hover { transform: scale(1.1); box-shadow: 0 1px 5px rgba(0,0,0,0.25); }
.tp-clear {
  grid-column: 1 / -1; border: 1px dashed var(--line-strong); border-radius: 4px;
  background: none; color: var(--text-2); font-size: 11px; padding: 4px 0; cursor: pointer;
}
.tp-clear:hover { border-color: var(--red); color: var(--red); }
.tb-sep { width: 1px; height: 18px; background: var(--line); margin: 0 5px; }
.tb-save { margin-left: auto; }

/* 正文排版：类 Word 阅读体验 */
.editor-content { flex: 1; overflow-y: auto; background: var(--paper); }
.editor-content :deep(.tiptap-content) {
  padding: 28px 40px 64px; outline: none; min-height: 100%;
  max-width: 860px; margin: 0 auto; background: var(--card);
  font-size: 15px; color: var(--text);
}
.editor-content :deep(.tiptap-content h1) {
  font-size: 21px; font-weight: 700; margin: 24px 0 12px; padding-bottom: 8px;
  border-bottom: 2px solid var(--brand-soft); letter-spacing: 0.01em;
}
.editor-content :deep(.tiptap-content h2) { font-size: 18px; font-weight: 700; margin: 20px 0 10px; }
.editor-content :deep(.tiptap-content h3) { font-size: 16px; font-weight: 600; margin: 16px 0 8px; }
.editor-content :deep(.tiptap-content p) { line-height: 1.9; margin: 6px 0; text-align: justify; }
.editor-content :deep(.tiptap-content ul),
.editor-content :deep(.tiptap-content ol) { padding-left: 24px; line-height: 1.85; }
.editor-content :deep(.tiptap-content table) { border-collapse: collapse; margin: 12px 0; width: 100%; }
.editor-content :deep(.tiptap-content th),
.editor-content :deep(.tiptap-content td) { border: 1px solid var(--line-strong); padding: 7px 12px; font-size: 14px; }
.editor-content :deep(.tiptap-content th) { background: var(--paper-warm); font-weight: 600; }
/* 正文不使用"引用"样式：存量正文即使含 <blockquote> 也呈现为普通段落 */
.editor-content :deep(.tiptap-content blockquote) { margin: 6px 0; padding: 0; border: 0; background: none; color: var(--text); }
/* 图片：支持 data-width 缩放（100%/75%/50%），点击循环切换 */
.editor-content :deep(.tiptap-content img) { max-width: 100%; display: block; margin: 8px auto; }
.editor-content :deep(.tiptap-content img[data-width="75%"]) { width: 75%; }
.editor-content :deep(.tiptap-content img[data-width="50%"]) { width: 50%; }
.editor-content :deep(.tiptap-content .selectedCell) { background: var(--brand-soft); }
</style>
