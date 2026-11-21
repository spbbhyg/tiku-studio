<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api.js'
import DocEditorPane from './DocEditorPane.vue'
import CanvasPane from './CanvasPane.vue'
import { importPdfToCanvas, isCanvasImage, isCanvasPdf, uploadImageToCanvas } from '../canvasImport.js'

const props = defineProps({
  mode: { type: String, default: 'doc' },
  // 和出题页「题目画布」共用的排版数据：由 Authoring 传入，这边改完再回传
  layout: { type: Object, default: null },
})
const emit2 = defineEmits(['mode', 'layout'])
const docRef = ref(null)
const paneRef = ref(null)
const canvasFileEl = ref(null)
const importing = ref(false)
const saving = ref(false)
const savedAt = ref('')
const dirty = ref(false)
const dmsg = ref('')
let msgTimer = 0
function dsay(t) {
  dmsg.value = t || ''
  clearTimeout(msgTimer)
  if (t) msgTimer = setTimeout(() => { dmsg.value = '' }, 6000)
}
onBeforeUnmount(() => clearTimeout(msgTimer))

// ---------- 左右比例（可拖动，记忆上次） ----------
const RKEY = 'tiku-desk-ratio'
const ratio = ref(Number(localStorage.getItem(RKEY) || 0.62))
const dragging = ref(false)
function startSplit(e) {
  dragging.value = true
  e.preventDefault()
  window.addEventListener('pointermove', onSplit)
  window.addEventListener('pointerup', endSplit)
}
function onSplit(e) {
  const wrap = document.querySelector('.desk .dbody')
  if (!wrap) return
  const rc = wrap.getBoundingClientRect()
  const r = (e.clientX - rc.left) / Math.max(1, rc.width)
  ratio.value = Math.min(0.82, Math.max(0.28, r))
}
function endSplit() {
  dragging.value = false
  localStorage.setItem(RKEY, String(ratio.value))
  window.removeEventListener('pointermove', onSplit)
  window.removeEventListener('pointerup', endSplit)
}
const RFOLD = 'tiku-desk-fold-r'
const rf = ref(localStorage.getItem(RFOLD) === '1')
function toggleCanvas() {
  rf.value = !rf.value
  localStorage.setItem(RFOLD, rf.value ? '1' : '0')
}
const docStyle = computed(() => (rf.value
  ? { flex: '1 1 auto' }
  : { flex: `0 0 ${(ratio.value * 100).toFixed(2)}%` }))

// ---------- 画布同步：和出题页「题目画布」共用同一份排版数据 ----------
// 父组件（出题页）把题目画布的 layout 传进来，这边画布上改完再回传。
// lastEmit 记住「本组件刚发出去的那一份」，父组件原样传回来时不再重建，避免来回覆盖。
const lastEmit = ref(null)
function applyLayout(l) {
  if (!l || !paneRef.value) return
  try { paneRef.value.setLayout(l) } catch (e) { /* 旧格式不兼容时保留原画布 */ }
}
watch(() => props.layout, (v) => {
  if (!v || v === lastEmit.value) return
  nextTick(() => applyLayout(v))
})
onMounted(() => { nextTick(() => applyLayout(props.layout)) })
function onCanvasChanged() {
  if (!paneRef.value) return
  const lay = paneRef.value.getLayout()
  lastEmit.value = lay
  emit2('layout', lay)
}

// ---------- 文档 ----------
const curName = ref('')
onMounted(async () => {
  await nextTick()
  setTimeout(() => {
    if (docRef.value && !docRef.value.isReady()) newDoc()
  }, 80)
})
function newDoc() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  curName.value = `题目文档-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.docx`
  return docRef.value && docRef.value.newBlank(curName.value)
}
function onDocChanged() {
  dirty.value = true
  savedAt.value = ''
  scheduleSave()
}

// ---------- 自动保存（3 秒防抖，写回 data/docx） ----------
let saveTimer = 0
function scheduleSave() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(saveNow, 3000)
}
async function saveNow() {
  if (!docRef.value || !docRef.value.isReady() || saving.value) return
  const name = (docRef.value.currentName && docRef.value.currentName()) || curName.value || '未命名.docx'
  saving.value = true
  try {
    const blob = await docRef.value.exportBlob(name)
    if (!blob) return
    const fd = new FormData()
    fd.append('file', new File([blob], name))
    fd.append('name', name)
    fd.append('overwrite', 'true')
    await api.postForm('/api/doce/save', fd)
    dirty.value = false
    const d = new Date()
    const p = (n) => String(n).padStart(2, '0')
    savedAt.value = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  } catch (e) {
    dsay('自动保存失败：' + api.errText(e))
  } finally {
    saving.value = false
  }
}
onBeforeUnmount(() => { clearTimeout(saveTimer) })

// ---------- 把画布上的图送进文档 ----------
async function onToDoc(item) {
  if (!docRef.value) return
  try {
    const r = await fetch(`/api/images/${item.path}`)
    if (!r.ok) { dsay('这张图取不下来'); return }
    const blob = await r.blob()
    const data = await new Promise((res) => {
      const fr = new FileReader()
      fr.onload = () => res(String(fr.result || ''))
      fr.onerror = () => res('')
      fr.readAsDataURL(blob)
    })
    if (!data) return
    const ok = await docRef.value.insertImageData(data, item.w || 300, item.h || 0, '画布图片')
    dsay(ok ? '已把这张图插进文档（光标处）' : '插入被拒：' + (docRef.value.lastError() || '未知原因'))
  } catch (e) {
    dsay('插入失败：' + api.errText(e))
  }
}

/** 从文档里拖图片到画布：把 dataURL 塞进拖拽数据，画布那边负责落图 */
function onDocDragStart(e) {
  const img = e.target && e.target.closest ? e.target.closest('img') : null
  if (!img || !img.src) return
  try {
    e.dataTransfer.setData('text/plain', img.src)
    e.dataTransfer.effectAllowed = 'copy'
    // 拖的时候给个跟着鼠标走的小预览，方便对准落点
    const dup = document.createElement('div')
    dup.className = 'dragghost'
    dup.style.cssText = 'position:fixed;left:-9999px;top:0;border-radius:6px;overflow:hidden;' +
      'box-shadow:0 6px 18px rgba(0,0,0,.28);background:#fff;padding:2px'
    const im = document.createElement('img')
    im.src = img.src
    const w = Math.min(160, img.naturalWidth || 160)
    im.style.cssText = 'display:block;width:' + w + 'px;height:auto;opacity:.92'
    dup.appendChild(im)
    document.body.appendChild(dup)
    e.dataTransfer.setDragImage(dup, Math.round(w / 2), 18)
    setTimeout(() => dup.remove(), 0)
  } catch { /* 忽略 */ }
}

// ---------- 导入图片 / PDF 进画布 ----------
async function onCanvasFiles(payload) {
  const files = (payload && payload.files) || []
  if (!files.length) return
  importing.value = true
  try {
    for (const f of files) {
      if (isCanvasPdf(f)) {
        await importPdfToCanvas(f, paneRef.value)
        dsay(`已把「${f.name}」放进画布，可以在画布上拖框裁剪`)
      } else if (isCanvasImage(f)) {
        await uploadImageToCanvas(f, paneRef.value)
        dsay(`已把「${f.name}」放进画布`)
      } else {
        dsay('只支持图片（png/jpg）和 PDF')
      }
    }
  } catch (e) {
    dsay('导入失败：' + api.errText(e))
  } finally {
    importing.value = false
  }
}
function pickCanvasFiles() { canvasFileEl.value && canvasFileEl.value.click() }
async function onPickCanvasFiles(ev) {
  const files = Array.from((ev.target && ev.target.files) || [])
  ev.target.value = ''
  await onCanvasFiles({ files })
}

// ---------- AI 悬浮球 ----------
const aiOpen = ref(false)
const aiText = ref('')
const aiBusy = ref(false)
const aiOut = ref('')
const AI_ACTS = [
  { k: 'intro', n: '写题干引入', q: '请为我这段化学竞赛题的题干写 2 句引入语（说明背景与考点），直接给中文正文，不要解释。' },
  { k: 'polish', n: '美化文字', q: '请把下面的文字润色成规范的化学竞赛题面中文：句子通顺、术语准确、不改变事实，直接给结果。' },
  { k: 'fmt', n: '排版建议', q: '下面是一道化学竞赛题的草稿。请给出排版建议（分段、小问编号、公式位置、图文关系），并给出重排后的纯文本。' },
]
async function runAi(q) {
  const text = (aiText.value || '').trim()
  if (!text && !q) { aiOut.value = '先把要处理的文字粘进来（或者选个动作）'; return }
  aiBusy.value = true
  aiOut.value = ''
  try {
    const r = await api.post('/api/ai/chat', {
      messages: [
        { role: 'system', content: '你是化学竞赛命题助手，只输出可直接放进试卷的中文文本，不要客套话。' },
        { role: 'user', content: `${q}\n\n${text}` },
      ],
      page: 'docdesk',
      search: false,
    })
    aiOut.value = String(r.text || r.content || r.answer || '').trim() || '（模型没有返回内容）'
  } catch (e) {
    aiOut.value = 'AI 调用失败：' + api.errText(e)
  } finally {
    aiBusy.value = false
  }
}
async function aiInsert() {
  const t = aiOut.value.trim()
  if (!t || !docRef.value) return
  const html = t.split('\n').filter((x) => x.trim()).map((x) => `<p>${x.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))}</p>`).join('')
  const ok = await docRef.value.insertHtml(html, { atEnd: true })
  dsay(ok ? '已把结果写进文档末尾（要撤销就按 Ctrl+Z）' : '写入被拒：' + (docRef.value.lastError() || '未知原因'))
  if (ok) aiOpen.value = false
}
</script>

<template>
  <div class="desk">
    <div class="dtop">
      <button class="btn" title="新建一份空 Word 文档" @click="newDoc">新建文档</button>
      <button class="btn" :title="rf ? '把右边的画布展开' : '把右边的画布收起，文档占满'" @click="toggleCanvas">
        {{ rf ? '显示画布' : '收起画布' }}
      </button>
      <span class="sp" />
      <div class="modesw" title="在「文档排版」和「自由画布」之间切换">
        <button :class="{ on: props.mode === 'doc' }" @click="emit2('mode', 'doc')">文档</button>
        <button :class="{ on: props.mode === 'canvas' }" @click="emit2('mode', 'canvas')">画布</button>
      </div>
      <span v-if="dirty && saving" class="mut">正在自动保存…</span>
      <span v-else-if="savedAt" class="mut">已自动保存 {{ savedAt }}</span>
      <span v-else class="mut">改完自动保存，不用点保存</span>
      <span v-if="dmsg" class="dmsg">{{ dmsg }}</span>
    </div>

    <div class="dbody" :class="{ drag: dragging }">
      <section class="mpane" :style="docStyle"
               @dragstart.capture="onDocDragStart">
        <DocEditorPane ref="docRef" @changed="onDocChanged" />
        <button class="aifab" title="AI 助手：写题干、润色、排版" @click="aiOpen = !aiOpen">
          <span class="dot"></span>AI
        </button>
        <div v-if="aiOpen" class="aipanel">
          <div class="aihead">
            <b>AI 助手</b>
            <span class="mut">改完可以按 Ctrl+Z 撤销</span>
            <span class="sp" />
            <button class="x" title="收起" @click="aiOpen = false">×</button>
          </div>
          <textarea v-model="aiText" class="aiin" placeholder="把题干草稿粘在这里，或者直接选下面的动作…"></textarea>
          <div class="airow">
            <button v-for="a in AI_ACTS" :key="a.k" class="btn sm" :disabled="aiBusy" @click="runAi(a.q)">{{ a.n }}</button>
            <button class="btn sm" :disabled="aiBusy" @click="runAi('')">自定义</button>
          </div>
          <div v-if="aiBusy" class="mut">正在想…</div>
          <div v-if="aiOut" class="aiout">
            <pre>{{ aiOut }}</pre>
            <div class="airow">
              <button class="btn sm" @click="aiInsert">写进文档末尾</button>
              <button class="btn sm" @click="aiText = aiOut; aiOut = ''">当作草稿继续改</button>
            </div>
          </div>
        </div>
      </section>

      <div class="split" :class="{ on: dragging }" title="拖动调整左右宽度" @pointerdown="startSplit">
        <span></span>
      </div>

      <section v-show="!rf" class="rpane">
        <CanvasPane ref="paneRef" title="画布" :max-pages="1" to-doc image-only
                    hint="把图拖进来，或点「＋ 文本」；选中图可裁剪、可送进左边文档"
                    @toDoc="onToDoc" @files="onCanvasFiles" @changed="onCanvasChanged" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.desk { display: flex; flex-direction: column; height: calc(100vh - 118px); min-height: 0;
  width: 100%; max-width: 100%; overflow: hidden; }
.dtop { display: flex; align-items: center; gap: 8px; padding: 6px 2px 8px; flex-wrap: wrap; }
.dtop .sp { flex: 1; }
.dtop .mut { font-size: 12px; color: #98a2b3; }
.dtop .modesw { display: inline-flex; border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; margin-right: 8px; }
.modesw button { border: 0; background: #fff; color: #4b5563; font-size: 12.5px; padding: 4px 12px; cursor: pointer; }
.modesw button.on { background: #2563eb; color: #fff; }
.dmsg { font-size: 12px; color: #1c8c46; background: #f2fbf5; border: 1px solid #cdebd8;
  border-radius: 999px; padding: 2px 10px; }
.dbody { display: flex; align-items: stretch; gap: 0; flex: 1; min-height: 0;
  width: 100%; overflow: hidden; }
.dbody.drag { cursor: col-resize; user-select: none; }
.mpane { position: relative; min-width: 0; display: flex; flex-direction: column; padding-right: 6px; }
.mpane :deep(.doce) { height: 100%; }
.rpane :deep(.cvhead) { flex-wrap: wrap; row-gap: 4px; }
.rpane { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; padding-left: 6px; overflow: hidden; }
.rpane :deep(.canvas-wrap) { overflow: auto; }
.split { flex: 0 0 10px; align-self: stretch; cursor: col-resize; display: flex;
  align-items: center; justify-content: center; }
.split span { width: 4px; height: 42px; border-radius: 3px; background: #d7dce5; }
.split:hover span, .split.on span { background: #2f6fed; }
.aifab { position: absolute; right: 16px; bottom: 14px; height: 34px; padding: 0 14px; border-radius: 999px;
  border: 0; background: #2f6fed; color: #fff; font-size: 13px; box-shadow: 0 6px 18px rgba(47,111,237,.35);
  display: flex; align-items: center; gap: 6px; cursor: pointer; z-index: 5; }
.aifab .dot { width: 7px; height: 7px; border-radius: 50%; background: #fff; }
.aipanel { position: absolute; right: 16px; bottom: 56px; width: 380px; max-width: 92%; background: #fff;
  border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 18px 44px rgba(20,24,32,.18);
  padding: 10px; z-index: 6; }
.aihead { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.aihead .sp { flex: 1; }
.aihead .x { border: 0; background: transparent; font-size: 18px; line-height: 1; cursor: pointer; color: #98a2b3; }
.aiin { width: 100%; min-height: 84px; resize: vertical; border: 1px solid var(--line); border-radius: 8px;
  padding: 8px; font-size: 13px; }
.airow { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.aiout pre { white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.7;
  background: #f7f9fc; border: 1px solid var(--line); border-radius: 8px; padding: 8px; max-height: 220px; overflow: auto; }
</style>
