<script setup>
import { onMounted, ref } from 'vue'
import { api } from './api.js'

const open = ref(false)
const navOpen = ref(localStorage.getItem('tiku-nav-open') !== '0')
function toggleNav() {
  navOpen.value = !navOpen.value
  localStorage.setItem('tiku-nav-open', navOpen.value ? '1' : '0')
}

const navs = [
  { path: '/', label: '首页' },
  { path: '/v2/editor', label: '题组编辑 v2' },
  { path: '/author', label: '出题' },
  { path: '/reader', label: '精读' },
  { path: '/smart', label: '智能出题' },
  { path: '/questions', label: '题库' },
  { path: '/papers', label: '试卷' },
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
  <div class="layout" :class="{ narrow: !navOpen }">
    <aside class="sidebar" :class="{ open, collapsed: !navOpen }" @click="open = false">
      <div class="logo">
        <span class="wordmark">tiku-studio<small>题库 · 对纲 · 组卷</small></span>
        <button class="fold" :title="navOpen ? '收起导航' : '展开导航'" @click.stop="toggleNav">
          {{ navOpen ? '«' : '»' }}
        </button>
      </div>
      <nav class="nav">
        <template v-for="n in navs" :key="n.path">
          <router-link :to="n.path" :title="n.label">
            <span class="ic">{{ n.label.slice(0, 1) }}</span><span class="lbl">{{ n.label }}</span>
          </router-link>
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
.logo { display: flex; align-items: flex-start; gap: 6px; }
.wordmark { flex: 1; min-width: 0; }
.fold {
  border: 1px solid #e4e7ec; background: #fff; color: #667085; cursor: pointer;
  width: 22px; height: 22px; border-radius: 6px; line-height: 1; font-size: 12px;
  display: flex; align-items: center; justify-content: center; flex: none;
}
.fold:hover { color: var(--brand); border-color: var(--brand); }
.nav a .ic { display: none; }
.collapsed .wordmark, .collapsed .subnav { display: none; }
.collapsed .logo { justify-content: center; }
.collapsed .nav a { justify-content: center; padding-left: 0; padding-right: 0; }
.collapsed .nav a .lbl { display: none; }
.collapsed .nav a .ic { display: inline-block; font-size: 13px; font-weight: 600; }

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
