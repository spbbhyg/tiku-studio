<script setup>
import { computed, onMounted, ref } from 'vue'
import { renderMath } from '../math.js'
import { api } from '../api.js'

const LEVELS = ['高中联赛（预赛/初赛）', '决赛（CChO）', '国家集训队', '高校期末', '研究生入学']
const TOPICS = ['原子结构', '分子结构与化学键', '晶体结构', '配位化学', '化学热力学', '化学动力学',
  '化学平衡与电离', '电化学', '元素化学', '有机反应机理', '有机合成', '波谱分析',
  '定量分析与滴定', '量子化学初步', '高分子化学', '分析化学']
const TYPES = ['选择题', '填空题', '计算题', '综合推断题', '简答题', '机理题']

const tab = ref('ai')
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

async function runBrief() {
  if (!docId.value && !source.value.trim()) {
    err.value = '请选择一份 PDF，或粘贴文献文字'
    return
  }
  briefBusy.value = true
  err.value = ''
  try {
    const r = await api.post('/api/ai/doc-brief', {
      source: source.value,
      doc_id: source.value.trim() ? null : docId.value,
      count: briefCount.value,
      level: level.value,
      focus: focus.value,
    })
    brief.value = r
    if (r.questions && r.questions.length) {
      draft.value = [...r.questions.map((q) => ({ ...q, id: crypto.randomUUID() })), ...draft.value]
    }
    msg.value = '文献解读完成' + (r.questions && r.questions.length ? `，并生成了 ${r.questions.length} 道改编题` : '')
  } catch (e) { err.value = String(e.message || e) }
  finally { briefBusy.value = false }
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
})
</script>

<template>
  <h2>智能出题</h2>
  <p class="muted" style="margin-top:-8px">
    先定级别、知识点、题型与难度，AI 一次命制一组竞赛题；也可以让 AI 先把文献读薄
    （摘要 + 要点 + 对照段落），再据它改编。勾选满意的题即可直接组成新试卷，不写题库。
  </p>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="warnbox">{{ msg }}</div>

  <div class="smart">
    <aside class="card panel">
      <div class="tabs">
        <button :class="{ on: tab === 'ai' }" @click="tab = 'ai'">AI 出题</button>
        <button :class="{ on: tab === 'lit' }" @click="tab = 'lit'">文献解读</button>
        <button :class="{ on: tab === 'src' }" @click="tab = 'src'">素材</button>
      </div>

      <label class="f"><span>文献（可选，选中后直接把整份交给 AI）</span>
        <select v-model="docId" @change="loadOutline">
          <option value="">不使用 PDF</option>
          <option v-for="d in docs" :key="d.doc_id" :value="d.doc_id">
            {{ d.filename }}（{{ d.page_count }} 页）
          </option>
        </select>
      </label>

      <template v-if="tab === 'ai'">
        <h3>竞赛级别</h3>
        <div class="chips">
          <button v-for="l in LEVELS" :key="l" class="chipbtn" :class="{ on: level === l }"
                  @click="level = l">{{ l }}</button>
        </div>

        <h3>知识点（可多选）</h3>
        <div class="chips">
          <button v-for="t in TOPICS" :key="t" class="chipbtn" :class="{ on: topics.includes(t) }"
                  @click="onTopic(t)">{{ t }}</button>
        </div>

        <h3>题型</h3>
        <div class="chips">
          <button v-for="t in TYPES" :key="t" class="chipbtn" :class="{ on: types.includes(t) }"
                  @click="onType(t)">{{ t }}</button>
        </div>

        <label class="f"><span>难度 {{ '★'.repeat(difficulty) }}{{ '☆'.repeat(5 - difficulty) }}</span>
          <input v-model.number="difficulty" type="range" min="1" max="5" step="1" />
        </label>
        <label class="f"><span>题目数量：{{ count }}</span>
          <input v-model.number="count" type="range" min="1" max="8" step="1" />
        </label>
        <label class="f"><span>额外要求（可选）</span>
          <textarea v-model="extra" rows="2" maxlength="500"
                    placeholder="如：以过渡金属配合物为背景，含晶胞计算"></textarea>
        </label>

        <button class="primary" style="width:100%" :disabled="loading" @click="run">
          {{ loading ? 'AI 正在命题与解析…' : '一键 AI 出题' }}
        </button>

        <div v-if="outlineDoc && outlineDoc.sentences && outlineDoc.sentences.length" class="tips">
          <div class="muted sm">这份文献已识别出 {{ outlineDoc.sentences.length }} 处重点句：</div>
          <p v-for="s in outlineDoc.sentences.slice(0, 3)" :key="s.id" class="tip">
            [{{ s.page }} 页] {{ s.text.slice(0, 60) }}…
          </p>
          <p class="muted sm">需要逐句读与翻译，去「精读」页。</p>
        </div>
      </template>

      <template v-else-if="tab === 'lit'">
        <label class="f"><span>重点关注（可选）</span>
          <textarea v-model="focus" rows="2" maxlength="500"
                    placeholder="如：实验部分、反应机理、晶体结构数据"></textarea>
        </label>
        <label class="f"><span>据此出题数量：{{ briefCount }}</span>
          <input v-model.number="briefCount" type="range" min="0" max="8" step="1" />
        </label>
        <button class="primary" style="width:100%" :disabled="briefBusy" @click="runBrief">
          {{ briefBusy ? 'AI 正在研读文献…' : '解读文献' }}
        </button>
      </template>

      <template v-else>
        <label class="f"><span>命题素材（题干、文献片段、数据都可以）</span>
          <textarea v-model="source" rows="9" maxlength="20000"
                    placeholder="粘贴题干/文献段落；留空则使用上方选中的 PDF"></textarea>
        </label>
        <div class="btnrow">
          <button :disabled="!docId" @click="useDocText">取 PDF 第 1 页文字</button>
          <button :disabled="!source" @click="source = ''">清空</button>
        </div>
        <p class="muted sm">素材只发送给当前配置的模型，不会上传到别处。</p>
      </template>
    </aside>

    <div class="main">
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

      <div class="card">
        <div class="head">
          <h3 style="margin:0">生成结果 <span class="muted">{{ draft.length }} 题</span></h3>
          <div class="btnrow">
            <button :disabled="picking" @click="pickFromBank">{{ picking ? '读取中…' : '从题库带入' }}</button>
            <button :disabled="!draft.length" @click="toggleAll">
              {{ draft.length && draft.every((q) => picked[q.id]) ? '取消全选' : '全选' }}
            </button>
          </div>
        </div>

        <p v-if="loading" class="muted empty">正在调用 AI 命题，请稍候…</p>
        <p v-else-if="!draft.length" class="muted empty">在左侧选好条件后点「一键 AI 出题」，或在「从题库带入」挑现成的题。</p>

        <div v-for="(q, i) in draft" :key="q.id" class="qitem" :class="{ sel: picked[q.id] }">
          <label class="pick"><input :checked="!!picked[q.id]" type="checkbox" @change="togglePick(q.id)" /></label>
          <div class="qbody">
            <div class="qmeta">
              <b>{{ i + 1 }}.</b>
              <span class="chip brand">{{ q.q_type || '综合' }}</span>
              <span v-for="k in q.knowledge_points || []" :key="k" class="chip">{{ k }}</span>
              <span class="muted">（{{ q.score || 10 }} 分）</span>
              <button class="ghost sm" style="margin-left:auto" @click="drop(q.id)">移除</button>
            </div>
            <div class="stem" v-html="preview(q).__html"></div>
            <div v-if="q.images && q.images.length" class="qimgs">
              <img v-for="(im, ii) in q.images" :key="ii" :src="imgUrl(im)" alt="题图" loading="lazy" />
            </div>
            <div v-if="q.options && q.options.length" class="opts">
              <div v-for="(o, oi) in q.options" :key="oi" v-html="renderMath(o)"></div>
            </div>
            <details class="ans">
              <summary>答案与解析</summary>
              <div class="q-answer"><span class="cap">答案</span><span v-html="renderMath(q.answer)"></span></div>
              <div v-if="q.analysis" class="q-answer"><span class="cap">解析</span><span v-html="renderMath(q.analysis)"></span></div>
            </details>
          </div>
        </div>
      </div>

      <div v-if="pickedItems.length" class="card buildbar">
        <div>
          <b>已选 {{ pickedItems.length }} 题</b>
          <span class="muted">　总分 {{ pickedItems.reduce((s, q) => s + (Number(q.score) || 10), 0) }} 分</span>
        </div>
        <div class="btnrow" style="flex:1;justify-content:flex-end">
          <input v-model="buildTitle" class="title" placeholder="试卷标题（可空）" />
          <button class="primary" :disabled="building" @click="buildPaper">
            {{ building ? '正在成卷…' : '生成新试卷' }}
          </button>
          <router-link to="/papers"><button>去「试卷」页</button></router-link>
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
