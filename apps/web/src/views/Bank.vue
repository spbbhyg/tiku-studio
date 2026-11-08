<script setup>
// Chemy 风格题库：左专题 / 中题目目录 / 右阅读区
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { toast } from '../tasks.js'
import QuestionSheet from '../components/QuestionSheet.vue'

const router = useRouter()
const isText = (q) => !!(q && q.layout && q.layout.kind === 'text')
const items = ref([])
const total = ref(0)
const dims = ref([])
const loading = ref(false)
const topic = ref('')          // 当前专题
const source = ref('')         // 全部届次/来源
const type = ref('')           // 全部题型
const q = ref('')
const jump = ref('')
const active = ref(null)       // 当前题目
const showAns = ref(false)
const showTalk = ref(false)
const notes = ref([])
const noteBody = ref('')
const noteBusy = ref(false)
const loadNotes = async () => {
  if (!active.value) { notes.value = []; return }
  try {
    const r = await api.get(`/api/questions/${active.value.id}/notes`)
    notes.value = r.notes || []
  } catch (e) { notes.value = [] }
}
const addNote = async () => {
  const body = noteBody.value.trim()
  if (!body || !active.value) return
  noteBusy.value = true
  try {
    await api.post(`/api/questions/${active.value.id}/notes`, { body })
    noteBody.value = ''
    await loadNotes()
  } catch (e) { msg.value = '留言失败：' + (e.message || e) } finally { noteBusy.value = false }
}
const delNote = async (n) => {
  try {
    await api.del(`/api/questions/notes/${n.id}`)
    await loadNotes()
  } catch (e) { msg.value = '删除失败：' + (e.message || e) }
}
const toggleTalk = () => {
  showTalk.value = !showTalk.value
  if (showTalk.value) loadNotes()
}
const picked = ref([])         // 选入练习卷
const inPicked = (q) => picked.value.some(x => x.id === q.id)
const ansImgs = (q) => {
  const a = (q && q.answer_layout) || {}
  const list = Array.isArray(a.images) ? a.images : []
  return list.map(x => (typeof x === 'string' ? x : x.path)).filter(Boolean)
}
const togglePick = (q) => {
  picked.value = inPicked(q) ? picked.value.filter(x => x.id !== q.id) : [...picked.value, q]
}
/* 试题篮：勾选或拖拽题目进篮子，篮子里点「组卷」直接生成试卷 */
const basketOpen = ref(localStorage.getItem('tiku-basket-open') !== '0')
const dragOver = ref(false)
const basketOpenSet = () => { basketOpen.value = !basketOpen.value; localStorage.setItem('tiku-basket-open', basketOpen.value ? '1' : '0') }
const dragStart = (q, e) => {
  e.dataTransfer.effectAllowed = 'copy'
  e.dataTransfer.setData('text/plain', 'q:' + q.id)
}
const onBasketDrop = (e) => {
  dragOver.value = false
  const raw = e.dataTransfer.getData('text/plain') || ''
  const id = Number(raw.replace(/^q:/, ''))
  if (!id) return
  const q = items.value.find(x => x.id === id)
  if (q && !inPicked(q)) picked.value = [...picked.value, q]
  if (!basketOpen.value) basketOpenSet()
}
const basketAdd = (e, q) => { e.stopPropagation(); if (!inPicked(q)) picked.value = [...picked.value, q] }
const scores = ref({})
const percentMap = ref({})
const scoreOf = (q) => (scores.value[q.id] ?? (Number(q.score) || 5))
const setScore = (q, e) => { const v = Number(e.target.value); if (v > 0) scores.value = { ...scores.value, [q.id]: v } }
// 占比只在导出时显示；留空就由导出按分值自动算
const percentOf = (q) => (percentMap.value[q.id] ?? null)
const setPercent = (q, e) => {
  const raw = String(e.target.value).trim()
  const v = raw === '' ? null : Number(raw)
  percentMap.value = { ...percentMap.value, [q.id]: (v === null || isNaN(v) || v <= 0 || v > 100) ? null : v }
}
const percentSum = computed(() => picked.value.reduce((n, q) => n + (percentOf(q) || 0), 0))
const allPicked = computed(() => items.value.length > 0 && items.value.every(q => inPicked(q)))
const togglePickAll = () => {
  picked.value = allPicked.value ? [] : items.value.slice()
}
const busy = ref(false)
const msg = ref('')
const collapsed = ref({})      // 目录树折叠
const sideOpen = ref(localStorage.getItem('tiku-bank-side') !== '0')   // 左栏（专题）
const dirOpen = ref(localStorage.getItem('tiku-bank-dir') !== '0')     // 中栏（目录）
const resetScroll = () => requestAnimationFrame(() => { const el = document.querySelector('.bread'); if (el) el.scrollTop = 0 })
const toggleSide = () => { sideOpen.value = !sideOpen.value; localStorage.setItem('tiku-bank-side', sideOpen.value ? '1' : '0'); resetScroll() }
const toggleDir = () => { dirOpen.value = !dirOpen.value; localStorage.setItem('tiku-bank-dir', dirOpen.value ? '1' : '0'); resetScroll() }
const cols = computed(() => {
  if (mode.value === 'one') return 'minmax(0, 1fr)'
  if (mode.value === 'two') return `${dirOpen.value ? '320px' : '34px'} minmax(0, 1fr)`
  return `${sideOpen.value ? '200px' : '34px'} ${dirOpen.value ? '300px' : '34px'} minmax(0, 1fr)`
})

const load = async () => {
  loading.value = true
  try {
    const p = new URLSearchParams({ limit: '500', sort: 'added_desc' })
    if (q.value.trim()) p.set('q', q.value.trim())
    if (type.value) p.set('type', type.value)
    const d = await api.get('/api/questions?' + p.toString())
    items.value = d.items || []
    total.value = d.total || items.value.length
    if (mode.value !== 'one' && !active.value && items.value.length) active.value = items.value[0]
  } finally { loading.value = false }
}
const loadDims = async () => {
  try {
    const d = await api.get('/api/wb/categories')
    dims.value = d.dims || []
  } catch (e) { dims.value = [] }
}
const ghSkip = ref([])          // 不发布到公共题库的题 id
const isSkipped = (q) => !!q && ghSkip.value.includes(Number(q.id))
const loadGhSkip = async () => {
  try {
    const d = await api.get('/api/github/status')
    ghSkip.value = (d.skip_ids || []).map(Number)
  } catch (e) { /* 没配 GitHub 时忽略 */ }
}
const toggleSkip = async (q) => {
  if (!q || ghBusy.value) return
  const want = !isSkipped(q)
  ghBusy.value = true
  try {
    await api.post('/api/github/skip', { id: q.id, skip: want })
    const rest = ghSkip.value.filter(x => x !== Number(q.id))
    ghSkip.value = want ? [...rest, Number(q.id)] : rest
    clsMsg.value = want ? `第 ${q.id} 题已排除，不会再发布到公共题库` : `第 ${q.id} 题恢复发布`
    setTimeout(() => { if (clsMsg.value.includes('公共题库')) clsMsg.value = '' }, 4000)
  } catch (e) {
    msg.value = '设置失败：' + (e.message || e)
  } finally { ghBusy.value = false }
}
onMounted(async () => { await Promise.all([load(), loadDims(), loadGhSkip()]) })

const topicValues = computed(() => {
  const dim = dims.value.find(d => d.key === 'topic') || dims.value[0]
  return dim ? dim.values || [] : []
})
// 专题：题目入库时写在 knowledge_points，旧数据才是 tags；两者都认
const kpOf = (x) => {
  const kp = Array.isArray(x.knowledge_points) ? x.knowledge_points : (x.knowledge_points ? [x.knowledge_points] : [])
  const tg = Array.isArray(x.tags) ? x.tags : (x.tags ? String(x.tags).split(',') : [])
  return [...kp, ...tg].map(s => String(s || '').trim()).filter(Boolean)
}
const countOf = (name) => items.value.filter(x => kpOf(x).includes(name)).length
const sources = computed(() => [...new Set(items.value.map(x => (x.source_title || '未标注来源').split('·')[0]))].filter(Boolean))
const types = computed(() => [...new Set(items.value.map(x => x.type).filter(Boolean))])

const filtered = computed(() => items.value.filter(x => {
  if (source.value && !(x.source_title || '').startsWith(source.value)) return false
  if (topic.value && !kpOf(x).includes(topic.value)) return false
  return true
}))
const groups = computed(() => {
  const map = new Map()
  for (const it of filtered.value) {
    const g = (it.source_title || '未标注来源').split('·')[0] || '未标注来源'
    if (!map.has(g)) map.set(g, [])
    map.get(g).push(it)
  }
  return [...map.entries()].map(([name, arr]) => ({ name, arr }))
})
const pickTopic = (t) => { topic.value = topic.value === t ? '' : t }
const pick = (it) => {
  active.value = it; showAns.value = false; showTalk.value = false
  notes.value = []; noteBody.value = ''
  requestAnimationFrame(() => {
    const el = document.querySelector('.bread')
    if (el) el.scrollTop = 0
  })
}

const stemPlain = (it) => String(it.stem || '').replace(/\$[^$]*\$/g, '').replace(/\s+/g, ' ').trim()
const titleOf = (it) => {
  const s = stemPlain(it).replace(/^\s*\d+\s*[.、．)]\s*/, '')
  return s.length > 26 ? s.slice(0, 26) + '…' : s
}
const figs = (it) => {
  const out = []
  if (it.image) out.push(it.image)
  if (Array.isArray(it.images)) out.push(...it.images)
  return out.filter(Boolean).map(f => typeof f === 'string' ? f : (f.path || f.url || ''))
}
const imgUrl = (f) => {
  if (!f) return ''
  const s = String(f)
  if (/^https?:|^data:/.test(s)) return s
  if (s.startsWith('/api/images/')) return s
  return '/api/images/' + s.replace(/^\/+/, '')
}
const opts = (it) => {
  const o = it.options
  if (!o) return []
  if (Array.isArray(o)) return o
  try { const v = JSON.parse(o); return Array.isArray(v) ? v : [] } catch (e) { return String(o).split('\n').filter(Boolean) }
}
const pickedScore = computed(() => picked.value.reduce((s, x) => s + (Number(x.difficultyScore || x.score) || 2), 0))

// 视口自适应：>=1240 三栏；900~1240 两栏（左栏收起，专题挪进目录头）；<900 单栏（目录 ⇄ 阅读）
const vw = ref(window.innerWidth)
const mode = computed(() => (vw.value >= 1240 ? 'three' : vw.value >= 900 ? 'two' : 'one'))
const onResize = () => { vw.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))

// —— AI 拆题（纯截图，不识别文字）——
const splitOpen = ref(false)
const splitBusy = ref(false)
const splitMsg = ref('')
const splitItems = ref([])
const zoom = ref('')
const splitPaper = ref(null)
const splitAnswer = ref(null)
const books = ref([])
const splitBook = ref('')

const ghBusy = ref(false)
const ghMsg = ref('')
const ghTip = ref('')
const updateBank = async () => {
  ghBusy.value = true; ghMsg.value = ''; ghTip.value = ''
  try {
    const r = await api.post('/api/github/pull', {})
    ghMsg.value = r.up_to_date ? '已经是最新的了' : `已更新：新增 ${r.updated} 题（跳过 ${r.skipped} 题）`
    if (!r.up_to_date) { await load(); await loadDims() }
  } catch (e) { ghMsg.value = '更新失败：' + (e.message || e) } finally { ghBusy.value = false }
}

const openSplit = async () => {
  splitOpen.value = true
  splitMsg.value = ''
  splitItems.value = []
  splitPaper.value = null
  splitAnswer.value = null
  if (!books.value.length) {
    try {
      const r = await api.get('/api/books')
      books.value = (r.items || r.books || r || []).map(b => ({ id: b.id, title: b.title }))
      if (!splitBook.value && books.value.length) splitBook.value = books.value[0].id
    } catch (e) { /* 忽略 */ }
  }
}

const doSplit = async () => {
  if (!splitPaper.value) { splitMsg.value = '先选题目卷 PDF'; return }
  splitBusy.value = true
  splitMsg.value = '正在按题号切割并去白边…'
  splitItems.value = []
  try {
    const fd = new FormData()
    fd.append('paper', splitPaper.value)
    if (splitAnswer.value) fd.append('answer', splitAnswer.value)
    const r = await fetch('/api/wb/split-images', { method: 'POST', body: fd }).then(x => x.json())
    if (r.detail) throw new Error(typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail))
    splitItems.value = r.items || []
    splitMsg.value = `切出 ${r.count} 题${r.matched ? '（' + r.matched + ' 题配到答案图）' : ''}`
  } catch (e) {
    splitMsg.value = '拆题失败：' + (e.message || e)
  } finally {
    splitBusy.value = false
  }
}

// —— 批量自动分类：给已入库题目补「专题」（纯图片题先 OCR，9 题约 2~3 分钟）——
const clsBusy = ref(false)
const clsMsg = ref('')
const autoClassify = async () => {
  if (clsBusy.value) return
  clsBusy.value = true
  clsMsg.value = '正在识别与分类…'
  const t0 = Date.now()
  try {
    const r = await api.post('/api/wb/questions/reclassify', {})
    const sec = Math.max(1, Math.round((Date.now() - t0) / 1000))
    clsMsg.value = `已分类 ${r.classified || 0} 题，跳过 ${r.skipped || 0} 题（${sec} 秒）`
    await load()
    await loadDims()
    if (active.value) {
      const fresh = items.value.find(x => x.id === active.value.id)
      if (fresh) active.value = fresh
    }
  } catch (e) {
    clsMsg.value = '自动分类失败：' + (e.message || e)
    msg.value = clsMsg.value
  } finally {
    clsBusy.value = false
  }
}

const splitImport = async () => {
  if (!splitBook.value) { splitMsg.value = '还没选入库书册'; return }  if (!splitItems.value.length) return
  splitBusy.value = true
  try {
    const r = await api.post('/api/wb/split-import', {
      book_id: Number(splitBook.value),
      source_title: (splitPaper.value && splitPaper.value.name || '').replace(/\.pdf$/i, ''),
      items: splitItems.value.map(it => ({ no: it.no, title: it.title, topic: it.topic, paras: it.paras, blocks: it.blocks, q: it.q, a: it.a })),
    })
    splitMsg.value = `已入库 ${r.created} 题（重复的会跳过）`
    await load()
  } catch (e) {
    splitMsg.value = '入库失败：' + (e.message || e)
  } finally {
    splitBusy.value = false
  }
}

const delPicked = async () => {
  if (!picked.value.length) return
  if (!confirm(`删除选中的 ${picked.value.length} 道题？删除后无法恢复。`)) return
  busy.value = true
  msg.value = ''
  try {
    const ids = picked.value.map(x => (typeof x === 'number' ? x : x.id))
    const r = await api.post('/api/wb/questions/delete', { ids })
    msg.value = `已删除 ${r.deleted} 道题`
    picked.value = []
    await load()
  } catch (e) {
    msg.value = '删除失败：' + (e.message || e)
  } finally {
    busy.value = false
  }
}

const makePaper = async () => {
  if (!picked.value.length) return
  busy.value = true; msg.value = ''
  try {
    const body = {
      title: '练习卷·' + new Date().toLocaleDateString('zh-CN'),
      duration_minutes: 60,
      subject: '化学',
      items: picked.value.map(x => ({ question_id: x.id, score: scoreOf(x), percent: percentOf(x), align: 'left', font_size: 10.5, color: '#111827' })),
    }
    const r = await api.post('/api/wb/papers', body)
    router.push('/papers/' + r.id)
  } catch (e) { msg.value = '生成试卷失败：' + (e.message || e) } finally { busy.value = false }
}
const downloadOne = async (it, part = 'questions') => {
  // 单题导出统一走考试渲染器（画布题也支持了）；用 blob 下载，
  // 这样在桌面壳（WebView2）里不会把整个窗口导航走。
  try {
    const r = await fetch(`/api/exam/question/${it.id}/export?part=${part}`)
    if (!r.ok) throw new Error(`导出失败（${r.status}）`)
    const blob = await r.blob()
    const name = blobName(r, `题目${it.id}-${part === 'answers' ? '答案' : '题目'}.pdf`)
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = name
    document.body.appendChild(a)
    a.click()
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove() }, 4000)
  } catch (e) {
    toast(`导出失败：${e.message}`, 'bad', 8000)
  }
}

function blobName(res, fallback) {
  const cd = res.headers.get('content-disposition') || ''
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd)
  if (!m) return fallback
  try { return decodeURIComponent(m[1]) } catch (e) { return fallback }
}
</script>

<template>
  <div class="bank" :class="'m-' + mode" :style="{ gridTemplateColumns: cols }">
    <div class="btop">
    <div v-if="clsMsg" class="cls-msg">{{ clsMsg }}</div>
    <div v-if="ghMsg" class="cls-msg gh-msg">{{ ghMsg }}</div>
    <button class="split-fab" title="AI 拆题：按题号把卷子切成图片（自动去白边），答案也切成图片" @click="openSplit">
      <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7">
        <path d="M4 4h9l4 4v12H4z"/><path d="M13 4v4h4"/><path d="M9 13l6 2.2-2.4 1 .9 2.3-1.7.7-.9-2.3-1.9 1.4z"/>
      </svg>
      <span>拆题</span>
    </button>
    <button class="split-fab cls-fab" :disabled="clsBusy"
            :title="clsMsg || '自动分类：给还没写专题的题补专题（题干/答案关键词，纯图片题先 OCR）；已有专题的题会跳过，不覆盖手改结果'"
            @click="autoClassify">
      <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7">
        <path d="M4 6h6v6H4z"/><path d="M14 12h6v6h-6z"/><path d="M10 9h4a2 2 0 0 1 2 2v4"/>
      </svg>
      <span>{{ clsBusy ? '分类中…' : '自动分类' }}</span>
    </button>
    <button class="split-fab gh-fab" :disabled="ghBusy"
            title="一键更新：从 GitHub 公共题库拉取新题（只拉没见过的题，不改动你已有的题）"
            @click="updateBank">
      <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7">
        <path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/>
      </svg>
      <span>{{ ghBusy ? '更新中…' : '更新题库' }}</span>
    </button>
    </div>
    <!-- 左：专题 -->
    <aside v-if="mode === 'three' && sideOpen" class="bside">
      <div class="bs-h">介绍与使用</div>
      <div class="bs-sec">专题</div>
      <button class="bs-item" :class="{ on: !topic }" @click="topic = ''">
        <span>全部题目</span><em>{{ total }}</em>
      </button>
      <button v-for="v in topicValues" :key="v.id" class="bs-item" :class="{ on: topic === v.name }" @click="pickTopic(v.name)">
        <span>{{ v.name }}</span><em>{{ countOf(v.name) }}</em>
      </button>
      <div class="bs-foot">{{ total }} 道题 · 每页最多 500</div>
      <button class="rail-btn left" title="收起专题栏" @click="toggleSide">‹</button>
    </aside>
    <button v-if="mode === 'three' && !sideOpen" class="railbtn" title="展开专题栏" @click="toggleSide"><span>›</span><i>专题</i></button>

    <!-- 中：目录 -->
    <section v-if="dirOpen" class="bdir" :class="{ 'hide-one': mode === 'one' && active }">
      <div class="bd-h">题目目录 <button class="rail-btn" title="收起目录" @click="toggleDir">‹</button></div>
      <div v-if="mode !== 'three'" class="bd-row">
        <select v-model="topic" class="bd-sel" style="margin:0;flex:1" @change="load">
          <option value="">全部专题</option>
          <option v-for="v in topicValues" :key="v.id" :value="v.name">{{ v.name }}（{{ countOf(v.name) }}）</option>
        </select>
      </div>
      <div class="bd-row">
        <input v-model="jump" class="bd-in" placeholder="输入题号" @keyup.enter="active = filtered.find(x => String(x.id) === jump.trim()) || active" />
        <button class="ghost sm" @click="active = filtered.find(x => String(x.id) === jump.trim()) || active">跳转</button>
      </div>
      <select v-model="source" class="bd-sel">
        <option value="">全部来源</option>
        <option v-for="s in sources" :key="s" :value="s">{{ s }}</option>
      </select>
      <select v-model="type" class="bd-sel" @change="load">
        <option value="">全部题型</option>
        <option v-for="t in types" :key="t" :value="t">{{ t }}</option>
      </select>
      <div class="bd-ct">{{ filtered.length }} 道题 · 点击标题定位原题</div>
      <button class="rail-btn left" title="收起目录" @click="toggleDir">‹</button>
      <div class="bd-tree">
        <div v-for="g in groups" :key="g.name" class="bgrp">
          <button class="bgrp-h" @click="collapsed[g.name] = !collapsed[g.name]">
            <span>{{ collapsed[g.name] ? '▶' : '▼' }}</span>{{ g.name }}<em>{{ g.arr.length }} 题</em>
          </button>
          <div v-show="!collapsed[g.name]" class="bqlist">
            <div v-for="it in g.arr" :key="it.id" class="bq" :class="{ on: active && active.id === it.id, picked: inPicked(it) }"
                 draggable="true" @dragstart="dragStart(it, $event)" @click="pick(it)">
              <input class="bqck" type="checkbox" :checked="inPicked(it)" @click.stop="togglePick(it)" />
              <span><b>第{{ it.id }}题</b> {{ titleOf(it) }}</span>
              <button class="bqadd" :class="{ on: inPicked(it) }" :title="inPicked(it) ? '已在试题篮' : '加入试题篮'"
                      @click="basketAdd($event, it)">{{ inPicked(it) ? '✓' : '＋' }}</button>
            </div>
          </div>
        </div>
        <p v-if="!groups.length && !loading" class="bd-empty">没有匹配的题目</p>
      </div>
    </section>

    <button v-if="mode !== 'one' && !dirOpen" class="railbtn" title="展开目录" @click="toggleDir"><span>›</span><i>目录</i></button>

    <!-- 右：阅读 -->
    <section v-show="mode !== 'one' || active" class="bread" :class="{ 'hide-one': mode === 'one' && !active }">
      <article v-if="active" class="rd">
        <button v-if="mode === 'one'" class="ghost sm bd-back" @click="active = null">← 返回目录</button>
        <QuestionSheet v-if="isText(active)" :doc="active.layout" variant="card" :show-answers="showAns" />
        <div v-else class="rd-body">
          <p v-for="(line, i) in String(active.stem || '').split('\n')" :key="i" class="rd-line">{{ line }}</p>
          <img v-for="(f, i) in figs(active)" :key="'f' + i" :src="imgUrl(f)" class="rd-fig" @error="(e) => e.target.remove()" />
          <ol v-if="opts(active).length" class="rd-opts">
            <li v-for="(o, i) in opts(active)" :key="i">{{ o }}</li>
          </ol>
        </div>

        <button v-if="!isText(active)" class="acc" @click="showAns = !showAns">
          <span>查看参考答案</span><i>{{ showAns ? '⌃' : '⌄' }}</i>
        </button>
        <div v-show="showAns && !isText(active)" class="acc-body">
          <p v-if="active.answer" class="rd-ans">{{ active.answer }}</p>
          <template v-if="ansImgs(active).length">
            <img v-for="(f, i) in ansImgs(active)" :key="'a' + i" :src="imgUrl(f)" class="rd-fig ansfig" @click="zoom = imgUrl(f)" />
          </template>
          <p v-else-if="!active.answer" class="rd-ans">（暂无答案）</p>
          <p v-if="active.analysis" class="rd-ana">解析：{{ active.analysis }}</p>
        </div>
        <button class="acc" @click="toggleTalk">
          <span>讨论与提问{{ notes.length ? `（${notes.length}）` : '' }}</span><i>{{ showTalk ? '⌃' : '⌄' }}</i>
        </button>
        <div v-show="showTalk" class="acc-body">
          <div v-if="!notes.length" class="muted">还没有留言。对这道题的疑问、不同解法、勘误都可以记在这里。</div>
          <div v-for="n in notes" :key="n.id" class="note">
            <div class="note-h">
              <b>{{ n.author }}</b>
              <span class="muted">{{ (n.created_at || '').slice(0, 16).replace('T', ' ') }}</span>
              <button class="ghost sm" @click="delNote(n)">删除</button>
            </div>
            <p class="note-b">{{ n.body }}</p>
          </div>
          <textarea v-model="noteBody" class="note-in" rows="3"
                    placeholder="写点讨论或提问，Ctrl+Enter 发送"
                    @keydown.ctrl.enter.prevent="addNote" />
          <button class="btn sm" :disabled="noteBusy || !noteBody.trim()" @click="addNote">
            {{ noteBusy ? '发送中…' : '发表' }}
          </button>
        </div>

        <div class="rd-meta">
          <div class="rd-tags">
            <span v-for="t in kpOf(active)" :key="t" class="chip">{{ t }}</span>
            <span class="chip gray">第{{ active.id }}题</span>
            <span class="chip gray">{{ active.score || 5 }}分</span>
          </div>
          <h3 class="rd-title">第 {{ active.id }} 题　{{ titleOf(active) }}（{{ active.score || 5 }} 分）</h3>
          <p class="rd-src">来源：{{ active.source_title || '未标注' }}{{ active.source_page ? ' · 第 ' + active.source_page + ' 页' : '' }}</p>
          <div class="rd-act">
            <button class="ghost sm" :class="{ skip: isSkipped(active) }" :disabled="ghBusy"
                    :title="isSkipped(active) ? '这道题被排除在公共题库之外（本地照常使用）' : '点一下可在「立即发布」时排除这道题'"
                    @click="toggleSkip(active)">
              {{ isSkipped(active) ? '公共库：已排除' : '公共库：会发布' }}
            </button>
            <button class="ghost sm" @click="downloadOne(active)">下载本题 PDF</button>
            <button v-if="isText(active)" class="ghost sm" @click="downloadOne(active, 'answers')">下载答案 PDF</button>
          </div>
        </div>
      </article>
      <p v-else class="rd-empty">左侧选一道题开始阅读</p>
    </section>

    <!-- 试题篮：勾选或拖拽题目进来，点「组卷」生成试卷 -->
    <button class="basket-fab" :class="{ over: dragOver, on: picked.length }"
            title="试题篮：把题目拖到这里，或点题目右侧的「＋」"
            @click="basketOpenSet" @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onBasketDrop">
      <span>试题篮</span><b>{{ picked.length }}</b>
    </button>

    <div v-if="basketOpen" class="basket" :class="{ over: dragOver }"
         @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onBasketDrop">
      <div class="basket-h">
        <b>试题篮</b>
        <span class="muted">已选 {{ picked.length }} 题 · 约 {{ pickedScore }} 分<span v-if="percentSum"> · 占比合计 {{ percentSum }}%</span></span>
        <button class="ghost sm" @click="basketOpenSet">收起</button>
      </div>
      <p v-if="!picked.length" class="basket-empty">把左边的题目<b>拖到这里</b>，或点题目右侧的「＋」。</p>
      <div v-else class="basket-list">
        <div v-for="(it, i) in picked" :key="it.id" class="basket-item">
          <span class="bi-n">{{ i + 1 }}</span>
          <span class="bi-t">{{ titleOf(it) }}</span>
          <input class="bi-s" type="number" min="1" step="0.5" :value="scoreOf(it)" title="分值（分）" @input="setScore(it, $event)" />
          <input class="bi-p" type="number" min="1" max="100" step="1" :value="percentOf(it)" placeholder="自动" title="占比（只影响导出显示的「占 X%」；留空=按分值自动算）" @input="setPercent(it, $event)" />
          <button class="bi-x" title="移出试题篮" @click="togglePick(it)">✕</button>
        </div>
      </div>
      <div class="basket-f">
        <button class="ghost sm nb" :disabled="!picked.length" @click="picked = []">清空</button>
        <button class="ghost sm danger" :disabled="!picked.length || busy" @click="delPicked">删除所选</button>
        <button class="primary sm" :disabled="!picked.length || busy" @click="makePaper">{{ busy ? '生成中…' : '组卷' }}</button>
      </div>
    </div>
    <p v-if="msg" class="errbar">{{ msg }}</p>
  </div>

  <div v-if="splitOpen" class="xmask" @click.self="splitOpen = false">
    <div class="xcard">
      <div class="xhead">
        <b>AI 拆题 · 按题号截成图片</b>
        <button class="ghost sm" @click="splitOpen = false">关闭</button>
      </div>
      <p class="xnote">选题目卷（必填）和答案卷（可选）。系统按「第 N 题」标题切图并自动裁掉四周空白，<b>同时把文字层一起存下来</b>（题库里仍按图片显示，导出试卷时按文字排版）；答案卷同样切成图片，按题号配对。</p>
      <div class="xrow">
        <label class="xlab">题目卷</label>
        <input type="file" accept="application/pdf" @change="splitPaper = $event.target.files[0]" />
      </div>
      <div class="xrow">
        <label class="xlab">答案卷（可选）</label>
        <input type="file" accept="application/pdf" @change="splitAnswer = $event.target.files[0]" />
      </div>
      <div class="xrow">
        <label class="xlab">入库到</label>
        <select v-model="splitBook" class="bd-sel" style="margin:0;flex:1">
          <option v-for="b in books" :key="b.id" :value="b.id">{{ b.title }}</option>
        </select>
        <button class="primary sm" :disabled="splitBusy || !splitItems.length" @click="splitImport">全部入库</button>
      </div>
      <div class="xrow">
        <button class="primary" :disabled="splitBusy" @click="doSplit">{{ splitBusy ? '处理中…' : '开始拆题' }}</button>
        <span v-if="splitMsg" class="xmsg">{{ splitMsg }}</span>
      </div>
      <div class="xlist">
        <div v-for="it in splitItems" :key="it.no" class="xitem">
          <div class="xi-h"><b>第 {{ it.no }} 题</b><span class="mut">{{ it.title }}</span></div>
          <div class="xi-body">
            <figure v-if="it.q.length"><img :src="'/api/images/' + it.q[0]" @click="zoom = '/api/images/' + it.q[0]" /><figcaption>题目图 {{ it.q.length }} 张</figcaption></figure>
            <figure v-if="it.a.length"><img :src="'/api/images/' + it.a[0]" @click="zoom = '/api/images/' + it.a[0]" /><figcaption>答案图 {{ it.a.length }} 张</figcaption></figure>
            <div v-if="it.paras && it.paras.length" class="xi-txt">
              <div class="mut">文字层 {{ it.paras.length }} 段 · 图 {{ (it.blocks || []).filter(b => b.t === 'img').length }} 块</div>
              <div class="xi-p">{{ it.paras[0].replace(/<[^>]+>/g, '').slice(0, 120) }}</div>
            </div>
            <div v-else-if="!it.a.length" class="mut">这份没配到答案图</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div v-if="zoom" class="zmask" @click="zoom = ''"><img :src="zoom" class="zimg" /></div>
</template>

<style scoped>
.bank { position: relative; }
.btop { grid-column: 1 / -1; display: flex; align-items: center; justify-content: flex-end; gap: 8px;
  padding: 7px 12px; border-bottom: 1px solid var(--line); background: #fbfdff; min-height: 0; }
.btop .cls-msg { position: static; margin: 0 auto 0 0; max-width: 560px; top: auto; right: auto; z-index: 1; }
.split-fab {
  display: inline-flex; align-items: center; gap: 6px; white-space: nowrap;
  padding: 6px 11px; border-radius: 8px; border: 1px solid var(--brand);
  background: #fff; color: var(--brand); font-size: 12.5px; font-weight: 600; cursor: pointer;
  box-shadow: 0 1px 2px rgba(37,99,235,.08);
}
.split-fab:hover { background: #eff6ff; }
.cls-fab { color: #475569; border-color: #cbd5e1; box-shadow: 0 2px 8px rgba(15,23,42,.10); }
.gh-fab { color: #0f766e; border-color: #99f6e4; box-shadow: 0 2px 8px rgba(13,148,136,.12); }
.gh-fab:hover { background: #f0fdfa; }
.gh-fab:disabled { opacity: .65; cursor: progress; }
.cls-fab:hover { background: #f8fafc; }
.cls-fab:disabled { opacity: .65; cursor: progress; }
.btop .cls-msg.gh-msg { background: #f0fdfa; border-color: #99f6e4; color: #0f766e; }
.cls-msg { position: absolute; top: 100px; right: 14px; z-index: 6; max-width: 320px; padding: 6px 10px;
  border-radius: 8px; background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; font-size: 12px; line-height: 1.5; }
.xmask { position: fixed; inset: 0; background: rgba(15,23,42,.45); display: flex; align-items: center; justify-content: center; z-index: 80; }
.xcard { width: min(980px, 94vw); max-height: 88vh; overflow: auto; background: #fff; border-radius: 12px; padding: 16px 18px; box-shadow: 0 20px 50px rgba(15,23,42,.25); }
.xhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.xnote { margin: 0 0 12px; font-size: 12.5px; color: #64748b; line-height: 1.7; }
.xrow { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.xlab { width: 92px; font-size: 12.5px; color: #475569; }
.xmsg { font-size: 12.5px; color: #0f766e; }
.xlist { margin-top: 8px; display: flex; flex-direction: column; gap: 10px; }
.xitem { border: 1px solid var(--line); border-radius: 10px; padding: 10px; }
.xi-h { display: flex; gap: 10px; align-items: baseline; font-size: 13px; margin-bottom: 8px; }
.xi-h .mut { color: #94a3b8; font-size: 12px; }
.xi-body { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-start; }
.xi-body figure { margin: 0; }
.xi-body img { height: 150px; border: 1px solid var(--line); border-radius: 6px; background: #fff; cursor: zoom-in; }
.xi-body figcaption { font-size: 11.5px; color: #94a3b8; text-align: center; margin-top: 4px; }
.zmask { position: fixed; inset: 0; background: rgba(15,23,42,.6); display: flex; align-items: center; justify-content: center; z-index: 90; }
.zimg { max-width: 92vw; max-height: 92vh; background: #fff; border-radius: 8px; }
.bank { display: grid; grid-template-rows: auto minmax(0, 1fr); grid-template-columns: 200px 300px minmax(0, 1fr); gap: 0; height: calc(100vh - 74px); min-width: 0; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: #fff; }
.bank.m-two { }
.bank.m-one { height: calc(100vh - 74px); }
.bank .hide-one { display: none; }
.railbtn { border: 0; border-right: 1px solid var(--line); background: #fafcff; color: #94a3b8; cursor: pointer; font-size: 13px; padding: 12px 0 0; height: 100%; width: 100%; display: flex; align-items: flex-start; justify-content: center; padding-top: 14px; font-size: 16px; }
.railbtn:hover { background: #eef4ff; color: var(--brand); }
.railbtn i { font-style: normal; writing-mode: vertical-rl; letter-spacing: 2px; font-size: 11px; margin-top: 6px; color: #b6c2d4; }
.railbtn:hover { background: #eef4ff; color: var(--brand); }
.rail-btn { border: 0; background: none; color: #cbd5e1; cursor: pointer; font-size: 14px; line-height: 1; padding: 2px 4px; border-radius: 4px; }
.rail-btn:hover { background: #eef4ff; color: var(--brand); }
.rail-btn.left { position: absolute; right: 4px; top: 6px; }
.bside, .bdir { position: relative; }
.bank.m-one .bdir, .bank.m-one .bread { border-right: 0; }
.bd-back { margin-bottom: 10px; }
.bside { border-right: 1px solid var(--line); padding: 10px 8px; overflow: auto; background: #fcfdff; }
.bs-h { font-size: 11px; color: #94a3b8; padding: 4px 8px 10px; }
.bs-sec { font-size: 10.5px; color: #94a3b8; letter-spacing: .1em; padding: 10px 8px 4px; }
.bs-item { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 6px; padding: 6px 8px; border: 0; background: none; border-radius: 5px; font-size: 12.5px; color: #334155; cursor: pointer; text-align: left; }
.bs-item em { font-style: normal; color: #9aa6b8; font-size: 11px; }
.bs-item:hover { background: #f1f5fb; }
.bs-item.on { background: #e8f5ec; color: #166534; font-weight: 600; }
.bs-item.muted { color: #94a3b8; cursor: default; }
.bs-foot { margin-top: 14px; padding: 8px; font-size: 11px; color: #94a3b8; border-top: 1px dashed var(--line); }
.bdir { border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; min-width: 0; overflow-x: hidden; }
.bd-h { padding: 10px 12px; font-weight: 700; font-size: 13.5px; border-bottom: 1px solid var(--line); }
.bd-row { display: flex; gap: 6px; padding: 10px 12px 6px; }
.bd-in { flex: 1; min-width: 0; height: 30px; border: 1px solid var(--line); border-radius: 6px; padding: 0 8px; font-size: 12px; }
.bd-sel { margin: 0 12px 6px; width: calc(100% - 24px); height: 30px; border: 1px solid var(--line); border-radius: 6px; font-size: 12px; background: #fff; box-sizing: border-box; }
.bd-ct { padding: 2px 12px 8px; font-size: 11px; color: #94a3b8; }
.bd-tree { flex: 1; overflow: auto; padding: 0 6px 12px; }
.bgrp-h { width: 100%; display: flex; align-items: center; gap: 6px; padding: 6px 8px; border: 0; background: none; font-size: 12.5px; color: #0f172a; font-weight: 600; cursor: pointer; }
.bgrp-h em { margin-left: auto; font-style: normal; font-weight: 400; color: #94a3b8; font-size: 11px; }
.bqlist { padding-left: 14px; }
.bq { display: flex; gap: 6px; align-items: flex-start; width: 100%; text-align: left; padding: 6px 8px; border-radius: 5px; font-size: 12px; color: #334155; cursor: pointer; line-height: 1.5; }
.bqck { margin-top: 2px; flex: 0 0 auto; }
.bq.picked { background: #eef4ff; }
.bd-pickrow { display: flex; align-items: center; gap: 8px; padding: 0 12px 6px; }
.bd-pickrow .bd-count { margin: 0; padding: 0; flex: 1; }
.bd-pickrow .ghost.xs { padding: 1px 8px; font-size: 11.5px; }
.bq b { color: #1e3a8a; font-weight: 600; margin-right: 4px; }
.bq:hover { background: #f1f5fb; }
.bq.on { background: #e8f5ec; color: #166534; }
.bd-empty { font-size: 12px; color: #94a3b8; padding: 12px; }
.bread { overflow: auto; min-width: 0; padding: 18px 22px 90px; }
.rd-body { font-size: 13.5px; line-height: 1.9; color: #0f172a; }
.rd-line { margin: 0 0 6px; }
.rd-fig { max-width: 100%; max-height: 46vh; object-fit: contain; display: block; margin: 8px 0; }
.rd-opts { margin: 8px 0 0 18px; padding: 0; }
.rd-opts li { margin: 2px 0; }
.acc { width: 100%; display: flex; justify-content: space-between; align-items: center; background: none; border: 0; border-top: 1px solid var(--line); padding: 12px 2px; font-size: 12.5px; color: #334155; cursor: pointer; }
.acc i { font-style: normal; color: #94a3b8; }
.acc-body { padding: 0 2px 12px; font-size: 12.5px; line-height: 1.8; }
.acc-body.muted { color: #94a3b8; }
.rd-fig.ansfig { max-width: 100%; height: auto; cursor: zoom-in; border: 1px solid var(--line); border-radius: 6px; margin: 6px 0; }
.rd-ans { white-space: pre-wrap; }
.rd-ana { color: #475569; }
.rd-meta { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin-top: 14px; background: #fcfdff; }
.rd-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #eef4ff; color: #2563eb; }
.chip.gray { background: #f1f5f9; color: #64748b; }
.rd-title { margin: 2px 0 4px; font-size: 15px; }
.rd-src { margin: 0 0 10px; font-size: 11.5px; color: #94a3b8; }
.rd-act { display: flex; align-items: center; gap: 10px; }
.pickbar .danger { color: #b91c1c; border-color: #fecaca; }
.pickbar { position: fixed; left: 50%; bottom: 18px; max-width: 92vw; flex-wrap: wrap; transform: translateX(-50%); display: flex; align-items: center; gap: 10px; background: #fff; border: 1px solid var(--line); box-shadow: 0 10px 30px rgba(15, 23, 42, .12); border-radius: 999px; padding: 8px 14px; font-size: 12.5px; z-index: 30; }
.errbar { position: fixed; left: 50%; bottom: 66px; transform: translateX(-50%); background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; padding: 6px 12px; border-radius: 8px; font-size: 12px; }
.rd-empty { color: #94a3b8; font-size: 13px; }
.note { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; margin: 0 0 8px; background: #fff; }
.note-h { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: #64748b; }
.note-h b { color: #334155; font-size: 12px; }
.note-h button { margin-left: auto; }
.note-b { margin: 4px 0 0; white-space: pre-wrap; }
.note-in { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 12.5px; margin: 4px 0 8px; resize: vertical; }

.rd-act .ghost.skip { color:#b45309; border-color:#f59e0b; background:#fffbeb }

/* 试题篮 */
.bqadd { margin-left: auto; flex: none; width: 20px; height: 20px; line-height: 1; border-radius: 6px; border: 1px solid transparent; background: transparent; color: #94a3b8; font-size: 13px; cursor: pointer; }
.bqadd:hover { background: #eef2ff; color: #2563eb; border-color: #c7d2fe; }
.bqadd.on { color: #16a34a; }
.basket-fab { position: fixed; right: 18px; bottom: 18px; z-index: 32; display: flex; align-items: center; gap: 8px; padding: 9px 14px; border-radius: 999px; border: 1px solid var(--line); background: #fff; box-shadow: 0 10px 26px rgba(15, 23, 42, .14); font-size: 13px; cursor: pointer; }
.basket-fab.on { border-color: #bfdbfe; background: #eff6ff; color: #1d4ed8; }
.basket-fab.over { border-color: #2563eb; background: #dbeafe; }
.basket-fab b { min-width: 20px; height: 20px; border-radius: 999px; background: #2563eb; color: #fff; font-size: 11.5px; display: inline-flex; align-items: center; justify-content: center; padding: 0 5px; }
.basket-fab.on b { background: #1d4ed8; }
.basket { position: fixed; right: 18px; bottom: 70px; z-index: 33; width: 330px; max-height: 62vh; display: flex; flex-direction: column; background: #fff; border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 18px 46px rgba(15, 23, 42, .18); }
.basket.over { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .16), 0 18px 46px rgba(15, 23, 42, .18); }
.basket-h { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.basket-h .muted { font-size: 11.5px; margin-right: auto; }
.basket-empty { margin: 0; padding: 18px 14px; color: #94a3b8; font-size: 12.5px; line-height: 1.7; text-align: center; }
.basket-list { overflow: auto; padding: 6px; }
.basket-item { display: flex; align-items: center; gap: 7px; padding: 6px 7px; border-radius: 8px; }
.basket-item:hover { background: #f8fafc; }
.bi-n { width: 18px; flex: none; color: #94a3b8; font-size: 11.5px; text-align: right; }
.bi-t { flex: 1; min-width: 0; font-size: 12.5px; color: #334155; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.basket-foot button.nb, .basket-foot > button { white-space: nowrap; padding: 7px 10px; }
.xi-txt { margin-top: 6px; }
.xi-p { font-size: 12px; color: #475569; line-height: 1.6; margin-top: 3px; }
.bi-p { width: 46px; flex: none; padding: 3px 4px; border: 1px solid #dbe3ee; border-radius: 6px; font-size: 11.5px; text-align: center; color: #475569; }
.bi-p::placeholder { color: #cbd5e1; }
.bi-s { width: 52px; flex: none; padding: 3px 5px; border: 1px solid #dbe3ee; border-radius: 6px; font-size: 12px; text-align: center; }
.bi-x { flex: none; width: 20px; height: 20px; border: 0; background: transparent; color: #cbd5e1; cursor: pointer; border-radius: 6px; }
.bi-x:hover { background: #fef2f2; color: #dc2626; }
.basket-f { display: flex; gap: 6px; padding: 9px 10px; border-top: 1px solid var(--line); }
.basket-f .primary { margin-left: auto; }
</style>
