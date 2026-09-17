<template>
  <div class="kp">
    <!-- 页头 -->
    <header class="page-head">
      <button class="back-btn" @click="goBack">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>
        返回知识库
      </button>
      <div class="head-title">
        <h1 class="page-title">{{ isProof ? '解析校对' : '知识页' }}</h1>
        <span class="page-sub">{{ isProof ? (proof.doc_name + ' · 左原文右解析对照') : '历史施组提炼的「写法参考」知识页' }}</span>
      </div>
    </header>

    <!-- 校对视图（M2-3：左原文右解析对照，人工修正后保存/重新提炼） -->
    <div v-if="isProof" class="proof-body">
      <div class="proof-col">
        <div class="proof-col-head">原文</div>
        <iframe v-if="proof.source_url" :src="proof.source_url" class="proof-iframe" title="原文预览" />
        <div v-else class="proof-nofile">原件暂不可预览（仅支持 PDF 预览）</div>
      </div>
      <div class="proof-divider" />
      <div class="proof-col">
        <div class="proof-col-head">
          <span>解析结果（可修正）</span>
          <div class="proof-actions">
            <el-button size="small" type="primary" :loading="proofSaving" @click="saveProof">保存修正</el-button>
            <el-button size="small" @click="reprocessDoc">重新提炼</el-button>
          </div>
        </div>
        <el-input type="textarea" v-model="proofMd" class="proof-editor" resize="none" placeholder="解析结果加载中…" />
      </div>
    </div>

    <!-- 浏览/编辑视图 -->
    <div v-else class="kp-body">
      <!-- 左：章节树（按 title 全路径"父 > 子"动态构建，替代已废弃的章节类型分组） -->
      <aside class="type-panel">
        <div class="type-head">
          {{ docFilter ? '章节结构' : '历史施组文档' }}
          <span v-if="totalPageCount" class="type-total num">{{ totalPageCount }} 页</span>
        </div>
        <div class="type-list">
          <el-tree
            ref="chapterTreeRef"
            :data="chapterTree"
            :props="{ label: 'title', children: 'children' }"
            node-key="key"
            :default-expanded-keys="expandedKeys"
            :expand-on-click-node="false"
            highlight-current
            @node-click="onChapterClick"
            class="kp-tree"
          >
            <template #default="{ data }">
              <div class="kp-tree-node" :title="data.title">
                <span class="kt-label">{{ data.title }}</span>
                <span v-if="data.pageCount" class="kt-count num">{{ data.pageCount }}</span>
              </div>
            </template>
          </el-tree>
          <div v-if="!chapterTree.length" class="type-empty">本库暂无知识页</div>
        </div>
      </aside>

      <!-- 中：知识页列表（平铺当前章节下的所有子页） -->
      <section class="list-panel">
        <div class="list-head">
          {{ keyword ? '搜索结果' : (activeChapterTitle || '知识页') }}
          <span class="list-count num">{{ currentPages.length }} 页</span>
        </div>
        <div class="list-search">
          <el-input v-model="keyword" size="small" placeholder="搜索当前文档知识页标题…" clearable prefix-icon="Search" />
        </div>
        <div class="list-scroll">
          <div v-if="!currentPages.length" class="list-empty">
            <p>{{ keyword ? '没有匹配的知识页' : (activeChapterTitle ? '该章节暂无知识页' : '请选择左侧章节') }}</p>
            <p class="list-empty-sub">{{ keyword ? '换个关键词试试' : '点选左侧章节查看其下知识页' }}</p>
          </div>
          <div
            v-for="p in currentPages" :key="p.page_id"
            class="page-card" :class="{ on: activePage?.page_id === p.page_id }"
            @click="selectPage(p)"
          >
            <div class="page-card-title">{{ p.title }}</div>
            <div class="page-card-meta">
              <span class="pc-tag" v-for="tag in p.tags.slice(0, 3)" :key="tag">{{ tag }}</span>
              <span v-if="p.estimate_words" class="pc-tag num">约 {{ p.estimate_words }} 字</span>
            </div>
            <div class="page-card-src">来源：{{ p.source.doc }}{{ p.source.location ? ' · ' + p.source.location : '' }}</div>
          </div>
        </div>
      </section>

      <!-- 右：详情编辑 -->
      <section class="detail-panel">
        <div v-if="!activePage" class="detail-empty">
          <div class="de-icon">▤</div>
          <p>选择左侧知识页查看详情</p>
        </div>
        <div v-else class="detail-body">
          <div class="detail-head">
            <span class="detail-label">知识页详情</span>
            <el-button size="small" type="primary" @click="savePage">保存修订</el-button>
          </div>
          <div class="detail-scroll">
            <div class="field">
              <label>标题</label>
              <el-input v-model="activePage.title" size="small" />
            </div>
            <div class="field">
              <label>预计字数（该章原文篇幅，编写时的篇幅参考）</label>
              <div class="field-readonly num">{{ activePage.estimate_words ? '约 ' + activePage.estimate_words + ' 字' : '—' }}</div>
            </div>
            <div class="field">
              <label>出处（只读）</label>
              <div class="field-readonly num">{{ activePage.source.doc }}{{ activePage.source.location ? ' · ' + activePage.source.location : '' }}</div>
            </div>
            <div class="field">
              <label>工程标签</label>
              <el-input v-model="activePage.tagsText" size="small" placeholder="逗号分隔" />
            </div>
            <div class="field">
              <label>编制方式 method<span class="np-tag">暂不参与正文生成</span></label>
              <el-input v-model="activePage.method" type="textarea" :rows="4" />
            </div>
            <div class="field">
              <label>要点 key_points<span class="np-tag">暂不参与正文生成</span></label>
              <div v-for="(kp, i) in activePage.key_points" :key="i" class="kp-item">
                <el-input v-model="activePage.key_points[i]" size="small" />
                <button class="kp-del" @click="activePage.key_points.splice(i, 1)">×</button>
              </div>
              <button class="add-btn" @click="activePage.key_points.push('')">＋ 添加要点</button>
            </div>
            <div class="field">
              <label>表格 tables（点击查看完整表格与填写说明）</label>
              <div v-for="(t, i) in activePage.tables" :key="i" class="table-chip clickable" :title="t.description || t.title" @click="previewTable = t">
                ▦ {{ t.title }}<span v-if="t.columns?.length" class="chip-extra">（{{ t.columns.length }} 列）</span>
              </div>
              <div v-if="!activePage.tables.length" class="field-empty">无</div>
            </div>
            <div class="field">
              <label>图片 images（点击放大查看）</label>
              <div class="img-grid">
                <div v-for="(img, i) in activePage.images" :key="i" class="img-card clickable" @click="previewImage = img">
                  <img v-if="img.url" :src="img.url" class="img-real" loading="lazy" alt="" />
                  <div v-else class="img-thumb">🖼</div>
                  <div class="img-cap">{{ img.caption }}</div>
                  <div v-if="img.content" class="img-usage" :title="img.content">{{ img.content }}</div>
                </div>
              </div>
              <div v-if="!activePage.images.length" class="field-empty">无</div>
            </div>
            <div class="field">
              <label>适用范围</label>
              <el-input v-model="activePage.usage" type="textarea" :rows="3" />
            </div>
            <div class="field">
              <label class="ref-label">
                历史内容参考（该历史项目原文）
                <span class="ref-hint">AI 仅参考其风格写法，禁止照搬其中数据</span>
              </label>
              <button class="add-btn" @click="showRefDialog" :disabled="refLoading">
                {{ refLoading ? '加载中…' : (pageRefText ? '查看原文' : '加载原文') }}
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 表格放大预览：完整表格 + 维度说明 + 逐列填写说明 -->
    <el-dialog v-model="previewTable" width="720" top="6vh" :title="previewTable?.title || '表格详情'" class="preview-dlg" destroy-on-close>
      <template v-if="previewTable">
        <div v-if="previewTable.description" class="pv-desc">{{ previewTable.description }}</div>
        <div v-if="previewTable.columns?.length" class="pv-columns">
          <div class="pv-columns-title">表头填写说明：</div>
          <div v-for="c in previewTable.columns" :key="c.name" class="pv-col-item">
            <b>{{ c.name }}</b>：{{ c.desc }}
          </div>
        </div>
        <div class="pv-table" v-html="renderMd(previewTable.content_md)" />
      </template>
    </el-dialog>

    <!-- 图片放大预览：原图 + 内容描述 + 用途 -->
    <el-dialog v-model="previewImage" width="640" top="6vh" :title="previewImage?.caption || '图片详情'" class="preview-dlg" destroy-on-close>
      <template v-if="previewImage">
        <img v-if="previewImage.url" :src="previewImage.url" class="pv-img" alt="" />
        <div v-else class="pv-img-empty">未挂接原图（该页提炼时未记录图片文件）</div>
        <div v-if="previewImage.content" class="pv-field"><span class="pv-label">图片内容：</span>{{ previewImage.content }}</div>
        <div v-if="previewImage.usage" class="pv-field"><span class="pv-label">用途说明：</span>{{ previewImage.usage }}</div>
      </template>
    </el-dialog>

    <!-- 历史内容参考弹窗：该历史项目原文（只作风格参考） -->
    <el-dialog v-model="refDialog" width="820" top="5vh"
      :title="`历史内容参考 · ${activePage?.title || ''}`" destroy-on-close class="ref-dlg">
      <div class="ref-dlg-hint">
        以下为该<strong>历史项目</strong>原文。AI 生成时只模仿其风格写法、结构与专业措辞；
        其中的项目名 / 标段 / 里程 / 工期 / 工程量 / 数据等属于该历史项目，<strong>不会照搬进本项目正文</strong>。
      </div>
      <div v-if="refLoading" class="field-empty">加载中…</div>
      <div v-else-if="pageRefText" class="ref-dlg-body" v-html="renderMd(pageRefText)" />
      <div v-else class="field-empty">
        本页暂无历史原文参考——需在知识库对该历史施组文档「重新提炼」或重新上传后自动补充。
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import { listKnowledgePages, updateKnowledgePage, getKnowledgePage,
  getParsed, saveParsed, reprocessKnowledgeDoc } from '../api'

const route = useRoute()
const router = useRouter()
// 返回知识库并回到进入前来源库 tab（sod 知识页 / method 解析校对均回到对应库）
function goBack() {
  const t = route.query.from
  router.push({ path: '/knowledge', query: (t === 'sod' || t === 'method') ? { tab: t } : { tab: 'sod' } })
}
const isProof = computed(() => route.query.view === 'proof')
const docFilter = computed(() => route.query.doc ? Number(route.query.doc) : null)

const chapterTreeRef = ref(null)
const activePage = ref(null)
const activeFull = ref('')        // 当前选中章节的全路径（title 路径前缀）
const pageRefText = ref('')   // 历史内容参考（原文，按需从详情接口加载）
const refLoading = ref(false)
const refDialog = ref(false)
const pages = ref([])
const loading = ref(false)
const keyword = ref('')

// ---------- 章节树构建：按 title 全路径"父 > 子"拆分 ----------
// title 如「5.1 大临工程 > 5.1.2 混凝土集中拌和站 > ⑵实施方案」；
// 每一段 = 一层，无独立页的中间层（如 5.2 只有子页）作为分组节点，点它展示子页聚合。
function splitPath(title) {
  return (title || '').split('>').map(s => s.trim()).filter(Boolean)
}

// 自然排序：提取标题开头编号（1 / 1.2 / 5.10）比较；无编号段按字符串兜底
function _numKey(seg) {
  const m = /^(\d+)(?:\.(\d+))*/.exec((seg || '').trim())
  if (m) return m[0].split('.').map(Number)
  return null
}
function _cmpSeg(a, b) {
  const ka = _numKey(a.title)
  const kb = _numKey(b.title)
  if (ka && kb) {
    const n = Math.max(ka.length, kb.length)
    for (let i = 0; i < n; i++) {
      const x = ka[i] || 0
      const y = kb[i] || 0
      if (x !== y) return x - y
    }
    return 0
  }
  return (a.title || '').localeCompare(b.title || '', 'zh-Hans-CN')
}

function buildDocTree(docPages) {
  // 返回 el-tree 数据：节点含 own 页数（该路径恰为本页标题）与子孙总数 pageCount
  const roots = []
  const childrenOf = (node) => node.children || (node.children = [])

  // 先按全路径归集（map: 全路径 -> node），再挂到父级下
  const nodeByPath = new Map()
  for (const p of docPages) {
    const segs = splitPath(p.title)
    if (!segs.length) continue
    // 逐级确保节点存在
    for (let i = 0; i < segs.length; i++) {
      const full = segs.slice(0, i + 1).join(' > ')
      if (!nodeByPath.has(full)) {
        nodeByPath.set(full, { key: full, title: segs[i], full, own: 0, children: [] })
      }
    }
    // 页面标题恰等于全路径时计为该路径的"本体页"
    const leaf = nodeByPath.get(segs.join(' > '))
    if (leaf) leaf.own += 1
  }
  // 挂父子（按出现序稳定）
  for (const node of nodeByPath.values()) {
    const segs = splitPath(node.full)
    if (segs.length === 1) { roots.push(node); continue }
    const parentPath = segs.slice(0, -1).join(' > ')
    const parent = nodeByPath.get(parentPath)
    if (parent) childrenOf(parent).push(node)
  }
  // 章节排序：顶层/子层均按标题编号自然序（1.1 < 1.2 < 1.10）
  roots.sort(_cmpSeg)
  for (const node of nodeByPath.values()) node.children.sort(_cmpSeg)
  // 递归算子孙页总数 = 本体页 + 全部后代 own
  const calc = (node) => {
    let c = node.own
    for (const ch of node.children) c += calc(ch)
    node.pageCount = c
    return c
  }
  for (const r of roots) calc(r)
  return roots
}

const docPages = computed(() => {
  let list = pages.value
  if (docFilter.value) list = list.filter(p => p.doc_id === docFilter.value)
  return list
})

// 顶层：单文档时章节为根；无 doc（全库/直开 page=ID）时先按文档分组
const chapterTree = computed(() => {
  const list = docPages.value
  if (!list.length) return []
  if (docFilter.value) return buildDocTree(list)
  // 按文档名分组，每文档内再建章节树
  const byDoc = new Map()
  for (const p of list) {
    const key = p.doc_id || p.source?.doc || '未知文档'
    if (!byDoc.has(key)) byDoc.set(key, { docId: p.doc_id, name: p.source?.doc || String(key), items: [] })
    byDoc.get(key).items.push(p)
  }
  return [...byDoc.values()].map(g => ({
    key: 'doc-' + (g.docId ?? g.name), title: g.name, full: g.name,
    own: 0, children: buildDocTree(g.items),
  }))
})
const totalPageCount = computed(() => docPages.value.length)

// 展开的节点键：选中路径的所有祖先 + 顶层（doc 分组模式展开文档层）
const expandedKeys = computed(() => {
  const keys = new Set()
  if (!docFilter.value) for (const d of chapterTree.value) keys.add(d.key)
  if (activeFull.value) {
    const segs = splitPath(activeFull.value)
    for (let i = 1; i <= segs.length; i++) keys.add(segs.slice(0, i).join(' > '))
  }
  // 保证首屏能看到内容：展开前 6 个顶层节点
  for (const d of chapterTree.value.slice(0, 6)) keys.add(d.key)
  return [...keys]
})

const activeChapterTitle = computed(() => splitPath(activeFull.value).pop() || '')

// 当前列表：命中搜索词 → 全文档搜；否则列出选中章节下所有子孙页（含本体页）
const currentPages = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (k) {
    return docPages.value.filter(p => (p.title || '').toLowerCase().includes(k))
  }
  if (!activeFull.value) return docPages.value
  return docPages.value.filter(p => {
    const full = (p.title || '').trim()
    return full === activeFull.value || full.startsWith(activeFull.value + ' > ')
  })
})

function onChapterClick(data) {
  keyword.value = ''
  activeFull.value = data.key
  // 若该节点恰是唯一本体页且无子节点 → 直接打开详情（如"1.1 编制依据"单页）
  if (data.own >= 1 && !data.children?.length) {
    const p = docPages.value.find(x => (x.title || '').trim() === data.key)
    if (p) selectPage(p)
  }
}

async function load() {
  loading.value = true
  try {
    // 按 doc 过滤拉取（doc15 全量含超大 content 页，全库一次拉取过重且触发后端超时/500）
    const { data } = await listKnowledgePages(docFilter.value ? { doc_id: docFilter.value } : {})
    pages.value = data
    // 默认选中第一个有内容的顶层章节
    const pick = docPages.value.find(p => p.title)
    if (!isProof.value && pick && !activeFull.value) {
      activeFull.value = splitPath(pick.title).slice(0, 1).join(' > ')
    }
  } finally {
    loading.value = false
  }
}
// doc/pages 变化后，若无选中则选首个顶层（直开 page=ID 的兜底）
watch([docPages], () => {
  if (isProof.value) return
  if (!activeFull.value && docPages.value.length) {
    const p = docPages.value.find(x => (x.title || '').trim())
    if (p) activeFull.value = splitPath(p.title).slice(0, 1).join(' > ')
  }
})

// ---------- 解析校对 ----------
const proof = ref({ source_url: '', doc_name: '' })
const proofMd = ref('')
const proofSaving = ref(false)

async function loadProof() {
  if (!docFilter.value) return
  const { data } = await getParsed(docFilter.value)
  proof.value = data
  proofMd.value = data.markdown || ''
}

async function saveProof() {
  proofSaving.value = true
  try {
    await saveParsed(docFilter.value, proofMd.value)
    ElMessage.success('解析结果已保存，可点击「重新提炼」更新知识页')
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    proofSaving.value = false
  }
}

async function reprocessDoc() {
  try {
    await reprocessKnowledgeDoc(docFilter.value)
    ElMessage.success('已提交重新提炼，稍后在知识库列表查看进度')
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

// ---------- 表格/图片放大预览 ----------
const previewTable = ref(null)
const previewImage = ref(null)
const renderMd = (md) => (md ? marked.parse(md) : '<p class="pv-empty">无表格内容</p>')

function selectPage(p) {
  p.tagsText = (p.tags || []).join(',')
  activePage.value = p
  // 历史原文体积较大，不进列表；选中时从详情接口按需加载
  pageRefText.value = ''
  loadRef(p)
}

async function loadRef(p) {
  refLoading.value = true
  try {
    const { data } = await getKnowledgePage(p.id)
    pageRefText.value = data.ref_text || ''
  } catch {
    pageRefText.value = ''
  } finally {
    refLoading.value = false
  }
}

function showRefDialog() {
  if (!pageRefText.value && !refLoading.value && activePage.value) loadRef(activePage.value)
  refDialog.value = true
}

async function savePage() {
  const p = activePage.value
  if (!p) return
  p.tags = (p.tagsText || '').split(/[,，]/).map(s => s.trim()).filter(Boolean)
  try {
    await updateKnowledgePage(p.id, {
      title: p.title, tags: p.tags, method: p.method,
      key_points: p.key_points, tables: p.tables, images: p.images, usage: p.usage,
    })
    ElMessage.success('知识页修订已保存')
    await load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  }
}

// 校对视图初始化
onMounted(async () => {
  await load()
  if (isProof.value) loadProof()
  // 支持 /knowledge/pages?page=ID 直接打开某知识页详情（如工作台思路引用跳过来）
  const pid = Number(route.query.page)
  if (pid) {
    const p = pages.value.find(x => x.id === pid)
    if (p) {
      // 把树定位到该页所属章节，再选中该页（列表/详情联动）
      const segs = splitPath(p.title)
      if (segs.length) activeFull.value = segs[0]  // 首段即顶层章节
      selectPage(p)
    }
  }
})

</script>

<style scoped>
.kp { height: 100%; display: flex; flex-direction: column; padding: 24px 28px; }

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

/* 校对视图 */
.proof-body { flex: 1; display: flex; min-height: 0; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-m); overflow: hidden; }
.proof-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.proof-col-head { padding: 11px 16px; font-size: 12px; font-weight: 700; color: var(--text-2); border-bottom: 1px solid var(--line); background: var(--paper-warm); display: flex; align-items: center; justify-content: space-between; }
.proof-actions { display: flex; gap: 6px; }
.proof-iframe { flex: 1; border: none; background: #525659; }
.proof-nofile { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--text-3); font-size: 13px; }
.proof-editor { flex: 1; display: flex; }
.proof-editor :deep(textarea) { flex: 1; height: 100% !important; border: none; border-radius: 0; padding: 16px; line-height: 1.8; font-family: var(--font-num); font-size: 12px; }
.proof-divider { width: 1px; background: var(--line); }

/* 三栏主体 */
.kp-body { flex: 1; display: flex; min-height: 0; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-m); overflow: hidden; }

.type-panel { width: 240px; flex-shrink: 0; border-right: 1px solid var(--line); display: flex; flex-direction: column; background: var(--paper-warm); }
.type-head { padding: 10px 12px; font-size: 11px; font-weight: 700; color: var(--text-3); letter-spacing: 0.1em; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; }
.type-total { font-weight: 600; letter-spacing: 0; }
.type-list { flex: 1; overflow-y: auto; padding: 6px; }
.type-empty { padding: 24px 12px; text-align: center; font-size: 12px; color: var(--text-3); }
/* 章节树 */
.kp-tree { background: transparent; --el-tree-node-hover-bg-color: transparent; }
.kp-tree :deep(.el-tree-node__content) { height: 26px; border-radius: var(--radius-s); }
.kp-tree :deep(.el-tree-node__content:hover) { background: var(--card); }
.kp-tree :deep(.el-tree-node.is-current > .el-tree-node__content) { background: var(--brand-soft); }
.kp-tree :deep(.el-tree-node__expand-icon) { font-size: 11px; }
.kp-tree-node { flex: 1; display: flex; align-items: center; gap: 6px; min-width: 0; padding-right: 6px; }
.kt-label { font-size: 12px; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kp-tree :deep(.el-tree-node.is-current > .el-tree-node__content) .kt-label { color: var(--brand); font-weight: 600; }
.kt-count { font-size: 10px; color: var(--text-3); background: var(--card); border-radius: 8px; padding: 0 6px; flex-shrink: 0; }
.kp-tree :deep(.el-tree-node.is-current > .el-tree-node__content) .kt-count { background: #fff; color: var(--brand); }

.list-panel { width: 280px; flex-shrink: 0; border-right: 1px solid var(--line); display: flex; flex-direction: column; }
.list-head { padding: 12px 14px 8px; font-size: 11px; font-weight: 700; color: var(--text-3); letter-spacing: 0.1em; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; }
.list-count { color: var(--text-3); }
.list-search { padding: 8px 10px; border-bottom: 1px solid var(--line); }
/* 中栏列表滚动容器：head/search 固定，列表独占剩余高度可滚动 */
.list-scroll { flex: 1; overflow-y: auto; min-height: 0; }
.list-empty { padding: 30px 16px; text-align: center; font-size: 12px; color: var(--text-2); }
.list-empty-sub { font-size: 11px; color: var(--text-3); }
.page-card { padding: 12px 14px; border-bottom: 1px solid var(--line); cursor: pointer; transition: background 0.12s; }
.page-card:hover { background: var(--paper-warm); }
.page-card.on { background: var(--brand-soft); border-left: 3px solid var(--brand); }
.page-card-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.page-card-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.pc-tag { font-size: 10px; padding: 1px 7px; border-radius: 3px; background: var(--paper-warm); color: var(--text-2); }
.page-card.on .pc-tag { background: #fff; }
.page-card-src { font-size: 10px; color: var(--text-3); }

.detail-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.detail-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-3); }
.de-icon { font-size: 28px; color: var(--line-strong); margin-bottom: 10px; }
.detail-body { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.detail-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--line); }
.detail-label { font-size: 12px; font-weight: 700; color: var(--text-2); }
.detail-scroll { flex: 1; overflow-y: auto; padding: 16px 20px 32px; }
.field { margin-bottom: 18px; }
.field label { display: block; font-size: 12px; font-weight: 600; color: var(--text-2); margin-bottom: 7px; }
.field-readonly { font-size: 12px; color: var(--text-3); padding: 8px 10px; background: var(--paper-warm); border-radius: var(--radius-s); }
.field-empty { font-size: 12px; color: var(--text-3); }
.np-tag { font-weight: 400; font-size: 10px; color: #8a5a12; background: #fff3d6; border: 1px solid #eedcae; padding: 0 5px; margin-left: 6px; border-radius: 3px; vertical-align: 1px; }
.ref-label { display: flex; align-items: baseline; gap: 8px; }
.ref-hint { font-weight: 400; font-size: 11px; color: #8a5a12; }
.ref-dlg-hint { font-size: 12px; line-height: 1.8; color: #7a5a1e; background: #fff7e0; border: 1px solid #eee0b8; border-radius: var(--radius-s); padding: 8px 12px; margin-bottom: 10px; }
.ref-dlg-pre { margin: 0; padding: 14px 16px; max-height: 62vh; overflow-y: auto; font-size: 13px; line-height: 1.85; color: var(--text); white-space: pre-wrap; word-break: break-word; font-family: var(--font-num); background: var(--paper-warm); border: 1px solid var(--line); border-radius: var(--radius-s); }
.ref-dlg-body { max-height: 62vh; overflow: auto; padding: 12px 16px; background: var(--paper-warm); border: 1px solid var(--line); border-radius: var(--radius-s); font-size: 13px; line-height: 1.85; color: var(--text); }
.ref-dlg-body :deep(p) { margin: 0 0 8px; }
.ref-dlg-body :deep(h1), .ref-dlg-body :deep(h2), .ref-dlg-body :deep(h3), .ref-dlg-body :deep(h4) { font-size: 14px; margin: 12px 0 6px; }
.ref-dlg-body :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.ref-dlg-body :deep(th), .ref-dlg-body :deep(td) { border: 1px solid var(--line-strong); padding: 5px 9px; font-size: 12px; }
.ref-dlg-body :deep(th) { background: #eef2f8; font-weight: 600; white-space: nowrap; }
.kp-item { display: flex; gap: 6px; margin-bottom: 6px; align-items: center; }
.kp-del { border: none; background: none; color: var(--text-3); font-size: 15px; cursor: pointer; padding: 2px 6px; }
.kp-del:hover { color: var(--red); }
.add-btn { border: 1px dashed var(--line-strong); background: none; color: var(--brand); font-size: 12px; padding: 5px 12px; border-radius: var(--radius-s); cursor: pointer; }
.add-btn:hover { border-color: var(--brand); }
.img-grid { display: flex; gap: 10px; flex-wrap: wrap; }
.img-card { width: 150px; border: 1px solid var(--line); border-radius: var(--radius-s); overflow: hidden; }
.img-thumb { height: 70px; background: var(--paper-warm); display: flex; align-items: center; justify-content: center; font-size: 24px; }
.img-cap { font-size: 11px; color: var(--text); padding: 5px 8px 2px; text-align: center; }
.img-usage { font-size: 10px; color: var(--text-3); padding: 2px 8px 6px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.table-chip { display: inline-block; font-size: 12px; padding: 5px 10px; background: var(--paper-warm); border-radius: var(--radius-s); color: var(--text-2); margin-right: 6px; margin-bottom: 6px; }
.table-chip.clickable { cursor: pointer; border: 1px solid transparent; transition: all 0.12s; }
.table-chip.clickable:hover { border-color: var(--brand); color: var(--brand); background: var(--brand-soft); }
.chip-extra { color: var(--text-3); font-size: 10px; }
.img-card.clickable { cursor: pointer; transition: all 0.12s; }
.img-card.clickable:hover { border-color: var(--brand); box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.img-real { width: 100%; height: 70px; object-fit: cover; display: block; background: var(--paper-warm); }

/* 预览弹窗 */
.pv-desc { font-size: 13px; color: var(--text); background: var(--brand-soft); border: 1px solid #d8e4f8; border-radius: var(--radius-s); padding: 10px 14px; line-height: 1.7; margin-bottom: 12px; }
.pv-columns { margin-bottom: 12px; }
.pv-columns-title { font-size: 12px; font-weight: 700; color: var(--text-2); margin-bottom: 6px; }
.pv-col-item { font-size: 12px; color: var(--text-2); line-height: 1.8; }
.pv-col-item b { color: var(--brand); }
.pv-table { max-height: 420px; overflow: auto; border: 1px solid var(--line); border-radius: var(--radius-s); padding: 4px; }
.pv-table :deep(table) { border-collapse: collapse; width: 100%; }
.pv-table :deep(th), .pv-table :deep(td) { border: 1px solid var(--line-strong); padding: 6px 10px; font-size: 12px; white-space: nowrap; }
.pv-table :deep(th) { background: var(--paper-warm); font-weight: 600; }
.pv-img { width: 100%; border: 1px solid var(--line); border-radius: var(--radius-s); margin-bottom: 12px; }
.pv-img-empty { text-align: center; color: var(--text-3); font-size: 13px; padding: 40px 0; background: var(--paper-warm); border-radius: var(--radius-s); margin-bottom: 12px; }
.pv-field { font-size: 13px; color: var(--text-2); line-height: 1.8; margin-bottom: 6px; }
.pv-label { font-weight: 700; color: var(--text); }
</style>
