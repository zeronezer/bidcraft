import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    // 监听所有网卡，便于局域网/容器内访问
    host: '0.0.0.0',
    // 固定端口（默认 Vite 的 5173 常被其他本地服务占用），冲突时直接报错不静默换端口
    port: 5180,
    strictPort: true,
    // Vite 6 默认拦截非 localhost 的 Host 头（局域网 IP、反向代理域名会 403），开发期放开
    allowedHosts: true,
    hmr: {
      // HMR 的 websocket 走与页面相同的端口，保证经代理访问时热更新也能生效
      clientPort: 5180,
    },
    proxy: {
      // 后端 API 代理，避免开发期跨域
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
