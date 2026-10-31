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
const qf = reactive({ found: false, db_path: '', banks: [], hint: '', busy: '', result: null, picked: [] })

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

function goCompose() {
  if (!sel.value.size) return
  router.push({ path: '/compose', query: { ids: selList.value.join(',') } })
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

// ---------------------------------------------------------------- QuizForge 导入
async function openImport() {
  showImport.value = true
  qf.result = null
  qf.busy = ''
  try {
    const data = await api.get('/api/import/quizforge')
    qf.found = data.found
    qf.db_path = data.db_path || ''
    qf.banks = data.banks || []
    qf.hint = data.hint || ''
    qf.picked = qf.banks.map((b) => b.id)
  } catch (e) { qf.hint = e.message }
}

async function runImport() {
  qf.busy = 'path'
  err.value = ''
  try {
    const data = await api.post('/api/import/quizforge', { bank_ids: qf.picked })
    qf.result = data
    msg.value = `导入完成：新增 ${data.questions_created} 题，补全 ${data.questions_updated} 题`
    showImport.value = false
    await Promise.all([load(), loadTopics()])
  } catch (e) { err.value = e.message }
  qf.busy = ''
}

async function uploadDb(event) {
  const file = event.target.files && event.target.files[0]
  if (!file) return
  qf.busy = 'file'
  err.value = ''
  const fd = new FormData()
  fd.append('file', file)
  try {
    const data = await api.postForm('/api/import/quizforge/upload', fd)
    qf.result = data
    msg.value = `导入完成：新增 ${data.questions_created} 题，补全 ${data.questions_updated} 题`
    showImport.value = false
    await load()
  } catch (e) { err.value = e.message }
  qf.busy = ''
  event.target.value = ''
}

onMounted(async () => {
  await Promise.all([loadTopics(), loadQtypes(), loadSets()])
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
      <div class="sgroup">
        <div class="shead">专题</div>
        <button class="sitem" :class="{ on: !activeTag }" @click="activeTag = ''; search()">全部题目</button>
        <button v-for="t in topicTags" :key="t.id" class="sitem"
                :class="{ on: activeTag === t.name }" @click="pickTag(t.name)">{{ t.name }}</button>
      </div>
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

    <main class="bank-main">

  <div class="card q-toolbar">
    <div class="searchrow">
      <input v-model="f.q" class="search" placeholder="搜索题干、答案、文献名…（回车）" @keyup.enter="search" />
      <button class="primary" @click="search">查询</button>
      <button @click="reset">重置</button>
      <span class="spacer"></span>
      <button class="ghost" @click="openImport">⤓ 从 QuizForge 导入</button>
    </div>



    <div class="frow countrow">
      <span class="muted">共 {{ total }} 题<template v-if="activeTag"> · 专题「{{ activeTag }}」</template></span>
    </div>
  </div>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="okbox">{{ msg }}</div>

  <div class="card selbar">
    <label class="chk"><input type="checkbox" :checked="allChecked" @change="toggleAll" /> 全选本页</label>
    <span class="muted">已选 {{ selList.length }} 题</span>
    <button class="ghost" :disabled="!selList.length" @click="clearSel">清空已选</button>
    <button class="ghost" v-if="activeSet && activeSet !== 'unfiled'"
            @click="selectWholeSet">整卷加入已选</button>
    <button class="ghost" :disabled="!selList.length" @click="showSets = true">归入整卷…</button>
    <button class="ghost" :disabled="!selList.length" @click="detachSelected">移出整卷</button>
    <span class="spacer"></span>
    <button :disabled="!selList.length" @click="goCompose">用所选组卷 →</button>
    <button class="primary" :disabled="!total" @click="showExport = true">
      导出{{ selList.length ? `所选 ${selList.length} 题` : '全部（按筛选）' }}
    </button>
  </div>

  <div v-if="loading" class="muted">加载中…</div>

  <div class="qgrid">
    <article v-for="q in items" :key="q.id" class="qcard" :class="{ picked: sel.has(q.id) }">
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

  <!-- 导出面板 -->
  <div v-if="showExport" class="modal" @click.self="showExport = false">
    <div class="modal-card">
      <h3>导出 {{ selList.length ? `所选 ${selList.length} 题` : `全部 ${total} 题（按当前筛选）` }}</h3>
      <label class="f"><span>文档标题</span><input v-model="exportOpts.title" /></label>
      <label class="f"><span>答案位置</span>
        <select v-model="exportOpts.answers">
          <option value="appendix">卷末统一（题目页干净）</option>
          <option value="inline">每题下方</option>
          <option value="none">不含答案</option>
        </select>
      </label>
      <div class="frow">
        <label class="chk"><input v-model="exportOpts.analysis" type="checkbox" /> 含解析</label>
        <label class="chk"><input v-model="exportOpts.tags" type="checkbox" /> 含标签</label>
        <label class="chk"><input v-model="exportOpts.source" type="checkbox" /> 含出处</label>
      </div>
      <div class="btnrow">
        <button class="primary" :disabled="!!exporting" @click="doExport('docx')">下载 Word（.docx）</button>
        <button :disabled="!!exporting" @click="doExport('pdf')">下载 PDF</button>
        <button :disabled="!!exporting" @click="doExport('md')">下载 Markdown</button>
        <button class="ghost" @click="showExport = false">取消</button>
      </div>
      <p class="muted" style="font-size:12px">公式会渲染成清晰的小图嵌入文档；PDF 用等线排版，可直接打印。</p>
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

  <!-- QuizForge 导入 -->
  <div v-if="showImport" class="modal" @click.self="showImport = false">
    <div class="modal-card">
      <h3>从 QuizForge 导入题库</h3>
      <p class="muted" style="font-size:12.5px">
        内置适配器会把 QuizForge 的「题库 → 题目」映射成这里的「书册 → 题目」，
        题型、选项、答案、解析、页码一并带过来；重复导入只补全空字段，不会重复建题。
      </p>
      <div class="qfpath">
        <b>库文件：</b>
        <code v-if="qf.found">{{ qf.db_path }}</code>
        <span v-else class="muted">{{ qf.hint || '没找到 quizforge.db' }}</span>
      </div>
      <div v-if="qf.banks.length" class="qfbank">
        <label v-for="b in qf.banks" :key="b.id" class="chk">
          <input v-model="qf.picked" type="checkbox" :value="b.id" />
          {{ b.name }} <span class="muted">（{{ b.question_count }} 题）</span>
        </label>
      </div>
      <div class="btnrow">
        <button class="primary" :disabled="!qf.banks.length || qf.busy === 'path'" @click="runImport">
          {{ qf.busy === 'path' ? '导入中…' : `导入所选（${qf.picked.length} 个题库）` }}
        </button>
        <label class="upload sm-upload">
          <input type="file" accept=".db,.sqlite,.sqlite3" @change="uploadDb" />
          <span>{{ qf.busy === 'file' ? '上传导入中…' : '或上传 quizforge.db 文件' }}</span>
        </label>
        <button class="ghost" @click="showImport = false">取消</button>
      </div>
      <div v-if="qf.result" class="okbox">
        新增 {{ qf.result.questions_created }} 题，补全 {{ qf.result.questions_updated }} 题，
        新建书册 {{ qf.result.books_created }} 个
      </div>
    </div>
  </div>
</template>

<style scoped>
.q-toolbar { display: flex; flex-direction: column; gap: 10px; }
.searchrow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.searchrow .search { flex: 1 1 320px; min-width: 240px; }
.spacer { flex: 1; }
.topics { display: flex; flex-wrap: wrap; gap: 8px; }
.topics .chip { cursor: pointer; }
.topics .chip.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.frow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.frow select { width: auto; min-width: 120px; }

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
@media (max-width: 1080px) {
  .bank { grid-template-columns: 1fr; }
  .bank-side { position: static; flex-direction: row; flex-wrap: wrap; }
}
</style>
