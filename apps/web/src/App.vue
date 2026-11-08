<script setup>
import { ref } from 'vue'
import { tasks, dropToast } from './tasks.js'

const open = ref(false)
const navOpen = ref(localStorage.getItem('tiku-nav-open') !== '0')
function toggleNav() {
  navOpen.value = !navOpen.value
  localStorage.setItem('tiku-nav-open', navOpen.value ? '1' : '0')
}

const navs = [
  { path: '/', label: '首页' },
  { path: '/author', label: '出题' },
  { path: '/reader', label: '精读' },
  { path: '/smart', label: '智能出题' },
  { path: '/questions', label: '题库' },
  { path: '/exam', label: '出整卷' },
  { path: '/ingest', label: '整卷导入' },
  { path: '/papers', label: '试卷' },
  { path: '/wrong', label: '错题集' },
  { path: '/settings', label: '设置' },
]
</script>

<template>
  <div class="layout" :class="{ narrow: !navOpen }">
    <aside class="sidebar" :class="{ open, collapsed: !navOpen }" @click="open = false">
      <div class="logo">
        <span class="wordmark">神奋<small>题库 · 对纲 · 组卷</small></span>
        <button class="fold" :title="navOpen ? '收起导航' : '展开导航'" @click.stop="toggleNav">
          {{ navOpen ? '«' : '»' }}
        </button>
      </div>
      <nav class="nav">
        <template v-for="n in navs" :key="n.path">
          <router-link :to="n.path" :title="n.label">
            <span class="ic">{{ n.label.slice(0, 1) }}</span><span class="lbl">{{ n.label }}</span>
          </router-link>
        </template>
      </nav>
    </aside>
    <div class="topbar">
      <button @click="open = !open">☰</button>
      <b>神奋</b>
    </div>
    <main class="main" @click="open = false">
      <router-view />
    </main>

    <!-- 全局任务条：长任务切页面也不会丢 -->
    <div v-if="tasks.running.length" class="gtask">
      <span class="dot" />
      <span>{{ tasks.running[0].title }}</span>
    </div>

    <!-- 全局提示：切到别的页面也还看得到 -->
    <div class="gtoasts">
      <div v-for="t in tasks.toasts" :key="t.id" class="gtoast" :class="t.kind"
           @click="dropToast(t.id)">
        {{ t.text }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.gtask {
  position: fixed; left: 50%; transform: translateX(-50%); bottom: 76px; z-index: 60;
  display: flex; align-items: center; gap: 8px;
  background: #fff; border: 1px solid #e4e7ec; border-radius: 999px;
  padding: 7px 14px; font-size: 12.5px; color: #344054;
  box-shadow: 0 6px 20px rgba(16, 24, 40, .08);
}
.gtask .dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--brand, #2e6bff);
  animation: gpulse 1s ease-in-out infinite;
}
@keyframes gpulse { 0%, 100% { opacity: .35 } 50% { opacity: 1 } }
.gtoasts {
  position: fixed; left: 50%; transform: translateX(-50%); bottom: 18px; z-index: 70;
  display: flex; flex-direction: column; gap: 8px; max-width: 360px;
}
.gtoast {
  background: #ecfdf3; border: 1px solid #abefc6; color: #067647;
  border-radius: 10px; padding: 9px 13px; font-size: 13px; line-height: 1.6;
  box-shadow: 0 8px 24px rgba(16, 24, 40, .10); cursor: pointer;
}
.gtoast.bad { background: #fef3f2; border-color: #fecdca; color: #b42318; }

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
