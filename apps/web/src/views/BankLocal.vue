<script setup>
// 题库（本地）：整个题库由 D:\化学竞赛题库 提供，这里只做内嵌与探活。
// 跨源 iframe（127.0.0.1:8080）——不代理、不改写对方任何页面，题库改版也不会坏。
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { chemy } from '../api.js'

const DEFAULT_BASE = 'http://127.0.0.1:8080/'
const status = ref(null)
const loading = ref(true)
const err = ref('')
const reloadKey = ref(0)
const starting = ref(false)
const forceShow = ref(false)
let timer = null

const base = computed(() => {
  const b = status.value?.base || DEFAULT_BASE
  return b.endsWith('/') ? b : b + '/'
})
const stats = computed(() => status.value?.stats || {})
const canShow = computed(() => !!status.value?.ok || forceShow.value)

async function probe(quiet = false) {
  if (!quiet) loading.value = true
  try {
    status.value = await chemy.status()
    err.value = ''
  } catch (e) {
    err.value = e.message || String(e)
    status.value = { ok: false, base: DEFAULT_BASE, error: err.value }
  } finally {
    loading.value = false
  }
}

async function startBank() {
  starting.value = true
  err.value = ''
  try {
    await chemy.start()
    // 题库启动要几秒，轮询到起来为止
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 1000))
      await probe(true)
      if (status.value?.ok) break
    }
    if (status.value?.ok) reloadKey.value++
  } catch (e) {
    err.value = e.message || String(e)
  } finally {
    starting.value = false
  }
}

function reload() {
  reloadKey.value++
  probe(true)
}
function openTab() {
  window.open(base.value, '_blank', 'noopener')
}
function retry() {
  forceShow.value = false
  probe()
}

onMounted(() => {
  probe()
  timer = setInterval(() => probe(true), 20000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<template>
  <div class="bank">
    <div class="bar">
      <span class="dot" :class="{ on: status?.ok }" />
      <b>本地题库</b>
      <span class="muted" v-if="status?.ok">
        v{{ status.version }} · {{ stats.published ?? 0 }} 题 · {{ stats.papers ?? 0 }} 卷
      </span>
      <span class="muted" v-else-if="!loading">未连接（{{ base }}）</span>
      <span class="muted" v-else>连接中…</span>
      <span class="grow" />
      <button @click="reload" :disabled="loading">刷新</button>
      <button @click="openTab">新窗口打开</button>
    </div>

    <div v-if="err" class="errbox">{{ err }}</div>

    <iframe v-if="canShow" :key="reloadKey" class="frame" :src="base"
            referrerpolicy="no-referrer" title="本地题库" />

    <div v-else-if="!loading" class="card offline">
      <h3>本地题库没有启动</h3>
      <p class="muted">
        题目的检索、校对、导入、发布、备份、打印都在本地题库里，本项目只是它的前端。
        题库不在线时，出题和组卷也会跟着不可用。
      </p>
      <div class="btnrow">
        <button class="primary" @click="startBank" :disabled="starting">
          {{ starting ? '正在启动…' : '启动题库' }}
        </button>
        <button @click="retry">重试连接</button>
        <button @click="forceShow = true">仍然打开</button>
      </div>
      <p class="muted tip">
        手动启动：双击 <code>D:\化学竞赛题库\chemy.exe</code>（或题库目录里的启动脚本）。
        题库默认监听 <code>127.0.0.1:8080</code>。
      </p>
    </div>
  </div>
</template>

<style scoped>
/* 让 iframe 顶到页面边缘：抵消 .main 的 24px/28px 内边距 */
.bank {
  display: flex; flex-direction: column;
  height: calc(100vh - 48px); margin: -24px -28px;
}
.bar {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px; border-bottom: 1px solid var(--line); background: #fff;
}
.bar .grow { flex: 1; }
.bar button { padding: 4px 12px; font-size: 13px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #c8ccd4; }
.dot.on { background: var(--ok); }
.frame { flex: 1; width: 100%; border: 0; background: #fff; }
.offline { margin: 24px 28px; max-width: 620px; }
.offline h3 { margin: 0 0 6px; }
.offline .btnrow { margin: 14px 0 10px; }
.tip { margin-top: 6px; }
code { background: #f2f4f7; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }
</style>
