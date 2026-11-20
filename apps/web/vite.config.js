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
  // maxParallelFileOps 调低：@superdoc/docx-engine 是 18MB 的单文件，
  // Rollup 默认并发读取时会同时持有多个副本，小内存机器直接 OOM。
  build: {
    outDir: 'dist',
    // 不整体清空 dist：Windows 上 dist/ketcher/app.js 这类文件偶尔被占用（EPERM unlink），
    // 清空失败会让整次构建白跑。增量覆盖即可，旧 chunk 留着不影响（index.html 只引用新 hash）。
    emptyOutDir: false,
    // public 里的 ketcher（30MB 大文件）经常因文件占用拷贝失败导致整次构建失败——
    // 它只在重建 ketcher-embed 时才变，平时直接用 robocopy 增量同步，构建时不再复制。
    copyPublicDir: false,
    chunkSizeWarningLimit: 4000,
    rollupOptions: { maxParallelFileOps: 2 },
  },
})
