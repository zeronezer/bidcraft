import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('../views/HomeView.vue'),
    },
    {
      path: '/workspace/:projectId',
      name: 'workspace',
      component: () => import('../views/WorkspaceView.vue'),
      props: true,
      meta: { fullscreen: true },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: () => import('../views/KnowledgeView.vue'),
    },
    {
      path: '/knowledge/pages',
      name: 'knowledge-pages',
      component: () => import('../views/KnowledgePagesView.vue'),
    },
    {
      path: '/knowledge/methods',
      name: 'knowledge-methods',
      component: () => import('../views/MethodEntriesView.vue'),
    },
    {
      path: '/experience',
      name: 'experience',
      component: () => import('../views/ExperienceView.vue'),
    },
  ],
})

export default router
