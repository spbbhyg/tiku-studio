import { createRouter, createWebHashHistory } from 'vue-router'

import Dashboard from './views/Dashboard.vue'
import Questions from './views/Questions.vue'
import Compose from './views/Compose.vue'
import ComposeCanvas from './views/ComposeCanvas.vue'
import Papers from './views/Papers.vue'
import PaperView from './views/PaperView.vue'
import Books from './views/Books.vue'
import Settings from './views/Settings.vue'
import Authoring from './views/Authoring.vue'
import QuizForge from './views/QuizForge.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: Dashboard },
    // 出题：左边裁 PDF，右边自由排版，存进去就是一道大题
    { path: '/author', component: Authoring },
    { path: '/questions', component: Questions },
    { path: '/quizforge', component: QuizForge },
    { path: '/compose', component: Compose },
    { path: '/compose/canvas', component: ComposeCanvas },
    { path: '/papers', component: Papers },
    { path: '/papers/:id', component: PaperView, props: true },
    { path: '/books', component: Books },
    { path: '/settings', component: Settings },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
