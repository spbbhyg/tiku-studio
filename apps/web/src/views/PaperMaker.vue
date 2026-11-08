<script setup>
/* 人工整卷：题目树编辑 + A4 实时预览 + 文献裁图 + 双份 PDF 导出
   数据落在 papers / paper_questions（question_id = NULL，config.kind = 'manual'），
   题目树存 paper_questions.snapshot；排版与导出全在 services/exam_render.py。 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import PaperExport from '../components/PaperExport.vue'
import CropDialog from '../components/CropDialog.vue'
import FigureEdit from '../components/FigureEdit.vue'
import ChemDock from '../components/ChemDock.vue'

const route = useRoute()
const router = useRouter()

const uid = () => Math.random().toString(36).slice(2, 9)
const clone = (x) => JSON.parse(JSON.stringify(x))
const fmt = (n) => {
  const v = Number(n || 0)
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

function blankSub(score = 2) {
  return { id: uid(), stem: '', stem2: '', score, answer: '', points: [], images: [], answer_images: [], subs: [], source: null }
}
function blankItem(score = 10) {
  return { id: uid(), title: '', score, intro: '', images: [], tables: [], answer: '', answer_images: [], subs: [blankSub()], source: null }
}
function emptyDoc() {
  return {
    id: 0, title: '', subtitle: '', duration: 120, total_score: 0, status: 'draft',
    template: {}, items: [blankItem(14)],
  }
}

function normTpl(t) {
  const d = {
    margin: { left: 90, right: 80, top: 72.5, bottom: 82 },
    size: 10.45, lineHeight: 15.6, subSize: 6.85,
    numbering: { depth: 4, style: 'arabic' },
    scoreFormat: '第{n} 题{title}（{score} 分，占{percent}%）',
    heading: { gapBefore: 15.6, size: 10.45 },
    figure: { maxWidth: 415, captionSize: 6.8, captionColor: '#7030A0', autoNumber: true },
    table: { border: 0.5, size: 10.45 },
    answer: { color: '#FF0000', scoreColor: '#0000FF' },
    title: { size: 10.45, bold: true },
  }
  const src = t || {}
  const out = { ...d, ...src }
  for (const k of ['margin', 'numbering', 'heading', 'figure', 'table', 'answer', 'title']) {
    out[k] = { ...d[k], ...(src[k] || {}) }
  }
  return out
}
function fixDoc() {
  doc.template = normTpl(doc.template)
  if (!Array.isArray(doc.items)) doc.items = []
  // 新卷别给空白页：先摆一个大题，老师直接能打字
  if (!doc.items.length) doc.items.push(blankItem(14))
  const fixSub = (s) => {
    s.images = s.images || []
    s.answer_images = s.answer_images || []
    s.subs = s.subs || []
    if (s.score == null) s.score = 2
    s.subs.forEach(fixSub)
  }
  doc.items.forEach((it) => {
    it.images = it.images || []
    it.tables = it.tables || []
    it.subs = it.subs || []
    if (it.score == null) it.score = 10
    it.subs.forEach(fixSub)
  })
}

const doc = reactive(emptyDoc())
const paperId = ref(0)
const sel = reactive({ kind: 'item', item: 0, path: [] })
const issues = ref([])
const part = ref('questions')
const zoom = ref(1)
const saveState = ref('')
const loading = ref(true)
const previewUrl = ref('')
const previewBusy = ref(false)
const showTpl = ref(false)
const cropState = reactive({ open: false, target: null })

const history = []
let hIndex = -1
let saveTimer = 0
let previewTimer = 0
let histTimer = 0

/* ------------------------------------------------------------ 节点定位 */
function walk(list, path) {
  let arr = list
  let node = null
  for (const i of path) {
    node = arr[i]
    if (!node) return null
    arr = node.subs || (node.subs = [])
  }
  return node
}

const cur = computed(() => {
  if (sel.kind === 'paper') return null
  if (sel.kind === 'item') return doc.items[sel.item] || null
  return walk(doc.items[sel.item]?.subs || [], sel.path)
})

const curKind = computed(() => sel.kind)

/* 选中节点的父数组（删除 / 移动 / 插图都靠它） */
function container(node) {
  if (!node) return null
  if (sel.kind === 'item') return { arr: doc.items, idx: sel.item, kind: 'item' }
  const path = sel.path
  if (!path.length) return { arr: doc.items[sel.item].subs, idx: 0, kind: 'sub' }
  const parentPath = path.slice(0, -1)
  const parent = parentPath.length ? walk(doc.items[sel.item].subs, parentPath) : doc.items[sel.item]
  const arr = parentPath.length ? (parent.subs || (parent.subs = [])) : (doc.items[sel.item].subs || (doc.items[sel.item].subs = []))
  return { arr, idx: path[path.length - 1], kind: 'sub' }
}

/* ------------------------------------------------------------ 编号 */
function subNo(itemIdx, path) {
  const bits = [String(itemIdx + 1)]
  for (const i of path) bits.push(String(i + 1))
  return bits.join('-')
}

const tree = computed(() => {
  const out = []
  doc.items.forEach((item, i) => {
    out.push({ key: `i${i}`, kind: 'item', level: 0, no: String(i + 1), label: item.title || '未命名大题', item: i, path: [] })
    const walkSubs = (arr, path, level) => {
      (arr || []).forEach((s, j) => {
        const p = [...path, j]
        out.push({
          key: `s${i}-${p.join('_')}`, kind: 'sub', level, no: subNo(i, p),
          label: (s.stem || '未填题干').slice(0, 40), item: i, path: p,
        })
        walkSubs(s.subs, p, level + 1)
      })
    }
    walkSubs(item.subs, [], 1)
  })
  return out
})

/* ------------------------------------------------------------ 分值统计 */
function leafSum(subs) {
  let t = 0
  for (const s of subs || []) {
    if ((s.subs || []).length) t += leafSum(s.subs)
    else t += Number(s.score || 0)
  }
  return t
}
const stats = computed(() => {
  let used = 0
  let subCount = 0
  for (const it of doc.items) {
    used += leafSum(it.subs)
    const count = (arr) => (arr || []).reduce((a, s) => a + 1 + count(s.subs), 0)
    subCount += count(it.subs)
  }
  // 总分 = 各大题分值之和；已用 = 小问分值之和。两者不等就是「子问分值和 ≠ 父题」，标红
  const total = Math.round(doc.items.reduce((a, it) => a + (Number(it.score) || 0), 0) * 10) / 10
  return { total, used: Math.round(used * 10) / 10, items: doc.items.length, subs: subCount }
})
const hasIssues = computed(() => issues.value.filter((i) => i.level !== 'info').length)

/* ------------------------------------------------------------ 历史 / 保存 */
function snapshot() {
  clearTimeout(histTimer)
  histTimer = setTimeout(() => {
    const s = JSON.stringify(doc)
    if (history[hIndex] === s) return
    history.splice(hIndex + 1)
    history.push(s)
    if (history.length > 60) history.shift()
    hIndex = history.length - 1
  }, 350)
}
function undo() {
  if (hIndex <= 0) return
  hIndex -= 1
  Object.assign(doc, JSON.parse(history[hIndex]))
  schedulePreview()
  saveLocal()
}
function redo() {
  if (hIndex >= history.length - 1) return
  hIndex += 1
  Object.assign(doc, JSON.parse(history[hIndex]))
  schedulePreview()
  saveLocal()
}

function saveLocal() {
  saveState.value = '待保存'
  try { localStorage.setItem(`tiku:exam:${paperId.value}`, JSON.stringify({ at: Date.now(), doc })) } catch (e) { /* 隐私模式忽略 */ }
  clearTimeout(saveTimer)
  saveTimer = setTimeout(saveNow, 2000)
}

async function saveNow() {
  clearTimeout(saveTimer)
  if (!paperId.value) return
  saveState.value = '保存中'
  try {
    const r = await api.put(`/api/exam/papers/${paperId.value}`, { doc: { ...doc } })
    issues.value = r.issues || []
    saveState.value = '已保存'
    localStorage.removeItem(`tiku:exam:${paperId.value}`)
  } catch (e) {
    saveState.value = '保存失败：' + e.message
  }
}

/* ------------------------------------------------------------ 预览 */
function schedulePreview() {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 900)
}
async function refreshPreview() {
  if (!doc.items.length) { previewUrl.value = ''; return }
  previewBusy.value = true
  try {
    const r = await api.post('/api/exam/preview', { doc: { ...doc }, part: part.value })
    previewUrl.value = r.url + `?p=${part.value}`
    if (r.issues?.length) issues.value = r.issues
  } catch (e) {
    previewUrl.value = ''
  } finally {
    previewBusy.value = false
  }
}

/* ------------------------------------------------------------ 结构操作 */
function select(kind, item, path = []) {
  sel.kind = kind
  sel.item = item
  sel.path = path
}
function addItem() {
  doc.items.push(blankItem(10))
  select('item', doc.items.length - 1)
  snapshot(); schedulePreview(); saveLocal()
}
function addSub() {
  const c = container(cur.value)
  if (!c) return
  if (sel.kind === 'item') {
    const it = doc.items[sel.item]
    it.subs.push(blankSub(2))
    select('sub', sel.item, [it.subs.length - 1])
  } else {
    const node = cur.value
    node.subs = node.subs || []
    node.subs.push(blankSub(2))
    select('sub', sel.item, [...sel.path, node.subs.length - 1])
  }
  snapshot(); schedulePreview(); saveLocal()
}
function addSiblingSub() {
  const c = container(cur.value)
  if (!c) return
  const node = clone(blankSub(2))
  c.arr.splice(c.idx + 1, 0, node)
  if (c.kind === 'sub') {
    const p = [...sel.path]
    p[p.length - 1] = c.idx + 1
    select('sub', sel.item, p)
  }
  snapshot(); schedulePreview(); saveLocal()
}
function delNode() {
  const c = container(cur.value)
  if (!c) return
  if (c.kind === 'item') {
    if (doc.items.length <= 1) return
    doc.items.splice(c.idx, 1)
    select('item', Math.max(0, Math.min(c.idx, doc.items.length - 1)))
  } else {
    c.arr.splice(c.idx, 1)
    const p = [...sel.path]
    p.pop()
    select(p.length ? 'sub' : 'item', sel.item, p)
  }
  snapshot(); schedulePreview(); saveLocal()
}
function moveNode(dir) {
  const c = container(cur.value)
  if (!c) return
  const j = c.idx + dir
  if (j < 0 || j >= c.arr.length) return
  const [x] = c.arr.splice(c.idx, 1)
  c.arr.splice(j, 0, x)
  if (c.kind === 'sub') {
    const p = [...sel.path]
    p[p.length - 1] = j
    select('sub', sel.item, p)
  } else {
    select('item', j)
  }
  snapshot(); schedulePreview(); saveLocal()
}

/* 拖拽排序 */
const dragInfo = ref(null)
function onDragStart(e, row) {
  dragInfo.value = row
  e.dataTransfer.effectAllowed = 'move'
}
function onDrop(e, row) {
  const from = dragInfo.value
  dragInfo.value = null
  if (!from || from.key === row.key) return
  if (from.kind === 'item' && row.kind === 'item') {
    const [x] = doc.items.splice(from.item, 1)
    doc.items.splice(row.item, 0, x)
    select('item', row.item)
  } else if (from.kind === 'sub' && row.kind === 'sub' && from.item === row.item) {
    const parentArr = (path) => {
      if (!path.length) return doc.items[from.item]
      return walk(doc.items[from.item].subs, path.slice(0, -1))
    }
    const a = parentArr(from.path)
    const b = parentArr(row.path)
    if (!a || !b) return
    const aArr = a.subs || a
    const bArr = b.subs || b
    const [x] = aArr.splice(from.path[from.path.length - 1], 1)
    bArr.splice(row.path[row.path.length - 1], 0, x)
    select('sub', row.item, row.path)
  }
  snapshot(); schedulePreview(); saveLocal()
}

/* ------------------------------------------------------------ 图 / 表 */
function imgUrl(p) {
  if (!p) return ''
  if (/^(https?:|data:|\/)/.test(p)) return p
  return '/api/images/' + String(p).replace(/\\/g, '/').replace(/^\/+/, '')
}
function addFigure(node) {
  node.images = node.images || []
  node.images.push({ path: '', caption: '', width: 415, source: null })
  snapshot(); saveLocal()
}
async function insertLocalImage(node, file) {
  const dataUrl = await new Promise((res, rej) => {
    const fr = new FileReader()
    fr.onload = () => res(fr.result)
    fr.onerror = rej
    fr.readAsDataURL(file)
  })
  const r = await api.post('/api/manual/image', { data_url: dataUrl })
  node.images = node.images || []
  node.images.push({ path: r.path, caption: '', width: 415, source: null })
  snapshot(); schedulePreview(); saveLocal()
  return r
}
function onPickFile(e, node) {
  const f = e.target.files?.[0]
  if (f) insertLocalImage(node, f)
  e.target.value = ''
}
function moveFigure(node, i, d) {
  const arr = node.images || []
  const j = i + d
  if (j < 0 || j >= arr.length) return
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
  snapshot(); saveLocal(); schedulePreview()
}
function delFigure(node, i) {
  node.images.splice(i, 1)
  snapshot(); schedulePreview(); saveLocal()
}
function addTable(node) {
  node.tables = node.tables || []
  node.tables.push({ head: ['列 1', '列 2'], rows: [['', '']], caption: '' })
  snapshot(); saveLocal()
}
function tableToText(t) {
  return (t.rows || []).map((r) => r.join(' | ')).join('\n')
}
function textToTable(t, v) {
  t.rows = v.split('\n').filter((l) => l.trim()).map((l) => l.split('|').map((c) => c.trim()))
  snapshot(); schedulePreview(); saveLocal()
}

/* 出处 */
function editSource(node) {
  node.source = node.source || { doc_id: '', page: 1, kind: 'text', title: '', quote: '' }
  snapshot()
}

/* 给分点 */
function addPoint(node) {
  node.points = node.points || []
  node.points.push({ text: '', score: 1 })
  snapshot(); saveLocal()
}
function delPoint(node, i) {
  node.points.splice(i, 1)
  snapshot(); saveLocal()
}
function pointsText(node) {
  return (node.points || []).map((p) => `${p.text || ''}｜${p.score ?? 1}`).join('\n')
}
function setPoints(node, v) {
  node.points = v.split('\n').filter((l) => l.trim()).map((l) => {
    const [text, score] = l.split('｜')
    return { text: (text || '').trim(), score: Number(score || 1) }
  })
  snapshot(); schedulePreview(); saveLocal()
}

/* ------------------------------------------------------------ 键盘 */
function onStemKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault()
    addSiblingSub()
  } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    addSub()
  } else if (e.key === 'Tab') {
    e.preventDefault()
    nextItem()
  }
}
function nextItem() {
  const next = (sel.item + 1) % Math.max(1, doc.items.length)
  select('item', next)
}
const focusText = (el) => el && el.focus()

/* 粘贴：剪贴板里的图直接成图块 */
async function onPaste(e) {
  const items = e.clipboardData?.items || []
  for (const it of items) {
    if (it.type?.startsWith('image/')) {
      const f = it.getAsFile()
      if (f) {
        e.preventDefault()
        if (cur.value) await insertLocalImage(cur.value, f)
        return
      }
    }
  }
}

/* ------------------------------------------------------------ 裁图 */
const ai = reactive({ open: false, busy: false, jobId: '', list: [], err: '' })

function aiSource(node) {
  const src = node?.source || {}
  return {
    doc_id: src.doc_id || '',
    page: src.page || 1,
    kind: 'text',
    title: src.title || src.source_title || doc.title || '',
    quote: (node?.stem || node?.intro || '').slice(0, 120),
  }
}

async function aiDraft(node) {
  const text = String(node?.stem || node?.intro || '').trim()
  if (text.length < 30) {
    ai.err = '题干太短（至少 30 字）——AI 需要一段文献做素材'
    ai.list = []
    ai.open = true
    return
  }
  ai.err = ''
  ai.busy = true
  ai.open = true
  ai.list = []
  try {
    const src = node?.source || {}
    const job = await api.post('/api/ai/smart-questions/jobs', {
      source: text, doc_id: src.doc_id || '', count: 2, difficulty: 4,
    })
    ai.jobId = job.job_id
    for (let i = 0; i < 90; i += 1) {
      await new Promise((r) => setTimeout(r, 2000))
      const r = await api.get(`/api/ai/smart-questions/jobs/${ai.jobId}`)
      if (r.status === 'running') continue
      if (r.status === 'error') throw new Error(r.error || 'AI 出题失败')
      const res = r.result || {}
      const qs = res.questions || res.items || []
      ai.list = qs.map((q) => ({
        stem: String(q.stem || '').trim(),
        answer: String(q.answer || '').trim(),
        score: Number(q.score) || 6,
        q_type: q.q_type || '',
      }))
      break
    }
  } catch (e) {
    ai.err = e.message
  } finally {
    ai.busy = false
  }
}

async function aiAccept(draft, idx) {
  if (!draft?.stem?.trim()) return
  try {
    const r = await api.post('/api/exam/draft/accept', {
      paper_id: doc.id,
      draft: { stem: draft.stem, answer: draft.answer, score: draft.score, q_type: draft.q_type },
      source: aiSource(cur.value),
      mode: 'sub',
      item_id: cur.value?.id || '',
    })
    Object.assign(doc, emptyDoc(), r.doc)
    fixDoc()
    issues.value = r.issues || []
    ai.list.splice(idx, 1)
    await saveNow()
    schedulePreview()
  } catch (e) {
    ai.err = e.message
  }
}

async function openCrop(target) {
  cropState.target = target
  cropState.open = true
}
function closeCrop() {
  cropState.open = false
}

/* 弹窗裁完一张：插到当前节点的图里，或替换正在重裁的那张 */
function onCropDone(fig) {
  const tgt = cropState.target
  if (!tgt) return
  if (tgt.mode === 'recrop' && tgt.figure) {
    Object.assign(tgt.figure, { path: fig.path, source: fig.source })
  } else if (tgt.node) {
    tgt.node.images = tgt.node.images || []
    tgt.node.images.push({ ...fig })
    tgt.index = tgt.node.images.length - 1
  }
  snapshot()
  schedulePreview()
  saveLocal()
}

/* ------------------------------------------------------ 悬浮公式 / 结构式 */
let lastField = null
function onFocusIn(e) {
  const t = e.target
  if (!t || (t.tagName !== 'TEXTAREA' && t.tagName !== 'INPUT')) return
  // 悬浮面板自己的输入框不算「插入目标」
  if (t.closest && t.closest('.dock')) return
  lastField = t
}
function onDockText(text) {
  const el = lastField
  if (el && el.tagName === 'TEXTAREA' && typeof el.selectionStart === 'number') {
    const a = el.selectionStart
    const b = el.selectionEnd
    const v = String(el.value || '')
    el.value = v.slice(0, a) + text + v.slice(b)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.selectionStart = el.selectionEnd = a + text.length
    el.focus()
  } else if (cur.value) {
    // 没聚焦：写进当前节点的题干/说明
    if (cur.value.intro !== undefined) cur.value.intro = (cur.value.intro || '') + text
    else cur.value.stem = (cur.value.stem || '') + text
    snapshot(); schedulePreview(); saveLocal()
  }
}
function onDockImage(path, w = 110) {
  const node = cur.value
  if (!node) { alert('先在左边选一个大题或小问'); return }
  node.images = node.images || []
  node.images.push({ path, caption: '', width: w })
  snapshot(); schedulePreview(); saveLocal()
}

/* ------------------------------------------------------------ 导出 / 校验 */
async function check() {
  const r = await api.post('/api/exam/check', { doc: { ...doc } })
  issues.value = r.issues || []
  return issues.value
}
const exportRt = ref(null)
async function openExport() {
  const list = await check()
  const bad = list.filter((i) => i.level !== 'info')
  if (bad.length && !confirm(`有 ${bad.length} 条校验提示：\n` + bad.map((b) => '· ' + b.msg).join('\n') + '\n\n仍然导出？')) return
  await saveNow()
  exportRt.value && exportRt.value.show()
}
function onExported() {
  // 导出成功 = 出完了：草稿自动转正式卷（后端也这么记，这里同步徽标）
  doc.status = 'done'
  saveState.value = '已出完，已放进试卷列表'
}

/* 完成出卷：不导出也能收工，点了就从草稿变正式卷 */
async function finishPaper() {
  await saveNow()
  try {
    await api.post(`/api/exam/papers/${paperId.value}/finish`)
    doc.status = 'done'
    saveState.value = '已出完'
    router.push('/papers')
  } catch (e) { saveState.value = '完成失败：' + e.message }
}

/* ------------------------------------------------------------ 载入 */
async function load() {
  loading.value = true
  const p = String(route.params.id || '')
  if (p && p !== 'new') {
    paperId.value = Number(p)
    const r = await api.get(`/api/exam/papers/${paperId.value}`)
    Object.assign(doc, emptyDoc(), r.doc)
    issues.value = r.issues || []
    fixDoc()
  } else {
    const seed = sessionStorage.getItem('tiku:exam-seed')
    sessionStorage.removeItem('tiku:exam-seed')
    const r = await api.post('/api/exam/papers', { title: seed ? '文献改编卷' : '未命名整卷' })
    paperId.value = r.id
    Object.assign(doc, emptyDoc(), r.doc)
    fixDoc()
    if (seed) {
      try {
        const s = JSON.parse(seed)
        doc.items[0].intro = s.text || ''
        doc.items[0].source = s.source || null
        doc.items[0].subs[0].stem = '（请在此处写出小问题干）'
      } catch (e) { /* 种子坏了就不管 */ }
    }
    router.replace(`/papers/${paperId.value}/edit`)
  }
  fixDoc()
  loading.value = false
  document.title = doc.title || '人工整卷'
  history.length = 0
  history.push(JSON.stringify(doc))
  hIndex = 0
  if (doc.items.length) { select('item', 0); schedulePreview() }
}

/* 切题号 / 缩放 */
function setPart(p) { part.value = p; refreshPreview() }
function setZoom(z) { zoom.value = z }

onMounted(async () => {
  await load()
})
// 同一个组件切到另一份卷（/papers/12/edit → /papers/13/edit）时要重新载入
watch(() => route.params.id, (n, o) => { if (n && n !== o) load() })
onBeforeUnmount(() => {
  clearTimeout(saveTimer); clearTimeout(previewTimer); clearTimeout(histTimer)
  saveNow()
})
watch(() => sel.key, () => {})

function keysGlobal(e) {
  const meta = e.ctrlKey || e.metaKey
  if (!meta) return
  const k = e.key.toLowerCase()
  if (k === 's') { e.preventDefault(); saveNow(); return }
  if (k === 'z' && cropState.open) return
  if (k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo() }
}
</script>

<template>
  <div class="pm" @keydown="keysGlobal" @focusin="onFocusIn">
    <header class="pmbar">
      <router-link to="/exam" class="back">← 出整卷</router-link>
      <input v-model="doc.title" class="ttl" placeholder="卷名：第 41 届…… 考试卷 3" @input="saveLocal" />
      <span class="stat">
        总分 <b>{{ fmt(stats.total) }}</b> ｜ 已用 <b :class="{ bad: stats.total && stats.used !== stats.total }">{{ fmt(stats.used) }}</b>
        ｜ 题目 <b>{{ stats.items }}</b> ｜ 小问 <b>{{ stats.subs }}</b>
      </span>
      <span v-if="hasIssues" class="issueflag" @click="check">⚠ {{ hasIssues }} 条提示</span>
      <span class="save" :class="{ err: saveState.includes('失败') }">{{ saveState }}</span>
      <span class="chip" :class="doc.status === 'done' ? 'ok' : 'brand'" style="margin-left:4px">
        {{ doc.status === 'done' ? '已出完' : '草稿' }}
      </span>
      <div class="seg">
        <button :class="{ on: part === 'questions' }" @click="setPart('questions')">题目卷</button>
        <button :class="{ on: part === 'answers' }" @click="setPart('answers')">答案卷</button>
      </div>
      <div class="seg">
        <button :class="{ on: zoom === 1 }" @click="setZoom(1)">100%</button>
        <button :class="{ on: zoom === 1.5 }" @click="setZoom(1.5)">150%</button>
      </div>
      <button @click="showTpl = !showTpl">模板</button>
      <button v-if="doc.status !== 'done'" @click="finishPaper">完成出卷</button>
      <button class="primary" @click="openExport">导出 PDF</button>
      <PaperExport ref="exportRt" :paper-id="paperId" :title="doc.title" :cfg="doc.config || {}"
                   @exported="onExported" />
    </header>

    <div v-if="loading" class="loading">正在载入…</div>

    <div v-else class="pmbody">
      <aside class="tree">
        <div class="treehead">
          <b>大纲</b>
          <button class="mini" title="新增大题" @click="addItem">+ 大题</button>
        </div>
        <div class="treebody">
          <div
            v-for="row in tree" :key="row.key"
            class="trow" :class="{ on: sel.kind === row.kind && sel.item === row.item && sel.path.join('/') === row.path.join('/'), item: row.kind === 'item' }"
            :style="{ paddingLeft: 10 + row.level * 14 + 'px' }"
            draggable="true"
            @dragstart="onDragStart($event, row)"
            @dragover.prevent
            @drop.prevent="onDrop($event, row)"
            @click="select(row.kind, row.item, row.path)"
          >
            <span v-if="row.kind === 'item'" class="no">第{{ row.no }}题</span>
            <span v-else class="no sub">{{ row.no }}</span>
            <span class="lbl">{{ row.label }}</span>
          </div>
        </div>
        <div class="treefoot">
          <button class="mini" @click="addItem">+ 大题</button>
          <button class="mini" @click="addSub" :disabled="!cur">+ 小问</button>
        </div>
      </aside>

      <section class="edit">
        <!-- 卷面设置 -->
        <div v-if="curKind === 'paper'" class="pane">
          <label class="f"><span>副标题（可留空）</span><input v-model="doc.subtitle" @input="saveLocal" /></label>
          <label class="f"><span>考试时长（分钟）</span><input type="number" v-model.number="doc.duration" @input="saveLocal" /></label>
        </div>

        <template v-else-if="cur">
          <!-- 大题 -->
          <div v-if="curKind === 'item'" class="pane">
            <div class="row2">
              <label class="f grow"><span>大题标题</span><input v-model="cur.title" placeholder="未知物的推断" @input="saveLocal" @keydown="onStemKey" /></label>
              <label class="f w90"><span>分值</span><input type="number" v-model.number="cur.score" @input="saveLocal" /></label>
              <label class="f w90"><span>占比%（留空自动）</span><input type="number" v-model.number="cur.percent" @input="saveLocal" /></label>
            </div>
            <div v-if="cur.source" class="srcbar">
              出处：文献 {{ cur.source.doc_id }} 第 {{ cur.source.page }} 页
              <button class="mini" @click="cur.source = null">解除</button>
            </div>
            <label class="f"><span>大题共同题干（回车分段，第一段顶格、后续缩进 2 字）</span>
              <textarea class="stem" rows="5" v-model="cur.intro" @input="saveLocal" @paste="onPaste"></textarea>
            </label>
            <div class="figrow">
              <button class="mini" @click="addFigure(cur)">+ 图</button>
              <button class="mini" @click="addTable(cur)">+ 表</button>
              <button class="mini" @click="editSource(cur)">+ 出处</button>
              <button class="mini" @click="openCrop({ node: cur, mode: 'new' })">✂ 从文献裁图</button>
              <button class="mini" :disabled="ai.busy" @click="aiDraft(cur)">
                {{ ai.busy ? 'AI 出题中…' : '✨ AI 出草稿' }}
              </button>
              <label class="mini file">插入本机图片<input type="file" accept="image/*" @change="onPickFile($event, cur)" /></label>
            </div>
          </div>

          <!-- 小问 -->
          <div v-else class="pane">
            <div class="row2">
              <span class="no big">{{ subNo(sel.item, sel.path) }}</span>
              <label class="f w90"><span>分值</span><input type="number" v-model.number="cur.score" @input="saveLocal" /></label>
              <div class="grow"></div>
              <button class="mini" @click="moveNode(-1)">↑</button>
              <button class="mini" @click="moveNode(1)">↓</button>
              <button class="mini danger" @click="delNode">删除</button>
            </div>
            <label class="f"><span>题干（Enter 同级小问，Ctrl+Enter 下一级小问，Tab 下一题）</span>
              <textarea class="stem" rows="4" v-model="cur.stem" @input="saveLocal" @keydown="onStemKey" @paste="onPaste"></textarea>
            </label>
            <label class="f"><span>续行 / 补充说明（可留空）</span>
              <textarea rows="2" v-model="cur.stem2" @input="saveLocal"></textarea>
            </label>
            <div class="figrow">
              <button class="mini" @click="addFigure(cur)">+ 图</button>
              <button class="mini" @click="addTable(cur)">+ 表</button>
              <button class="mini" @click="openCrop({ node: cur, mode: 'new' })">✂ 从文献裁图</button>
              <button class="mini" @click="editSource(cur)">+ 出处</button>
              <button class="mini" :disabled="ai.busy" @click="aiDraft(cur)">
                {{ ai.busy ? 'AI 出题中…' : '✨ AI 出草稿' }}
              </button>
            </div>
            <label class="f"><span>答案（答案卷红字）</span>
              <textarea rows="3" class="ans" v-model="cur.answer" @input="saveLocal"></textarea>
            </label>
            <label class="f"><span>给分点（一行一条，用｜分隔分值，如：写出 X1 2 分｜2）</span>
              <textarea rows="3" :value="pointsText(cur)" @change="setPoints(cur, $event.target.value)"></textarea>
            </label>
            <button class="mini" @click="addPoint(cur)">+ 给分点</button>
          </div>

          <!-- 图 / 表 / 出处 -->
          <div v-if="(cur.images || []).length" class="pane">
            <h3>图</h3>
            <div v-for="(img, i) in cur.images" :key="i" class="figcard">
              <img v-if="img.path" :src="imgUrl(img.path)" class="thumb" @click="openCrop({ node: cur, figure: img, mode: 'recrop' })" />
              <div v-else class="thumb empty">未选图</div>
              <div class="figmeta">
                <input class="capinput" v-model="img.caption" placeholder="图注文字（编号自动）" @input="saveLocal" />
                <div class="muted">
                  宽度 {{ img.width || 415 }}pt
                  <span v-if="img.source"> · 来自文献 {{ img.source.doc_id }} 第 {{ img.source.page }} 页 · {{ img.source.dpi || 216 }}dpi</span>
                </div>
                <div class="figrow">
                  <label class="muted">宽
                    <input class="wnum" v-model.number="img.width" type="number" min="20" max="415" step="10" @input="saveLocal" />
                    pt
                  </label>
                  <select class="wsel" v-model="img.align" @change="saveLocal">
                    <option value="">默认</option>
                    <option value="left">居左</option>
                    <option value="center">居中</option>
                    <option value="right">居右</option>
                  </select>
                  <button class="mini" :disabled="i === 0" @click="moveFigure(cur, i, -1)">上移</button>
                  <button class="mini" :disabled="i >= cur.images.length - 1" @click="moveFigure(cur, i, 1)">下移</button>
                  <button class="mini" @click="openCrop({ node: cur, figure: img, mode: 'recrop' })">重裁</button>
                  <button class="mini danger" @click="delFigure(cur, i)">删除</button>
                </div>
              </div>
            </div>
          </div>
          <div v-if="(cur.answer_images || []).length" class="pane">
            <h3>答案配图</h3>
            <div v-for="(img, i) in cur.answer_images" :key="'ans' + i" class="figcard">
              <img v-if="img.path" :src="imgUrl(img.path)" class="thumb" />
              <div class="figmeta">
                <input class="capinput" v-model="img.caption" placeholder="图注（可空）" @input="saveLocal" />
                <div class="figrow">
                  <label class="muted">宽
                    <input class="wnum" v-model.number="img.w" type="number" min="20" max="415" step="10" @input="saveLocal" />
                    pt
                  </label>
                  <select class="wsel" v-model="img.align" @change="saveLocal">
                    <option value="">默认</option>
                    <option value="left">居左</option>
                    <option value="center">居中</option>
                    <option value="right">居右</option>
                  </select>
                  <button class="mini danger" @click="cur.answer_images.splice(i, 1); saveLocal()">删除</button>
                </div>
              </div>
            </div>
          </div>
          <div v-if="(cur.tables || []).length" class="pane">
            <h3>表</h3>
            <div v-for="(t, i) in cur.tables" :key="i" class="figcard">
              <div class="figmeta grow">
                <input v-model="t.caption" placeholder="表注（可留空）" @input="saveLocal" />
                <input :value="(t.head || []).join(' | ')" placeholder="表头，用 | 分隔" @change="t.head = $event.target.value.split('|').map(s => s.trim()); snapshot(); schedulePreview()" />
                <textarea :value="tableToText(t)" rows="3" @change="textToTable(t, $event.target.value)"></textarea>
                <button class="mini danger" @click="cur.tables.splice(i, 1); snapshot(); schedulePreview()">删除</button>
              </div>
            </div>
          </div>
          <div v-if="cur.source" class="pane">
            <h3>出处</h3>
            <div class="row2">
              <label class="f grow"><span>文献 doc_id</span><input v-model="cur.source.doc_id" @input="saveLocal" /></label>
              <label class="f w90"><span>页码</span><input type="number" v-model.number="cur.source.page" @input="saveLocal" /></label>
              <label class="f w90"><span>类型</span>
                <select v-model="cur.source.kind" @change="saveLocal">
                  <option value="text">段落</option><option value="figure">图</option>
                </select>
              </label>
            </div>
            <label class="f"><span>引用原文（可留空）</span><textarea rows="2" v-model="cur.source.quote" @input="saveLocal"></textarea></label>
          </div>

          <div v-if="issues.length" class="pane">
            <h3>校验</h3>
            <div v-for="(it, i) in issues" :key="i" class="issue" :class="it.level">{{ it.msg }}</div>
          </div>
        </template>

        <!-- 模板 -->
        <div v-if="showTpl" class="pane tpl">
          <h3>版式模板</h3>
          <div class="row2">
            <label class="f"><span>左 90</span><input type="number" v-model.number="doc.template.margin.left" @input="saveLocal" /></label>
            <label class="f"><span>右 80</span><input type="number" v-model.number="doc.template.margin.right" @input="saveLocal" /></label>
            <label class="f"><span>上 72.5</span><input type="number" step="0.5" v-model.number="doc.template.margin.top" @input="saveLocal" /></label>
            <label class="f"><span>下 82</span><input type="number" v-model.number="doc.template.margin.bottom" @input="saveLocal" /></label>
            <label class="f"><span>正文字号 10.45</span><input type="number" step="0.05" v-model.number="doc.template.size" @input="saveLocal" /></label>
            <label class="f"><span>行距 15.6</span><input type="number" step="0.1" v-model.number="doc.template.lineHeight" @input="saveLocal" /></label>
            <label class="f"><span>题号风格</span>
              <select v-model="doc.template.numbering.style" @change="saveLocal">
                <option value="arabic">阿拉伯数字 1-1</option>
                <option value="chinese">中文数字 一、二</option>
              </select>
            </label>
            <label class="f"><span>答案色</span><input v-model="doc.template.answer.color" @input="saveLocal" /></label>
          </div>
        </div>
      </section>

      <section class="prev">
        <div class="prevhead">
          <span>{{ part === 'questions' ? '题目卷' : '答案卷' }}预览</span>
          <span class="muted" v-if="previewBusy">排版中…</span>
          <button class="mini" @click="refreshPreview">刷新</button>
        </div>
        <div class="prevbody">
          <iframe v-if="previewUrl" :src="previewUrl" class="sheet" :style="{ transform: `scale(${zoom})` }"></iframe>
          <div v-else class="empty">还没有题目</div>
        </div>
      </section>
    </div>

  <div v-if="ai.open" class="ai-mask" @click.self="ai.open = false">
    <div class="ai-card">
      <div class="ai-head">
        <b>AI 草稿</b>
        <span class="muted">AI 只出草稿：改完点「采用」才入卷；「忽略」直接丢弃</span>
        <button class="mini" @click="ai.open = false">关闭</button>
      </div>
      <div v-if="ai.err" class="errbox">{{ ai.err }}</div>
      <div v-for="(d, i) in ai.list" :key="i" class="ai-row">
        <label class="f"><span>题干（可直接改）</span>
          <textarea rows="5" v-model="d.stem"></textarea>
        </label>
        <label class="f"><span>答案（可直接改）</span>
          <textarea rows="3" v-model="d.answer"></textarea>
        </label>
        <div class="figrow">
          <label class="f" style="max-width:120px"><span>分值</span>
            <input type="number" min="1" v-model.number="d.score" />
          </label>
          <button class="mini primary" @click="aiAccept(d, i)">采用，入卷</button>
          <button class="mini danger" @click="ai.list.splice(i, 1)">忽略</button>
        </div>
      </div>
      <div v-if="!ai.list.length && !ai.busy" class="muted" style="padding:12px">没有草稿</div>
    </div>
  </div>

  <!-- 裁图弹窗（共用组件） -->
  <CropDialog :open="cropState.open" :source="cropState.target?.figure?.source || null"
              @close="closeCrop" @done="onCropDone" />
  <ChemDock @text="onDockText" @image="onDockImage" />
  </div>
</template>

<style scoped>
.pm { display: flex; flex-direction: column; height: 100vh; background: #f5f6f8; }
.pmbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: #fff; border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 10; flex-wrap: wrap; }
.pmbar .back { font-size: 13px; }
.pmbar .ttl { width: 300px; font-weight: 600; }
.pmbar .stat { font-size: 13px; color: var(--muted); white-space: nowrap; }
.pmbar .stat b { color: var(--ink); }
.pmbar .stat b.bad, .bad { color: var(--bad); }
.issueflag { color: var(--bad); font-size: 13px; cursor: pointer; }
.save { font-size: 12px; color: var(--muted); min-width: 48px; }
.save.err { color: var(--bad); }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { border: 0; border-radius: 0; padding: 5px 10px; font-size: 13px; }
.seg button.on { background: var(--brand-soft); color: var(--brand); font-weight: 600; }
.pmbody { flex: 1; display: grid; grid-template-columns: 210px minmax(360px, 1fr) minmax(420px, 46%); min-height: 0; }
.loading { padding: 40px; text-align: center; color: var(--muted); }

.tree { background: #fff; border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
.treehead { display: flex; align-items: center; justify-content: space-between; padding: 10px; border-bottom: 1px solid var(--line); font-size: 13px; }
.treebody { flex: 1; overflow: auto; padding: 6px 0; }
.trow { display: flex; gap: 6px; align-items: baseline; padding: 5px 10px; font-size: 13px; cursor: pointer; border-left: 3px solid transparent; }
.trow:hover { background: #f7f8fa; }
.trow.on { background: var(--brand-soft); border-left-color: var(--brand); }
.trow.item { font-weight: 600; margin-top: 6px; }
.trow .no { color: var(--brand); font-weight: 700; flex: 0 0 auto; font-size: 12px; }
.treebody .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.treefoot { display: flex; gap: 6px; padding: 8px; border-top: 1px solid var(--line); }

.edit { overflow: auto; padding: 14px; min-height: 0; }
.pane { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; }
.pane h3 { margin: 0 0 8px; font-size: 14px; }
.row2 { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
.row2 .grow { flex: 1; }
.row2 .w90 { width: 92px; }
label.f { display: block; margin-bottom: 8px; font-size: 12px; color: var(--muted); }
label.f span { display: block; margin-bottom: 3px; }
label.f input, label.f textarea, label.f select { font-size: 14px; color: var(--ink); }
.stem { min-height: 70px; }
.ans { color: #b91c1c; }
.figrow { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin: 6px 0; }
.mini { font-size: 12px; padding: 3px 9px; }
.mini.file { border: 1px dashed var(--line); border-radius: 8px; padding: 3px 9px; cursor: pointer; }
.mini.file input { display: none; }
.no.big { font-weight: 700; color: var(--brand); font-size: 15px; }
.ai-mask { position: fixed; inset: 0; background: rgba(15, 18, 24, .55); display: flex; align-items: center; justify-content: center; z-index: 60; }
.ai-card { width: min(760px, 92vw); max-height: 86vh; overflow: auto; background: #fff; border-radius: 12px; padding: 14px 16px; }
.ai-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.ai-row { border: 1px solid var(--line); border-radius: 10px; padding: 10px; margin-bottom: 12px; }
.figcard { display: flex; gap: 10px; border: 1px solid var(--line); border-radius: 8px; padding: 8px; margin-bottom: 8px; }
.thumb { width: 120px; height: 90px; object-fit: contain; background: #fafbfc; border-radius: 6px; cursor: pointer; }
.thumb.empty { display: flex; align-items: center; justify-content: center; font-size: 12px; color: var(--muted); }
.figmeta { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.srcbar { font-size: 12px; color: #7c4d00; background: #fdf6e3; border-radius: 6px; padding: 4px 8px; margin-bottom: 8px; }
.issue { font-size: 13px; padding: 3px 0; }
.issue.warn { color: var(--warn); }
.issue.error { color: var(--bad); }
.tpl .row2 { gap: 10px; }
.tpl label.f { width: 120px; }
.tpl select { width: 100%; }

.prev { border-left: 1px solid var(--line); background: #eef0f4; display: flex; flex-direction: column; min-height: 0; }
.prevhead { display: flex; align-items: center; gap: 8px; padding: 8px 10px; background: #fff; border-bottom: 1px solid var(--line); font-size: 13px; }
.prevbody { flex: 1; overflow: auto; padding: 12px; }
.prevbody .sheet { width: 210mm; height: 297mm; border: 0; background: #fff; box-shadow: 0 2px 12px rgba(16, 24, 40, .16); transform-origin: top left; }
.prevbody .empty { padding: 30px; text-align: center; color: var(--muted); }

.cropmask { position: fixed; inset: 0; background: rgba(17, 24, 39, .55); z-index: 60; display: flex; align-items: center; justify-content: center; }
.cropcard { background: #fff; border-radius: 12px; width: min(1080px, 94vw); height: min(880px, 92vh); display: flex; flex-direction: column; overflow: hidden; }
.crophead, .cropfoot { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--line); font-size: 13px; flex-wrap: wrap; }
.cropfoot { border-bottom: 0; border-top: 1px solid var(--line); }
.crophead select { width: auto; max-width: 260px; font-size: 13px; }
.cropbody { flex: 1; overflow: hidden; background: #3f4550; display: flex; align-items: center; justify-content: center; padding: 12px; min-height: 0; }
.viewer { position: relative; user-select: none; cursor: crosshair; height: 100%; }
.viewer img { display: block; height: 100%; width: auto; max-width: 100%; }
.viewer .sel { position: absolute; border: 1.5px solid #ffd400; background: rgba(255, 212, 0, .18); box-shadow: 0 0 0 1px rgba(0, 0, 0, .25); pointer-events: none; }
.wnum { width: 56px; height: 24px; border: 1px solid #d1d5db; border-radius: 5px; padding: 0 4px; font-size: 12.5px; text-align: right; }
.wsel { height: 24px; border: 1px solid #d1d5db; border-radius: 5px; font-size: 12.5px; background: #fff; }
</style>
