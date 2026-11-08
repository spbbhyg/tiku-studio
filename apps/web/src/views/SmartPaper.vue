<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { renderMath } from '../math.js'
import { api } from '../api.js'

const LEVELS = ['高中联赛（预赛/初赛）', '决赛（CChO）', '国家集训队', '高校期末', '研究生入学']
const TOPICS = ['原子结构', '分子结构与化学键', '晶体结构', '配位化学', '化学热力学', '化学动力学',
  '化学平衡与电离', '电化学', '元素化学', '有机反应机理', '有机合成', '波谱分析',
  '定量分析与滴定', '量子化学初步', '高分子化学', '分析化学']
const TYPES = ['选择题', '填空题', '计算题', '综合推断题', '简答题', '机理题']

const tab = ref('lit')
const docs = ref([])
const docId = ref('')
const outlineDoc = ref(null)
const level = ref(LEVELS[0])
const topics = ref(['化学热力学'])
const types = ref(['计算题'])
const difficulty = ref(3)
const count = ref(3)
const extra = ref('')
const source = ref('')

const loading = ref(false)
const err = ref('')
const msg = ref('')
const draft = ref([])

const focus = ref('')
const briefCount = ref(2)
const brief = ref(null)
const briefBusy = ref(false)
const qBusy = ref(false)
const qCount = ref(3)
const importing = ref(false)
const importOpen = ref(false)
const importTopic = ref('')
const IMPORT_TOPICS = ['有机化学','高分子化学','晶体化学','无机综合与结构推断','方程式与元素化学','热力学与化学平衡','电化学','动力学','分析化学']

const picking = ref(false)
const picked = ref({})
const building = ref(false)
const buildTitle = ref('')

const pickedItems = computed(() => draft.value.filter((q) => picked.value[q.id]))

function toggle(list, v) {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v]
}
function onTopic(v) { topics.value = toggle(topics.value, v) }
function onType(v) { types.value = toggle(types.value, v) }

function plain(html) {
  const d = document.createElement('div')
  d.innerHTML = html
  return (d.textContent || '').replace(/\s+/g, ' ').trim()
}

async function loadDocs() {
  try {
    const d = await api.get('/api/manual/docs')
    docs.value = d.docs || []
    if (docs.value.length) docId.value = docs.value[0].doc_id
  } catch (e) { /* 没有上传过文献时忽略 */ }
}

async function useDocText() {
  if (!docId.value) { err.value = '先选一份 PDF'; return }
  err.value = ''
  try {
    const page = await api.get(`/api/manual/doc/${docId.value}/page/1/text`)
    source.value = (page.text || '').slice(0, 6000)
    if (!source.value.trim()) {
      msg.value = '这一页没有文字层（扫描件），可以用「文献解读」让 AI 读整份，或直接粘贴素材。'
    } else {
      msg.value = '已把第 1 页文字放进素材区，可再手动删改。'
    }
  } catch (e) { err.value = String(e.message || e) }
}

async function loadOutline() {
  outlineDoc.value = null
  if (!docId.value) return
  try { outlineDoc.value = await api.get(`/api/reader/doc/${docId.value}/outline`) }
  catch (e) { outlineDoc.value = null }
}

async function run() {
  if (!topics.value.length && !types.value.length) {
    err.value = '至少选一个知识点或一种题型'
    return
  }
  if (!source.value.trim() && !docId.value) {
    err.value = '请粘贴命题素材，或选择一份 PDF'
    return
  }
  loading.value = true
  err.value = ''
  msg.value = ''
  try {
    const r = await api.post('/api/ai/smart-questions', {
      source: source.value,
      doc_id: source.value.trim() ? null : docId.value,
      count: count.value,
      level: level.value,
      topics: topics.value,
      types: types.value,
      difficulty: difficulty.value,
      extra: extra.value,
    })
    draft.value = (r.questions || []).map((q) => ({ ...q, id: crypto.randomUUID() }))
    picked.value = {}
    msg.value = `已生成 ${draft.value.length} 道题${r.model ? '（' + r.model + '）' : ''}，勾选后可以组成新试卷。`
  } catch (e) { err.value = String(e.message || e) }
  finally { loading.value = false }
}

let qTimer = null

function stopQPoll() {
  if (qTimer) { clearInterval(qTimer); qTimer = null }
}

async function pollQ(jid) {
  try {
    const r = await api.get('/api/ai/smart-questions/jobs/' + jid)
    if (r.status === 'running') { qBusy.value = true; return }
    stopQPoll()
    qBusy.value = false
    localStorage.removeItem('tiku-sq-job')
    if (r.status === 'error') throw new Error(r.error || '出题失败')
    const qs = (r.result && r.result.questions) || []
    if (!qs.length) throw new Error('AI 没有返回可用的题目，请调小数量或换更短的素材重试')
    draft.value = qs.map((q) => ({ ...q, id: crypto.randomUUID() }))
    picked.value = {}
    msg.value = `已依据文献生成 ${draft.value.length} 道题，可勾选后生成试卷`
  } catch (e2) {
    stopQPoll()
    qBusy.value = false
    localStorage.removeItem('tiku-sq-job')
    msg.value = ''
    err.value = String(e2.message || e2)
  }
}

function resumeQJob() {
  let jid = ''
  try { jid = localStorage.getItem('tiku-sq-job') || '' } catch (e2) {}
  if (!jid) return
  qBusy.value = true
  msg.value = '继续等待后台出题…'
  pollQ(jid)
  qTimer = setInterval(() => pollQ(jid), 3000)
}

function openImport() {
  if (!pickedItems.value.length) { err.value = '先勾选要导入题库的题'; return }
  importTopic.value = ''
  importOpen.value = true
}

async function importToBank() {
  const qs = pickedItems.value
  if (!qs.length) { err.value = '先勾选要导入题库的题'; return }
  importing.value = true
  err.value = ''
  let ok = 0, dup = 0
  try {
    for (const q of qs) {
      const r = await api.post('/api/questions', {
        stem: q.stem || '', answer: q.answer || '', analysis: q.analysis || '',
        q_type: q.q_type || '综合', difficulty: q.difficulty || 3,
        knowledge_points: importTopic.value ? [importTopic.value] : (q.knowledge_points || []),
        images: q.images || [], score: q.score || 10,
      })
      if (r.deduped) dup += 1; else ok += 1
    }
    importOpen.value = false
    msg.value = `已导入题库 ${ok} 道${dup ? `，${dup} 道重复已跳过` : ''}`
  } catch (e2) {
    err.value = String(e2.message || e2)
  } finally { importing.value = false }
}

async function genQuestions() {
  if (!docId.value && !source.value.trim()) { err.value = '请选择一份 PDF，或粘贴文献文字'; return }
  stopQPoll()
  qBusy.value = true
  err.value = ''
  msg.value = '已转到后台出题，切到别的页面也不会中断…'
  try {
    const r = await api.post('/api/ai/smart-questions/jobs', {
      source: source.value,
      doc_id: source.value.trim() ? null : docId.value,
      count: qCount.value,
      level: level.value,
      topics: [], types: [], difficulty: 3,
      extra: focus.value || '',
    })
    try { localStorage.setItem('tiku-sq-job', r.job_id) } catch (e2) {}
    pollQ(r.job_id)
    qTimer = setInterval(() => pollQ(r.job_id), 3000)
  } catch (e2) {
    qBusy.value = false
    err.value = String(e2.message || e2)
  }
}

let briefTimer = null

function stopBriefPoll() {
  if (briefTimer) { clearInterval(briefTimer); briefTimer = null }
}

async function pollBrief(jid) {
  try {
    const r = await api.get('/api/ai/doc-brief/jobs/' + jid)
    if (r.status === 'running') { briefBusy.value = true; return }
    stopBriefPoll()
    briefBusy.value = false
    localStorage.removeItem('tiku-brief-job')
    if (r.status === 'error') throw new Error(r.error || '文献解读失败')
    brief.value = r.result || null
    if (brief.value && brief.value.mechanism_figures) {
      msg.value = '文献解读完成：已标出机理相关图片/结构和关键段落'
    } else {
      msg.value = '文献解读完成'
    }
  } catch (e2) {
    stopBriefPoll()
    briefBusy.value = false
    localStorage.removeItem('tiku-brief-job')
    err.value = String(e2.message || e2)
  }
}

function resumeBriefJob() {
  let jid = ''
  try { jid = localStorage.getItem('tiku-brief-job') || '' } catch (e2) {}
  if (!jid) return
  briefBusy.value = true
  msg.value = '继续等待后台文献解读…'
  pollBrief(jid)
  briefTimer = setInterval(() => pollBrief(jid), 3000)
}

async function runBrief() {
  if (!docId.value && !source.value.trim()) {
    err.value = '请选择一份 PDF，或粘贴文献文字'
    return
  }
  stopBriefPoll()
  briefBusy.value = true
  err.value = ''
  msg.value = '文献解读已转到后台，切到别的页面也不会中断…'
  try {
    const r = await api.post('/api/ai/doc-brief/jobs', {
      source: source.value,
      doc_id: source.value.trim() ? null : docId.value,
      count: 0,
      level: level.value,
      focus: focus.value,
    })
    try { localStorage.setItem('tiku-brief-job', r.job_id) } catch (e2) {}
    pollBrief(r.job_id)
    briefTimer = setInterval(() => pollBrief(r.job_id), 3000)
  } catch (e2) {
    briefBusy.value = false
    err.value = String(e2.message || e2)
  }
}

function pickedIds() { return draft.value.filter((q) => picked.value[q.id]).map((q) => q.id) }
function togglePick(id) { picked.value = { ...picked.value, [id]: !picked.value[id] } }
function toggleAll() {
  const all = draft.value.length > 0 && draft.value.every((q) => picked.value[q.id])
  const next = {}
  draft.value.forEach((q) => { next[q.id] = !all })
  picked.value = next
}
function drop(id) {
  draft.value = draft.value.filter((q) => q.id !== id)
  const p = { ...picked.value }
  delete p[id]
  picked.value = p
}

async function pickFromBank() {
  picking.value = true
  err.value = ''
  try {
    const d = await api.get('/api/questions?page=1&page_size=50')
    const exist = new Set(draft.value.map((q) => (q.stem || '').slice(0, 40)))
    const added = (d.items || [])
      .filter((q) => !exist.has((q.stem || '').slice(0, 40)))
      .map((q) => ({
        id: crypto.randomUUID(),
        q_type: q.q_type,
        knowledge_points: q.knowledge_points || [],
        stem: (q.stem || '').trim() || `（题图题 #${q.id}）`,
        options: q.options || [],
        answer: q.answer || '',
        analysis: q.analysis || '',
        images: q.images || [],
        score: 10,
        from_bank: q.id,
      }))
    draft.value = [...added, ...draft.value]
    msg.value = added.length ? `从题库带入 ${added.length} 道题` : '题库里没有新的题'
  } catch (e) { err.value = String(e.message || e) }
  finally { picking.value = false }
}

async function buildPaper() {
  const items = pickedItems.value.map((q) => ({ snapshot: snapshotOf(q), score: Number(q.score) || 10 }))
  if (!items.length) { err.value = '先勾选至少一道题'; return }
  building.value = true
  err.value = ''
  try {
    const p = await api.post('/api/papers/ai/build', {
      items,
      title: buildTitle.value || undefined,
    })
    msg.value = `已生成试卷《${p.title}》，共 ${p.items.length} 题、${p.total_score} 分`
    picked.value = {}
  } catch (e) { err.value = String(e.message || e) }
  finally { building.value = false }
}

function snapshotOf(q) {
  return {
    stem: q.stem,
    options: q.options || [],
    answer: q.answer || '',
    analysis: q.analysis || '',
    q_type: q.q_type || '综合',
    difficulty: q.difficulty || 3,
    knowledge_points: q.knowledge_points || [],
    images: q.images || [],
    source_book_title: q.from_bank ? '题库' : (brief.value?.title || 'AI 生成'),
    unit_name: '',
  }
}

function imgUrl(im) {
  const raw = typeof im === 'string' ? im : (im && im.path) || ''
  if (!raw) return ''
  if (raw.startsWith('/') || raw.startsWith('http')) return raw
  return '/api/images/' + raw.split('/').map(encodeURIComponent).join('/')
}

function preview(q) { return { __html: renderMath(q.stem || '') } }

onMounted(async () => {
  await loadDocs()
  await loadOutline()
  resumeBriefJob()
  resumeQJob()
})
onBeforeUnmount(() => { stopBriefPoll(); stopQPoll() })
</script>

<template>
  <h2>文献解读</h2>
  <p class="muted" style="margin-top:-8px">
    让 AI 把文献读薄：先给摘要，再挑出反应机理、催化循环、关键中间体/结构与相关图注，
    并附关键段落的原文/译文。解读在后台运行，切页面不会丢。
  </p>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="warnbox">{{ msg }}</div>

  <div class="smart">
    <aside class="card panel">
      <label class="f"><span>文献（可选，选中后直接把整份交给 AI）</span>
        <select v-model="docId" @change="loadOutline">
          <option value="">不使用 PDF</option>
          <option v-for="d in docs" :key="d.doc_id" :value="d.doc_id">
            {{ d.filename }}（{{ d.page_count }} 页）
          </option>
        </select>
      </label>

      <div class="qgen">
        <div class="numpick">
          <button v-for="n in 8" :key="n" :class="{ on: qCount === n }" @click="qCount = n">{{ n }}</button>
        </div>
        <button class="primary" style="width:100%" :disabled="qBusy" @click="genQuestions">
          {{ qBusy ? '后台出题中…' : `出题（${qCount} 道）` }}
        </button>
        <p class="muted sm">先选题数再点「出题」，后台生成，切页面不丢；出完在右侧显示。</p>
      </div>

      <template>
        <label class="f"><span>重点关注（可选）</span>
          <textarea v-model="focus" rows="2" maxlength="500"
                    placeholder="如：实验部分、反应机理、晶体结构数据"></textarea>
        </label>
        <button class="primary" style="width:100%" :disabled="briefBusy" @click="runBrief">
          {{ briefBusy ? 'AI 正在研读文献…' : '解读文献' }}
        </button>
      </template>

    </aside>

    <div class="main">
      <div v-if="!brief" class="card">
        <p class="muted" style="margin:0">{{ briefBusy ? '正在后台解读文献，可切到别的页面，回来会继续显示…' : '选一份文献 PDF，点左侧「解读文献」。解读在后台运行，切到别的页面不会中断。' }}</p>
      </div>
      <div v-if="brief" class="card">
        <div class="head">
          <h3 style="margin:0">文献解读<template v-if="brief.title"> · {{ brief.title }}</template></h3>
          <button class="ghost" @click="brief = null">关闭</button>
        </div>
        <div class="briefsum" v-html="renderMath(brief.summary)"></div>
        <div v-if="brief.key_points && brief.key_points.length" class="cardinner">
          <div class="subtitle">核心要点</div>
          <ul class="pts">
            <li v-for="(k, i) in brief.key_points" :key="i" v-html="renderMath(k)"></li>
          </ul>
        </div>
        <div v-if="brief.mechanism_figures && brief.mechanism_figures.length" class="cardinner">
          <div class="subtitle">机理相关图片 / 结构</div>
          <ul class="pts">
            <li v-for="(f, i) in brief.mechanism_figures" :key="'fg' + i" v-html="renderMath(f)"></li>
          </ul>
        </div>
        <div v-for="(x, i) in brief.excerpts" :key="i" class="excerpt">
          <div>
            <div class="subtitle">{{ x.section || '关键段落' }} · 原文</div>
            <div class="txt" v-html="renderMath(x.original)"></div>
          </div>
          <div class="trans">
            <div class="subtitle">译文</div>
            <div class="txt" v-html="renderMath(x.translation)"></div>
          </div>
        </div>
      </div>

      <div v-if="draft.length" class="card">
        <div class="head">
          <h3 style="margin:0">依据文献出的题 <span class="muted">{{ draft.length }} 题</span></h3>
          <div class="btnrow">
            <button :disabled="!draft.length" @click="toggleAll">全选 / 取消</button>
            <button class="primary" :disabled="!pickedItems.length || importing" @click="openImport">
              {{ importing ? '导入中…' : '导入题库' }}
            </button>
          </div>
        </div>
        <div v-for="(q, i) in draft" :key="q.id" class="qitem" :class="{ sel: picked[q.id] }">
          <label class="pick"><input :checked="!!picked[q.id]" type="checkbox" @change="togglePick(q.id)" /></label>
          <div class="qbody">
            <div class="qmeta">
              <b>{{ i + 1 }}.</b>
              <span class="chip brand">{{ q.q_type || '综合' }}</span>
              <span class="muted">（{{ q.score || 10 }} 分）</span>
              <span v-if="q.knowledge_points && q.knowledge_points.length" class="chip">{{ q.knowledge_points[0] }}</span>
              <button class="ghost sm" style="margin-left:auto" @click="drop(q.id)">移除</button>
            </div>
            <div class="stem" v-html="renderMath(q.stem)"></div>
            <details class="ans">
              <summary>答案与解析</summary>
              <div class="q-answer"><span class="cap">答案</span><span v-html="renderMath(q.answer)"></span></div>
              <div v-if="q.analysis" class="q-answer"><span class="cap">解析</span><span v-html="renderMath(q.analysis)"></span></div>
            </details>
          </div>
        </div>
        <div v-if="pickedItems.length" class="buildbar">
          <b>已选 {{ pickedItems.length }} 题</b>
          <input v-model="buildTitle" class="title" placeholder="试卷标题（可空）" />
          <button class="primary" :disabled="building" @click="buildPaper">{{ building ? '正在成卷…' : '生成试卷' }}</button>
        </div>
      </div>

      <div v-if="importOpen" class="modal" @click.self="importOpen = false">
        <div class="modal-card">
          <h3 style="margin:0 0 8px">存入题库</h3>
          <p class="muted" style="margin:0 0 10px">已勾选 {{ pickedItems.length }} 道题。可指定归入专题；不选则用 AI 自动识别的分类。</p>
          <label class="f"><span>归入专题</span>
            <select v-model="importTopic">
              <option value="">自动识别</option>
              <option v-for="t in IMPORT_TOPICS" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
          <div class="btnrow" style="margin-top:12px">
            <button @click="importOpen = false">取消</button>
            <button class="primary" :disabled="importing" @click="importToBank">{{ importing ? '导入中…' : '确认导入' }}</button>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.smart { display: grid; grid-template-columns: 330px minmax(0, 1fr); gap: 16px; align-items: start; }
.panel { position: sticky; top: 12px; }
.tabs { display: flex; gap: 6px; margin-bottom: 14px; }
.tabs button { flex: 1; padding: 6px 8px; font-size: 13px; }
.tabs button.on { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 600; }
.panel h3 { font-size: 13.5px; margin: 14px 0 6px; color: #344054; }
.modal { position: fixed; inset: 0; background: rgba(15,23,42,.35); display: grid; place-items: center; z-index: 60; }
.modal-card { width: min(420px, 92vw); background: #fff; border-radius: 12px; padding: 18px; box-shadow: 0 20px 50px rgba(15,23,42,.25); }
.qgen { margin: 10px 0 14px; padding: 10px; border: 1px solid #dbe4f3; border-radius: 10px; background: #f8fbff; }
.numpick { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px; }
.numpick button { padding: 3px 9px; border-radius: 6px; }
.numpick button.on { background: var(--brand); border-color: var(--brand); color: #fff; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chipbtn {
  padding: 3px 10px; border-radius: 999px; font-size: 12.5px; line-height: 1.6;
  background: #f4f6fa; border: 1px solid #e6eaf2; color: #475467;
}
.chipbtn.on { background: var(--brand-soft); border-color: #b9d2ff; color: var(--brand); font-weight: 600; }
.tips { margin-top: 12px; padding-top: 10px; border-top: 1px dashed #e6eaf2; }
.tip { margin: 4px 0; font-size: 12px; color: #667085; line-height: 1.5; }
.sm { font-size: 12px; }
.main { min-width: 0; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.empty { padding: 26px 0; text-align: center; }
.qitem {
  display: flex; gap: 10px; padding: 12px 4px; border-top: 1px solid #f1f3f8;
}
.qitem:first-of-type { border-top: none; }
.qitem.sel { background: #f7faff; }
.pick { flex: 0 0 auto; padding-top: 3px; }
.qbody { min-width: 0; flex: 1; }
.qmeta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.stem { font-size: 14.5px; line-height: 1.75; }
.opts { margin-top: 4px; display: grid; gap: 2px; font-size: 14px; }
.qimgs { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.qimgs img { max-width: 320px; max-height: 200px; border: 1px solid #e6e9ef; border-radius: 6px; background: #fff; }
.ans { margin-top: 8px; }
.ans summary { font-size: 13px; }
.ans .q-answer { font-size: 13.5px; }
.buildbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; position: sticky; bottom: 0; }
.title { max-width: 260px; }
.briefsum { font-size: 14px; line-height: 1.8; color: #344054; }
.cardinner { margin-top: 10px; padding: 10px 12px; background: #f8fafc; border-radius: 8px; }
.subtitle { font-size: 12px; font-weight: 600; color: #667085; margin-bottom: 4px; }
.pts { margin: 0; padding-left: 20px; font-size: 13.5px; line-height: 1.7; }
.excerpt {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px;
  padding: 10px 12px; border: 1px solid #eef1f6; border-radius: 8px;
}
.excerpt .trans { border-left: 3px solid var(--brand); padding-left: 10px; }
.txt { font-size: 13px; line-height: 1.7; color: #344054; }
.ghost { background: transparent; border-color: transparent; color: var(--muted); }
.ghost.sm { padding: 2px 8px; font-size: 12px; }
@media (max-width: 1000px) {
  .smart { grid-template-columns: 1fr; }
  .panel { position: static; }
  .excerpt { grid-template-columns: 1fr; }
}
</style>
