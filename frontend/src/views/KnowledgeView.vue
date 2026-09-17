<template>
  <div class="knowledge">
    <!-- 页头 -->
    <header class="page-head">
      <div class="head-left">
        <h1 class="page-title">知识库</h1>
        <span class="page-sub">历史施组 · 工艺工法，两类知识分离管理</span>
      </div>
      <div class="head-tools">
        <el-input v-model="keyword" placeholder="搜索文档…" clearable class="search">
          <template #prefix>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          </template>
        </el-input>
        <el-button type="primary" @click="showUpload = true">
          <span class="plus">＋</span> 上传文档
        </el-button>
      </div>
    </header>

    <!-- 两类库 Tab -->
    <div class="kb-tabs">
      <button
        v-for="t in TABS" :key="t.key"
        class="kb-tab" :class="{ on: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        {{ t.label }}
        <span class="kb-tab-count num">{{ countByTab(t.key) }}</span>
      </button>
    </div>

    <!-- 说明条 -->
    <div class="kb-tip">
      <span class="kb-tip-dot" />
      {{ activeTab === 'sod' ? '历史施组库：存历史项目实施性施组，用于提炼「这类章节怎么写」的知识页（写法参考）' : '工艺工法库：存施工工艺/工法标准做法条目，用于「施工方案」章各专业子节的施工方法素材' }}
    </div>

    <!-- 文档列表 -->
    <main class="kb-table-wrap">
      <el-table :data="filteredDocs" class="kb-table" :empty-text="'暂无文档，点击右上角上传'">
        <el-table-column label="文件名" min-width="260">
          <template #default="{ row }">
            <div class="doc-cell">
              <span class="doc-icon" :class="row.kind">{{ fileIcon(row.file_name) }}</span>
              <span class="doc-name" :title="row.file_name">{{ row.file_name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="解析状态" width="180">
          <template #default="{ row }">
            <template v-if="row.status === 'parsing' && taskProgress[row.id]">
              <div class="row-progress">
                <div class="rp-head">
                  <span class="status-badge" :class="row.status"><span class="status-dot" />解析中</span>
                  <span class="rp-pct num">{{ taskProgress[row.id].progress }}%</span>
                </div>
                <div class="rp-bar"><div class="rp-fill" :style="{ width: taskProgress[row.id].progress + '%' }" /></div>
                <div class="rp-detail">{{ taskProgress[row.id].detail }}</div>
              </div>
            </template>
            <span v-else class="status-badge" :class="row.status">
              <span class="status-dot" />
              {{ STATUS_MAP[row.status] }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="activeTab === 'method' ? '概况' : '知识页'" width="100">
          <template #default="{ row }">
            <span class="num page-count">{{ row.pageCount }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="150">
          <template #default="{ row }">
            <span class="doc-time num">{{ fmtTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="290" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" size="small" @click="viewEntries(row)">
                {{ row.kind === 'method' ? '查看工法' : '查看知识页' }}
              </el-button>
              <el-button link size="small" @click="proofread(row)">解析校对</el-button>
              <el-button v-if="row.status === 'failed'" link type="warning" size="small" @click="reprocess(row)">重新提炼</el-button>
              <el-button link type="danger" size="small" @click="removeDoc(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </main>

    <!-- 上传对话框 -->
    <el-dialog v-model="showUpload" width="560" :show-close="false" class="kb-dialog">
      <template #header>
        <div class="dlg-head">
          <span class="dlg-title">上传文档到{{ activeTab === 'sod' ? '历史施组库' : '工艺工法库' }}</span>
          <button class="dlg-close" @click="showUpload = false">✕</button>
        </div>
      </template>
      <el-upload
        drag multiple
        :auto-upload="false"
        accept=".pdf,.docx"
        :on-change="onFileChange"
        class="kb-upload"
      >
        <div class="upload-hint">
          <div class="upload-icon">⇪</div>
          <p class="upload-text">拖拽文件到此处，或点击选择</p>
          <p class="upload-sub">支持 .pdf / .docx，可批量上传</p>
        </div>
      </el-upload>
      <!-- 上传进度（仅上传阶段短暂展示：全部传完自动关弹窗，解析进度看列表行内） -->
      <div v-if="jobs.length" class="upload-progress">
        <div v-for="(j, i) in jobs" :key="i" class="job-row">
          <div class="up-head">
            <span class="job-name" :title="j.name">{{ j.name }}</span>
            <span class="num job-pct" :class="j.phase">{{ j.phase === 'done' ? '✓' : j.pct + '%' }}</span>
          </div>
          <div class="up-bar"><div class="up-fill" :class="j.phase" :style="{ width: (j.phase === 'done' ? 100 : j.pct) + '%' }" /></div>
          <div class="up-detail">{{ j.detail }}</div>
        </div>
      </div>
      <div v-if="uploadError" class="upload-error">{{ uploadError }}</div>
      <template #footer>
        <el-button @click="closeUpload">关闭</el-button>
        <el-button v-if="!jobs.length" type="primary" :loading="uploading" @click="doUpload">开始上传并解析</el-button>
        <el-button v-else-if="jobs.some(j => j.phase === 'failed')" type="primary" @click="doUpload">重试上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { listKnowledgeDocs, uploadKnowledgeDoc, deleteKnowledgeDoc, reprocessKnowledgeDoc, knowledgeTaskStatus } from '../api'

const router = useRouter()
const route = useRoute()
const keyword = ref('')
const activeTab = ref('sod')
const showUpload = ref(false)
const uploading = ref(false)
const uploadList = ref([])  // 存 raw File

const TABS = [
  { key: 'sod', label: '历史施组库' },
  { key: 'method', label: '工艺工法库' },
]
const STATUS_MAP = { pending: '待解析', parsing: '解析中', parsed: '已解析', failed: '解析失败' }

const docs = ref([])

const filteredDocs = computed(() => {
  let list = docs.value.filter(d => d.kind === activeTab.value)
  if (keyword.value) list = list.filter(d => d.file_name.includes(keyword.value))
  return list
})
const countByTab = (key) => docs.value.filter(d => d.kind === key).length

const fileIcon = (name) => (/\.pdf$/i.test(name) ? 'PDF' : 'DOC')
const fmtTime = (t) => (t ? t.slice(5, 16).replace('T', ' ') : '')

async function load() {
  const { data } = await listKnowledgeDocs()
  docs.value = data
}

// 跳子页时携带来源 tab，返回后恢复到进入前的库
function viewEntries(row) {
  if (row.kind === 'method') router.push({ path: '/knowledge/methods', query: { doc: row.id, from: row.kind } })
  else router.push({ path: '/knowledge/pages', query: { doc: row.id, from: row.kind } })
}
function proofread(row) {
  router.push({ path: '/knowledge/pages', query: { doc: row.id, view: 'proof', from: row.kind } })
}
// 从 URL ?tab= 恢复激活库（返回按钮带参 / 手动刷新保持）
function applyTabQuery() {
  const t = route.query.tab
  if (t === 'sod' || t === 'method') activeTab.value = t
}
watch(() => route.query.tab, applyTabQuery)
async function removeDoc(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.file_name}」？`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await deleteKnowledgeDoc(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) { /* 用户取消或错误 */ }
}

async function reprocess(row) {
  try {
    const { data } = await reprocessKnowledgeDoc(row.id)
    ElMessage.success('已重新提交提炼')
    await load()
    if (data.task_id) startPolling([data.task_id])
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

function onFileChange(file) {
  uploadList.value.push(file.raw)
}
const uploadPct = ref(0)  // 上传进度百分比
// 上传进度：{ name, phase: uploading|done|failed, pct, detail, taskId }
const jobs = ref([])
const uploadError = ref('')
const jobsUploading = computed(() => jobs.value.some(j => j.phase === 'uploading'))

function closeUpload() {
  showUpload.value = false
  uploadList.value = []
  jobs.value = []
  uploadError.value = ''
}

async function doUpload() {
  if (!uploadList.value.length) return ElMessage.warning('请先选择文件')
  uploading.value = true
  uploadError.value = ''
  const taskIds = []
  let hasFail = false
  // 全部成功 → 关弹窗，进度交给列表行内异步解析轮询；有失败 → 弹窗停留显示错误可重试
  try {
    for (const f of [...uploadList.value]) {
      const job = { name: f.name, phase: 'uploading', pct: 0, detail: '上传中…', taskId: null }
      jobs.value.push(job)
      try {
        const { data } = await uploadKnowledgeDoc(f, activeTab.value, (p) => {
          job.pct = p
          job.detail = `上传中 ${p}%`
        })
        job.phase = 'done'
        job.pct = 100
        job.detail = '已提交解析'
        if (data.task_id) taskIds.push(data.task_id)
      } catch (e) {
        job.phase = 'failed'
        job.detail = '上传失败：' + (e?.response?.data?.detail || e.message)
        hasFail = true
      }
    }
    uploadList.value = []
    if (taskIds.length) startPolling(taskIds)
    await load()
    if (!hasFail) {
      ElMessage.success(`已上传 ${jobs.value.length} 个文件，解析提炼进行中（列表查看进度）`)
      setTimeout(() => { showUpload.value = false; jobs.value = [] }, 1200)  // 稍作停留展示✓后自动关
    } else {
      ElMessage.error('部分文件上传失败，请在弹窗内重试')
    }
  } finally {
    uploading.value = false
  }
}

// 提炼任务轮询：有任务在跑时每 3s 刷新列表与进度，全部结束停止
let pollTimer = null
const runningTasks = new Set()
const taskProgress = ref({})  // taskId -> { progress, detail }
function startPolling(taskIds = []) {
  taskIds.forEach(id => runningTasks.add(id))
  if (pollTimer || !runningTasks.size) return
  pollTimer = setInterval(async () => {
    await load()
    let anyDone = false
    for (const id of [...runningTasks]) {
      try {
        const { data } = await knowledgeTaskStatus(id)
        // data.ref_id 即文档 id，按文档维度记录进度供列表行展示
        taskProgress.value = { ...taskProgress.value, [data.ref_id]: { progress: data.progress, detail: data.detail } }
        if (data.status === 'success' || data.status === 'failed') {
          runningTasks.delete(id)
          anyDone = true
        }
      } catch { runningTasks.delete(id) }
    }
    if (anyDone && !runningTasks.size && pollTimer) {
      clearInterval(pollTimer); pollTimer = null
      await load()
      ElMessage.success('解析提炼完成')
      setTimeout(() => { taskProgress.value = {} }, 4000)
    }
  }, 3000)
}

onMounted(async () => {
  applyTabQuery()
  await load()
  await resumeRunningTasks()
})

// 页面切走再回来：从后端恢复仍在运行的提炼任务的行内进度轮询
async function resumeRunningTasks() {
  try {
    const { data } = await api.get('/knowledge/tasks/running')
    if (!data.length) return
    const ids = []
    for (const t of data) {
      taskProgress.value = { ...taskProgress.value, [t.ref_id]: { progress: t.progress, detail: t.detail } }
      ids.push(t.task_id)
    }
    startPolling(ids)
  } catch { /* 忽略 */ }
}

onUnmounted(() => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
})
</script>

<style scoped>
.knowledge { height: 100%; display: flex; flex-direction: column; padding: 28px 32px; }

.page-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; gap: 16px; }
.head-left { display: flex; align-items: baseline; gap: 14px; }
.page-title { font-size: 20px; font-weight: 700; margin: 0; }
.page-sub { font-size: 13px; color: var(--text-3); }
.head-tools { display: flex; align-items: center; gap: 10px; }
.search { width: 200px; }
.plus { font-weight: 400; margin-right: 2px; }

.kb-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); }
.kb-tab {
  display: flex; align-items: center; gap: 7px; padding: 10px 16px; border: none; background: none;
  font-size: 15px; color: var(--text-2); cursor: pointer; position: relative; transition: color 0.15s;
}
.kb-tab:hover { color: var(--text); }
.kb-tab.on { color: var(--brand); font-weight: 600; }
.kb-tab.on::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: var(--brand);
}
.kb-tab-count {
  font-size: 12px; padding: 1px 7px; border-radius: 999px; background: var(--paper-warm); color: var(--text-3);
}
.kb-tab.on .kb-tab-count { background: var(--brand-soft); color: var(--brand); }

.kb-tip {
  display: flex; align-items: center; gap: 8px; margin: 14px 0; padding: 9px 14px;
  background: var(--brand-soft); border: 1px solid #d8e4f8; border-radius: var(--radius-s);
  font-size: 13px; color: #33518f;
}
.kb-tip-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); flex-shrink: 0; }

.kb-table-wrap { flex: 1; overflow: hidden; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-m); }
.kb-table { width: 100%; }
.kb-table :deep(.el-table__cell) { font-size: 15px; }   /* 表体字号上调（Element 默认 14px） */
.doc-cell { display: flex; align-items: center; gap: 10px; }
.doc-icon {
  width: 28px; height: 28px; flex-shrink: 0; border-radius: 6px; font-size: 10px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; font-family: var(--font-num);
}
.doc-icon.PDF { background: #fdeaea; color: #c0392b; }
.doc-icon.DOC { background: #e8eefa; color: var(--brand); }
.doc-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.status-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-2); }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-3); }
.status-badge.parsed .status-dot { background: var(--green); }
.status-badge.parsing .status-dot { background: var(--amber); }
.status-badge.pending .status-dot { background: var(--amber); }
.status-badge.failed .status-dot { background: var(--red); }
.status-badge.failed { color: var(--red); }

.page-count { color: var(--text-2); }
.doc-time { font-size: 13px; color: var(--text-3); }
.row-actions { display: flex; gap: 4px; }

/* 解析进度（列表行内） */
.row-progress { display: flex; flex-direction: column; gap: 4px; }
.rp-head { display: flex; align-items: center; justify-content: space-between; }
.rp-pct { font-size: 12px; color: var(--amber); font-weight: 700; }
.rp-bar { height: 3px; background: var(--paper-warm); border-radius: 2px; overflow: hidden; }
.rp-fill { height: 100%; background: linear-gradient(90deg, var(--brand), var(--cyan)); transition: width 0.4s ease-out; }
.rp-detail { font-size: 11px; color: var(--text-3); max-width: 150px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 上传/解析进度（逐文件） */
.upload-progress { margin-top: 12px; padding: 10px 12px; background: var(--paper-warm); border-radius: var(--radius-s); max-height: 260px; overflow-y: auto; }
.job-row { margin-bottom: 10px; }
.job-row:last-child { margin-bottom: 0; }
.up-head { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--text-2); margin-bottom: 4px; }
.job-name { max-width: 65%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-pct.done { color: var(--green); font-weight: 700; }
.job-pct.failed { color: var(--red); font-weight: 700; }
.up-bar { height: 4px; background: var(--line); border-radius: 2px; overflow: hidden; }
.up-fill { height: 100%; background: var(--brand); transition: width 0.2s ease-out; }
.up-fill.done { background: var(--green); }
.up-fill.failed { background: var(--red); }
.up-detail { font-size: 11px; color: var(--text-3); margin-top: 2px; }
.upload-error { margin-top: 10px; padding: 8px 12px; font-size: 13px; color: var(--red); background: #fdf2f2; border: 1px solid #f5c6c6; border-radius: var(--radius-s); }

.dlg-head { display: flex; align-items: center; justify-content: space-between; }
.dlg-title { font-size: 15px; font-weight: 700; }
.dlg-close { border: none; background: none; font-size: 14px; color: var(--text-3); cursor: pointer; padding: 4px 6px; border-radius: 4px; }
.dlg-close:hover { background: var(--paper-warm); color: var(--text); }
.kb-upload :deep(.el-upload-dragger) { padding: 30px 0; }
.upload-hint { text-align: center; }
.upload-icon { font-size: 30px; color: var(--brand); margin-bottom: 6px; }
.upload-text { font-size: 14px; color: var(--text-2); margin: 0 0 4px; }
.upload-sub { font-size: 12px; color: var(--text-3); margin: 0; }
</style>
