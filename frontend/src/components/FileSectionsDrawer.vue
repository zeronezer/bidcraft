<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="(v) => emit('update:modelValue', v)"
    direction="rtl"
    size="72%"
    :with-header="false"
    class="fs-drawer"
  >
    <div class="fs-wrap">
      <header class="fs-head">
        <div>
          <div class="fs-title">章节索引 · {{ fileName }}</div>
          <div class="fs-sub">AI 建立的「目录 + 概要 + 适用范围」索引，点击章节查看完整原文（含表格与图片）</div>
        </div>
        <span class="fs-count num">{{ sections.length }} 节</span>
      </header>

      <div v-if="!sections.length" class="fs-empty">
        <p>该文件暂无章节索引</p>
        <p class="fs-empty-sub">解析完成后自动建立；如刚解析请稍候刷新</p>
      </div>

      <div v-else class="fs-body">
        <!-- 左：索引**树**（按章节层级组织；分组节点仅用于展开，叶子节点可点开看原文） -->
        <aside class="fs-list">
          <el-tree
            :data="treeData"
            node-key="path"
            :props="{ label: 'label', children: 'children' }"
            :expand-on-click-node="false"
            :highlight-current="true"
            :current-node-key="current?.path || ''"
            :default-expanded-keys="defaultExpanded"
            @node-click="onNodeClick"
          >
            <template #default="{ data }">
              <span class="fs-node" :class="{ group: !data.id }">
                <span class="fs-node-label" :title="data.path">{{ data.label }}</span>
                <span v-if="data.charCount" class="fs-node-cc num">{{ data.charCount }} 字</span>
              </span>
            </template>
          </el-tree>
        </aside>

        <!-- 右：概要/适用范围（可折叠） + 章节原文 -->
        <section class="fs-content">
          <template v-if="current">
            <div class="fs-content-head">
              <span class="fs-content-path" :title="current.sec_path">{{ current.sec_path }}</span>
              <button class="fs-toggle" @click="metaOpen = !metaOpen">{{ metaOpen ? '收起索引信息 ▲' : '展开索引信息 ▼' }}</button>
            </div>
            <div v-if="metaOpen" class="fs-meta-panel">
              <div class="fs-meta-row">
                <span class="fs-meta-label">章节概要</span>
                <p class="fs-meta-text">{{ current.summary || '—' }}</p>
              </div>
              <div class="fs-meta-row">
                <span class="fs-meta-label">适用范围</span>
                <p class="fs-meta-text">{{ current.applies_to || '—' }}</p>
              </div>
            </div>
            <div v-if="loading" class="fs-loading">原文加载中…</div>
            <div v-else class="fs-md" v-html="html" />
          </template>
          <div v-else class="fs-empty"><p>点击左侧章节查看概要与完整原文</p></div>
        </section>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { marked } from 'marked'
import { fileSections, fileSectionContent } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: [Number, String], default: null },
  fileId: { type: Number, default: null },
  fileName: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const sections = ref([])
const current = ref(null)
const text = ref('')
const loading = ref(false)
const metaOpen = ref(true)

const html = computed(() => {
  try {
    return text.value ? marked.parse(text.value) : '<p class="fs-none">（该章节无文本内容）</p>'
  } catch {
    return text.value
  }
})

// ---------- 章节索引树：把扁平的 sec_path（"第一章 > 第一节 …"）拼成树 ----------
// 中间分组节点没有真实章节 id（只用于展开/收起）；叶子节点（有 id）点开看原文。
const treeData = computed(() => {
  const root = { children: [] }
  const map = new Map()
  for (const s of sections.value) {
    const parts = String(s.sec_path || '').split(' > ').filter(Boolean)
    if (!parts.length) continue
    let parent = root
    let path = ''
    parts.forEach((part, i) => {
      path = path ? `${path} > ${part}` : part
      let node = map.get(path)
      if (!node) {
        node = { label: part, path, id: null, charCount: 0, children: [] }
        map.set(path, node)
        parent.children.push(node)
      }
      if (i === parts.length - 1) {  // 该路径本身就是一个真实章节
        node.id = s.id
        node.charCount = s.char_count || 0
        node.section = s
      }
      parent = node
    })
  }
  return root.children
})

// 默认展开第一层，其余收起（大文档下避免一次铺开上千行）
const defaultExpanded = computed(() => treeData.value.slice(0, 3).map((n) => n.path))

function onNodeClick(data) {
  if (data?.id && data.section) select(data.section)
}

async function select(s) {
  current.value = s
  loading.value = true
  text.value = ''
  try {
    const { data } = await fileSectionContent(props.projectId, props.fileId, s.id)
    text.value = data.text
  } catch {
    text.value = '原文加载失败'
  } finally {
    loading.value = false
  }
}

watch(() => [props.modelValue, props.fileId], async ([open, fid]) => {
  if (!open || !fid) return
  sections.value = []
  current.value = null
  try {
    sections.value = (await fileSections(props.projectId, fid)).data
  } catch { /* ignore */ }
})
</script>

<style scoped>
.fs-wrap { display: flex; flex-direction: column; height: 100%; }
.fs-head { display: flex; align-items: flex-start; justify-content: space-between; padding: 16px 20px 12px; border-bottom: 1px solid var(--line); }
.fs-title { font-size: 15px; font-weight: 700; }
.fs-sub { font-size: 11px; color: var(--text-3); margin-top: 3px; }
.fs-count { font-size: 12px; color: var(--text-3); }

.fs-empty { padding: 50px 20px; text-align: center; color: var(--text-3); font-size: 13px; }
.fs-empty-sub { font-size: 11px; margin-top: 6px; }

.fs-body { flex: 1; display: flex; min-height: 0; }
.fs-list { width: 300px; flex-shrink: 0; overflow-y: auto; border-right: 1px solid var(--line); background: var(--paper-warm); padding: 6px 4px; }
/* 索引树：分组节点（无 id）弱化，叶子章节可比可点 */
.fs-node { display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; padding-right: 6px; }
.fs-node-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; }
.fs-node.group .fs-node-label { color: var(--text-2); font-weight: 600; }
.fs-node-cc { flex-shrink: 0; font-size: 10px; color: var(--text-3); }
.fs-list :deep(.el-tree-node__content) { height: 28px; border-radius: 4px; }
.fs-list :deep(.el-tree-node.is-current > .el-tree-node__content) { background: var(--brand-soft); }
.fs-list :deep(.el-tree-node.is-current > .el-tree-node__content .fs-node-label) { color: var(--brand); font-weight: 600; }
.fs-path {
  font-size: 12px; font-weight: 600; color: var(--text); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.fs-meta { margin-top: 3px; font-size: 10px; color: var(--text-3); }

.fs-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.fs-content-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 12px 20px; border-bottom: 1px solid var(--line); font-size: 13px; font-weight: 700; color: var(--text-2);
}
.fs-content-path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fs-toggle {
  flex-shrink: 0; border: 1px solid var(--line-strong); background: var(--card);
  color: var(--text-3); font-size: 11px; padding: 3px 10px; border-radius: var(--radius-s); cursor: pointer;
}
.fs-toggle:hover { color: var(--brand); border-color: var(--brand); }
/* 概要/适用范围信息区 */
.fs-meta-panel { padding: 12px 20px; background: var(--brand-soft); border-bottom: 1px solid var(--line); }
.fs-meta-row { display: flex; gap: 10px; margin-bottom: 8px; }
.fs-meta-row:last-child { margin-bottom: 0; }
.fs-meta-label {
  flex-shrink: 0; font-size: 11px; font-weight: 700; color: var(--brand);
  padding: 2px 0;
}
.fs-meta-text { flex: 1; margin: 0; font-size: 12px; color: var(--text-2); line-height: 1.7; }
.fs-loading { padding: 30px; text-align: center; color: var(--text-3); font-size: 12px; }
.fs-md { flex: 1; overflow-y: auto; padding: 20px 26px; font-size: 13px; color: var(--text); }
.fs-md :deep(h1), .fs-md :deep(h2), .fs-md :deep(h3) { margin: 16px 0 8px; }
.fs-md :deep(p) { line-height: 1.8; margin: 6px 0; }
.fs-md :deep(table) { border-collapse: collapse; width: 100%; margin: 10px 0; }
.fs-md :deep(th), .fs-md :deep(td) { border: 1px solid var(--line-strong); padding: 6px 10px; font-size: 12px; }
.fs-md :deep(th) { background: var(--paper-warm); font-weight: 600; }
.fs-md :deep(img) { max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 6px; }
.fs-none { color: var(--text-3); }
</style>

<style>
/* 抽屉 teleport 到 body，需非 scoped 覆盖：去掉默认 header 与 body 内边距，避免顶部空白 */
.fs-drawer .el-drawer__body { padding: 0; }
</style>
