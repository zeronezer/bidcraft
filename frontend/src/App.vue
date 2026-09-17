<template>
  <div class="app-shell">
    <GlobalNav v-if="!isFullscreen" />
    <div class="app-main" :class="{ fullscreen: isFullscreen }">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import GlobalNav from './components/GlobalNav.vue'

const route = useRoute()
const isFullscreen = computed(() => !!route.meta.fullscreen)
</script>

<style scoped>
.app-shell { display: flex; height: 100%; width: 100%; }
.app-main { flex: 1; min-width: 0; height: 100%; overflow: auto; }
.app-main.fullscreen { overflow: hidden; }
</style>
