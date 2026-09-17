<template>
  <div class="ml">
    <!-- 页头 -->
    <header class="page-head">
      <button class="back-btn" @click="goBack">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>
        返回知识库
      </button>
      <div class="head-title">
        <h1 class="page-title">工艺工法</h1>
        <span class="page-sub">每个工法一条概况；编写「施工方案」章节时按需选中并整篇注入（含文字/工艺/表格/图片）</span>
      </div>
    </header>

    <div class="ml-body">
      <!-- 左：工法列表 -->
      <aside class="list-panel">
        <div class="list-head">
          工法库
          <span class="list-count num">{{ methods.length }} 个</span>
        </div>
        <div class="list-search">
          <el-input v-model="keyword" size="small" placeholder="搜索工法名称/适用范围…" clearable />
        </div>
        <div class="list-scroll">
          <div v-if="!filtered.length" class="list-empty">
            <p>暂无工法</p>
            <p class="list-empty-sub">在知识库「工艺工法库」上传工法文档，AI 将建立概况</p>
          </div>
          <div v-for="m in filtered" :key="m.doc_id" class="method-card" :class="{ on: current?.doc_id === m.doc_id }" @click="select(m)">
            <div class="method-name">{{ m.name }}</div>
            <div class="method-src">{{ m.file_name }}</div>
          </div>
        </div>
      </aside>

      <!-- 右：概况 + 整篇工法 -->
      <section class="detail-panel">
        <template v-if="current">
          <div class="detail-head">
            <span class="detail-label">{{ current.name }}</span>
            <div class="detail-head-right">
              <span class="detail-file num">{{ current.file_name }}</span>
              <button class="fs-toggle" @click="metaOpen = !metaOpen">{{ metaOpen ? '收起索引信息 ▲' : '展开索引信息 ▼' }}</button>
            </div>
          </div>
          <div v-if="metaOpen" class="fs-meta-panel">
            <div class="fs-meta-row"><span class="fs-meta-label">工法概况</span><p class="fs-meta-text">{{ current.summary || '—' }}</p></div>
            <div class="fs-meta-row"><span class="fs-meta-label">适用范围</span><p class="fs-meta-text">{{ current.applies_to || '—' }}</p></div>
          </div>
          <div v-if="loading" class="ml-loading">工法全文加载中…</div>
          <div v-else class="ml-md" v-html="html" />
        </template>
        <div v-else class="detail-empty">
          <div class="de-icon">⚙</div>
          <p>选择左侧工法查看完整做法</p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import { listMethods, getMethodContent } from '../api'

const route = useRoute()
const router = useRouter()
// 返回知识库并回到进入前来源库 tab
function goBack() {
  const t = route.query.from
  router.push({ path: '/knowledge', query: (t === 'sod' || t === 'method') ? { tab: t } : { tab: 'method' } })
}
const keyword = ref('')
const methods = ref([])
const current = ref(null)
const text = ref('')
const loading = ref(false)
const metaOpen = ref(true)

const html = computed(() => {
  try {
    return text.value ? marked.parse(text.value) : '<p class="ml-none">（无文本内容）</p>'
  } catch {
    return text.value
  }
})

const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return methods.value
  return methods.value.filter(m =>
    (m.name || '').toLowerCase().includes(k) ||
    (m.summary || '').toLowerCase().includes(k) ||
    (m.applies_to || '').toLowerCase().includes(k))
})

async function select(m) {
  current.value = m
  loading.value = true
  text.value = ''
  try {
    const { data } = await getMethodContent(m.doc_id)
    text.value = data.text
  } catch {
    text.value = '工法原文加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const { data } = await listMethods()
    methods.value = data
    // 从知识库文档行进入：定位到对应工法并展开
    if (route.query.doc) {
      const target = data.find(m => m.doc_id === Number(route.query.doc))
      if (target) select(target)
    } else if (data.length) {
      select(data[0])
    }
  } catch { /* ignore */ }
})
</script>

<style scoped>
.ml { height: 100%; display: flex; flex-direction: column; padding: 24px 28px; }

.page-head { display: flex; align-items: center; gap: 16px; margin-bottom: 18px; }
.back-btn {
  display: inline-flex; align-items: center; gap: 5px; background: none;
  border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  color: var(--text-2); font-size: 12px; padding: 6px 12px; cursor: pointer; transition: all 0.15s;
}
.back-btn:hover { color: var(--brand); border-color: var(--brand); }
.head-title { display: flex; align-items: baseline; gap: 12px; }
.page-title { font-size: 20px; font-weight: 700; margin: 0; }
.page-sub { font-size: 12px; color: var(--text-3); }

.ml-body { flex: 1; display: flex; min-height: 0; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-m); overflow: hidden; }

.list-panel { width: 360px; flex-shrink: 0; border-right: 1px solid var(--line); display: flex; flex-direction: column; background: var(--paper-warm); }
.list-head { padding: 12px 14px; font-size: 11px; font-weight: 700; color: var(--text-3); letter-spacing: 0.1em; display: flex; justify-content: space-between; }
.list-search { padding: 8px 10px; border-bottom: 1px solid var(--line); }
.list-scroll { flex: 1; overflow-y: auto; }
.list-empty { padding: 30px 16px; text-align: center; font-size: 12px; color: var(--text-2); }
.list-empty-sub { font-size: 11px; color: var(--text-3); }
.method-card { padding: 10px 14px; border-bottom: 1px solid var(--line); cursor: pointer; transition: background 0.12s; }
.method-card:hover { background: var(--card); }
.method-card.on { background: var(--brand-soft); border-left: 3px solid var(--brand); }
.method-name { font-size: 13px; font-weight: 700; line-height: 1.5; }
.method-src { font-size: 10px; color: var(--text-3); margin-top: 3px; }

.detail-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.detail-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-3); }
.de-icon { font-size: 28px; color: var(--line-strong); margin-bottom: 10px; }
.detail-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 20px; border-bottom: 1px solid var(--line); font-size: 15px; font-weight: 700; color: var(--text); }
.detail-head-right { display: flex; align-items: center; gap: 10px; }
.detail-file { font-size: 11px; color: var(--text-3); font-weight: 400; }
.fs-toggle {
  flex-shrink: 0; border: 1px solid var(--line-strong); background: var(--card);
  color: var(--text-3); font-size: 11px; padding: 3px 10px; border-radius: var(--radius-s); cursor: pointer;
}
.fs-toggle:hover { color: var(--brand); border-color: var(--brand); }
.fs-meta-panel { padding: 12px 20px; background: var(--brand-soft); border-bottom: 1px solid var(--line); }
.fs-meta-row { display: flex; gap: 10px; margin-bottom: 8px; }
.fs-meta-row:last-child { margin-bottom: 0; }
.fs-meta-label { flex-shrink: 0; font-size: 11px; font-weight: 700; color: var(--brand); padding: 2px 0; }
.fs-meta-text { flex: 1; margin: 0; font-size: 12px; color: var(--text-2); line-height: 1.7; }
.ml-loading { padding: 30px; text-align: center; color: var(--text-3); font-size: 12px; }
.ml-md { flex: 1; overflow-y: auto; padding: 24px 32px; font-size: 14px; color: var(--text); line-height: 1.8; }
.ml-md :deep(h1), .ml-md :deep(h2), .ml-md :deep(h3) { margin: 20px 0 10px; }
.ml-md :deep(h1) { font-size: 20px; }
.ml-md :deep(h2) { font-size: 17px; }
.ml-md :deep(p) { margin: 8px 0; }
.ml-md :deep(table) { border-collapse: collapse; width: 100%; margin: 12px 0; }
.ml-md :deep(th), .ml-md :deep(td) { border: 1px solid var(--line-strong); padding: 6px 10px; font-size: 13px; }
.ml-md :deep(th) { background: var(--paper-warm); font-weight: 600; }
.ml-md :deep(img) { max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 6px; }
.ml-none { color: var(--text-3); }
</style>
