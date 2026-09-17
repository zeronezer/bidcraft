<template>
  <div class="exp">
    <!-- 页头 -->
    <header class="page-head">
      <div class="head-left">
        <h1 class="page-title">经验库</h1>
        <span class="page-sub">对话/编写过程中 AI 自动沉淀的「编制经验」与「编写经验」</span>
      </div>
      <div class="head-tools">
        <el-input v-model="keyword" placeholder="搜索经验…" clearable class="search">
          <template #prefix>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          </template>
        </el-input>
      </div>
    </header>

    <!-- 说明条 -->
    <div class="exp-tip">
      <span class="exp-tip-dot" />
      对话/编写中沉淀的经验自动入库（无需确认），生成时按「章节标题」匹配注入（未限定章节的为全局经验、始终注入），优先级高于知识页
    </div>

    <!-- 经验条目列表 -->
    <main class="exp-list">
      <div v-if="!filteredItems.length" class="exp-empty">
        <div class="ee-icon">✦</div>
        <p>暂无经验</p>
        <p class="ee-sub">在工作台对话中修改 AI 生成内容或下达纠偏指令后，AI 会自动提炼经验</p>
      </div>

      <div v-for="item in filteredItems" :key="item.exp_id" class="exp-card">
        <div class="exp-main">
          <div class="exp-row1">
            <span class="exp-type" :class="typeCls(item.exp_type)">{{ TYPE_MAP[item.exp_type] || item.exp_type }}</span>
            <span v-if="item.chapter_type" class="exp-chapter">{{ item.chapter_type }}</span>
          </div>
          <div class="exp-content">{{ item.content }}</div>
          <div class="exp-src">来源：{{ srcText(item) }}</div>
        </div>
        <div class="exp-actions">
          <button class="ea-btn" @click="openEdit(item)">编辑</button>
          <button class="ea-btn danger" @click="askDelete(item)">删除</button>
        </div>
      </div>
    </main>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="showEdit" title="编辑经验" width="560" append-to-body>
      <el-form label-position="top" class="edit-form">
        <el-form-item label="类型">
          <el-select v-model="editForm.exp_type" class="full">
            <el-option v-for="t in TYPE_OPTIONS" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用章节（留空 = 全局经验，任何章节都注入）">
          <el-input v-model="editForm.chapter_type" placeholder="如：施工方案、主要工程项目的施工方法…" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="editForm.content" type="textarea" :rows="5"
                    placeholder="经验内容（可自由书写，可写「参考某文件某章节」等指引）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listExperience, updateExperience, deleteExperience } from '../api'

const keyword = ref('')
const loading = ref(false)
const allItems = ref([])
const showEdit = ref(false)
const saving = ref(false)
const editForm = ref({ id: null, exp_type: '编写经验', chapter_type: '', content: '' })

// DB 里存中文 exp_type；CSS 类用英文 key → 映射，避免样式失配
// （类型只三档：编写经验/表述偏好/纠偏规则——原"编制经验"与"编写经验"语义重叠，已去掉）
const TYPE_MAP = {
  writing: '编写经验', expression: '表述偏好', correction: '纠偏规则',
}
const TYPE_OPTIONS = Object.values(TYPE_MAP)
const CLS_BY_TYPE = { '编写经验': 'writing', '表述偏好': 'expression', '纠偏规则': 'correction' }

function typeCls(expType) {
  return CLS_BY_TYPE[expType] || (TYPE_MAP[expType] ? expType : 'writing')
}

// 来源文案：只写**项目名称**（这条经验从哪个工程沉淀的）。
// 不显示 source.diff_summary（"1234字 → 987字"的改动规模，原本是喂给提炼 LLM 的上下文，
// 摆在经验库上只是噪音），也不显示沉淀方式（edit_diff/reject/chat_correction）。
function srcText(item) {
  const s = item.source || {}
  return s.project_name || (s.project_id ? `项目 #${s.project_id}` : '—')
}

const filteredItems = computed(() => {
  let list = allItems.value
  if (keyword.value) list = list.filter(i => (i.content || '').includes(keyword.value) || (i.chapter_type || '').includes(keyword.value))
  return list
})

async function load() {
  loading.value = true
  try {
    const { data } = await listExperience()
    allItems.value = data
  } finally {
    loading.value = false
  }
}

function openEdit(item) {
  // DB 老数据可能存过英文 key，统一转成中文选项值
  const et = TYPE_MAP[item.exp_type] || item.exp_type || '编写经验'
  editForm.value = {
    id: item.id,
    exp_type: TYPE_OPTIONS.includes(et) ? et : '编写经验',
    chapter_type: item.chapter_type || '',
    content: item.content || '',
  }
  showEdit.value = true
}

async function saveEdit() {
  if (!(editForm.value.content || '').trim()) {
    ElMessage.warning('内容不能为空')
    return
  }
  saving.value = true
  try {
    await updateExperience(editForm.value.id, {
      exp_type: editForm.value.exp_type,
      chapter_type: editForm.value.chapter_type,
      content: editForm.value.content,
    })
    ElMessage.success('已保存')
    showEdit.value = false
    await load()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function askDelete(item) {
  // 内容放进独立"待删预览"框（与提示文字分层、可滚动），HTML 转义防注入
  const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const typeName = TYPE_MAP[item.exp_type] || item.exp_type || '经验'
  const chapter = item.chapter_type
    ? esc(item.chapter_type)
    : '<span style="color:#999">全局经验</span>'
  const msg =
    '<div style="line-height:1.7">' +
    '<p style="margin:0 0 6px;color:#555">确定删除这条经验吗？删除后不可恢复。</p>' +
    '<div style="border:1px solid #e4e4e4;border-radius:8px;padding:10px 12px;background:#fafafa;' +
    'max-height:200px;overflow:auto">' +
    '<div style="font-size:13px;color:#8a8a8a;margin-bottom:6px">' +
    `【${esc(typeName)}】${chapter}</div>` +
    `<div style="color:#333;font-size:14px;word-break:break-word">${esc(item.content)}</div>` +
    '</div></div>'
  try {
    await ElMessageBox.confirm(msg, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      dangerouslyUseHTMLString: true,
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteExperience(item.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

onMounted(load)
</script>

<style scoped>
.exp { height: 100%; display: flex; flex-direction: column; padding: 28px 32px; }

.page-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; gap: 16px; }
.head-left { display: flex; align-items: baseline; gap: 14px; }
.page-title { font-size: 20px; font-weight: 700; margin: 0; }
.page-sub { font-size: 13px; color: var(--text-3); }
.search { width: 220px; }

.exp-tip {
  display: flex; align-items: center; gap: 8px; margin: 14px 0; padding: 9px 14px;
  background: var(--brand-soft); border: 1px solid #d8e4f8; border-radius: var(--radius-s); font-size: 13px; color: #33518f;
}
.exp-tip-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); flex-shrink: 0; }

.exp-list { flex: 1; overflow-y: auto; }
.exp-empty { text-align: center; padding: 60px 0; color: var(--text-2); }
.ee-icon { font-size: 30px; color: var(--brand); margin-bottom: 10px; }
.ee-sub { font-size: 13px; color: var(--text-3); }

.exp-card {
  display: flex; align-items: flex-start; gap: 16px; padding: 16px 18px; margin-bottom: 12px;
  background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-m);
  border-left: 3px solid var(--brand);
}
.exp-main { flex: 1; min-width: 0; }
.exp-row1 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
/* 经验类型标：color 用深色而非白字，防止样式失配时"全白"看不见 */
.exp-type { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; color: #fff; letter-spacing: 0.02em; }
.exp-type.writing { background: var(--brand); }
.exp-type.expression { background: #7c5cd0; }
.exp-type.correction { background: var(--amber); }
.exp-chapter { font-size: 13px; font-weight: 600; color: var(--text); }
.exp-content { font-size: 14px; line-height: 1.75; color: var(--text); margin-bottom: 8px; }
.exp-src { font-size: 12px; color: var(--text-3); }
.exp-actions { display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; }
.ea-btn {
  min-width: 52px; padding: 5px 12px; border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  background: var(--card); font-size: 13px; color: var(--text-2); cursor: pointer; transition: all 0.15s;
}
.ea-btn:hover { border-color: var(--brand); color: var(--brand); }
.ea-btn.danger:hover { border-color: var(--red); color: var(--red); }
.edit-form .full { width: 100%; }
</style>
