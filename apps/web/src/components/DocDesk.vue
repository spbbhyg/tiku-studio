<template>
  <div class="desk" :class="{ lf: lf, rf: rf }">
    <!-- 顶栏：左上角是左右两栏的收缩按钮 -->
    <div class="dtop">
      <button class="fold" :title="lf ? '展开左栏（单题文本）' : '收起左栏，给文档腾地方'" @click="toggleL">
        {{ lf ? '»' : '«' }}
      </button>
      <span class="lbl">单题文本</span>
      <span class="sep" />
      <button class="tab" :class="{ on: docTab === 'q' }" title="题目文档" @click="switchTab('q')">题目文档</button>
      <button class="tab" :class="{ on: docTab === 'a' }" title="答案文档" @click="switchTab('a')">答案文档</button>
      <button class="ghost sm" title="新建一份空白文档，接在当前标签下" @click="newDoc">新建</button>
      <span class="grow" />
      <button class="btn" :disabled="!textHasContent" title="把左边写的题目按排版写进中间的 Word 末尾" @click="emitDraft(true)">
        生成排版稿 →
      </button>
      <button class="btn" :disabled="!textHasContent" title="保留 $公式$，插入到 Word 当前光标处" @click="emitDraft(false)">
        插入到光标处
      </button>
      <button class="btn" title="把画布上的图片依次插进 Word 当前光标处" @click="pushCanvasImages">画布图片进文档</button>
      <button class="btn" :disabled="importing" title="图片直接进画布；PDF 自动逐页转图进画布" @click="pickCanvasFiles">导入图片/PDF</button>
      <input ref="canvasFileEl" type="file" accept="application/pdf,image/*" multiple hidden @change="onPickCanvasFiles" />
      <span class="sep" />
      <button class="fold" :title="rf ? '展开右栏（画布）' : '收起右栏，给文档腾地方'" @click="toggleR">
        {{ rf ? '«' : '»' }}
      </button>
      <span v-if="dmsg" class="dmsg">{{ dmsg }}</span>
    </div>

    <div class="dbody">
      <!-- 左：单题文本 -->
      <aside class="lpane">
        <div class="phead">
          <b>题目文本</b>
          <span class="mut">写在这里，点右上「生成排版稿」进 Word</span>
        </div>
        <input v-model="txt.title" class="in tit" placeholder="题目标题（可空，默认取题干第一行）" />
        <textarea v-model="txt.stem" class="ta" rows="9" placeholder="题干 / 小问……&#10;小问可以一行一条：1-1 ……&#10;1-2 ……" @input="saveText" />
        <div class="phead sub">
          <b>答案</b>
          <span class="mut">{{ docTab === 'a' ? '当前在答案文档' : '当前在题目文档' }}</span>
        </div>
        <textarea v-model="txt.answer" class="ta" rows="7" placeholder="答案 / 解析……" @input="saveText" />
        <p class="tip2">粘图片请直接粘到中间的 Word 里；公式点顶部「公式」，结构式点「结构式」。</p>
      </aside>

      <!-- 中：Word -->
      <main class="mpane">
        <DocEditorPane ref="docRef" @changed="onDocChanged" />
      </main>

      <!-- 右：画布 -->
      <aside class="rpane">
        <CanvasPane ref="paneRef" title="画布" hint="拖图进来，或者点「＋ 文本」；做好后点顶栏「画布图片进文档」" :max-pages="1"
                    @changed="onCanvasChanged" @files="onCanvasFiles" />
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api.js'
import { renderMath } from '../math.js'
import { importPdfToCanvas, isCanvasImage, isCanvasPdf, uploadImageToCanvas } from '../canvasImport.js'
import CanvasPane from './CanvasPane.vue'
import DocEditorPane from './DocEditorPane.vue'

const docRef = ref(null)
const paneRef = ref(null)
const canvasFileEl = ref(null)
const importing = ref(false)
const lf = ref(false)
const rf = ref(false)
const docTab = ref('q')
const docChanged = ref(false)
const dmsg = ref('')
function dsay(t) { dmsg.value = t; setTimeout(() => { if (dmsg.value === t) dmsg.value = '' }, 6000) }

const txt = reactive({ title: '', stem: '', answer: '' })
const slots = reactive({
  q: { name: '', blob: null },
  a: { name: '', blob: null },
})

const textHasContent = computed(() => !!(String(txt.stem || '').trim() || String(txt.answer || '').trim()))

const LKEY = 'tiku-desk-fold'
const TKEY = 'tiku-desk-text'

onMounted(async () => {
  try {
    const f = JSON.parse(localStorage.getItem(LKEY) || '{}')
    lf.value = !!f.l
    rf.value = !!f.r
  } catch { /* 忽略 */ }
  try {
    const t = JSON.parse(localStorage.getItem(TKEY) || '{}')
    txt.title = t.title || ''
    txt.stem = t.stem || ''
    txt.answer = t.answer || ''
    if (t.tab === 'a') docTab.value = 'a'
  } catch { /* 忽略 */ }
  // 一进来就给一份空白文档，用户不用先找文件
  await nextTick()
  setTimeout(() => { if (docRef.value && docRef.value.newBlank && !docRef.value.isReady()) docRef.value.newBlank() }, 60)
})

watch([lf, rf], () => {
  try { localStorage.setItem(LKEY, JSON.stringify({ l: lf.value, r: rf.value })) } catch { /* 忽略 */ }
})

function toggleL() { lf.value = !lf.value }
function toggleR() { rf.value = !rf.value }

function saveText() {
  try { localStorage.setItem(TKEY, JSON.stringify({ ...txt, tab: docTab.value })) } catch { /* 忽略 */ }
}

function onDocChanged() { docChanged.value = true }
function onCanvasChanged() { /* 画布变化不影响文档，这里只是接住事件 */ }

/** 切标签：先把当前文档导出成 Blob 存在内存里，切回来再灌回去 —— 不丢内容，也不弹提示。 */
async function switchTab(next) {
  if (next === docTab.value) return
  const cur = docTab.value
  try {
    const blob = docRef.value && docRef.value.exportBlob ? await docRef.value.exportBlob(slots[cur].name || cur) : null
    if (blob) slots[cur].blob = blob
  } catch { /* 导出失败就按原样切 */ }
  docTab.value = next
  saveText()
  const slot = slots[next]
  if (slot.blob) await docRef.value.loadBlob(slot.blob, slot.name || (next === 'q' ? '题目文档.docx' : '答案文档.docx'))
  else if (slot.name) await openByName(slot.name)
}

/** 新建空白文档（当前标签） */
async function newDoc() {
  const name = docTab.value === 'q' ? `题目文档-${stamp()}.docx` : `答案文档-${stamp()}.docx`
  slots[docTab.value] = { name, blob: null }
  await docRef.value.newBlank()
  slots[docTab.value] = { name, blob: null }
}

function stamp() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`
}

async function openByName(name) {
  // 文档下拉里已有的历史文档：让 DocEditorPane 自己按文件名打开
  if (docRef.value && docRef.value.openByName) return docRef.value.openByName(name)
  return null
}

function esc(t) {
  return String(t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 把「题目 / 小问 / 答案」拼成一段带排版的 HTML */
function buildHtml() {
  const title = String(txt.title || '').trim() || String(txt.stem || '').trim().split('\n')[0].slice(0, 40)
  const stem = String(txt.stem || '').trim()
  const ans = String(txt.answer || '').trim()
  const parts = []
  if (title) parts.push(`<p><strong>${renderMath(title)}</strong></p>`)
  stem.split(/\n+/).forEach((line) => {
    const t = line.trim()
    if (!t) return
    parts.push(`<p>${renderMath(t)}</p>`)
  })
  if (ans) {
    parts.push('<p>&nbsp;</p>')
    parts.push('<p><strong>【答案】</strong></p>')
    ans.split(/\n+/).forEach((line) => {
      const t = line.trim()
      if (!t) return
      parts.push(`<p>${renderMath(t)}</p>`)
    })
  }
  return parts.join('')
}

async function emitDraft(atEnd = true) {
  if (!textHasContent.value) return
  const html = buildHtml()
  const fn = docRef.value && docRef.value.insertHtml
  if (typeof fn !== 'function') { dsay('内部错误：insertHtml 没暴露出来'); return }
  const ok = await fn(html, { atEnd })
  if (ok) { docChanged.value = true; dsay(atEnd ? '已写进文档末尾' : '已插入到当前光标处') }
  else { dsay('写入被拒：' + (docRef.value.lastError ? docRef.value.lastError() : '未知原因')) }
}

/** 把画布上的图片按顺序插进文档 */
async function pushCanvasImages() {
  const lay = paneRef.value ? paneRef.value.getLayout() : null
  const imgs = (lay && lay.blocks ? lay.blocks : []).filter((b) => b.type === 'image' && b.path)
  if (!imgs.length) return
  for (const b of imgs) {
    try {
      const r = await fetch(`/api/images/${b.path}`)
      if (!r.ok) continue
      const blob = await r.blob()
      const data = await new Promise((res) => {
        const fr = new FileReader()
        fr.onload = () => res(String(fr.result || ''))
        fr.onerror = () => res('')
        fr.readAsDataURL(blob)
      })
      if (!data) continue
      await docRef.value.insertImageData(data, b.w || 300, b.h || 0, '画布图片')
    } catch { /* 单张失败跳过 */ }
  }
  docChanged.value = true
}

async function onCanvasFiles({ files, x, y }) {
  await importCanvasFiles(files, x, y)
}

function pickCanvasFiles() { canvasFileEl.value && canvasFileEl.value.click() }

async function onPickCanvasFiles(ev) {
  const files = Array.from((ev.target && ev.target.files) || [])
  ev.target.value = ''
  await importCanvasFiles(files, null, null)
}

async function importCanvasFiles(files, x, y) {
  const pane = paneRef.value
  if (!pane || importing.value) return
  const list = Array.from(files || [])
  if (!list.length) return
  importing.value = true
  try {
    for (const f of list) {
      if (isCanvasImage(f)) {
        dsay(`正在导入图片「${f.name}」…`)
        await uploadImageToCanvas(f, pane, x, y)
      } else if (isCanvasPdf(f)) {
        await importPdfToCanvas(f, pane, dsay)
      } else {
        dsay(`「${f.name}」不是图片或 PDF，没有导入`)
      }
      x = null
      y = null
    }
    dsay('导入完成：可以继续排版，或点「画布图片进文档」')
  } catch (e) {
    dsay(`导入失败：${e?.message || e}`)
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.desk { display: flex; flex-direction: column; height: calc(100vh - 108px); min-height: 0; padding: 0 2px; }
.dtop { display: flex; align-items: center; gap: 6px; padding: 6px 8px; background: #fff;
  border: 1px solid var(--line); border-radius: 12px; margin-bottom: 8px; min-width: 0;
  overflow-x: auto; overflow-y: hidden; scrollbar-width: thin; scrollbar-color: #cfd6e2 transparent; }
.dtop > .btn, .dtop > .tab, .dtop > .fold, .dtop > .lbl, .dtop > .sep { flex: 0 0 auto; white-space: nowrap; }
.dtop .grow { flex: 1 0 8px; }
.dtop .lbl { font-size: 12.5px; color: #667085; }
.dtop .sep { width: 1px; height: 16px; background: var(--line); }
.dtop .fold { width: 26px; height: 26px; padding: 0; border: 1px solid var(--line); border-radius: 8px;
  background: #fff; cursor: pointer; font-size: 13px; line-height: 1; }
.dtop .fold:hover { border-color: #98a2b3; }
.dtop .tab { height: 26px; padding: 0 12px; border-radius: 999px; border: 1px solid var(--line); background: #fff; cursor: pointer; font-size: 12.5px; }
.dtop .tab.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.dtop .ghost.sm { height: 26px; padding: 0 10px; font-size: 12.5px; }
.dmsg { font-size: 12px; color: #b54708; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.rpane :deep(.cvhead) { flex-wrap: wrap; row-gap: 4px; }
.rpane :deep(.cvhead .lab) { white-space: nowrap; }
.rpane :deep(.wtools) { flex-wrap: wrap; row-gap: 4px; }
.dbody { display: grid; grid-template-columns: 260px minmax(0, 1fr) clamp(320px, 26%, 420px); gap: 8px; flex: 1; min-height: 0; }
@media (max-width: 1500px) { .dbody { grid-template-columns: 240px minmax(0, 1fr) clamp(300px, 24%, 400px); } }
.desk.lf .dbody { grid-template-columns: 0 minmax(0, 1fr) 470px; }
.desk.rf .dbody { grid-template-columns: 300px minmax(0, 1fr) 0; }
.desk.lf.rf .dbody { grid-template-columns: 0 minmax(0, 1fr) 0; }

.lpane, .rpane { min-width: 0; overflow: auto; background: #fff; border: 1px solid var(--line); border-radius: 12px; }
.desk.lf .lpane, .desk.rf .rpane { overflow: hidden; border: 0; padding: 0; }
.mpane { min-width: 0; min-height: 0; overflow: hidden; }

.phead { display: flex; align-items: baseline; gap: 8px; padding: 10px 12px 4px; }
.phead.sub { padding-top: 6px; }
.phead .mut { font-size: 11.5px; }
.tit { margin: 6px 12px; width: calc(100% - 24px); height: 30px; padding: 0 8px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; }
.ta { margin: 6px 12px; width: calc(100% - 24px); padding: 8px; border: 1px solid var(--line); border-radius: 8px;
  font-size: 13px; line-height: 1.7; resize: vertical; font-family: inherit; }
.tip2 { margin: 2px 12px 12px; font-size: 11.5px; color: #98a2b3; line-height: 1.6; }
.rpane :deep(.cvpane) { border: 0; border-radius: 0; height: 100%; }
</style>
