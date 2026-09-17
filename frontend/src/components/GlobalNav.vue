<template>
  <aside class="global-nav">
    <!-- 品牌 -->
    <div class="nav-brand">
      <div class="brand-mark">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
          <path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="brand-text">
        <span class="brand-name">标书匠 BidCraft</span>
        <span class="brand-sub">对话式工程标书编制</span>
      </div>
    </div>

    <!-- 导航项 -->
    <nav class="nav-list">
      <router-link
        v-for="item in NAV"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
      >
        <span class="nav-icon" v-html="item.icon" />
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- 底部占位 -->
    <div class="nav-footer">
      <span class="nav-ver num">v0.1 · MVP</span>
    </div>
  </aside>
</template>

<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()

const NAV = [
  {
    path: '/',
    label: '首页',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/></svg>',
  },
  {
    path: '/knowledge',
    label: '知识库',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M8 7h8M8 11h8M8 15h5"/></svg>',
  },
  {
    path: '/experience',
    label: '经验库',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 8.7l5.4-.8z"/></svg>',
  },
]

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}
</script>

<style scoped>
.global-nav {
  width: 200px; flex-shrink: 0; height: 100%;
  background: var(--ink-900); border-right: 1px solid var(--ink-line);
  display: flex; flex-direction: column;
}

/* 品牌 */
.nav-brand { display: flex; align-items: center; gap: 10px; padding: 20px 16px 16px; border-bottom: 1px solid var(--ink-line); }
.brand-mark {
  width: 34px; height: 34px; flex-shrink: 0; border-radius: 8px;
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-deep) 100%);
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 2px 8px rgba(47, 95, 208, 0.4);
}
.brand-text { display: flex; flex-direction: column; min-width: 0; }
.brand-name { font-size: 13px; font-weight: 700; color: var(--ink-text); letter-spacing: 0.02em; white-space: nowrap; }
.brand-sub { font-size: 10px; color: var(--ink-text-3); margin-top: 1px; white-space: nowrap; }

/* 导航列表 */
.nav-list { flex: 1; padding: 12px 10px; display: flex; flex-direction: column; gap: 2px; }
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-radius: var(--radius-s); text-decoration: none; color: var(--ink-text-2);
  font-size: 13px; transition: all 0.15s ease-out;
}
.nav-item:hover { background: var(--ink-800); color: var(--ink-text); }
.nav-item.active { background: var(--brand); color: #fff; font-weight: 600; }
.nav-icon { display: flex; align-items: center; }
.nav-label { letter-spacing: 0.02em; }

.nav-footer { padding: 14px 16px; border-top: 1px solid var(--ink-line); }
.nav-ver { font-size: 10px; color: var(--ink-text-3); }
</style>
