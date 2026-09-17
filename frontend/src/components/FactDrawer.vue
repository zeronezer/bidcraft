<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="(v) => emit('update:modelValue', v)"
    direction="rtl"
    size="min(1180px, 88vw)"
    :with-header="false"
    class="fact-drawer"
  >
    <div class="fd-wrap">
      <!-- 头部 -->
      <div class="fd-head">
        <div>
          <div class="fd-title">全局参数</div>
          <div class="fd-sub">AI 从项目文件抽取后即生效，可直接编辑修正</div>
        </div>
        <button class="fd-close" @click="emit('update:modelValue', false)">✕</button>
      </div>

      <!-- 提示条 -->
      <div class="fd-notice">
        <span class="fd-notice-icon">!</span>
        <span>事实由 AI 从项目文件抽取，<b>抽取即生效</b>（无需确认，可编辑或删除）。</span>
      </div>

      <!-- 搜索 + 类别筛选 + 一键确认 -->
      <div class="fd-tools">
        <el-input v-model="keyword" size="small" placeholder="搜索事实（工期/人数/名称…）" clearable class="fd-search">
          <template #prefix><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg></template>
        </el-input>
        <div class="fd-chips">
          <button v-for="c in ['全部', ...CATEGORIES]" :key="c" class="fd-chip"
            :class="{ on: catFilter === c }" @click="catFilter = c">{{ c }}</button>
        </div>
        <!-- 按标段范围筛选 -->
        <div class="fd-chips">
          <button v-for="s in SCOPE_FILTERS" :key="s" class="fd-chip scope"
            :class="{ on: scopeFilter === s }" @click="scopeFilter = s">{{ s }}</button>
        </div>
        <div class="fd-batch-row">
          <span class="fd-batch-info">共 <b class="num">{{ facts.length }}</b> 条事实</span>
          <!-- 手动按本标段重提取：档案已建好、只想把全局参数按当前标段重刷一遍时用
               （档案本身不动；要连档案一起重抽走「标段档案 → 重新抽取」） -->
          <button class="fd-re-btn" :disabled="extracting" :title="extractMsg || '按当前选定标段重新提取全局参数'" @click="doReextract">
            {{ extracting ? (extractMsg || '提取中…') : '按本标段重新提取' }}
          </button>
        </div>
      </div>

      <!-- 统计 -->
      <div class="fd-stats">
        <div class="fd-stat"><span class="num">{{ filteredFacts.length }}</span><label>当前显示</label></div>
        <div class="fd-stat"><span class="num">{{ facts.length }}</span><label>总数</label></div>
        <div v-if="lowConfCount" class="fd-stat warn"><span class="num">{{ lowConfCount }}</span><label>低置信</label></div>
      </div>

      <!-- 事实列表 -->
      <div class="fd-list">
        <div v-if="!filteredFacts.length" class="req-empty">
          无匹配事实<br><span>{{ keyword ? '换个关键词试试' : '暂无事实，上传招标文件后 AI 自动抽取' }}</span>
        </div>
        <div v-for="f in filteredFacts" :key="f.id" class="fact-item" :class="f.status">
          <div class="fact-main">
            <div class="fact-row1">
              <span class="fact-key">{{ f.fact_key }}</span>
              <span class="fact-cat">{{ f.category }}</span>
              <span v-if="lotLabel(f)" class="fact-tag lot" :title="'该事实适用于：' + lotLabel(f)">{{ lotLabel(f) }}</span>
              <span v-if="f.status === 'expired'" class="fact-tag expired">已过期</span>
            </div>
            <div class="fact-row2">
              <span class="fact-value num">{{ f.fact_value }}<span v-if="f.unit" class="fact-unit">{{ f.unit }}</span></span>
              <span v-if="isLowConf(f)" class="fact-tag lowconf"
                title="AI 从上下文推断得来，可能有误差，建议核对后再依赖">低置信 {{ Math.round(f.confidence * 100) }}%</span>
            </div>
            <div class="fact-src">出处：{{ f.source_location }}</div>
          </div>
          <div class="fact-actions">
            <button class="fa-btn" @click="act(f, 'edit')">编辑</button>
            <button class="fa-btn danger" @click="act(f, 'delete')">删除</button>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listFacts, confirmFact, confirmAllFacts, updateFact, deleteFact, reextractFacts, taskStatus } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const CATEGORIES = ['工期', '人员', '机械', '造价', '地质', '结构', '质量标准', '安全目标', '其他']
const catFilter = ref('全部')
// 标段范围筛选（与后端 scope 对应；"全线"含 all 与 background 两类）
const SCOPE_FILTERS = ['全部范围', '本标段', '全线', '归属未判定']
const scopeFilter = ref('全部范围')
const isLowConf = (f) => typeof f.confidence === 'number' && f.confidence < 0.8
const lowConfCount = computed(() => facts.value.filter(isLowConf).length)
const keyword = ref('')
const facts = ref([])
const loading = ref(false)
const extracting = ref(false)   // "按本标段重新提取"进行中
const extractMsg = ref('')      // 该任务的实时进度文案

async function load() {
  if (!props.projectId) return
  loading.value = true
  try {
    const { data: f } = await listFacts(props.projectId)
    facts.value = f
  } finally {
    loading.value = false
  }
}

// 按当前选定标段重新提取全局参数（后台任务 + 轮询）。
// **只重提取事实，不动标段档案**——档案已建好、只想把全局参数按标段重刷时用这个；
// 要连档案一起重抽，走工作台的「标段档案 → 重新抽取」。
async function doReextract() {
  if (extracting.value) return
  extracting.value = true
  extractMsg.value = '正在按本标段重新提取…'
  try {
    const { data } = await reextractFacts(props.projectId)
    if (data.task_id) {
      for (let i = 0; i < 400; i++) {
        const { data: t } = await taskStatus(data.task_id)
        if (t.status === 'success') break
        if (t.status === 'failed') {
          // task.detail 是"异常 + traceback"，界面只取第一行的人话
          const raw = String(t.detail || '').split('\n')[0].split('Traceback')[0].trim()
          throw new Error(raw.replace(/[:：]\s*$/, '').slice(0, 200) || '重新提取失败')
        }
        extractMsg.value = (t.detail || '正在按本标段重新提取…').replace(/…$/, '')
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    await load()
    ElMessage.success('全局参数已按本标段重新提取')
    emit('changed')   // 通知父组件（顶栏待确认数等）刷新
  } catch (e) {
    ElMessage.error('重新提取失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    extracting.value = false
    extractMsg.value = ''
  }
}

const filteredFacts = computed(() => {
  let list = facts.value
  if (catFilter.value !== '全部') list = list.filter(f => f.category === catFilter.value)
  if (scopeFilter.value === '本标段') list = list.filter(f => f.scope === 'mine')
  else if (scopeFilter.value === '全线') list = list.filter(f => f.scope === 'all' || f.scope === 'background')
  else if (scopeFilter.value === '归属未判定') list = list.filter(f => f.scope === 'unknown')
  if (keyword.value.trim()) {
    const k = keyword.value.trim().toLowerCase()
    list = list.filter(f =>
      (f.fact_key || '').toLowerCase().includes(k) ||
      (f.fact_value || '').toLowerCase().includes(k) ||
      (f.source_location || '').toLowerCase().includes(k))
  }
  return list
})
// 一键批量确认全部待确认事实
const batchConfirming = ref(false)
async function confirmAll() {
  try {
    await ElMessageBox.confirm(
      `将 ${pendingCount.value} 条待确认事实全部置为「已确认」并可用于正文生成。低置信度条目建议先人工核对，确定继续？`,
      '批量确认事实', { type: 'warning', confirmButtonText: '全部确认', cancelButtonText: '取消' },
    )
    batchConfirming.value = true
    const { data } = await confirmAllFacts(props.projectId)
    ElMessage.success(`已批量确认 ${data.confirmed} 条事实`)
    await load()
    emit('changed')
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error('批量确认失败：' + (e?.response?.data?.detail || e.message))
    }
  } finally {
    batchConfirming.value = false
  }
}
// 适用标段标签：all/未标注 → 不显示（全线通用）；多标段显示前两个 + N
// 标段归属标签（后端已按标段过滤，只会有 本标段/全线/未判定 三类）
// 注："全线通用(all)" 与 "全线背景(background)" 合并显示为「全线」——两者区分过于专业、
// 用户反馈难理解；底层仍保留区分（生成时"全线背景"不会被写成"本标段…"）。
function lotLabel(f) {
  const s = f.scope
  if (s === 'mine') return '本标段'
  if (s === 'all' || s === 'background') return '全线'
  if (s === 'unknown') return '归属未判定'
  let lots = f.applicable_lots
  if (typeof lots === 'string') { try { lots = JSON.parse(lots) } catch { lots = null } }
  if (!Array.isArray(lots) || !lots.length) return ''
  return lots.length <= 2 ? lots.join('、') : `${lots[0]} 等${lots.length}段`
}
const pendingCount = computed(() => facts.value.filter(f => f.status === 'pending').length)
const confirmedCount = computed(() => facts.value.filter(f => f.status === 'confirmed').length)

async function act(f, action) {
  try {
    if (action === 'delete') {
      await ElMessageBox.confirm(
        `确定删除事实「${f.fact_key}」？删除后该条不再进入正文生成（如需恢复可重新解析文件）。`,
        '删除事实', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
      await deleteFact(f.id)
      ElMessage.success(`已删除「${f.fact_key}」`)
    } else if (action === 'confirm') {
      await confirmFact(f.id)
      ElMessage.success(`已确认「${f.fact_key}」`)
    } else {
      const { value } = await ElMessageBox.prompt(`修改「${f.fact_key}」的值`, '修正事实', {
        inputValue: f.fact_value, confirmButtonText: '保存', cancelButtonText: '取消',
      })
      if (value && value.trim()) {
        await updateFact(f.id, { fact_value: value.trim() })
        ElMessage.success('已修正')
      }
    }
    await load()
    emit('changed')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

watch(() => props.modelValue, (v) => { if (v) load() })
onMounted(() => { if (props.modelValue) load() })
</script>

<style scoped>
.fd-wrap { display: flex; flex-direction: column; height: 100%; background: var(--paper); }

.fd-head { display: flex; align-items: flex-start; justify-content: space-between; padding: 18px 20px 14px; background: var(--card); border-bottom: 1px solid var(--line); }
.fd-title { font-size: 15px; font-weight: 700; }
.fd-sub { font-size: 11px; color: var(--text-3); margin-top: 3px; }
.fd-close { border: none; background: none; font-size: 14px; color: var(--text-3); cursor: pointer; padding: 4px 6px; border-radius: 4px; }
.fd-close:hover { background: var(--paper-warm); color: var(--text); }

.req-content { font-size: 12px; color: var(--text-2); line-height: 1.6; margin: 4px 0; }
.req-empty { padding: 40px 0; text-align: center; font-size: 13px; color: var(--text-2); line-height: 1.8; }
.req-empty span { font-size: 11px; color: var(--text-3); }

.fd-notice {
  display: flex; align-items: center; gap: 8px; margin: 12px 16px 0; padding: 9px 12px;
  background: var(--amber-soft); border: 1px solid #f0d9ac; border-radius: var(--radius-s);
  font-size: 12px; color: #8a5a12;
}
.fd-notice-icon {
  width: 16px; height: 16px; border-radius: 50%; background: var(--amber); color: #fff;
  font-size: 11px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}

/* 工具区：搜索 + 类别筛选 + 一键确认 */
.fd-tools { display: flex; flex-direction: column; gap: 8px; padding: 12px 16px 0; }
.fd-search { width: 100%; }
.fd-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.fd-chip {
  padding: 4px 11px; border-radius: 999px; border: 1px solid var(--line-strong);
  background: var(--card); font-size: 12px; color: var(--text-2); cursor: pointer; transition: all 0.15s;
}
.fd-chip:hover { border-color: var(--brand); color: var(--brand); }
.fd-chip.on { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 600; }
.fd-batch-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.fd-batch-info { font-size: 11px; color: var(--text-3); }
.fd-batch-info b { color: var(--amber); }
/* 按本标段重新提取（次要动作，不抢"一键确认"的视觉重心） */
.fd-re-btn {
  border: 1px solid var(--line-strong); background: none; color: var(--text-2);
  font-size: 11px; padding: 3px 10px; border-radius: 999px; cursor: pointer; white-space: nowrap;
  max-width: 260px; overflow: hidden; text-overflow: ellipsis;
}
.fd-re-btn:hover:not(:disabled) { color: var(--brand); border-color: var(--brand); }
.fd-re-btn:disabled { cursor: default; color: var(--text-3); }
.fd-batch-btn {
  flex-shrink: 0; border: 1px solid var(--green, #1a9e63); background: var(--green-soft, #e5f6ee);
  color: var(--green, #0a6e4d); font-size: 12px; font-weight: 600; padding: 5px 14px;
  border-radius: 999px; cursor: pointer; transition: all 0.15s;
}
.fd-batch-btn:hover { background: var(--green, #1a9e63); color: #fff; }
.fd-batch-btn:disabled { opacity: 0.6; cursor: default; }

.fd-stats { display: flex; gap: 8px; padding: 12px 16px 0; }
.fd-stat {
  flex: 1; text-align: center; padding: 8px 4px; background: var(--card);
  border: 1px solid var(--line); border-radius: var(--radius-s);
}
.fd-stat span { font-size: 18px; font-weight: 700; color: var(--text); display: block; }
.fd-stat label { font-size: 11px; color: var(--text-3); }
.fd-stat.warn span { color: var(--amber); }
.fd-stat.ok span { color: var(--green); }

/* 多列卡片网格（事实条目多，单列太浪费横向空间） */
.fd-list {
  flex: 1; overflow-y: auto; padding: 12px 16px 20px;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 10px; align-content: start;
}
.fact-item {
  display: flex; flex-direction: column; gap: 8px; padding: 12px 14px;
  background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-m);
  border-left: 3px solid var(--text-3);
}
.fact-item.pending { border-left-color: var(--amber); }
.fact-item.confirmed { border-left-color: var(--green); }
.fact-item.expired { border-left-color: var(--line-strong); opacity: 0.6; }
.fact-main { flex: 1; min-width: 0; }
.fact-row1 { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; flex-wrap: wrap; }
.fact-key { font-size: 13px; font-weight: 600; }
.fact-cat {
  font-size: 10px; padding: 1px 7px; border-radius: 3px; background: var(--paper-warm); color: var(--text-2);
}
.fact-tag { font-size: 10px; padding: 1px 7px; border-radius: 3px; font-weight: 600; }
.fact-tag.pending { background: var(--amber-soft); color: #8a5a12; }
.fact-tag.confirmed { background: var(--green-soft); color: #0a6e4d; }
.fact-tag.expired { background: var(--paper-warm); color: var(--text-3); }
.fact-tag.lot { background: rgba(59, 130, 246, 0.12); color: #1d4ed8; }
.fact-row2 { display: flex; align-items: baseline; gap: 10px; margin-bottom: 3px; }
.fact-value { font-size: 16px; font-weight: 700; color: var(--brand); }
.fact-unit { font-size: 11px; color: var(--text-3); margin-left: 3px; font-weight: 400; }
.fact-conf { font-size: 11px; }
.fact-conf.low { color: var(--amber); font-weight: 600; }
.fact-src { font-size: 11px; color: var(--text-3); }
.fact-actions {
  display: flex; flex-direction: row; gap: 6px; flex-shrink: 0;
  padding-top: 8px; border-top: 1px dashed var(--line);
}
/* 低置信度徽标：按用户反馈做得醒目（AI 推断值，可能有误差） */
.fact-tag.lowconf {
  background: #fde8d6; color: #8a4b12; border: 1px solid #f0c9a0; font-weight: 600;
}
.fd-chip.scope { background: var(--paper-warm); border: 1px solid var(--line); }
.fd-chip.scope.on { background: var(--brand-soft); border-color: var(--brand); color: var(--brand); }
.fa-btn {
  min-width: 48px; padding: 4px 10px; border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  background: var(--card); font-size: 12px; color: var(--text-2); cursor: pointer; transition: all 0.15s;
}
.fa-btn:hover { border-color: var(--brand); color: var(--brand); }
.fa-btn.confirm { background: var(--brand); border-color: var(--brand); color: #fff; }
.fa-btn.confirm:hover { background: var(--brand-deep); }
.fa-btn.danger:hover { border-color: var(--red); color: var(--red); }
</style>
