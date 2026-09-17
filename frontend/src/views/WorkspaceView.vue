<template>
  <div class="workspace">
    <!-- 深色顶栏 -->
    <header class="ws-topbar">
      <button class="back-btn" @click="$router.push('/')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>
        首页
      </button>
      <div class="ws-title">
        <span class="ws-name">项目工作台</span>
        <span class="ws-id num">№ {{ String(projectId).padStart(3, '0') }}</span>
        <!-- 当前标段常驻标识：工作台所有操作均基于此标段；点击查看/编辑标段档案 -->
        <span
          class="lot-badge clickable"
          :title="selectedLot ? '点击查看本标段档案（工程范围/构造物/大临/里程碑）' : (lotList.length ? '尚未选定标段，点击选择' : '招标文件未划分标段，工作台基于全线')"
          @click="openLotProfile"
        >
          {{ selectedLot ? '标段 ' + selectedLot.lot_code : (lotList.length ? '未选择标段' : '全线（无标段划分）') }}
        </span>
      </div>

      <!-- 阶段步骤条 -->
      <div class="stage-track">
        <div
          v-for="s in STAGES" :key="s.key"
          class="stage-node"
          :class="{ done: stageOrder(s.key) < stageOrder(stage), on: s.key === stage }"
        >
          <span class="stage-dot num">{{ stageOrder(s.key) < stageOrder(stage) ? '✓' : s.idx }}</span>
          <span class="stage-label">{{ s.label }}</span>
        </div>
      </div>

      <div class="spacer" />
      <button class="ghost-btn" @click="showFacts = true">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5h16M4 12h16M4 19h16"/></svg>
        全局参数
      </button>
      <el-button
        size="small"
        :disabled="!canStart" :loading="stage === 'generating_toc'"
        @click="startCompile"
      >{{ stage === 'idle' ? '生成目录' : '重新生成目录' }}</el-button>
      <el-button
        type="primary" size="small"
        :disabled="!canWriteAll" :loading="stage === 'writing'"
        @click="writeAllChapters"
      >全文编制</el-button>
    </header>

    <div class="ws-body">
      <!-- 深色侧边栏：文件 + 对话 -->
      <aside class="sidebar">
        <section class="side-block files-block">
          <div class="side-head">
            <span class="side-title">项目文件</span>
            <!-- multiple：支持一次选多个文件（逐个串行上传，见 runUploadQueue） -->
            <el-upload
              multiple :show-file-list="false"
              :before-upload="(f) => enqueueUpload(f)"
              accept=".pdf,.docx,.png,.jpg,.jpeg,.xlsx"
            >
              <button class="upload-btn" :disabled="uploading">{{ uploadBtnText }}</button>
            </el-upload>
          </div>
          <div class="file-list">
            <div v-if="!files.length" class="file-empty">暂无文件<br><span>上传招标文件、指导性施组等</span></div>
            <div v-for="g in fileGroups" :key="g.label" class="file-group">
              <div class="group-head">
                {{ g.label }}
                <span class="num">{{ g.items.length }}</span>
              </div>
              <div v-for="f in g.items" :key="f.id" class="file-item">
                <span class="file-icon">{{ fileIcon(f.file_name) }}</span>
                <div class="file-info">
                  <div class="file-name" :title="f.file_name">{{ f.file_name }}</div>
                  <div class="file-meta">
                    <span class="file-status" :class="f.status">{{ STATUS_MAP[f.status] || f.status }}</span>
                  </div>
                  <!-- 解析进度：显示在对应文件行内（进度条 + 当前过程文字） -->
                  <div v-if="f.task_status === 'running'" class="file-prog">
                    <div class="fp-line">
                      <span class="fp-bar"><i class="fp-fill" :style="{ width: (f.task_progress || 0) + '%' }" /></span>
                      <span class="fp-pct num">{{ f.task_progress || 0 }}%</span>
                    </div>
                    <div class="fp-detail" :title="f.task_detail">{{ f.task_detail || '解析中…' }}</div>
                  </div>
                  <div v-else-if="f.status === 'pending'" class="file-prog hint">待解析</div>
                </div>
                <button
                  v-if="f.status === 'parsed'"
                  class="fs-btn" title="查看章节索引与原文"
                  @click.stop="openFileSections(f)"
                >☰</button>
                <button
                  v-if="f.status && f.status !== 'parsed' && f.task_status !== 'running'"
                  class="fs-btn" title="重新解析（无需重新上传）"
                  @click.stop="reparseFile(f)"
                >↻</button>
                <button class="fs-btn del" title="删除该文件" @click.stop="removeFile(f)">×</button>
              </div>
            </div>
          </div>
        </section>

        <section class="side-block chat-block">
          <div class="side-head">
            <span class="side-title">AI 助手</span>
            <button v-if="messages.length" class="chat-clear-btn" title="清空历史消息与上下文" @click="clearChat">清空</button>
          </div>

          <div v-if="progressCard" class="progress-card" :class="stage">
            <div class="pc-head">
              <span class="pc-stage">{{ stageText }}</span>
              <span v-if="stage === 'generating_outline' || stage === 'writing'" class="pc-count num">
                {{ progress }}/{{ total }}
              </span>
            </div>
            <div v-if="stage === 'generating_outline' || stage === 'writing'" class="pc-bar">
              <div class="pc-bar-fill" :style="{ width: total ? (progress / total * 100) + '%' : '0%' }" />
            </div>
            <div v-if="stage === 'confirm_toc'" class="pc-body">
              <p class="pc-tip">目录已生成（兼容旧会话）。如需继续，请在对话中说"重新生成目录"。</p>
            </div>
            <div v-else-if="stage === 'confirm_outline'" class="pc-body">
              <p class="pc-tip">编写思路已生成（兼容旧会话）。如需继续，请在对话中说"重新生成编写思路"。</p>
            </div>
            <div v-else-if="stage === 'outlines_ready'" class="pc-body">
              <p class="pc-tip">目录与编写思路已自动完成。点目录中章节的 ▶ 逐章生成，或：</p>
              <el-button size="small" type="primary" class="pc-btn" @click="startBatch">批量生成全部待写章节 →</el-button>
            </div>
            <div v-else-if="stage === 'writing'" class="pc-body">
              <el-button size="small" class="pc-btn" @click="stopBatch">停止批量生成（进行中的会写完）</el-button>
            </div>
            <div v-else-if="stage === 'failed'" class="pc-body">
              <p class="pc-err">{{ error }}</p>
            </div>
            <div v-else-if="stage === 'done'" class="pc-body">
              <p class="pc-ok">正文全部生成完毕，可在右侧逐章查看编辑。</p>
            </div>
          </div>

          <div class="chat-messages" ref="msgBox" @scroll.passive="onChatScroll">
            <div v-if="!messages.length" class="chat-empty">
              可以向 AI 提问，或下达修改指令<br>如：「第三章补充冬雨期施工措施」
            </div>
            <div v-else-if="hasMoreChat" class="chat-more" :class="{ loading: loadingOlder }" @click="loadOlderChat">
              {{ loadingOlder ? '加载更早…' : '加载更早消息 ▲' }}
            </div>
            <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
              <div class="bubble">
                <!-- 工作状态行：思考中 / 工具执行中，带动效；正文开始输出后消失 -->
                <div v-if="m.status && !m.content" class="chat-status">
                  <span class="cs-spinner" />
                  <span class="cs-text">{{ m.status }}<span class="cs-dots" /></span>
                </div>
                <template v-else>
                  <!-- 助手回复按 markdown 渲染（加粗/列表等）；用户消息纯文本 -->
                  <template v-if="m.role === 'assistant'">
                    <!-- 执行过程：模型回复（💬）与工具调用（⚙）按发生顺序留档，置于卡片顶部。
                         整体可收起；每一步可单独展开（含被核验否决的草稿，标 ✗ 已否决）。 -->
                    <div v-if="m.trace?.length" class="chat-steps">
                      <div class="steps-head" @click="m._stepsOpen = !m._stepsOpen">
                        <span class="sh-caret" :class="{ open: !!m._stepsOpen }">▸</span>
                        ⚙ 执行过程（{{ m.trace.length }} 步）
                      </div>
                      <ol v-if="m._stepsOpen" class="step-list">
                        <li v-for="(t, ti) in m.trace" :key="ti" class="step-item"
                            :class="{ err: isToolStep(t) && !t.ok, rejected: !!t.rejected }">
                          <div class="si-head" @click="t._open = !t._open">
                            <span class="si-caret" :class="{ open: !!t._open }">▸</span>
                            <span class="si-icon">{{ isToolStep(t) ? '⚙' : '💬' }}</span>
                            <span class="si-name">{{ isToolStep(t) ? toolLabel(t.tool) : 'AI 回复' }}</span>
                            <span v-if="isToolStep(t) && traceArgsText(t)" class="si-args num">{{ traceArgsText(t) }}</span>
                            <span v-else-if="!isToolStep(t)" class="si-sum">{{ stepSummary(t) }}</span>
                            <span v-if="t.rejected" class="si-badge">✗ 已否决 · 未执行</span>
                            <span v-else-if="isToolStep(t)" class="si-state" :class="{ ok: t.ok }">{{ t.ok ? '✓' : '✗' }}</span>
                          </div>
                          <div v-if="t._open" class="si-body">
                            <template v-if="isToolStep(t)">
                              <div v-if="t.note" class="si-note">{{ t.note }}</div>
                              <div v-else class="si-note ok-note">执行成功</div>
                            </template>
                            <div v-else class="si-text">{{ t.content }}</div>
                          </div>
                        </li>
                      </ol>
                    </div>
                    <div class="bubble-md" v-html="mdRender(m.content)" />
                    <span v-if="m.status" class="cs-cursor" />
                  </template>
                  <template v-else>{{ m.content }}</template>
                </template>
                <div class="msg-time">{{ fmtTime(m.time || m.created_at) }}</div>
              </div>
              <!-- 快捷追问（AI 自主决策，点击直接发送） -->
              <div v-if="m.role === 'assistant' && m.suggestions?.length && i === messages.length - 1 && !chatBusy" class="chat-chips">
                <button v-for="(s, si) in m.suggestions" :key="si" class="chat-chip" @click="askFollowup(s)">{{ s }}</button>
              </div>
            </div>
          </div>
          <!-- 有新回复但用户停在历史上方 → 悬浮"回到最新"按钮 -->
          <button v-if="showJump" class="chat-jump" title="回到最新回复" @click="jumpToLatest">▼ 有新内容</button>
          <div class="chat-input">
            <textarea
              v-model="input"
              rows="2"
              :placeholder="chatBusy ? 'AI 回复中…（可点发送键停止）' : '与 AI 对话…（Enter 发送）'"
              @keydown.enter.exact.prevent="send"
            />
            <button
              class="chat-send" :class="{ stop: chatBusy }"
              :title="chatBusy ? '停止生成' : '发送'"
              @click="chatBusy ? stopChat() : send()"
            >{{ chatBusy ? '■' : '➤' }}</button>
          </div>
        </section>
      </aside>

      <!-- 浅色编辑区：目录树 + 正文 -->
      <main class="editor-area">
        <div class="toc-panel">
          <div class="toc-header">
            <span>文档目录</span>
            <div class="toc-head-right">
              <span v-if="tocChapters.length" class="toc-count num">{{ treeCount(tocChapters) }}</span>
              <button v-if="unwrittenCount" class="toc-batch-btn ghost" :disabled="outlinesGen" :title="'为 ' + unwrittenCount + ' 个尚无思路的章节生成编写思路'" @click="genOutlines">
                {{ outlinesGen ? '思路生成中…' : '生成思路' }}<i v-if="unwrittenCount" class="toc-unw-num num">{{ unwrittenCount }}</i>
              </button>
              <button class="toc-batch-btn" title="批量编辑目录（粘贴文字生成/调整章节）" @click="openTocBatch">批量编辑</button>
            </div>
          </div>
          <div v-if="tocChapters.length" class="toc-legend">
            <span class="lg"><i class="dot st0" />未写</span>
            <span class="lg"><i class="dot st-thought" />思路</span>
            <span class="lg"><i class="dot st-gen" />已生成</span>
            <span class="lg"><i class="dot st-live" />生成中</span>
            <span class="lg"><i class="dot st-conf" />已确认</span>
          </div>
          <div v-if="!tocChapters.length" class="toc-empty">
            <div class="toc-empty-icon">☰</div>
            <p>尚未生成目录</p>
            <p class="toc-empty-sub">点击顶部「开始编制」<br>AI 将根据招标资料生成目录</p>
          </div>
          <el-tree
            v-else
            ref="tocTreeRef"
            :data="tocChapters"
            :props="{ label: 'title', children: 'children' }"
            node-key="id"
            default-expand-all
            :expand-on-click-node="false"
            highlight-current
            @node-click="onTocNodeClick"
            class="toc-tree"
          >
            <template #default="{ data }">
              <div class="tree-node" @contextmenu.prevent="onNodeContextMenu($event, data)">
                <span class="node-status" :class="[chapterStatus(data), { generating: genNodeId === data.id }]" />
                <span class="node-title">{{ data.title }}</span>
                <span v-if="genNodeId === data.id" class="node-gen" title="点击停止生成">
                  <i class="ng-dot" />生成中 {{ genChars }} 字
                  <em class="ng-stop" @click.stop="stopGenerate">停止</em>
                </span>
                <span class="node-actions" @click.stop>
                  <button
                    v-if="(data.status === 'thought_ready' || data.status === 'generated') && genNodeId !== data.id"
                    title="生成本章（AI 撰写，流式写入编辑器）"
                    @click="generateOne(data)"
                  >▶</button>
                  <button title="添加子章节" @click="addChild(data)">＋</button>
                  <button title="重命名" @click="renameNode(data)">✎</button>
                  <button title="删除" @click="removeNode(data)">×</button>
                </span>
              </div>
            </template>
          </el-tree>
        </div>

        <div class="content-panel">
          <!-- 章节思路生成遮罩：当前章正在重新生成编写思路时，禁止操作该章任意内容 -->
          <div v-if="outlineTasks.has(activeNodeId)" class="outline-gen-mask">
            <div class="ogm-spinner" />
            <p>正在重新生成「{{ activeChapterTitle }}」的编写思路…</p>
            <p class="ogm-sub">AI 检索素材并重写中（生成期间不可编辑本章内容）</p>
          </div>
          <div class="content-header">
            <span class="content-title">{{ activeChapterTitle || '正文' }}</span>
            <span v-if="activeChapterTitle" class="content-hint">在左侧目录切换章节</span>
            <span class="spacer-inline" />
            <span v-if="lastSaved" class="content-saved" :class="{ dirty: autoSaving }">
              {{ autoSaving ? '保存中…' : '上次保存 ' + fmtSaved(lastSaved) }}
            </span>
          </div>

          <!-- 本章编写思路（thought_ready 后展示；可手动编辑） -->
          <div v-if="activeOutline && activeOutline.thinking" class="outline-strip" :class="{ editing: outlineEditing }">
            <div class="os-toggle-row">
              <button class="os-toggle" @click="outlineOpen = !outlineOpen">
                <span class="os-dot" />编写思路<span class="os-arrow">{{ outlineOpen ? '▲' : '▼' }}</span>
              </button>
              <div v-if="!streaming && !outlineEditing" class="os-head-actions">
                <button class="os-mini" :disabled="outlineTasks.has(activeNodeId)" @click="regenOutline">重新生成</button>
                <button class="os-mini" @click="startEditOutline">编辑</button>
              </div>
              <div v-else-if="outlineEditing" class="os-head-actions">
                <button class="os-mini primary" :disabled="outlineSaving" @click="saveOutline">保存</button>
                <button class="os-mini" @click="outlineEditing = false">取消</button>
              </div>
            </div>

            <!-- 编辑态 -->
            <div v-if="outlineEditing" class="os-edit">
              <label>编写思路说明</label>
              <el-input v-model="editOutline.thinking" type="textarea" :rows="3" resize="vertical" placeholder="本章写什么、按什么顺序、用什么材料…" />
              <label>要点清单（每行一个）</label>
              <el-input
                v-model="editKpText" type="textarea" :rows="4" resize="vertical"
                placeholder="每条要点一行（AI 生成的要点会列在此，可增删改）"
              />
              <div class="os-meta">
                <label class="os-check"><el-checkbox v-model="editOutline.need_table" size="small" />需表格</label>
                <label class="os-check"><el-checkbox v-model="editOutline.need_image" size="small" />需配图</label>
              </div>
            </div>

            <!-- 展示态 -->
            <div v-else-if="outlineOpen" class="os-body">
              <p class="os-thinking">{{ activeOutline.thinking }}</p>
              <div v-if="activeOutline.key_points?.length" class="os-kps">
                <span v-for="(k, i) in activeOutline.key_points" :key="i" class="os-kp">{{ k }}</span>
              </div>
              <div class="os-meta">
                <span v-if="activeOutline.need_table" class="os-tag">需表格</span>
                <span v-if="activeOutline.need_image" class="os-tag">需配图</span>
                <button
                  v-if="(activeOutline._page_ids || []).length"
                  class="os-tag os-ref"
                  @click.stop="toggleRefPages"
                  :title="refPages.map(p => p.title).join('；')"
                >参考 {{ activeOutline._page_ids.length }} 篇知识页 {{ refPagesOpen ? '▲' : '▼' }}</button>
              </div>
              <div v-if="refPagesOpen" class="os-pages">
                <div v-for="pg in refPages" :key="pg.id" class="os-page clickable" @click="showKnowledgeDetail(pg)">
                  <span class="osp-title">{{ pg.title }}</span>
                  <span v-if="pg.doc" class="osp-doc">{{ pg.doc }}</span>
                </div>
                <div v-if="!refPages.length" class="os-page empty">（标题加载中…）</div>
              </div>
            </div>
          </div>

          <!-- 撰写中标记条：仅在正文已开始流入时显示（首个 token 前的等待由下方遮罩覆盖，避免露白） -->
          <div v-if="genVisibleStreaming && genStreamStarted" class="ai-mark streaming">
            <span class="ai-mark-tag">撰写中</span>
            <span class="ai-mark-text">AI 正在撰写「{{ activeChapterTitle }}」，内容流式写入中（生成期间不可编辑）…</span>
            <span class="spacer-inline" />
            <button class="ai-btn" @click="stopGenerate">停止生成</button>
          </div>

          <!-- AI 生成标记条 -->
          <div v-if="activeNodeId && aiGenerated && !streaming" class="ai-mark">
            <span class="ai-mark-tag">AI 生成</span>
            <span class="ai-mark-text">本章由 AI 撰写，可编辑后确认或回退到生成前</span>
            <button v-if="usedCount" class="ai-btn plain" @click="usedOpen = true">
              取材来源（{{ usedCount }}）
            </button>
            <span class="spacer-inline" />
            <button class="ai-btn confirm" @click="confirmChapter">确认</button>
            <button class="ai-btn" @click="revertChapter">回退</button>
          </div>

          <!-- 正文舞台：素材整理到首个字符之前全程覆盖半透明遮罩（首字后自动放行，避免空档露白） -->
          <div class="editor-stage">
            <!-- 生成中但已切走：不再回填流式内容，只占位等待，生成完成后一次性展示全文 -->
            <div v-if="activeIsGenerating && !genVisible" class="gen-wait-mask">
              <div class="gw-spinner" />
              <p>AI 正在撰写「{{ activeChapterTitle }}」…</p>
              <p class="gw-sub">已写 {{ genChars }} 字 · 完成后自动展示正文，可先去其他章节</p>
              <button class="gw-stop" @click="stopGenerate">停止生成</button>
            </div>
            <div v-else-if="genVisibleStreaming && !genStreamStarted" class="prep-loading">
              <div class="pl-spinner" />
              <p v-if="genPhase === 'prepare'" class="pl-text">正在整理「{{ activeChapterTitle }}」素材…</p>
              <p v-else class="pl-text">AI 正在撰写「{{ activeChapterTitle }}」…</p>
              <p class="pl-sub">{{ genPhase === 'prepare'
                ? '检索项目文件 · 选取工艺工法 · 匹配知识页'
                : '正在生成首段正文（此阶段约需数秒，请稍候）' }}</p>
            </div>
            <TiptapEditor
              v-if="activeNodeId && !(activeIsGenerating && !genVisible)"
              :key="activeNodeId"
              :model-value="activeChapterContent"
              :project-id="projectId"
              :can-generate="canGenerateEditor"
              :generating="activeIsGenerating"
              @generate="editorGenerate"
              @save="saveChapter"
              class="content-editor"
            />
            <div v-else class="content-empty">
              <div class="ce-icon">✎</div>
              <p class="ce-title">{{ tocChapters.length ? '在左侧目录选择章节' : '正文将在这里生成' }}</p>
              <p class="ce-sub" v-if="!tocChapters.length">确认目录与编写思路后，AI 将逐章撰写，<br>生成后可直接在此编辑修改</p>
            </div>
          </div>
        </div>
      </main>
    </div>

    <!-- 右键引用菜单 -->
    <div
      v-if="ctxMenu.show"
      class="ctx-menu"
      :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
      @click.stop
    >
      <div class="ctx-title">{{ ctxMenu.title }}</div>
      <button v-for="m in CTX_ITEMS" :key="m.key" class="ctx-item" @click="onCtxAction(m.key)">
        <span class="ctx-icon" v-html="m.icon" />
        {{ m.label }}
      </button>
    </div>

    <!-- 目录批量编辑弹窗 -->
    <el-dialog v-model="tocBatchOpen" width="640" class="toc-batch-dlg" :close-on-click-modal="false">
      <template #header>
        <div class="dlg-head">
          <span class="dlg-title">批量编辑目录</span>
          <span class="dlg-sub">粘贴一段目录文字，AI 解析成章→节并替换现有目录</span>
        </div>
      </template>
      <div class="tb-body">
        <el-input
          v-model="tocText" type="textarea" :rows="9" resize="vertical"
          placeholder="在此粘贴目录文字，格式不限（如「第一章 编制依据」换行下列子节；支持编号/无序列表）"
        />
        <div class="tb-actions">
          <el-button size="small" @click="tocText = ''">清空</el-button>
          <el-button size="small" type="primary" :loading="tocPreviewing" @click="doTocPreview">预览变更</el-button>
        </div>

        <div v-if="tocPlan" class="tb-plan" :class="{ warned: tocPlan.deletions.length > 0, danger: tocPlan.delete_with_body > 0 }">
          <div class="tb-plan-head">
            <span>解析出 {{ tocPlan.node_count }} 个章节节点</span>
            <span class="tb-stat">新增 <b class="num">{{ tocPlan.additions }}</b> · 删除 <b class="num">{{ tocPlan.deletions.length }}</b></span>
          </div>
          <div v-if="tocPlan.delete_with_body" class="tb-warn danger">
            ⚠ 将删除 {{ tocPlan.deletions.length }} 个现有节点，其中 <b>{{ tocPlan.delete_with_body }}</b> 节<b>已有正文，将被一并删除、无法恢复</b>！
          </div>
          <div v-else-if="tocPlan.deletions.length" class="tb-warn">
            ⚠ 将删除 {{ tocPlan.deletions.length }} 个现有节点（这些章节不在你给的目录中）；当前均<b>无正文</b>，如曾写过正文请务必核对
          </div>
          <div v-if="tocPlan.deletions.length" class="tb-del-list">
            <div v-for="d in tocPlan.deletions" :key="d.id" class="tb-del" :class="{ has: d.has_body }">
              <span class="tb-del-t">✕ {{ d.title }}</span>
              <span v-if="d.has_body" class="tb-del-has num">含正文</span>
            </div>
          </div>
          <div v-else-if="tocPlan.node_count" class="tb-nodel">
            无删除——将在现有基础上新增缺失章节，同名章节保留其思路与正文
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="tocBatchOpen = false">取消</el-button>
        <el-button v-if="tocPlan" type="primary" :loading="tocApplying" @click="applyTocPlan">
          {{ tocPlan.delete_with_body ? '确认替换（含删除已有正文）' : '确认应用' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 全局参数抽屉 -->
    <FactDrawer v-model="showFacts" :project-id="projectId" @changed="refreshPendingFacts" />
    <!-- 章节索引查看抽屉（文件旁 ☰ 按钮） -->
    <FileSectionsDrawer v-model="showFileSections" :project-id="projectId" :file-id="sectionFile?.id" :file-name="sectionFile?.file_name || ''" />

    <!-- 标段档案：本标段包含什么（取材过滤与正文生成白名单的依据；可人工编辑） -->
    <el-dialog v-model="lotProfileOpen" width="860" top="6vh" title="标段档案" append-to-body class="lot-profile-dlg">
      <div class="lp-wrap">
        <!-- 抽取中遮罩：禁止编辑 + 显示进度（任务在后台跑，刷新页面/重开弹窗会自动恢复） -->
        <div v-if="lotProfileBusy" class="lp-mask">
          <span class="lp-spinner lp-spinner-lg" />
          <div class="lp-mask-text">{{ lotRebuildMsg || '正在抽取标段档案…' }}</div>
          <div class="lp-mask-sub">{{ lotBusySub || '抽取期间不可编辑；任务在后台运行，刷新页面不会中断' }}</div>
        </div>

        <div v-if="lotProfileLoading" class="lp-empty">加载中…</div>
      <template v-else-if="lotProfile">
        <div class="lp-head">
          <span class="lp-code">{{ lotProfile.lot_code }}</span>
          <span class="lp-name">{{ lotProfile.lot_name }}</span>
          <span class="spacer-inline" />
          <button class="lp-btn" :disabled="lotProfileBusy" @click="doRebuildLotProfile">
            {{ lotProfileBusy ? '抽取中…' : '重新抽取' }}
          </button>
          <button class="lp-btn primary" :disabled="lotProfileBusy" @click="doSaveLotProfile">保存修改</button>
        </div>

        <div class="lp-desc">
          本档案是 AI 编制本标段的<strong>范围依据</strong>：目录、编写思路与正文只围绕下面列出的工程展开，
          其他标段的工点与工程类型不会被写入；含多标段的整表章节也会按本标段内容裁剪后再使用。
        </div>

        <div class="lp-sec">
          <div class="lp-title">
            工程构成
            <span class="lp-hint">正文只写这里列出的工程；可直接编辑，保存后自动解析回结构化数据</span>
          </div>
          <el-input
            v-model="lpEdit.text" type="textarea" :rows="10" resize="vertical" class="lp-textarea"
            placeholder="里程范围：…&#10;工程构成：路基…；桥梁…；隧道…&#10;主要构造物：…&#10;大型临时设施：…"
          />
        </div>

        <div class="lp-sec">
          <div class="lp-title">
            关键里程碑
            <button class="lp-mini" @click="lpEdit.milestones.push({ name: '', days: '', start: '', end: '' })">+ 添加</button>
          </div>
          <div v-if="lpEdit.milestones.length" class="lp-ms-head">
            <span class="lp-ms-c1">节点</span>
            <span class="lp-ms-c2">天数</span>
            <span class="lp-ms-c3">开始</span>
            <span class="lp-ms-c3">结束</span>
            <span class="lp-ms-del" />
          </div>
          <div v-for="(m, i) in lpEdit.milestones" :key="i" class="lp-row">
            <el-input v-model="m.name" size="small" style="flex: 1" placeholder="节点名称（如 施工准备 / 赣州赣江铁路特大桥）" />
            <el-input v-model="m.days" size="small" class="lp-ms-c2" placeholder="天数" />
            <el-input v-model="m.start" size="small" class="lp-ms-c3" placeholder="2025-8-15" />
            <el-input v-model="m.end" size="small" class="lp-ms-c3" placeholder="2030-8-15" />
            <button class="lp-del lp-ms-del" title="删除该行" @click="lpEdit.milestones.splice(i, 1)">✕</button>
          </div>
          <div v-if="!lpEdit.milestones.length" class="lp-none">暂无里程碑（可点「重新抽取」从《各标段分阶段工期》自动提取）</div>
        </div>
      </template>
      <div v-else class="lp-empty">
        <!-- ① 还没传招标文件：标段划分就来自它，没有输入可抽 -->
        <template v-if="lotEmptyKind === 'no-tender'">
          尚未上传招标文件
          <div class="lp-empty-sub">
            标段划分来自招标文件。请先在左侧「项目文件」上传招标文件，解析完成后回到这里识别标段
          </div>
        </template>

        <!-- ② 招标文件解析中：识别吃的是解析产物，等解析完才能抽 -->
        <template v-else-if="lotEmptyKind === 'parsing'">
          招标文件解析中
          <div class="lp-empty-sub">
            解析完成后即可识别标段划分；进度见左侧文件列表，完成后点左上角标段标识回到这里
          </div>
        </template>

        <!-- ③ 正在识别：任务在后台跑，刷新页面/重开弹窗会自动恢复这个遮罩 -->
        <template v-else-if="lotEmptyKind === 'extracting'">
          正在识别标段划分…
          <div class="lp-empty-sub">{{ lotExtractMsg || 'AI 正在读招标文件' }}（任务在后台运行，刷新页面不会中断）</div>
        </template>

        <!-- ④ 已解析完成、尚无标段：给识别入口（识别过但为空也算这一支，可重试） -->
        <template v-else-if="lotEmptyKind === 'no-lots'">
          {{ lotsStatus && lotsStatus.extracted ? '上次未识别到标段划分' : '尚未识别标段划分' }}
          <div class="lp-empty-sub">
            从已解析的招标文件中识别标段划分。若本项目确实未划分标段，识别后工作台将基于全线编制
          </div>
          <button class="lp-btn primary" :disabled="lotProfileBusy" @click="doExtractLots">识别标段划分</button>
        </template>

        <!-- ⑤ 有标段划分但未选定：列出标段，选中即自动建立该标段档案 -->
        <template v-else-if="!selectedLot">
          请选择要编制档案的标段
          <div class="lp-empty-sub">本项目共 {{ lotList.length }} 个标段，选中后自动抽取该标段档案并按本标段重提取全局参数</div>
          <div class="lot-pick">
            <button
              v-for="l in lotList" :key="l.id"
              class="lot-pick-item" :disabled="lotProfileBusy"
              @click="pickLotAndBuild(l)"
            >
              <span class="lpi-code">{{ l.lot_code }}</span>
              <span class="lpi-name">{{ l.lot_name }}</span>
            </button>
          </div>
          <div class="lp-empty-sub lp-re">
            <button class="lp-mini" :disabled="lotProfileBusy" @click="doExtractLots">重新识别标段划分</button>
            <span>（会清空现有标段及档案）</span>
          </div>
        </template>

        <!-- ⑥ 已选定标段但尚无档案：直接抽取本标段档案 -->
        <template v-else>
          暂无标段档案
          <div class="lp-empty-sub">上传《标段划分表》（附表7）等含标段范围的文件后会自动生成；也可点下方按钮立即抽取</div>
          <button class="lp-btn primary" :disabled="lotProfileBusy" @click="doRebuildLotProfile">立即抽取</button>
        </template>
      </div>
      </div>
    </el-dialog>

    <!-- 本章取材来源：AI 生成这一章时实际用了哪些经验 / 知识页 / 项目文件片段 / 工法 -->
    <el-dialog v-model="usedOpen" width="760" top="6vh" title="本章取材来源" append-to-body class="used-dlg">
      <template v-if="activeChapterUsed">
        <div v-if="activeChapterUsed.experiences?.length" class="used-sec">
          <div class="us-title">经验库 · {{ activeChapterUsed.experiences.length }} 条（最高优先级，必须遵守）</div>
          <div
            v-for="(e, i) in activeChapterUsed.experiences" :key="i"
            class="us-item clickable" title="点击查看完整内容"
            @click="openUsedDetail('experience', e.id)"
          >
            <div class="us-head">
              <span class="us-tag">{{ e.type }}</span>
              <span class="us-chapter">{{ e.chapter || '全局经验（所有章节）' }}</span>
            </div>
            <div class="us-text">{{ e.content }}</div>
          </div>
        </div>
        <div v-if="activeChapterUsed.knowledge?.length" class="used-sec">
          <div class="us-title">知识页 · {{ activeChapterUsed.knowledge.length }} 篇（示范行文/结构，数据不得照搬）</div>
          <div
            v-for="(k, i) in activeChapterUsed.knowledge" :key="i"
            class="us-item clickable" title="点击查看原文"
            @click="openUsedDetail('knowledge', k.id)"
          >
            <div class="us-text">◆ {{ k.title }}</div>
          </div>
        </div>
        <div v-if="activeChapterUsed.files?.length" class="used-sec">
          <div class="us-title">项目文件原文片段 · {{ activeChapterUsed.files.length }} 段（数据以此为准）</div>
          <div
            v-for="(f, i) in activeChapterUsed.files" :key="i"
            class="us-item clickable" title="点击查看该章原文"
            @click="openUsedDetail('file', f.id)"
          >
            <div class="us-text">◆ {{ f.title }}</div>
          </div>
        </div>
        <div v-if="activeChapterUsed.methods?.length" class="used-sec">
          <div class="us-title">工艺工法 · {{ activeChapterUsed.methods.length }} 个</div>
          <div
            v-for="(m, i) in activeChapterUsed.methods" :key="i"
            class="us-item clickable" title="点击查看工法内容"
            @click="openUsedDetail('method', m.id)"
          >
            <div class="us-text">◆ {{ m.name }}</div>
          </div>
        </div>
        <div class="used-foot">
          另注入：本项目事实 {{ activeChapterUsed.facts_n || 0 }} 条<span v-if="activeChapterUsed.lot">、本标段信息</span>
        </div>
      </template>
      <div v-else class="used-empty">本章没有取材记录（多为用户手工编辑保存后的版本）</div>
    </el-dialog>

    <!-- 取材条目详情：点取材来源里的某条 → 看该条原文/全文 -->
    <el-dialog
      v-model="usedDetailOpen" width="840" top="5vh"
      :title="usedDetail ? usedDetail.title : '片段内容'" append-to-body class="used-detail-dlg"
    >
      <div v-if="usedDetailLoading" class="used-empty">加载中…</div>
      <div v-else class="ud-md" v-html="mdRender((usedDetail && usedDetail.text) || '（无内容）')" />
    </el-dialog>

    <!-- 思路参考知识页详情弹窗 -->
    <el-dialog v-model="kpDialog" width="860" top="5vh" :title="kpDetail ? ('知识页 · ' + kpDetail.title) : '知识页详情'" destroy-on-close class="kp-dlg">
      <div v-if="kpLoading" class="req-empty">加载中…</div>
      <template v-else-if="kpDetail">
        <div class="kp-grid">
          <div class="kp-field"><label>标题</label><div class="kp-val">{{ kpDetail.title }}</div></div>
          <div class="kp-field"><label>类别</label><div class="kp-val">{{ kpDetail.category || '—' }}</div></div>
          <div class="kp-field"><label>预计字数</label><div class="kp-val num">{{ kpDetail.estimate_words ? '约 ' + kpDetail.estimate_words + ' 字' : '—' }}</div></div>
          <div class="kp-field"><label>出处</label><div class="kp-val">{{ kpDetail.source?.doc || '—' }}</div></div>
          <div class="kp-field" v-if="kpDetail.method"><label>编制方式</label><div class="kp-val">{{ kpDetail.method }}</div></div>
          <div class="kp-field" v-if="(kpDetail.key_points || []).length">
            <label>要点</label>
            <ul class="kp-list"><li v-for="(k, i) in kpDetail.key_points" :key="i">{{ k }}</li></ul>
          </div>
          <div class="kp-field" v-if="(kpDetail.tables || []).length">
            <label>表格（点击查看完整表格与填写说明）</label>
            <div v-for="(t, i) in kpDetail.tables" :key="i" class="kp-chip clickable" @click="kpPreviewTable = t">
              ▦ {{ t.title || t.name }}<span v-if="(t.columns || []).length" class="kp-chip-sub">（{{ t.columns.length }} 列）</span>
            </div>
          </div>
          <div class="kp-field" v-if="(kpDetail.images || []).length">
            <label>图片（点击放大查看）</label>
            <div class="kp-imgs">
              <div v-for="(img, i) in kpDetail.images" :key="i" class="kp-img clickable" @click="kpPreviewImage = img">
                <img v-if="img.url" :src="img.url" class="kp-img-real" loading="lazy" alt="" />
                <div v-else class="kp-img-ph">🖼</div>
                <div class="kp-img-cap">{{ img.caption || img.title }}</div>
              </div>
            </div>
          </div>
          <div class="kp-field" v-if="kpDetail.ref_text">
            <label>历史内容参考（该历史项目原文，仅作风格参考）</label>
            <div class="kp-ref" v-html="renderRefMd(kpDetail.ref_text)" />
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 表格放大预览 -->
    <el-dialog v-model="kpPreviewTable" width="780" top="6vh" :title="kpPreviewTable?.title || '表格详情'" class="kp-preview" destroy-on-close>
      <template v-if="kpPreviewTable">
        <div v-if="kpPreviewTable.description" class="kp-desc">{{ kpPreviewTable.description }}</div>
        <div v-if="(kpPreviewTable.columns || []).length" class="kp-cols">
          <div class="kp-cols-title">表头填写说明：</div>
          <div v-for="c in kpPreviewTable.columns" :key="c.name" class="kp-col-item"><b>{{ c.name }}</b>：{{ c.desc }}</div>
        </div>
        <div class="kp-table" v-html="renderRefMd(kpPreviewTable.content_md || '')" />
      </template>
    </el-dialog>

    <!-- 图片放大预览 -->
    <el-dialog v-model="kpPreviewImage" width="760" top="6vh" :title="kpPreviewImage?.caption || '图片详情'" class="kp-preview" destroy-on-close>
      <template v-if="kpPreviewImage">
        <img v-if="kpPreviewImage.url" :src="kpPreviewImage.url" class="kp-bigimg" alt="" />
        <div v-else class="kp-img-empty">未挂接原图</div>
        <div v-if="kpPreviewImage.content" class="kp-img-txt"><b>图片内容：</b>{{ kpPreviewImage.content }}</div>
        <div v-if="kpPreviewImage.usage" class="kp-img-txt"><b>用途说明：</b>{{ kpPreviewImage.usage }}</div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listFiles, uploadFile, deleteProjectFile, reparseProjectFile, chatStream, getChatHistory, clearChatHistory, compileStart, compileStatus, compileResume, writeAll, taskStatus, listFacts, selectLot, extractLots, getLotsStatus, getLotProfile, saveLotProfile, rebuildLotProfile, getLotProfileRebuildStatus, getUsedDetail, tocPreview, tocApply, generateTreeOutlines, listKnowledgePages, getKnowledgePage, listRequirements,
  writeBatch, writeStop, generateSectionStream,
  getChapterTree, createChapterNode, updateChapterNode, deleteChapterNode,
  getSectionContent, saveSectionContent, confirmSection, revertSection, putNodeOutline,
  regenerateNodeOutline, runningOutlineTasks } from '../api'
import TiptapEditor from '../components/TiptapEditor.vue'
import FactDrawer from '../components/FactDrawer.vue'
import FileSectionsDrawer from '../components/FileSectionsDrawer.vue'

const props = defineProps({ projectId: String })
const router = useRouter()
const files = ref([])
const messages = ref([])
const input = ref('')
const msgBox = ref(null)
const showFacts = ref(false)

// 章节索引查看（文件旁 ☰ 按钮）
const showFileSections = ref(false)
const sectionFile = ref(null)
function openFileSections(f) {
  sectionFile.value = f
  showFileSections.value = true
}

// 当前标段（顶栏常驻徽标 + 全局参数/图表/质检的过滤依据）
const selectedLot = ref(null)
const lotList = ref([])   // 项目标段划分（区分"未划分标段"与"有标段但没选"）
// 标段识别状态：供弹窗区分「没传招标文件 / 正在解析 / 正在识别 / 确实未划分」四种空态
const lotsStatus = ref(null)

async function loadSelectedLot() {
  // 走 /lots/status：一次拿到标段列表 + 识别状态（是 /lots 的超集），少一次请求
  try {
    const { data } = await getLotsStatus(Number(props.projectId))
    lotsStatus.value = data
    lotList.value = data.lots || []
    selectedLot.value = (data.lots || []).find(l => l.selected) || null
  } catch { /* ignore */ }
}

// 目录批量编辑（粘贴文字生成/替换目录）
const tocBatchOpen = ref(false)
const tocText = ref('')
const tocPlan = ref(null)
const tocPreviewing = ref(false)
const tocApplying = ref(false)
function openTocBatch() {
  tocText.value = ''
  tocPlan.value = null
  tocBatchOpen.value = true
}
async function doTocPreview() {
  if (!tocText.value.trim()) return ElMessage.warning('请先粘贴目录文字')
  tocPreviewing.value = true
  try {
    const { data } = await tocPreview(Number(props.projectId), tocText.value)
    if (!data.chapters?.length || data.node_count === 0) {
      tocPlan.value = null
      ElMessage.warning('未能从这段文字中解析出任何章节节点，不会替换目录。请粘贴真正的目录结构文字（如「第一章 编制依据」/「1.1 …」「5.1.1 …」），而非正文说明')
      return
    }
    tocPlan.value = data
  } catch (e) {
    ElMessage.error('解析失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    tocPreviewing.value = false
  }
}
async function applyTocPlan() {
  const del = tocPlan.value
  // 有删除 → 二次确认（含正文更强提示）
  if (del?.deletions.length) {
    const withBody = del.delete_with_body || 0
    const msg = withBody
      ? `目录将删除 ${del.deletions.length} 个现有节点，其中 ${withBody} 节已有正文，会被一并删除且无法恢复。确定继续？`
      : `目录将删除 ${del.deletions.length} 个现有节点（当前无正文）。这些章节若不在你粘贴的新目录中即会被移除。确定继续？`
    try {
      await ElMessageBox.confirm(msg, '确认目录替换', { type: 'warning', confirmButtonText: '确认替换', cancelButtonText: '取消' })
    } catch { return }
  }
  tocApplying.value = true
  try {
    const { data } = await tocApply(Number(props.projectId), tocText.value)
    if (!data.ok) {
      ElMessage.error(data.error || '替换失败')
      return
    }
    ElMessage.success(`已应用：删除 ${data.deleted}（含正文 ${data.deleted_with_body}），新增 ${data.added} 章节`)
    tocBatchOpen.value = false
    tocPlan.value = null
    await loadTree()
  } catch (e) {
    ElMessage.error('应用失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    tocApplying.value = false
  }
}

// ---------- 编排状态 ----------
const stage = ref('idle')
const progress = ref(0)
const total = ref(0)
const error = ref('')
const tocChapters = ref([])
const outlines = ref([])
const chapters = ref([])
const activeChapterTitle = ref('')
let pollTimer = null

const STAGES = [
  { idx: 1, key: 'generating_toc', label: '目录' },
  { idx: 2, key: 'generating_outline', label: '编写思路' },
  { idx: 3, key: 'outlines_ready', label: '待写正文' },
  { idx: 4, key: 'writing', label: '撰写正文' },
  { idx: 5, key: 'done', label: '完成' },
]
const stageOrder = (k) => {
  if (k === 'idle') return 0
  if (k === 'failed') return 99
  const i = STAGES.findIndex(s => s.key === k)
  return i === -1 ? 0 : i + 1
}

const CAT_MAP = { tender: '招标', guiding_sod: '指导性施组', clarification: '答疑补遗', survey_report: '勘察报告', planning: '前期策划', drawing: '图纸', other: '其他' }
const CAT_ORDER = ['tender', 'guiding_sod', 'planning', 'survey_report', 'clarification', 'drawing', 'other']
// 文件按分类分组显示（组顺序按业务习惯：招标 → 指导性施组 → 前期策划 → …）
const fileGroups = computed(() => {
  const byCat = new Map()
  for (const f of files.value) {
    const cat = f.category || 'other'
    if (!byCat.has(cat)) byCat.set(cat, [])
    byCat.get(cat).push(f)
  }
  return [...byCat.entries()]
    .sort((a, b) => CAT_ORDER.indexOf(a[0]) - CAT_ORDER.indexOf(b[0]))
    .map(([cat, items]) => ({ label: CAT_MAP[cat] || cat, items }))
})
const STATUS_MAP = { pending: '待解析', uploaded: '已上传', parsing: '解析中', parsed: '已解析', failed: '失败' }
const fileIcon = (name) => {
  if (/\.pdf$/i.test(name)) return 'PDF'
  if (/\.docx?$/i.test(name)) return 'W'
  if (/\.xlsx?$/i.test(name)) return 'X'
  if (/\.(png|jpe?g)$/i.test(name)) return '图'
  return '文'
}

// ---------- 目录树 + 正文（数据库驱动） ----------
const activeNodeId = ref(null)
const activeChapterContent = ref('')
const aiGenerated = ref(false)
// 本章取材来源（后端生成时落库在 content.used：经验/知识页/项目文件片段/工法/事实条数）
const activeChapterUsed = ref(null)
const usedOpen = ref(false)
// 取材条目详情（点取材来源里的某条 → 二级弹窗看原文/全文）
const usedDetail = ref(null)
const usedDetailOpen = ref(false)
const usedDetailLoading = ref(false)
async function openUsedDetail(kind, id) {
  if (!id) return
  usedDetailOpen.value = true
  usedDetailLoading.value = true
  usedDetail.value = null
  try {
    const { data } = await getUsedDetail(Number(props.projectId), kind, id)
    usedDetail.value = data
  } catch (e) {
    ElMessage.error('读取内容失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    usedDetailLoading.value = false
  }
}
const usedCount = computed(() => {
  const u = activeChapterUsed.value
  if (!u) return 0
  return (u.experiences?.length || 0) + (u.knowledge?.length || 0)
    + (u.files?.length || 0) + (u.methods?.length || 0)
})

// ---------- 标段档案（本标段包含什么：取材过滤与正文生成白名单的依据） ----------
const lotProfileOpen = ref(false)
const lotProfile = ref(null)
const lotProfileLoading = ref(false)
const lotProfileBusy = ref(false)
const lpEdit = reactive({ text: '', milestones: [] })
const lotRebuildMsg = ref('')  // "正在抽取标段档案…" 进度文案
const lotBusySub = ref('')     // 遮罩副标题（保存与抽取两件事文案不同，别都说成"抽取"）
const lotExtractMsg = ref('')  // "正在识别标段划分…" 进度文案

// 空态分支："没有标段档案"要看是哪种原因——没传招标文件 / 正在解析 / 正在识别 /
// 确实未划分，四种的正确动作完全不同（去传文件 / 等解析 / 等识别 / 点识别），
// 不能一律显示"本项目未划分标段"（用户会以为没救）。
const lotEmptyKind = computed(() => {
  if (lotList.value.length) return selectedLot.value ? 'no-profile' : 'pick-lot'
  const st = lotsStatus.value
  if (st?.running) return 'extracting'
  if (!st?.has_tender) return 'no-tender'
  if (st?.parsing && !st?.parsed) return 'parsing'
  return 'no-lots'
})

// 同步档案 → 编辑态（工程构成是一段文字，里程碑是表格）
function _syncLotProfileEdit() {
  const p = lotProfile.value || {}
  lpEdit.text = p.text || ''
  lpEdit.milestones = (p.milestones || []).map((m) => ({
    name: m.name || '', days: m.days ?? '', start: m.start || '', end: m.end || '',
  }))
}
async function openLotProfile() {
  lotProfileOpen.value = true
  lotProfileLoading.value = true
  lotBusySub.value = ''   // 复位副标题：打开弹窗后若有抽取任务，用"抽取"的默认文案
  await loadSelectedLot()   // 空态分支要用识别状态，先拿到
  try {
    const { data } = await getLotProfile(Number(props.projectId))
    lotProfile.value = data.profile || null
    _syncLotProfileEdit()
  } catch (e) {
    ElMessage.error('读取标段档案失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    lotProfileLoading.value = false
  }
  // 打开时检查是否有进行中的任务：有则恢复遮罩（刷新页面/重开弹窗也能看到"进行中"）
  if (await checkExtractRunning()) return
  checkRebuildRunning()
}

// 轮询抽取任务直到完成（成功返回，失败抛错）
async function _pollRebuild(taskId, fallback = '正在抽取标段档案…') {
  for (let i = 0; i < 200; i++) {
    const { data: t } = await taskStatus(taskId)
    if (t.status === 'success') return
    if (t.status === 'failed') {
      // task.detail 存的是"异常 + traceback"（给排查用），界面只取第一行的人话部分
      const raw = String(t.detail || '').split('\n')[0].split('Traceback')[0].trim()
      throw new Error(raw.replace(/[:：]\s*$/, '').slice(0, 200) || '抽取失败')
    }
    lotRebuildMsg.value = (t.detail || fallback).replace(/…$/, '')
    lotExtractMsg.value = lotRebuildMsg.value
    await new Promise((r) => setTimeout(r, 1500))
  }
  throw new Error('抽取超时，请稍后在弹窗中查看结果')
}

// 从已解析的招标文件识别标段划分（后台任务 + 轮询；已有标段时须先二次确认）
async function doExtractLots() {
  if (lotProfileBusy.value) return
  const had = lotList.value.length
  if (had) {
    try {
      await ElMessageBox.confirm(
        `重新识别会清空现有的 ${had} 个标段，并连带清除已建立的标段档案（需重新抽取）。确定继续？`,
        '重新识别标段划分',
        { type: 'warning', confirmButtonText: '确定继续', cancelButtonText: '取消' },
      )
    } catch { return }   // 用户取消
  }
  lotProfileBusy.value = true
  lotExtractMsg.value = '正在识别标段划分…'
  lotRebuildMsg.value = ''
  try {
    const { data } = await extractLots(Number(props.projectId), Boolean(had))
    if (data.need_confirm) {   // 后端兜底（比如另一端已建了标段）
      ElMessage.warning(data.message)
      return
    }
    if (data.task_id) await _pollRebuild(data.task_id, '正在识别标段划分…')
    await loadSelectedLot()
    await openLotProfileSilent()
    ElMessage.success(
      lotList.value.length
        ? `识别到 ${lotList.value.length} 个标段，请选择要编制的标段`
        : '未识别到标段划分——工作台将基于全线编制',
    )
  } catch (e) {
    ElMessage.error('识别失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    lotProfileBusy.value = false
    lotExtractMsg.value = ''
    lotRebuildMsg.value = ''
  }
}

// 刷新/重开弹窗时恢复"正在识别标段划分"的遮罩；返回是否接管了本次打开
async function checkExtractRunning() {
  if (!lotsStatus.value?.running || !lotsStatus.value.task_id) return false
  lotProfileBusy.value = true
  lotExtractMsg.value = lotsStatus.value.detail || '正在识别标段划分…'
  try {
    await _pollRebuild(lotsStatus.value.task_id, '正在识别标段划分…')
    await loadSelectedLot()
    loadTree()   // 标段变了 → 目录/事实按新标段刷新
  } catch (e) {
    ElMessage.error('标段识别失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    lotProfileBusy.value = false
    lotExtractMsg.value = ''
    lotRebuildMsg.value = ''
  }
  return true
}

async function checkRebuildRunning() {
  try {
    const { data } = await getLotProfileRebuildStatus(Number(props.projectId))
    if (!data.running || !data.task_id) return
    lotProfileBusy.value = true
    lotRebuildMsg.value = data.detail || '正在抽取标段档案…'
    await _pollRebuild(data.task_id)
    await openLotProfileSilent()
    ElMessage.success('标段档案已更新')
  } catch (e) {
    ElMessage.error('抽取失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    lotProfileBusy.value = false
    lotRebuildMsg.value = ''
  }
}

// 只重拉档案数据（不重开弹窗、不再触发任务检查）
async function openLotProfileSilent() {
  try {
    const { data } = await getLotProfile(Number(props.projectId))
    lotProfile.value = data.profile || null
    _syncLotProfileEdit()
  } catch { /* ignore */ }
}

// 选定标段并自动建立该标段档案（档案为空时的补救入口：项目有标段划分但一个都没选）
// 任务内含两步（后端串行）：建本标段档案 → 按本标段重提取全局参数
async function pickLotAndBuild(lot) {
  if (lotProfileBusy.value) return
  lotProfileBusy.value = true
  lotRebuildMsg.value = `正在切换到「${lot.lot_code}」：抽取标段档案并按本标段重提取全局参数…`
  try {
    await selectLot(Number(props.projectId), lot.id)
    await loadSelectedLot()   // 顶部标段徽章同步
    const { data } = await rebuildLotProfile(Number(props.projectId))
    if (data.task_id) await _pollRebuild(data.task_id)
    await openLotProfileSilent()
    ElMessage.success(`已切换到「${lot.lot_code}」：档案已建立，全局参数已按本标段重提取`)
    // 标段变了 → 工作台的目录与事实按新标段刷新（不阻塞提示）
    loadTree()
    refreshPendingFacts()
  } catch (e) {
    ElMessage.error('建立档案失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    lotProfileBusy.value = false
    lotRebuildMsg.value = ''
  }
}

async function doRebuildLotProfile() {
  lotProfileBusy.value = true
  lotRebuildMsg.value = '正在抽取标段档案并按本标段重提取全局参数…'
  try {
    const { data } = await rebuildLotProfile(Number(props.projectId))
    if (data.task_id) await _pollRebuild(data.task_id)
    await openLotProfileSilent()
    ElMessage.success('标段档案已更新，全局参数已按本标段重提取')
    refreshPendingFacts()
  } catch (e) {
    ElMessage.error('抽取失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    lotProfileBusy.value = false
    lotRebuildMsg.value = ''
  }
}
async function doSaveLotProfile() {
  if (!lotProfile.value) return
  // 只有"工程构成"文字**真的改了**才需要跑 LLM 解析（后端同规则），此时才值得弹遮罩；
  // 只改里程碑、或原样保存都是瞬时的，弹遮罩反而会闪一下。
  const textChanged = (lpEdit.text || '').trim() !== ((lotProfile.value.text || '')).trim()
  if (textChanged) {
    lotProfileBusy.value = true
    lotRebuildMsg.value = '正在保存…'
    lotBusySub.value = '工程构成有改动，正在解析回结构化数据（正文白名单要用）'
  }
  try {
    await saveLotProfile(Number(props.projectId), {
      text: lpEdit.text,
      milestones: lpEdit.milestones
        .filter((m) => String(m.name || '').trim())
        .map((m) => ({
          name: m.name.trim(),
          days: m.days === '' || m.days === null ? null : Number(m.days),
          start: m.start || '',
          end: m.end || '',
        })),
    })
    ElMessage.success('标段档案已保存')
    // 只重拉档案数据（openLotProfileSilent）——**不能走 openLotProfile**：它会把
    // lotProfileLoading 置 true，模板随即切到"加载中…"分支，档案内容消失一瞬再回来，
    // 用户看到的就是"弹窗闪了一下"。
    await openLotProfileSilent()
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    lotProfileBusy.value = false
    lotRebuildMsg.value = ''
    lotBusySub.value = ''
  }
}
const contentLoading = ref(false)

const statusLabel = (s) => ({
  unwritten: '未写', thought_ready: '思路已定', generated: '已生成', confirmed: '已确认',
}[s] || s)

// 章节状态点样式：直接取库内状态
function chapterStatus(data) {
  return {
    confirmed: 'confirmed',
    generated: 'generated',
    thought_ready: 'thought',
  }[data.status] || 'pending'
}

// 目录节点总数（含子级，用于头部计数）
function treeCount(nodes) {
  let n = 0
  const walk = (list) => { for (const x of list || []) { n++; if (x.children?.length) walk(x.children) } }
  walk(nodes)
  return n
}

// 尚无编写思路（unwritten）的节点数
const unwrittenCount = computed(() => {
  let n = 0
  const walk = (list) => { for (const x of list || []) { if (x.status === 'unwritten') n++; if (x.children?.length) walk(x.children) } }
  walk(tocChapters.value)
  return n
})

// 批量生成思路（对 unwritten 节点，后台任务，进度走 task 表）
const outlinesGen = ref(false)
async function genOutlines() {
  if (outlinesGen.value) return
  try {
    const { data } = await generateTreeOutlines(Number(props.projectId))
    await pollGenOutlines(data.task_id)
  } catch (e) {
    outlinesGen.value = false
    ElMessage.error('启动失败：' + (e?.response?.data?.detail || e.message))
  }
}
async function genOutlinesResume(taskId) {
  await pollGenOutlines(taskId)
}
// 批量思路任务轮询（genOutlines 启动 / 刷新恢复共用）
async function pollGenOutlines(taskId) {
  if (outlinesGen.value) return
  outlinesGen.value = true
  const timer = setInterval(async () => {
    try {
      const st = (await taskStatus(taskId)).data
      await loadTree()  // 每轮都刷新目录树：已完成思路的节点蓝点实时点亮
      if (st.status === 'success') {
        clearInterval(timer); outlinesGen.value = false
        ElMessage.success(st.detail || '思路已生成')
        await loadTree()
      } else if (st.status === 'failed') {
        clearInterval(timer); outlinesGen.value = false
        ElMessage.error('思路生成失败：' + (st.detail || '未知错误'))
      }
    } catch { /* 下轮重试 */ }
  }, 3000)
}

async function loadTree() {
  try {
    const { data } = await getChapterTree(props.projectId)
    tocChapters.value = data
    // 若当前选中节点已不在树里，清掉
    if (activeNodeId.value && !findNodeById(data, activeNodeId.value)) {
      activeNodeId.value = null
      activeChapterTitle.value = ''
      activeChapterContent.value = ''
      aiGenerated.value = false
    }
  } catch { /* 后端未就绪时保持本地状态 */ }
}

function findNodeById(list, id) {
  for (const n of list) {
    if (n.id === id) return n
    const hit = findNodeById(n.children || [], id)
    if (hit) return hit
  }
  return null
}

async function loadContent(nodeId) {
  contentLoading.value = true
  try {
    const { data } = await getSectionContent(nodeId)
    activeChapterContent.value = data.found ? (data.content.body || '') : ''
    aiGenerated.value = data.found && !!data.ai_generated
    activeChapterUsed.value = data.found ? (data.content.used || null) : null
    activeNodeStatus.value = data.status || activeNodeStatus.value
    lastSaved.value = data.saved_at || ''
  } catch {
    activeChapterContent.value = ''
    aiGenerated.value = false
    activeChapterUsed.value = null
  } finally {
    contentLoading.value = false
  }
}

// ---------- 目录树编辑 ----------
async function addChild(data) {
  try {
    await createChapterNode(props.projectId, { title: '新章节', parent_id: data.id })
    await loadTree()
    ElMessage.success('已添加子章节')
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function renameNode(data) {
  try {
    const { value } = await ElMessageBox.prompt('输入新标题', '重命名', {
      inputValue: data.title, confirmButtonText: '确定', cancelButtonText: '取消',
    })
    if (value && value.trim()) {
      await updateChapterNode(data.id, { title: value.trim() })
      await loadTree()
      if (activeNodeId.value === data.id) activeChapterTitle.value = value.trim()
      ElMessage.success('已重命名')
    }
  } catch { /* 取消 */ }
}

async function removeNode(data) {
  try {
    await ElMessageBox.confirm(`确定删除「${data.title}」及其子章节？`, '删除章节', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await deleteChapterNode(data.id)
    if (activeNodeId.value === data.id) {
      activeNodeId.value = null
      activeChapterTitle.value = ''
      activeChapterContent.value = ''
      aiGenerated.value = false
    }
    await loadTree()
    ElMessage.success('已删除')
  } catch { /* 取消 */ }
}

// ---------- 右键引用 ----------
const ctxMenu = ref({ show: false, x: 0, y: 0, title: '', node: null })
const pendingRefs = ref([])  // 下一条消息附带的引用上下文
const CTX_ITEMS = [
  { key: 'send', label: '发给 AI', icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/></svg>' },
  { key: 'modify', label: '基于此修改', icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>' },
  { key: 'explain', label: '解释', icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2.5-2.5 2.5M12 17h.01"/></svg>' },
  { key: 'expand', label: '扩写', icon: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>' },
]

function onNodeContextMenu(e, data) {
  ctxMenu.value = { show: true, x: e.clientX, y: e.clientY, title: data.title, node: data }
  window.addEventListener('click', closeCtx, { once: true })
}
function closeCtx() { ctxMenu.value.show = false }

function onCtxAction(key) {
  const title = ctxMenu.value.title
  const node = ctxMenu.value.node
  const nid = node?.id
  pendingRefs.value = [{ type: 'toc_node', ref: nid ?? null, content: title }]
  // 指令明确带上节点 id，并说明完成动作（扩写/修改要用 rewrite_section 提交完整新版）
  const prefix = {
    send: () => `关于「${title}」章节（节点 ${nid}）：`,
    modify: () => `请改写「${title}」章节（节点 ${nid}）正文：先用 read_section 读取现文，在其基础上按我的要求改写，用 rewrite_section 提交完整的改写后正文（要忠于原文事实数字，缺依据标 [待补充]）。`,
    explain: () => `请解释「${title}」章节（节点 ${nid}）的编写思路：`,
    expand: () => `请扩写「${title}」章节（节点 ${nid}）正文：先用 read_section 读取现文，在忠于原有数据、不新增无依据内容的前提下扩写充实，用 rewrite_section 提交完整的扩写后正文。`,
  }[key] || (() => title)
  input.value = prefix()
  ctxMenu.value.show = false
  nextTick(() => {
    const ta = document.querySelector('.chat-input textarea')
    ta?.focus()
  })
  ElMessage.info(`已引用「${title}」，补充要求后按 Enter 发送`)
}

// ---------- AI 标记 / 确认回退 ----------
async function confirmChapter() {
  if (!activeNodeId.value) return
  try {
    await confirmSection(activeNodeId.value)
    aiGenerated.value = false
    await loadTree()
    ElMessage.success('本章已确认')
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}
async function revertChapter() {
  if (!activeNodeId.value) return
  try {
    await revertSection(activeNodeId.value)
    await loadContent(activeNodeId.value)
    await loadTree()
    ElMessage.info('已回退到生成前内容')
  } catch (e) {
    ElMessage.error('回退失败：' + (e?.response?.data?.detail || e.message))
  }
}

// ---------- 章节选择 ----------
const activeOutline = ref(null)
const outlineOpen = ref(true)
const outlineEditing = ref(false)
const outlineSaving = ref(false)
const editOutline = ref({ thinking: '', need_table: false, need_image: false })
const editKpText = ref('')
// 思路引用的知识页标题（懒加载：点开"参考 N 篇知识页"时取）
const refPages = ref([])
const refPagesOpen = ref(false)
async function toggleRefPages() {
  if (refPagesOpen.value) { refPagesOpen.value = false; return }
  refPagesOpen.value = true
  const ids = (activeOutline.value?._page_ids) || []
  if (!ids.length || refPages.value.length) return
  try {
    const { data } = await listKnowledgePages({ ids: ids.join(',') })
    refPages.value = data.map(p => ({ id: p.id, title: p.title, doc: (p.source && p.source.doc) || '' }))
  } catch { /* ignore */ }
}
function resetRefPages() { refPagesOpen.value = false; refPages.value = [] }

// 思路参考知识页点击：跳到该知识页详情（知识库页）
function goKnowledgePage(p) {
  if (!p?.id) return
  router.push({ path: '/knowledge/pages', query: { page: p.id } })
}

// 思路参考知识页点击：弹窗展示详情（不跳转）
const kpDialog = ref(false)
const kpDetail = ref(null)
const kpLoading = ref(false)
const kpPreviewTable = ref(null)
const kpPreviewImage = ref(null)
// 历史内容参考/表格：markdown/HTML 渲染（含 HTML 表格）
function renderRefMd(md) {
  if (!md) return '<div class="kp-empty">（空）</div>'
  try { return marked.parse(md) } catch { return '<p>' + md + '</p>' }
}
async function showKnowledgeDetail(p) {
  if (!p?.id) return
  kpDetail.value = null
  kpDialog.value = true
  kpLoading.value = true
  try {
    const { data } = await getKnowledgePage(p.id)
    kpDetail.value = data
  } catch (e) {
    ElMessage.error('加载知识页失败')
  } finally {
    kpLoading.value = false
  }
}
function startEditOutline() {
  const o = activeOutline.value
  editOutline.value = { thinking: o?.thinking || '', need_table: !!o?.need_table, need_image: !!o?.need_image }
  editKpText.value = (o?.key_points || []).join('\n')
  outlineEditing.value = true
}
// 重新生成编写思路（异步后台任务）：启动后盖遮罩、轮询 task 至结束。
// 刷新页面后由 recoverOutlineTasks 恢复跟踪（任务在服务端线程继续）。
const outlineGenTask = ref(null)        // {taskId, nodeId} 当前在生成的思路任务
const outlineTasks = reactive(new Map()) // nodeId -> taskId，跟踪所有进行中的单章思路任务（含刷新恢复）
async function regenOutline() {
  if (outlineGenTask.value || !activeNodeId.value) return
  try {
    const { data } = await regenerateNodeOutline(activeNodeId.value)
    const nodeId = activeNodeId.value
    outlineTasks.set(nodeId, data.task_id)
    outlineGenTask.value = { taskId: data.task_id, nodeId }
    await pollOutlineTasks()   // 轮询至该 task 结束（完成后刷新 outline + 目录树）
  } catch (e) {
    ElMessage.error('启动重新生成失败：' + (e?.response?.data?.detail || e.message))
  }
}
async function pollOutlineTasks() {
  // 轮询 outlineTasks 中所有 running 的单章思路任务；全部结束返回
  const pending = () => [...outlineTasks.entries()].filter(([, tid]) => tid)
  const timer = setInterval(async () => {
    const entries = pending()
    if (!entries.length) { clearInterval(timer); return }
    let allDone = true
    for (const [nodeId, tid] of entries) {
      let st
      try { st = (await taskStatus(tid)).data } catch { continue }
      if (st.status === 'success' || st.status === 'failed') {
        outlineTasks.delete(nodeId)
        if (nodeId === activeNodeId.value) {
          // 完成当前章 → 关遮罩并刷新思路/目录树
          outlineGenTask.value = null
          await refreshOutlineAfterTask()
          st.status === 'success'
            ? ElMessage.success(st.detail || '编写思路已重新生成')
            : ElMessage.error('重新生成失败：' + (st.detail || '未知错误'))
        }
      } else allDone = false
    }
    if (allDone && !pending().length) clearInterval(timer)
  }, 2000)
  // 立即先刷一次目录树（显示进行中）
  await loadTree()
}
async function refreshOutlineAfterTask() {
  await loadTree()
  const node = activeNodeId.value ? findNodeById(tocChapters.value, activeNodeId.value) : null
  if (node) {
    activeOutline.value = node.outline || null
    activeChapterTitle.value = node.title || ''
    activeNodeStatus.value = node.status || null
    resetRefPages()
  }
}
// 恢复刷新前仍在进行中的思路任务：查 running 单章/批量任务，续轮询
async function recoverOutlineTasks() {
  try {
    const { data } = await runningOutlineTasks(Number(props.projectId))
    for (const t of data.tasks || []) {
      if (t.kind === 'node' && t.node_id != null) {
        outlineTasks.set(t.node_id, t.task_id)
        if (t.node_id === activeNodeId.value) outlineGenTask.value = { taskId: t.task_id, nodeId: t.node_id }
      } else if (t.kind === 'batch') {
        genOutlinesResume(t.task_id)   // 恢复批量思路任务轮询
      }
    }
    if ([...outlineTasks.keys()].length) await pollOutlineTasks()
  } catch { /* ignore */ }
}
async function saveOutline() {
  outlineSaving.value = true
  try {
    const kps = editKpText.value.split('\n').map(s => s.trim()).filter(Boolean)
    const next = { ...activeOutline.value, thinking: editOutline.value.thinking.trim(),
                   need_table: editOutline.value.need_table, need_image: editOutline.value.need_image,
                   key_points: kps }
    await putNodeOutline(activeNodeId.value, next)
    activeOutline.value = next
    outlineEditing.value = false
    await loadTree()
    ElMessage.success('编写思路已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    outlineSaving.value = false
  }
}
const activeNodeStatus = ref(null)
const lastSaved = ref('')
const autoSaving = ref(false)
const fmtSaved = (t) => (t ? new Date(t).toLocaleTimeString('zh-CN', { hour12: false }) : '')

// 编辑区顶部"生成本章"：思路已定/已生成/已确认 且非生成中才可点
const canGenerateEditor = computed(() =>
  !!activeNodeId.value && !!activeNodeStatus.value &&
  ['thought_ready', 'generated', 'confirmed'].includes(activeNodeStatus.value) && !streaming.value)

function onTocNodeClick(data) {
  if (!data || !data.id) return
  // 生成中允许切换章节：切走即放弃流式渲染，那一章改为"生成中"占位，写完一次性展示
  if (genNodeId.value && genVisible.value && data.id !== genNodeId.value) genVisible.value = false
  if (data.id === genNodeId.value) {
    if (genVisible.value) return  // 正看着这一章的流式输出，重复点击不动它
    // 生成中的章节不拉库：库里还是旧内容/空，交给占位遮罩等生成完成
    activeNodeId.value = data.id
    activeChapterTitle.value = data.title
    activeOutline.value = data.outline || null
    activeNodeStatus.value = data.status || null
    outlineEditing.value = false
    resetRefPages()
    activeChapterContent.value = ''
    activeChapterUsed.value = null
    aiGenerated.value = false
    lastSaved.value = ''
    return
  }
  activeNodeId.value = data.id
  activeChapterTitle.value = data.title
  activeOutline.value = data.outline || null
  activeNodeStatus.value = data.status || null
  outlineEditing.value = false
  resetRefPages()
  loadContent(data.id)
}

function editorGenerate() {
  if (!canGenerateEditor.value || !activeNodeId.value) return
  generateOne({ id: activeNodeId.value, title: activeChapterTitle.value, outline: activeOutline.value })
}

async function saveChapter(html, auto = false) {
  if (!activeNodeId.value) return ElMessage.warning('请先在目录中选择章节')
  autoSaving.value = true
  try {
    const { data } = await saveSectionContent(activeNodeId.value, { format: 'html', body: html })
    lastSaved.value = data.saved_at || ''
    await loadTree()
    if (!auto) ElMessage.success('已保存到服务器')
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    autoSaving.value = false
  }
}

const stageText = computed(() => ({
  idle: '就绪',
  generating_toc: '正在生成目录…',
  confirm_toc: '等待确认目录',
  generating_outline: '正在生成编写思路…',
  confirm_outline: '等待确认思路',
  outlines_ready: '思路已确认，待写正文',
  writing: '正在批量撰写正文…',
  done: '编制完成',
  failed: '出错',
}[stage.value] || stage.value))

const canStart = computed(() => ['idle', 'done', 'failed', 'outlines_ready'].includes(stage.value))
// 全文编制：目录已有章节且当前没有在批量写正文
const canWriteAll = computed(() => tocChapters.value.length > 0 && stage.value !== 'writing')
const progressCard = computed(() => stage.value !== 'idle')

// 文件解析进度：只显示在对应文件行内（服务端任务驱动的行内轮询）
const uploading = ref(false)
const uploadPending = ref(0)    // 上传队列中还没传完的文件数（多选时逐个串行）
// 多选时按钮显示剩余数，让用户知道一次传了几个、还剩几个
const uploadBtnText = computed(() => {
  if (!uploading.value) return '＋ 上传'
  return uploadPending.value > 1 ? `上传中…（剩 ${uploadPending.value}）` : '上传中…'
})
const pendingFacts = ref(0)
let pollRowsTimer = null

async function loadFiles() {
  const { data } = await listFiles(props.projectId)
  files.value = data
  // 有正在解析的文件 → 启动行内进度轮询（刷新/重进自动恢复）
  if (data.some(f => f.task_status === 'running')) startPollRows()
}

function startPollRows() {
  if (pollRowsTimer) return
  pollRows()
  pollRowsTimer = setInterval(pollRows, 3000)
}

async function pollRows() {
  try {
    const { data } = await listFiles(props.projectId)
    const prev = new Map(files.value.map(f => [f.id, f]))
    files.value = data
    // 完成/失败只提示一次（按前后状态变化判断）
    for (const f of data) {
      const p = prev.get(f.id)
      if (!p) continue
      const wasWorking = p.status === 'parsing' || p.status === 'pending' || p.task_status === 'running'
      if (wasWorking && f.status === 'parsed') ElMessage.success(`「${f.file_name}」解析完成`)
      else if (p.task_status === 'running' && f.status === 'failed') ElMessage.error(`「${f.file_name}」解析失败，可点 ↻ 重新解析`)
    }
    if (!data.some(f => f.task_status === 'running')) {
      clearInterval(pollRowsTimer); pollRowsTimer = null
      refreshPendingFacts()
    }
  } catch { /* ignore */ }
}

async function refreshPendingFacts() {
  try {
    const { data } = await listFacts(props.projectId)
    pendingFacts.value = data.filter(f => f.status === 'pending').length
  } catch { /* ignore */ }
}

// 按文件名推断文件类别（决定是否触发事实抽取、以及抽取侧重点）
function guessCategory(file) {
  return file.name.includes('招标') ? 'tender'
    : file.name.includes('指导性') ? 'guiding_sod'
    : file.name.includes('答疑') || file.name.includes('补遗') ? 'clarification'
    : file.name.includes('勘察') ? 'survey_report'
    : file.name.includes('策划') ? 'planning'
    : /\.(png|jpe?g)$/i.test(file.name) ? 'drawing' : 'other'
}

// 上传队列（2026-09-11 支持多选）：**串行**逐个上传，不并发。
// 并发的话一次选 10 个文件会同时触发 10 个解析任务（MinerU + LLM 一起上，把服务打满），
// 提示消息也会连弹 10 条。串行还顺带让"剩 N 个"的进度有意义。
const uploadQueue = []
let uploadRunning = false

function enqueueUpload(file) {
  uploadQueue.push(file)
  if (!uploadRunning) runUploadQueue()
  return false   // 返回 false 阻止 el-upload 自带的 XHR 上传（我们走自己的 API）
}

async function runUploadQueue() {
  uploadRunning = true
  const total = uploadQueue.length
  const failed = []
  let ok = 0
  uploading.value = true
  try {
    while (uploadQueue.length) {
      const file = uploadQueue[0]
      uploadPending.value = uploadQueue.length
      try {
        await uploadFile(props.projectId, file, guessCategory(file))
        ok++
      } catch (e) {
        failed.push(`${file.name}（${e?.response?.data?.detail || e.message}）`)
      }
      uploadQueue.shift()
    }
  } finally {
    uploadRunning = false
    uploading.value = false
    uploadPending.value = 0
  }
  await loadFiles()
  startPollRows()   // 新上传的文件会各自跑解析任务 → 启动行内进度轮询
  // 统一提示（一条，而不是每个文件弹一次）
  if (!failed.length) {
    ElMessage.success(total > 1
      ? `${ok} 个文件上传成功，解析中（进度显示在各文件行内）`
      : '上传成功，解析中（进度显示在该文件行内）')
  } else {
    ElMessage.error(`${failed.length} 个文件上传失败：${failed.join('；').slice(0, 200)}`)
  }
}

async function reparseFile(f) {
  try {
    const { data } = await reparseProjectFile(Number(props.projectId), f.id)
    if (data.ok) {
      ElMessage.success('已开始重新解析')
      await loadFiles()
      startPollRows()
    } else {
      ElMessage.info(data.message || '未启动')
    }
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function removeFile(f) {
  try {
    await ElMessageBox.confirm(
      `删除后该文件及其解析章节索引将从项目移除（已抽取的全局事实保留）。确定删除「${f.file_name}」？`,
      '删除项目文件', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    await deleteProjectFile(Number(props.projectId), f.id)
    ElMessage.success('已删除')
    await loadFiles()
  } catch (e) {
    ElMessage.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function startCompile() {
  // 判断"是否重新生成"以**目录是否存在**为准（比 stage 可靠：页面刚加载/状态未同步时
  // stage 可能还是 idle，曾因此绕过确认、直接触发清空式重建）。
  const hasToc = tocChapters.value.length > 0
  if (hasToc) {
    const nNodes = tocChapters.value.length
    try {
      await ElMessageBox.confirm(
        `<div style="font-size:13px;line-height:1.85;color:#1a2332">
           <div style="background:#fef4f4;border:1px solid #f3c3c3;border-radius:6px;padding:11px 13px;margin-bottom:11px">
             <div style="font-weight:700;color:#c0392b;margin-bottom:6px">⚠ 将清空以下内容，且不可恢复</div>
             <div style="color:#8a5a12">· 现有目录树（共 ${nNodes} 个顶级章及全部子节）</div>
             <div style="color:#8a5a12">· 各章编写思路</div>
             <div style="color:#8a5a12">· 已生成的全部正文</div>
           </div>
           <div style="color:#8b95ab;font-size:12px;line-height:1.8">
             新目录与编写思路会按<b style="color:#2f5bd0">当前标段范围</b>重新生成（本标段不含的工程不会出现），
             完成后需重新生成正文。
           </div>
         </div>`,
        '重新生成目录', {
          type: '', dangerouslyUseHTMLString: true,
          confirmButtonText: '清空并重新生成', cancelButtonText: '取消',
          confirmButtonClass: 'el-button--danger',
        },
      )
    } catch { return }  // 用户取消
  }
  try {
    // 输入由后端从 DB 组装（项目信息/已确认事实/专业），前端只传 project_id
    await compileStart({ project_id: Number(props.projectId) })
    ElMessage.success(hasToc ? '已开始重新生成目录（将清空旧目录）' : '已开始生成目录')
    startPolling()
  } catch (e) {
    ElMessage.error('启动失败：' + (e?.response?.data?.detail || e.message))
  }
}
async function confirmToc() {
  await compileResume({ project_id: Number(props.projectId), approval: { action: 'confirm' } })
  startPolling()
}
async function confirmOutline() {
  await compileResume({ project_id: Number(props.projectId), approval: { action: 'confirm' } })
  startPolling()
}
function startPolling() { stopPolling(); pollTimer = setInterval(poll, 2000); poll() }
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

async function poll() {
  try {
    const { data } = await compileStatus(props.projectId)
    stage.value = data.stage
    progress.value = data.progress || 0
    total.value = data.total || 0
    error.value = data.error || ''
    if (data.stage === 'generating_toc' && data.partial?.chapters?.length) {
      // 目录流式：章骨架先出现，子节逐章"长出来"（不改 DB 树，仅预览）
      tocChapters.value = data.partial.chapters
    }
    if (data.stage === 'generating_outline') {
      // 目录已自动落库（v0.2 取消确认环节），切到 DB 驱动视图（含节点 id）
      await loadTree()
    }
    if (data.interrupt) {
      // 兼容旧检查点（已无确认环节，正常不会出现）
      if (data.interrupt.type === 'confirm_toc') tocChapters.value = data.interrupt.toc?.chapters || []
    }
    if (data.stage === 'writing') {
      // 批量生成中：刷新目录状态点
      await loadTree()
    }
    if (data.result) {
      // 状态机跑完（思路已落库），刷新目录树与当前正文
      await loadTree()
      if (activeNodeId.value) await loadContent(activeNodeId.value)
    }
    if (['done', 'failed', 'outlines_ready'].includes(data.stage)) stopPolling()
  } catch (e) { /* 忽略轮询偶发错误 */ }
}

// ---------- 按章生成（单章流式 / 批量可停止） ----------
// 生成期间允许自由切换章节浏览，因此"在生成"与"在看"必须分开表示：
// genNodeId = 哪一章在生成（null=空闲）；genVisible = 是否仍把流式增量渲染进编辑器。
// 一旦切走，genVisible 置 false 且不可逆——切回来只显示"生成中"占位，生成完成后
// 一次性展示全文（避免半截内容重建 TipTap 带来的光标/undo/AI 标记边界问题）。
const genNodeId = ref(null)
const genTitle = ref('')
const genVisible = ref(false)
const genChars = ref(0)   // 本次已接收正文字数（目录树实时进度）
const genCtrl = ref(null)  // 当前生成流的 abort 控制器（"停止生成"用）
let genStopped = false     // 用户主动停止（与真实失败区分，决定提示文案与落库等待）
const genPhase = ref('idle')  // prepare: 整理素材中；writing: 已进入撰写（首个 token 未到前遮罩仍覆盖）
const genStreamStarted = ref(false)  // 是否已收到首个正文字符（决定遮罩放行/撰写条出现）
const streaming = computed(() => genNodeId.value !== null)

// 当前查看的正是生成中的那一章（占位遮罩、禁保存、不拉库都以此为准）
const activeIsGenerating = computed(() => !!activeNodeId.value && activeNodeId.value === genNodeId.value)
// 生成中且仍在向上屏的流式渲染（切走后为 false）
const genVisibleStreaming = computed(() => activeIsGenerating.value && genVisible.value)

function generateOne(data) {
  if (genNodeId.value) {
    return ElMessage.warning(genNodeId.value === data.id
      ? `「${data.title}」正在生成中，请稍候`
      : `「${genTitle.value}」正在生成中，完成前请等待（可先浏览其他章节）`)
  }
  activeNodeId.value = data.id
  activeChapterTitle.value = data.title
  activeChapterContent.value = ''
  activeChapterUsed.value = null  // 生成中清掉旧取材，完成后 loadContent 拉取本次记录
  aiGenerated.value = false
  activeOutline.value = data.outline || null
  genNodeId.value = data.id
  genTitle.value = data.title
  genVisible.value = true
  genChars.value = 0
  genPhase.value = 'prepare'
  genStreamStarted.value = false
  genStopped = false
  genCtrl.value = generateSectionStream(
    data.id,
    Number(props.projectId),
    (evt) => {
      if (evt.type === 'chapter_prepare') genPhase.value = 'prepare'
      else if (evt.type === 'chapter_start') genPhase.value = 'writing'
      else if (evt.type === 'chapter_delta') {
        genChars.value += (evt.content || '').length
        // 切走后不再回填编辑器：增量照常接收只用于进度，写完一次性展示
        if (genVisible.value && activeNodeId.value === data.id) {
          if (!genStreamStarted.value) genStreamStarted.value = true
          activeChapterContent.value += evt.content
        }
      }
      else if (evt.type === 'debug_context') {
        // 调试：打印本次生成实际发给模型的上下文
        console.log(`%c[生成上下文 · ${data.title}]`, 'color:#b45309;font-weight:700')
        console.log(evt.content)
      }
    },
    async () => {
      const nodeId = data.id
      genNodeId.value = null
      genCtrl.value = null
      genStopped = false
      genPhase.value = 'idle'
      genStreamStarted.value = false
      genVisible.value = false
      genChars.value = 0
      await loadTree()
      if (activeNodeId.value === nodeId) await loadContent(nodeId)  // 正看着这章 → 立刻展示全文
      ElMessage.success(`「${data.title}」已生成，请审阅后确认或回退`)
    },
    async (err) => {
      const nodeId = data.id
      const stopped = genStopped  // 主动停止 ≠ 真失败，两者提示与善后都不同
      genNodeId.value = null
      genCtrl.value = null
      genStopped = false
      genPhase.value = 'idle'
      genStreamStarted.value = false
      genVisible.value = false
      genChars.value = 0
      if (stopped) {
        await loadTree()
        // 正看着这一章才需要等落库（否则用户不在看，等来也没意义）
        let kept = false
        if (activeNodeId.value === nodeId) {
          kept = await waitForPartial(nodeId)
          await loadContent(nodeId)
        }
        ElMessage.info(kept
          ? `「${data.title}」已停止，已生成的部分内容已保留`
          : `「${data.title}」已停止生成`)
      } else {
        // 中断时后端会尽量保留已生成的部分内容，拉一次看落库了什么
        if (activeNodeId.value === nodeId) loadContent(nodeId)
        ElMessage.error('生成失败：' + err)
      }
    },
  )
}

// 停止当前单章生成：断开 SSE，后端保留已生成的部分内容（可重新生成或续写）
function stopGenerate() {
  if (!genNodeId.value || !genCtrl.value) return
  genStopped = true
  genCtrl.value.abort()
}

// 停止后等后端把已生成的部分落库：服务端要等下一个 chunk 才感知断开并触发 GeneratorExit，
// 落库时机取决于模型何时产下一个 token，所以轮询而不是固定 sleep（最长约 5s）
async function waitForPartial(nodeId) {
  for (let i = 0; i < 6; i++) {
    await new Promise((r) => setTimeout(r, i === 0 ? 600 : 800))
    try {
      const { data } = await getSectionContent(nodeId)
      if (data.found && (data.content?.body || '').trim()) return true
    } catch { /* 偶发错误，继续等 */ }
  }
  return false
}

async function startBatch() {
  try {
    const { data } = await writeBatch(Number(props.projectId))
    if (data.started) {
      ElMessage.success(`开始批量生成 ${data.total} 个待写章节`)
      startPolling()
    } else {
      ElMessage.info(data.message || '未启动')
    }
  } catch (e) {
    ElMessage.error('启动失败：' + (e?.response?.data?.detail || e.message))
  }
}

// 全文编制：对目录中**全部章节**重新生成正文（旧版自动存为可回退版本）
async function writeAllChapters() {
  if (!tocChapters.value.length) return ElMessage.warning('请先生成目录')
  try {
    await ElMessageBox.confirm(
      `<div style="font-size:13px;line-height:1.85;color:#1a2332">
         <div style="background:#f0f7ff;border:1px solid #c4d6f5;border-radius:6px;padding:11px 13px;margin-bottom:11px">
           <div style="font-weight:700;color:#2f5bd0;margin-bottom:6px">为「已有编写思路、尚未生成正文」的章节生成正文</div>
           <div style="color:#4c586e">· 已生成、已确认的章节不会被改动</div>
           <div style="color:#4c586e">· 需要重写某章时，请在目录里单独点该章的 ▶</div>
         </div>
         <div style="color:#8b95ab;font-size:12px;line-height:1.8">
           正文会按<b style="color:#2f5bd0">本标段工程范围</b>生成（只写本标段的工点）。
           过程中可在顶部查看进度，也可随时停止。
         </div>
       </div>`,
      '全文编制', {
        type: '', dangerouslyUseHTMLString: true,
        confirmButtonText: '开始全文编制', cancelButtonText: '取消',
      },
    )
  } catch { return }  // 用户取消
  try {
    const { data } = await writeAll(Number(props.projectId))
    if (data.started) {
      ElMessage.success(`已启动全文编制：共 ${data.total} 章`)
      startPolling()
      loadTree()
    } else {
      ElMessage.info(data.message || '未启动')
    }
  } catch (e) {
    ElMessage.error('启动失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function stopBatch() {
  try {
    await writeStop(Number(props.projectId))
    ElMessage.info('已请求停止：进行中的章节写完后停，已写内容保留')
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.detail || e.message))
  }
}

// 对话发送：进行中锁定（防并发导致上下文错乱），可点停止按钮中止
const chatBusy = ref(false)
const showJump = ref(false)   // 用户停在历史区、AI 有新回复时显示"回到最新"
let chatAbortCtrl = null

function send() {
  if (chatBusy.value) return ElMessage.warning('上一条还在回复中，可点 ■ 停止后再发')
  const text = input.value.trim()
  if (!text) return
  messages.value.push({ role: 'user', content: text, time: Date.now() })
  input.value = ''
  const refs = pendingRefs.value
  pendingRefs.value = []
  messages.value.push({ role: 'assistant', content: '', status: '正在思考中', time: Date.now() })
  // 必须取响应式代理：直接改普通对象字段不会触发 Vue 重渲染（表现为流式失效、整段一次出现）
  const reply = messages.value[messages.value.length - 1]
  reply.trace = []
  chatBusy.value = true
  // 发送即滚底：让用户立刻看到自己的消息 + "思考中"动画（不显得卡住/空白）
  showJump.value = false
  scrollBottom()
  chatAbortCtrl = chatStream(
    { project_id: Number(props.projectId), message: text, refs },
    (delta) => {
      reply.content += delta
      if (reply.status) reply.status = ''  // 正文开始输出，状态行退场
      followStream()  // 贴底才跟随；用户停在历史时不拉底（亮"回到最新"）
    },
    () => {
      // 助手消息时间取"回复完成"时刻（与历史消息的落库时间一致），而非开始/占位时间
      reply.time = Date.now()
      reply._stepsOpen = false  // 回复完成后执行过程自动收起（需要时点开再看）
      if (!reply.content) reply.content = '（已完成）'
      reply.status = ''
      chatBusy.value = false
      chatAbortCtrl = null
      showJump.value = false
      scrollBottom()  // 完成时回到最新
    },
    (err) => {
      reply.time = Date.now()  // 出错/中断也记结束时刻
      reply.content += `\n[错误] ${err}`
      reply.status = ''
      chatBusy.value = false
      chatAbortCtrl = null
    },
    (evt) => {
      if (evt.type === 'progress') {
        // 工具执行状态：实时更新状态行文案（不混入正文）。
        // 首个工具开始时：把正文区里模型"边叙述边调工具"的那段移出（后端已先把它作为
        // 「AI 叙述」步骤发来，存于上方过程区），正文区只留给最终答复。
        if (!reply._toolsStarted) {
          reply._toolsStarted = true
          if (reply.content) reply.content = ''
        }
        reply.status = evt.content.replace(/…$/, '')
        followStream()
      } else if (evt.type === 'final_answer') {
        // 定稿：以此替换整条回复。丢弃流式过程中"先查目录/准备生成"等草稿、以及纠偏中间
        // 过程（此前会出现"前半段乱说、后半段才对"整段落库到一条消息里）
        reply.content = evt.content || reply.content
        reply.status = ''
        followStream()
      } else if (evt.type === 'draft_discard') {
        // 定稿核验判定"该答复缺工具背书、需重做"：把正文区这段清掉（内容已作为
        // 「✗ 已否决 · 未执行」步骤留在上方过程区，不会凭空消失），等重做后的定稿
        reply.content = ''
        followStream()
      } else if (evt.type === 'tool_trace') {
        // 工具调用：作为过程步骤累积（定稿下方「执行过程」可折叠查看）
        reply.trace = reply.trace || []
        reply.trace.push({ kind: 'tool', tool: evt.tool, args: evt.args || {}, ok: !!evt.ok, note: evt.note || '' })
      } else if (evt.type === 'step_text') {
        // 模型叙述（含被定稿核验否决的草稿）：作为过程步骤留档——不再静默丢弃，
        // 被否决的标「✗ 已否决 · 未执行」，用户展开可见原文。
        reply.trace = reply.trace || []
        reply.trace.push({ kind: 'text', content: evt.content || '', rejected: !!evt.rejected })
      } else if (evt.type === 'compile_started') {
        // 对话触发了编制/批量生成：启动进度轮询并刷新目录树
        startPolling()
        loadTree()
      } else if (evt.type === 'toc_updated') {
        // AI 通过 edit_toc 调整了目录树：刷新目录
        loadTree()
      } else if (evt.type === 'toc_refresh') {
        // AI 后台批量生成思路中：实时刷新目录树（思路蓝点逐节点点亮）
        loadTree()
      } else if (evt.type === 'suggestions') {
        reply.suggestions = evt.items || []
        scrollBottom()
      } else if (evt.type === 'chapter_updated') {
        // 对话写入/改写了章节：编辑器自动切到该章（用户无需手动找）
        switchToNode(evt.node_id)
        loadTree()
      }
    }
  )
}
async function clearChat() {
  try {
    await ElMessageBox.confirm('清空后对话历史与上下文将全部删除（不影响已生成内容），确定？', '清空对话', {
      type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消',
    })
    await clearChatHistory(Number(props.projectId))
    messages.value = []
    ElMessage.success('对话已清空')
  } catch { /* 用户取消 */ }
}

function stopChat() {
  // 中止当前对话：后端会落库已生成的部分回复
  if (chatAbortCtrl) chatAbortCtrl.abort()
  chatBusy.value = false
  chatAbortCtrl = null
}
// 自动切换编辑器到指定章节（对话写入章节时调用，用户无需手动找）
const tocTreeRef = ref(null)
async function switchToNode(nodeId) {
  await loadTree()
  const node = findNodeById(tocChapters.value, nodeId)
  // 与目录点击一致：切到别的章即放弃流式渲染；切到生成中的章不拉库（等生成完成）
  if (genNodeId.value && genVisible.value && nodeId !== genNodeId.value) genVisible.value = false
  activeNodeId.value = nodeId
  activeChapterTitle.value = node?.title || ''
  activeOutline.value = node?.outline || null
  resetRefPages()
  activeNodeStatus.value = node?.status || null
  if (nodeId !== genNodeId.value) await loadContent(nodeId)
  nextTick(() => tocTreeRef.value?.setCurrentKey(nodeId))
}
function scrollBottom() { nextTick(() => msgBox.value?.scrollTo({ top: msgBox.value.scrollHeight })) }

// 是否贴近消息区底部（差 ≤60px 视为贴底）：贴底才自动跟随流式输出
function isNearBottom() {
  const el = msgBox.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 60
}
// 流式/进度更新时：贴底 → 跟随；用户停在历史 → 不拉底，亮"回到最新"
function followStream() {
  if (isNearBottom()) { showJump.value = false; scrollBottom() }
  else showJump.value = true
}
function jumpToLatest() {
  showJump.value = false
  scrollBottom()
}

// 快捷追问：点击 chip 直接作为新消息发送
function askFollowup(text) {
  if (chatBusy.value || !text) return
  input.value = text
  send()
}

// 助手回复 markdown 渲染（AI 输出的加粗/列表按格式展示）
import { marked } from 'marked'
// 遗留标记（[待补充]/[图片占位]）红底
function mdHighlight(html) {
  if (!html) return html
  return html
    .replace(/\[待补充\]|\(待补充\)|【待补充】/g, '<span class="md-tdz">$&</span>')
    .replace(/\[图片占位[^\]]*\]/g, '<span class="md-tdz">$&</span>')
}
function mdRender(text) {
  if (!text) return ''
  try {
    return mdHighlight(marked.parse(text))
  } catch {
    return text
  }
}

// 工具调用轨迹展示：工具名中文 + 参数摘要
const TOOL_LABELS = {
  rewrite_section: '改写章节',
  write_chapter: '生成章节正文',
  write_batch: '批量生成正文',
  start_compile: '发起编制',
  edit_toc: '调整目录',
  regenerate_outline: '重写编写思路',
  generate_outlines: '批量生成思路',
  read_section: '读取章节',
  get_chapter_tree: '读取目录树',
  find_chapters: '检索章节',
  search_knowledge_pages: '检索知识页',
  list_facts: '查询事实表',
  list_tender_requirements: '查询招标要求',
  search_experiences: '查询经验库',
  list_project_files: '读取项目文件',
  search_project_files: '检索项目文件',
}
function toolLabel(name) {
  return TOOL_LABELS[name] || name
}
function traceArgsText(t) {
  const a = t.args || {}
  const keys = Object.keys(a)
  if (!keys.length) return ''
  return keys.map((k) => `${k}=${a[k]}`).join('  ')
}

// ---------- 执行过程（步骤时间线）：模型叙述 💬 + 工具调用 ⚙ 按发生顺序留档 ----------
// 历史消息里的旧 trace 是纯工具列表（无 kind）→ 按工具步骤渲染。
function isToolStep(t) {
  return (t?.kind || 'tool') === 'tool'
}
// 叙述步骤折叠态显示的摘要（首 42 字）
function stepSummary(t) {
  const s = String(t?.content || '').replace(/\s+/g, ' ').trim()
  return s.length > 42 ? s.slice(0, 42) + '…' : s
}

// 对话消息时间显示：兼容历史接口返回的 "YYYY-MM-DD HH:MM:SS" 字符串与实时消息的毫秒时间戳
function fmtTime(t) {
  if (!t) return ''
  let d
  if (typeof t === 'number') d = new Date(t)
  else {
    const norm = String(t).trim().replace(' ', 'T')
    d = new Date(norm)
  }
  if (isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const hasMoreChat = ref(false)      // 是否还有更早历史可加载
const loadingOlder = ref(false)

async function loadChatHistory() {
  try {
    const { data } = await getChatHistory(props.projectId)  // 默认最近 40 条（20问20答）
    if (data.messages?.length) {
      messages.value = data.messages.map(m => ({ ...m }))
      hasMoreChat.value = !!data.has_more
      // 打开工作台默认展示最新消息（滚动到底部）
      nextTick(() => msgBox.value?.scrollTo({ top: msgBox.value.scrollHeight }))
    }
  } catch { /* 后端未就绪时忽略 */ }
}

// 消息区滚动：接近顶部且有更早历史 → 触发懒加载
function onChatScroll() {
  const box = msgBox.value
  if (!box || loadingOlder.value || !hasMoreChat.value) return
  if (box.scrollTop < 60) loadOlderChat()
}
async function loadOlderChat() {
  if (loadingOlder.value || !hasMoreChat.value) return
  const oldest = messages.value[0]
  if (!oldest || oldest.id == null) return
  loadingOlder.value = true
  const box = msgBox.value
  const prevHeight = box ? box.scrollHeight : 0
  const prevTop = box ? box.scrollTop : 0
  try {
    const { data } = await getChatHistory(props.projectId, { before_id: oldest.id })
    if (data.messages?.length) {
      messages.value = [...data.messages.map(m => ({ ...m })), ...messages.value]
    }
    hasMoreChat.value = !!data.has_more
    // 把滚动位置补偿到"加载前看到的同一内容处"，避免列表上插导致跳动
    nextTick(() => {
      const b = msgBox.value
      if (b) b.scrollTop = prevTop + (b.scrollHeight - prevHeight)
    })
  } catch { /* ignore */ } finally {
    loadingOlder.value = false
  }
}

onMounted(() => { loadFiles(); loadTree(); refreshPendingFacts(); loadChatHistory(); loadSelectedLot(); recoverOutlineTasks() })
onUnmounted(() => {
  stopPolling(); if (pollRowsTimer) { clearInterval(pollRowsTimer); pollRowsTimer = null }
  if (chatAbortCtrl) chatAbortCtrl.abort()  // 离开页面中止对话（部分回复后端照常落库）
})
</script>

<style scoped>
.workspace { height: 100%; display: flex; flex-direction: column; background: var(--ink-950); }

.ws-topbar {
  display: flex; align-items: center; gap: 14px; height: 52px; padding: 0 16px;
  background: var(--ink-900); border-bottom: 1px solid var(--ink-line); flex-shrink: 0;
}
.back-btn {
  display: inline-flex; align-items: center; gap: 5px; background: none;
  border: 1px solid var(--ink-line-strong); border-radius: var(--radius-s);
  color: var(--ink-text-2); font-size: 12px; padding: 5px 10px; cursor: pointer; transition: all 0.15s;
}
.back-btn:hover { color: var(--ink-text); border-color: var(--ink-text-3); }
.ws-title { display: flex; align-items: baseline; gap: 8px; flex-shrink: 0; white-space: nowrap; }
.ws-name { font-size: 14px; font-weight: 700; color: var(--ink-text); }
.ws-id { font-size: 11px; color: var(--ink-text-3); }
.spacer { flex: 1; }
.ghost-btn {
  display: inline-flex; align-items: center; gap: 5px; background: none;
  border: 1px solid var(--ink-line-strong); border-radius: var(--radius-s);
  color: var(--ink-text-2); font-size: 12px; padding: 5px 12px; cursor: pointer; transition: all 0.15s;
}
.ghost-btn:hover { color: var(--ink-text); border-color: var(--ink-text-3); }

.stage-track { display: flex; align-items: center; gap: 2px; margin-left: 20px; }
.stage-node { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: var(--radius-s); }
.stage-node + .stage-node { position: relative; margin-left: 10px; }
.stage-node + .stage-node::before { content: ''; position: absolute; left: -12px; top: 50%; width: 8px; height: 1px; background: var(--ink-line-strong); }
.stage-dot {
  width: 18px; height: 18px; border-radius: 50%; font-size: 10px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center; background: var(--ink-700); color: var(--ink-text-3);
}
.stage-label { font-size: 11px; color: var(--ink-text-3); white-space: nowrap; }

.lot-badge {
  margin-left: 10px; padding: 3px 10px; border-radius: 999px;
  background: rgba(59, 130, 246, 0.18); border: 1px solid rgba(96, 165, 250, 0.45);
  color: #93c5fd; font-size: 11px; font-weight: 700; white-space: nowrap; cursor: default;
}
.stage-node.on .stage-dot { background: var(--amber); color: #fff; box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.25); }
.stage-node.on .stage-label { color: var(--ink-text); font-weight: 600; }
.stage-node.done .stage-dot { background: var(--green); color: #fff; }
.stage-node.done .stage-label { color: var(--ink-text-2); }

.ws-body { flex: 1; display: flex; min-height: 0; }

.sidebar { width: 400px; flex-shrink: 0; display: flex; flex-direction: column; background: var(--ink-900); border-right: 1px solid var(--ink-line); }
.side-block { display: flex; flex-direction: column; min-height: 0; }
.files-block { flex: 0 0 auto; max-height: 40%; border-bottom: 1px solid var(--ink-line); }
.chat-block { flex: 1; position: relative; }
.chat-jump {
  position: absolute; right: 16px; bottom: 64px; z-index: 5;
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 12px; border-radius: 999px; cursor: pointer;
  background: var(--brand); color: #fff; border: none;
  font-size: 12px; box-shadow: 0 3px 10px rgba(0,0,0,0.35);
  transition: all 0.15s;
}
.chat-jump:hover { background: var(--brand-deep); }
.side-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px 8px; }
.chat-clear-btn {
  border: none; background: none; color: var(--ink-text-3); font-size: 11px; cursor: pointer;
  padding: 2px 8px; border-radius: var(--radius-s);
}
.chat-clear-btn:hover { color: var(--red); background: var(--ink-800); }

/* 对话工作状态行：spinner + 文案 + 呼吸点 */
.chat-status { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
.cs-spinner {
  width: 12px; height: 12px; flex-shrink: 0; border-radius: 50%;
  border: 2px solid var(--ink-line-strong); border-top-color: var(--brand);
  animation: cs-spin 0.8s linear infinite;
}
.cs-text { font-size: 13px; color: var(--ink-text-2); }
.cs-dots::after { content: '…'; animation: cs-dots 1.4s steps(4) infinite; }
@keyframes cs-spin { to { transform: rotate(360deg); } }
@keyframes cs-dots { 0% { content: ''; } 25% { content: '.'; } 50% { content: '..'; } 75% { content: '...'; } }
/* 流式输出中的末尾光标 */
.cs-cursor {
  display: inline-block; width: 7px; height: 13px; margin-left: 2px; vertical-align: -2px;
  background: var(--brand); animation: cs-blink 0.9s steps(2) infinite;
}
@keyframes cs-blink { 50% { opacity: 0; } }
.side-title { font-size: 12px; font-weight: 700; color: var(--ink-text-3); letter-spacing: 0.1em; text-transform: uppercase; }
.upload-btn { background: none; border: none; color: var(--cyan); font-size: 12px; cursor: pointer; padding: 2px 4px; }
.upload-btn:hover { color: #6cc4dd; }

/* 事实抽取进度卡 */
.file-prog { margin-top: 4px; }
.file-prog .fp-line { display: flex; align-items: center; gap: 6px; }
.file-prog .fp-bar { flex: 1; height: 3px; background: var(--ink-700); border-radius: 2px; overflow: hidden; }
.file-prog .fp-fill { display: block; height: 100%; background: linear-gradient(90deg, var(--brand), var(--cyan)); transition: width 0.4s ease-out; }
.file-prog .fp-pct { font-size: 10px; color: var(--cyan); min-width: 32px; text-align: right; }
.file-prog .fp-detail { font-size: 10px; color: var(--ink-text-3); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.file-prog.hint { font-size: 10px; color: var(--ink-text-3); }

.fact-badge {
  min-width: 16px; height: 16px; padding: 0 4px; margin-left: 6px; border-radius: 8px;
  background: var(--amber); color: #fff; font-size: 10px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
}

.file-list { overflow-y: auto; padding: 0 8px 10px; }
.file-empty { padding: 18px 12px; text-align: center; font-size: 12px; color: var(--ink-text-2); line-height: 1.7; }
.file-empty span { font-size: 11px; color: var(--ink-text-3); }
.file-item { display: flex; align-items: center; gap: 10px; padding: 7px 8px; border-radius: var(--radius-s); transition: background 0.15s; }
.file-item:hover { background: var(--ink-800); }
.fs-btn {
  flex-shrink: 0; width: 22px; height: 22px; border: 1px solid var(--ink-line-strong);
  border-radius: 5px; background: transparent; color: var(--ink-text-3);
  font-size: 11px; cursor: pointer; transition: all 0.15s;
  display: inline-flex; align-items: center; justify-content: center;
}
.fs-btn:hover { color: var(--brand); border-color: var(--brand); background: var(--ink-800); }
.fs-btn.del:hover { color: var(--red); border-color: var(--red); background: rgba(244, 67, 54, 0.08); }
.group-head {
  display: flex; align-items: center; gap: 6px; padding: 7px 8px 3px;
  font-size: 10px; font-weight: 700; color: var(--ink-text-3); letter-spacing: 0.08em;
}
.group-head .num { color: var(--ink-text-3); }
.file-icon {
  width: 30px; height: 30px; flex-shrink: 0; border-radius: 6px; background: var(--ink-700); color: var(--ink-text-2);
  font-size: 9px; font-weight: 700; display: flex; align-items: center; justify-content: center; font-family: var(--font-num);
}
.file-info { min-width: 0; flex: 1; }
.file-name { font-size: 13px; color: var(--ink-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.file-meta { display: flex; gap: 8px; margin-top: 2px; font-size: 11px; }
.file-cat { color: var(--ink-text-3); }
.file-status { color: var(--green); }
.file-status.parsing, .file-status.pending { color: var(--amber); }
.file-status.failed { color: var(--red); }

.progress-card { margin: 4px 12px 8px; padding: 12px 14px; background: var(--ink-800); border: 1px solid var(--ink-line-strong); border-radius: var(--radius-m); }
.progress-card.confirm_toc, .progress-card.confirm_outline { border-color: rgba(217, 119, 6, 0.5); }
.progress-card.done { border-color: rgba(14, 159, 110, 0.5); }
.progress-card.failed { border-color: rgba(217, 72, 72, 0.5); }
.pc-head { display: flex; align-items: center; justify-content: space-between; }
.pc-stage { font-size: 12px; font-weight: 700; color: var(--ink-text); }
.pc-count { font-size: 11px; color: var(--amber); font-weight: 700; }
.pc-bar { height: 3px; background: var(--ink-700); border-radius: 2px; margin-top: 8px; overflow: hidden; }
.pc-bar-fill { height: 100%; background: linear-gradient(90deg, var(--brand), var(--cyan)); border-radius: 2px; transition: width 0.4s ease-out; }
.pc-body { margin-top: 8px; }
.pc-tip { font-size: 11px; color: var(--ink-text-2); line-height: 1.6; margin: 0 0 8px; }
.pc-err { font-size: 11px; color: #ff8585; margin: 0; }
.pc-ok { font-size: 11px; color: #4ade80; margin: 0; }
.pc-btn { width: 100%; }

.chat-messages { flex: 1; overflow-y: auto; padding: 10px 14px; }
.chat-empty { font-size: 12px; color: var(--ink-text-2); text-align: center; line-height: 1.8; padding: 20px 0; }
.chat-more {
  text-align: center; font-size: 12px; color: var(--ink-text-2); padding: 8px 0 2px;
  cursor: pointer; user-select: none;
}
.chat-more:hover { color: var(--brand); }
.chat-more.loading { cursor: default; opacity: .7; }
.msg { display: flex; flex-wrap: wrap; margin-bottom: 10px; }
.msg.user { justify-content: flex-end; }
.bubble {
  max-width: 88%; padding: 9px 12px; border-radius: 9px; font-size: 14px; line-height: 1.7;
  white-space: pre-wrap; word-break: break-word; background: var(--ink-800); color: var(--ink-text); border: 1px solid var(--ink-line);
}
.msg.user .bubble { background: var(--brand); border-color: var(--brand); color: #fff; }
.msg-time { margin-top: 4px; font-size: 11px; color: var(--ink-text-3); line-height: 1; }
.msg.user .msg-time { text-align: right; color: rgba(255, 255, 255, 0.7); }
/* 助手回复 markdown 排版（深色气泡内；覆盖 pre-wrap，否则标签间换行渲染成空行） */
.bubble-md { white-space: normal; }
.bubble-md :deep(p) { margin: 2px 0; line-height: 1.7; }
.bubble-md :deep(.md-tdz) { background: rgba(220, 38, 38, 0.2); color: #ff9b9b; padding: 0 3px; border-radius: 3px; font-weight: 600; }
.bubble-md :deep(p:empty) { display: none; }
.bubble-md :deep(ul), .bubble-md :deep(ol) { margin: 2px 0; padding-left: 16px; line-height: 1.7; }
.bubble-md :deep(li) { margin: 1px 0; }
.bubble-md :deep(strong) { color: var(--ink-text); font-weight: 700; }
.bubble-md :deep(code) {
  font-family: var(--font-num); font-size: 12px; padding: 1px 5px;
  background: var(--ink-800); border-radius: 4px;
}
/* 气泡内表格：深色主题下清晰可见的边框与斑马纹 */
.bubble-md :deep(table) {
  border-collapse: collapse; width: 100%; margin: 6px 0;
  font-size: 12px; display: block; overflow-x: auto; white-space: nowrap;
}
.bubble-md :deep(th), .bubble-md :deep(td) {
  border: 1px solid var(--ink-line-strong); padding: 5px 9px; text-align: left;
}
.bubble-md :deep(th) { background: var(--ink-700); font-weight: 700; color: var(--ink-text); }
.bubble-md :deep(tr:nth-child(even) td) { background: rgba(255, 255, 255, 0.04); }
/* 图文混排：原文图片在气泡内自适应展示 */
.bubble-md :deep(img) {
  max-width: 100%; height: auto; display: block; margin: 8px 0;
  border: 1px solid var(--ink-line-strong); border-radius: 6px; background: #fff;
}
/* 执行过程（步骤时间线，置于卡片顶部）：模型回复 💬 + 工具调用 ⚙，整体可收起、每步可展开 */
.chat-steps { margin-bottom: 8px; border-bottom: 1px dashed var(--ink-line-strong); padding-bottom: 6px; }
.steps-head {
  cursor: pointer; font-size: 12px; color: var(--ink-text-2); user-select: none;
  display: flex; align-items: center; gap: 4px;
}
.steps-head:hover { color: var(--ink-text); }
.sh-caret { display: inline-block; transition: transform 0.15s; font-size: 11px; }
.sh-caret.open { transform: rotate(90deg); }
.step-list { margin: 6px 0 2px; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 3px; }
.step-item {
  font-size: 12px; color: var(--ink-text-2); line-height: 1.6;
  padding: 3px 5px; border-radius: 4px; background: rgba(255, 255, 255, 0.04);
}
.step-item.err { background: rgba(220, 38, 38, 0.12); }
/* 被核验否决的步骤：不用降透明度（深蓝底上会糊成一片），改用浅红底 + 亮红字保证清晰可辨 */
.step-item.rejected { background: rgba(220, 38, 38, 0.12); }
.si-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; cursor: pointer; }
.si-caret { display: inline-block; color: var(--ink-text-3); font-size: 10px; transition: transform 0.15s; }
.si-caret.open { transform: rotate(90deg); }
.si-icon { font-size: 11px; }
.si-name { font-weight: 700; color: var(--ink-text); }
.step-item.err .si-name { color: #ff9b9b; }
.si-sum { color: var(--ink-text-2); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 380px; }
.si-args { color: var(--ink-text-2); font-size: 11px; word-break: break-all; }
.si-state.ok { color: #4ade80; }
.si-state { color: #ff9b9b; }
.si-badge {
  font-size: 11px; color: #ffc9c9; background: rgba(220, 38, 38, 0.22);
  border: 1px solid rgba(255, 155, 155, 0.5); border-radius: 3px; padding: 0 5px;
}
.si-body { margin: 4px 0 2px 14px; }
.si-note { color: #ff9b9b; font-size: 11px; }
.si-note.ok-note { color: var(--ink-text-2); }
/* 叙述正文：折叠态才显示；被否决的整段划删除线 + 亮红字（深底上依然清晰） */
.si-text {
  font-size: 12px; color: var(--ink-text-2); white-space: pre-wrap; word-break: break-word;
  border-left: 2px solid var(--ink-line-strong); padding-left: 8px;
}
.step-item.rejected .si-text { text-decoration: line-through; color: #ffb4b4; }
.chat-input { padding: 10px 12px; border-top: 1px solid var(--ink-line); position: relative; }
.chat-send {
  position: absolute; right: 20px; bottom: 16px; width: 30px; height: 30px;
  border: 1px solid var(--ink-line-strong); border-radius: 8px; background: var(--ink-900);
  color: var(--ink-text-2); font-size: 13px; cursor: pointer; transition: all 0.15s;
  display: inline-flex; align-items: center; justify-content: center;
}
.chat-send:hover { color: var(--brand); border-color: var(--brand); }
.chat-send.stop { color: #fff; background: var(--red); border-color: var(--red); }

/* 快捷追问 chips（独立成行，跟在气泡下方） */
.chat-chips {
  flex-basis: 100%;
  display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 2px;
}
.chat-chip {
  font-size: 12px; padding: 4px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--ink-line-strong); background: var(--ink-900); color: var(--ink-text-2);
  transition: all 0.15s; line-height: 1.4; white-space: nowrap;
}
.chat-chip:hover { border-color: var(--brand); color: var(--brand); background: var(--ink-800); }
.chat-input textarea {
  width: 100%; resize: none; border: 1px solid var(--ink-line-strong); background: var(--ink-800); color: var(--ink-text);
  border-radius: var(--radius-s); padding: 8px 10px; font-size: 13px; font-family: inherit; outline: none; line-height: 1.5;
}
.chat-input textarea::placeholder { color: var(--ink-text-3); }
.chat-input textarea:focus { border-color: var(--brand); }

.editor-area { flex: 1; display: flex; min-width: 0; background: var(--paper); border-radius: 12px 0 0 0; overflow: hidden; }

.toc-panel { width: 300px; flex-shrink: 0; border-right: 1px solid var(--line); display: flex; flex-direction: column; background: var(--card); }
.toc-header {
  display: flex; align-items: center; justify-content: space-between; gap: 6px; white-space: nowrap;
  padding: 9px 10px; font-size: 12px; font-weight: 700; color: var(--text-3);
  letter-spacing: 0.03em; border-bottom: 1px solid var(--line);
}
.toc-head-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.toc-count { font-weight: 600; letter-spacing: 0; color: var(--text-3); font-size: 11px; }
.toc-batch-btn {
  border: 1px solid var(--line-strong); background: var(--brand-soft); color: var(--brand);
  font-size: 11px; line-height: 1; padding: 3px 8px; border-radius: 999px; cursor: pointer; transition: all 0.15s;
  letter-spacing: 0; white-space: nowrap;
}
.toc-batch-btn:hover { background: var(--brand); color: #fff; border-color: var(--brand); }
.toc-batch-btn.ghost { background: transparent; color: var(--text-3); border-color: var(--line); }
.toc-batch-btn.ghost:hover { color: var(--green); border-color: var(--green); background: transparent; }
.toc-batch-btn:disabled { opacity: .6; cursor: default; }
.toc-unw-num { margin-left: 2px; font-style: normal; color: var(--amber); font-weight: 700; font-size: 11px; }
.toc-batch-btn.ghost:hover .toc-unw-num { color: var(--green); }

/* 目录批量编辑弹窗 */
.tb-body { }
.tb-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.tb-plan { margin-top: 14px; border: 1px solid var(--line); border-radius: var(--radius-m); padding: 12px 14px; background: var(--paper-warm); }
.tb-plan.warned { border-color: #f0c36b; background: #fdf8ee; }
.tb-plan.danger { border-color: #f3c3c3; background: #fdf5f5; }
.tb-plan-head { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-2); margin-bottom: 8px; }
.tb-stat b { color: var(--text); font-weight: 700; }
.tb-warn { font-size: 12px; color: #8a5a12; background: #fdf3dd; border: 1px solid #f0c36b; border-radius: var(--radius-s); padding: 8px 12px; margin-bottom: 8px; }
.tb-warn.danger { color: var(--red); background: #fff1f1; border-color: #f3c3c3; }
.tb-del-list { max-height: 200px; overflow-y: auto; }
.tb-del { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--text-2); padding: 4px 6px; border-radius: 4px; }
.tb-del.has { color: var(--red); }
.tb-del-has { font-size: 10px; padding: 1px 7px; background: #ffe3e3; border-radius: 3px; flex-shrink: 0; }
.tb-nodel { font-size: 12px; color: var(--green); }
.toc-legend {
  display: flex; align-items: center; gap: 10px; padding: 6px 14px;
  border-bottom: 1px solid var(--line); background: var(--paper-warm);
  font-size: 11px; color: var(--text-3);
}
.lg { display: inline-flex; align-items: center; gap: 4px; }
.lg .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.st0 { background: var(--text-3); }
.dot.st-thought { background: var(--brand); }
.dot.st-gen { background: var(--amber); }
.dot.st-live { background: var(--amber); animation: ns-pulse 1.1s ease-in-out infinite; }
.dot.st-conf { background: var(--green); }

.toc-empty { padding: 36px 16px; text-align: center; font-size: 13px; color: var(--text-2); }
.toc-empty-icon { font-size: 24px; color: var(--line-strong); margin-bottom: 8px; }
.toc-empty p { margin: 4px 0; }
.toc-empty-sub { font-size: 12px; color: var(--text-3); line-height: 1.7; }
.toc-tree { flex: 1; overflow-y: auto; padding: 6px 8px 12px; }
.toc-tree :deep(.el-tree-node__content) { height: 30px; min-width: 0; border-radius: var(--radius-s); }
.toc-tree :deep(.el-tree-node__expand-icon) { font-size: 12px; }
.toc-tree :deep(.el-tree-node.is-current > .el-tree-node__content) { background: var(--brand-soft); }
.toc-tree :deep(.el-tree-node.is-current > .el-tree-node__content .node-title) { color: var(--brand); font-weight: 600; }
.toc-tree :deep(.el-tree-node__content:hover) { background: var(--paper-warm); }
.toc-tree :deep(.el-tree-node.is-current > .el-tree-node__content:hover) { background: var(--brand-soft); }

.tree-node {
  display: flex; align-items: center; gap: 6px;
  width: 100%; min-width: 0; overflow: hidden;   /* 防长标题把操作按钮挤出边界 */
}
.node-status { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; background: var(--text-3); }
.node-status.thought { background: var(--brand); }
.node-status.generated { background: var(--amber); }
.node-status.confirmed { background: var(--green); }
/* 生成中：状态点脉动（切换章节后仍能一眼看出哪一章在写） */
.node-status.generating { background: var(--amber); animation: ns-pulse 1.1s ease-in-out infinite; }
@keyframes ns-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.node-gen {
  display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
  font-size: 11px; color: #b45309; background: #fdf3e0; border-radius: 9px;
  padding: 1px 7px; font-family: var(--font-num); cursor: pointer;
}
.node-gen:hover { background: #fae4c3; }
.node-gen .ng-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--amber);
  animation: ns-pulse 1.1s ease-in-out infinite;
}
/* 胶囊里的"停止"：不占额外宽度，hover 才强调，避免长标题被挤没 */
.node-gen .ng-stop {
  font-style: normal; color: #9a6a1c; border-left: 1px solid #eed9b6;
  padding-left: 5px; margin-left: 1px;
}
.node-gen:hover .ng-stop { color: #d64545; border-left-color: #f0c4c4; }
.node-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; color: var(--text); }
.node-actions { display: none; gap: 2px; flex-shrink: 0; }
.tree-node:hover .node-actions { display: inline-flex; }
.node-actions button {
  border: none; background: transparent; color: var(--text-3); font-size: 12px;
  width: 20px; height: 20px; border-radius: 4px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center;
}
.tree-node:hover .node-actions button { background: var(--paper-warm); color: var(--text-2); }
.node-actions button:hover { background: var(--brand) !important; color: #fff !important; }

.content-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--card); position: relative; }
/* 章节思路生成遮罩：铺满整个编辑区，禁止编辑该章内容 */
.outline-gen-mask {
  position: absolute; inset: 0; z-index: 20; background: rgba(244, 246, 250, 0.86);
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px;
  color: var(--text-2);
}
.ogm-spinner {
  width: 30px; height: 30px; border-radius: 50%;
  border: 3px solid #d4dae3; border-top-color: var(--brand);
  animation: ogm-spin 0.9s linear infinite;
}
@keyframes ogm-spin { to { transform: rotate(360deg); } }
.outline-gen-mask p { margin: 0; font-size: 15px; font-weight: 600; color: var(--text); }
.outline-gen-mask .ogm-sub { font-size: 13px; font-weight: 400; color: var(--text-3); }
.content-header { padding: 12px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: baseline; gap: 10px; }
.content-title { font-size: 14px; font-weight: 700; }
.content-hint { font-size: 11px; color: var(--text-3); }
.spacer-inline { flex: 1; }
.content-saved { font-size: 11px; color: var(--text-3); font-family: var(--font-num); }
.content-saved.dirty { color: var(--brand); font-weight: 600; }

/* 知识页详情弹窗 */
.kp-dlg .kp-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
.kp-dlg .kp-field label { display: block; font-size: 11px; font-weight: 600; color: var(--text-3); margin-bottom: 4px; }
.kp-dlg .kp-val { font-size: 13px; color: var(--text); line-height: 1.7; }
.kp-dlg .kp-list { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.8; }
.kp-dlg .kp-chip { display: inline-block; font-size: 11px; padding: 3px 9px; margin: 0 6px 6px 0; background: var(--paper-warm); border-radius: 999px; }
.kp-dlg .kp-chip.clickable { cursor: pointer; border: 1px solid transparent; transition: all 0.12s; }
.kp-dlg .kp-chip.clickable:hover { border-color: var(--brand); color: var(--brand); background: var(--brand-soft); }
.kp-dlg .kp-chip-sub { font-size: 10px; color: var(--text-3); }
/* 历史内容参考渲染（markdown/HTML 表格还原） */
.kp-dlg .kp-ref { max-height: 420px; overflow-y: auto; word-break: break-word; background: var(--paper-warm); padding: 10px 12px; border-radius: var(--radius-s); font-size: 13px; line-height: 1.85; }
.kp-dlg .kp-ref :deep(table), .kp-preview .kp-table :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.kp-dlg .kp-ref :deep(th), .kp-dlg .kp-ref :deep(td), .kp-preview .kp-table :deep(th), .kp-preview .kp-table :deep(td) { border: 1px solid var(--line-strong); padding: 5px 9px; font-size: 12px; }
.kp-dlg .kp-ref :deep(th), .kp-preview .kp-table :deep(th) { background: #eef2f8; font-weight: 600; }
.kp-dlg .kp-ref :deep(p), .kp-preview .kp-table :deep(p) { margin: 0 0 6px; }
.kp-dlg .kp-ref :deep(img) { max-width: 100%; }
/* 图片网格 */
.kp-dlg .kp-imgs { display: flex; flex-wrap: wrap; gap: 10px; }
.kp-dlg .kp-img { width: 120px; border: 1px solid var(--line); border-radius: var(--radius-s); overflow: hidden; background: var(--card); }
.kp-dlg .kp-img.clickable { cursor: pointer; }
.kp-dlg .kp-img.clickable:hover { border-color: var(--brand); }
.kp-dlg .kp-img-real { width: 100%; height: 84px; object-fit: cover; display: block; background: var(--paper-warm); }
.kp-dlg .kp-img-ph { height: 84px; display: flex; align-items: center; justify-content: center; font-size: 24px; color: var(--text-3); }
.kp-dlg .kp-img-cap { font-size: 10px; padding: 4px 6px; line-height: 1.4; color: var(--text-2); }
/* 表格/图片放大弹窗 */
.kp-preview .kp-desc { font-size: 12px; line-height: 1.7; color: var(--text-2); margin-bottom: 8px; }
.kp-preview .kp-cols { background: var(--paper-warm); border-radius: var(--radius-s); padding: 8px 10px; margin-bottom: 8px; }
.kp-preview .kp-cols-title { font-size: 11px; font-weight: 600; color: var(--text-2); margin-bottom: 4px; }
.kp-preview .kp-col-item { font-size: 12px; line-height: 1.7; }
.kp-preview .kp-table { overflow-x: auto; }
.kp-preview .kp-bigimg { max-width: 100%; display: block; margin: 0 auto; }
.kp-preview .kp-img-empty { text-align: center; color: var(--text-3); padding: 30px 0; }
.kp-preview .kp-img-txt { font-size: 13px; line-height: 1.7; margin-top: 10px; }
.kp-preview .kp-empty { color: var(--text-3); }

/* 本章编写思路条（折叠/可编辑） */
.outline-strip { border-bottom: 1px solid #d8e4f8; background: var(--brand-soft); }
.outline-strip.editing { background: #fffbe6; border-bottom-color: #e6d68a; }
.os-toggle-row { display: flex; align-items: center; gap: 8px; padding: 0 16px; }
.os-toggle {
  display: flex; align-items: center; gap: 7px; flex: 1; text-align: left;
  border: none; background: none; padding: 8px 0; cursor: pointer;
  font-size: 14px; font-weight: 700; color: var(--brand);
}
.os-toggle:hover { opacity: .85; }
.os-head-actions { display: flex; gap: 6px; flex-shrink: 0; }
.os-mini {
  font-size: 12px; padding: 3px 11px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--line-strong); background: #fff; color: var(--text-2); transition: all .15s;
}
.os-mini:hover { color: var(--brand); border-color: var(--brand); }
.os-mini.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
.os-mini.primary:hover { opacity: .9; }
.os-mini:disabled { opacity: .6; cursor: default; }
.os-edit { padding: 4px 16px 14px; }
.os-edit label { display: block; font-size: 12px; font-weight: 600; color: var(--text-2); margin: 8px 0 5px; }
.os-edit label:first-child { margin-top: 0; }
.os-edit .os-check { display: inline-flex; align-items: center; margin-right: 16px; font-size: 13px; color: var(--text-2); }
.os-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); }
.os-arrow { font-size: 11px; color: var(--text-3); }
.os-body { padding: 2px 16px 12px; }
.os-thinking { margin: 0 0 8px; font-size: 14px; color: var(--text); line-height: 1.85; }
.os-kps { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.os-kp {
  font-size: 12px; padding: 3px 10px; background: #fff; border: 1px solid #d8e4f8;
  border-radius: 999px; color: var(--text-2);
}
.os-meta { display: flex; gap: 6px; align-items: center; }
.os-tag { font-size: 11px; padding: 2px 8px; border-radius: 3px; background: #fff; color: var(--text-3); border: 1px solid var(--line); }
.os-tag.os-ref { cursor: pointer; color: var(--brand); border-color: var(--brand-soft); }
.os-tag.os-ref:hover { background: var(--brand-soft); }
.os-pages { margin-top: 8px; border: 1px solid var(--line); border-radius: var(--radius-s); overflow: hidden; }
.os-page { display: flex; align-items: baseline; gap: 8px; padding: 6px 10px; font-size: 13px; background: var(--card); border-bottom: 1px solid var(--line); }
.os-page:last-child { border-bottom: none; }
.os-page.clickable { cursor: pointer; transition: background 0.12s; }
.os-page.clickable:hover { background: var(--brand-soft); }
.os-page .osp-title { color: var(--text); }
.os-page .osp-doc { font-size: 11px; color: var(--text-3); margin-left: auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 45%; }
.os-page.empty { color: var(--text-3); }

.ai-mark {
  display: flex; align-items: center; gap: 8px; padding: 8px 16px;
  background: var(--amber-soft); border-bottom: 1px solid #f0d9ac; font-size: 13px;
}
.ai-mark.streaming { background: var(--brand-soft); border-bottom-color: #c4d6f5; }
.ai-mark.streaming .ai-mark-tag { background: var(--brand); }
.ai-mark.streaming .ai-mark-text { color: var(--brand); }
.ai-mark-tag { background: var(--amber); color: #fff; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 3px; }
.ai-mark-text { color: #8a5a12; }
.ai-btn { border: 1px solid #d9b26a; background: #fff; color: #7a4e0d; font-size: 12px; padding: 4px 12px; border-radius: var(--radius-s); cursor: pointer; transition: all 0.15s; }
.ai-btn:hover { border-color: var(--amber); }
.ai-btn.plain { background: rgba(255, 255, 255, 0.7); color: #7a4e0d; }
.ai-btn.plain:hover { background: #fff; }
.ai-btn.confirm { background: var(--green); border-color: var(--green); color: #fff; }
.ai-btn.confirm:hover { background: #0a7d57; }

/* 本章取材来源弹窗 */
.used-dlg .el-dialog__body { max-height: 70vh; overflow-y: auto; }
.used-sec { margin-bottom: 16px; }
.us-title { font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 7px; }
.us-item {
  padding: 7px 11px; margin-bottom: 6px; border-left: 3px solid var(--brand-soft);
  background: var(--paper-warm); border-radius: var(--radius-s);
}
.us-head { display: flex; align-items: center; gap: 7px; margin-bottom: 3px; }
.us-tag {
  font-size: 11px; font-weight: 700; color: #fff; background: var(--amber);
  padding: 1px 7px; border-radius: 3px;
}
.us-chapter { font-size: 12px; color: var(--text-3); }
.us-text { font-size: 13px; color: var(--text-2); line-height: 1.75; word-break: break-word; }
.us-item.clickable { cursor: pointer; transition: background 0.12s, border-color 0.12s; }
.us-item.clickable:hover { background: var(--brand-soft); border-left-color: var(--brand); }
/* 取材条目详情（二级弹窗）：markdown 渲染（**浅色**主题，表格/列表/加粗都要出效果） */
.used-detail-dlg .el-dialog__body { max-height: 74vh; overflow-y: auto; }
.ud-md { font-size: 13px; line-height: 1.85; color: var(--text); word-break: break-word; }
.ud-md :deep(p) { margin: 6px 0; }
.ud-md :deep(h1), .ud-md :deep(h2), .ud-md :deep(h3), .ud-md :deep(h4) {
  font-size: 14px; font-weight: 700; margin: 14px 0 6px; color: var(--text);
}
.ud-md :deep(ul), .ud-md :deep(ol) { margin: 6px 0; padding-left: 20px; line-height: 1.8; }
.ud-md :deep(strong) { font-weight: 700; color: var(--text); }
.ud-md :deep(table) {
  border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 12px;
  display: block; overflow-x: auto; white-space: nowrap;
}
.ud-md :deep(th), .ud-md :deep(td) { border: 1px solid var(--line-strong); padding: 5px 9px; text-align: left; }
.ud-md :deep(th) { background: var(--paper-warm); font-weight: 600; }
.ud-md :deep(tr:nth-child(even) td) { background: #fafbfd; }
.ud-md :deep(code) {
  background: var(--paper-warm); padding: 1px 5px; border-radius: 3px;
  font-family: var(--font-num); font-size: 12px;
}
.ud-md :deep(img) { max-width: 100%; }
.used-foot { font-size: 12px; color: var(--text-3); border-top: 1px dashed var(--line); padding-top: 9px; }
.used-empty { font-size: 13px; color: var(--text-3); text-align: center; padding: 24px 0; }

/* 标段档案弹窗 */
.lot-badge.clickable { cursor: pointer; }
.lot-badge.clickable:hover { background: var(--brand); color: #fff; border-color: var(--brand); }
.lot-profile-dlg .el-dialog__body { max-height: 72vh; overflow-y: auto; }
/* head：上下**对称**内边距 + 下边框 —— 内容在整行内真正垂直居中（只给下边距会偏上） */
.lp-head {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
  margin-bottom: 14px;
}
.lp-head > * { line-height: 1.2; }
.lp-code {
  display: inline-flex; align-items: center; height: 26px;
  font-size: 13px; font-weight: 700; color: #fff; background: var(--brand);
  padding: 0 11px; border-radius: 4px; font-family: var(--font-num); letter-spacing: 0.02em;
}
.lp-name { font-size: 13px; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lp-btn {
  display: inline-flex; align-items: center; justify-content: center; height: 30px; line-height: 1;
  border: 1px solid var(--line-strong); background: #fff; color: var(--text-2);
  font-size: 12px; padding: 0 14px; border-radius: var(--radius-s); cursor: pointer;
  transition: all 0.15s; flex-shrink: 0;
}
.lp-btn:hover:not(:disabled) { color: var(--brand); border-color: var(--brand); }
.lp-btn.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
.lp-btn.primary:hover:not(:disabled) { background: var(--brand-deep); }
.lp-btn:disabled { opacity: .6; cursor: default; }
/* 抽取中遮罩：盖住整个档案内容，期间不可编辑 */
.lp-wrap { position: relative; }
.lp-mask {
  position: absolute; inset: -10px; z-index: 10;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px;
  background: rgba(255, 255, 255, 0.86); backdrop-filter: blur(2px);
  border-radius: var(--radius-s);
}
.lp-spinner-lg { width: 26px; height: 26px; border-width: 3px; }
.lp-mask-text { font-size: 14px; font-weight: 600; color: var(--brand); }
.lp-mask-sub { font-size: 12px; color: var(--text-3); }
.lp-spinner {
  width: 13px; height: 13px; border-radius: 50%; flex-shrink: 0;
  border: 2px solid rgba(47, 95, 208, 0.25); border-top-color: var(--brand);
  animation: lp-rot 0.8s linear infinite;
}
@keyframes lp-rot { to { transform: rotate(360deg); } }
.lp-sec { margin-bottom: 18px; }
/* 档案作用说明（一行小字，不抢版面） */
.lp-desc {
  margin-bottom: 14px; padding: 8px 12px; border-radius: var(--radius-s);
  background: var(--brand-soft); border: 1px solid #d8e4f8;
  font-size: 12px; line-height: 1.7; color: #33518f;
}
.lp-desc strong { color: var(--brand); }
.lp-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 8px;
}
.lp-hint { font-size: 11px; font-weight: 400; color: var(--text-3); }
.lp-textarea :deep(.el-textarea__inner) {
  font-size: 13px; line-height: 1.8; font-family: inherit; color: var(--text);
  background: var(--paper-warm); border-color: var(--line);
}
.lp-ms-head {
  display: flex; align-items: center; gap: 6px; padding: 0 0 6px 2px;
  font-size: 11px; color: var(--text-3); border-bottom: 1px dashed var(--line);
}
.lp-ms-c1 { flex: 1; }
.lp-ms-c2 { width: 76px; flex-shrink: 0; }
.lp-ms-c3 { width: 118px; flex-shrink: 0; }
.lp-ms-del { width: 26px; flex-shrink: 0; }
.lp-row { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
.lp-kv { display: flex; gap: 10px; font-size: 13px; color: var(--text-2); line-height: 1.9; }
.lp-kv label { flex-shrink: 0; width: 64px; color: var(--text-3); }
.lp-num { font-family: var(--font-num); }
.lp-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.lp-chip {
  font-size: 12px; padding: 2px 10px; border-radius: 999px;
  background: var(--brand-soft); color: var(--brand); border: 1px solid #c4d6f5;
}
.lp-none { font-size: 12px; color: var(--text-3); }
.lp-li { font-size: 13px; color: var(--text-2); line-height: 1.8; }
.lp-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.lp-table td { border: 1px solid var(--line); padding: 5px 9px; color: var(--text-2); }
.lp-table td.num { font-family: var(--font-num); white-space: nowrap; }
.lp-foot { font-size: 12px; color: var(--text-3); border-top: 1px dashed var(--line); padding-top: 9px; }
.lp-empty { font-size: 13px; color: var(--text-2); text-align: center; padding: 22px 0; }
.lp-empty-sub { font-size: 12px; color: var(--text-3); margin: 6px 0 12px; }
/* 未选定标段时的标段选择列表：点一个即切换过去并自动建档案 */
.lot-pick {
  display: flex; flex-direction: column; gap: 6px;
  margin-top: 14px; max-height: 380px; overflow-y: auto; text-align: left;
}
.lot-pick-item {
  display: flex; align-items: baseline; gap: 10px; width: 100%;
  padding: 9px 12px; border: 1px solid var(--line); border-radius: var(--radius-s);
  background: var(--card); cursor: pointer; transition: all 0.15s; text-align: left;
}
.lot-pick-item:hover:not(:disabled) { border-color: var(--brand); background: var(--brand-soft); }
.lot-pick-item:disabled { opacity: 0.5; cursor: not-allowed; }
.lpi-code { flex-shrink: 0; font-family: var(--font-num); font-size: 13px; font-weight: 700; color: var(--brand); }
.lpi-name { font-size: 13px; color: var(--text-2); }
/* 档案编辑控件 */
.lp-frow { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.lp-frow > label { flex-shrink: 0; width: 64px; font-size: 13px; color: var(--text-3); }
.lp-unit { font-size: 12px; color: var(--text-3); flex-shrink: 0; }
.lp-qty { display: flex; flex-wrap: wrap; gap: 8px 18px; }
.lp-q { display: flex; align-items: center; gap: 4px; }
.lp-q-name { font-size: 13px; color: var(--text-2); width: 56px; flex-shrink: 0; }
.lp-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.lp-mini {
  margin-left: 8px; border: 1px dashed var(--line-strong); background: none; color: var(--text-2);
  font-size: 11px; padding: 1px 8px; border-radius: 999px; cursor: pointer; font-weight: 400;
}
.lp-mini:hover { color: var(--brand); border-color: var(--brand); }
/* 标段列表下方的"重新识别"次要入口（比标段选择按钮低一级，带风险说明） */
.lp-re { display: flex; align-items: center; gap: 6px; margin-top: 14px; }
.lp-re .lp-mini { margin-left: 0; }
.lp-re span { font-size: 11px; color: var(--text-3); }
.lp-del {
  border: none; background: none; color: var(--text-3); font-size: 12px; cursor: pointer;
  padding: 2px 6px; border-radius: 4px; flex-shrink: 0;
}
.lp-del:hover { color: var(--red); background: #fdf2f2; }

.editor-stage { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }

/* 整理素材遮罩：正文区半透明 loading，正文流入(chapter_start)后自动消失 */
.prep-loading {
  position: absolute; inset: 0; z-index: 20;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px;
  background: rgba(255, 255, 255, 0.6); backdrop-filter: blur(1.5px);
}
.prep-loading .pl-spinner {
  width: 36px; height: 36px; border-radius: 50%;
  border: 3px solid var(--brand-soft); border-top-color: var(--brand);
  animation: pl-rot 0.9s linear infinite;
}
@keyframes pl-rot { to { transform: rotate(360deg); } }
.prep-loading .pl-text { font-size: 14px; font-weight: 600; color: var(--text); margin: 0; }
.prep-loading .pl-sub { font-size: 12px; color: var(--text-2); margin: 0; letter-spacing: 0.02em; }

/* 生成中但已切走：不回流式内容，只占位等待（完成后一次性展示全文） */
.gen-wait-mask {
  position: absolute; inset: 0; z-index: 20;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px;
  background: rgba(255, 255, 255, 0.72); backdrop-filter: blur(1.5px);
}
.gen-wait-mask .gw-spinner {
  width: 36px; height: 36px; border-radius: 50%;
  border: 3px solid var(--brand-soft); border-top-color: var(--brand);
  animation: pl-rot 0.9s linear infinite;
}
.gen-wait-mask p { font-size: 14px; font-weight: 600; color: var(--text); margin: 0; }
.gen-wait-mask .gw-sub { font-size: 12px; font-weight: 400; color: var(--text-2); }
.gen-wait-mask .gw-stop {
  margin-top: 4px; padding: 5px 16px; font-size: 13px; cursor: pointer;
  border: 1px solid var(--line-strong); border-radius: var(--radius-s);
  background: var(--card); color: var(--text-2); transition: all 0.15s;
}
.gen-wait-mask .gw-stop:hover { border-color: #d64545; color: #d64545; }

.content-editor { flex: 1; min-height: 0; }
.content-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.ce-icon { width: 52px; height: 52px; border-radius: 14px; background: var(--brand-soft); color: var(--brand); font-size: 22px; display: flex; align-items: center; justify-content: center; margin-bottom: 14px; }
.ce-title { font-size: 16px; font-weight: 600; margin: 0 0 6px; }
.ce-sub { font-size: 13px; color: var(--text-3); text-align: center; line-height: 1.8; margin: 0; }

/* 右键菜单 */
.ctx-menu {
  position: fixed; z-index: 3000; min-width: 160px; padding: 6px;
  background: var(--card); border: 1px solid var(--line-strong); border-radius: var(--radius-m);
  box-shadow: var(--shadow-pop);
}
.ctx-title { font-size: 11px; color: var(--text-3); padding: 4px 10px 6px; border-bottom: 1px solid var(--line); margin-bottom: 4px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ctx-item {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
  border: none; background: none; padding: 7px 10px; font-size: 13px; color: var(--text-2);
  border-radius: var(--radius-s); cursor: pointer; transition: all 0.12s;
}
.ctx-item:hover { background: var(--brand-soft); color: var(--brand); }
.ctx-icon { display: flex; align-items: center; }
</style>
