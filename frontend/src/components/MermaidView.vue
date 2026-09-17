<template>
  <node-view-wrapper class="mermaid-view">
    <div v-if="!showSource" class="mm-render" :class="{ failed: !!error }">
      <!-- 渲染成功：SVG 图 -->
      <div v-if="svg" class="mm-svg" v-html="svg" />
      <!-- 渲染失败：降级显示源码 + 错误提示（用户可直接改） -->
      <div v-else class="mm-error">
        <div class="mm-error-tip">流程图语法有误（{{ error }}），双击图块修改源码</div>
        <pre class="mm-src-readonly">{{ node.attrs.code }}</pre>
      </div>
      <button class="mm-src-btn" title="编辑源码" @click.stop="showSource = true">⌨ 源码</button>
    </div>
    <div v-else class="mm-edit">
      <textarea v-model="editingCode" class="mm-textarea" spellcheck="false" />
      <div class="mm-edit-actions">
        <button class="mm-btn" @click.stop="showSource = false">取消</button>
        <button class="mm-btn primary" @click.stop="applySource">应用</button>
      </div>
    </div>
  </node-view-wrapper>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { nodeViewProps, NodeViewWrapper } from '@tiptap/vue-3'

const props = defineProps(nodeViewProps)
const svg = ref('')
const error = ref('')
const showSource = ref(false)
const editingCode = ref('')

let renderSeq = 0

async function render(code) {
  if (!code || !code.trim()) {
    svg.value = ''
    error.value = ''
    return
  }
  const seq = ++renderSeq
  try {
    // 动态引入：mermaid 体积大，按需加载不拖慢首屏
    const { default: mermaid } = await import('mermaid')
    mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' })
    const { svg: out } = await mermaid.render(`mm-${Date.now()}-${seq}`, code)
    if (seq === renderSeq) {
      svg.value = out
      error.value = ''
    }
  } catch (e) {
    if (seq === renderSeq) {
      svg.value = ''
      error.value = String(e?.message || e).slice(0, 80)
    }
  }
}

function applySource() {
  props.updateAttributes({ code: editingCode.value })
  showSource.value = false
}

watch(() => props.node.attrs.code, (c) => render(c), { immediate: false })
onMounted(() => render(props.node.attrs.code))

function onSourceOpen() {
  editingCode.value = props.node.attrs.code || ''
}
watch(showSource, (v) => { if (v) onSourceOpen() })
</script>

<style scoped>
.mermaid-view { margin: 14px 0; }
.mm-render {
  position: relative; border: 1px solid var(--line); border-radius: var(--radius-s);
  background: var(--paper-warm); padding: 16px; min-height: 60px; overflow-x: auto;
}
.mm-svg :deep(svg) { max-width: 100%; height: auto; display: block; margin: 0 auto; }
.mm-error-tip { font-size: 12px; color: var(--red); margin-bottom: 8px; }
.mm-src-readonly { font-size: 11px; color: var(--text-2); margin: 0; white-space: pre-wrap; }
.mm-src-btn {
  position: absolute; top: 6px; right: 6px; font-size: 11px; padding: 2px 8px;
  border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  background: var(--card); color: var(--text-3); cursor: pointer; opacity: 0.4; transition: opacity 0.15s;
}
.mm-render:hover .mm-src-btn { opacity: 1; }
.mm-src-btn:hover { color: var(--brand); border-color: var(--brand); }
.mm-edit { border: 1px solid var(--brand); border-radius: var(--radius-s); padding: 10px; background: var(--card); }
.mm-textarea {
  width: 100%; min-height: 120px; font-family: var(--font-num); font-size: 12px;
  border: 1px solid var(--line); border-radius: var(--radius-s); padding: 8px; resize: vertical;
  outline: none;
}
.mm-edit-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 8px; }
.mm-btn {
  font-size: 12px; padding: 4px 12px; border-radius: var(--radius-s);
  border: 1px solid var(--line-strong); background: var(--card); color: var(--text-2); cursor: pointer;
}
.mm-btn.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
</style>
