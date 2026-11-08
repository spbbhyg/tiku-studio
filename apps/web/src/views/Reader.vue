<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()
const docs = ref([])
const docId = ref('')
const outline = ref(null)
const loading = ref(false)
const err = ref('')
const msg = ref('')

const pageNo = ref(1)
const zoom = ref(1.4)
const activeId = ref('')
const notes = ref({})

const showMarks = ref(true)
const annot = reactive({ on: false, tool: 'pen', color: '#e11d48', size: 3 })
const annotCanvas = ref(null)
const strokes = ref({})
let drawing = null
const showRuns = ref(true)
const settings = ref({ fontSize: 'md', color: 'ink', lineHeight: 1.6 })
const FONT_PX = { sm: 14, md: 16, lg: 19 }
const COLOR_HEX = { ink: '#0f172a', blue: '#1d4ed8', gray: '#475569' }
const textStyle = computed(() => ({
  fontSize: FONT_PX[settings.value.fontSize] + 'px',
  color: COLOR_HEX[settings.value.color],
  lineHeight: settings.value.lineHeight,
}))

const activeDoc = computed(() => docs.value.find((d) => d.doc_id === docId.value) || null)
const pages = computed(() => {
  const n = Number(activeDoc.value?.page_count) || 1
  return Array.from({ length: n }, (_, i) => i + 1)
})
const sentences = computed(() => (outline.value && outline.value.sentences) || [])
const sections = computed(() => (outline.value && outline.value.sections) || [])
const pageSentences = computed(() => sentences.value.filter((s) => s.page === pageNo.value))
const numbers = computed(() => {
  const m = {}
  sentences.value.forEach((s, i) => { m[s.id] = i + 1 })
  return m
})
const activeSentence = computed(() => sentences.value.find((s) => s.id === activeId.value) || null)
const activeNumber = computed(() => (activeSentence.value ? numbers.value[activeSentence.value.id] : null))
const activeSection = computed(() => {
  const s = activeSentence.value
  if (!s) return ''
  return (sections.value.find((x) => x.key === s.section) || {}).label || ''
})
const activeNote = computed(() => notes.value[activeId.value] || { translation: '', note: '' })
const notesModel = computed({
  get: () => activeNote.value.translation,
  set: (v) => { if (activeId.value) setNote(activeId.value, { translation: v }) },
})
const noteModel = computed({
  get: () => activeNote.value.note,
  set: (v) => { if (activeId.value) setNote(activeId.value, { note: v }) },
})
const pendingCount = computed(
  () => sentences.value.filter((s) => !(notes.value[s.id] || {}).translation).length,
)

const pageUrl = computed(() => (docId.value
  ? `/api/manual/doc/${docId.value}/page/${pageNo.value}.png?zoom=${zoom.value}`
  : ''))

// 文字层：把 PDF 的每行文字铺成透明层，支持鼠标选中后立即翻译
const textLines = ref([])
const stageH = ref(0)
const pageImg = ref(null)
const sel = ref(null)
let textSeq = 0

async function loadTextLayer() {
  textLines.value = []
  if (!docId.value) return
  const seq = ++textSeq
  try {
    const d = await api.get(`/api/manual/doc/${docId.value}/page/${pageNo.value}/text`)
    if (seq === textSeq) textLines.value = d.lines || []
  } catch (e) { /* 扫描页没有文字层，忽略 */ }
}

function syncStageH() {
  stageH.value = pageImg.value ? pageImg.value.clientHeight : 0
  if (annot.on) sizeAnnotCanvas()
}

function lineStyle(l) {
  const h = (l.y1 - l.y0) * stageH.value
  return {
    left: l.x0 * 100 + '%',
    top: l.y0 * 100 + '%',
    width: Math.max(0.5, (l.x1 - l.x0) * 100) + '%',
    height: h ? h + 'px' : '1.4em',
    fontSize: h ? h * 0.94 + 'px' : '12px',
    lineHeight: h ? h + 'px' : '1.4',
  }
}

function onPageMouseUp() {
  const s = window.getSelection()
  const text = (s ? s.toString() : '').replace(/\s+/g, ' ').trim()
  if (text.length < 2) return
  sel.value = { text: text.slice(0, 800), translation: '', busy: false, err: '' }
}

async function translateSelection() {
  if (!sel.value) return
  sel.value = { ...sel.value, busy: true, err: '' }
  try {
    const r = await api.post('/api/reader/translate', { text: sel.value.text })
    sel.value = { ...sel.value, translation: r.text || '', busy: false }
  } catch (e) {
    sel.value = { ...sel.value, busy: false, err: String(e.message || e) }
  }
}

async function selectionToAuthoring() {
  if (!sel.value) return
  const parts = [sel.value.text, sel.value.translation].filter(Boolean).join('\n\n')
  // 选中段落 → 直接开一张人工整卷，题干预填这段、出处自动带上 doc_id / 页码
  sessionStorage.setItem('tiku:exam-seed', JSON.stringify({
    text: parts,
    source: {
      doc_id: docId.value,
      page: pageNo.value,
      kind: 'text',
      title: activeDoc.value?.filename || '',
      quote: (sel.value.text || '').slice(0, 120),
    },
  }))
  router.push('/papers/new')
}

function clearSelection() { sel.value = null }

const saving = ref('')
const translating = ref({})
const translateErr = ref({})
const batch = ref(null)
const exporting = ref(false)
const lastExport = ref(null)   // { path, dir, file, size } 最近一次导出
const expSettings = ref({ fontSize: 'md', color: 'ink', lineHeight: 1.6, underline: true, appendix: true })

function noteOf(id) { return notes.value[id] || { translation: '', note: '' } }
function setNote(id, patch) {
  notes.value = { ...notes.value, [id]: { translation: '', note: '', ...noteOf(id), ...patch } }
}

async function loadDocs() {
  err.value = ''
  try {
    const d = await api.get('/api/manual/docs')
    docs.value = d.docs || []
    if (!docId.value && docs.value.length) docId.value = docs.value[0].doc_id
  } catch (e) { err.value = String(e.message || e) }
}

const uploading = ref(false)
async function uploadDoc(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file) return
  uploading.value = true
  err.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    const d = await api.postForm('/api/manual/upload', fd)
    await loadDocs()
    docId.value = d.doc_id
    msg.value = `已上传《${d.filename}》，共 ${d.page_count} 页`
  } catch (e2) { err.value = String(e2.message || e2) }
  finally { uploading.value = false }
}

async function loadOutline() {
  if (!docId.value) { outline.value = null; return }
  loading.value = true
  err.value = ''
  activeId.value = ''
  try {
    const d = await api.get(`/api/reader/doc/${docId.value}/outline`)
    outline.value = d
    notes.value = d.notes || {}
    pageNo.value = 1
    sel.value = null
    await loadTextLayer()
    if (!d.has_text) {
      msg.value = '这份 PDF 没有文字层（扫描件）。重点句识别需要文字：可直接裁剪成图片题。'
    } else if (!(d.sentences || []).length) {
      msg.value = '没有识别到明显的小标题或高价值句，换一份排版规整的文献更准，也可在左侧手动点选。'
    } else {
      msg.value = ''
    }
  } catch (e) {
    err.value = String(e.message || e)
    outline.value = null
  } finally { loading.value = false }
}

function focusSentence(s) {
  activeId.value = s.id
  pageNo.value = s.page
}

async function persist(id) {
  if (!docId.value) return
  const s = sentences.value.find((x) => x.id === id)
  saving.value = id
  try {
    await api.post('/api/reader/notes', {
      doc_id: docId.value,
      sentence_key: id,
      page_no: s ? s.page : null,
      source: s ? s.text : '',
      translation: noteOf(id).translation,
      note: noteOf(id).note,
    })
  } catch (e) { translateErr.value = { ...translateErr.value, [id]: String(e.message || e) } }
  finally { if (saving.value === id) saving.value = '' }
}

async function translateOne(s) {
  translating.value = { ...translating.value, [s.id]: true }
  const next = { ...translateErr.value }
  delete next[s.id]
  translateErr.value = next
  try {
    const r = await api.post('/api/reader/translate', { text: s.text })
    setNote(s.id, { translation: r.text || '' })
    await persist(s.id)
  } catch (e) {
    translateErr.value = { ...translateErr.value, [s.id]: String(e.message || e) }
  } finally {
    const t = { ...translating.value }
    delete t[s.id]
    translating.value = t
  }
}

async function translateAll() {
  const pending = sentences.value.filter((s) => !noteOf(s.id).translation)
  if (!pending.length) return
  batch.value = { done: 0, total: pending.length }
  for (let i = 0; i < pending.length; i++) {
    await translateOne(pending[i])
    batch.value = { done: i + 1, total: pending.length }
  }
  batch.value = null
}

async function exportPdf() {
  if (!docId.value || !sentences.value.length) return
  exporting.value = true
  err.value = ''
  lastExport.value = null
  try {
    const items = sentences.value.map((s) => ({
      number: numbers.value[s.id],
      page: s.page,
      rects: s.rects,
      source: s.text,
      translation: noteOf(s.id).translation,
      label: `[${numbers.value[s.id]}]`,
    }))
    const payload = { doc_id: docId.value, items, name: activeDoc.value?.filename || '', ...expSettings.value }
    const res = await fetch('/api/reader/export-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      let detail = await res.text()
      try { detail = JSON.parse(detail).detail || detail } catch (e) { /* keep */ }
      throw new Error(detail || `导出失败（${res.status}）`)
    }
    const data = await res.json()
    lastExport.value = data
    msg.value = `已生成标注版 PDF（${(data.size / 1024 / 1024).toFixed(1)} MB）：${data.file}`
  } catch (e) { err.value = String(e.message || e) }
  finally { exporting.value = false }
}

async function revealExport() {
  if (!lastExport.value) return
  try {
    await api.post('/api/reader/reveal', { path: lastExport.value.path })
  } catch (e) { err.value = String(e.message || e) }
}

function openExport() {
  if (!lastExport.value) return
  window.open(lastExport.value.url, '_blank')
}

async function sendToAuthoring() {
  const s = activeSentence.value
  if (!s) return
  try {
    await api.post('/api/reader/notes', {
      doc_id: docId.value, sentence_key: s.id, page_no: s.page,
      source: s.text, translation: noteOf(s.id).translation, note: noteOf(s.id).note,
    })
    sessionStorage.setItem('tiku:seed', JSON.stringify({
      text: [s.text, noteOf(s.id).translation].filter(Boolean).join('\n\n'),
      source_title: activeDoc.value?.filename || '',
      source_citation: s.section ? `${activeSection.value} · 第 ${s.page} 页` : '',
    }))
    msg.value = '已把这句话放到「出题」页的素材区，切过去就能用。'
  } catch (e) { err.value = String(e.message || e) }
}

watch(docId, loadOutline)
watch(pageNo, () => { sel.value = null; loadTextLayer() })
watch(showRuns, (v) => { if (v) loadTextLayer() })
const annoKey = computed(() => `${docId.value}:${pageNo.value}`)

function annoList() {
  if (!strokes.value[annoKey.value]) strokes.value[annoKey.value] = []
  return strokes.value[annoKey.value]
}

function drawStroke(ctx, st, w, h) {
  if (!st.points || !st.points.length) return
  ctx.save()
  ctx.globalCompositeOperation = st.tool === 'eraser' ? 'destination-out' : 'source-over'
  ctx.strokeStyle = st.color || '#e11d48'
  ctx.lineWidth = st.tool === 'eraser' ? Math.max(8, (st.size || 3) * 3) : (st.size || 3)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  st.points.forEach((pt, i) => {
    const x = pt[0] * w, y = pt[1] * h
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
  })
  if (st.points.length === 1) ctx.lineTo(st.points[0][0] * w + 0.1, st.points[0][1] * h + 0.1)
  ctx.stroke()
  ctx.restore()
}

function redrawAnnot() {
  const cv = annotCanvas.value
  if (!cv || !cv.width || !cv.height) return
  const ctx = cv.getContext('2d')
  ctx.clearRect(0, 0, cv.width, cv.height)
  for (const st of annoList()) drawStroke(ctx, st, cv.width, cv.height)
}

function sizeAnnotCanvas() {
  const cv = annotCanvas.value
  const img = pageImg.value
  if (!cv || !img) return
  const w = img.clientWidth, h = img.clientHeight
  if (!w || !h) return
  cv.width = w; cv.height = h
  redrawAnnot()
}

async function toggleAnnot() {
  annot.on = !annot.on
  if (annot.on) { await nextTick(); sizeAnnotCanvas() }
}

function annoPos(e) {
  const cv = annotCanvas.value
  const r = cv.getBoundingClientRect()
  return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
          Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]
}

function annoDown(e) {
  if (!annot.on) return
  e.preventDefault()
  const cv = annotCanvas.value
  if (!cv) return
  const st = { tool: annot.tool, color: annot.color, size: annot.size, points: [annoPos(e)] }
  annoList().push(st)
  drawing = st
  cv.setPointerCapture?.(e.pointerId)
}

function annoMove(e) {
  if (!drawing || !annot.on) return
  e.preventDefault()
  const pt = annoPos(e)
  const last = drawing.points[drawing.points.length - 1]
  if (last && Math.hypot(pt[0] - last[0], pt[1] - last[1]) < 0.002) return
  drawing.points.push(pt)
  const cv = annotCanvas.value
  const ctx = cv.getContext('2d')
  ctx.save()
  ctx.globalCompositeOperation = drawing.tool === 'eraser' ? 'destination-out' : 'source-over'
  ctx.strokeStyle = drawing.color
  ctx.lineWidth = drawing.tool === 'eraser' ? Math.max(8, drawing.size * 3) : drawing.size
  ctx.lineCap = 'round'; ctx.lineJoin = 'round'
  ctx.beginPath()
  ctx.moveTo(last[0] * cv.width, last[1] * cv.height)
  ctx.lineTo(pt[0] * cv.width, pt[1] * cv.height)
  ctx.stroke()
  ctx.restore()
}

function annoUp() { drawing = null }

function clearAnnot() {
  strokes.value[annoKey.value] = []
  redrawAnnot()
}

watch(pageNo, async () => { await nextTick(); if (annot.on) sizeAnnotCanvas() })
watch(docId, async () => { await nextTick(); if (annot.on) sizeAnnotCanvas() })

onMounted(loadDocs)
</script>

<template>
  <h2>精读</h2>
  <p class="muted" style="margin-top:-8px">
    左边章节导航、中间原页、右边译文与批注。重点句由本地算法识别（不花 token），
    译文可逐句或一键翻译，标注版 PDF 会把下划线、编号和译文附录写回原件。
  </p>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="warnbox">{{ msg }}</div>

  <div v-if="!docs.length" class="card">
    <p class="muted">还没有上传文献。先到「出题」页用「＋ 放入 PDF」传一份，或直接在这里上传。</p>
    <p class="muted">上传后回到本页即可读；同一份文件在「出题」「精读」之间共用。</p>
  </div>

  <template v-else>
    <div class="card readerbar">
      <label class="f" style="margin:0;min-width:260px">
        <span>文献</span>
        <select v-model="docId">
          <option v-for="d in docs" :key="d.doc_id" :value="d.doc_id">
            {{ d.filename }}（{{ d.page_count }} 页）
          </option>
        </select>
      </label>
      <div class="btnrow" style="margin-left:auto">
        <span class="chip brand">{{ sentences.length }} 处重点</span>
        <span class="chip">{{ pendingCount ? `待翻译 ${pendingCount}` : '已全部翻译' }}</span>
        <label class="inline"><input v-model="showMarks" type="checkbox" />显示标注</label>
        <label class="inline"><input v-model="showRuns" type="checkbox" />文字层</label>
        <label class="uploadbtn">
          {{ uploading ? '上传中…' : '＋ 放入 PDF' }}
          <input type="file" accept="application/pdf" :disabled="uploading" @change="uploadDoc" />
        </label>
      </div>
    </div>

    <div class="reader">
      <aside class="pane nav">
        <div class="panehead">
          <b>章节目录</b>
          <span class="muted">{{ sections.length }} 节</span>
        </div>
        <p v-if="loading" class="muted" style="padding:0 12px">正在识别重点句…</p>
        <template v-for="g in sections" :key="g.key">
          <div class="sec">
            <span>{{ g.label }}</span>
            <span class="muted">{{ g.page_from === g.page_to ? `第 ${g.page_from} 页` : `第 ${g.page_from}–${g.page_to} 页` }} · {{ g.count }}</span>
          </div>
          <button v-for="s in sentences.filter((x) => x.section === g.key)" :key="s.id"
                  class="sent" :class="{ on: s.id === activeId }" @click="focusSentence(s)">
            <span class="num">{{ numbers[s.id] }}</span>
            <span class="txt">
              <span class="line2">{{ s.text }}</span>
              <span class="muted sm">第 {{ s.page }} 页</span>
            </span>
          </button>
        </template>
        <p v-if="!loading && !sections.length" class="muted" style="padding:0 12px">
          没有识别到章节，可在右侧直接选中文字翻译。
        </p>
      </aside>

      <section class="pane stage">
        <div class="stagebar">
          <button :disabled="pageNo <= 1" @click="pageNo--">上一页</button>
          <input v-model.number="pageNo" type="number" min="1" :max="pages.length" class="pageno" />
          <span class="muted">/ {{ pages.length }}</span>
          <button :disabled="pageNo >= pages.length" @click="pageNo++">下一页</button>
          <select v-model.number="zoom" style="width:auto;margin-left:auto">
            <option :value="1">100%</option>
            <option :value="1.4">140%</option>
            <option :value="2">200%</option>
            <option :value="3">300%</option>
          </select>
          <button :class="{ on: annot.on }" @click="toggleAnnot">✎ 批注</button>
          <template v-if="annot.on">
            <button :class="{ on: annot.tool === 'pen' }" @click="annot.tool = 'pen'">画笔</button>
            <button :class="{ on: annot.tool === 'eraser' }" @click="annot.tool = 'eraser'">橡皮擦</button>
            <input v-model="annot.color" type="color" style="width:30px;height:26px;padding:0;border:1px solid #d0d5dd;border-radius:4px" />
            <input v-model.number="annot.size" type="range" min="1" max="12" style="width:90px" />
            <button @click="clearAnnot">清除本页</button>
          </template>
        </div>
        <div class="pagescroll">
          <div v-if="pageUrl" class="pagewrap">
            <img ref="pageImg" :src="pageUrl" alt="文献页" draggable="false" @load="syncStageH" />
            <div v-if="showRuns && textLines.length" class="textlayer" @mouseup="onPageMouseUp">
              <span v-for="(l, i) in textLines" :key="i" class="tl" :style="lineStyle(l)">{{ l.text }}</span>
            </div>
            <canvas v-if="annot.on" ref="annotCanvas" class="annotcanvas"
                    @pointerdown="annoDown" @pointermove="annoMove" @pointerup="annoUp" @pointerleave="annoUp"
                    @pointercancel="annoUp"></canvas>
            <template v-if="showMarks">
              <template v-for="s in pageSentences" :key="s.id">
                <span v-for="(r, ri) in s.rects" :key="s.id + '-' + ri" class="mark"
                      :class="{ on: s.id === activeId }"
                      :style="{ left: r[0] * 100 + '%', top: r[1] * 100 + '%', width: (r[2] - r[0]) * 100 + '%', height: (r[3] - r[1]) * 100 + '%' }"></span>
                <button class="badge" :class="{ on: s.id === activeId }"
                        :style="{ left: 'calc(' + s.rects[0][0] * 100 + '% - 26px)', top: s.rects[0][1] * 100 + '%' }"
                        @click="focusSentence(s)">{{ numbers[s.id] }}</button>
              </template>
            </template>
          </div>
          <p v-else class="muted">选择一份文献后开始阅读。</p>
        </div>
      </section>

      <aside class="pane side">
        <div v-if="activeSentence" class="card inner">
          <div class="sidehead">
            <b>重点句 {{ activeNumber }}</b>
            <span class="muted">{{ activeSection }} · 第 {{ activeSentence.page }} 页</span>
          </div>
          <p class="muted sm">原文</p>
          <p class="src">{{ activeSentence.text }}</p>

          <p class="muted sm" style="margin-top:12px">中文译文（可直接改）</p>
          <p v-if="translating[activeId]" class="muted">正在翻译…</p>
          <textarea v-else v-model="notesModel" :style="textStyle" rows="5"
                    placeholder="译文会显示在这里"
                    @blur="persist(activeId)"></textarea>
          <div class="btnrow">
            <button :disabled="translating[activeId]" @click="translateOne(activeSentence)">
              {{ translating[activeId] ? '翻译中…' : '重新翻译' }}
            </button>
            <button @click="persist(activeId)">保存</button>
            <span v-if="saving === activeId" class="muted">保存中…</span>
          </div>
          <p v-if="translateErr[activeId]" class="errbox">{{ translateErr[activeId] }}</p>

          <p class="muted sm" style="margin-top:12px">我的批注</p>
          <textarea v-model="noteModel" rows="3" placeholder="写下对这一句的理解…"
                    @blur="persist(activeId)"></textarea>
          <div class="btnrow" style="margin-top:10px">
            <button @click="sendToAuthoring">送去出题</button>
          </div>
        </div>

        <div v-else class="card inner">
          <p class="muted">点左侧的重点句，或直接点页面上的蓝色编号，即可查看译文。</p>
        </div>

        <div v-if="sel" class="card inner">
          <div class="sidehead">
            <b>选中文字</b>
            <button class="ghost sm" @click="clearSelection">关闭</button>
          </div>
          <p class="src">{{ sel.text }}</p>
          <p v-if="sel.busy" class="muted">正在翻译…</p>
          <p v-else-if="sel.translation" class="srct" :style="textStyle">{{ sel.translation }}</p>
          <p v-if="sel.err" class="errbox">{{ sel.err }}</p>
          <div class="btnrow">
            <button :disabled="sel.busy || !!sel.translation" @click="translateSelection">
              {{ sel.translation ? '已翻译' : '翻译这段' }}
            </button>
            <button class="primary" @click="selectionToAuthoring">用这段出题</button>
          </div>
        </div>

        <div class="card inner">
          <div class="sidehead"><b>批量与导出</b></div>
          <button class="primary" style="width:100%" :disabled="!!batch || !pendingCount" @click="translateAll">
            {{ batch ? `正在翻译 ${batch.done}/${batch.total}` : pendingCount ? `翻译全部重点句（${pendingCount} 条）` : '重点句已全部翻译' }}
          </button>

          <div class="grid cols2" style="margin-top:12px">
            <label class="f"><span>字号</span>
              <select v-model="expSettings.fontSize">
                <option value="sm">小</option><option value="md">中</option><option value="lg">大</option>
              </select>
            </label>
            <label class="f"><span>颜色</span>
              <select v-model="expSettings.color">
                <option value="ink">墨黑</option><option value="blue">蓝色</option><option value="gray">灰色</option>
              </select>
            </label>
          </div>
          <label class="inline" style="display:flex;margin-bottom:6px">
            <input v-model="expSettings.underline" type="checkbox" /> 原文画下划线
          </label>
          <label class="inline" style="display:flex;margin-bottom:10px">
            <input v-model="expSettings.appendix" type="checkbox" /> 追加译文附录
          </label>
          <button style="width:100%" :disabled="exporting || !sentences.length" @click="exportPdf">
            {{ exporting ? '正在生成…' : '导出标注版 PDF' }}
          </button>
          <div v-if="lastExport" class="expsaved">
            <div class="p" :title="lastExport.path">{{ lastExport.path }}</div>
            <div class="acts">
              <button class="ghost sm" @click="revealExport">打开所在文件夹</button>
              <button class="ghost sm" @click="openExport">预览</button>
            </div>
          </div>
        </div>

        <div class="card inner">
          <div class="sidehead"><b>阅读设置</b></div>
          <label class="f"><span>正文字号</span>
            <select v-model="settings.fontSize">
              <option value="sm">小</option><option value="md">中</option><option value="lg">大</option>
            </select>
          </label>
          <label class="f"><span>文字颜色</span>
            <select v-model="settings.color">
              <option value="ink">墨黑</option><option value="blue">蓝色</option><option value="gray">灰色</option>
            </select>
          </label>
          <label class="f"><span>行距 {{ settings.lineHeight.toFixed(1) }}</span>
            <input v-model.number="settings.lineHeight" type="range" min="1.2" max="2.2" step="0.1" />
          </label>
        </div>
      </aside>
    </div>
  </template>
</template>

<style scoped>
.readerbar { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap; }
.inline { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; font-size: 13px; color: var(--muted); }
.uploadbtn {
  position: relative; display: inline-block; padding: 6px 14px; border-radius: 8px;
  border: 1px solid var(--brand); color: var(--brand); font-size: 13px; cursor: pointer;
  background: #fff;
}
.uploadbtn:hover { background: var(--brand-soft); }
.uploadbtn input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
.reader { display: grid; grid-template-columns: 260px minmax(320px, 1fr) 340px; gap: 12px; align-items: start; }
.pane { background: #fff; border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.nav { max-height: 78vh; overflow: auto; padding-bottom: 10px; }
.panehead, .sidehead {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  padding: 12px 14px 8px;
}
.sec {
  display: flex; justify-content: space-between; gap: 8px; align-items: baseline;
  padding: 10px 14px 4px; font-size: 13px; font-weight: 600; color: #344054;
  border-top: 1px solid #f2f4f7;
}
.sent {
  display: flex; gap: 8px; width: calc(100% - 16px); margin: 2px 8px;
  padding: 7px 8px; border: 1px solid transparent; border-radius: 8px;
  background: transparent; text-align: left; cursor: pointer;
}
.sent:hover { background: #f7f9fc; }
.sent.on { background: var(--brand-soft); border-color: #c9dbff; }
.num {
  flex: 0 0 auto; min-width: 20px; height: 20px; line-height: 20px; text-align: center;
  border-radius: 5px; background: var(--brand-soft); color: var(--brand);
  font-size: 11px; font-weight: 700;
}
.sent.on .num { background: var(--brand); color: #fff; }
.txt { min-width: 0; }
.line2 {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; font-size: 12.5px; line-height: 1.45; color: #344054;
}
.sm { font-size: 12px; }
.stage { overflow: hidden; }
.stagebar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--line); }
.stagebar button { padding: 4px 10px; }
.pageno { width: 64px; text-align: center; }
.pagescroll { max-height: 76vh; overflow: auto; padding: 14px; background: #f8fafc; }
.pagewrap { position: relative; width: fit-content; margin: 0 auto; box-shadow: 0 6px 20px rgba(16, 24, 40, .1); background: #fff; }
.pagewrap img { display: block; max-width: 100%; }
.annotcanvas { position: absolute; inset: 0; z-index: 20; cursor: crosshair; touch-action: none; }
.textlayer { position: absolute; inset: 0; z-index: 2; }
.tl {
  position: absolute; color: transparent; overflow: hidden; white-space: pre;
  cursor: text; font-family: "Times New Roman", "Songti SC", serif;
}
.tl::selection { background: rgba(37, 99, 235, .28); color: transparent; }
.srct { font-size: 14px; line-height: 1.75; color: #0f172a; }
.ghost.sm { padding: 2px 8px; font-size: 12px; background: transparent; border-color: transparent; color: var(--muted); }
.mark {
  position: absolute; border-bottom: 2px solid rgba(37, 99, 235, .5);
  cursor: pointer; border-radius: 2px; z-index: 1;
  pointer-events: none;
}
.mark.on { border-bottom-color: #1d4ed8; box-shadow: inset 0 -7px 0 rgba(37, 99, 235, .12); }
.badge {
  position: absolute; transform: translateY(-2px); min-width: 22px; height: 18px; padding: 0 5px;
  border-radius: 5px; border: none; background: #2563eb; color: #fff;
  font-size: 10px; font-weight: 700; cursor: pointer; z-index: 3;
  box-shadow: 0 1px 3px rgba(0,0,0,.2);
}
.badge.on { background: #1e40af; }
.side { max-height: 78vh; overflow: auto; padding: 10px; background: #f8fafc; }
.inner { margin-bottom: 10px; padding: 12px 14px; }
.inner:last-child { margin-bottom: 0; }
.src { font-size: 13px; color: #475467; line-height: 1.7; white-space: pre-wrap; }
.grid.cols2 { gap: 10px; }
@media (max-width: 1180px) {
  .reader { grid-template-columns: 220px 1fr; }
  .side { grid-column: 1 / -1; max-height: none; display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .inner { margin-bottom: 0; }
}
@media (max-width: 820px) {
  .reader { grid-template-columns: 1fr; }
  .nav { max-height: 40vh; }
  .side { grid-template-columns: 1fr; }
}

.expsaved { margin-top: 6px; background: #f0f7ff; border: 1px solid #cfe2ff; border-radius: 8px; padding: 8px; }
.expsaved .p { font-size: 11px; color: #33507a; word-break: break-all; margin-bottom: 6px; }
.expsaved .acts { display: flex; gap: 6px; }
</style>
