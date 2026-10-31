<script setup>
import { onMounted, ref } from 'vue'
import { api } from './api.js'

const open = ref(false)
const navs = [
  { path: '/', label: '首页' },
  { path: '/author', label: '出题' },
  { path: '/questions', label: '题库' },
  { path: '/compose', label: '组卷' },
  { path: '/papers', label: '试卷' },
  { path: '/books', label: '资料库' },
  { path: '/quizforge', label: 'QF题库' },
  { path: '/settings', label: '设置' },
]

// 侧边栏直接铺出 quizforge.db 里的题库，点一下就到导入页
const qfBanks = ref([])

onMounted(async () => {
  try {
    const d = await api.get('/api/import/quizforge')
    qfBanks.value = d.banks || []
  } catch (e) { /* 没有 quizforge 时不影响其它页面 */ }
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar" :class="{ open }" @click="open = false">
      <div class="logo">tiku-studio<small>题库 · 对纲 · 组卷</small></div>
      <nav class="nav">
        <template v-for="n in navs" :key="n.path">
          <router-link :to="n.path">{{ n.label }}</router-link>
          <div v-if="n.path === '/quizforge' && qfBanks.length" class="subnav">
            <router-link v-for="b in qfBanks" :key="b.id"
                         :to="{ path: '/quizforge', query: { bank: b.id } }"
                         :title="b.source_file || b.name">
              <span class="dot" :class="{ done: b.imported }"></span>{{ b.name }}
            </router-link>
          </div>
        </template>
      </nav>
    </aside>
    <div class="topbar">
      <button @click="open = !open">☰</button>
      <b>tiku-studio</b>
    </div>
    <main class="main" @click="open = false">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.subnav {
  display: flex; flex-direction: column; gap: 2px;
  margin: 2px 0 6px 10px; padding-left: 8px; border-left: 2px solid #eef1f6;
}
.subnav a {
  font-size: 12.5px; color: #667085; padding: 4px 8px; border-radius: 6px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.subnav a.router-link-active { color: var(--brand); background: var(--brand-soft); }
.subnav .dot {
  display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: #d0d5dd; margin-right: 6px; vertical-align: middle;
}
.subnav .dot.done { background: #12b76a; }
</style>
