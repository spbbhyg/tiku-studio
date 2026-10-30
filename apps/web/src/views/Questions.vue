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

const f = reactive({ q: '', q_type: '', difficulty: '', status: '' })

const sel = ref(new Set())
const openAnswers = ref(new Set())
const zoomImg = ref('')

const showExport = ref(false)
const exporting = ref('')
const exportOpts = reactive({ title: '练习卷', answers: 'appendix', analysis: true, tags: true, source: true })

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
function toggleAnswer(id) {
  const s = new Set(openAnswers.value)
  s.has(id) ? s.delete(id) : s.add(id)
  openAnswers.value = s
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
  await Promise.all([loadTopics(), loadQtypes()])
  // 支持 ?tag=QuizForge / ?tag=分析化学 这类直达筛选
  const t = String(route.query.tag || '').trim()
  if (t) activeTag.value = t
  await load()
})
</script>

<template>
  <h2>题库</h2>
  <p class="muted">
    按专题找题（仿 Chemy 的九大专题），也可关键词检索；勾选后可一键组卷或导出 Word / PDF / Markdown。
  </p>

  <div class="card q-toolbar">
    <div class="searchrow">
      <input v-model="f.q" class="search" placeholder="搜索题干、答案、文献名…（回车）" @keyup.enter="search" />
      <button class="primary" @click="search">查询</button>
      <button @click="reset">重置</button>
      <span class="spacer"></span>
      <button class="ghost" @click="openImport">⤓ 从 QuizForge 导入</button>
    </div>

    <div class="topics">
      <button class="chip" :class="{ on: !activeTag }" @click="activeTag = ''; search()">全部专题</button>
      <button v-for="t in topicTags" :key="t.id" class="chip" :class="{ on: activeTag === t.name }"
              @click="pickTag(t.name)">{{ t.name }}</button>
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
        </div>
        <div class="q-stem" v-html="renderMath(q.stem) || '<i class=muted>（纯图片题）</i>'"></div>
        <div v-if="stemImg(q)" class="qfigs">
          <img :src="imgUrl(stemImg(q).path)" alt="题图" @click="zoomImg = imgUrl(stemImg(q).path)" />
        </div>
        <div v-if="q.options && q.options.length" class="qopts">
          <span v-for="(o, i) in q.options" :key="i">{{ o }}</span>
        </div>
        <div class="q-tags">
          <span v-for="t in q.tags" :key="t" class="chip sm2" @click="pickTag(t)">{{ t }}</span>
        </div>
        <div class="q-actions">
          <button class="ghost sm" @click="toggleAnswer(q.id)">
            {{ openAnswers.has(q.id) ? '收起答案' : '查看参考答案' }}
          </button>
          <router-link class="ghost sm" :to="{ path: '/author', query: { id: q.id } }">编辑</router-link>
          <button class="ghost sm danger" @click="deleteOne(q.id)">删除</button>
        </div>
        <div v-if="openAnswers.has(q.id)" class="answerbox">
          <div class="ans" v-html="renderMath(q.answer) || '<i class=muted>（见答案图）</i>'"></div>
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
  display: flex; gap: 10px; padding: 12px 14px; background: #fff;
  border: 1px solid var(--line); border-radius: 10px;
}
.qcard.picked { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.q-left { display: flex; gap: 8px; align-items: flex-start; padding-top: 2px; }
.q-left .no { font-size: 12px; color: #98a2b3; }
.q-main { flex: 1; min-width: 0; }
.q-meta-line { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 6px; }
.src { font-size: 12px; color: #8a94a6; }
.chip.draft { background: #fff7e6; border-color: #ffd8a8; color: #b26a00; }
.q-stem { font-size: 14.5px; line-height: 1.72; color: #1f2937; }
.q-stem :deep(p) { margin: 0 0 4px; }
.qfigs { margin: 8px 0 4px; }
.qfigs img {
  max-width: 420px; max-height: 260px; border: 1px solid var(--line);
  border-radius: 8px; cursor: zoom-in; background: #fff;
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
