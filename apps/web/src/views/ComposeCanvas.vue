<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { renderMath } from '../math.js'
import { loadShortcuts, matchCombo } from '../shortcuts.js'

const route = useRoute()
const router = useRouter()

const PW = 595
const PH = 842
const MARGIN = 48

// ---------- 画布 ----------
const blocks = ref([])
const selectedId = ref('')
const selIds = ref([])
const clip = ref([])
const bandOpen = ref(false)
const band = reactive({
  header_left: '', header_center: '', header_right: '', footer_center: '', name_line: false,
})
const hasMeta = computed(() => !!(
  band.header_left || band.header_center || band.header_right || band.footer_center || band.name_line
))

function isSel(id) { return selIds.value.includes(id) }
function selectOnly(id) { selectedId.value = id || ''; selIds.value = id ? [id] : [] }
function selectBlocks(list) {
  selIds.value = list.map((b) => b.id)
  selectedId.value = selIds.value[0] || ''
}
function selectGroupOf(b) {
  if (b.gid) selectBlocks(blocks.value.filter((x) => x.gid === b.gid))
  else selectOnly(b.id)
}
function toggleSel(b) {
  if (isSel(b.id)) {
    selIds.value = selIds.value.filter((x) => x !== b.id)
    selectedId.value = selIds.value[0] || ''
  } else {
    selIds.value = [...selIds.value, b.id]
    selectedId.value = b.id
  }
}
function clearSel() { selectedId.value = ''; selIds.value = [] }

function groupSel() {
  if (selIds.value.length < 2) { err.value = '先按住 Shift 点选两个以上元素，再组合'; return }
  const gid = 'g' + Date.now().toString(36)
  for (const b of blocks.value) if (isSel(b.id)) b.gid = gid
  msg.value = `已组合 ${selIds.value.length} 个元素（点其中任意一个即可整体移动）`
}
function ungroupSel() {
  let n = 0
  for (const b of blocks.value) if (isSel(b.id) && b.gid) { b.gid = null; n++ }
  msg.value = n ? `已拆分 ${n} 个元素` : '所选元素没有组合'
}

function copySel() {
  const list = blocks.value.filter((b) => isSel(b.id))
  if (!list.length) { err.value = '先选中元素再复制'; return }
  clip.value = list.map((b) => ({ ...b }))
  msg.value = `已复制 ${list.length} 个元素（Ctrl+V 粘贴）`
}
function pasteClip() {
  if (!clip.value.length) { err.value = '剪贴板是空的：先选中元素按 Ctrl+C'; return }
  const gmap = {}
  const made = []
  for (const src of clip.value) {
    const b = reactive({ ...src, id: uid(), x: Math.min(src.x + 16, PW - 40), y: Math.min(src.y + 16, PH - 40) })
    if (src.gid) { gmap[src.gid] = gmap[src.gid] || 'g' + uid(); b.gid = gmap[src.gid] }
    blocks.value.push(b)
    made.push(b)
  }
  selectBlocks(made)
  msg.value = `已粘贴 ${made.length} 个元素`
}
function removeSelected() {
  if (!selIds.value.length) return
  const ids = new Set(selIds.value)
  blocks.value = blocks.value.filter((b) => !ids.has(b.id))
  clearSel()
}
function keymap() { return loadShortcuts() }

function onKeys(e) {
  const t = e.target || {}
  const tag = (t.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || t.isContentEditable) return
  const m = keymap()
  const k = (e.key || '').toLowerCase()
  if (matchCombo(e, 'copy', m)) { copySel(); e.preventDefault(); return }
  if (matchCombo(e, 'paste', m)) { pasteClip(); e.preventDefault(); return }
  if (matchCombo(e, 'group', m)) { groupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'ungroup', m)) { ungroupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'rotate', m)) { rotateSel(); e.preventDefault(); return }
  if (matchCombo(e, 'del', m) || (k === 'backspace' && !m.del)) {
    if (selIds.value.length) { removeSelected(); e.preventDefault() }
    return
  }
  if (matchCombo(e, 'esc', m)) { clearSel(); editingId.value = ''; return }
  const step = e.shiftKey ? 10 : 1
  const ids = selIds.value.length ? selIds.value : (selectedId.value ? [selectedId.value] : [])
  if (!ids.length) return
  const delta = { arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step] }[k]
  if (!delta) return
  for (const id of ids) {
    const b = blocks.value.find((x) => x.id === id)
    if (!b) continue
    b.x = clamp(Math.round(b.x + delta[0]), 0, PW - (b.w || 100))
    b.y = clamp(Math.round(b.y + delta[1]), 0, PH - 40)
  }
  e.preventDefault()
}
const editingId = ref('')
const scale = ref(1)
const autoAlign = ref(true)
const trimBlank = ref(true)   // 保存时裁掉下方空白
const contentBottom = ref(PH)  // 内容底部（pt），用于把画布页面缩到内容处

function measureBottom() {
  const el = canvasEl.value
  if (!el) return
  let m = 0
  el.querySelectorAll('.blk').forEach((k) => {
    m = Math.max(m, (k.offsetTop + k.offsetHeight) / (scale.value || 1))
  })
  contentBottom.value = (m <= 0 ? PH : Math.min(PH * 6, Math.max(PH, m + 24)))
}

watch([blocks, scale, trimBlank], () => nextTick(measureBottom), { deep: true })
const canvasEl = ref(null)

const selected = computed(() => blocks.value.find((b) => b.id === selectedId.value) || null)
const selectedText = computed(() => (selected.value && selected.value.type === 'text' ? selected.value : null))

let seq = 0
const uid = () => `b${Date.now().toString(36)}${(seq++).toString(36)}`
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

function blockH(b) {
  if (b.type === 'image') return b.h || b.w * 0.4
  const size = b.size || 13
  const per = Math.max(8, Math.floor((b.w || 400) / (size * 0.98)))
  return Math.max(size * 1.6, Math.ceil((b.text || '').length / per) * size * 1.55)
}

function nextY() {
  const bottom = blocks.value.reduce((m, b) => Math.max(m, b.y + blockH(b)), 40)
  return Math.min(PH * 6 - 40, Math.round(bottom + 10))
}

function rotateSel() {
  const target = selected.value && selected.value.type === 'image'
    ? selected.value
    : blocks.value.find((b) => b.type === 'image' && selIds.value.includes(b.id))
  if (!target) { err.value = '先选中一张题图再旋转'; return }
  target.rotate = ((target.rotate || 0) + 90) % 360
  if (target.rotate % 180 === 90) {
    const w = target.w
    target.w = target.h
    target.h = w
  }
  err.value = ''
}

function addText(text = '') {
  const b = reactive({
    id: uid(), type: 'text', text: text || '', x: MARGIN, y: nextY(),
    w: PW - MARGIN * 2, size: 13, align: 'left', bold: false,
  })
  blocks.value.push(b)
  selectOnly(b.id)
  if (!text) nextTick(() => { editingId.value = b.id })
  return b
}

function imageSize(path) {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve({ w: img.naturalWidth || 600, h: img.naturalHeight || 400 })
    img.onerror = () => resolve({ w: 600, h: 400 })
    img.src = imgUrl(path)
  })
}

async function addImage(path, atX, atY) {
  const nat = await imageSize(path)
  const ratio = nat.h / Math.max(1, nat.w)
  let w = Math.round((PW - MARGIN * 2) * 0.72)
  if (nat.w < nat.h) w = Math.round(w * 0.7)     // 竖图窄一点
  w = clamp(w, 160, PW - MARGIN * 2)
  const h = Math.round(w * ratio)
  const b = reactive({ id: uid(), type: 'image', path, x: MARGIN, y: 0, w, h })
  b.y = atY != null ? clamp(Math.round(atY - h / 2), 0, PH * 6 - h - 10) : Math.min(PH * 6 - h - 20, nextY())
  if (atX != null) {
    const cx = clamp(atX - w / 2, 0, Math.max(0, PW - w))
    if (autoAlign.value) {
      const candidates = [MARGIN, Math.round((PW - w) / 2), Math.round(PW - MARGIN - w)]
      b.x = candidates.reduce((a, c) => (Math.abs(c - cx) < Math.abs(a - cx) ? c : a), candidates[0])
    } else {
      b.x = Math.round(cx)
    }
  }
  blocks.value.push(b)
  selectOnly(b.id)
  return b
}

// ---------- 拖动 / 缩放 ----------
let drag = null

function startDrag(e, b) {
  if (editingId.value === b.id) return
  if (e.shiftKey) { toggleSel(b); return }
  if (!isSel(b.id)) selectGroupOf(b)
  else if (b.gid && !e.altKey) selectGroupOf(b)
  const moving = blocks.value.filter((x) => isSel(x.id)).map((x) => ({ id: x.id, x: x.x, y: x.y }))
  drag = { ids: moving.length ? moving : [{ id: b.id, x: b.x, y: b.y }], sx: e.clientX, sy: e.clientY, kind: 'move' }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', endDrag)
}

function startResize(e, b) {
  selectOnly(b.id)
  drag = { id: b.id, sx: e.clientX, sy: e.clientY, w: b.w, h: b.h, kind: 'size' }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', endDrag)
}

function onMove(e) {
  if (!drag) return
  const dx = (e.clientX - drag.sx) / scale.value
  const dy = (e.clientY - drag.sy) / scale.value
  if (drag.kind === 'move') {
    for (const it of drag.ids) {
      const t = blocks.value.find((x) => x.id === it.id)
      if (!t) continue
      t.x = clamp(Math.round(it.x + dx), 0, Math.max(0, PW - (t.w || 40)))
      t.y = clamp(Math.round(it.y + dy), 0, Math.max(0, PH - (t.h || 40)))
    }
    if (autoAlign.value) {
      const p = blocks.value.find((x) => x.id === drag.ids[0].id)
      if (p) {
        if (Math.abs(p.x - MARGIN) < 10) p.x = MARGIN
        if (Math.abs(p.y - Math.round(p.y / 4) * 4) < 3) p.y = Math.round(p.y / 4) * 4
      }
    }
  } else {
    const b = blocks.value.find((x) => x.id === drag.id)
    if (!b) return
    const nw = clamp(Math.round(drag.w + dx), 80, PW - b.x - 8)
    b.w = nw
    if (b.type === 'image') b.h = Math.round(nw * (drag.h / Math.max(1, drag.w)))
  }
}

function endDrag() {
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', endDrag)
  drag = null
}

function removeBlock(id) {
  blocks.value = blocks.value.filter((b) => b.id !== id)
  if (isSel(id)) {
    selIds.value = selIds.value.filter((x) => x !== id)
    selectedId.value = selIds.value[0] || ''
  }
}

function bringTop(id) {
  const b = blocks.value.find((x) => x.id === id)
  if (!b) return
  blocks.value = [...blocks.value.filter((x) => x.id !== id), b]
}

// ---------- 文字样式 ----------
function bumpSize(delta) {
  const b = selectedText.value
  if (!b) return
  b.size = clamp(Math.round((b.size || 13) + delta), 6, 48)
}
function toggleBold() { const b = selectedText.value; if (b) b.bold = !b.bold }
function setAlign(a) { const b = selectedText.value; if (b) b.align = a }

// ---------- 左侧：文档与裁剪 ----------
const docs = ref([])
const activeDoc = ref(null)
const pageNo = ref(1)
const zoom = ref(2)
const sel = ref(null)
const crops = ref([])
const busy = ref('')
const msg = ref('')
const err = ref('')

const pageImg = ref(null)
const pageUrl = computed(() => (activeDoc.value
  ? `/api/manual/doc/${activeDoc.value.doc_id}/page/${pageNo.value}.png?zoom=${zoom.value}`
  : ''))
function imgUrl(p) { return p ? `/api/images/${p}` : '' }

async function upload(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  if (!files.length) return
  busy.value = `上传 ${files.length} 个文件…`
  err.value = ''
  try {
    for (const f of files) {
      const fd = new FormData()
      fd.append('file', f)
      const d = await api.postForm('/api/manual/upload', fd)
      docs.value.push({ doc_id: d.doc_id, filename: d.filename, page_count: d.page_count })
      activeDoc.value = docs.value[docs.value.length - 1]
      pageNo.value = 1
      sel.value = null
    }
    msg.value = '已放入。在页面上拖框选一块，点「裁剪放入」'
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}

function turn(d) {
  if (!activeDoc.value) return
  pageNo.value = clamp(pageNo.value + d, 1, activeDoc.value.page_count)
  sel.value = null
}

let boxing = null
function localPos(e) {
  const r = pageImg.value.getBoundingClientRect()
  return {
    rx: clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 1),
    ry: clamp((e.clientY - r.top) / Math.max(1, r.height), 0, 1),
  }
}
function boxDown(e) {
  if (!activeDoc.value) return
  const p = localPos(e)
  boxing = { x0: p.rx, y0: p.ry, x1: p.rx, y1: p.ry }
  sel.value = { ...boxing }
  window.addEventListener('pointermove', boxMove)
  window.addEventListener('pointerup', boxUp)
}
function boxMove(e) {
  if (!boxing) return
  const p = localPos(e)
  sel.value = {
    x0: Math.min(boxing.x0, p.rx), y0: Math.min(boxing.y0, p.ry),
    x1: Math.max(boxing.x0, p.rx), y1: Math.max(boxing.y0, p.ry),
  }
}
function boxUp() {
  window.removeEventListener('pointermove', boxMove)
  window.removeEventListener('pointerup', boxUp)
  boxing = null
}
function selStyle() {
  const s = sel.value
  if (!s) return {}
  return {
    left: `${s.x0 * 100}%`, top: `${s.y0 * 100}%`,
    width: `${(s.x1 - s.x0) * 100}%`, height: `${(s.y1 - s.y0) * 100}%`,
  }
}

async function crop() {
  const s = sel.value
  if (!activeDoc.value || !s) return
  if ((s.x1 - s.x0) * (s.y1 - s.y0) < 0.002) { err.value = '选的区域太小'; return }
  busy.value = '裁剪中…'
  err.value = ''
  try {
    const d = await api.post('/api/manual/crop', {
      doc_id: activeDoc.value.doc_id, page_no: pageNo.value,
      rect: [s.x0, s.y0, s.x1, s.y1], zoom: 3,
    })
    crops.value.push({ path: d.path, w: d.width, h: d.height })
    sel.value = null
    msg.value = '已裁剪：拖到右侧画布，或点缩略图直接追加'
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}

async function cropAnswer() {
  const s0 = sel.value
  if (!activeDoc.value || !s0) { err.value = '先在 PDF 上拖框选一块'; return }
  if ((s0.x1 - s0.x0) * (s0.y1 - s0.y0) < 0.002) { err.value = '选的区域太小'; return }
  busy.value = '裁剪答案图…'
  err.value = ''
  try {
    const d = await api.post('/api/manual/crop', {
      doc_id: activeDoc.value.doc_id, page_no: pageNo.value,
      rect: [s0.x0, s0.y0, s0.x1, s0.y1], zoom: 3,
    })
    answerImages.value.push({ path: d.path, w: d.width, h: d.height })
    sel.value = null
    answerOpen.value = true
    msg.value = '已裁剪为答案图（可继续裁剪更多，或在答案面板里删掉）'
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}

function dropAnswerImage(i) {
  answerImages.value.splice(i, 1)
}

function dragCrop(e, c) {
  e.dataTransfer.setData('text/plain', c.path)
  e.dataTransfer.effectAllowed = 'copy'
}

async function dropOnCanvas(e) {
  const path = e.dataTransfer.getData('text/plain')
  if (!path) return
  const r = canvasEl.value.getBoundingClientRect()
  await addImage(path, (e.clientX - r.left) / scale.value, (e.clientY - r.top) / scale.value)
}

async function appendCrop(c) { await addImage(c.path, null, null) }

// ---------- 其它功能 ----------
const auxOpen = ref(false)
const floatEl = ref(null)
const fpos = reactive({ x: 24, y: 110 })
let fdrag = null

function clampF() {
  const el = floatEl.value
  const w = el ? el.offsetWidth : 320
  const h = el ? el.offsetHeight : 40
  fpos.x = Math.max(8, Math.min(fpos.x, window.innerWidth - w - 8))
  fpos.y = Math.max(8, Math.min(fpos.y, window.innerHeight - h - 8))
}

function floatDown(e) {
  if (e.target.closest('button')) return
  fdrag = { sx: e.clientX, sy: e.clientY, x: fpos.x, y: fpos.y }
  window.addEventListener('pointermove', floatMove)
  window.addEventListener('pointerup', floatUp)
}

function floatMove(e) {
  if (!fdrag) return
  fpos.x = fdrag.x + (e.clientX - fdrag.sx)
  fpos.y = fdrag.y + (e.clientY - fdrag.sy)
  clampF()
}

function floatUp() {
  window.removeEventListener('pointermove', floatMove)
  window.removeEventListener('pointerup', floatUp)
  const el = floatEl.value
  const w = el ? el.offsetWidth : 320
  const h = el ? el.offsetHeight : 40
  const m = 12
  const dl = fpos.x
  const dr = window.innerWidth - fpos.x - w
  const dt = fpos.y
  const db = window.innerHeight - fpos.y - h
  if (Math.min(dl, dr) < 120) fpos.x = dl <= dr ? m : window.innerWidth - w - m
  if (Math.min(dt, db) < 120) fpos.y = dt <= db ? m : window.innerHeight - h - m
  fpos.x = Math.round(fpos.x)
  fpos.y = Math.round(fpos.y)
  try { localStorage.setItem('tiku-aux-pos', JSON.stringify({ x: fpos.x, y: fpos.y })) } catch (e2) {}
  fdrag = null
}

function toggleAux() {
  auxOpen.value = !auxOpen.value
  setTimeout(() => {
    const el = floatEl.value
    if (el && fpos.x + el.offsetWidth > window.innerWidth - 8) {
      fpos.x = Math.max(8, window.innerWidth - el.offsetWidth - 12)
    }
    clampF()
  }, 0)
}
const leftOpen = ref(true)
const meta = reactive({ books: [], tags: [], qtypes: [], default_book_id: null })
const form = reactive({ title: '', duration_minutes: 90, total_score: 0 })
const qList = ref([])
const qSearch = ref('')
const usedIds = ref([])
async function loadBanks() {
  try {
    const d = await api.get('/api/questions', { params: { page_size: 60, q: qSearch.value } })
    qList.value = d.items || []
  } catch (e2) { err.value = e2?.message || String(e2) }
}

function stemImg(q) {
  const imgs = q.images || []
  const hit = imgs.find((i) => (i.caption || '') !== '答案')
  return hit ? hit.path : ''
}

async function insertQuestion(q) {
  let path = stemImg(q)
  if (!path) { err.value = '这道题还没有题图，去「题库」点一次「裁空白」生成'; return }
  try {
    const t = await api.post('/api/manual/trim', { path })
    if (t && t.path) path = t.path
  } catch (e2) { /* 裁白边失败就用原图 */ }
  const b = await addImage(path, null, null)
  // 题图按 200dpi 渲染，1px = 0.36pt：按原始字号插入，超过版心才缩
  const nat = await imageSize(path)
  const natW = Math.round(nat.w * 0.36)
  const ratio = nat.h / Math.max(1, nat.w)
  b.w = Math.min(PW - MARGIN * 2, Math.max(240, natW))
  b.h = Math.round(b.w * ratio)
  b.x = MARGIN
  b.y = Math.min(b.y, PH * 6 - b.h - 20)   // addImage 已按内容底部排好，这里只做越界保护
  if (!usedIds.value.includes(q.id)) usedIds.value.push(q.id)
  msg.value = `已插入第 ${usedIds.value.length} 题`
}
// ---------- 保存 ----------
const saving = ref(false)
const editingQuestionId = ref(null)
const savedId = ref(null)

function payloadLayout() {
  return {
    width: PW, height: Math.round(trimBlank.value ? contentBottom.value : PH),
    blocks: blocks.value.map((b) => (b.type === 'image'
      ? { type: 'image', path: b.path, x: b.x, y: b.y, w: b.w, h: b.h, rotate: b.rotate || 0 }
      : { type: 'text', text: b.text, x: b.x, y: b.y, w: b.w, size: b.size, align: b.align, bold: b.bold })),
    meta: hasMeta.value ? {
      header: { left: band.header_left, center: band.header_center, right: band.header_right },
      footer: { center: band.footer_center },
      name_line: band.name_line,
    } : {},
    trim: trimBlank.value,
    trim_pad: 24,
  }
}

async function save() {
  if (!blocks.value.length) { err.value = '画布是空的：先插入题目图或写点文字'; return }
  saving.value = true
  err.value = ''; msg.value = ''
  try {
    const r = await api.post('/api/papers/canvas', {
      paper_id: editingQuestionId.value || null,
      title: form.title || '试卷草稿',
      duration_minutes: Number(form.duration_minutes) || 90,
      total_score: Number(form.total_score) || 0,
      layout: payloadLayout(),
      question_ids: usedIds.value,
    })
    editingQuestionId.value = r.id
    savedId.value = r.id
    msg.value = r.created ? `已生成试卷 #${r.id}` : `已更新试卷 #${r.id}`
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { saving.value = false }
}

function resetAll() {
  blocks.value = []
  selectedId.value = ''
  editingId.value = ''
  editingQuestionId.value = null
  savedId.value = null
  err.value = ''
  msg.value = '画布已清空，可以出下一题'
}

async function loadExisting(id) {
  try {
    const d = await api.get(`/api/papers/${id}/canvas`)
    blocks.value = ((d.layout || {}).blocks || []).map((b) => reactive({ ...b, id: uid() }))
    const lm = (d.layout || {}).meta || {}
    const lh = lm.header || {}
    band.header_left = lh.left || ''
    band.header_center = lh.center || ''
    band.header_right = lh.right || ''
    band.footer_center = (lm.footer || {}).center || ''
    band.name_line = !!lm.name_line
    if (band.header_left || band.header_center || band.header_right || band.footer_center || band.name_line) {
      bandOpen.value = true
    }
    editingQuestionId.value = d.id
    savedId.value = d.id
    form.title = d.title || ''
    form.duration_minutes = d.duration_minutes || 90
    form.total_score = d.total_score || 0
    usedIds.value = [...(d.question_ids || [])]
    msg.value = `已载入试卷 #${d.id}，可继续编排`
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

function fitScale() {
  const host = canvasEl.value ? canvasEl.value.parentElement : null
  const w = host ? host.clientWidth : 800
  scale.value = clamp((w - 56) / PW, 0.55, 1.15)
}

let ro = null
onMounted(async () => {
  await loadBanks()
  try {
    const saved = JSON.parse(localStorage.getItem('tiku-aux-pos') || 'null')
    if (saved && Number.isFinite(saved.x)) { fpos.x = saved.x; fpos.y = saved.y }
    else { fpos.x = Math.max(16, window.innerWidth - 72); fpos.y = Math.round(window.innerHeight / 2 - 20) }
  } catch (e2) {}
  setTimeout(clampF, 0)
  fitScale()
  ro = new ResizeObserver(fitScale)
  if (canvasEl.value?.parentElement) ro.observe(canvasEl.value.parentElement)
  window.addEventListener('keydown', onKeys)
  const qid = Number(route.query.id || 0)
  if (qid) await loadExisting(qid)
})
onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  window.removeEventListener('keydown', onKeys)
})

function goPaper() { router.push(`/papers/${savedId.value}`) }

const vFocus = { mounted(el) { el.focus() } }
</script>

<template>
  <div class="writer" :class="{ 'left-off': !leftOpen }">
    <aside class="left" v-show="leftOpen">
      <section class="pane">
        <h3>PDF 放入</h3>
        <label class="drop">
          <input type="file" accept="application/pdf,image/*" multiple @change="upload" />
          <span>＋ 选择 PDF / 图片（可多份）</span>
        </label>
        <div class="docs" v-if="docs.length">
          <button v-for="d in docs" :key="d.doc_id" class="doc"
                  :class="{ on: activeDoc && activeDoc.doc_id === d.doc_id }"
                  @click="activeDoc = d; pageNo = 1; sel = null">
            {{ d.filename }}<i>{{ d.page_count }} 页</i>
          </button>
        </div>
        <div class="pager" v-if="activeDoc">
          <button @click="turn(-1)" :disabled="pageNo <= 1">‹</button>
          <span>{{ pageNo }} / {{ activeDoc.page_count }}</span>
          <button @click="turn(1)" :disabled="pageNo >= activeDoc.page_count">›</button>
          <select v-model.number="zoom">
            <option :value="1.5">标准</option>
            <option :value="2">清晰</option>
            <option :value="3">放大</option>
          </select>
        </div>
        <div class="viewer" v-if="activeDoc">
          <div class="page-wrap" @pointerdown="boxDown">
            <img ref="pageImg" :src="pageUrl" draggable="false" @dragstart.prevent />
            <div v-if="sel" class="selbox" :style="selStyle()"></div>
          </div>
        </div>
        <div class="hint" v-else>放进一份 PDF（或旧卷子），拖框裁剪后拖到右边排版。</div>
        <div class="row">
          <button class="primary" :disabled="!sel" @click="crop">裁剪放入</button>
          <button :disabled="!sel" @click="sel = null">清除选区</button>
        </div>
        <div class="tray" v-if="crops.length">
          <div class="tray-head">待放入（拖到右侧，或点一下追加）</div>
          <div class="tray-list">
            <img v-for="c in crops" :key="c.path" :src="imgUrl(c.path)" draggable="true" title="拖入画布"
                 @dragstart="dragCrop($event, c)" @click="appendCrop(c)" />
          </div>
        </div>
        <div class="busy" v-if="busy">{{ busy }}</div>
      </section>
    </aside>

    <div ref="floatEl" class="auxfloat" :class="{ min: !auxOpen }"
         :style="{ left: fpos.x + 'px', top: fpos.y + 'px' }">
      <div class="auxhead" @pointerdown="floatDown" @dblclick="toggleAux">
        <span v-if="!auxOpen" class="burger" title="其它功能（拖动可移动，双击展开）">≡</span>
        <template v-else>
          <b>试卷设置</b>
          <span class="spacer"></span>
          <button class="icon" title="收起" @click.stop="toggleAux">—</button>
        </template>
      </div>
      <div v-if="auxOpen" class="auxbody">
        <div class="aux">
          <label class="f"><span>试卷标题</span>
            <input v-model="form.title" placeholder="如：2026 秋季学期期中试卷（A 卷）" />
          </label>
          <div class="cols2">
            <label class="f"><span>限时（分钟）</span>
              <input type="number" v-model.number="form.duration_minutes" />
            </label>
            <label class="f"><span>总分</span>
              <input type="number" v-model.number="form.total_score" />
            </label>
          </div>
          <div class="f">
            <span>从题库插入（已插入 {{ usedIds.length }} 题）</span>
            <input v-model="qSearch" placeholder="搜题干 / 答案 / 文献名，回车" @keyup.enter="loadBanks" />
            <div class="qlist">
              <button v-for="q in qList" :key="q.id" class="qitem" @click="insertQuestion(q)">
                <b>#{{ q.id }}</b> {{ q.q_type }}
                <span class="qtext">{{ (q.stem || '（纯图题）').slice(0, 46) }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <section class="right">
      <div class="toolbar">
        <button class="icon" :title="leftOpen ? '收起左侧' : '展开左侧'" @click="leftOpen = !leftOpen">
          {{ leftOpen ? '⇤' : '⇥' }}
        </button>
        <span class="tdiv"></span>
        <div class="tgroup">
          <span class="lbl">字号</span>
          <button class="icon" @click="bumpSize(-1)">A－</button>
          <b class="size">{{ selectedText ? selectedText.size : 13 }}</b>
          <button class="icon" @click="bumpSize(1)">A＋</button>
        </div>
        <span class="tdiv"></span>
        <div class="tgroup">
          <button class="icon" :class="{ on: selectedText && selectedText.bold }" @click="toggleBold"><b>B</b></button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'left' }" @click="setAlign('left')">⇤</button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'center' }" @click="setAlign('center')">↔</button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'right' }" @click="setAlign('right')">⇥</button>
        </div>
        <span class="tdiv"></span>
        <button class="ghost" @click="addText('')">＋ 文本</button>
        <button class="ghost" :class="{ on: bandOpen }" @click="bandOpen = !bandOpen">页眉页脚</button>
        <button class="ghost" :disabled="!selected" @click="rotateSel" title="旋转选中的题图（快捷键 R）">旋转 90°</button>
        <button class="ghost" :disabled="!selIds.length" @click="copySel">复制</button>
          <button class="ghost" :disabled="!clip.length" @click="pasteClip">粘贴</button>
          <button class="ghost" :disabled="selIds.length < 2" @click="groupSel">组合</button>
        <button class="ghost" :disabled="!selIds.length" @click="ungroupSel">拆分</button>
        <button class="ghost" :disabled="!selIds.length" @click="removeSelected">
          删除所选{{ selIds.length > 1 ? `(${selIds.length})` : '' }}
        </button>
        <span class="spacer"></span>
        <label class="chk"><input type="checkbox" v-model="autoAlign" /> 自动对齐</label>
    <label class="chk" title="保存时按内容裁掉页面下方的空白"><input type="checkbox" v-model="trimBlank" /> 裁空白</label>
        <button v-if="editingQuestionId" @click="resetAll">新建一卷</button>
        <button class="primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : (editingQuestionId ? '更新试卷' : '保存为试卷') }}
        </button>
      </div>



      <div v-if="bandOpen" class="answerpop">
        <div class="aphead">
          <b>页眉 / 页脚</b>
          <span class="muted">比如「XX 大学 2026 秋季期中考试」+ 页码，保存时会画进题图</span>
          <span class="spacer"></span>
          <button class="ghost sm" @click="bandOpen = false">收起</button>
        </div>
        <div class="bandrow">
          <input v-model="band.header_left" placeholder="页眉左（可空）" />
          <input v-model="band.header_center" placeholder="页眉中，如：XX 大学 2026 年秋季期中考试" />
          <input v-model="band.header_right" placeholder="页眉右，如：A 卷" />
        </div>
        <div class="bandrow">
          <input v-model="band.footer_center" placeholder="页脚中，如：第 1 页 共 1 页" />
          <label class="chk"><input v-model="band.name_line" type="checkbox" /> 加「姓名 / 班级 / 得分」栏</label>
        </div>
      </div>

      <div v-if="err" class="errbox">{{ err }}</div>
      <div v-if="msg" class="okbox">
        {{ msg }}
        <a v-if="savedId" href="javascript:void(0)" @click="goPaper">去试卷页看看 →</a>
      </div>

      <div class="canvas-wrap">
        <div ref="canvasEl" class="page" :style="{ width: PW * scale + 'px', height: (trimBlank ? contentBottom : PH) * scale + 'px' }"
             @dragover.prevent @drop.prevent="dropOnCanvas" @pointerdown.self="clearSel">
          <div v-if="!blocks.length" class="empty">从题库插入题目、或把左侧裁好的题图拖进来，再从「＋ 文本」加标题</div>
          <div v-for="b in blocks" :key="b.id" class="blk"
               :class="{ sel: isSel(b.id), img: b.type === 'image', grouped: !!b.gid }"
               :data-gid="b.gid || ''"
               :style="{ left: b.x * scale + 'px', top: b.y * scale + 'px', width: b.w * scale + 'px',
                         height: b.type === 'image' ? (b.h || 0) * scale + 'px' : 'auto' }"
               @pointerdown="startDrag($event, b)"
               @dblclick="b.type === 'text' ? (editingId = b.id) : null">
            <template v-if="b.type === 'image'">
              <img :src="imgUrl(b.path)" draggable="false" @dragstart.prevent />
              <span class="handle" @pointerdown.stop="startResize($event, b)"></span>
            </template>
            <template v-else>
              <textarea v-if="editingId === b.id" v-model="b.text" class="etext" v-focus
                        :style="{ fontSize: b.size * scale + 'px', textAlign: b.align, fontWeight: b.bold ? 700 : 400 }"
                        @pointerdown.stop @blur="editingId = ''"></textarea>
              <div v-else class="tt" :style="{ fontSize: b.size * scale + 'px', textAlign: b.align,
                          fontWeight: b.bold ? 700 : 400 }"
                   v-html="renderMath(b.text) || '<span class=\'ph\'>（双击输入文字）</span>'"></div>
              <span class="handle text" @pointerdown.stop="startResize($event, b)"></span>
            </template>
          </div>
        </div>
      </div>

      <div class="foot">
        <span class="muted">版面 {{ PW }} × {{ trimBlank ? Math.round(contentBottom) : PH }} pt（约 {{ Math.ceil((trimBlank ? contentBottom : PH) / PH) }} 页）· 共 {{ blocks.length }} 个元素</span>
        <span class="muted" v-if="selectedText">已选文字：{{ selectedText.size }}pt · {{ selectedText.align }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.writer { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; align-items: start; }
.writer.left-off { grid-template-columns: minmax(0, 1fr); }
.left-off .left { display: none; }
.left { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.pane { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px; }
.pane h3 { margin: 0 0 10px; font-size: 14px; }
.pane h3.fold { display: flex; justify-content: space-between; cursor: pointer; }
.pane h3.fold span { font-size: 12px; color: var(--brand, #2f6fed); font-weight: 400; }
.drop { display: block; border: 1px dashed #c7d2e4; border-radius: 10px; padding: 12px; text-align: center; color: #4a5568; cursor: pointer; font-size: 13px; }
.drop input { display: none; }
.docs { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.doc { text-align: left; background: #f7f9fc; border: 1px solid #e7ebf2; border-radius: 8px; padding: 6px 8px; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc i { color: #8b95a5; font-style: normal; margin-left: 6px; }
.doc.on { border-color: var(--brand, #2f6fed); background: #eef4ff; color: var(--brand, #2f6fed); }
.pager { display: flex; align-items: center; gap: 6px; margin: 8px 0; font-size: 12.5px; }
.pager select { width: auto; }
.viewer { background: #f1f3f7; border-radius: 10px; padding: 8px; max-height: calc(100vh - 300px); overflow: auto; }
.page-wrap { position: relative; display: block; margin: 0 auto; cursor: crosshair; }
.page-wrap img { display: block; width: 100%; height: auto; touch-action: none; user-select: none; }
.selbox { position: absolute; border: 1px dashed var(--brand, #2f6fed); background: rgba(47, 111, 237, .12); pointer-events: none; }
.hint { color: #8b95a5; font-size: 12.5px; }
.row { display: flex; gap: 8px; margin-top: 10px; }
.row button { flex: 1; }
.tray { margin-top: 10px; }
.tray-head { font-size: 12px; color: #667085; margin-bottom: 6px; }
.tray-list { display: flex; flex-wrap: wrap; gap: 8px; }
.tray-list img { width: 82px; height: 58px; object-fit: cover; border: 1px solid #dbe3ef; border-radius: 6px; cursor: grab; background: #fff; }
.busy { margin-top: 8px; font-size: 12px; color: var(--brand, #2f6fed); }
.aux { display: flex; flex-direction: column; gap: 8px; }
.f { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: #475467; }
.f input, .f select, .f textarea { font-size: 13px; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid #dfe5ee; background: #fff; border-radius: 999px; padding: 3px 10px; font-size: 12px; color: #475467; cursor: pointer; }
.chip.on { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; }

.right { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px; min-width: 0; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding-bottom: 10px; border-bottom: 1px solid #eef1f6; }
.tgroup { display: flex; align-items: center; gap: 4px; }
.tgroup .lbl { font-size: 12px; color: #667085; }
.tgroup .size { min-width: 22px; text-align: center; font-size: 12.5px; }
.tdiv { width: 1px; height: 18px; background: #e7ebf2; }
.icon { padding: 4px 8px; border: 1px solid #dfe5ee; background: #fff; border-radius: 7px; cursor: pointer; font-size: 12.5px; }
.icon.on { background: #eef4ff; border-color: #b9cdf7; color: var(--brand, #2f6fed); }
.spacer { flex: 1; }
.chk { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: #475467; }

.canvas-wrap { margin-top: 12px; background: #f1f3f7; border-radius: 10px; padding: 16px; display: flex; justify-content: center; max-height: calc(100vh - 200px); overflow: auto; }
.page { position: relative; background: #fff; box-shadow: 0 6px 24px rgba(16, 24, 40, .1); flex: 0 0 auto; }
.page .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #98a2b3; font-size: 13px; text-align: center; padding: 0 40px; }
.blk { position: absolute; cursor: move; }
.blk.sel { outline: 1px dashed var(--brand, #2f6fed); outline-offset: 1px; }
.blk.img img { display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; }
.blk .tt { white-space: pre-wrap; line-height: 1.55; color: #101828; min-height: 18px; }
.blk :deep(.ph) { color: #b6bdc9; }
.blk .etext { width: 100%; border: 1px solid var(--brand, #2f6fed); border-radius: 4px; padding: 2px 4px; line-height: 1.55; resize: vertical; font-family: inherit; }
.handle { position: absolute; right: -5px; bottom: -5px; width: 10px; height: 10px; background: #fff; border: 1px solid var(--brand, #2f6fed); border-radius: 2px; cursor: nwse-resize; }
.handle.text { bottom: -6px; }
.foot { display: flex; justify-content: space-between; margin-top: 8px; font-size: 12px; }
@media (max-width: 1100px) {
  .writer { grid-template-columns: 1fr; }
  .left { position: static; }
}

/* ---- 「其它功能」浮动面板 ---- */
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
.auxfloat.min .auxhead { border-bottom: none; cursor: grab; }
.auxhead:active { cursor: grabbing; }
.auxhead b { font-size: 13px; }
.auxhead .burger { font-size: 18px; line-height: 1; padding: 2px 4px; }
.auxhead .spacer { flex: 1; }
.auxbody { padding: 12px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.toolbar .primary { margin-left: auto; }

/* ---- 答案浮层 ---- */
.answerpop {
  margin-top: 10px; background: #f8fafc; border: 1px solid var(--line, #e7ebf2);
  border-radius: 10px; padding: 10px 12px;
}
.aphead { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; }
.aphead .muted { font-size: 12px; }
.answerpop textarea { width: 100%; font-family: inherit; font-size: 13px; line-height: 1.6; }
.ansfigs { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
.ansfigs figure { position: relative; margin: 0; }
.ansfigs img { max-width: 150px; max-height: 110px; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
.ansfigs .del {
  position: absolute; top: -6px; right: -6px; width: 18px; height: 18px; padding: 0;
  border-radius: 50%; border: 1px solid var(--line); background: #fff; color: #b42318; line-height: 1;
}
.apprev { margin-top: 8px; padding: 8px 10px; background: #f8fafc; border-radius: 8px; font-size: 13.5px; }
.blk.grouped { outline: 1px dashed rgba(47, 111, 237, .45); outline-offset: 3px; }
</style>

<style scoped>
.bandrow { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; }
.bandrow input[type="text"], .bandrow input:not([type]) { flex: 1 1 200px; min-width: 160px; }
.qlist { display: flex; flex-direction: column; gap: 4px; max-height: 240px; overflow: auto; margin-top: 6px; }
.qitem {
  text-align: left; background: #f7f9fc; border: 1px solid #e7ebf2; border-radius: 8px;
  padding: 5px 8px; font-size: 12px; color: #344054; display: flex; gap: 6px; align-items: center;
}
.qitem:hover { border-color: var(--brand); color: var(--brand); }
.qitem b { color: var(--brand); font-weight: 600; }
.qtext { color: #667085; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
