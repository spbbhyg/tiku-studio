<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import { renderMath } from '../math.js'
import { flowText } from '../textflow.js'

const props = defineProps({
  title: { type: String, default: '画布' },
  hint: { type: String, default: '把左侧裁好的图拖进来，或点「＋ 文本」开始排版' },
  maxPages: { type: Number, default: 1 },
  metaable: { type: Boolean, default: false },   // 是否带页眉页脚（只有题目画布需要）
})
const emit = defineEmits(['activate', 'changed'])

const PW = 595
const PH = 842
const MARGIN = 48

const blocks = ref([])
const selectedId = ref('')
const selIds = ref([])
const clip = ref([])
const editingId = ref('')
const scale = ref(1)
const autoAlign = ref(true)
const trimBlank = ref(true)
const fontTarget = ref(12)          // 统一字号（pt）
const textColor = ref('#111111')    // 文字颜色（选中块或新建块）
const tip = ref('')                 // 画布内提示（统一字号结果等）
const imgPxPerPt = ref(3)           // 裁剪图默认 zoom=3（3px/pt）
const collapsed = ref(false)
const metaOpen = ref(false)
const marquee = ref(null)
const charSel = ref(null)            // 文字块内的字符选区：{ id, start, end, text }
const tr = reactive({ open: false, text: '', trans: '', target: '简体中文', busy: false, err: '', msg: '' })
const band = reactive({ header_left: '', header_center: '', header_right: '', footer_center: '', name_line: false })
const hasMeta = computed(() => !!(band.header_left || band.header_center || band.header_right || band.footer_center || band.name_line))

let seq = 0
const uid = () => `b${Date.now().toString(36)}${(seq++).toString(36)}`
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
const imgUrl = (p) => (p ? `/api/images/${p}` : '')

function blockH(b) {
  if (b.type === 'image') return b.h || b.w * 0.4
  const size = b.size || 13
  const per = Math.max(8, Math.floor((b.w || 400) / (size * 0.98)))
  return Math.max(size * 1.6, Math.ceil((b.text || '').length / per) * size * 1.55)
}

const limit = computed(() => props.maxPages * PH)
const contentBottom = computed(() => {
  const m = blocks.value.reduce((x, b) => Math.max(x, b.y + blockH(b)), 0)
  if (m <= 0) return Math.min(limit.value, 420)      // 空画布给个短画布，不用整页
  return Math.min(limit.value, Math.max(260, m + 24))
})
const pageHeight = computed(() => (trimBlank.value ? contentBottom.value : limit.value))
const selected = computed(() => blocks.value.find((b) => b.id === selectedId.value) || null)
const selectedText = computed(() => (selected.value && selected.value.type === 'text' ? selected.value : null))
const lockedImages = computed(() => blocks.value.filter((b) => b.type === 'image' && b.locked))
function pinStyle(b) {
  return {
    left: Math.max(0, (b.x + (b.w || 0)) * scale.value - 13) + 'px',
    top: Math.max(0, (b.y || 0) * scale.value - 11) + 'px',
  }
}

// ---------- 文字块内的「字符级」格式 ----------
// runs: [{ t, c?, s? }]，t 为文字，c 为颜色，s 为字号；没有 c/s 的段落继承块级设置。
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
function escHtml(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ESC_MAP[c]) }
function safeColor(c) { return /^#[0-9a-fA-F]{3,8}$/.test(String(c || '')) ? c : '' }
function hasRuns(b) {
  return Array.isArray(b.runs) && b.runs.some((r) => r && String(r.t || ''))
}
function textHtml(b) {
  if (!hasRuns(b)) return renderMath(b.text) || '<span class="ph">（双击输入文字）</span>'
  return b.runs.map((r) => {
    const t = String((r && r.t) || '')
    if (!t) return ''
    const st = []
    const c = safeColor(r.c)
    if (c) st.push(`color:${c}`)
    if (r.s) st.push(`font-size:${Math.round(Number(r.s) * scale.value)}px`)
    return st.length ? `<span style="${st.join(';')}">${escHtml(t)}</span>` : escHtml(t)
  }).join('')
}
function ensureRuns(b) {
  if (Array.isArray(b.runs) && b.runs.length) return b.runs
  b.runs = b.text ? [{ t: b.text }] : []
  return b.runs
}
function normalizeRuns(b) {
  if (!Array.isArray(b.runs)) return
  const out = []
  for (const r of b.runs) {
    const t = String((r && r.t) || '')
    if (!t) continue
    const c = safeColor(r.c)
    const s = Number(r.s) || 0
    const last = out[out.length - 1]
    if (last && (last.c || '') === (c || '') && (last.s || 0) === s) last.t += t
    else out.push({ t, ...(c ? { c } : {}), ...(s ? { s } : {}) })
  }
  b.runs = out
  b.text = out.map((r) => r.t).join('')
}
function splitRunsAt(b, at) {
  ensureRuns(b)
  const out = []
  let pos = 0
  for (const r of b.runs) {
    const len = String(r.t || '').length
    if (at > pos && at < pos + len) {
      out.push({ ...r, t: String(r.t).slice(0, at - pos) })
      out.push({ ...r, t: String(r.t).slice(at - pos) })
    } else out.push({ ...r })
    pos += len
  }
  b.runs = out
}
function applyRange(b, start, end, patch) {
  ensureRuns(b)
  const total = b.runs.reduce((n, r) => n + String(r.t || '').length, 0)
  start = clamp(Math.round(start), 0, total)
  end = clamp(Math.round(end), start, total)
  if (end <= start) return false
  splitRunsAt(b, start)
  splitRunsAt(b, end)
  let pos = 0
  for (const r of b.runs) {
    const len = String(r.t || '').length
    if (pos >= start && pos + len <= end) Object.assign(r, typeof patch === 'function' ? patch(r) : patch)
    pos += len
  }
  normalizeRuns(b)
  return true
}
function rebuildRuns(b) {
  const old = Array.isArray(b.runs) ? b.runs : []
  const oldText = old.map((r) => String(r.t || '')).join('')
  const nt = String(b.text || '')
  if (!oldText || oldText === nt) return
  const styles = []
  for (const r of old) for (const _ of String(r.t || '')) styles.push({ c: r.c, s: r.s })
  let p = 0
  while (p < oldText.length && p < nt.length && oldText[p] === nt[p]) p += 1
  let sfx = 0
  while (sfx < oldText.length - p && sfx < nt.length - p
         && oldText[oldText.length - 1 - sfx] === nt[nt.length - 1 - sfx]) sfx += 1
  const out = []
  for (let i = 0; i < nt.length; i += 1) {
    let st = {}
    if (i < p) st = styles[i] || {}
    else if (i >= nt.length - sfx) st = styles[oldText.length - (nt.length - i)] || {}
    const c = safeColor(st.c)
    const s = Number(st.s) || 0
    const last = out[out.length - 1]
    if (last && (last.c || '') === (c || '') && (last.s || 0) === s) last.t += nt[i]
    else out.push({ t: nt[i], ...(c ? { c } : {}), ...(s ? { s } : {}) })
  }
  b.runs = out
}
function onTextInput(b) { rebuildRuns(b) }
function onTextMouseUp(b) {
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || !sel.rangeCount) return
  const root = canvasEl.value ? canvasEl.value.querySelector(`[data-bid="${b.id}"] .tt`) : null
  if (!root) return
  const range = sel.getRangeAt(0)
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return
  const pre = document.createRange()
  pre.selectNodeContents(root)
  pre.setEnd(range.startContainer, range.startOffset)
  const start = pre.toString().length
  const text = range.toString()
  if (!text) return
  charSel.value = { id: b.id, start, end: start + text.length, text }
  tip.value = `已选中 ${text.length} 个字符：点 A＋ / A－ 或「上色」只改这几个字`
}
function applyCharColor(color) {
  const cs = charSel.value
  if (!cs || !cs.text) return false
  const b = blocks.value.find((x) => x.id === cs.id)
  if (!b || b.type !== 'text') return false
  if (!applyRange(b, cs.start, cs.end, { c: color })) return false
  tip.value = `已把选中的 ${cs.text.length} 个字符设成 ${color}`
  emit('changed')
  return true
}
function applyCharSize(delta) {
  const cs = charSel.value
  if (!cs || !cs.text) return false
  const b = blocks.value.find((x) => x.id === cs.id)
  if (!b || b.type !== 'text') return false
  const ok = applyRange(b, cs.start, cs.end,
    (r) => ({ s: clamp(Math.round((Number(r.s) || Number(b.size) || 13) + delta), 6, 48) }))
  if (!ok) return false
  const now = b.runs.find((r) => r.s)
  tip.value = `已把选中的 ${cs.text.length} 个字符字号改为 ${now ? now.s : b.size}pt`
  emit('changed')
  return true
}
function toggleLock() {
  const b = selected.value
  if (!b || b.type !== 'image') return
  b.locked = !b.locked
  tip.value = b.locked
    ? '图片已固定：点右上角 🔒 可重新选中；要改图里文字请点「图片转文字」'
    : '图片已解锁，可以继续拖动或缩放'
  emit('changed')
}



function isSel(id) { return selIds.value.includes(id) }
function selectOnly(id) { selectedId.value = id || ''; selIds.value = id ? [id] : []; charSel.value = null }
function selectLocked(b) {
  selectOnly(b.id)
  tip.value = '图片已选中；图里的字是像素，不能直接改字号/颜色，可点「图片转文字」识别成可编辑文字，或解锁后拖动'
}
async function imageToText(ask = true) {
  const b = selected.value
  if (!b || b.type !== 'image') return
  if (!b.path) return
  if (ask && !window.confirm('识别出的文字会替换这张图片；原图仍留在资料库。继续？')) return
  tip.value = '正在识别图片里的文字…'
  try {
    const r = await api.post('/api/ai/ocr-image', { path: b.path })
    const text = String(r.text || '').trim()
    if (!text) { tip.value = '没有识别出文字：图片可能太糊，或只包含结构式/公式'; return }
    b.type = 'text'
    b.text = text
    b.size = clamp(Math.round(Number(b.fontPt) || 13), 6, 48)
    b.align = 'left'
    b.bold = false
    b.color = textColor.value || '#111111'
    delete b.runs
    delete b.path
    delete b.locked
    delete b.rotate
    delete b.pxPerPt
    delete b.fontPt
    delete b.h
    selectOnly(b.id)
    tip.value = `已识别 ${r.count} 行并替换为文字块，可直接改字号/颜色，或选中单字修改`
    emit('changed')
  } catch (e) {
    tip.value = e?.message || String(e)
  }
}

// 「对齐」：把所有文字块的首字左边缘对齐到最左边那一段
function alignTextAll() {
  const ts = blocks.value.filter((b) => b.type === 'text')
  if (!ts.length) { tip.value = '画布里还没有文字块'; return }
  const x = Math.min(...ts.map((b) => Number(b.x) || MARGIN))
  for (const b of ts) b.x = Math.round(x)
  tip.value = `已把所有文字块的首字对齐（${ts.length} 段）`
  emit('changed')
}

// 「统一」：把选中的文字（单字/整段）统一到当前字号和颜色
function unifySelected() {
  const cs = charSel.value
  if (cs && cs.text) {
    const b = blocks.value.find((x) => x.id === cs.id)
    if (b && b.type === 'text') {
      const ok = applyRange(b, cs.start, cs.end, () => ({
        s: clamp(Number(fontTarget.value) || Number(b.size) || 13, 6, 48),
        c: textColor.value || '#111111',
      }))
      if (ok) { tip.value = `已把选中的 ${cs.text.length} 个字符统一为 ${fontTarget.value}pt`; emit('changed'); return }
    }
  }
  const ids = selIds.value.length ? selIds.value : (selected.value ? [selected.value.id] : [])
  const list = blocks.value.filter((b) => ids.includes(b.id) && b.type === 'text')
  if (!list.length) { tip.value = '先选中要统一的文字块（或文字块里的字）'; return }
  for (const b of list) {
    b.size = clamp(Number(fontTarget.value) || Number(b.size) || 13, 6, 48)
    b.color = textColor.value || '#111111'
    delete b.runs
  }
  tip.value = `已统一 ${list.length} 个文字块的字号和颜色`
  emit('changed')
}

function selectBlocks(list) { selIds.value = list.map((b) => b.id); selectedId.value = selIds.value[0] || ''; charSel.value = null }
function selectGroupOf(b) { if (b.gid) selectBlocks(blocks.value.filter((x) => x.gid === b.gid)); else selectOnly(b.id) }
function toggleSel(b) {
  if (isSel(b.id)) { selIds.value = selIds.value.filter((x) => x !== b.id); selectedId.value = selIds.value[0] || '' }
  else { selIds.value = [...selIds.value, b.id]; selectedId.value = b.id }
}
function clearSel() { selectedId.value = ''; selIds.value = []; editingId.value = ''; charSel.value = null }

function selectAll() {
  if (blocks.value.length) selectBlocks(blocks.value)
}
function pageDown(e) {
  if (e.button !== 0) return
  emit('activate')
  charSel.value = null
  const host = canvasEl.value.getBoundingClientRect()
  const sx = (e.clientX - host.left) / scale.value
  const sy = (e.clientY - host.top) / scale.value
  marquee.value = { x: sx, y: sy, w: 0, h: 0 }
  let moved = false
  const move = (ev) => {
    const x = (ev.clientX - host.left) / scale.value
    const y = (ev.clientY - host.top) / scale.value
    if (Math.abs(x - sx) > 3 || Math.abs(y - sy) > 3) moved = true
    marquee.value = { x: Math.min(sx, x), y: Math.min(sy, y),
                      w: Math.abs(x - sx), h: Math.abs(y - sy) }
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    const m = marquee.value
    marquee.value = null
    if (!moved || !m) { clearSel(); return }
    const hits = blocks.value.filter((b) => {
      const h = blockH(b)
      return b.x < m.x + m.w && b.x + (b.w || 0) > m.x
        && b.y < m.y + m.h && b.y + h > m.y
    })
    if (hits.length) selectBlocks(hits)
    else clearSel()
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
const marqueeStyle = computed(() => {
  const m = marquee.value
  if (!m) return {}
  return {
    left: m.x * scale.value + 'px', top: m.y * scale.value + 'px',
    width: m.w * scale.value + 'px', height: m.h * scale.value + 'px',
  }
})

function groupSel() {
  if (selIds.value.length < 2) return
  const gid = 'g' + Date.now().toString(36)
  for (const b of blocks.value) if (isSel(b.id)) b.gid = gid
  emit('changed')
}
function ungroupSel() {
  for (const b of blocks.value) if (isSel(b.id) && b.gid) b.gid = null
  emit('changed')
}
function copySel() {
  const list = blocks.value.filter((b) => isSel(b.id))
  if (list.length) clip.value = list.map((b) => ({ ...b }))
}
function pasteClip() {
  if (!clip.value.length) return
  const gmap = {}
  const made = []
  for (const src of clip.value) {
    const b = reactive({ ...src, id: uid(), x: Math.min(src.x + 16, PW - 40), y: Math.min(src.y + 16, limit.value - 40) })
    if (src.gid) { gmap[src.gid] = gmap[src.gid] || 'g' + uid(); b.gid = gmap[src.gid] }
    blocks.value.push(b)
    made.push(b)
  }
  selectBlocks(made)
  emit('changed')
}
function removeSelected() {
  if (!selIds.value.length) return
  const ids = new Set(selIds.value)
  blocks.value = blocks.value.filter((b) => !ids.has(b.id))
  clearSel()
  emit('changed')
}
function removeBlock(id) {
  blocks.value = blocks.value.filter((b) => b.id !== id)
  if (isSel(id)) { selIds.value = selIds.value.filter((x) => x !== id); selectedId.value = selIds.value[0] || '' }
  emit('changed')
}
function bringTop(id) {
  const b = blocks.value.find((x) => x.id === id)
  if (!b) return
  blocks.value = [...blocks.value.filter((x) => x.id !== id), b]
}
function rotateSel() {
  const target = selected.value && selected.value.type === 'image'
    ? selected.value
    : blocks.value.find((b) => b.type === 'image' && selIds.value.includes(b.id))
  if (!target) return
  target.rotate = ((target.rotate || 0) + 90) % 360
  if (target.rotate % 180 === 90) { const w = target.w; target.w = target.h; target.h = w }
  emit('changed')
}
function bumpSize(delta) {
  if (applyCharSize(delta)) return
  const ids = selIds.value.length ? selIds.value : (selectedId.value ? [selectedId.value] : [])
  const targets = blocks.value.filter((b) => ids.includes(b.id))
  if (!targets.length) {
    tip.value = '先选中文字或图片，再点 A＋ / A－ 调整字号大小'
    return
  }
  let n = 0
  for (const b of targets) {
    if (b.type === 'text') {
      b.size = clamp(Math.round((b.size || 13) + delta), 6, 48)
      n++
    } else if (b.type === 'image') {
      const k = delta > 0 ? 1.08 : 1 / 1.08
      const maxW = Math.max(60, PW - (b.x || MARGIN))
      const w = Math.round(clamp((b.w || 200) * k, 60, maxW))
      const ratio = (b.h || 1) / Math.max(1, b.w || 1)
      b.w = w
      b.h = Math.max(16, Math.round(w * ratio))
      n++
    }
  }
  if (n) {
    tip.value = delta > 0
      ? `已放大 ${n} 个元素${targets.some((b) => b.type === 'image') ? '（图片按比例放大）' : ''}`
      : `已缩小 ${n} 个元素`
    emit('changed')
  } else {
    tip.value = '选中的元素不支持调整字号'
  }
}
function toggleBold() { const b = selectedText.value; if (b) b.bold = !b.bold }
function setAlign(a) { const b = selectedText.value; if (b) b.align = a }
function nudge(dx, dy) {
  const ids = selIds.value.length ? selIds.value : (selectedId.value ? [selectedId.value] : [])
  for (const id of ids) {
    const b = blocks.value.find((x) => x.id === id)
    if (!b) continue
    b.x = clamp(Math.round(b.x + dx), 0, PW - (b.w || 100))
    b.y = clamp(Math.round(b.y + dy), 0, limit.value - 40)
  }
  emit('changed')
}

function nextY() {
  const bottom = blocks.value.reduce((m, b) => Math.max(m, b.y + blockH(b)), 40)
  return Math.min(limit.value - 60, Math.round(bottom + 10))
}

function addText(text = '') {
  const b = reactive({ id: uid(), type: 'text', text: flowText(text || ''), x: MARGIN, y: nextY(), w: PW - MARGIN * 2, size: 13, align: 'justify', bold: false, color: textColor.value })
  blocks.value.push(b)
  selectOnly(b.id)
  if (!text) nextTick(() => { editingId.value = b.id })
  emit('changed')
  return b
}

function estimateTextH(text, w, size) {
  const perLine = Math.max(6, Math.floor(Math.max(80, w) / (Math.max(6, size) * 0.62)))
  let lines = 0
  for (const seg of String(text || '').split('\n')) lines += Math.max(1, Math.ceil(seg.length / perLine))
  return Math.ceil(lines * Math.max(6, size) * 1.55)
}

// 翻译/摘录导入时按顺序往下接：放得下就新建一段，放不下就接到最后一段，
// 避免多个导入块都堆在页面底部互相盖住。
function addTextFlow(text = '') {
  const t = String(text || '').trim()
  if (!t) return null
  const texts = blocks.value.filter((b) => b.type === 'text')
  const last = texts.slice().sort((a, b) => (a.y + blockH(a)) - (b.y + blockH(b))).pop()
  if (last && (last.text || '').trim()) {
    const bottom = last.y + blockH(last)
    const need = estimateTextH(t, last.w, last.size || 13)
    if (bottom + need > limit.value - 36) {
      last.text = `${last.text.replace(/\s+$/, '')}\n\n${t}`
      selectOnly(last.id)
      tip.value = '已按顺序接到上一段后面（页面底部空间不足）'
      emit('changed')
      return last
    }
  }
  return addText(t)
}

// 把已有文字块里的「硬换行」整理成整段：一段排满再换行，中文之间不插空格。
function reflowSelected() {
  const ids = selIds.value.length ? selIds.value : (selectedId.value ? [selectedId.value] : [])
  const targets = blocks.value.filter((b) => b.type === 'text' && (!ids.length || ids.includes(b.id)))
  let n = 0
  for (const b of targets) {
    const next = flowText(b.text || '')
    if (next !== b.text) { b.text = next; n += 1 }
    if (b.align !== 'justify' && b.align !== 'center' && b.align !== 'right') b.align = 'justify'
  }
  tip.value = n ? `已连句 ${n} 段：按整段重新排版` : '选中的文字已经是整段，不用再连句'
  emit('changed')
}

function imageSize(path) {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve({ w: img.naturalWidth || 600, h: img.naturalHeight || 400 })
    img.onerror = () => resolve({ w: 600, h: 400 })
    img.src = imgUrl(path)
  })
}

async function addImage(path, atX, atY, fullWidth = false, opts = {}) {
  const pxPerPt = opts.pxPerPt || imgPxPerPt.value || 3
  const target = opts.targetFont || fontTarget.value || 12

  // 先裁掉白边（pad 取 2px，让墨迹几乎贴边，方便按文字对齐）
  let p = path
  let natW = 0
  let natH = 0
  let fontPt = 0
  try {
    const t = await api.post('/api/manual/trim', { path, px_per_pt: pxPerPt, pad: 2 })
    p = t.path; natW = t.width; natH = t.height; fontPt = t.font_pt || 0
  } catch (e) {
    const nat = await imageSize(path)
    natW = nat.w; natH = nat.h
  }
  if (!natW || !natH) { const nat = await imageSize(p); natW = nat.w; natH = nat.h }

  const ratio = natH / Math.max(1, natW)
  // 默认按 1:1 原始字号插入（同一份 PDF 裁出来的图字号天然一致、清晰不失真）
  // 只有显式传 targetFont 时才按测得字号缩放
  const k = (opts.targetFont && fontPt > 0) ? target / fontPt : 1
  let w = (natW / pxPerPt) * k
  w = Math.round(clamp(w, 120, PW - MARGIN * 2))
  const h = Math.round(w * ratio)
  const b = reactive({ id: uid(), type: 'image', path: p, x: MARGIN, y: 0, w, h,
                       pxPerPt, fontPt })
  b.y = atY != null ? clamp(Math.round(atY - h / 2), 0, limit.value - h - 10) : Math.min(limit.value - h - 20, nextY())
  if (atX != null) {
    const cx = clamp(atX - w / 2, 0, Math.max(0, PW - w))
    if (autoAlign.value) {
      const cands = [MARGIN, Math.round((PW - w) / 2), Math.round(PW - MARGIN - w)]
      b.x = cands.reduce((a, c) => (Math.abs(c - cx) < Math.abs(a - cx) ? c : a), cands[0])
    } else b.x = Math.round(cx)
  }
  blocks.value.push(b)
  selectOnly(b.id)
  emit('changed')
  return b
}


async function unifyFonts() {
  const imgs = blocks.value.filter((b) => b.type === 'image')
  if (!imgs.length) return
  for (const b of imgs) {
    try {
      const t = await api.post('/api/manual/trim', {
        path: b.path, px_per_pt: b.pxPerPt || imgPxPerPt.value || 3, pad: 2,
      })
      if (t.path && t.path !== b.path) b.path = t.path
      const fp = t.font_pt || 0
      if (!fp) continue
      const k = fontTarget.value / fp
      b.w = Math.round(clamp(b.w * k, 120, PW - MARGIN * 2))
      b.h = Math.round(b.w * ((b.h || 1) / Math.max(1, b.w / k)))
      b.fontPt = fp
    } catch (e) { /* 忽略单块失败 */ }
  }
  // 按文字左边缘对齐：裁过白边后块左边即文字左边
  imgs.sort((a, b2) => a.y - b2.y)
  for (const b of imgs) b.x = MARGIN
  emit('changed')
  tip.value = `已把 ${imgs.length} 张图统一到 ${fontTarget.value}pt 并左对齐`
}

function applyColor() {
  if (applyCharColor(textColor.value)) return
  const targets = blocks.value.filter((b) => b.type === 'text' && isSel(b.id))
  if (!targets.length) {
    tip.value = `已把新建文字的颜色设为 ${textColor.value}；选中文字块后再点「上色」可改已有文字`
    return
  }
  for (const b of targets) b.color = textColor.value
  tip.value = `已把 ${targets.length} 个文字块设成 ${textColor.value}`
  emit('changed')
}

// ---------- 画布内翻译 ----------
function openTranslate() {
  let text = ''
  if (charSel.value && charSel.value.text) text = charSel.value.text
  else if (selectedText.value) text = selectedText.value.text
  text = String(text || '').trim()
  if (!text) {
    tip.value = '先在文字里拖选要翻译的一个词或一句话，再点「翻译选中」'
    return
  }
  tr.open = true
  tr.text = text
  tr.trans = ''
  tr.err = ''
  tr.msg = ''
}
async function doTranslate() {
  if (!tr.text.trim()) return
  tr.busy = true
  tr.err = ''
  tr.msg = ''
  try {
    const r = await api.post('/api/ai/translate', { text: tr.text, target: tr.target })
    tr.trans = r.text || ''
    if (!tr.trans) tr.msg = '模型没有返回译文，可以重试'
  } catch (e) {
    tr.err = e?.message || String(e)
  } finally { tr.busy = false }
}
function replaceRangeText(b, start, end, newText) {
  ensureRuns(b)
  splitRunsAt(b, start)
  splitRunsAt(b, end)
  const out = []
  let pos = 0
  let inserted = false
  for (const r of b.runs) {
    const len = String(r.t || '').length
    if (!inserted && pos >= start) { out.push({ t: newText }); inserted = true }
    if (!(pos >= start && pos + len <= end)) out.push({ ...r })
    pos += len
  }
  if (!inserted) out.push({ t: newText })
  b.runs = out
  normalizeRuns(b)
  charSel.value = { id: b.id, start, end: start + newText.length, text: newText }
}
function replaceTrans() {
  if (!tr.trans.trim()) return
  const cs = charSel.value
  if (!cs) { tr.msg = '选区已经失效：请重新选中文字，或点「插入新文字块」'; return }
  const b = blocks.value.find((x) => x.id === cs.id)
  if (!b || b.type !== 'text') { tr.msg = '找不到原文字块，请重新选中'; return }
  replaceRangeText(b, cs.start, cs.end, tr.trans.trim())
  tr.msg = '已替换选中的文字，可继续改字号/颜色'
  emit('changed')
}
function insertTrans() {
  if (!tr.trans.trim()) return
  const b = addTextFlow(tr.trans.trim())
  tr.msg = b ? '已按顺序插入画布' : '译文是空的'
  emit('changed')
}
async function copyTrans() {
  try {
    await navigator.clipboard.writeText(tr.trans || '')
    tr.msg = '译文已复制'
  } catch (e) { tr.msg = '浏览器不允许直接复制，请在译文框里全选复制' }
}

// ---------- 拖动 / 缩放 ----------
let drag = null
function startDrag(e, b) {
  emit('activate')
  if (editingId.value === b.id) return
  if (b.type === 'image' && b.locked) { selectOnly(b.id); tip.value = '图片已固定，先点「解锁图片」才能拖动'; return }
  charSel.value = null
  if (e.shiftKey) { toggleSel(b); return }
  if (!isSel(b.id)) selectGroupOf(b)
  else if (b.gid && !e.altKey) selectGroupOf(b)
  const moving = blocks.value.filter((x) => isSel(x.id)).map((x) => ({ id: x.id, x: x.x, y: x.y }))
  drag = { ids: moving.length ? moving : [{ id: b.id, x: b.x, y: b.y }], sx: e.clientX, sy: e.clientY, kind: 'move' }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', endDrag)
}
function onBlkDown(e, b) {
  emit('activate')
  if (e.button !== 0) return
  const t = e.target
  if (t && t.closest && (t.closest('.grip') || t.closest('.handle'))) return
  if (t && t.closest && t.closest('.tt')) {
    // 点在文字上：交给浏览器做原生选区（可只选一个字），不启动拖动
    if (!isSel(b.id)) { selectedId.value = b.id; selIds.value = [b.id] }
    return
  }
  startDrag(e, b)
}
function startResize(e, b) {
  emit('activate')
  if (b.type === 'image' && b.locked) { tip.value = '图片已固定，先点「解锁图片」才能缩放'; return }
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
      t.y = clamp(Math.round(it.y + dy), 0, Math.max(0, limit.value - (t.h || 40)))
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
  emit('changed')
}
function endDrag() {
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', endDrag)
  drag = null
}

// ---------- 缩放适配 ----------
const canvasEl = ref(null)
function fitScale() {
  const host = canvasEl.value ? canvasEl.value.parentElement : null
  const w = host ? host.clientWidth : 760
  scale.value = clamp((w - 48) / PW, 0.35, 1.15)
}
let ro = null
onMounted(() => { fitScale(); ro = new ResizeObserver(fitScale); if (canvasEl.value?.parentElement) ro.observe(canvasEl.value.parentElement) })
onBeforeUnmount(() => { if (ro) ro.disconnect() })

// ---------- 拖放（来自左栏裁剪） ----------
function dropOnCanvas(e) {
  if (e.dataTransfer.files && e.dataTransfer.files.length) return
  const p = e.dataTransfer.getData('text/plain')
  if (!p) return
  const host = e.currentTarget.getBoundingClientRect()
  addImage(p, (e.clientX - host.left) / scale.value, (e.clientY - host.top) / scale.value)
}

// ---------- 对外接口 ----------
function getLayout() {
  return {
    width: PW,
    height: Math.round(pageHeight.value),
    blocks: blocks.value.map((b) => (b.type === 'image'
      ? { type: 'image', path: b.path, x: b.x, y: b.y, w: b.w, h: b.h,
          rotate: b.rotate || 0, ...(b.locked ? { locked: true } : {}) }
      : { type: 'text', text: b.text, x: b.x, y: b.y, w: b.w, size: b.size,
          align: b.align, bold: b.bold, color: b.color || '',
          ...(hasRuns(b) ? { runs: b.runs.map((r) => ({ t: r.t, ...(r.c ? { c: r.c } : {}), ...(r.s ? { s: r.s } : {}) })) } : {}) })),
    meta: props.metaable && hasMeta.value
      ? { header: { left: band.header_left, center: band.header_center, right: band.header_right },
          footer: { center: band.footer_center }, name_line: band.name_line }
      : {},
    trim: trimBlank.value,
    trim_pad: 24,
  }
}
function setLayout(layout) {
  const l = layout || {}
  blocks.value = (l.blocks || []).map((b) => reactive({ ...b, id: uid() }))
  const m = l.meta || {}
  const h = m.header || {}
  band.header_left = h.left || ''
  band.header_center = h.center || ''
  band.header_right = h.right || ''
  band.footer_center = (m.footer || {}).center || ''
  band.name_line = !!m.name_line
  clearSel()
  nextTick(fitScale)
}
function clear() { blocks.value = []; clearSel(); clip.value = [] }

defineExpose({ getLayout, setLayout, addImage, clear, rotateSel, copySel, pasteClip, groupSel, ungroupSel, removeSelected, clearSel, nudge, addText, addTextFlow, reflowSelected, unifyFonts, selectAll, bumpSize, imageToText })
</script>

<template>
  <section class="cvpane" :class="{ collapsed }">
    <header class="cvhead">
      <b>{{ title }}</b>
      <span class="muted">{{ blocks.length }} 个元素 · {{ Math.round(pageHeight) }}pt</span>
      <span class="spacer"></span>
      <label class="chk" v-if="!collapsed"><input type="checkbox" v-model="autoAlign" /> 自动对齐</label>
      <label class="chk" v-if="!collapsed" title="保存时按内容裁掉下方空白"><input type="checkbox" v-model="trimBlank" /> 裁空白</label>
      <span class="muted" v-if="!collapsed" style="margin-left:8px">字号</span>
      <input v-if="!collapsed" v-model.number="fontTarget" type="number" min="6" max="24" step="0.5"
             style="width:56px;padding:3px 6px" title="统一到多少 pt" />
      <span class="muted" v-if="!collapsed" style="margin-left:8px">颜色</span>
      <input v-if="!collapsed" v-model="textColor" type="color" class="colorin" title="文字颜色" @change="applyColor" />
      <button class="ghost sm" v-if="!collapsed" @click="applyColor" title="给选中的文字块上色；未选中时选的颜色用于新插入的文字">上色</button>
      <button class="ghost sm" v-if="!collapsed" @click="unifyFonts" title="按测得字号统一并左对齐">统一字号</button>
      <button class="icon" :title="collapsed ? '展开' : '收起'" @click="collapsed = !collapsed">{{ collapsed ? '▾' : '▴' }}</button>
    </header>
    <div v-if="tip && !collapsed" class="cvhint">{{ tip }}</div>

    <div v-show="!collapsed" class="cvbody" @pointerdown="emit('activate')">
      <div class="toolbar">
        <div class="tgroup">
          <span class="lbl">字号</span>
          <button class="icon" title="缩小选中的文字 / 图片" @click="bumpSize(-1)">A－</button>
          <b class="size">{{ selectedText ? selectedText.size : (selected && selected.type === 'image' ? '图' : 13) }}</b>
          <button class="icon" title="放大选中的文字 / 图片" @click="bumpSize(1)">A＋</button>
        </div>
        <span class="tdiv"></span>
        <div class="tgroup">
          <button class="icon" :class="{ on: selectedText && selectedText.bold }" @click="toggleBold"><b>B</b></button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'left' }" @click="setAlign('left')">⇤</button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'center' }" @click="setAlign('center')">↔</button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'right' }" @click="setAlign('right')">⇥</button>
        </div>
        <span class="tdiv"></span>
        <button class="ghost trbtn" title="翻译选中的字/词/句（DeepSeek）" @click="openTranslate">翻译选中</button>
        <button class="ghost" title="把所有文字块的首字左边缘对齐" @click="alignTextAll">对齐</button>
        <button class="ghost" title="把选中的文字（单字/整段）统一为当前字号和颜色" @click="unifySelected">统一</button>
        <button class="ghost" :disabled="!blocks.some((b) => b.type === 'text')" title="把硬换行连成整段：一段排满再换行" @click="reflowSelected">连句</button>
        <button v-if="selected && selected.type === 'image'" class="ghost" :class="{ on: selected.locked }"
                :title="selected.locked ? '解锁后可以拖动、缩放图片' : '固定图片，避免选中文字时误拖动'"
                @click="toggleLock">{{ selected.locked ? '解锁图片' : '固定图片' }}</button>
        <button v-if="selected && selected.type === 'image'" class="ghost trbtn"
                title="用本机 OCR 把这张图识别成可编辑文字（会替换图片）" @click="imageToText">图片转文字</button>
        <button class="ghost" @click="addText('')">＋ 文本</button>
        <button v-if="metaable" class="ghost" :class="{ on: metaOpen }" @click="metaOpen = !metaOpen">页眉页脚</button>
        <button class="ghost" :disabled="!selected" @click="rotateSel">旋转 90°</button>
        <button class="ghost" :disabled="!selIds.length" @click="copySel">复制</button>
        <button class="ghost" :disabled="!clip.length" @click="pasteClip">粘贴</button>
        <button class="ghost" :disabled="selIds.length < 2" @click="groupSel">组合</button>
        <button class="ghost" :disabled="!selIds.length" @click="ungroupSel">拆分</button>
        <button class="ghost" :disabled="!selIds.length" @click="removeSelected">
          删除所选{{ selIds.length > 1 ? `(${selIds.length})` : '' }}
        </button>
      </div>

      <div v-if="tr.open" class="trbox">
        <div class="trhead">
          <b>AI 翻译</b>
          <span class="muted">先选字 → 再翻译</span>
          <span class="spacer"></span>
          <label class="chk">译为
            <select v-model="tr.target" class="trtarget">
              <option>简体中文</option>
              <option>English</option>
            </select>
          </label>
          <button class="icon" title="关闭" @click="tr.open = false">✕</button>
        </div>
        <textarea v-model="tr.text" rows="3" placeholder="要翻译的文字（可以在画布里选中一个字/词/句）"></textarea>
        <div class="trrow">
          <button class="primary" :disabled="tr.busy || !tr.text.trim()" @click="doTranslate">
            {{ tr.busy ? '翻译中…' : '翻译' }}
          </button>
          <button :disabled="!tr.trans" @click="replaceTrans">替换选中文字</button>
          <button :disabled="!tr.trans" @click="insertTrans">插入新文字块</button>
          <button :disabled="!tr.trans" @click="copyTrans">复制译文</button>
        </div>
        <textarea v-if="tr.trans" v-model="tr.trans" rows="4" placeholder="译文"></textarea>
        <div v-if="tr.msg" class="trmsg">{{ tr.msg }}</div>
        <div v-if="tr.err" class="trerr">{{ tr.err }}</div>
      </div>

      <div v-if="metaable && metaOpen" class="metabox">
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

      <div class="canvas-wrap">
        <div ref="canvasEl" class="page"
             :style="{ width: PW * scale + 'px', height: pageHeight * scale + 'px' }"
             @dragover.prevent @drop.prevent="dropOnCanvas" @pointerdown.self="pageDown">
          <div v-if="marquee" class="marquee" :style="marqueeStyle"></div>
          <div v-if="!blocks.length" class="empty">{{ hint }}</div>
          <div v-for="b in blocks" :key="b.id" class="blk"
               :class="{ sel: isSel(b.id), img: b.type === 'image', grouped: !!b.gid, locked: b.locked }"
               :data-gid="b.gid || ''"
               :data-bid="b.id"
               :style="{ left: b.x * scale + 'px', top: b.y * scale + 'px', width: b.w * scale + 'px',
                         height: b.type === 'image' ? (b.h || 0) * scale + 'px' : 'auto' }"
               @pointerdown="onBlkDown($event, b)"
               @dblclick="b.type === 'text' ? (editingId = b.id) : null">
            <span v-if="isSel(b.id) && editingId !== b.id && !b.locked" class="grip" title="拖动"
                  @pointerdown.stop="startDrag($event, b)">⠿</span>
            <span v-if="b.type === 'image' && b.locked" class="lockbadge" title="图片已固定">🔒</span>
            <template v-if="b.type === 'image'">
              <img :src="imgUrl(b.path)" draggable="false" @dragstart.prevent />
              <span v-if="!b.locked" class="handle" @pointerdown.stop="startResize($event, b)"></span>
            </template>
            <template v-else>
              <textarea v-if="editingId === b.id" v-model="b.text" class="etext" autofocus
                        :style="{ fontSize: b.size * scale + 'px', textAlign: b.align, fontWeight: b.bold ? 700 : 400, color: b.color || undefined }"
                        @pointerdown.stop @input="onTextInput(b)" @blur="editingId = ''"></textarea>
              <div v-else class="tt"
                   :style="{ fontSize: b.size * scale + 'px', textAlign: b.align, fontWeight: b.bold ? 700 : 400, color: b.color || undefined }"
                   v-html="textHtml(b)" @mouseup="onTextMouseUp(b)"></div>
              <span class="handle text" @pointerdown.stop="startResize($event, b)"></span>
            </template>
          </div>
          <button v-for="b in lockedImages" :key="'pin' + b.id" class="unlockpin" :style="pinStyle(b)"
                  :title="'图片已固定：点这里重新选中图片，再点「解锁图片」'"
                  @pointerdown.stop @click.stop="selectLocked(b)">🔒</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ghost.sm { padding: 3px 9px; font-size: 12.5px; }
.colorin {
  width: 30px; height: 24px; padding: 0; border: 1px solid var(--line); border-radius: 6px; background: #fff;
}

.cvhint {
  padding: 4px 12px 6px; font-size: 12.5px; color: #3b7a57; background: #f2faf5;
}

.cvpane { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; margin-bottom: 12px; }
.cvhead { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid #eef1f6; }
.cvhead b { font-size: 14px; }
.cvpane.collapsed .cvhead { border-bottom: none; }
.cvbody { padding: 10px 12px 12px; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding-bottom: 8px; }
.tgroup { display: flex; align-items: center; gap: 4px; }
.tgroup .lbl { font-size: 12px; color: #667085; }
.tgroup .size { min-width: 22px; text-align: center; font-size: 12.5px; }
.tdiv { width: 1px; height: 18px; background: #e7ebf2; }
.icon { padding: 4px 8px; border: 1px solid #dfe5ee; background: #fff; border-radius: 7px; cursor: pointer; font-size: 12.5px; }
.icon.on { background: #eef4ff; border-color: #b9cdf7; color: var(--brand, #2f6fed); }
.spacer { flex: 1; }
.chk { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: #475467; }
.muted { font-size: 12px; color: #8b95a5; }
.metabox { background: #f8fafc; border: 1px solid var(--line, #e7ebf2); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
.bandrow { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 6px; }
.bandrow input { flex: 1 1 180px; min-width: 140px; }
.canvas-wrap { overscroll-behavior: contain; background: #f1f3f7; border-radius: 10px; padding: 14px; display: flex; justify-content: center; max-height: calc(100vh - 260px); overflow: auto; }
.page { position: relative; background: #fff; box-shadow: 0 6px 24px rgba(16, 24, 40, .1); flex: 0 0 auto; }
.page .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #98a2b3; font-size: 13px; text-align: center; padding: 0 40px; }
.marquee { position: absolute; border: 1px dashed var(--brand, #2f6fed); background: rgba(47, 111, 237, .1); pointer-events: none; z-index: 5; }
.blk { position: absolute; cursor: move; }
.blk.sel { outline: 1px dashed var(--brand, #2f6fed); outline-offset: 1px; }
.blk.grouped { outline: 1px dashed rgba(47, 111, 237, .45); outline-offset: 3px; }
.blk.img img { display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; }
.blk .tt { white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; line-height: 1.55; color: #101828; min-height: 18px; user-select: text; cursor: text; }
.blk :deep(.ph) { color: #b6bdc9; }
.blk .etext { width: 100%; border: 1px solid var(--brand, #2f6fed); border-radius: 4px; padding: 2px 4px; line-height: 1.55; resize: vertical; font-family: inherit; }
.handle { position: absolute; right: -5px; bottom: -5px; width: 10px; height: 10px; background: #fff; border: 1px solid var(--brand, #2f6fed); border-radius: 2px; cursor: nwse-resize; }
.handle.text { bottom: -6px; }
.grip {
  position: absolute; left: -18px; top: -13px; width: 18px; height: 18px;
  display: flex; align-items: center; justify-content: center;
  background: #fff; border: 1px solid #cbd5e1; border-radius: 4px;
  font-size: 11px; color: #667085; cursor: grab; z-index: 6;
}
.lockbadge {
  position: absolute; right: -9px; top: -11px; font-size: 11px; line-height: 1;
  background: #fff; border: 1px solid #e5e7eb; border-radius: 4px; padding: 2px; z-index: 6;
}
.blk.locked { cursor: default; }
.blk.locked.img { pointer-events: none; }
.unlockpin {
  position: absolute; z-index: 8; width: 22px; height: 20px; padding: 0; line-height: 1;
  font-size: 11px; border: 1px solid #d6dfec; border-radius: 5px; background: #fff;
  box-shadow: 0 2px 6px rgba(16, 24, 40, .12); cursor: pointer;
}
.unlockpin:hover { border-color: var(--brand, #2f6fed); background: #eef4ff; }
.ghost.trbtn { border-color: #b9cdf7; background: #eef4ff; color: var(--brand, #2f6fed); font-weight: 600; }
.trbox { background: #f8fafc; border: 1px solid #dbe4f3; border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; }
.trhead { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.trbox textarea { width: 100%; margin: 6px 0; font-size: 13px; box-sizing: border-box; }
.trrow { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.trtarget { width: auto; padding: 3px 6px; }
.trmsg { font-size: 12px; color: #3b7a57; margin-top: 5px; }
.trerr { font-size: 12px; color: var(--bad, #dc2626); margin-top: 5px; }
</style>
