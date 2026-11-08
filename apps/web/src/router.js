import { createRouter, createWebHashHistory } from 'vue-router'

import Dashboard from './views/Dashboard.vue'
import Questions from './views/Questions.vue'
import Bank from './views/Bank.vue'
import V2Editor from './views/v2/Editor.vue'
import Reader from './views/Reader.vue'
import SmartPaper from './views/SmartPaper.vue'
import ComposeCanvas from './views/ComposeCanvas.vue'
import Papers from './views/Papers.vue'
import PaperView from './views/PaperView.vue'
import PaperMaker from './views/PaperMaker.vue'
import ExamHome from './views/ExamHome.vue'
import Ingest from './views/Ingest.vue'
import Settings from './views/Settings.vue'
import Authoring from './views/Authoring.vue'
import WrongBook from './views/WrongBook.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: Dashboard },
    // 出题：左边裁 PDF，右边自由排版，存进去就是一道大题
    { path: '/author', component: Authoring },
    { path: '/questions', component: Bank },
    { path: '/questions/classic', component: Questions },
    { path: '/compose', redirect: '/questions' },
    { path: '/compose/canvas', component: ComposeCanvas },
    // 出整卷：一个独立的出卷界面，没出完的卷子留在草稿里
    { path: '/exam', component: ExamHome },
    { path: '/ingest', component: Ingest },
    { path: '/papers', component: Papers },
    { path: '/papers/new', component: PaperMaker, name: 'paper-new' },
    { path: '/papers/:id/edit', component: PaperMaker, name: 'paper-edit' },
    { path: '/papers/:id', component: PaperView, props: true },
    { path: '/v2/editor/:id?', component: V2Editor },
    { path: '/wrong', component: WrongBook },
    { path: '/wrong/:id', component: WrongBook, props: true },
    { path: '/reader', component: Reader },
    { path: '/smart', component: SmartPaper },
  { path: '/settings', component: Settings },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
