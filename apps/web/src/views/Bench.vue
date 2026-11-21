<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'

const kind = ref('初赛')
const refs = reactive({ items: [], median: null, count: 0 })
const mine = reactive({ title: '我的卷子', questions: 0, subs: 0, score: 0, pages: 0 })
const out = reactive({ rows: [], advice: [] })
const busy = ref(false)
const err = ref('')
const papers = ref([])

async function loadRefs() {
  err.value = ''
  try {
    const r = await api.get('/api/bench/refs?kind=' + encodeURIComponent(kind.value))
    refs.items = r.items || []
    refs.median = r.median
    refs.count = r.count || 0
  } catch (e) {
    err.value = api.errText(e)
  }
}

async function loadPapers() {
  try {
    const r = await api.get('/api/papers?limit=50')
    papers.value = Array.isArray(r) ? r : (r.items || [])
  } catch { /* 没有试卷也不影响手填 */ }
}

function pickPaper(id) {
  const p = papers.value.find((x) => String(x.id) === String(id))
  if (!p) return
  mine.title = p.title || '我的卷子'
  mine.questions = p.question_count || (p.items ? p.items.length : 0) || 0
  mine.subs = p.sub_count || 0
  mine.score = p.total_score || 0
  mine.pages = p.page_count || 0
}

async function run() {
  busy.value = true
  err.value = ''
  try {
    const r = await api.post('/api/bench/compare', { ...mine, kind: kind.value })
    out.rows = r.rows || []
    out.advice = r.advice || []
  } catch (e) {
    err.value = api.errText(e)
  } finally {
    busy.value = false
  }
}

onMounted(() => { loadRefs(); loadPapers() })
</script>

<template>
  <div class="bench">
    <h2>样卷对标</h2>
    <p class="mut lead">
      把自己出的卷子和真卷比一比题量、小问密度、每题占页。只比「结构」，不评价题目好坏——
      真卷的统计是扫 PDF 估出来的，看趋势够用。
    </p>

    <div class="card">
      <div class="row">
        <span class="lbl">参考卷库</span>
        <button class="sm" :class="{ on: kind === '初赛' }" @click="kind = '初赛'; loadRefs()">初赛</button>
        <button class="sm" :class="{ on: kind === '决赛' }" @click="kind = '决赛'; loadRefs()">决赛</button>
        <span class="mut" v-if="refs.median">
          扫到 {{ refs.count }} 份：中位数 {{ refs.median.questions }} 题 / {{ refs.median.subs }} 问 /
          {{ refs.median.pages }} 页
        </span>
      </div>
      <p v-if="err" class="bad">{{ err }}</p>
    </div>

    <div class="card">
      <h3>我这套</h3>
      <div class="grid">
        <label class="f"><span>卷名</span><input v-model="mine.title" /></label>
        <label class="f"><span>题数</span><input v-model.number="mine.questions" type="number" min="0" /></label>
        <label class="f"><span>小问数</span><input v-model.number="mine.subs" type="number" min="0" /></label>
        <label class="f"><span>总分</span><input v-model.number="mine.score" type="number" min="0" /></label>
        <label class="f"><span>页数（可留空）</span><input v-model.number="mine.pages" type="number" min="0" /></label>
        <label class="f" v-if="papers.length"><span>或从试卷里取</span>
          <select @change="pickPaper($event.target.value)">
            <option value="">选一份已存的试卷</option>
            <option v-for="p in papers" :key="p.id" :value="p.id">{{ p.title }}</option>
          </select>
        </label>
      </div>
      <button class="primary" :disabled="busy" @click="run">{{ busy ? '正在比…' : '开始比对' }}</button>
    </div>

    <div v-if="out.rows.length" class="card">
      <h3>对比结果</h3>
      <table class="cmp">
        <tr><th>项目</th><th>我这套</th><th>{{ kind }}中位数</th></tr>
        <tr v-for="r in out.rows" :key="r.k">
          <td>{{ r.k }}</td>
          <td class="mine">{{ r.mine || '—' }} {{ r.unit }}</td>
          <td>{{ r.ref || '—' }}</td>
        </tr>
      </table>
      <ul class="adv">
        <li v-for="(a, i) in out.advice" :key="i">{{ a }}</li>
      </ul>
    </div>

    <div class="card" v-if="refs.items.length">
      <h3>{{ kind }}参考卷（前 20 份）</h3>
      <table class="list">
        <tr><th>文件</th><th>页</th><th>题</th><th>小问</th><th>页/题</th></tr>
        <tr v-for="it in refs.items.slice(0, 20)" :key="it.path">
          <td class="nm">{{ it.name }}</td>
          <td>{{ it.pages }}</td>
          <td>{{ it.questions }}</td>
          <td>{{ it.subs }}</td>
          <td>{{ it.per_q }}</td>
        </tr>
      </table>
    </div>
  </div>
</template>

<style scoped>
.bench { display: flex; flex-direction: column; gap: 12px; padding-bottom: 30px; }
.lead { font-size: 13px; line-height: 1.8; margin: -4px 0 2px; }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.row .lbl { font-size: 13px; color: #667085; }
.row .sm { height: 28px; padding: 0 12px; border-radius: 999px; border: 1px solid var(--line); background: #fff; cursor: pointer; }
.row .sm.on { background: #2f6fed; border-color: #2f6fed; color: #fff; font-weight: 600; }
.grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin: 6px 0 12px; }
.f { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.f span { color: #667085; }
.f input, .f select { height: 32px; border: 1px solid var(--line); border-radius: 8px; padding: 0 9px; font: inherit; }
table.cmp { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 10px; }
table.cmp th, table.cmp td { border-bottom: 1px solid var(--line); padding: 7px 6px; text-align: left; }
table.cmp th { color: #667085; font-weight: 600; }
table.cmp td.mine { color: #1a5fd0; font-weight: 600; }
.adv { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.9; color: #344054; }
table.list { width: 100%; border-collapse: collapse; font-size: 12.5px; }
table.list th, table.list td { border-bottom: 1px solid var(--line); padding: 5px 6px; text-align: left; }
table.list th { color: #667085; }
table.list td.nm { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bad { color: #b42318; font-size: 13px; }
</style>
