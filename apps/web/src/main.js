import { createApp } from 'vue'
import App from './App.vue'
import router from './router.js'
import 'katex/dist/katex.min.css'
import './style.css'
import { errText } from './api.js'
import { toast } from './tasks.js'

const app = createApp(App)

// 兜底：任何没人接的报错，都用中文说一句人话，别让人对着英文发呆
let lastToast = ''
function friendly(err) {
  const msg = errText(err)
  if (msg && msg !== lastToast) {
    lastToast = msg
    setTimeout(() => { lastToast = '' }, 4000)
    toast(msg, 'bad', 7000)
  }
  return msg
}
app.config.errorHandler = (err) => { console.error(err); friendly(err) }
window.addEventListener('unhandledrejection', (e) => { console.error(e.reason); friendly(e.reason) })
window.addEventListener('error', (e) => { if (e && e.error) { console.error(e.error); friendly(e.error) } })

app.use(router).mount('#app')
