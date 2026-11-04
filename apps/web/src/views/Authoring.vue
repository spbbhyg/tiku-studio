<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { renderMath } from '../math.js'
import { loadShortcuts, matchCombo } from '../shortcuts.js'
import { flowText } from '../textflow.js'
import CanvasPane from '../components/CanvasPane.vue'
import FormulaPad from '../components/FormulaPad.vue'

const drawer = ref('')            // '' | 'formula' | 'struct'
const phoneHint = ref(typeof window !== 'undefined' && window.innerWidth < 820)
const hintClosed = ref(false)
const railOpen = ref(true)
const toggleCrop = () => { qOpen.value = !qOpen.value; if (qOpen.value) drawer.value = '' }
const toggleTool = (k) => { drawer.value = drawer.value === k ? '' : k; if (drawer.value) qOpen.value = false }        // 左侧功能轨
const smiles = ref('')
const smilesSvg = ref('')
const smilesMsg = ref('')

function insertFormula(p) {
  const text = (p && p.text) || ''
  if (!text) return
  const target = (p && p.target) || 'stem'
  if (target.startsWith('answer') && aPane.value) aPane.value.addText(text)
  else if (qPane.value) qPane.value.addText(text)
  drawer.value = ''
}

async function previewSmiles() {
  smilesMsg.value = ''
  smilesSvg.value = ''
  const v = smiles.value.trim()
  if (!v) return
  try {
    const r = await api.post('/api/v2/chem/preview', { source: v, width: 360, height: 240 })
    smilesSvg.value = String(r.svg || '').replace(/^<\?xml[^>]*\?>\s*/i, '')
  } catch (e) {
    smilesMsg.value = '渲染失败：' + (e.message || e)
  }
}

// ---------- 画布标签页 + 小问 ----------
const ctab = ref('q')
const subs = ref([])
const subTarget = ref('q')
const addSub = () => subs.value.push({ hint: '' })
const delSub = (i) => subs.value.splice(i, 1)
function moveSub(i, d) {
  const j = i + d
  if (j < 0 || j >= subs.value.length) return
  const arr = subs.value
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}
function insertSub(i) {
  const pane = subTarget.value === 'q' ? qPane.value : aPane.value
  if (!pane) return
  ctab.value = subTarget.value
  pane.addText('(' + (i + 1) + ')')
}

// ---------- Ketcher 手绘板（iframe + postMessage） ----------
const structTab = ref('smiles')
const kframe = ref(null)
const ketcherReady = ref(false)
let kseq = 0
const kpending = new Map()

function ketcherPost(payload, timeout = 20000) {
  return new Promise((resolve) => {
    const id = 'k' + (++kseq)
    kpending.set(id, resolve)
    const win = kframe.value && kframe.value.contentWindow
    if (!win) return resolve({ ok: false, error: '画板未就绪' })
    win.postMessage({ ...payload, id }, '*')
    setTimeout(() => { if (kpending.has(id)) { kpending.delete(id); resolve({ ok: false, error: '超时' }) } }, timeout)
  })
}

function ketcherPing() {
  ketcherReady.value = false
  setTimeout(() => ketcherPost({ type: 'ketcher:ping' }, 8000), 600)
}

function switchStruct(k) {
  structTab.value = k
  if (k === 'draw') setTimeout(ketcherPing, 100)
}

async function ketcherSetFromInput() {
  const v = smiles.value.trim()
  if (!v) { smilesMsg.value = '先在上面填 SMILES，或直接在画板上画'; return }
  const r = await ketcherPost({ type: 'ketcher:set', molfile: v })
  smilesMsg.value = r.ok ? '已载入画板，可继续增删原子' : ('载入失败：' + (r.error || ''))
}

async function ketcherTake() {
  const r = await ketcherPost({ type: 'ketcher:get' })
  if (!r.ok || !r.molfile) { smilesMsg.value = '画板里还没有结构'; return }
  smilesMsg.value = '正在生成图…'
  try {
    const res = await api.post('/api/v2/chem', { kind: 'structure', molfile: r.molfile, svg: r.svg || '', renderer: 'ketcher' })
    const svgOut = (res && res.svg) || r.svg || ''
    if (svgOut) smilesSvg.value = String(svgOut).replace(/^<\?xml[^>]*\?>\s*/i, '')

    // 直接把结构图插入当前画布（题目/答案按当前标签页决定）
    let inserted = false
    if (res && res.id) {
      try {
        const png = await api.post('/api/v2/chem/' + res.id + '/png')
        const pane = ctab.value === 'a' ? aPane.value : qPane.value
        if (png && png.path && pane) {
          await pane.addImage(png.path)
          inserted = true
        }
      } catch (e) {
        smilesMsg.value = '出图失败：' + (e.message || e)
      }
    }
    smilesMsg.value = '已保存结构对象 #' + (res && res.id ? res.id : '') +
      (inserted ? '，已插入' + (ctab.value === 'a' ? '答案' : '题目') + '画布' : '（可复制 SVG 或截图贴进题图）')
  } catch (e) {
    smilesMsg.value = '保存失败：' + (e.message || e)
  }
}

onMounted(() => {
  window.addEventListener('message', (ev) => {
    const d = ev.data || {}
    if (d.type === 'ketcher:ready') { ketcherReady.value = true; return }
    if (d.type === 'ketcher:result' && d.id && kpending.has(d.id)) {
      kpending.get(d.id)(d)
      kpending.delete(d.id)
    }
  })
})

async function insertStructure() {
  const v = smiles.value.trim()
  if (!v) return
  smilesMsg.value = ''
  try {
    const r = await api.post('/api/v2/chem', { kind: 'structure', smiles: v, renderer: 'rdkit' })
    if (r && r.svg) {
      smilesSvg.value = String(r.svg).replace(/^<\?xml[^>]*\?>\s*/i, '')
      smilesMsg.value = '已生成结构图，点「复制 SVG」或截图贴进题图'
    } else {
      smilesMsg.value = '已保存结构对象 #' + (r && r.id ? r.id : '')
    }
  } catch (e) {
    smilesMsg.value = '保存失败：' + (e.message || e)
  }
}

function copySvg() {
  if (!smilesSvg.value) return
  navigator.clipboard.writeText(smilesSvg.value)
  smilesMsg.value = 'SVG 已复制到剪贴板'
}

const route = useRoute()
const router = useRouter()

// ---------- 左栏：PDF 与裁剪 ----------
const docs = ref([])
const activeDoc = ref(null)
const pageNo = ref(1)
const zoom = ref(2)
const sel = ref(null)
const busy = ref('')
const msg = ref('')
const err = ref('')
const pageImg = ref(null)
const qPane = ref(null)
const aPane = ref(null)
const activePane = ref('q')
const qOpen = ref(false)
const mode = ref('crop')            // crop = 框选裁剪；text = 选择文字 / 翻译
const textLines = ref([])
const textLoading = ref(false)
const imgBox = reactive({ w: 0, h: 0 })
const readFrom = ref(1)             // AI 逐页精读：起始页
const readCount = ref(4)            // 一次连续读几页（0=读到末尾）
const readBusy = ref(false)
const readDone = ref(0)
const readTotal = ref(0)
const pageSummaries = ref([])       // [{page_no, summary, key_points, has_text}]
const answerBusy = ref(false)
const ocrAfterCrop = ref(false)      // 裁剪后直接把图片 OCR 成可编辑文字
const aiStem = ref('')               // 给 AI 读的题干文字（扫描图/图片题用）
const genCount = ref(5)              // AI 一次出几道题
const genTitle = ref('')             // 新试卷名（可空）
const genBusy = ref(false)
const paperPlan = reactive({ open: false })   // 「出题」先选 1-11 题
const paperStep = reactive({ active: false, total: 0, idx: 0, items: [], title: '', busy: false })
const viewerEl = ref(null)
const keyPoints = computed(() => (pageSummaries.value || []).flatMap(
  (s) => (s.key_points || []).map((k) => ({ ...k, page_no: s.page_no }))))
const analysisBusy = ref(false)
const analysisText = ref('')
const pop = reactive({ open: false, x: 0, y: 0, text: '', trans: '', busy: false, err: '', msg: '' })
const textCache = new Map()
let textSeq = 0

const pageUrl = computed(() => (activeDoc.value
  ? `/api/manual/doc/${activeDoc.value.doc_id}/page/${pageNo.value}.png?zoom=${zoom.value}`
  : ''))
function imgUrl(p) { return p ? `/api/images/${p}` : '' }
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

async function upload(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  if (!files.length) return
  busy.value = `上传 ${files.length} 个文件…`
  err.value = ''
  try {
    let lastId = ''
    for (const f of files) {
      const fd = new FormData()
      fd.append('file', f)
      const d = await api.postForm('/api/manual/upload', fd)
      lastId = d.doc_id
      const i = docs.value.findIndex((x) => x.doc_id === d.doc_id)
      const item = { doc_id: d.doc_id, filename: d.filename, page_count: d.page_count }
      if (i >= 0) docs.value.splice(i, 1, item)
      else docs.value.unshift(item)
    }
    await loadDocs()
    activeDoc.value = docs.value.find((d) => d.doc_id === lastId) || docs.value[0] || null
    pageNo.value = 1
    sel.value = null
    msg.value = '已放入。在页面上拖框裁图，或切到「选择文字 / 翻译」选中英文翻译后插入画布'
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}

async function loadDocs() {
  try {
    const r = await api.get('/api/manual/docs')
    docs.value = r.docs || []
    if (!activeDoc.value && docs.value.length) activeDoc.value = docs.value[0]
  } catch (e2) { /* 列表接口失败不影响主流程 */ }
}
async function delDoc(d) {
  if (!confirm(`删除「${d.filename}」？只删文献文件，已入库的题目不受影响。`)) return
  try {
    await api.del(`/api/manual/doc/${d.doc_id}`)
    const i = docs.value.findIndex((x) => x.doc_id === d.doc_id)
    if (i >= 0) docs.value.splice(i, 1)
    if (activeDoc.value?.doc_id === d.doc_id) {
      activeDoc.value = docs.value[0] || null
      pageNo.value = 1
      sel.value = null
      pageSummaries.value = []
    }
    msg.value = `已删除「${d.filename}」`
  } catch (e2) { err.value = e2?.message || String(e2) }
}

function turn(d) {
  if (!activeDoc.value) return
  pageNo.value = clamp(pageNo.value + d, 1, activeDoc.value.page_count)
  sel.value = null
}

let boxing = null
let boxScrollTimer = null
let lastBoxPointer = null
function localPos(e) {
  const r = pageImg.value.getBoundingClientRect()
  return {
    rx: clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 1),
    ry: clamp((e.clientY - r.top) / Math.max(1, r.height), 0, 1),
  }
}
function updateBoxFromPointer() {
  if (!boxing || !lastBoxPointer) return
  const p = localPos(lastBoxPointer)
  sel.value = {
    x0: Math.min(boxing.x0, p.rx), y0: Math.min(boxing.y0, p.ry),
    x1: Math.max(boxing.x0, p.rx), y1: Math.max(boxing.y0, p.ry),
  }
}
function stepBoxScroll() {
  const v = viewerEl.value
  if (!boxing || !v || !lastBoxPointer) return
  const r = v.getBoundingClientRect()
  const edge = 54
  let dy = 0
  if (lastBoxPointer.clientY > r.bottom - edge) {
    dy = Math.min(34, 8 + (lastBoxPointer.clientY - (r.bottom - edge)) * 0.4)
  } else if (lastBoxPointer.clientY < r.top + edge) {
    dy = -Math.min(34, 8 + ((r.top + edge) - lastBoxPointer.clientY) * 0.4)
  }
  if (!dy) return
  const before = v.scrollTop
  v.scrollTop = before + dy
  if (v.scrollTop !== before) updateBoxFromPointer()
}
function startBoxScroll() {
  if (boxScrollTimer) return
  boxScrollTimer = window.setInterval(stepBoxScroll, 30)
}
function stopBoxScroll() {
  if (boxScrollTimer) window.clearInterval(boxScrollTimer)
  boxScrollTimer = null
  lastBoxPointer = null
}
function boxDown(e) {
  if (mode.value !== 'crop') return
  if (!activeDoc.value) return
  const p = localPos(e)
  boxing = { x0: p.rx, y0: p.ry, x1: p.rx, y1: p.ry }
  sel.value = { ...boxing }
  lastBoxPointer = e
  startBoxScroll()
  window.addEventListener('pointermove', boxMove)
  window.addEventListener('pointerup', boxUp)
}
function boxMove(e) {
  if (!boxing) return
  lastBoxPointer = e
  updateBoxFromPointer()
}
function boxUp() {
  window.removeEventListener('pointermove', boxMove)
  window.removeEventListener('pointerup', boxUp)
  stopBoxScroll()
  boxing = null
  if (sel.value && (sel.value.x1 - sel.value.x0) * (sel.value.y1 - sel.value.y0) < 0.00006) sel.value = null
}
function selStyle() {
  const s = sel.value || { x0: 0, y0: 0, x1: 0, y1: 0 }
  return {
    left: s.x0 * 100 + '%', top: s.y0 * 100 + '%',
    width: (s.x1 - s.x0) * 100 + '%', height: (s.y1 - s.y0) * 100 + '%',
  }
}

async function doCrop(target) {
  if (!sel.value || !activeDoc.value) return
  busy.value = '裁剪中…'
  err.value = ''
  try {
    const r = await api.post('/api/manual/crop', {
      doc_id: activeDoc.value.doc_id, page_no: pageNo.value,
      rect: [sel.value.x0, sel.value.y0, sel.value.x1, sel.value.y1], zoom: 3,
    })
    const pane = target === 'a' ? aPane.value : qPane.value
    if (pane) {
      await pane.addImage(r.path)
      if (ocrAfterCrop.value && pane.imageToText) await pane.imageToText(false)
    }
    sel.value = null
    msg.value = ocrAfterCrop.value
      ? '已裁剪并识别为文字，可直接改字号/颜色'
      : (target === 'a' ? '已裁剪答案图，放到下方「答案画布」' : '已裁剪题目图，放到上方「题目画布」')
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}
const crop = () => doCrop('q')
const cropAnswer = () => doCrop('a')

// ---------- 文字层：原生选中 → 复制 / 翻译 → 插入画布 ----------
function syncImgBox() {
  const el = pageImg.value
  if (!el) return
  imgBox.w = el.clientWidth
  imgBox.h = el.clientHeight
}
function setMode(m) {
  mode.value = m
  sel.value = null
  pop.open = false
  if (m === 'text') loadPageText()
  else textLines.value = []
}
async function loadPageText() {
  if (!activeDoc.value || mode.value !== 'text') { textLines.value = []; return }
  const key = `${activeDoc.value.doc_id}:${pageNo.value}`
  if (textCache.has(key)) { textLines.value = textCache.get(key); return }
  const seq = ++textSeq
  textLoading.value = true
  try {
    const r = await api.get(`/api/manual/doc/${activeDoc.value.doc_id}/page/${pageNo.value}/text`)
    if (seq !== textSeq) return
    textLines.value = r.lines || []
    textCache.set(key, textLines.value)
    if (!r.has_text) msg.value = '这一页没有文字层（扫描版 PDF），只能裁剪成图片，或用 AI 直接识别整页'
  } catch (e2) {
    if (seq === textSeq) textLines.value = []
  } finally {
    if (seq === textSeq) textLoading.value = false
  }
}
function tlStyle(l) {
  const h = imgBox.h || 0
  const fh = Math.max(1, (l.y1 - l.y0) * h)
  return {
    left: l.x0 * 100 + '%', top: l.y0 * 100 + '%',
    width: (l.x1 - l.x0) * 100 + '%', height: fh + 'px',
    fontSize: fh * 0.92 + 'px', lineHeight: fh + 'px',
  }
}
function normRects(rects) {
  const box = pageImg.value ? pageImg.value.getBoundingClientRect() : null
  if (!box || !box.width) return []
  const out = []
  for (const r of rects) {
    const x0 = Math.max(box.left, r.left)
    const x1 = Math.min(box.right, r.right)
    const y0 = Math.max(box.top, r.top)
    const y1 = Math.min(box.bottom, r.bottom)
    if (x1 - x0 < 2 || y1 - y0 < 2) continue
    out.push([(x0 - box.left) / box.width, (y0 - box.top) / box.height,
              (x1 - box.left) / box.width, (y1 - box.top) / box.height])
  }
  return out
}
function onPagePointerUp(e) {
  if (mode.value !== 'text' || !activeDoc.value) return
  const s = window.getSelection()
  // 直接用浏览器原生选区的文字，不再按矩形回抽样：按行回抽会把相邻行/整段一起带进来，
  // 造成「只选了一点却插入了很多」。
  let text = ''
  if (s && !s.isCollapsed && s.rangeCount) {
    text = s.toString()
  } else {
    const el = document.elementFromPoint(e.clientX, e.clientY)
    const lineEl = el && el.closest ? el.closest('.tl') : null
    if (lineEl) text = lineEl.textContent || ''
  }
  text = flowText(text)
  if (!text) return
  pop.open = true
  pop.text = text
  pop.trans = ''; pop.err = ''; pop.msg = ''; pop.busy = false
  pop.x = clamp(e.clientX + 12, 8, Math.max(8, window.innerWidth - 390))
  pop.y = clamp(e.clientY + 12, 8, Math.max(8, window.innerHeight - 300))
}
function closePop() {
  pop.open = false
  pop.err = ''
  pop.msg = ''
  try { window.getSelection()?.removeAllRanges() } catch (e2) {}
}
async function translatePop() {
  if (!pop.text.trim()) return
  pop.busy = true
  pop.err = ''
  pop.msg = ''
  try {
    const r = await api.post('/api/ai/translate', { text: pop.text, target: '简体中文' })
    pop.trans = flowText(r.text || '')
    if (!pop.trans) pop.msg = '模型没有返回译文，请重试'
  } catch (e2) {
    pop.err = e2?.message || String(e2)
  } finally {
    pop.busy = false
  }
}
async function copyPop(text) {
  try {
    await navigator.clipboard.writeText(text || '')
    pop.msg = '已复制到剪贴板'
  } catch (e2) {
    pop.msg = '浏览器不允许直接复制，请在文本框里全选复制'
  }
}
function insertPop(where, useTrans) {
  const text = ((useTrans ? pop.trans : pop.text) || '').trim()
  if (!text) return
  const pane = where === 'a' ? aPane.value : qPane.value
  if (!pane) return
  pane.addTextFlow(text)
  msg.value = `已插入${where === 'a' ? '答案' : '题目'}画布，可继续排版`
  closePop()
}

// ---------- AI：逐页精读 → 每页摘要 → 机理/重点 ----------
const keyLines = computed(() => keyPoints.value.filter((p) => p.page_no === pageNo.value))
function keyStyle(p) {
  return { top: Math.max(0, p.y * 100 - 1.2) + '%', height: '2.4%' }
}
function gotoKeyPoint(p) {
  if (p.page_no && p.page_no !== pageNo.value) pageNo.value = p.page_no
  if (mode.value !== 'text') setMode('text')
}
async function translateSelection() {
  if (mode.value !== 'text') setMode('text')
  const s = window.getSelection()
  let text = s && !s.isCollapsed ? String(s.toString() || '').trim() : ''
  if (!text && pop.open) text = pop.text
  if (!text) {
    msg.value = '先用鼠标在 PDF 上拖选要翻译的英文（可跨行），松手会自动弹出翻译框；再点「翻译成中文」'
    return
  }
  pop.open = true
  pop.text = text
  pop.trans = ''
  pop.err = ''
  pop.msg = ''
  pop.x = clamp(window.innerWidth / 2 - 190, 8, Math.max(8, window.innerWidth - 400))
  pop.y = 120
  await translatePop()
}

async function readPages() {
  if (!activeDoc.value || readBusy.value) return
  const doc = activeDoc.value
  const total = Math.max(1, doc.page_count)
  const from = clamp(Number(readFrom.value) || 1, 1, total)
  readFrom.value = from
  const count = Number(readCount.value) || 0
  const to = count <= 0 ? total : Math.min(total, from + count - 1)
  readBusy.value = true
  readDone.value = 0
  readTotal.value = to - from + 1
  err.value = ''
  try {
    for (let p = from; p <= to; p++) {
      msg.value = `AI 逐页精读：第 ${p} 页（${readDone.value + 1}/${readTotal.value}）…`
      try {
        const r = await api.post('/api/ai/summarize-page', { doc_id: doc.doc_id, page_no: p })
        const rest = pageSummaries.value.filter((s) => s.page_no !== p)
        rest.push({
          page_no: p,
          summary: r.summary || '',
          key_points: Array.isArray(r.key_points) ? r.key_points : [],
          has_text: !!r.has_text,
          message: r.message || '',
        })
        rest.sort((a, b) => a.page_no - b.page_no)
        pageSummaries.value = rest
      } catch (e2) {
        err.value = `第 ${p} 页精读失败：${e2?.message || e2}`
        break
      }
      readDone.value += 1
      await nextTick()
    }
    const textPages = pageSummaries.value.filter((s) => s.has_text).length
    if (to < total) readFrom.value = to + 1
    msg.value = `已读第 ${from}–${to} 页；${textPages} 页有摘要。黄线是 AI 标出的机理/重点解释。`
      + (to < total ? `再点一次「开始逐页精读」会从第 ${to + 1} 页继续。` : '已经读到最后一页。')
  } finally {
    readBusy.value = false
  }
}
function clearAiRead() {
  pageSummaries.value = []
  readDone.value = 0
  readTotal.value = 0
  msg.value = '已清除 AI 逐页摘要和重点标记'
}

watch(() => activeDoc.value?.doc_id, () => {
  pageSummaries.value = []
  textLines.value = []
  nextTick(() => { syncImgBox(); loadPageText() })
})
watch(pageNo, () => {
  const total = activeDoc.value?.page_count || 1
  if (!Number.isFinite(pageNo.value)) pageNo.value = 1
  if (pageNo.value < 1) pageNo.value = 1
  if (pageNo.value > total) pageNo.value = total
  sel.value = null
  pop.open = false
  nextTick(() => { syncImgBox(); loadPageText() })
})
watch(mode, () => { pop.open = false; loadPageText() })

// ---------- 「其它功能」浮动面板 ----------
const auxOpen = ref(false)
const floatEl = ref(null)
const fpos = reactive({ x: 24, y: 110 })
function clampF() {
  const el = floatEl.value
  if (!el) return
  const w = el.offsetWidth || 348
  const h = el.offsetHeight || 200
  fpos.x = Math.max(8, Math.min(fpos.x, window.innerWidth - w - 8))
  fpos.y = Math.max(8, Math.min(fpos.y, window.innerHeight - h - 8))
}
function floatDown(e) {
  if (e.target.closest('button')) return
  const sx = e.clientX - fpos.x
  const sy = e.clientY - fpos.y
  const move = (ev) => { fpos.x = ev.clientX - sx; fpos.y = ev.clientY - sy; clampF() }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    try { localStorage.setItem('tiku-aux-pos', JSON.stringify({ x: fpos.x, y: fpos.y })) } catch (e2) {}
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
function toggleAux() { auxOpen.value = !auxOpen.value; nextTick(clampF) }

// ---------- 入库信息 ----------
const meta = reactive({ books: [], tags: [], qtypes: [], default_book_id: null })
const form = reactive({
  book_id: null, unit_id: null, q_type: '综合', difficulty: 2, tags: [],
  source_title: '', source_citation: '', locator: '', status: 'confirmed', answer: '',
})
const units = computed(() => {
  const b = meta.books.find((x) => x.id === form.book_id)
  return b ? b.units || [] : []
})
function pickTag(name) {
  const i = form.tags.indexOf(name)
  if (i >= 0) form.tags.splice(i, 1)
  else form.tags.push(name)
}
async function loadMeta() {
  try {
    const m = await api.get('/api/authoring/meta')
    meta.books = m.books || []
    meta.qtypes = m.qtypes || []
    meta.default_book_id = m.default_book_id || null
    let tags = []
    try {
      const t = await api.get('/api/authoring/tags')
      tags = Array.isArray(t) ? t : (t.tags || t.items || [])
    } catch (e2) { tags = [] }
    meta.tags = tags
    if (form.book_id == null) form.book_id = meta.default_book_id
  } catch (e) { err.value = e?.message || String(e) }
  if (!meta.qtypes.length) meta.qtypes = ['简答']
}

// ---------- 保存 / 载入 ----------
const saving = ref(false)
const editingQuestionId = ref(null)
const savedId = ref(null)

async function save() {
  const ql = qPane.value ? qPane.value.getLayout() : null
  if (!ql || !ql.blocks.length) { err.value = '题目画布是空的：先裁剪或写点文字'; return }
  saving.value = true
  err.value = ''; msg.value = ''
  try {
    const r = await api.post('/api/authoring/compose', {
      layout: ql,
      answer_layout: aPane.value ? aPane.value.getLayout() : null,
      question_id: editingQuestionId.value || null,
      book_id: form.book_id, unit_id: form.unit_id,
      q_type: form.q_type, difficulty: Number(form.difficulty),
      tags: form.tags, source_title: form.source_title,
      source_citation: form.source_citation, locator: form.locator,
      status: form.status, answer: form.answer,
      analysis: analysisText.value,
      stem_text: aiStem.value,
      auto_classify: true,
    })
    editingQuestionId.value = r.id
    savedId.value = r.id
    if (r.q_type) form.q_type = r.q_type
    if (r.difficulty) form.difficulty = Number(r.difficulty)
    if (Array.isArray(r.tags)) form.tags = [...r.tags]
    const cls = r.classified || {}
    let text = `${r.created ? '已入库' : '已更新'} #${r.id}；AI 归类为「${cls.q_type || form.q_type}」· 难度 ${cls.difficulty || form.difficulty}`
    if (cls.topics && cls.topics.length) text += `；专题：${cls.topics.join('、')}`
    if (cls.reason) text += `（${cls.reason}）`
    msg.value = text
    loadMeta()
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { saving.value = false }
}

async function genAnalysis() {
  if (!savedId.value) { msg.value = '先点「保存到题库」，AI 才能把解析写回题目'; return }
  const stem = stemText()
  if (!stem && !form.answer.trim()) { err.value = '画布里还没有题干文字：先用「＋ 文本」写题干，再生成解析'; return }
  analysisBusy.value = true
  err.value = ''
  try {
    const r = await api.post('/api/ai/analysis', {
      stem: stem || form.answer, answer: form.answer, q_type: form.q_type,
    })
    analysisText.value = r.text || ''
    if (!analysisText.value) { msg.value = '模型没有返回解析，可以稍后重试'; return }
    await api.patch(`/api/questions/${savedId.value}`, { analysis: analysisText.value })
    msg.value = `AI 解析已生成，并写入题库 #${savedId.value}`
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { analysisBusy.value = false }
}

function stemText() {
  const ql = qPane.value ? qPane.value.getLayout() : null
  const canvas = (ql?.blocks || []).filter((b) => b.type === 'text').map((b) => b.text || '').join('\n').trim()
  return canvas || aiStem.value.trim()
}
function stemImage() {
  const ql = qPane.value ? qPane.value.getLayout() : null
  const img = (ql?.blocks || []).find((b) => b.type === 'image' && b.path)
  return img ? img.path : ''
}
async function genAnswer() {
  const stem = stemText()
  const imagePath = stem ? '' : stemImage()
  if (!stem && !imagePath) {
    err.value = '题目画布还没有题干：先裁剪题图、写文字块，或在「题干文字」里粘贴题目'
    return
  }
  answerBusy.value = true
  err.value = ''
  msg.value = imagePath ? '题目是图片：AI 先用本机 OCR 读题，再解题并复核（约 30-60 秒）…' : 'AI 正在解题并自动复核（约 30-60 秒）…'
  try {
    const r = await api.post('/api/ai/answer', {
      stem, q_type: form.q_type, answer_hint: form.answer.trim(), image_path: imagePath || null,
    })
    const ans = (r.answer || r.text || '').trim()
    const ana = (r.analysis || '').trim()
    const refs = (r.references || []).map((x) => String(x).trim()).filter(Boolean)
    const refsText = refs.length ? refs.map((x, i) => `[${i + 1}] ${x}`).join('\n') : '无参考文献'
    if (!ans) {
      msg.value = '模型没有返回答案，可以稍后重试'
      return
    }
    let packed = ans
    if (ana) packed += `\n\n【解析】\n${ana}`
    packed += `\n\n【参考文献】\n${refsText}`
    form.answer = packed
    analysisText.value = (ana ? `【解析】\n${ana}\n\n` : '') + `【参考文献】\n${refsText}`
    const secs = r.usage?.elapsed_ms ? `，用时约 ${Math.round(r.usage.elapsed_ms / 1000)} 秒` : ''
    msg.value = `AI 已把答案、解析和参考文献填入「参考答案」${r.usage?.verified ? '（已自动复核）' : ''}${secs}`
      + `${r.ocr_used ? '；题目图已用本机 OCR 识别，若有错字请顺手改' : ''}，可直接修改`
  } catch (e2) {
    err.value = e2?.message || String(e2)
    msg.value = ''
  } finally { answerBusy.value = false }
}
function answerToCanvas() {
  const text = form.answer.trim()
  if (!text) { msg.value = '参考答案还是空的：先点「AI 生成答案」或自己写'; return }
  if (aPane.value) aPane.value.addText(text)
  msg.value = '已把参考答案插入答案画布；双击文字块即可修改字号、颜色或译文'
}

// AI 出几道题：只进试卷，不写题库；一次生成 = 一套可直接命名/导出/融合的试卷
async function genPaper() {
  if (genBusy.value) return
  err.value = ''
  msg.value = ''
  const summary = (pageSummaries.value || [])
    .filter((s) => s.page_no === pageNo.value)
    .map((s) => s.summary || (s.message || ''))
    .filter(Boolean).join('\n')
  const src = [aiStem.value.trim(), summary,
    form.answer.trim() ? `参考答案参考：\n${form.answer.trim()}` : ''].filter(Boolean).join('\n\n')
  if (!src && !activeDoc.value) {
    err.value = '先在「题干文字」里粘贴题目/素材，或从左侧选一份 PDF'
    return
  }
  genBusy.value = true
  try {
    const body = { count: genCount.value, title: genTitle.value || undefined, q_type: form.q_type }
    if (src) body.source = src
    else body.doc_id = activeDoc.value.doc_id
    const r = await api.post('/api/papers/ai', body)
    msg.value = `AI 已出 ${r.items.length} 题并直接生成试卷「${r.title}」，可在试卷页改名、导出或融合`
    router.push(`/papers/${r.id}`)
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally {
    genBusy.value = false
  }
}

// 分步出题：先选 1-11 道 → 一次生成题目快照（不进库）→ 逐题显示在画布上改 → 「下一题」
function openPaperPlan() {
  err.value = ''
  msg.value = ''
  paperPlan.open = true
}

function paperSourceText() {
  const summary = (pageSummaries.value || [])
    .filter((s) => s.page_no === pageNo.value)
    .map((s) => s.summary || (s.message || ''))
    .filter(Boolean).join('\n')
  return [aiStem.value.trim(), summary,
    form.answer.trim() ? `参考答案参考：\n${form.answer.trim()}` : ''].filter(Boolean).join('\n\n')
}

function paperCanvasText(pane) {
  if (!pane) return ''
  const layout = pane.getLayout ? pane.getLayout() : {}
  return (layout.blocks || []).filter((b) => b.type === 'text').map((b) => b.text || '').join('\n\n').trim()
}

function showPaperItem(i) {
  if (!paperStep.items[i]) return
  paperStep.idx = i
  const snap = paperStep.items[i].snapshot || {}
  if (qPane.value) {
    qPane.value.clear()
    if (snap.stem) qPane.value.addText(snap.stem)
  }
  if (aPane.value) {
    aPane.value.clear()
    const ans = [snap.answer || '', snap.analysis ? `\n【解析】\n${snap.analysis}` : ''].join('').trim()
    if (ans) aPane.value.addText(ans)
  }
}

function capturePaperItem() {
  const it = paperStep.items[paperStep.idx]
  if (!it) return
  const stem = paperCanvasText(qPane.value)
  const raw = paperCanvasText(aPane.value)
  if (stem) it.snapshot.stem = stem
  const parts = raw.split(/\n*【解析】\n*/)
  it.snapshot.answer = (parts[0] || '').trim()
  if (parts.length > 1) it.snapshot.analysis = parts.slice(1).join('\n【解析】\n').trim()
}

async function pickPaperCount(n) {
  paperPlan.open = false
  paperStep.busy = true
  paperStep.total = n
  paperStep.idx = 0
  paperStep.items = []
  paperStep.active = false
  err.value = ''
  msg.value = 'AI 正在一次生成题目，约 30-60 秒…'
  try {
    const src = paperSourceText()
    const body = { count: n, title: genTitle.value || undefined, q_type: form.q_type }
    if (src) body.source = src
    else if (activeDoc.value) body.doc_id = activeDoc.value.doc_id
    else throw new Error('先在「题干文字」里粘贴题目/素材，或从左侧选一份 PDF')
    const r = await api.post('/api/papers/ai/preview', body)
    paperStep.items = r.items || []
    paperStep.total = paperStep.items.length || n
    paperStep.title = r.title || genTitle.value || `AI 命题 · ${n} 题`
    paperStep.active = true
    showPaperItem(0)
    msg.value = `已生成 ${paperStep.total} 题：第 1 题显示在画布上，改完点右上角「出下一个题」`
  } catch (e2) {
    err.value = e2?.message || String(e2)
    paperStep.active = false
  } finally { paperStep.busy = false }
}

async function nextPaperQuestion() {
  if (paperStep.busy) return
  capturePaperItem()
  if (paperStep.idx + 1 < paperStep.total) {
    showPaperItem(paperStep.idx + 1)
    msg.value = `第 ${paperStep.idx + 1}/${paperStep.total} 题；可以继续改，改完再点「出下一个题」`
    return
  }
  paperStep.busy = true
  try {
    const r = await api.post('/api/papers/ai/build', {
      title: paperStep.title,
      items: paperStep.items.map((x) => ({ snapshot: x.snapshot, score: x.score })),
    })
    paperStep.active = false
    msg.value = `已生成试卷「${r.title}」，正在打开…`
    router.push(`/papers/${r.id}`)
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { paperStep.busy = false }
}

function cancelPaperStep() {
  paperStep.active = false
  paperStep.items = []
  paperStep.total = 0
  msg.value = '已退出分步出题；已生成的题目没有写入题库'
}

function resetAll() {
  if (qPane.value) qPane.value.clear()
  if (aPane.value) aPane.value.clear()
  editingQuestionId.value = null
  savedId.value = null
  analysisText.value = ''
  err.value = ''
  msg.value = '画布已清空，可以出下一题'
}

async function loadExisting(id) {
  try {
    const d = await api.get(`/api/authoring/compose/${id}`)
    if (qPane.value) qPane.value.setLayout(d.layout)
    if (aPane.value) aPane.value.setLayout(d.answer_layout || { blocks: [] })
    editingQuestionId.value = d.id
    savedId.value = d.id
    form.book_id = d.book_id || form.book_id
    form.unit_id = d.unit_id || null
    form.q_type = d.q_type || form.q_type
    form.difficulty = d.difficulty || 2
    form.tags = [...(d.tags || [])]
    form.source_title = d.source_title || ''
    form.source_citation = d.source_citation || ''
    form.locator = d.locator || ''
    form.status = d.status || 'confirmed'
    form.answer = d.answer || ''
    analysisText.value = d.analysis || ''
    aiStem.value = d.stem_text || ''
    msg.value = `已载入 #${d.id}，可继续编辑`
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

function goBank() { router.push('/questions') }

// ---------- 快捷键：作用在「当前」画布 ----------
function paneOf(key) { return key === 'a' ? aPane.value : qPane.value }
function onKeys(e) {
  const t = e.target || {}
  const tag = (t.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || t.isContentEditable) return
  const p = paneOf(activePane.value)
  if (!p) return
  const m = loadShortcuts()
  const k = (e.key || '').toLowerCase()
  if (matchCombo(e, 'copy', m)) { p.copySel(); e.preventDefault(); return }
  if (matchCombo(e, 'paste', m)) { p.pasteClip(); e.preventDefault(); return }
  if (matchCombo(e, 'group', m)) { p.groupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'ungroup', m)) { p.ungroupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'rotate', m)) { p.rotateSel(); e.preventDefault(); return }
  if (matchCombo(e, 'del', m) || k === 'backspace') { p.removeSelected(); e.preventDefault(); return }
  if ((e.ctrlKey || e.metaKey) && k === 'a') { p.selectAll(); e.preventDefault(); return }
  if (matchCombo(e, 'esc', m)) { if (pop.open) closePop(); p.clearSel(); return }
  const step = e.shiftKey ? 10 : 1
  const delta = { arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step] }[k]
  if (delta) { p.nudge(delta[0], delta[1]); e.preventDefault() }
}

onMounted(async () => {
  await loadMeta()
  await loadDocs()
  try {
    const saved = JSON.parse(localStorage.getItem('tiku-aux-pos') || 'null')
    if (saved && Number.isFinite(saved.x)) { fpos.x = saved.x; fpos.y = saved.y }
    else { fpos.x = Math.max(16, window.innerWidth - 72); fpos.y = Math.round(window.innerHeight / 2 - 20) }
  } catch (e2) {}
  setTimeout(clampF, 0)
  window.addEventListener('keydown', onKeys)
  window.addEventListener('resize', syncImgBox)
  const qid = Number(route.query.id || 0)
  if (qid) await nextTick().then(() => loadExisting(qid))
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeys)
  window.removeEventListener('resize', syncImgBox)
})
</script>

<template>
  <div v-if="phoneHint && !hintClosed" class="phonehint">
    手机屏幕画布比较挤，建议横屏或在电脑上排版；这里可以先裁图和预览。
    <button class="ghost sm" @click="hintClosed = true">知道了</button>
  </div>

  <div class="writer wps" :class="{ 'left-off': !qOpen, 'focus-mode': true }">

    <div class="wtools">
      <button class="wt" :class="{ on: qOpen }" @click="toggleCrop">✂ 文献与裁剪</button>
      <button class="wt" :class="{ on: drawer === 'formula' }" @click="toggleTool('formula')">∑ 公式</button>
      <button class="wt" :class="{ on: drawer === 'struct' }" @click="toggleTool('struct')">⌬ 结构式</button>
      <button class="wt" :class="{ on: drawer === 'subs' }" @click="toggleTool('subs')">⑂ 小问</button>
      <span class="wsep" />
      <button class="wt" @click="resetAll">＋ 新建一题</button>
      <button class="wt go" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存到题库' }}</button>
      <span class="wsp" />
      <span class="whint">{{ qOpen || drawer ? '右侧面板打开时，画布会自动缩窄' : '点上方按钮在右侧打开工具面板' }}</span>
    </div>

    <div class="wbody">

    <aside v-if="drawer" class="wdrawer dock" :class="{ wide: (drawer === 'struct' && structTab === 'draw') || drawer === 'subs' }">
      <div class="wdhead">
        <b>{{ drawer === 'formula' ? '公式' : drawer === 'subs' ? '小问' : '结构式' }}</b>
        <button class="ghost sm" @click="drawer = ''">收起</button>
      </div>
      <div class="wdbody">
        <FormulaPad v-if="drawer === 'formula'" @send="insertFormula" @close="drawer = ''" />
        <div v-else-if="drawer === 'subs'" class="subsbox">
          <p class="muted">给这道题加小问编号（(1)(2)(3)…），点「插入」会把编号放到当前画布的光标处；这里只管理编号，内容仍用画布排版。</p>
          <div class="subbar">
            <button class="ghost sm" @click="addSub">＋ 加一个小问</button>
            <span class="muted">插到：</span>
            <button class="ghost sm" :class="{ on: subTarget === 'q' }" @click="subTarget = 'q'">题目</button>
            <button class="ghost sm" :class="{ on: subTarget === 'a' }" @click="subTarget = 'a'">答案</button>
          </div>
          <div v-if="!subs.length" class="muted">还没有小问，点上面「＋ 加一个小问」。</div>
          <div class="subrow" v-for="(sb, i) in subs" :key="i">
            <b class="subno">({{ i + 1 }})</b>
            <input v-model="sb.hint" placeholder="这问考什么（可留空）" />
            <button class="ghost sm" @click="insertSub(i)">插入</button>
            <button class="ghost sm" :disabled="i === 0" @click="moveSub(i, -1)">↑</button>
            <button class="ghost sm" :disabled="i === subs.length - 1" @click="moveSub(i, 1)">↓</button>
            <button class="ghost sm" @click="delSub(i)">删</button>
          </div>
        </div>
        <div v-else class="structbox">
          <div class="stabs">
            <button :class="{ on: structTab === 'smiles' }" @click="structTab = 'smiles'">SMILES / MOL</button>
            <button :class="{ on: structTab === 'draw' }" @click="switchStruct('draw')">手绘（Ketcher）</button>
          </div>

          <template v-if="structTab === 'smiles'">
            <p class="muted">输入 SMILES（如 <code>O=C(O)c1ccccc1</code>），服务端 RDKit 出图，确认无误后可复制 SVG 或截图贴进题图。</p>
            <input v-model="smiles" placeholder="SMILES 或 MOL" @keyup.enter="previewSmiles" />
            <div class="srow">
              <button class="ghost sm" @click="previewSmiles">预览</button>
              <button class="ghost sm" :disabled="!smilesSvg" @click="insertStructure">保存结构</button>
              <button class="ghost sm" :disabled="!smilesSvg" @click="copySvg">复制 SVG</button>
            </div>
          </template>

          <template v-else>
            <div class="kbar">
              <button class="ghost sm" @click="ketcherSetFromInput">把 SMILES 载入画板</button>
              <button class="ghost sm" :disabled="!ketcherReady" @click="ketcherTake">取结构出图</button>
              <span class="muted" v-if="!ketcherReady">画板加载中…</span>
            </div>
            <iframe ref="kframe" class="kframe" src="/ketcher/index.html"
                    @load="ketcherPing" title="结构式画板"></iframe>
          </template>

          <div v-if="smilesMsg" class="okbox sm">{{ smilesMsg }}</div>
          <div v-if="smilesSvg" class="sprev" v-html="smilesSvg" />
        </div>
      </div>
    </aside>

    <aside class="left dock-left" v-show="qOpen">
      <section class="pane">
        <h3>PDF 放入</h3>
        <label class="drop">
          <input type="file" accept="application/pdf,image/*" multiple @change="upload" />
          <span>＋ 选择 PDF / 图片（可多份）</span>
        </label>
        <div class="docs" v-if="docs.length">
          <div class="docrow" v-for="d in docs" :key="d.doc_id">
            <button class="doc" :class="{ on: activeDoc && activeDoc.doc_id === d.doc_id }"
                    @click="activeDoc = d; pageNo = 1; sel = null">
              {{ d.filename }}<i>{{ d.page_count }} 页</i>
            </button>
            <button class="docx" title="删除这份文献" @click.stop="delDoc(d)">×</button>
          </div>
        </div>
        <div class="pager" v-if="activeDoc">
          <button @click="turn(-1)" :disabled="pageNo <= 1">‹</button>
          <input v-model.number="pageNo" class="pageno" type="number" min="1" :max="activeDoc.page_count" />
          <span>/ {{ activeDoc.page_count }}</span>
          <button @click="turn(1)" :disabled="pageNo >= activeDoc.page_count">›</button>
          <select v-model.number="zoom">
            <option :value="1.5">标准</option>
            <option :value="2">清晰</option>
            <option :value="3">放大</option>
          </select>
        </div>
        <div class="modes" v-if="activeDoc">
          <button :class="{ on: mode === 'crop' }" @click="setMode('crop')">框选裁剪</button>
          <button :class="{ on: mode === 'text' }" @click="setMode('text')">选择文字 / 翻译</button>
          <button class="primary" @click="translateSelection">翻译选中</button>
          <label v-if="mode === 'crop'" class="chk" title="裁剪后自动用本机 OCR 把图识别成可编辑文字（扫描页也适用）">
            <input type="checkbox" v-model="ocrAfterCrop" /> 裁剪后识别为文字
          </label>
          <span class="muted" v-if="textLoading">取文字层…</span>
        </div>

        <div class="aipanel" v-if="activeDoc">
          <div class="aihead">
            <b>AI 逐页精读</b>
            <span class="muted">先概括每页，再标机理 / 重点</span>
            <span class="spacer"></span>
            <button class="icon" :disabled="readBusy" @click="readPages">
              {{ readBusy ? `精读中 ${readDone}/${readTotal}` : '开始逐页精读' }}
            </button>
            <button v-if="pageSummaries.length" class="icon" @click="clearAiRead">清除</button>
          </div>
          <div class="readrow">
            <span>从第</span>
            <input v-model.number="readFrom" type="number" min="1" :max="activeDoc.page_count" class="pagenum" />
            <span>页起，读</span>
            <select v-model.number="readCount" class="readcount">
              <option :value="1">1 页</option>
              <option :value="2">2 页</option>
              <option :value="4">4 页</option>
              <option :value="8">8 页</option>
              <option :value="0">到末尾</option>
            </select>
            <span class="muted">{{ readFrom }}–{{ readCount ? Math.min(activeDoc.page_count, readFrom + readCount - 1) : activeDoc.page_count }} 页</span>
          </div>
          <div class="aiempty" v-if="!pageSummaries.length">
            AI 会逐页读文字层：先给每页摘要，再用黄线标出机理研究 / 重点解释。扫描页会自动走本机 OCR（首次约十几秒/页）。
          </div>
          <div class="sumlist" v-if="pageSummaries.length">
            <div v-for="s in pageSummaries" :key="s.page_no" class="sumcard"
                 :class="{ on: s.page_no === pageNo }" @click="pageNo = s.page_no">
              <div class="sumhead">
                <b>第 {{ s.page_no }} 页</b>
                <span v-if="!s.has_text" class="muted">扫描页 · OCR 未识别出文字</span>
              </div>
              <div class="aisum">{{ s.summary || s.message || '（本页没有摘要）' }}</div>
              <ul v-if="s.key_points && s.key_points.length" class="kplist">
                <li v-for="(p, i) in s.key_points" :key="i"
                    @click.stop="gotoKeyPoint({ ...p, page_no: s.page_no })">
                  <span class="kppage">L{{ p.line_no }}</span>
                  <span class="kptext">{{ p.note || p.text }}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
        <div class="viewer" ref="viewerEl" v-if="activeDoc">
          <div class="page-wrap" :class="{ textmode: mode === 'text' }"
               @pointerdown="boxDown" @pointerup="onPagePointerUp">
            <img ref="pageImg" :src="pageUrl" draggable="false" @dragstart.prevent @load="syncImgBox" />
            <div v-if="textLines.length" class="textlayer" :class="{ on: mode === 'text' }">
              <div v-for="(l, i) in textLines" :key="i" class="tl" :style="tlStyle(l)">{{ l.text }}</div>
            </div>
            <div v-if="sel" class="selbox" :style="selStyle()"></div>
            <div v-for="(p, i) in keyLines" :key="'kp' + i" class="keyline" :style="keyStyle(p)"></div>
          </div>
        </div>
        <div class="hint" v-else>放进来一份 PDF，在页面上拖框即可裁剪。</div>
        <div class="hint" v-if="mode === 'text'">
          直接拖选英文（可跨行），松手后会弹出文本框：可复制、翻译成中文，并插入题目 / 答案画布。
        </div>
        <div class="row">
          <button class="primary" :disabled="!sel" @click="crop">裁剪到题目</button>
          <button class="primary ghost" :disabled="!sel" @click="cropAnswer">裁剪到答案</button>
        </div>
        <div class="row">
          <button :disabled="!sel" @click="sel = null">清除选区</button>
        </div>
        <div class="busy" v-if="busy">{{ busy }}</div>
        <div class="hint" style="margin-top:8px">
          裁剪图会进题目画布；也可以把 PDF 页直接拖到画布。选区文字也可直接插入画布。
        </div>
      </section>
    </aside>

    <div ref="floatEl" class="auxfloat" :class="{ min: !auxOpen }"
         :style="{ left: fpos.x + 'px', top: fpos.y + 'px' }">
      <div class="auxhead" @pointerdown="floatDown" @dblclick="toggleAux">
        <span v-if="!auxOpen" class="burger" title="入库信息（拖动可移动，双击展开）">≡</span>
        <template v-else>
          <b>入库信息</b>
          <span class="spacer"></span>
          <button class="icon" title="收起" @click.stop="toggleAux">—</button>
        </template>
      </div>
      <div v-if="auxOpen" class="auxbody">
        <div class="aux">
          <label class="f"><span>书册（入库位置）</span>
            <select v-model="form.book_id">
              <option v-for="b in meta.books" :key="b.id" :value="b.id">{{ b.title }}</option>
            </select>
          </label>
          <label class="f"><span>单元（可选）</span>
            <select v-model="form.unit_id">
              <option :value="null">不指定</option>
              <option v-for="u in units" :key="u.id" :value="u.id">{{ u.name }}</option>
            </select>
          </label>
          <div class="cols2">
            <label class="f"><span>题型</span>
              <select v-model="form.q_type">
                <option v-for="t in meta.qtypes" :key="t" :value="t">{{ t }}</option>
              </select>
            </label>
            <label class="f"><span>难度</span>
              <select v-model.number="form.difficulty">
                <option :value="1">基础</option><option :value="2">中等</option><option :value="3">较难</option>
              </select>
            </label>
          </div>
          <label class="f"><span>文献 / 教材 / 试卷</span>
            <input v-model="form.source_title" placeholder="论文名、书名或试卷名" />
          </label>
          <label class="f"><span>出处（作者·期刊·年份）</span>
            <input v-model="form.source_citation" placeholder="如：张三. 某研究[J]. 某学报, 2024, 12(3): 45-67" />
          </label>
          <label class="f"><span>位置（页码 / 题号）</span>
            <input v-model="form.locator" placeholder="如：p.128 / 第 3 题" />
          </label>
          <div class="f">
            <span>标签（去「设置 → 标签管理」维护）</span>
            <div class="tags">
              <button v-for="t in meta.tags" :key="t.id" class="chip"
                      :class="{ on: form.tags.includes(t.name) }" @click="pickTag(t.name)">{{ t.name }}</button>
            </div>
          </div>
          <label class="f"><span>状态</span>
            <select v-model="form.status">
              <option value="confirmed">已确认</option>
              <option value="draft">草稿</option>
            </select>
          </label>
          <label class="f"><span>题干文字（给 AI 读；扫描图/图片题可把题目粘贴到这里）</span>
            <textarea v-model="aiStem" rows="3" placeholder="例如：第1题 多芳甲烷隐色体化学（21分）1-1 … 1-2 …（也可留空，AI 会读题目画布里的文字块）"></textarea>
          </label>
          <label class="f"><span>参考答案（文字，可选）</span>
            <textarea v-model="form.answer" rows="3" placeholder="答案文字，支持 $公式$；也可以直接在下方答案画布排版"></textarea>
          </label>
          <div class="btnrow ansbtns">
            <button class="primary ghost" :disabled="answerBusy" @click="genAnswer">
              {{ answerBusy ? 'AI 解题复核中…' : 'AI 生成答案' }}
            </button>
            <button class="ghost" :disabled="!form.answer.trim()" @click="answerToCanvas">插入答案画布</button>
          </div>
          <div v-if="form.answer" class="apprev" v-html="renderMath(form.answer)"></div>
          <div v-if="analysisText" class="analysisbox">
            <div class="analysishead">AI 解析（已写入题库）</div>
            <div v-html="renderMath(analysisText)"></div>
          </div>
          <div class="aigen">
            <div class="analysishead">AI 出题 → 直接生成试卷（不写题库）</div>
            <div class="aigenrow">
              <label>题数 <input v-model.number="genCount" type="number" min="1" max="12" style="width:62px" /></label>
              <input v-model="genTitle" placeholder="试卷名（可空，之后也能改）" />
            </div>
            <button class="primary" :disabled="genBusy || paperStep.busy" @click="openPaperPlan">
              出题…（先选 1-11 题）
            </button>
            <div class="hint">点「出题」先选出几道；生成后逐题显示在画布上，改完点右上角「出下一个题」，全部完成自动成卷，不写题库。</div>
          </div>
        </div>
      </div>
    </div>

    <section class="right">
      <div class="toolbar">
        <button class="icon" :title="qOpen ? '收起裁剪面板' : '打开裁剪面板'" @click="toggleCrop">
          {{ qOpen ? '⇤' : '✂' }}
        </button>
        <span class="spacer"></span>
        <span class="muted" v-if="editingQuestionId">正在编辑 #{{ editingQuestionId }}</span>
        <button v-if="editingQuestionId" @click="resetAll">新建一题</button>
        <button class="primary" :disabled="paperStep.busy" @click="openPaperPlan">出题</button>
        <button :disabled="answerBusy" @click="genAnswer">
          {{ answerBusy ? 'AI 解题复核中…' : 'AI 生成答案' }}
        </button>
        <button v-if="savedId" :disabled="analysisBusy" @click="genAnalysis">
          {{ analysisBusy ? 'AI 写解析中…' : 'AI 生成解析' }}
        </button>
        <button class="primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : (editingQuestionId ? '更新题目' : '保存到题库') }}
        </button>
      </div>

      <div v-if="paperStep.active" class="stepbar">
        <span>分步出题 · 第 {{ paperStep.idx + 1 }}/{{ paperStep.total }} 题</span>
        <span class="spacer"></span>
        <button class="primary" :disabled="paperStep.busy" @click="nextPaperQuestion">
          {{ paperStep.busy ? '生成试卷中…' : (paperStep.idx + 1 < paperStep.total ? '出下一个题 →' : '完成并生成试卷') }}
        </button>
        <button class="ghost" :disabled="paperStep.busy" @click="cancelPaperStep">退出</button>
      </div>

      <div v-if="err" class="errbox">{{ err }}</div>
      <div v-if="msg" class="okbox">
        {{ msg }}
        <a v-if="savedId" href="javascript:void(0)" @click="goBank">去题库看看 →</a>
      </div>

      <div class="hint qhint">
        竞赛题可以直接照原卷排版：题号、分值、小问（1-1、1-2…）整段裁剪，或用「＋ 文本」补写；
        题型默认「综合」，保存时 AI 还会按内容再归类。答案画布可让 AI 先写（答案+解析+参考文献）再改；
        有官方答案时，可先把要点贴在「参考答案」里再点 AI，准确率更高。
      </div>

      <div class="ctabs">
        <button class="ctab" :class="{ on: ctab === 'q' }" @click="ctab = 'q'">📝 题目</button>
        <button class="ctab" :class="{ on: ctab === 'a' }" @click="ctab = 'a'">✅ 答案</button>
        <span class="spacer" />
        <span class="muted">像浏览器标签页一样切换，两边内容各自保留</span>
      </div>

      <div class="cwrap" v-show="ctab === 'q'">
        <CanvasPane ref="qPane" title="题目画布" metaable :max-pages="1"
                    hint="把右侧裁剪好的题目图拖进来，或点「＋ 文本」开始排版"
                    @activate="activePane = 'q'" />
      </div>
      <div class="cwrap" v-show="ctab === 'a'">
        <CanvasPane ref="aPane" title="答案画布" :max-pages="2"
                    hint="把右侧「裁剪到答案」的图拖进来，或用「＋ 文本」写解析"
                    @activate="activePane = 'a'" />
      </div>
    </section>
    </div>

    <div v-if="pop.open" class="textpop" :style="{ left: pop.x + 'px', top: pop.y + 'px' }"
         @pointerdown.stop>
      <div class="pophead">
        <b>选中的文字</b>
        <span class="spacer"></span>
        <button class="icon" title="关闭" @click="closePop">✕</button>
      </div>
      <div class="popbody">
        <textarea v-model="pop.text" rows="4" placeholder="选区文字（可手动修改）"></textarea>
        <div class="poprow">
          <button @click="copyPop(pop.text)">复制</button>
          <button class="primary" :disabled="pop.busy || !pop.text.trim()" @click="translatePop">
            {{ pop.busy ? '处理中…' : '翻译成中文' }}
          </button>
          <button :disabled="!pop.text.trim()" @click="insertPop('q', false)">插入题目</button>
          <button :disabled="!pop.text.trim()" @click="insertPop('a', false)">插入答案</button>
        </div>
        <template v-if="pop.trans">
          <div class="popsub">译文（可修改）</div>
          <textarea v-model="pop.trans" rows="4"></textarea>
          <div class="poprow">
            <button @click="copyPop(pop.trans)">复制译文</button>
            <button class="primary" @click="insertPop('q', true)">译文插入题目</button>
            <button @click="insertPop('a', true)">译文插入答案</button>
          </div>
        </template>
        <div class="popmsg" v-if="pop.msg">{{ pop.msg }}</div>
        <div class="poperr" v-if="pop.err">{{ pop.err }}</div>
      </div>
    </div>
  </div>

  <div v-if="paperPlan.open" class="modal" @click.self="paperPlan.open = false">
    <div class="modal-card planpick">
      <h3>这次出几道题？</h3>
      <p class="muted">选一个数量，AI 按当前素材一次生成；生成后逐题显示在画布上，可改题干/答案，再点右上角「出下一个题」。</p>
      <div class="numpick">
        <button v-for="n in 11" :key="n" class="numbtn" @click="pickPaperCount(n)">{{ n }}</button>
      </div>
      <div class="hint">全部完成后自动生成一套试卷（不进题库），可在试卷页改名、导出或与别的卷融合。</div>
      <div class="btnrow" style="margin-top:12px">
        <button class="ghost" @click="paperPlan.open = false">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ---- WPS 式：顶部工具栏 + 右侧内嵌面板 ---- */
.writer.wps { display: flex; flex-direction: column; gap: 10px; align-items: stretch; }
.wtools {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  padding: 6px 10px; position: sticky; top: 8px; z-index: 30;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
.wt {
  display: inline-flex; align-items: center; gap: 4px; height: 30px; padding: 0 12px;
  border: 1px solid var(--line, #e7ebf2); background: #fff; color: #1f2937;
  border-radius: 8px; font-size: 13px; cursor: pointer;
}
.wt:hover { border-color: var(--brand, #2563eb); color: var(--brand, #2563eb); }
.wt.on { background: var(--brand, #2563eb); border-color: var(--brand, #2563eb); color: #fff; }
.wt.go { margin-left: 6px; }
.wt:disabled { opacity: .5; cursor: default; }
.wsep { width: 1px; height: 18px; background: var(--line, #e7ebf2); margin: 0 4px; }
.wsp { flex: 1 1 auto; }
.whint { font-size: 12px; color: #94a3b8; }
.wbody { display: flex; gap: 12px; align-items: flex-start; width: 100%; }
.wbody > .right { order: 2; flex: 1 1 auto; min-width: 0; }
.wbody > .dock-left { order: 1; }
.wbody > .wdrawer { order: 3; }
.wbody > .left.dock-left {
  position: static; flex: 0 0 372px; width: 372px; max-height: calc(100vh - 150px);
  overflow: auto; border-right: none; box-shadow: none;
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px;
}
.wbody > .wdrawer.dock {
  position: static; flex: 0 0 420px; width: 420px; max-height: calc(100vh - 150px);
  overflow: auto; border-left: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  background: #fff; box-shadow: none;
}
@media (max-width: 1100px) {
  .wbody { flex-direction: column; }
  .wbody > .left.dock-left, .wbody > .wdrawer.dock { flex: none; width: 100%; max-height: 46vh; }
}
.phonehint {
  display: flex; align-items: center; gap: 10px; justify-content: space-between;
  background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
  padding: 8px 12px; border-radius: 10px; font-size: 13px; margin-bottom: 10px;
}
@media (max-width: 820px) {
  .writer.focus-mode { display: block; }
  .wrail { position: fixed; left: 0; top: 56px; bottom: 0; height: auto; width: 46px; }
  .writer.focus-mode .left { left: 46px; width: calc(100vw - 46px); }
  .writer.focus-mode .right { margin-left: 46px; overflow-x: auto; }
  .wdrawer { width: 100vw; }
}

.wrail {
  position: sticky; top: 0; align-self: flex-start; width: 52px; flex: none;
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 10px 6px; border-right: 1px solid var(--line); background: #fff; height: 100vh; z-index: 60;
}
.wrail.slim { width: 34px; }
.wrail .rt { border: 0; background: none; color: #94a3b8; font-size: 16px; cursor: pointer; padding: 2px 0 6px; }
.wrail .rb {
  width: 34px; height: 34px; border-radius: 9px; border: 1px solid transparent; background: none;
  cursor: pointer; font-size: 15px; color: #334155; display: flex; align-items: center; justify-content: center;
}
.wrail .rb:hover { background: #f1f5f9; }
.wrail .rb.on { background: var(--brand-soft); color: var(--brand); border-color: #dbeafe; }
.wrail .rb.primary { background: var(--brand); color: #fff; }
.wrail .rb.new { font-size: 19px; font-weight: 700; color: var(--brand); }
.wrail .rsp { flex: 1; }

.wdrawer {
  position: fixed; right: 0; top: 0; bottom: 0; width: min(440px, 40vw);
  background: #fff; border-left: 1px solid var(--line); box-shadow: -8px 0 24px rgba(15,23,42,.06);
  display: flex; flex-direction: column; z-index: 45;
}
.wdhead { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--line); }
.wdbody { flex: 1; overflow: auto; padding: 12px 14px 24px; }
.structbox input { width: 100%; margin: 8px 0; }
.srow { display: flex; gap: 8px; margin-bottom: 10px; }
.sprev { border: 1px solid var(--line); border-radius: 10px; padding: 10px; text-align: center; background: #fff; }
.sprev svg { max-width: 100%; max-height: 260px; }
.okbox.sm { font-size: 12.5px; padding: 6px 10px; }

.writer.focus-mode { display: flex; align-items: flex-start; gap: 14px; }
.writer.focus-mode .right { flex: 1 1 auto; min-width: 0; }
.writer.focus-mode .left {
  position: fixed; left: 52px; top: 0; bottom: 0; width: min(420px, 40vw); z-index: 40;
  background: #fff; border-right: 1px solid var(--line); box-shadow: 8px 0 24px rgba(15,23,42,.06);
  overflow: auto; padding: 12px 14px 24px;
}

.writer { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); gap: 14px; align-items: start; }
.writer.left-off { grid-template-columns: minmax(0, 1fr); }
.left-off .left { display: none; }
.left { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.pane { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px; }
.pane h3 { margin: 0 0 10px; font-size: 14px; }
.drop { display: block; border: 1px dashed #c7d2e4; border-radius: 10px; padding: 12px; text-align: center; color: #4a5568; cursor: pointer; font-size: 13px; }
.drop input { display: none; }
.docs { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.docrow { display: flex; align-items: stretch; gap: 4px; }
.docrow .doc { flex: 1; min-width: 0; }
.docx { flex: 0 0 26px; padding: 0; border: 1px solid #e7ebf2; background: #fff; color: #98a2b3; border-radius: 8px; cursor: pointer; font-size: 15px; line-height: 1; }
.docx:hover { color: #dc2626; border-color: #fca5a5; background: #fff5f5; }
.doc { text-align: left; background: #f7f9fc; border: 1px solid #e7ebf2; border-radius: 8px; padding: 6px 8px; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc i { color: #8b95a5; font-style: normal; margin-left: 6px; }
.doc.on { border-color: var(--brand, #2f6fed); background: #eef4ff; color: var(--brand, #2f6fed); }
.pager { display: flex; align-items: center; gap: 6px; margin: 8px 0; font-size: 12.5px; }
.pager input.pageno { width: 58px; padding: 3px 6px; font-size: 12.5px; text-align: center; }
.pager select { width: auto; }
.viewer { overscroll-behavior: contain; background: #f1f3f7; border-radius: 10px; padding: 8px; max-height: calc(100vh - 320px); overflow: auto; }
.page-wrap { position: relative; display: block; margin: 0 auto; cursor: crosshair; }
.page-wrap img { display: block; width: 100%; height: auto; touch-action: none; user-select: none; }
.selbox { position: absolute; border: 1px dashed var(--brand, #2f6fed); background: rgba(47, 111, 237, .12); pointer-events: none; }
.page-wrap.textmode { cursor: text; }
.page-wrap.textmode img { pointer-events: none; }
.textlayer { position: absolute; inset: 0; pointer-events: none; user-select: none; overflow: hidden; }
.textlayer.on { user-select: text; }
.tl { position: absolute; color: transparent; white-space: pre; pointer-events: none; }
.textlayer.on .tl { pointer-events: auto; cursor: text; }
.tl::selection { background: rgba(47, 111, 237, .3); color: transparent; }
.modes { display: flex; gap: 6px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.modes .chk { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #475467; white-space: nowrap; }
.modes .chk input { width: auto; }
.modes button { padding: 4px 10px; font-size: 12.5px; border: 1px solid #dfe5ee; background: #fff; border-radius: 7px; cursor: pointer; }
.modes button.on { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; }
.modes button.primary { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; font-weight: 600; }
.aipanel { border: 1px solid #dbe4f3; background: #f8fbff; border-radius: 10px; padding: 8px 10px; margin: 8px 0; }
.aihead { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.aihead b { font-size: 13px; flex: 1; }
.aihead .icon { padding: 3px 7px; font-size: 12px; }
.readrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 12.5px; color: #475467; margin-bottom: 6px; }
.readrow input.pagenum { width: 58px; padding: 3px 6px; font-size: 12.5px; text-align: center; }
.readrow select.readcount { width: auto; padding: 3px 6px; font-size: 12.5px; }
.sumlist { overscroll-behavior: contain; display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow: auto; margin-top: 6px; }
.sumcard { border: 1px solid #e3e8f0; background: #fff; border-radius: 8px; padding: 6px 8px; cursor: pointer; }
.sumcard.on { border-color: var(--brand, #2f6fed); box-shadow: inset 0 0 0 1px rgba(47, 111, 237, .18); }
.sumhead { display: flex; align-items: baseline; gap: 6px; font-size: 12px; color: #475467; }
.sumhead b { font-size: 12.5px; color: #1f2430; }
.aisum { margin-top: 4px; font-size: 12.5px; color: #344054; line-height: 1.55; }
.kplist { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
.kplist li { display: flex; gap: 6px; font-size: 12px; color: #475467; cursor: pointer; padding: 3px 4px; border-radius: 5px; }
.kplist li:hover { background: #eef4ff; }
.kppage { flex: 0 0 auto; color: var(--brand, #2f6fed); font-weight: 600; }
.kptext { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.aiempty { font-size: 12px; color: #8b95a5; line-height: 1.55; }
.keyline { position: absolute; left: 0; right: 0; background: rgba(250, 204, 21, .28); border-top: 1px solid rgba(202, 138, 4, .55); pointer-events: none; z-index: 3; }
.analysisbox { margin-top: 8px; padding: 8px 10px; background: #f0f9f4; border: 1px solid #cdebd9; border-radius: 8px; font-size: 13px; white-space: pre-wrap; }
.analysishead { font-size: 12px; color: #3b7a57; margin-bottom: 4px; font-weight: 600; }
.aigen { margin-top: 10px; padding: 9px 10px; background: #f7f4ff; border: 1px solid #e2d9fb; border-radius: 8px; }
.aigenrow { display: flex; align-items: center; gap: 8px; margin: 5px 0 7px; }
.aigenrow label { display: flex; align-items: center; gap: 4px; font-size: 12px; white-space: nowrap; }
.aigen button { width: 100%; }
.aigen .hint { margin-top: 5px; font-size: 11.5px; color: #7b6a9e; line-height: 1.5; }

.stepbar {
  position: sticky; top: 8px; z-index: 9;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin: 6px 0 8px; padding: 8px 12px;
  background: #fffbe6; border: 1px solid #f5d97a; border-radius: 10px;
  font-size: 13px; box-shadow: 0 4px 12px rgba(146, 110, 0, .08);
}
.stepbar .spacer { flex: 1; }
.modal { position: fixed; inset: 0; background: rgba(16, 24, 40, .45); display: flex; align-items: center; justify-content: center; z-index: 90; padding: 20px; }
.modal-card { background: #fff; border-radius: 14px; padding: 20px 22px; width: min(460px, 94vw); box-shadow: 0 20px 50px rgba(16, 24, 40, .2); }
.modal-card h3 { margin: 0 0 6px; font-size: 17px; }
.planpick .muted { font-size: 12.5px; color: #667085; line-height: 1.6; margin: 0 0 12px; }
.numpick { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.numbtn { padding: 12px 0; font-size: 16px; font-weight: 600; border: 1px solid #dbe3ff; background: #f4f7ff; color: #2f6fed; border-radius: 9px; cursor: pointer; }
.numbtn:hover { background: #2f6fed; color: #fff; border-color: #2f6fed; }
.planpick .hint { margin-top: 12px; font-size: 12px; color: #8a6d1a; background: #fffbe6; padding: 8px 10px; border-radius: 8px; }
.textpop {
  position: fixed; z-index: 60; width: 372px; max-height: calc(100vh - 24px);
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  box-shadow: 0 18px 40px rgba(16, 24, 40, .18); display: flex; flex-direction: column;
}
.pophead { display: flex; align-items: center; gap: 8px; padding: 8px 10px; background: #f8fafc; border-bottom: 1px solid var(--line, #e7ebf2); }
.pophead b { font-size: 13px; }
.popbody { overscroll-behavior: contain; padding: 10px; display: flex; flex-direction: column; gap: 8px; overflow: auto; }
.popbody textarea { font-size: 13px; }
.poprow { display: flex; gap: 6px; flex-wrap: wrap; }
.poprow button { padding: 5px 10px; font-size: 12.5px; }
.popsub { font-size: 12px; color: #667085; }
.popmsg { font-size: 12px; color: #3b7a57; }
.poperr { font-size: 12px; color: #dc2626; white-space: pre-wrap; }
.hint { color: #8b95a5; font-size: 12.5px; }
.row { display: flex; gap: 8px; margin-top: 10px; }
.row button { flex: 1; }
.busy { margin-top: 8px; font-size: 12px; color: var(--brand, #2f6fed); }
.aux { display: flex; flex-direction: column; gap: 8px; }
.f { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: #475467; }
.f input, .f select, .f textarea { font-size: 13px; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid #dfe5ee; background: #fff; border-radius: 999px; padding: 3px 10px; font-size: 12px; color: #475467; cursor: pointer; }
.chip.on { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; }
.apprev { margin-top: 8px; padding: 8px 10px; background: #f8fafc; border-radius: 8px; font-size: 13.5px; }
.ansbtns { display: flex; gap: 6px; margin-top: 2px; }
.ansbtns button { flex: 1; font-size: 12.5px; padding: 5px 8px; }
.qhint { margin: 8px 0 6px; line-height: 1.6; }

.right { background: #f7f9fc; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px; min-width: 0; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.icon { padding: 4px 8px; border: 1px solid #dfe5ee; background: #fff; border-radius: 7px; cursor: pointer; font-size: 12.5px; }
.spacer { flex: 1; }
.muted { font-size: 12px; color: #8b95a5; }

.auxfloat {
  position: fixed; z-index: 40; width: 348px; max-height: min(620px, calc(100vh - 40px));
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  box-shadow: 0 18px 40px rgba(16, 24, 40, .16); overflow: hidden;
  display: flex; flex-direction: column;
}
.auxfloat.min { width: auto; }
.auxhead {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  cursor: grab; user-select: none; background: #f8fafc; border-bottom: 1px solid var(--line, #e7ebf2);
}
.auxfloat.min .auxhead { border-bottom: none; }
.auxhead:active { cursor: grabbing; }
.auxhead b { font-size: 13px; }
.auxhead .burger { font-size: 18px; line-height: 1; padding: 2px 4px; }
.auxbody { overscroll-behavior: contain; padding: 12px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
@media (max-width: 1180px) {
  .writer { grid-template-columns: 1fr; }
}

/* ---- 画布标签页 / 小问 ---- */
.ctabs { display: flex; align-items: center; gap: 6px; margin: 2px 0 8px; }
.ctab {
  display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 14px;
  border: 1px solid var(--line, #e7ebf2); border-bottom: none; background: #eef2f8; color: #475467;
  border-radius: 10px 10px 0 0; font-size: 13px; cursor: pointer;
}
.ctab.on { background: #fff; color: var(--brand, #2563eb); font-weight: 600; box-shadow: 0 -1px 0 var(--brand, #2563eb) inset; }
.cwrap { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 0 12px 12px 12px; padding: 10px; }
.subsbox { display: flex; flex-direction: column; gap: 8px; }
.subbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.subrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.subrow input { min-width: 110px; }
.subrow input { flex: 1 1 auto; }
.subno { width: 34px; color: var(--brand, #2563eb); }
.ghost.sm.on { background: var(--brand, #2563eb); border-color: var(--brand, #2563eb); color: #fff; }

/* ---- 结构式画板 ---- */
.writer.wps .wbody > .wdrawer.dock.wide { flex: 0 0 560px; width: 560px; }
.stabs { display: flex; gap: 6px; margin-bottom: 8px; }
.stabs button { padding: 4px 10px; font-size: 12.5px; border: 1px solid #dfe5ee; background: #fff; border-radius: 999px; cursor: pointer; color: #475467; }
.stabs button.on { background: var(--brand, #2563eb); border-color: var(--brand, #2563eb); color: #fff; }
.kbar { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; flex-wrap: wrap; }
.kframe { width: 100%; height: 460px; border: 1px solid var(--line, #e7ebf2); border-radius: 10px; background: #fff; }

/* ---- WPS 式覆盖（放最后，确保优先级） ---- */
.writer.wps { display: flex; flex-direction: column; gap: 10px; align-items: stretch; }
.writer.wps .wbody { display: flex; gap: 12px; align-items: flex-start; width: 100%; }
.writer.wps .wbody > .right { order: 2; flex: 1 1 auto; min-width: 0; }
.writer.wps .wbody > .left.dock-left {
  order: 3; position: static !important; left: auto; top: auto; bottom: auto;
  flex: 0 0 372px; width: 372px; max-height: calc(100vh - 150px); overflow: auto;
  border-right: none; box-shadow: none; z-index: auto;
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px;
}
.writer.wps .wbody > .wdrawer.dock {
  order: 3; position: static !important; right: auto; top: auto; bottom: auto;
  flex: 0 0 420px; width: 420px; max-height: calc(100vh - 150px); overflow: auto;
  border-radius: 12px; box-shadow: none; background: #fff;
}
/* 裁剪面板里文档列表限高，保证裁剪按钮始终可见 */
.writer.wps .wbody > .left.dock-left .docs { max-height: 30vh; overflow: auto; }
@media (max-width: 1100px) {
  .writer.wps .wbody { flex-direction: column; }
  .writer.wps .wbody > .left.dock-left,
  .writer.wps .wbody > .wdrawer.dock { flex: none; width: 100%; max-height: 46vh; }
}
</style>
