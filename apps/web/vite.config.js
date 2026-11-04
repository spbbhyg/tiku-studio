import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { nodePolyfills } from 'vite-plugin-node-polyfills'

export default defineConfig({
  plugins: [
    vue(),
    // Ketcher（结构式编辑器）依赖 Node 的 events/buffer/process，浏览器里需要 polyfill
    nodePolyfills({
      include: ['events', 'buffer', 'process', 'stream', 'util', 'path', 'crypto', 'assert', 'os'],
      globals: { Buffer: true, global: true, process: true },
      protocolImports: true,
    }),
  ],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8100' },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 4000 },
})
