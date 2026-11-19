import { createRouter, createWebHashHistory } from 'vue-router'

import Dashboard from './views/Dashboard.vue'
import V2Editor from './views/v2/Editor.vue'

import SmartPaper from './views/SmartPaper.vue'
import ComposeCanvas from './views/ComposeCanvas.vue'
import Papers from './views/Papers.vue'
import PaperView from './views/PaperView.vue'
import PaperMaker from './views/PaperMaker.vue'
import AiHome from './views/AiHome.vue'
import Settings from './views/Settings.vue'
import Authoring from './views/Authoring.vue'
import BankLocal from './views/BankLocal.vue'
import Compose from './views/Compose.vue'
import Bench from './views/Bench.vue'
import Convert from './views/Convert.vue'
import Drafts from './views/Drafts.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: Dashboard },
    // 出题：左边裁 PDF，右边自由排版，存进去就是一道大题
    { path: '/author', component: Authoring },
    // 题库：题目只存在于 D:\化学竞赛题库，这里内嵌它（跨源 iframe，不代理不改写）
    { path: '/bank', component: BankLocal },
    // 草稿箱：出到一半关掉页面也不丢（自动存 data/drafts）
    { path: '/drafts', component: Drafts },
    // 组卷：从本地题库勾题 → A4 实时预览 → 自动页脚 → 导出 PDF/DOCX
    { path: '/compose', component: Compose },
  { path: '/bench', component: Bench },
    // 格式转换：docx ↔ pdf，本地转
    { path: '/convert', component: Convert },
    { path: '/compose/canvas', component: ComposeCanvas },
    { path: '/papers', component: Papers },
    { path: '/papers/new', component: PaperMaker, name: 'paper-new' },
    { path: '/papers/:id/edit', component: PaperMaker, name: 'paper-edit' },
    { path: '/papers/:id', component: PaperView, props: true },
    { path: '/v2/editor/:id?', component: V2Editor },
    { path: '/reader', redirect: '/ai?tab=read' },
    // AI 工作台：智能出题 + 对话 + 模型用量，一个入口上方切
    { path: '/ai', component: AiHome },
    { path: '/smart', redirect: '/ai' },
    { path: '/exam', redirect: '/author?mode=paper' },
    { path: '/settings', component: Settings },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
