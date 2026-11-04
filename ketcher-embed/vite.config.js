import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { nodePolyfills } from 'vite-plugin-node-polyfills'

export default defineConfig({
  base: './',
  plugins: [
    react(),
    nodePolyfills({
      include: ['events', 'buffer', 'process', 'stream', 'util', 'path', 'crypto', 'assert', 'os'],
      globals: { Buffer: true, global: true, process: true },
      protocolImports: true,
    }),
  ],
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 40000 },
})
