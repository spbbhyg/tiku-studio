<script setup>
// 组卷：左栏检索本地题库 → 勾选 → 中栏 A4 实时预览 → 右栏版式与页脚 → 导出
// 题库只读：这里从不写题库文件，勾题不留任何数据。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({ embedded: { type: Boolean, default: false } })

const kw = ref('')
const topic = ref('')
const topics = ref([])
const hits = ref([])
const busy = ref(false)
const err = ref('')
const msg = ref('')

const picked = ref([])          // [{id, title, topic_name, year, difficulty}]
const previewUrl = ref('')
const previewBusy = ref(false)
const zoom = ref(1)

const opt = reactive({
  title: '化学竞赛练习卷',
  school: '',
  reviewer: localStorage.getItem('tiku-author-name') || '',
  footer: '',
  answers: false,
  scores: true,
})

const ids = computed(() => picked.value.map(p => p.id))
const totalScore = ref(0)

async function loadTopics() {
  try {
    const r = await api.get('/api/chemy/topics')
    topics.value = Array.isArray(r) ? r : (r.topics || [])
  } catch (e) { /* 题库没起来就先空着 */ }
}

async function search() {
  busy.value = true
  err.value = ''
  try {
    if (!kw.value.trim()) {
      // 没关键词就按专题列出来（题库的搜索接口必须带关键词，空的查不到）
      const r = await api.get('/api/chemy/tree?topic=' + encodeURIComponent(topic.value || ''))
      const rows = Array.isArray(r) ? r : (r.items || [])
      hits.value = rows.map(x => ({
        id: x.id, title: x.title,
        topic_name: x.topic_name || (topics.value.find(y => y.id === topic.value) || {}).name || '',
        year: x.year,
      })).sort((a, b) => String(a.id).localeCompare(String(b.id)))
    } else {
      const r = await api.get('/api/chemy/search?' + new URLSearchParams({
        q: kw.value, topic: topic.value || '', page: 1, pageSize: 50,
      }))
      hits.value = Array.isArray(r) ? r : (r.items || r.rows || [])
    }
  } catch (e) {
    err.value = e?.message || String(e)
    hits.value = []
  } finally { busy.value = false }
}

function isPicked(id) { return picked.value.some(p => p.id === id) }

function toggle(h) {
  const i = picked.value.findIndex(p => p.id === h.id)
  if (i >= 0) picked.value.splice(i, 1)
  else picked.value.push({ id: h.id, title: h.title || h.id, topic_name: h.topic_name || '', year: h.year || '' })
}

function move(i, d) {
  const j = i + d
  if (j < 0 || j >= picked.value.length) return
  const [x] = picked.value.splice(i, 1)
  picked.value.splice(j, 0, x)
}

function clearAll() { picked.value = [] }

function body() {
  return {
    ids: ids.value,
    title: opt.title,
    subtitle: '',
    school: opt.school,
    reviewer: opt.reviewer,
    footer: opt.footer,
    answers: opt.answers,
    scores: opt.scores,
    part: opt.answers ? 'all' : 'questions',
  }
}

let timer = 0
async function refresh() {
  if (!ids.value.length) { previewUrl.value = ''; totalScore.value = 0; return }
  previewBusy.value = true
  err.value = ''
  try {
    const r = await api.post('/api/compose/preview', body())
    previewUrl.value = r.url
    totalScore.value = r.total_score || 0
  } catch (e) {
    err.value = e?.message || String(e)
    previewUrl.value = ''
  } finally { previewBusy.value = false }
}

function schedule() {
  clearTimeout(timer)
  timer = setTimeout(refresh, 500)
}

watch([picked, () => opt.title, () => opt.school, () => opt.reviewer, () => opt.footer, () => opt.answers, () => opt.scores], schedule, { deep: true })

async function exportAs(fmt) {
  if (!ids.value.length) { err.value = '先在左边勾几道题'; return }
  err.value = ''
  msg.value = fmt === 'pdf' ? '正在生成 PDF（走浏览器内核，10 题大约 10 秒）…' : '正在生成 Word…'
  try {
    const resp = await fetch(`/api/compose/export?fmt=${fmt}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body()),
    })
    if (!resp.ok) {
      const t = await resp.text()
      let d = t
      try { d = JSON.parse(t).detail || t } catch { /* 原样显示 */ }
      throw new Error(d)
    }
    const blob = await resp.blob()
    const cd = resp.headers.get('content-disposition') || ''
    const m = /filename\*=UTF-8''([^;]+)/.exec(cd)
    const name = m ? decodeURIComponent(m[1]) : `试卷.${fmt}`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 4000)
    msg.value = `已导出 ${name}`
  } catch (e) {
    err.value = e?.message || String(e)
    msg.value = ''
  }
}

onMounted(async () => {
  await loadTopics()
  await search()
})
</script>

<template>
  <div class="cmp" :class="{ embedded: props.embedded }">
    <!-- 左：题库检索 -->
    <section class="col left">
      <div class="chead">从本地题库选题</div>
      <div class="filter">
        <input v-model="kw" class="kw" placeholder="关键词：化合物、考点、年份…" @keyup.enter="search" />
        <select v-model="topic" class="tp" @change="search">
          <option value="">全部专题</option>
          <option v-for="t in topics" :key="t.id" :value="t.id">{{ t.name }}（{{ t.count }}）</option>
        </select>
        <button class="ghost sm" :disabled="busy" @click="search">{{ busy ? '搜索中' : '搜索' }}</button>
      </div>
      <div class="hints">找到 {{ hits.length }} 道；点一行就加进卷子（右边可调顺序）</div>
      <div class="list">
        <div v-for="h in hits" :key="h.id" class="hit" :class="{ on: isPicked(h.id) }" @click="toggle(h)">
          <span class="ck">{{ isPicked(h.id) ? '✓' : '' }}</span>
          <span class="ht">
            <b>{{ h.title || h.id }}</b>
            <i>{{ h.topic_name }}<template v-if="h.year"> · {{ h.year }}</template></i>
          </span>
        </div>
        <div v-if="!hits.length" class="none">没搜到。换个词，或先在「题库」里导题。</div>
      </div>
    </section>

    <!-- 中：A4 预览 -->
    <section class="col mid">
      <div class="chead">
        A4 实时预览
        <span class="cnt">已选 {{ picked.length }} 题<template v-if="totalScore"> · {{ totalScore }} 分</template></span>
        <span class="spacer"></span>
        <label class="sw"><input v-model="opt.answers" type="checkbox" /> 连答案</label>
        <label class="sw"><input v-model="opt.scores" type="checkbox" /> 标分值</label>
        <button class="ghost sm" @click="zoom = Math.max(0.5, zoom - 0.1)">－</button>
        <span class="zm">{{ Math.round(zoom * 100) }}%</span>
        <button class="ghost sm" @click="zoom = Math.min(1.6, zoom + 0.1)">＋</button>
      </div>
      <div class="sheetwrap">
        <iframe v-if="previewUrl" :src="previewUrl" class="sheet"
                :style="{ transform: `scale(${zoom})` }" title="试卷预览"></iframe>
        <div v-else class="empty">
          <p v-if="previewBusy">正在排版…</p>
          <p v-else>左边勾几道题，这里立刻按 A4 分页排出来。</p>
        </div>
        <div v-if="previewBusy && previewUrl" class="mask">排版中…</div>
      </div>
    </section>

    <!-- 右：版式 / 页脚 / 导出 -->
    <section class="col right">
      <div class="chead">版式与页脚</div>
      <label class="f"><span>卷名</span><input v-model="opt.title" /></label>
      <label class="f"><span>学校</span><input v-model="opt.school" placeholder="选填：印在页眉/页脚" /></label>
      <label class="f"><span>命题人</span><input v-model="opt.reviewer" placeholder="选填" /></label>
      <label class="f"><span>页脚</span>
        <input v-model="opt.footer" placeholder="留空则自动：第 {page} 页 共 {pages} 页" />
      </label>
      <p class="tip">自动页脚：不填就印「第 X 页 共 Y 页」，还会带上学校与命题人。{page} / {pages} 是页码占位符。</p>

      <div class="picked">
        <div class="ph">已选 {{ picked.length }} 题（顺序就是卷面顺序）</div>
        <div v-for="(p, i) in picked" :key="p.id" class="row">
          <span class="no">{{ i + 1 }}</span>
          <span class="nm">{{ p.title }}</span>
          <button class="mini" title="上移" :disabled="i === 0" @click="move(i, -1)">↑</button>
          <button class="mini" title="下移" :disabled="i === picked.length - 1" @click="move(i, 1)">↓</button>
          <button class="mini" title="移出" @click="toggle({ id: p.id })">×</button>
        </div>
        <div v-if="!picked.length" class="none">还没选题</div>
        <button v-if="picked.length" class="ghost sm wide" @click="clearAll">全部清空</button>
      </div>

      <div class="acts">
        <button class="pri" :disabled="!ids.length" @click="exportAs('pdf')">导出 PDF</button>
        <button class="pri alt" :disabled="!ids.length" @click="exportAs('docx')">导出 Word</button>
      </div>
      <div v-if="msg" class="ok">{{ msg }}</div>
      <div v-if="err" class="bad">{{ err }}</div>
    </section>
  </div>
</template>

<style scoped>
.cmp { display: grid; grid-template-columns: 300px minmax(0, 1fr) 280px; gap: 12px; height: calc(100vh - 56px); }
.cmp.embedded { height: 100%; min-height: 0; }
.col { background: #fff; border: 1px solid #e9edf3; border-radius: 12px; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.chead { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #eef1f6; font-weight: 600; font-size: 13.5px; color: #1d2939; }
.chead .spacer { flex: 1; }
.cnt { font-weight: 400; color: #667085; font-size: 12px; }
.filter { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px 6px; }
.kw, .tp, .f input { height: 30px; border: 1px solid #e4e7ec; border-radius: 8px; padding: 0 8px; font-size: 13px; }
.hints { padding: 0 12px 6px; font-size: 11.5px; color: #98a2b3; }
.list { flex: 1; overflow: auto; padding: 0 8px 8px; }
.hit { display: flex; gap: 8px; align-items: flex-start; padding: 7px 8px; border-radius: 8px; cursor: pointer; }
.hit:hover { background: #f7f9fc; }
.hit.on { background: #eef4ff; }
.ck { width: 16px; height: 16px; margin-top: 2px; border: 1px solid #d0d5dd; border-radius: 4px; font-size: 11px; line-height: 15px; text-align: center; color: #2f6fed; flex: 0 0 auto; }
.hit.on .ck { border-color: #2f6fed; background: #fff; }
.ht { display: flex; flex-direction: column; min-width: 0; }
.ht b { font-weight: 500; font-size: 13px; color: #1d2939; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ht i { font-style: normal; font-size: 11.5px; color: #98a2b3; }
.none { padding: 14px 8px; color: #98a2b3; font-size: 12.5px; text-align: center; }
.sheetwrap { position: relative; flex: 1; overflow: auto; background: #f2f4f7; padding: 12px; }
.sheet { width: 794px; height: 1123px; border: 0; background: #fff; box-shadow: 0 2px 10px rgba(16, 24, 40, .08); transform-origin: top left; display: block; margin: 0 auto; }
.empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #98a2b3; font-size: 13px; }
.mask { position: absolute; top: 10px; right: 12px; background: rgba(17, 24, 39, .72); color: #fff; font-size: 12px; padding: 4px 10px; border-radius: 999px; }
.zm { font-size: 12px; color: #667085; width: 36px; text-align: center; }
.sw { display: inline-flex; align-items: center; gap: 4px; font-weight: 400; font-size: 12.5px; color: #475467; }
.right { padding-bottom: 10px; }
.f { display: flex; flex-direction: column; gap: 4px; padding: 8px 12px 0; font-size: 12.5px; color: #475467; }
.tip { margin: 6px 12px 0; font-size: 11.5px; color: #98a2b3; line-height: 1.6; }
.picked { margin: 10px 12px 0; border-top: 1px dashed #eef1f6; padding-top: 8px; flex: 1; overflow: auto; }
.ph { font-size: 12px; color: #667085; margin-bottom: 6px; }
.row { display: flex; align-items: center; gap: 6px; font-size: 12.5px; padding: 3px 0; }
.no { width: 16px; color: #98a2b3; text-align: right; }
.nm { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini { width: 20px; height: 20px; border: 1px solid #e4e7ec; background: #fff; border-radius: 5px; font-size: 11px; color: #475467; cursor: pointer; }
.mini:disabled { opacity: .4; cursor: default; }
.wide { width: 100%; margin-top: 6px; }
.acts { display: flex; gap: 8px; padding: 10px 12px 0; }
.acts .pri { flex: 1; }
.pri.alt { background: #1c8c46; }
.ok { margin: 8px 12px 0; font-size: 12px; color: #157347; }
.bad { margin: 8px 12px 0; font-size: 12px; color: #b42318; }
</style>
