<script setup>
/* 统一的试卷导出入口：标题 / 页眉 / 页脚 + 题目卷 / 答案卷两份 PDF。
   所有导出都走 POST /api/papers/{id}/export.pdf（part=questions|answers）。 */
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
const done = ref('')
const form = ref({ title: '', header: '', footer: '' })

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
  done.value = ''
  open.value = true
}

async function run(part) {
  busy.value = part
  err.value = ''
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
      let detail = ''
      try { detail = await resp.text() } catch (e) { /* ignore */ }
      throw new Error(detail.slice(0, 300) || `导出失败（${resp.status}）`)
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
    err.value = String(e.message || e)
  } finally {
    busy.value = ''
  }
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
      <div v-if="err" class="ebox">{{ err }}</div>
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
.ebox { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; margin: 8px 0 0; white-space: pre-wrap; }
.obox { background: #ecfdf5; border: 1px solid #bbf7d0; color: #047857; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; margin: 8px 0 0; }
.acts { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
</style>
