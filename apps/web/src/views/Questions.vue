<script setup>
/**
 * 题库（仿 Chemy 的风格）
 * - 顶部：搜索 + 专题 chips（Chemy 九大专题）+ 题型/难度/状态筛选
 * - 列表：卡片直接显示题干、题图、标签、出处；勾选后可一键组卷或导出
 * - 右上：从 QuizForge 导入（内置适配器，选库或上传 .db）
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import Extract from './Extract.vue'
import { renderMath, stripMath } from '../math.js'

const router = useRouter()
const route = useRoute()
const DIFF_LABEL = { 1: '基础', 2: '中等', 3: '较难' }
const STATUS_LABEL = { confirmed: '已确认', draft: '草稿' }

const items = ref([])
const total = ref(0)
const page = ref(1)
const PAGE_SIZE = 20
const loading = ref(false)
const err = ref('')
const msg = ref('')

const topicGroups = ref([])
const activeTag = ref('')
const qtypes = ref([])

const LS_SIDE = 'tiku-bank-side'
const sideOpen = ref(localStorage.getItem(LS_SIDE) !== '0')
function toggleSide() {
  sideOpen.value = !sideOpen.value
  localStorage.setItem(LS_SIDE, sideOpen.value ? '1' : '0')
}

const f = reactive({ q: '', q_type: '', difficulty: '', status: '' })

const sel = ref(new Set())

// ---------- 排序：加入顺序 / 难易 / 手动 ----------
const LS_SORT = 'tiku-bank-sort'
const sortMode = ref(localStorage.getItem(LS_SORT) || 'added_desc')
function setSort(v) {
  sortMode.value = v
  localStorage.setItem(LS_SORT, v)
  page.value = 1
  load()
}

// ---------- 分类维度（在设置里自定义） ----------
const dims = ref([])
async function loadDims() {
  try { const d = await api.get('/api/wb/categories'); dims.value = d.dims || [] } catch { dims.value = [] }
}

// ---------- 试题篮 ----------
const LS_BASKET = 'tiku-basket-v1'
const basket = ref([])
const basketOpen = ref(localStorage.getItem('tiku-basket-open') === '1')
const basketTitle = ref('')

// 错题集
const moreOpen = ref(false)
const showExtract = ref(false)
const wrongBooks = ref([])
const wrongPick = ref(null)        // 正在加题的目标题 id
const wrongName = ref('')
async function loadWrongBooks() {
  try { const d = await api.get('/api/wb/wrongbooks'); wrongBooks.value = d.books || d || [] } catch { /* 忽略 */ }
}
function openWrong(q) { wrongPick.value = q.id; wrongName.value = ''; loadWrongBooks() }
async function addWrong(bid) {
  if (!wrongPick.value) return
  try {
    await api.post(`/api/wb/wrongbooks/${bid}/items`, { question_ids: [wrongPick.value] })
    msg.value = '已加入错题集'
    wrongPick.value = null
    loadWrongBooks()
  } catch (e) { err.value = e.message }
}
async function createWrongAndAdd() {
  const n = wrongName.value.trim()
  if (!n) return
  try {
    let bid = null
    try {
      const b = await api.post('/api/wb/wrongbooks', { name: n, note: '' })
      bid = b.id
    } catch {
      const d = await api.get('/api/wb/wrongbooks')
      const hit = (d.books || []).find((x) => x.name === n)
      if (!hit) throw new Error('同名错题集已存在，但取不到它的 id')
      bid = hit.id
    }
    await addWrong(bid)
  } catch (e) { err.value = e.message }
}
const basketBusy = ref(false)
function saveBasket() { localStorage.setItem(LS_BASKET, JSON.stringify(basket.value)) }
function loadBasket() {
  try { basket.value = JSON.parse(localStorage.getItem(LS_BASKET) || '[]') } catch { basket.value = [] }
}
function toggleBasket() {
  basketOpen.value = !basketOpen.value
  localStorage.setItem('tiku-basket-open', basketOpen.value ? '1' : '0')
}
function inBasket(id) { return basket.value.some(x => x.id === id) }
function addToBasket(q) {
  if (!q || inBasket(q.id)) return
  const base = q.q_type === '选择' ? 2 : (q.q_type === '计算' ? 10 : 6)
  basket.value.push({ id: q.id, stem: q.stem, q_type: q.q_type, difficulty: q.difficulty,
    score: base, align: 'left', font_size: 0, color: '' })
  basketOpen.value = true
  saveBasket()
}
function removeFromBasket(id) { basket.value = basket.value.filter(x => x.id !== id); saveBasket() }
function moveInBasket(i, d) {
  const j = i + d
  if (j < 0 || j >= basket.value.length) return
  const a = basket.value
  const t = a[i]; a[i] = a[j]; a[j] = t
  saveBasket()
}
function clearBasket() { basket.value = []; saveBasket() }
const basketTotal = computed(() => basket.value.reduce((s, x) => s + Number(x.score || 0), 0))
async function makePaper() {
  if (!basket.value.length) return
  basketBusy.value = true
  try {
    const r = await api.post('/api/wb/papers', {
      title: basketTitle.value,
      duration_minutes: 90,
      items: basket.value.map(x => ({ question_id: x.id, score: x.score,
        align: x.align, font_size: x.font_size, color: x.color })),
    })
    clearBasket()
    router.push(`/papers/${r.id}`)
  } catch (e) {
    err.value = e.message
  } finally { basketBusy.value = false }
}

// ---------- 拖拽 ----------
const dragQid = ref(0)
const manualDragId = ref(0)
function onCardDragStart(q, e) {
  dragQid.value = q.id
  manualDragId.value = q.id
  e.dataTransfer.setData('text/plain', String(q.id))
  e.dataTransfer.effectAllowed = 'copyMove'
}
function onBasketDrop(e) {
  e.preventDefault()
  const id = Number(e.dataTransfer.getData('text/plain') || dragQid.value)
  const q = items.value.find(x => x.id === id)
  if (q) addToBasket(q)
}
async function onCardDrop(target, e) {
  if (sortMode.value !== 'manual') { onBasketDrop(e); return }
  e.preventDefault()
  const from = manualDragId.value
  if (!from || from === target.id) return
  const ids = items.value.map(x => x.id)
  const a = ids.indexOf(from), b = ids.indexOf(target.id)
  if (a < 0 || b < 0) return
  ids.splice(b, 0, ids.splice(a, 1)[0])
  const map = new Map(items.value.map(x => [x.id, x]))
  items.value = ids.map(id => map.get(id))
  try { await api.post('/api/wb/questions/reorder', { ids }) } catch (err2) { err.value = err2.message }
}

const openAnswers = ref(new Set())
const zoomImg = ref('')

const showExport = ref(false)
const exporting = ref('')
const exportOpts = reactive({ title: '练习卷', answers: 'appendix', analysis: true, tags: true, source: true })

const sets = ref([])              // [{id,name,year,count}]
const unfiled = ref(0)
const activeSet = ref('')         // '' | 'unfiled' | '3'
const showSets = ref(false)       // 「归入整卷」弹窗
const newSet = reactive({ name: '', year: new Date().getFullYear(), busy: false })
const vers = ref(null)            // { q, items, busy }

const showImport = ref(false)
const paperImp = reactive({ busy: false, title: '', result: null, err: '' })

const topicTags = computed(() => {
  const g = topicGroups.value.find((x) => x.group === '专题')
  return g ? g.tags : []
})
const selList = computed(() => [...sel.value])
const allChecked = computed(() => items.value.length > 0 && items.value.every((q) => sel.value.has(q.id)))
const pages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function imgUrl(p) { return p ? `/api/images/${p}` : '' }
function stemImg(q) {
  const imgs = q.images || []
  return imgs.find((i) => (i.caption || '') !== '答案') || null
}

async function loadTopics() {
  try {
    const data = await api.get('/api/authoring/tags')
    topicGroups.value = Object.entries(data.groups || {}).map(([group, tags]) => ({ group, tags }))
  } catch (e) { err.value = e.message }
}

async function loadSets() {
  try {
    const data = await api.get('/api/sets')
    sets.value = data.items || []
    unfiled.value = data.unfiled || 0
  } catch (e) { sets.value = [] }
}

async function loadQtypes() {
  try { qtypes.value = await api.get('/api/qtypes') } catch (e) { qtypes.value = [] }
}

async function load() {
  loading.value = true; err.value = ''
  try {
    const p = new URLSearchParams({ page: String(page.value), page_size: String(PAGE_SIZE) })
    if (f.q.trim()) p.set('q', f.q.trim())
    if (f.q_type) p.set('q_type', f.q_type)
    if (f.difficulty) p.set('difficulty', f.difficulty)
    if (f.status) p.set('status', f.status)
    if (activeTag.value) p.set('tag', activeTag.value)
    if (activeSet.value) p.set('set_id', activeSet.value === 'unfiled' ? '-1' : activeSet.value)
    p.set('sort', sortMode.value)
    const data = await api.get('/api/questions?' + p.toString())
    items.value = data.items || []
    total.value = data.total || 0
  } catch (e) { err.value = e.message }
  loading.value = false
}

function pickTag(name) {
  activeTag.value = activeTag.value === name ? '' : name
  page.value = 1
  load()
}
function search() { page.value = 1; load() }
function reset() {
  Object.assign(f, { q: '', q_type: '', difficulty: '', status: '' })
  activeTag.value = ''
  page.value = 1
  load()
}
function goPage(d) {
  const next = page.value + d
  if (next < 1 || next > pages.value) return
  page.value = next
  load()
}

function toggleSel(id) {
  const s = new Set(sel.value)
  s.has(id) ? s.delete(id) : s.add(id)
  sel.value = s
}
function toggleAll() {
  const s = new Set(sel.value)
  if (allChecked.value) items.value.forEach((q) => s.delete(q.id))
  else items.value.forEach((q) => s.add(q.id))
  sel.value = s
}
function clearSel() { sel.value = new Set() }
function answerImgs(q) {
  return (q.images || []).filter((i) => (i.caption || '') === '答案')
}

function toggleAnswer(id) {
  const s = new Set(openAnswers.value)
  s.has(id) ? s.delete(id) : s.add(id)
  openAnswers.value = s
}

function pickSet(v) {
  activeSet.value = activeSet.value === v ? '' : v
  page.value = 1
  load()
}

/** 把整卷题目一次性加入已选 */
async function selectWholeSet() {
  if (!activeSet.value || activeSet.value === 'unfiled') return
  try {
    const data = await api.get(`/api/sets/${activeSet.value}/question-ids`)
    const s2 = new Set(sel.value)
    for (const id of data.question_ids || []) s2.add(id)
    sel.value = s2
    msg.value = `已把整卷 ${data.question_ids.length} 题加入已选`
  } catch (e) { err.value = e.message }
}

async function doAssign(setId) {
  try {
    const data = await api.post(`/api/sets/${setId}/assign`, { question_ids: selList.value })
    msg.value = `已归入整卷：${data.assigned} 题`
    showSets.value = false
    await Promise.all([load(), loadSets()])
  } catch (e) { err.value = e.message }
}

async function createSetAndAssign() {
  if (!newSet.name.trim()) { err.value = '先填一个卷名'; return }
  newSet.busy = true
  try {
    const made = await api.post('/api/sets', { name: newSet.name.trim(), year: Number(newSet.year) || 0 })
    await doAssign(made.id)
    newSet.name = ''
  } catch (e) { err.value = e.message }
  newSet.busy = false
}

async function detachSelected() {
  try {
    const data = await api.post('/api/sets/detach', { question_ids: selList.value })
    msg.value = `已移出整卷：${data.detached} 题`
    await Promise.all([load(), loadSets()])
  } catch (e) { err.value = e.message }
}

async function trimBlank(q) {
  try {
    const r = await api.post(`/api/questions/${q.id}/trim`)
    msg.value = `已裁剪 #${r.id}（${r.width}×${r.height}）`
    await load()
  } catch (e) { err.value = e?.message || String(e) }
}

async function openVersions(q) {
  vers.value = { q, items: [], busy: true }
  try {
    const data = await api.get(`/api/questions/${q.id}/versions`)
    vers.value = { q, items: data.items || [], busy: false }
  } catch (e) { vers.value = { q, items: [], busy: false }; err.value = e.message }
}

async function restoreVersion(v) {
  try {
    await api.post(`/api/questions/${vers.value.q.id}/versions/${v.id}/restore`)
    msg.value = `已回滚到第 ${v.version_no} 版`
    await openVersions(vers.value.q)
    await load()
  } catch (e) { err.value = e.message }
}

function fmtTime(t) {
  if (!t) return ''
  return String(t).replace('T', ' ').slice(0, 16)
}

const showPaper = ref(false)
const paperPool = ref([])
const paperMeta = reactive({ title: '', duration_minutes: 120 })
const paperScores = reactive({})
const paperBusy = ref(false)
const PAPER_SCORE = {
  '单选': 3, '多选': 4, '判断': 2, '填空': 4, '名词解释': 5, '简答': 6,
  '论述': 10, '计算': 8, '证明': 8, '案例分析': 10, '翻译': 5, '文献阅读': 10, '其他': 4,
}
const paperTypes = computed(() => [...new Set(paperPool.value.map((q) => q.q_type))])
const paperTotal = computed(() =>
  paperPool.value.reduce((sum, q) => sum + Number(paperScores[q.q_type] ?? PAPER_SCORE[q.q_type] ?? 4), 0))
const paperStem = (q) => {
  const t = stripMath(q.stem || '').replace(/\s+/g, ' ').trim()
  return t ? (t.length > 48 ? t.slice(0, 48) + '…' : t) : '（题图题）'
}

async function openPaper() {
  if (!selList.value.length) return
  err.value = ''
  paperBusy.value = true
  try {
    const data = await api.get(`/api/questions?ids=${selList.value.join(',')}&page_size=200`)
    const map = new Map((data.items || []).map((q) => [q.id, q]))
    paperPool.value = selList.value.map((id) => map.get(id)).filter(Boolean)
    for (const t of new Set(paperPool.value.map((q) => q.q_type))) {
      if (paperScores[t] == null) paperScores[t] = PAPER_SCORE[t] ?? 4
    }
    paperMeta.duration_minutes = Math.max(30, paperPool.value.length * 6)
    paperMeta.title = `自组卷 · ${new Date().toLocaleDateString('zh-CN')}`
    showPaper.value = true
  } catch (e) { err.value = e.message } finally { paperBusy.value = false }
}

function movePaper(i, delta) {
  const j = i + delta
  if (j < 0 || j >= paperPool.value.length) return
  const arr = paperPool.value.slice()
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
  paperPool.value = arr
}

function removePaper(i) {
  paperPool.value = paperPool.value.filter((_, k) => k !== i)
}

async function createPaper() {
  if (!paperPool.value.length) { err.value = '还没有题'; return }
  paperBusy.value = true
  err.value = ''
  try {
    const perType = {}
    for (const t of paperTypes.value) perType[t] = Number(paperScores[t] ?? PAPER_SCORE[t] ?? 4)
    const p = await api.post('/api/papers', {
      question_ids: paperPool.value.map((q) => q.id),
      total_score: paperTotal.value,
      duration_minutes: Number(paperMeta.duration_minutes) || 60,
      per_type_score: perType,
      title: paperMeta.title || undefined,
    })
    showPaper.value = false
    router.push(`/papers/${p.id}`)
  } catch (e) { err.value = e.message } finally { paperBusy.value = false }
}

/* 按教材自动组卷（原「组卷」页的自动模式，并入题库） */
const showAuto = ref(false)
const autoBusy = ref(false)
const autoBooks = ref([])
const autoBookId = ref(null)
const autoUnits = ref([])
const autoStats = ref({})
const autoMeta = reactive({ title: '', duration_minutes: 120, unit_from: 1, unit_to: null, include_extension: false })
const autoCounts = reactive({})
const autoScores = reactive({})
const autoTypes = computed(() => Object.keys(autoCounts))
const autoTotal = computed(() => autoTypes.value.reduce(
  (sum, t) => sum + Number(autoCounts[t] || 0) * Number(autoScores[t] || PAPER_SCORE[t] || 4), 0))

async function openAuto() {
  err.value = ''
  showAuto.value = true
  autoBusy.value = true
  try {
    autoBooks.value = await api.get('/api/books/textbooks')
    if (!autoBooks.value.length) return
    if (!autoBookId.value || !autoBooks.value.some((b) => b.id === autoBookId.value)) {
      autoBookId.value = autoBooks.value[0].id
    }
    await loadAutoBook()
  } catch (e) { err.value = e.message } finally { autoBusy.value = false }
}

async function loadAutoBook() {
  if (!autoBookId.value) return
  autoBusy.value = true
  err.value = ''
  try {
    const [book, stats] = await Promise.all([
      api.get(`/api/books/${autoBookId.value}`),
      api.get(`/api/questions/type-stats?textbook_id=${autoBookId.value}`),
    ])
    autoUnits.value = book.units || []
    autoStats.value = stats || {}
    const maxSeq = autoUnits.value.length ? autoUnits.value[autoUnits.value.length - 1].seq : 1
    autoMeta.unit_from = 1
    autoMeta.unit_to = maxSeq
    for (const k of Object.keys(autoCounts)) delete autoCounts[k]
    for (const k of Object.keys(autoStats.value)) {
      autoCounts[k] = 0
      if (autoScores[k] == null) autoScores[k] = PAPER_SCORE[k] ?? 4
    }
    if (!Object.keys(autoCounts).length) {
      for (const t of ['单选', '填空', '简答']) autoCounts[t] = 0
    }
    const b = autoBooks.value.find((x) => x.id === autoBookId.value)
    autoMeta.title = `${b ? b.title + ' · ' : ''}自动组卷 ${new Date().toLocaleDateString('zh-CN')}`
  } catch (e) { err.value = e.message } finally { autoBusy.value = false }
}

async function createAuto() {
  const counts = {}
  for (const t of autoTypes.value) {
    const n = Number(autoCounts[t] || 0)
    if (n > 0) counts[t] = n
  }
  if (!Object.keys(counts).length) { err.value = '至少给一种题型填数量'; return }
  autoBusy.value = true
  err.value = ''
  try {
    const perType = {}
    for (const t of Object.keys(counts)) perType[t] = Number(autoScores[t] || PAPER_SCORE[t] || 4)
    const p = await api.post('/api/papers', {
      textbook_id: autoBookId.value,
      unit_from: Number(autoMeta.unit_from) || 1,
      unit_to: Number(autoMeta.unit_to) || undefined,
      title: autoMeta.title || undefined,
      total_score: autoTotal.value,
      duration_minutes: Number(autoMeta.duration_minutes) || 60,
      include_extension: !!autoMeta.include_extension,
      counts,
      per_type_score: perType,
    })
    showAuto.value = false
    router.push(`/papers/${p.id}`)
  } catch (e) { err.value = e.message } finally { autoBusy.value = false }
}

function download(name, blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

async function doExport(fmt) {
  exporting.value = fmt
  err.value = ''
  try {
    const p = new URLSearchParams({ fmt, title: exportOpts.title || '练习卷' })
    p.set('answers', exportOpts.answers)
    p.set('analysis', exportOpts.analysis ? '1' : '0')
    p.set('show_tags', exportOpts.tags ? '1' : '0')
    p.set('source', exportOpts.source ? '1' : '0')
    if (sel.value.size) p.set('ids', selList.value.join(','))
    else {
      if (f.q.trim()) p.set('q', f.q.trim())
      if (f.q_type) p.set('q_type', f.q_type)
      if (f.difficulty) p.set('difficulty', f.difficulty)
      if (activeTag.value) p.set('tags', activeTag.value)
    }
    const resp = await fetch('/api/authoring/export?' + p.toString())
    if (!resp.ok) {
      let detail = resp.statusText
      try { detail = (await resp.json()).detail || detail } catch (e) { /* 保留 statusText */ }
      throw new Error(detail)
    }
    const cd = resp.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*=UTF-8''([^;]+)/)
    const name = m ? decodeURIComponent(m[1]) : `练习卷.${fmt}`
    download(name, await resp.blob())
    msg.value = `已导出 ${resp.headers.get('X-Question-Count') || ''} 题`
  } catch (e) { err.value = e.message }
  exporting.value = ''
}

async function deleteOne(id) {
  if (!window.confirm(`删除题目 #${id}？`)) return
  try {
    await api.del(`/api/questions/${id}`)
    const s = new Set(sel.value); s.delete(id); sel.value = s
    await load()
  } catch (e) { err.value = e.message }
}

// ---------------------------------------------------------------- 导入整卷
async function openImport() {
  showImport.value = true
  paperImp.err = ''
  paperImp.result = null
}

async function uploadPaper(event) {
  const files = Array.from(event.target.files || [])
  if (!files.length) return
  paperImp.busy = true
  paperImp.err = ''
  paperImp.result = null
  err.value = ''
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  try {
    const q = paperImp.title.trim() ? `?title=${encodeURIComponent(paperImp.title.trim())}` : ''
    const data = await api.postForm(`/api/import/papers${q}`, fd)
    paperImp.result = data
    msg.value = `试卷「${data.title}」导入完成：新增 ${data.imported} 题，跳过重复 ${data.skipped} 题`
    await Promise.all([load(), loadTopics(), loadSets()])
  } catch (e) { paperImp.err = e.message } finally { paperImp.busy = false }
  event.target.value = ''
}

function paperDrop(event) {
  const files = Array.from(event.dataTransfer?.files || []).filter((f) => /\.pdf$/i.test(f.name))
  if (!files.length) { paperImp.err = '请拖入 PDF 文件'; return }
  uploadPaper({ target: { files } })
}

async function onExtracted(r) {
  msg.value = `批量拆题完成：新增 ${r?.total ?? 0} 题（草稿），核对后确认即可`
  await Promise.all([load(), loadTopics()])
}

async function deleteSelected() {
  if (!selList.value.length) return
  if (!window.confirm(`删除选中的 ${selList.value.length} 道题？删除后不可恢复。`)) return
  err.value = ''
  let done = 0
  try {
    for (const id of selList.value.slice()) {
      await api.del(`/api/questions/${id}`)
      done += 1
    }
    sel.value = new Set()
    msg.value = `已删除 ${done} 道题`
    await load()
  } catch (e) { err.value = `已删除 ${done} 道题；继续删除时失败：${e.message}` }
}

onMounted(async () => {
  // 窄屏默认收起分类，先把题目露出来
  const narrowInit = typeof window !== 'undefined' && window.innerWidth < 760
  if (narrowInit) sideOpen.value = false
  loadBasket()
  await Promise.all([loadTopics(), loadQtypes(), loadSets(), loadDims()])
  // 支持 ?tag=QuizForge / ?tag=分析化学 这类直达筛选
  const t = String(route.query.tag || '').trim()
  if (t) activeTag.value = t
  await load()
})
</script>

<template>
  <h2>题库</h2>

  <div class="bank" :class="{ collapsed: !sideOpen }">
    <button v-if="!sideOpen" class="side-tab" title="展开分类" @click="toggleSide">»</button>
    <aside v-show="sideOpen" class="bank-side">
      <div class="shead-row">
        <span class="muted">分类</span>
        <button class="ghost sm" title="收起分类" @click="toggleSide">«</button>
      </div>
      <div class="sgroup" v-for="d in dims" :key="d.key">
        <div class="shead">{{ d.name }}</div>
        <button v-if="d.key === dims[0]?.key" class="sitem" :class="{ on: !activeTag }" @click="activeTag = ''; search()">
          全部题目
        </button>
        <button v-for="v in d.values" :key="v.id" class="sitem"
                :class="{ on: activeTag === v.name }" @click="pickTag(v.name)">{{ v.name }}</button>
      </div>

      <div class="sgroup" v-if="!dims.length && topicTags.length">
        <div class="shead">专题</div>
        <button class="sitem" :class="{ on: !activeTag }" @click="activeTag = ''; search()">全部题目</button>
        <button v-for="t in topicTags" :key="t.id" class="sitem"
                :class="{ on: activeTag === t.name }" @click="pickTag(t.name)">{{ t.name }}</button>
      </div>

      <p class="muted s-hint">分类项可在「设置 → 分类」里自己增删改名。</p>
      <div class="sgroup" v-if="sets.length || unfiled">
        <div class="shead">整卷</div>
        <button class="sitem" :class="{ on: !activeSet }" @click="activeSet = ''; search()">全部</button>
        <button class="sitem" :class="{ on: activeSet === 'unfiled' }" @click="pickSet('unfiled')">
          未归卷 <i>{{ unfiled }}</i>
        </button>
        <button v-for="st in sets" :key="st.id" class="sitem"
                :class="{ on: activeSet === String(st.id) }" @click="pickSet(String(st.id))">
          {{ st.name }} <i>{{ st.count }}</i>
        </button>
      </div>
        </aside>

      <div v-if="showExtract" class="xmask" @click.self="showExtract = false">
        <div class="xcard">
          <div class="xhead">
            <h3>批量拆题</h3>
            <button class="ghost sm" @click="showExtract = false">关闭</button>
          </div>
          <Extract embedded @done="onExtracted" />
        </div>
      </div>

      <div v-if="wrongPick" class="modal-mask" @click.self="wrongPick = null">
        <div class="modal">
          <h3>加入错题集</h3>
          <div class="wrow">
            <input v-model="wrongName" placeholder="新建一本，如：电化学易错" @keyup.enter="createWrongAndAdd" />
            <button class="primary sm" @click="createWrongAndAdd">新建并加入</button>
          </div>
          <p class="muted" v-if="!wrongBooks.length">还没有错题集，先在上面建一本。</p>
          <div class="wlist" v-else>
            <button v-for="b in wrongBooks" :key="b.id" class="ghost sm" @click="addWrong(b.id)">
              {{ b.name }} <span class="muted">（{{ b.count }}）</span>
            </button>
          </div>
          <div class="wfoot"><button class="ghost sm" @click="wrongPick = null">取消</button></div>
        </div>
      </div>

    <main class="bank-main">

  <div class="card q-toolbar">
    <div class="searchrow">
      <input v-model="f.q" class="search" placeholder="搜索题干、答案、文献名…（回车）" @keyup.enter="search" />
      <button class="primary" @click="search">查询</button>
      <button @click="reset">重置</button>
      <span class="spacer"></span>
      <button class="ghost" @click="showExtract = true">⤓ 批量拆题</button>
    <div class="frow sortrow">
      <label class="muted">排序</label>
      <select :value="sortMode" @change="setSort($event.target.value)">
        <option value="added_desc">加入顺序（新→旧）</option>
        <option value="added_asc">加入顺序（旧→新）</option>
        <option value="easy">由易到难</option>
        <option value="hard">由难到易</option>
        <option value="manual">手动排序（拖动卡片）</option>
      </select>
      <span v-if="sortMode === 'manual'" class="muted tiny">拖卡片可调顺序，松手即保存</span>
      <button class="ghost" :class="{ primary: moreOpen || f.q_type || f.difficulty }" @click="moreOpen = !moreOpen">
        筛选{{ (f.q_type ? 1 : 0) + (f.difficulty ? 1 : 0) ? ` · ${(f.q_type ? 1 : 0) + (f.difficulty ? 1 : 0)}` : '' }}
      </button>
      <span class="spacer"></span>
      <button class="ghost" :class="{ primary: basketOpen }" @click="toggleBasket">
        🧺 试题篮{{ basket.length ? ` · ${basket.length} 题` : '' }}
      </button>
    </div>
    </div>



    <div class="frow filterrow" v-show="moreOpen">
      <label class="muted">题型</label>
      <select v-model="f.q_type" @change="search">
        <option value="">全部题型</option>
        <option v-for="t in qtypes" :key="t" :value="t">{{ t }}</option>
      </select>
      <label class="muted">难度</label>
      <select v-model="f.difficulty" @change="search">
        <option value="">全部难度</option>
        <option :value="1">基础</option>
        <option :value="2">中等</option>
        <option :value="3">提优</option>
      </select>
      <button class="ghost sm" v-if="f.q_type || f.difficulty" @click="f.q_type = ''; f.difficulty = ''; search()">清除筛选</button>
    </div>

    <div class="frow countrow">
      <span class="muted">共 {{ total }} 题<template v-if="activeTag"> · 专题「{{ activeTag }}」</template></span>
    </div>
  </div>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="okbox">{{ msg }}</div>

  <div class="card selbar slim">
    <label class="chk"><input type="checkbox" :checked="allChecked" @change="toggleAll" /> 全选本页</label>
    <span class="muted" v-if="!selList.length">勾选题目后这里会出现批量操作</span>
    <template v-else>
      <span class="muted">已选 {{ selList.length }} 题</span>
      <button class="ghost" @click="clearSel">清空</button>
      <button class="ghost danger" @click="deleteSelected">删除所选</button>
      <button class="ghost" v-if="activeSet && activeSet !== 'unfiled'" @click="selectWholeSet">整卷加入</button>
      <button class="ghost" @click="showSets = true">归入整卷…</button>
      <button class="ghost" @click="detachSelected">移出整卷</button>
      <button @click="openPaper">用所选组卷 →</button>
    </template>
    <span class="spacer"></span>
    <button class="ghost" :disabled="paperBusy" @click="openAuto">自动组卷</button>
  </div>

  <div v-if="loading" class="muted">加载中…</div>

  <div class="qwrap" :class="{ withb: basketOpen }">
  <aside v-if="basketOpen" class="basket"
         @dragover.prevent
         @drop="onBasketDrop">
    <div class="bhead">
      <b>试题篮</b>
      <span class="muted">{{ basket.length }} 题 · {{ basketTotal }} 分</span>
      <span class="spacer"></span>
      <button class="ghost sm" @click="toggleBasket">收起</button>
    </div>
    <p v-if="!basket.length" class="muted tiny">
      把左边的题目卡片拖进来，或点题目上的「+ 试题篮」。
    </p>
    <div v-for="(b, i) in basket" :key="b.id" class="bitem">
      <div class="brow1">
        <span class="bno">{{ i + 1 }}</span>
        <span class="bstem" :title="stripMath(b.stem)">{{ stripMath(b.stem) || '（图片题）' }}</span>
        <button class="x" title="取出" @click="removeFromBasket(b.id)">×</button>
      </div>
      <div class="brow2">
        <input class="bscore" type="number" min="0" step="1" v-model.number="b.score" @change="saveBasket" />
        <span class="muted tiny">分</span>
        <select v-model="b.align" class="bsel" @change="saveBasket" title="对齐">
          <option value="left">左对齐</option>
          <option value="center">居中</option>
          <option value="right">右对齐</option>
        </select>
        <input class="bnum" type="number" min="0" max="24" step="0.5" v-model.number="b.font_size"
               placeholder="字号" title="字号 pt（0=默认）" @change="saveBasket" />
        <input class="bcolor" type="color" v-model="b.color" title="文字颜色" @change="saveBasket" />
        <span class="spacer"></span>
        <button class="ghost sm" @click="moveInBasket(i, -1)">↑</button>
        <button class="ghost sm" @click="moveInBasket(i, 1)">↓</button>
      </div>
    </div>
    <div class="bfoot">
      <input v-model="basketTitle" placeholder="卷名（可空）" />
      <button class="primary sm" :disabled="!basket.length || basketBusy" @click="makePaper">
        {{ basketBusy ? '生成中…' : `生成试卷（${basket.length} 题 · ${basketTotal} 分）` }}
      </button>
      <button class="ghost sm" :disabled="!basket.length" @click="clearBasket">清空</button>
    </div>
  </aside>

  <div class="qgrid">
    <article v-for="q in items" :key="q.id" class="qcard" :class="{ picked: sel.has(q.id) }"
             draggable="true"
             @dragstart="onCardDragStart(q, $event)"
             @dragover.prevent
             @drop="onCardDrop(q, $event)">
      <div class="q-left">
        <input type="checkbox" :checked="sel.has(q.id)" @change="toggleSel(q.id)" />
        <span class="no">#{{ q.id }}</span>
      </div>
      <div class="q-main">
        <div class="q-meta-line">
          <span class="chip sm2">{{ q.q_type }}</span>
          <span class="chip sm2">{{ DIFF_LABEL[q.difficulty] || '中等' }}</span>
          <span class="chip sm2" :class="{ draft: q.status === 'draft' }">{{ STATUS_LABEL[q.status] || q.status }}</span>
          <span class="src">
            {{ q.book_title }}<template v-if="q.unit_name"> · {{ q.unit_name }}</template>
            <template v-if="q.source_title"> · {{ q.source_title }}</template>
            <template v-if="q.source_page"> · {{ q.source_page }}</template>
          </span>
          <span v-if="q.set_name" class="chip sm2 setchip">{{ q.set_name }}</span>
        </div>
        <div v-if="stemImg(q)" class="qfigs">
          <img :src="imgUrl(stemImg(q).path)" alt="题图" @click="zoomImg = imgUrl(stemImg(q).path)" />
        </div>
        <div v-if="answerImgs(q).length" class="ansfigs direct">
          <span class="alabel">答案图</span>
          <img v-for="(im, i) in answerImgs(q)" :key="'a' + i" :src="imgUrl(im.path)" alt="答案图"
               @click="zoomImg = imgUrl(im.path)" />
        </div>
        <template v-else>
          <div class="q-stem" v-html="renderMath(q.stem) || '<i class=muted>（无题干）</i>'"></div>
          <div v-if="q.options && q.options.length" class="qopts">
            <span v-for="(o, i) in q.options" :key="i">{{ o }}</span>
          </div>
        </template>
        <div class="q-tags">
          <span v-for="t in q.tags" :key="t" class="chip sm2" @click="pickTag(t)">{{ t }}</span>
        </div>
        <div class="q-actions">
          <button class="ghost sm" @click="toggleAnswer(q.id)">
            {{ openAnswers.has(q.id) ? '收起答案' : '查看参考答案' }}
          </button>
          <button class="ghost sm" @click="trimBlank(q)" title="按版面重渲染，裁掉下方空白">裁空白</button>
        <button class="ghost sm" :disabled="inBasket(q.id)" @click="addToBasket(q)">
          {{ inBasket(q.id) ? '已在篮中' : '+ 试题篮' }}
        </button>
        <button class="ghost sm" @click="openWrong(q)">+ 错题集</button>
        <button class="ghost sm" @click="openVersions(q)">历史</button>
          <router-link class="ghost sm" :to="{ path: '/author', query: { id: q.id } }">编辑</router-link>
          <button class="ghost sm danger" @click="deleteOne(q.id)">删除</button>
        </div>
        <div v-if="openAnswers.has(q.id)" class="answerbox">
          <div class="ans" v-html="renderMath(q.answer) || '<i class=muted>（答案见上方答案图）</i>'"></div>
          <div v-if="q.analysis" class="ana" v-html="'解析：' + renderMath(q.analysis)"></div>
        </div>
      </div>
    </article>
  </div>
  </div>

  <p v-if="!loading && !items.length" class="muted" style="padding:18px 0">
    还没有题目。可以点右上「从 QuizForge 导入」，或去「文献出题」自己裁一道。
  </p>

  <div class="pager" v-if="pages > 1">
    <button :disabled="page <= 1" @click="goPage(-1)">上一页</button>
    <span class="muted">第 {{ page }} / {{ pages }} 页</span>
    <button :disabled="page >= pages" @click="goPage(1)">下一页</button>
  </div>

    </main>
  </div>

  <!-- 图片放大 -->
  <div v-if="zoomImg" class="zoom" @click="zoomImg = ''">
    <img :src="zoomImg" alt="放大" />
  </div>

  
  <!-- 组卷（原「组卷」页并入题库） -->
  <div v-if="showPaper" class="modal" @click.self="showPaper = false">
    <div class="modal-card" style="max-width:760px">
      <h3>用所选 {{ paperPool.length }} 题组卷</h3>
      <p class="muted" style="font-size:12.5px">
        按下面的顺序出卷；可上下调整顺序、移除某题，分值按题型统一设置。生成后会打开试卷预览。
      </p>
      <label class="f"><span>卷名</span><input v-model="paperMeta.title" /></label>
      <div class="frow" style="margin:8px 0 10px">
        <label class="f" style="flex:0 0 160px"><span>考试时长（分钟）</span>
          <input v-model.number="paperMeta.duration_minutes" type="number" min="10" />
        </label>
        <span class="chip brand" style="align-self:flex-end;margin-bottom:2px">合计 {{ paperTotal }} 分</span>
      </div>
      <div style="max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:10px;padding:6px 8px">
        <div v-for="(q, i) in paperPool" :key="q.id"
             style="display:flex;align-items:center;gap:8px;padding:6px 2px;border-bottom:1px dashed var(--line)">
          <span class="muted" style="width:22px;text-align:right">{{ i + 1 }}</span>
          <span class="chip sm2">{{ q.q_type }}</span>
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ paperStem(q) }}</span>
          <button class="ghost sm" title="上移" @click="movePaper(i, -1)">↑</button>
          <button class="ghost sm" title="下移" @click="movePaper(i, 1)">↓</button>
          <button class="ghost sm danger" @click="removePaper(i)">移除</button>
        </div>
        <p v-if="!paperPool.length" class="muted" style="padding:12px 2px">题目都被移除了，关闭后重新勾选。</p>
      </div>
      <div class="frow" style="margin-top:12px;flex-wrap:wrap;gap:8px 14px">
        <span class="muted">每题分值：</span>
        <label v-for="t in paperTypes" :key="t" style="display:flex;align-items:center;gap:6px">
          <span class="chip sm2">{{ t }}</span>
          <input v-model.number="paperScores[t]" type="number" min="0" style="width:64px" />
        </label>
      </div>
      <div class="btnrow" style="margin-top:14px">
        <button class="primary" :disabled="paperBusy || !paperPool.length" @click="createPaper">
          {{ paperBusy ? '生成中…' : `生成试卷（${paperPool.length} 题 · ${paperTotal} 分）` }}
        </button>
        <button class="ghost" @click="showPaper = false">取消</button>
      </div>
    </div>
  </div>

  <!-- 自动组卷（原「组卷」页的自动模式） -->
  <div v-if="showAuto" class="modal" @click.self="showAuto = false">
    <div class="modal-card" style="max-width:760px">
      <h3>按教材自动组卷</h3>
      <p class="muted" style="font-size:12.5px">
        选课本纲和单元范围，按题型填需要的数量，系统从已确认题里抽题。题干相同的题会去重。
      </p>
      <p v-if="!autoBooks.length" class="muted" style="padding:10px 0">
        还没有导入「课本纲」。先到「资料库」导入一本课本（role=textbook），再回来自动组卷；
        也可以直接勾选题目用「用所选组卷」。
      </p>
      <template v-else>
        <label class="f"><span>课本纲</span>
          <select v-model.number="autoBookId" @change="loadAutoBook">
            <option v-for="b in autoBooks" :key="b.id" :value="b.id">
              {{ b.title }}（{{ b.subject }} · {{ b.grade }} 年级 · {{ b.semester }}）
            </option>
          </select>
        </label>
        <div class="frow" style="margin:10px 0;flex-wrap:wrap;gap:8px 14px">
          <label class="f" style="flex:0 0 170px"><span>起始单元</span>
            <select v-model.number="autoMeta.unit_from">
              <option v-for="u in autoUnits" :key="u.id" :value="u.seq">{{ u.seq }} · {{ u.name }}</option>
            </select>
          </label>
          <label class="f" style="flex:0 0 170px"><span>结束单元</span>
            <select v-model.number="autoMeta.unit_to">
              <option v-for="u in autoUnits" :key="u.id" :value="u.seq">{{ u.seq }} · {{ u.name }}</option>
            </select>
          </label>
          <label class="f" style="flex:0 0 120px"><span>时长（分钟）</span>
            <input v-model.number="autoMeta.duration_minutes" type="number" min="10" />
          </label>
          <label class="chk" style="align-self:flex-end;margin-bottom:8px">
            <input v-model="autoMeta.include_extension" type="checkbox" /> 包含提优题
          </label>
        </div>
        <label class="f"><span>卷名</span><input v-model="autoMeta.title" /></label>
        <div class="frow" style="margin:10px 0;flex-wrap:wrap;gap:8px 14px">
          <span class="muted">按题型抽题：</span>
          <label v-for="t in autoTypes" :key="t" style="display:flex;align-items:center;gap:6px">
            <span class="chip sm2">{{ t }}</span>
            <input v-model.number="autoCounts[t]" type="number" min="0" style="width:64px" title="数量" />
            <span class="muted" style="font-size:12px">可用 {{ autoStats[t] || 0 }}</span>
            <input v-model.number="autoScores[t]" type="number" min="1" style="width:56px" title="每题分值" />
            <span class="muted" style="font-size:12px">分/题</span>
          </label>
        </div>
        <div class="btnrow" style="margin-top:14px">
          <span class="chip brand">合计 {{ autoTotal }} 分</span>
          <button class="primary" :disabled="autoBusy || !autoTotal" @click="createAuto">
            {{ autoBusy ? '生成中…' : '生成自动组卷' }}
          </button>
          <button class="ghost" @click="showAuto = false">取消</button>
        </div>
      </template>
    </div>
  </div>

  <!-- 归入整卷 -->
  <div v-if="showSets" class="modal" @click.self="showSets = false">
    <div class="modal-card">
      <h3>把所选 {{ selList.length }} 题归入整卷</h3>
      <p class="muted" style="font-size:12.5px">
        整卷相当于「某一届 / 某一份卷」，方便按卷浏览、整卷勾选和整卷加入组卷。
      </p>
      <div v-if="sets.length" class="setpick">
        <button v-for="st in sets" :key="st.id" class="ghost" @click="doAssign(st.id)">
          {{ st.name }}<span class="muted">（{{ st.count }} 题）</span>
        </button>
      </div>
      <p v-else class="muted">还没有整卷，下面新建一个。</p>
      <div class="frow" style="margin-top:10px">
        <input v-model="newSet.name" placeholder="新卷名，如：2026 秋季期中 A 卷" style="flex:1 1 260px" />
        <input v-model="newSet.year" type="number" placeholder="年份" style="width:110px" />
        <button class="primary" :disabled="newSet.busy" @click="createSetAndAssign">新建并归入</button>
      </div>
      <div class="btnrow" style="margin-top:12px">
        <button class="ghost" @click="showSets = false">取消</button>
      </div>
    </div>
  </div>

  <!-- 版本历史 -->
  <div v-if="vers" class="modal" @click.self="vers = null">
    <div class="modal-card">
      <h3>#{{ vers.q.id }} 的版本历史</h3>
      <p class="muted" style="font-size:12.5px">
        每次编辑前都会自动留一版快照；回滚会把当前状态再存一版，回滚本身也能反悔。
      </p>
      <div v-if="vers.busy" class="muted">加载中…</div>
      <div v-else-if="!vers.items.length" class="muted">还没有历史版本（尚未编辑过）。</div>
      <table v-else class="vtable">
        <thead><tr><th>版本</th><th>时间</th><th>说明</th><th></th></tr></thead>
        <tbody>
          <tr v-for="v in vers.items" :key="v.id">
            <td>v{{ v.version_no }}</td>
            <td class="muted">{{ fmtTime(v.created_at) }}</td>
            <td>{{ v.note || '—' }}</td>
            <td><button class="ghost sm" @click="restoreVersion(v)">回滚到此版</button></td>
          </tr>
        </tbody>
      </table>
      <div class="btnrow" style="margin-top:12px">
        <button class="ghost" @click="vers = null">关闭</button>
      </div>
    </div>
  </div>

  <!-- 导入整卷 -->
  <div v-if="showImport" class="modal" @click.self="showImport = false">
    <div class="modal-card">
      <h3>导入整卷（PDF，可多份）</h3>
      <p class="muted" style="font-size:12.5px">
        可一次选「题目卷 + 答案卷」两份（最多 8 份），系统逐页取文字（扫描页自动 OCR），
        交给 AI 按题号把题目和答案配对、拆题并归类。中文文件名可以直接选。
      </p>
      <div class="btnrow">
        <input v-model="paperImp.title" placeholder="试卷名称（可空，默认用文件名）" style="max-width:240px" />
      </div>
      <div class="pdftray" :class="{ hot: paperImp.busy }" @dragover.prevent @drop.prevent="paperDrop">
        <label class="upload sm-upload">
          <input type="file" accept=".pdf,application/pdf" multiple @change="uploadPaper" />
          <span>{{ paperImp.busy ? '识别/拆题中（可能需要 1-2 分钟）…' : '选择 PDF，或把题目卷/答案卷拖到这里' }}</span>
        </label>
        <span class="muted">支持多份：题目卷和答案卷可以分开；也可只传题目+答案合卷。</span>
      </div>
      <div v-if="paperImp.err" class="errbox">{{ paperImp.err }}</div>
      <div v-if="paperImp.result" class="okbox">
        已导入「{{ paperImp.result.title }}」：新增 {{ paperImp.result.imported }} 题，跳过重复
        {{ paperImp.result.skipped }} 题；归类专题：{{ (paperImp.result.tags || []).join('、') || '未识别' }}
        <template v-if="paperImp.result.files">（{{ paperImp.result.files.join(' + ') }}）</template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sortrow { gap: 8px; align-items: center; }
.sortrow select { width: auto; min-width: 168px; }
.qcard[draggable="true"] { cursor: grab; }
.qwrap { display: block; }
.qwrap.withb { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 12px; align-items: start; }
.qwrap.withb > .qgrid { grid-column: 1; grid-row: 1; }
.qwrap.withb > .basket { grid-column: 2; grid-row: 1; }
.basket {
  position: sticky; top: 12px; background: #fff; border: 1px solid #dbe3ef;
  border-radius: 12px; box-shadow: 0 8px 24px rgba(15, 23, 42, .08);
  padding: 10px 12px; max-height: calc(100vh - 120px); overflow: auto;
}
.bhead, .brow1, .brow2, .bfoot { display: flex; align-items: center; gap: 6px; }
.bhead { margin-bottom: 8px; }
.bitem { border: 1px solid #eef1f6; border-radius: 8px; padding: 6px; margin-bottom: 6px; background: #fbfcfe; }
.brow1 { gap: 6px; }
.bno { width: 18px; height: 18px; border-radius: 50%; background: #eef3ff; color: #2f6fed;
  font-size: 11.5px; display: inline-flex; align-items: center; justify-content: center; }
.bstem { flex: 1; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bitem .x { border: 0; background: transparent; cursor: pointer; color: #98a2b3; font-size: 15px; }
.brow2 { margin-top: 4px; gap: 4px; flex-wrap: wrap; }
.bscore { width: 54px; }
.bnum { width: 64px; }
.bsel { width: 84px; font-size: 12px; }
.bcolor { width: 28px; height: 24px; padding: 0; border: 1px solid #d1d5db; border-radius: 6px; }
.bfoot { margin-top: 8px; gap: 6px; }
.bfoot input { flex: 1; }
.tiny { font-size: 11.5px; }
.q-toolbar { display: flex; flex-direction: column; gap: 10px; }
.searchrow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.searchrow .search { flex: 1 1 320px; min-width: 240px; }
.spacer { flex: 1; }
.topics { display: flex; flex-wrap: wrap; gap: 8px; }
.topics .chip { cursor: pointer; }
.topics .chip.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.frow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.frow select { width: auto; min-width: 120px; }

.filterrow { padding: 2px 0 4px; }
.selbar.slim { padding: 6px 10px; }
.selbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 10px 0; }
.chk { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }

.qgrid { display: flex; flex-direction: column; gap: 10px; }
.qcard {
  display: flex; gap: 14px; padding: 18px 20px; background: #fff;
  border: 1px solid var(--line); border-radius: 12px;
}
.qcard.picked { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.q-left { display: flex; gap: 8px; align-items: flex-start; padding-top: 2px; }
.q-left .no { font-size: 12px; color: #98a2b3; }
.q-main { flex: 1; min-width: 0; }
.q-meta-line { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.src { font-size: 12px; color: #8a94a6; }
.chip.draft { background: #fff7e6; border-color: #ffd8a8; color: #b26a00; }
.q-stem { font-size: 16px; line-height: 1.78; color: #1f2937; }
.q-stem :deep(p) { margin: 0 0 4px; }
.ansfigs { display: flex; gap: 10px; flex-wrap: wrap; margin: 8px 0 2px; }
.ansfigs.direct { flex-direction: column; align-items: flex-start; gap: 6px; margin-top: 8px; }
.ansfigs .alabel {
  align-self: flex-start; font-size: 12px; color: #667085; background: #f2f4f7;
  border-radius: 6px; padding: 3px 8px; white-space: nowrap;
}
.ansfigs img {
  max-width: 420px; max-height: 320px; border: 1px solid var(--line);
  border-radius: 8px; background: #fff; cursor: zoom-in;
}
.qfigs { margin: 10px 0 6px; }
.qfigs img {
  width: auto; height: auto; max-width: 100%; max-height: 520px;
  border: 1px solid var(--line); border-radius: 8px; cursor: zoom-in; background: #fff;
}
.qopts { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13.5px; color: #374151; margin: 4px 0; }
.q-tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 4px; }
.q-tags .chip { cursor: pointer; }
.xmask { position: fixed; inset: 0; background: rgba(15,23,42,.45); display: flex; align-items: center; justify-content: center; z-index: 75; }
.xcard { width: min(920px, 92vw); max-height: 88vh; overflow: auto; background: #fff; border-radius: 12px; padding: 14px 18px; box-shadow: 0 18px 50px rgba(15,23,42,.25); }
.xhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.xhead h3 { margin: 0; font-size: 15px; }
.modal-mask { position: fixed; inset: 0; background: rgba(17,24,39,.35); display: flex; align-items: center; justify-content: center; z-index: 60; }
.modal { background: #fff; border-radius: 12px; padding: 16px 18px; width: min(480px, 92vw); box-shadow: 0 20px 50px rgba(0,0,0,.2); }
.modal h3 { margin: 0 0 10px; font-size: 15px; }
.wrow { display: flex; gap: 8px; }
.wrow input { flex: 1; }
.wlist { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.wfoot { display: flex; justify-content: flex-end; }
.q-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; align-items: center; }
.q-actions .sm { padding: 5px 11px; font-size: 12.5px; }
.q-actions .ghost { text-decoration: none; }
.answerbox {
  margin-top: 8px; padding: 10px 12px; background: #f8fafc;
  border: 1px dashed var(--line); border-radius: 8px; font-size: 13.5px;
}
.answerbox .ans { color: #111827; }
.answerbox .ana { color: #6b7280; margin-top: 6px; }

.pager { display: flex; gap: 10px; align-items: center; justify-content: center; margin: 16px 0; }
.zoom {
  position: fixed; inset: 0; background: rgba(15, 23, 42, .82);
  display: flex; align-items: center; justify-content: center; z-index: 80; cursor: zoom-out;
}
.zoom img { max-width: 92vw; max-height: 92vh; box-shadow: 0 12px 40px rgba(0,0,0,.45); }
.modal {
  position: fixed; inset: 0; background: rgba(15, 23, 42, .45);
  display: flex; align-items: center; justify-content: center; z-index: 70;
}
.modal-card {
  width: min(620px, 92vw); background: #fff; border-radius: 12px; padding: 18px 20px;
  box-shadow: 0 18px 50px rgba(15, 23, 42, .25); max-height: 88vh; overflow: auto;
}
.okbox { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; padding: 8px 12px; border-radius: 8px; }
.qfpath { font-size: 12.5px; margin: 8px 0; word-break: break-all; }
.qfbank { display: flex; flex-direction: column; gap: 6px; margin: 8px 0 12px; }
.sm-upload { cursor: pointer; }
.sm-upload input { display: none; }
.sm-upload span {
  display: inline-block; padding: 7px 12px; border: 1px dashed var(--line);
  border-radius: 8px; font-size: 13px; color: #475467;
}
</style>

<style scoped>
.setlabel { font-size: 12px; color: #8a94a6; align-self: center; }
.topics.sets .chip .cnt {
  margin-left: 6px; padding: 0 5px; border-radius: 8px;
  background: rgba(0, 0, 0, 0.06); font-size: 11px;
}
.topics.sets .chip.on .cnt { background: rgba(255, 255, 255, 0.25); }
.chip.setchip { background: #eef2ff; border-color: #dbe3ff; color: #3b5bdb; }
.setpick { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.setpick .ghost { text-align: left; }
.vtable { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
.vtable th, .vtable td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }
.vtable th { color: #8a94a6; font-weight: 600; font-size: 12px; }
</style>

<style scoped>
.bank { display: grid; grid-template-columns: 246px minmax(0, 1fr); gap: 16px; align-items: start; }
.bank.collapsed { grid-template-columns: 26px minmax(0, 1fr); }
.side-tab {
  width: 26px; height: 76px; border-radius: 8px; border: 1px solid var(--line);
  background: #fff; color: #667085; cursor: pointer; padding: 0; font-size: 15px;
}
.side-tab:hover { color: var(--brand); border-color: var(--brand); }
.shead-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.shead-row .ghost.sm { padding: 2px 8px; }
.bank-side {
  position: sticky; top: 16px; display: flex; flex-direction: column; gap: 14px;
  background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px;
}
.s-hint { font-size: 11.5px; margin: 8px 2px 0; }
.sgroup { display: flex; flex-direction: column; gap: 2px; }
.shead { font-size: 12px; color: #8a94a6; padding: 4px 8px 6px; letter-spacing: .02em; }
.sitem {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 7px 9px; border: 1px solid transparent; border-radius: 7px;
  background: transparent; color: #475467; font-size: 13px; text-align: left; cursor: pointer;
}
.sitem:hover { background: #f4f7fb; }
.sitem.on { background: #eef3ff; border-color: #dbe3ff; color: #2f6fed; font-weight: 600; }
.sitem i { font-style: normal; font-size: 11.5px; color: #98a2b3; }
.sitem.on i { color: #4c7ef3; }
.bank-main { min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.bank-main .q-toolbar { margin: 0; }
.pdftray {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  border: 1px dashed #c7d2e4; border-radius: 10px; padding: 12px; background: #f8fbff;
}
.pdftray.hot { border-color: var(--brand); background: #fffbe6; }
.pdftray .muted { font-size: 12.5px; }
button.danger { color: #b42318; border-color: #fecdca; }
button.danger:hover { background: #fef3f2; }
@media (max-width: 1080px) {
  .bank { grid-template-columns: 1fr; }
  .bank-side { position: static; flex-direction: column; }
}
/* 窄屏：分类变胶囊 */
@media (max-width: 760px) {
  .bank-side { max-height: none; padding: 10px 12px; }
  .sgroup { display: flex; flex-direction: row !important; flex-wrap: wrap; gap: 6px; align-items: center; }
  .shead { width: 100%; margin: 6px 0 2px; }
  .sitem { width: auto !important; display: inline-block; padding: 4px 10px; font-size: 12.5px; border-radius: 999px; }
  .s-hint { display: none; }
  .qwrap.withb { grid-template-columns: 1fr; }
  .qwrap.withb > .qgrid { grid-column: 1; grid-row: 1; }
  .qwrap.withb > .basket { grid-column: 1; grid-row: 2; position: static; }
}
</style>
