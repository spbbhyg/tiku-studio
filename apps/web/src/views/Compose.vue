<script setup>
/**
 * 组卷 —— 选题组卷（仿 Chemy：左侧专题筛选 + 卡片勾选，右侧已选托盘）
 * 另有「按范围自动组卷」保留原逻辑（需要课本纲，现在通常为空）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { renderMath } from '../math.js'

const router = useRouter()
const route = useRoute()

const mode = ref('manual')
const err = ref('')
const msg = ref('')
const busy = ref('')

const DIFF_LABEL = { 1: '基础', 2: '中等', 3: '较难' }
const DEFAULT_SCORE = {
  '口算': 1, '计算': 2, '判断': 1, '填空': 2, '选择': 2, '拼音': 2, '字词': 2,
  '笔画': 2, '连线': 2, '默写': 2, '听写': 2, '古诗': 2, '字谜': 2, '操作': 2,
  '看图列式': 4, '应用': 4, '简答': 4, '论述': 8, '名词解释': 4, '证明': 8,
  '案例分析': 8, '翻译': 4, '文献阅读': 6, '其他': 2,
}

/* ---------------------------------------------------------------- 选题组卷 */
const pool = ref([])
const poolTotal = ref(0)
const page = ref(1)
const PAGE = 20
const loading = ref(false)

const topics = ref([])
const activeTag = ref('')
const qtypes = ref([])
const pf = reactive({ q: '', q_type: '', difficulty: '', status: 'confirmed' })

const chosen = ref([])
const chosenIds = computed(() => new Set(chosen.value.map((q) => q.id)))
const scoreTable = reactive({})
const paperMeta = reactive({ title: '', duration_minutes: 60 })

const typeList = computed(() => {
  const seen = []
  for (const q of chosen.value) if (!seen.includes(q.q_type)) seen.push(q.q_type)
  return seen
})
const totalScore = computed(() =>
  chosen.value.reduce((sum, q) => sum + Number(scoreTable[q.q_type] ?? DEFAULT_SCORE[q.q_type] ?? 2), 0))
const pages = computed(() => Math.max(1, Math.ceil(poolTotal.value / PAGE)))

function imgUrl(im) {
  const p = typeof im === 'string' ? im : im.path
  if (!p) return ''
  if (p.startsWith('/') || p.startsWith('http')) return p
  return '/api/images/' + p.split('/').map(encodeURIComponent).join('/')
}
function stemImg(q) {
  const imgs = q.images || []
  return imgs.find((i) => (i.caption || '') !== '答案') || null
}

async function loadTopics() {
  try {
    const d = await api.get('/api/authoring/tags')
    const g = (d.groups || {})['专题'] || []
    topics.value = g
  } catch (e) { /* 标签不是必需 */ }
}

async function searchPool(reset = true) {
  if (reset) page.value = 1
  loading.value = true; err.value = ''
  try {
    const p = new URLSearchParams({ page: String(page.value), page_size: String(PAGE) })
    if (pf.q.trim()) p.set('q', pf.q.trim())
    if (pf.q_type) p.set('q_type', pf.q_type)
    if (pf.difficulty) p.set('difficulty', pf.difficulty)
    if (pf.status) p.set('status', pf.status)
    if (activeTag.value) p.set('tag', activeTag.value)
    const d = await api.get('/api/questions?' + p.toString())
    pool.value = d.items || []
    poolTotal.value = d.total || 0
  } catch (e) { err.value = e.message }
  loading.value = false
}

function pickTopic(name) {
  activeTag.value = activeTag.value === name ? '' : name
  searchPool()
}

function toggle(q) {
  const i = chosen.value.findIndex((x) => x.id === q.id)
  if (i >= 0) { chosen.value.splice(i, 1); return }
  chosen.value.push({ id: q.id, q_type: q.q_type, stem: q.stem || '' })
}
function choosePage() {
  for (const q of pool.value) if (!chosenIds.value.has(q.id)) chosen.value.push({ id: q.id, q_type: q.q_type, stem: q.stem || '' })
}
function clearChosen() { chosen.value = [] }
function removeAt(i) { chosen.value.splice(i, 1) }
function moveTo(i, d) {
  const j = i + d
  if (j < 0 || j >= chosen.value.length) return
  const a = chosen.value
  ;[a[i], a[j]] = [a[j], a[i]]
}

function goPage(d) {
  const n = page.value + d
  if (n < 1 || n > pages.value) return
  page.value = n
  searchPool(false)
}

async function preselect(ids) {
  for (const id of ids) {
    if (chosenIds.value.has(id)) continue
    try {
      const q = await api.get(`/api/questions/${id}`)
      chosen.value.push({ id: q.id, q_type: q.q_type, stem: q.stem || '' })
    } catch (e) { /* 已删除的题跳过 */ }
  }
}

async function generateManual() {
  err.value = ''
  if (!chosen.value.length) { err.value = '先从左边勾选题目'; return }
  busy.value = 'gen'
  try {
    const perType = {}
    for (const t of typeList.value) perType[t] = Number(scoreTable[t] ?? DEFAULT_SCORE[t] ?? 2)
    const body = {
      question_ids: chosen.value.map((q) => q.id),
      total_score: totalScore.value,
      duration_minutes: Number(paperMeta.duration_minutes) || 60,
      per_type_score: perType,
    }
    if (paperMeta.title) body.title = paperMeta.title
    const paper = await api.post('/api/papers', body)
    router.push(`/papers/${paper.id}`)
  } catch (e) { err.value = e.message }
  busy.value = ''
}

/* ---------------------------------------------------------------- 自动组卷 */
const textbooks = ref([])
const units = ref([])
const typeStats = ref({})
const cfg = reactive({
  textbook_id: '', unit_from: 1, unit_to: 1, title: '', total_score: 100,
  duration_minutes: 60, include_extension: false, seed: '', counts: {}, per_type_score: {},
})
const textbook = computed(() => textbooks.value.find((b) => b.id === Number(cfg.textbook_id)))

async function loadTextbook() {
  units.value = []; typeStats.value = {}
  if (!cfg.textbook_id) return
  const d = await api.get(`/api/books/${cfg.textbook_id}`)
  units.value = d.units || []
  if (units.value.length && !units.value.some((u) => u.seq === cfg.unit_to)) {
    cfg.unit_to = units.value[units.value.length - 1].seq
  }
  typeStats.value = await api.get(`/api/questions/type-stats?textbook_id=${cfg.textbook_id}`)
}
function clampRange() {
  if (Number(cfg.unit_to) < Number(cfg.unit_from)) cfg.unit_to = cfg.unit_from
}
async function generateAuto() {
  err.value = ''; busy.value = 'auto'
  try {
    const body = {
      textbook_id: Number(cfg.textbook_id), unit_from: Number(cfg.unit_from), unit_to: Number(cfg.unit_to),
      total_score: Number(cfg.total_score), duration_minutes: Number(cfg.duration_minutes),
      include_extension: cfg.include_extension,
      counts: Object.fromEntries(Object.entries(cfg.counts).filter(([, n]) => Number(n) > 0)),
      per_type_score: Object.fromEntries(Object.entries(cfg.per_type_score).filter(([, n]) => Number(n) > 0)),
    }
    if (cfg.title) body.title = cfg.title
    if (cfg.seed !== '') body.seed = Number(cfg.seed)
    const paper = await api.post('/api/papers', body)
    const warn = paper.config && paper.config.shortage && paper.config.shortage.length
    router.push(`/papers/${paper.id}${warn ? '?shortage=1' : ''}`)
  } catch (e) { err.value = e.message }
  busy.value = ''
}

onMounted(async () => {
  qtypes.value = await api.get('/api/qtypes')
  textbooks.value = await api.get('/api/books/textbooks')
  if (textbooks.value.length) { cfg.textbook_id = textbooks.value[0].id; await loadTextbook() }
  await Promise.all([loadTopics(), searchPool()])
  const ids = String(route.query.ids || '').split(',').map((x) => Number(x)).filter((n) => n > 0)
  if (ids.length) { mode.value = 'manual'; await preselect(ids) }
})
</script>

<template>
  <h2>组卷</h2>
  <p class="muted">
    从题库勾选题目 → 右侧调顺序与分值 → 生成试卷；也可以按单元范围让系统随机抽题。
  </p>

  <div class="tabs">
    <button :class="{ on: mode === 'manual' }" @click="mode = 'manual'">① 选题组卷</button>
    <button :class="{ on: mode === 'auto' }" @click="mode = 'auto'">② 自动组卷 <small v-if="!textbooks.length">（需课本纲）</small></button>
  </div>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="okbox">{{ msg }}</div>

  <!-- ============================================================ 选题组卷 -->
  <div v-if="mode === 'manual'" class="pick-grid">
    <div class="pick-left">
      <div class="card q-toolbar">
        <div class="searchrow">
          <input v-model="pf.q" class="search" placeholder="搜索题干、答案、文献名…（回车）" @keyup.enter="searchPool()" />
          <button class="primary" @click="searchPool()">查询</button>
          <button @click="choosePage">勾选本页（{{ pool.length }}）</button>
        </div>
        <div class="topics">
          <button class="chip" :class="{ on: !activeTag }" @click="activeTag = ''; searchPool()">全部专题</button>
          <button v-for="t in topics" :key="t.id" class="chip" :class="{ on: activeTag === t.name }"
                  @click="pickTopic(t.name)">{{ t.name }}</button>
        </div>
        <div class="frow">
          <select v-model="pf.q_type" @change="searchPool()">
            <option value="">全部题型</option>
            <option v-for="t in qtypes" :key="t" :value="t">{{ t }}</option>
          </select>
          <select v-model="pf.difficulty" @change="searchPool()">
            <option value="">全部难度</option>
            <option :value="1">基础</option><option :value="2">中等</option><option :value="3">较难</option>
          </select>
          <select v-model="pf.status" @change="searchPool()">
            <option value="">全部状态</option>
            <option value="confirmed">已确认</option>
            <option value="draft">草稿</option>
          </select>
          <span class="muted">共 {{ poolTotal }} 题 · 已选 {{ chosen.length }} 题</span>
        </div>
      </div>

      <div v-if="loading" class="muted">加载中…</div>

      <div class="qgrid">
        <article v-for="q in pool" :key="q.id" class="qcard" :class="{ picked: chosenIds.has(q.id) }">
          <div class="q-left">
            <input type="checkbox" :checked="chosenIds.has(q.id)" @change="toggle(q)" />
            <span class="no">#{{ q.id }}</span>
          </div>
          <div class="q-main">
            <div class="q-meta-line">
              <span class="chip sm2">{{ q.q_type }}</span>
              <span class="chip sm2">{{ DIFF_LABEL[q.difficulty] || '中等' }}</span>
              <span class="src">
                {{ q.book_title }}<template v-if="q.unit_name"> · {{ q.unit_name }}</template>
                <template v-if="q.source_title"> · {{ q.source_title }}</template>
              </span>
            </div>
            <div class="q-stem" @click="toggle(q)" v-html="renderMath(q.stem) || '<i class=&quot;muted&quot;>（纯图题）</i>'"></div>
            <div v-if="stemImg(q)" class="qfigs">
              <img :src="imgUrl(stemImg(q).path)" alt="题图" loading="lazy" @click="toggle(q)" />
            </div>
            <div v-if="q.options && q.options.length" class="qopts">
              <span v-for="(o, i) in q.options" :key="i">{{ o }}</span>
            </div>
            <div class="q-tags">
              <span v-for="t in q.tags || []" :key="t" class="chip sm2">{{ t }}</span>
            </div>
          </div>
        </article>
      </div>

      <p v-if="!loading && !pool.length" class="muted" style="padding:16px 0">
        没有符合条件的题目。先去「题库」或「文献出题」准备几道，或换个筛选条件。
      </p>

      <div class="pager" v-if="pages > 1">
        <button :disabled="page <= 1" @click="goPage(-1)">上一页</button>
        <span class="muted">第 {{ page }} / {{ pages }} 页</span>
        <button :disabled="page >= pages" @click="goPage(1)">下一页</button>
      </div>
    </div>

    <aside class="pick-right">
      <div class="card tray">
        <div class="tray-head">
          <b>已选 {{ chosen.length }} 题</b>
          <span class="muted">合计 {{ totalScore }} 分</span>
          <button v-if="chosen.length" class="link" @click="clearChosen">清空</button>
        </div>

        <div v-if="!chosen.length" class="muted" style="padding:8px 0;font-size:13px">
          左侧点题目（或勾选框）加入，加入后用 ↑↓ 调整顺序。
        </div>
        <ol class="traylist">
          <li v-for="(q, i) in chosen" :key="q.id">
            <span class="idx">{{ i + 1 }}</span>
            <span class="tt">{{ q.q_type }}</span>
            <span class="tstem" :title="q.stem">{{ q.stem ? q.stem.replace(/\$[^$]*\$/g, ' $…$ ') : '（纯图题）' }}</span>
            <button class="mini" :disabled="i === 0" @click="moveTo(i, -1)">↑</button>
            <button class="mini" :disabled="i === chosen.length - 1" @click="moveTo(i, 1)">↓</button>
            <button class="mini danger" @click="removeAt(i)">×</button>
          </li>
        </ol>

        <div v-if="typeList.length" class="scoretable">
          <div class="st-head">按题型设每题分值</div>
          <div v-for="t in typeList" :key="t" class="st-row">
            <span class="tname">{{ t }}</span>
            <input type="number" step="0.5" min="0" v-model.number="scoreTable[t]"
                   :placeholder="String(DEFAULT_SCORE[t] ?? 2)" />
            <span class="muted small">× {{ chosen.filter((q) => q.q_type === t).length }} 题</span>
          </div>
        </div>

        <div class="grid cols2" style="margin-top:10px">
          <label class="f"><span>卷名（可空）</span><input v-model="paperMeta.title" placeholder="如：期中自测卷" /></label>
          <label class="f"><span>限时（分钟）</span><input type="number" v-model.number="paperMeta.duration_minutes" /></label>
        </div>
        <button class="primary wide" :disabled="!!busy || !chosen.length" @click="generateManual">
          {{ busy === 'gen' ? '生成中…' : `生成试卷（${chosen.length} 题 · ${totalScore} 分）→` }}
        </button>
      </div>
    </aside>
  </div>

  <!-- ============================================================ 自动组卷 -->
  <template v-else>
    <div v-if="!textbooks.length" class="warnbox">
      当前没有课本纲（`role=textbook` 的书册）。自动组卷需要「单元范围 + 题型配额」，
      请先在「AI 拆解」或「资料库」里导入一本课本的目录；只想用现有题目出卷的话，用 ① 选题组卷。
    </div>
    <template v-else>
      <div class="card">
        <h3>① 范围</h3>
        <div class="grid cols3">
          <label class="f"><span>教材纲</span>
            <select v-model="cfg.textbook_id" @change="loadTextbook">
              <option v-for="b in textbooks" :key="b.id" :value="b.id">{{ b.title }}</option>
            </select>
          </label>
          <label class="f"><span>从第几单元</span>
            <select v-model="cfg.unit_from" @change="clampRange">
              <option v-for="u in units" :key="u.id" :value="u.seq">{{ u.seq }}. {{ u.name }}</option>
            </select>
          </label>
          <label class="f"><span>到第几单元</span>
            <select v-model="cfg.unit_to">
              <option v-for="u in units" :key="u.id" :value="u.seq">{{ u.seq }}. {{ u.name }}</option>
            </select>
          </label>
        </div>
        <label class="chk" style="margin-top:6px">
          <input type="checkbox" v-model="cfg.include_extension" /> 包含提优拓展题
        </label>
      </div>

      <div class="card" v-if="units.length">
        <h3>② 题型配额</h3>
        <div class="grid cols4">
          <label v-for="t in qtypes" :key="t" class="f">
            <span>{{ t }}（可用 {{ typeStats[t] || 0 }}）</span>
            <input type="number" min="0" v-model.number="cfg.counts[t]" placeholder="0" />
          </label>
        </div>
        <div class="grid cols4" style="margin-top:8px">
          <label class="f"><span>卷名</span><input v-model="cfg.title" :placeholder="textbook ? `${textbook.grade}年级${textbook.subject}` : ''" /></label>
          <label class="f"><span>总分</span><input type="number" v-model.number="cfg.total_score" /></label>
          <label class="f"><span>限时（分钟）</span><input type="number" v-model.number="cfg.duration_minutes" /></label>
        </div>
        <button class="primary" style="margin-top:10px" :disabled="!!busy || !cfg.textbook_id" @click="generateAuto">
          {{ busy === 'auto' ? '生成中…' : '生成试卷 →' }}
        </button>
      </div>
    </template>
  </template>
</template>

<style scoped>
.tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.tabs button {
  background: #fff; border: 1px solid var(--line); border-radius: 999px;
  padding: 7px 16px; font-size: 13.5px; color: #475467; cursor: pointer;
}
.tabs button.on { background: var(--brand); border-color: var(--brand); color: #fff; }
.tabs button small { opacity: .8; font-size: 11.5px; }

.pick-grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 14px; align-items: start; }
.pick-right { position: sticky; top: 12px; }
@media (max-width: 1180px) { .pick-grid { grid-template-columns: 1fr; } .pick-right { position: static; } }

.q-toolbar { display: flex; flex-direction: column; gap: 10px; }
.searchrow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.searchrow .search { flex: 1 1 280px; min-width: 220px; }
.topics { display: flex; flex-wrap: wrap; gap: 8px; }
.topics .chip { cursor: pointer; }
.topics .chip.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.frow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.frow select { width: auto; min-width: 120px; }

.qgrid { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
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
.q-stem { font-size: 14.5px; line-height: 1.72; color: #1f2937; cursor: pointer; }
.qfigs { margin: 8px 0 4px; }
.qfigs img { max-width: 380px; max-height: 220px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; background: #fff; }
.qopts { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13.5px; color: #374151; margin: 4px 0; }
.q-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }

.pager { display: flex; gap: 10px; align-items: center; justify-content: center; margin: 14px 0; }

.tray-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; }
.tray-head .link { margin-left: auto; }
.link { background: none; border: none; color: var(--brand); cursor: pointer; font-size: 12.5px; padding: 0; }
.traylist { list-style: none; margin: 6px 0 0; padding: 0; max-height: 300px; overflow: auto; }
.traylist li { display: flex; align-items: center; gap: 6px; padding: 5px 0; border-bottom: 1px dashed #eef1f6; }
.idx { width: 20px; color: #8492a6; font-size: 12px; flex: 0 0 auto; }
.tstem { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.tt { font-size: 11px; color: #6b7a90; background: #f2f5fa; border-radius: 4px; padding: 1px 5px; flex: 0 0 auto; }
.mini { padding: 0 6px; font-size: 12px; line-height: 20px; flex: 0 0 auto; }
.mini.danger { color: #d9483b; }
.scoretable { margin-top: 10px; border-top: 1px solid #eef1f6; padding-top: 8px; }
.st-head { font-size: 12.5px; color: #6b7a90; margin-bottom: 4px; }
.st-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 13px; }
.tname { width: 84px; }
.st-row input { width: 72px; }
.wide { width: 100%; margin-top: 8px; }
.small { font-size: 12px; }
.chk { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
</style>
