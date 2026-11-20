<script setup>
/* 统一的试卷导出入口：标题 / 页眉 / 页脚 + 题目卷 / 答案卷两份 PDF。
   所有导出都走 POST /api/papers/{id}/export.pdf（part=questions|answers）。

   0.5.3 修改（#4 导出时冒出一串红色英文）：
   以前失败时把 resp.text() 原样扔进红框，FastAPI 回的是 {"detail":"KeyError: ..."} 或
   直接把 Python 堆栈糊上去，用户看到就是一串红色英文。现在：
   - 错误体先按 JSON 解析，取 detail；
   - detail 里有中文就用中文；纯英文（堆栈）按状态码换成中文说法；
   - 有 E_XXX 错误码的保留并括在中文后面；
   - 原始文本只进 console 和日志文件，不上界面。 */
import { ref } from 'vue'

const emit = defineEmits(['exported', 'close'])

const props = defineProps({
  paperId: { type: [Number, String], required: true },
  title: { type: String, default: '' },
  cfg: { type: Object, default: () => ({}) },
  label: { type: String, default: '导出 PDF' },
  primary: { type: Boolean, default: false },
  small: { type: Boolean, default: false },
})

const open = ref(false)
const busy = ref('')
const err = ref('')
const errRaw = ref('')
const failedPart = ref('')
const done = ref('')
const form = ref({ title: '', header: '', footer: '' })

// 状态码兜底：后端没给中文时按这个说
const ERR_TEXT = {
  400: '请求参数不对：检查标题、页眉、页脚里有没有特殊字符',
  401: '没有权限导出，重新登录一次试试',
  403: '这个操作被拒绝了，检查一下访问码',
  404: '没找到这份试卷（可能已经被删除了）',
  413: '试卷内容太大，先把图片压一压再导出',
  429: '操作太频繁，歇几秒再点',
  500: '服务器在生成时出错了',
  502: '生成 PDF 的组件没响应（内置浏览器可能没起来）',
  503: '服务正忙，等几秒再试',
}

function pickError(status, raw) {
  let detail = ''
  try {
    const j = JSON.parse(raw)
    const d = j.detail ?? j.message ?? j.error ?? ''
    detail = Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join('；') : String(d)
  } catch (e) {
    detail = String(raw || '')
  }
  detail = detail.replace(/\s+/g, ' ').trim()
  const m = detail.match(/(E_[A-Z_]+)/)
  const code = m ? m[1] : ''
  const zh = /[\u4e00-\u9fa5]/.test(detail)
    ? detail.slice(0, 180)
    : ERR_TEXT[status] || `导出失败（HTTP ${status}）`
  return { text: zh + (code ? `（${code}）` : ''), raw: detail || `HTTP ${status}` }
}

function savedOpts() {
  const e = (props.cfg && props.cfg.exam && props.cfg.exam.export) || {}
  return {
    title: e.title || props.title || '',
    header: e.header || '',
    footer: e.footer || '',
  }
}

function close() {
  open.value = false
  emit('close')
}

function show() {
  form.value = savedOpts()
  err.value = ''
  errRaw.value = ''
  done.value = ''
  open.value = true
}

async function run(part) {
  busy.value = part
  err.value = ''
  errRaw.value = ''
  failedPart.value = ''
  done.value = ''
  try {
    const resp = await fetch(`/api/papers/${props.paperId}/export.pdf`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        part,
        title: form.value.title,
        header: form.value.header,
        footer: form.value.footer,
        save: 1,
      }),
    })
    if (!resp.ok) {
      let raw = ''
      try { raw = await resp.text() } catch (e) { /* ignore */ }
      const picked = pickError(resp.status, raw)
      // 技术细节只进控制台（用户报问题时让他 F12 复制）
      console.error('[导出失败]', resp.status, picked.raw)
      err.value = picked.text + '。可以再点一次重试；一直不行就把 data/logs/app.log 发给开发者。'
      errRaw.value = picked.raw
      failedPart.value = part
      return
    }
    const blob = await resp.blob()
    const partName = part === 'questions' ? '题目卷' : '答案卷'
    const name = `${(form.value.title || props.title || '试卷').replace(/[\\/:*?"<>|]/g, '_')}_${partName}.pdf`
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(a.href), 5000)
    done.value = `已导出 ${name}（${(blob.size / 1024 / 1024).toFixed(2)} MB）`
    emit('exported', part)
    try {
      await fetch(`/api/wb/papers/${props.paperId}/log-export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: 'pdf', file_name: name, file_size: blob.size }),
      })
    } catch (e) { /* 记日志失败不影响下载 */ }
  } catch (e) {
    // 网络层面挂了（断网、被代理拦）：同样给中文
    console.error('[导出异常]', e)
    err.value = '连不上本地服务（软件可能正在重启），等几秒再试'
    errRaw.value = String(e && e.message ? e.message : e)
  } finally {
    busy.value = ''
  }
}

function copyRaw() {
  try {
    navigator.clipboard.writeText(errRaw.value || '')
  } catch (e) { /* 剪贴板不可用就算了 */ }
}
defineExpose({ show, close })
</script>

<template>
  <button :class="[primary ? 'primary' : '', small ? 'sm' : '']" @click="show">{{ label }}</button>

  <div v-if="open" class="pmask" @click.self="close()">
    <div class="pdlg">
      <h3>导出试卷 PDF</h3>
      <p class="hint">题目卷、答案卷分开导出；页眉页脚留空就用默认值（卷名 + 「第 X 页 共 Y 页」）。</p>
      <label class="f"><span>试卷标题</span><input v-model="form.title" placeholder="打印在封面/首行的标题" /></label>
      <label class="f"><span>页眉</span><input v-model="form.header" placeholder="留空 = 自动用试卷标题" /></label>
      <label class="f"><span>页脚</span><input v-model="form.footer" placeholder="留空 = 第 X 页 共 Y 页" /></label>
      <div v-if="err" class="ebox">
        <div>{{ err }}</div>
        <div class="eraw">
          <button class="link" type="button" @click="copyRaw">复制技术细节</button>
          <button v-if="failedPart" class="link" type="button" :disabled="!!busy"
                  @click="run(failedPart)">重试</button>
        </div>
      </div>
      <div v-if="done" class="obox">{{ done }}</div>
      <div class="acts">
        <button class="ghost" @click="close()">关闭</button>
        <button :disabled="!!busy" @click="run('answers')">{{ busy === 'answers' ? '生成中…' : '导出答案卷' }}</button>
        <button class="primary" :disabled="!!busy" @click="run('questions')">{{ busy === 'questions' ? '生成中…' : '导出题目卷' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pmask { position: fixed; inset: 0; background: rgba(17, 24, 39, .5); display: flex; align-items: center; justify-content: center; z-index: 70; }
.pdlg { width: min(560px, 92vw); background: #fff; border-radius: 14px; padding: 20px 22px; box-shadow: 0 24px 60px rgba(15, 23, 42, .28); }
.pdlg h3 { margin: 0 0 6px; font-size: 17px; }
.hint { margin: 0 0 14px; color: #64748b; font-size: 12.5px; line-height: 1.6; }
.f { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.f span { width: 68px; flex: none; color: #475569; font-size: 13px; }
.f input { flex: 1; padding: 8px 10px; border: 1px solid #d8dee9; border-radius: 8px; font-size: 13px; }

.ebox, .obox { line-height: 1.7; }
.ebox { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; margin: 8px 0 0; }
.eraw { display: flex; gap: 12px; margin-top: 4px; }
.link { background: none; border: 0; padding: 0; color: #b42318; text-decoration: underline; cursor: pointer; font-size: 12px; }
.obox { background: #ecfdf5; border: 1px solid #bbf7d0; color: #047857; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; margin: 8px 0 0; }
.acts { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
</style>
