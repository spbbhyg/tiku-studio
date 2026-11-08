<script setup>
/**
 * 整卷导入 · 人工校对
 *
 * 流程：选题目卷 PDF（+ 可选参考答案 PDF）→ 独立进程解析成结构化题面 →
 * 这里逐题看一遍（确认 / 跳过 / 合并到上一题）→ 批量入库。
 *
 * 解析规则在 tools/pdf_import/cli.py，只通过 JSON 交换，主程序不 import PyMuPDF。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import QuestionSheet from '../components/QuestionSheet.vue'
import { taskBegin, taskEnd, toast } from '../tasks.js'

const router = useRouter()
const fileQ = ref(null)
const fileA = ref(null)
const busy = ref(false)
const err = ref('')
const tip = ref('')
const stats = ref(null)
const docId = ref('')
const items = ref([])          // [{ doc, use: true }]
const open = ref(-1)           // 展开预览的下标

const SNAP = 'tiku-ingest-draft'

function saveDraft() {
  try {
    sessionStorage.setItem(SNAP, JSON.stringify({
      stats: stats.value, docId: docId.value,
      name: (fileQ.value && fileQ.value.name) || '',
      items: items.value.map((x) => ({ doc: x.doc, use: x.use })),
    }))
  } catch (e) { /* 超出配额就放弃，不影响主流程 */ }
}

function clearDraft() {
  try { sessionStorage.removeItem(SNAP) } catch (e) { /* 忽略 */ }
}

onMounted(() => {
  // 切走再回来：上次解析的草稿还在，不用重新传 PDF 等 6 秒
  try {
    const raw = sessionStorage.getItem(SNAP)
    if (!raw) return
    const d = JSON.parse(raw)
    if (!d || !d.items || !d.items.length) return
    stats.value = d.stats || null
    docId.value = d.docId || ''
    items.value = d.items
    tip.value = `继续上次的草稿：${d.items.length} 道大题（${d.name || '已解析'}），勾选后可直接入库`
  } catch (e) { /* 坏数据就当没有 */ }
})

const picked = computed(() => items.value.filter((x) => x.use).length)
const totalScore = computed(() => items.value.filter((x) => x.use)
  .reduce((s, x) => s + Number((x.doc.meta || {}).score || 0), 0))

function onPick(e, which) {
  const f = e.target.files && e.target.files[0]
  if (which === 'q') fileQ.value = f || null
  else fileA.value = f || null
  if (which === 'q' && f) tip.value = ''
}

async function parse() {
  err.value = ''
  tip.value = ''
  if (!fileQ.value) { err.value = '先选一份题目卷 PDF'; return }
  busy.value = true
  const t0 = performance.now()
  const t = taskBegin('正在解析 PDF（可以切到别的页面，回来还在）…')
  try {
    const fd = new FormData()
    fd.append('file', fileQ.value)
    if (fileA.value) fd.append('answer', fileA.value)
    fd.append('dpi', '300')
    const r = await api.postForm('/api/authoring/pdf-parse', fd)
    stats.value = { ...r.stats, seconds: ((performance.now() - t0) / 1000).toFixed(1) }
    docId.value = r.doc_id
    items.value = (r.items || []).map((d) => ({ doc: d, use: true }))
    open.value = -1
    tip.value = `解析完成：${items.value.length} 道大题，${stats.value.subs} 个小问，图 ${stats.value.images} 张，用时 ${stats.value.seconds} 秒`
    saveDraft()
  } catch (e) {
    err.value = e.message
    toast(`解析失败：${e.message}`, 'bad', 8000)
  } finally {
    taskEnd(t)
    busy.value = false
    if (items.value.length) {
      toast(`解析完成：${items.value.length} 道大题 ${stats.value ? stats.value.subs : ''} 个小问`)
    }
  }
}

function subsOf(doc) {
  const walk = (arr) => (arr || []).reduce((n, s) => n + 1 + walk(s.subs), 0)
  return walk(doc.subs)
}

function toggle(i) { open.value = open.value === i ? -1 : i }

function mergeUp(i) {
  if (i <= 0) return
  const prev = items.value[i - 1]
  const cur = items.value[i]
  // 合并：题干接在上一题后面，小问挪过去
  const add = (cur.doc.stem || []).filter((b) => b.t === 'p' && (b.text || '').trim())
  if (add.length) {
    const joined = add.map((b) => b.text).join('\n')
    prev.doc.stem.push({ t: 'p', text: joined })
  }
  prev.doc.subs.push(...(cur.doc.subs || []))
  prev.doc.meta.score = Number(prev.doc.meta.score || 0) + Number(cur.doc.meta.score || 0)
  items.value.splice(i, 1)
  open.value = -1
}

async function doImport() {
  if (!picked.value) { err.value = '一道题都没勾'; return }
  if (!confirm(`把勾选的 ${picked.value} 道题写进题库？`)) return
  busy.value = true
  err.value = ''
  try {
    const docs = items.value.filter((x) => x.use).map((x) => x.doc)
    const t = taskBegin('正在把题目写入题库…')
    const r = await api.post('/api/authoring/pdf-import', {
      docs,
      source_title: (fileQ.value && fileQ.value.name) || 'PDF 导入',
      status: 'confirmed',
    })
    taskEnd(t)
    if (r.failed && r.failed.length) {
      err.value = `${r.count} 道入库，${r.failed.length} 道失败：` +
        r.failed.map((f) => `#${f.index} ${f.error}`).join('；')
      toast(`${r.count} 道已入库，${r.failed.length} 道失败`, 'bad', 9000)
    } else {
      clearDraft()
      items.value = []
      stats.value = null
      tip.value = ''
      toast(`${r.count} 道题已入库，正在跳到题库…`)
      setTimeout(() => router.push('/questions'), 700)
    }
  } catch (e) {
    taskEnd(t)
    err.value = e.message
    toast(`入库失败：${e.message}`, 'bad', 9000)
  } finally { busy.value = false }
}
</script>

<template>
  <h2>整卷导入</h2>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="tip" class="card tipcard">{{ tip }}</div>

  <div class="card">
    <div class="pickrow">
      <label class="pick">
        <span>题目卷 PDF</span>
        <input type="file" accept="application/pdf,.pdf" @change="onPick($event, 'q')">
      </label>
      <label class="pick">
        <span>参考答案 PDF（可选，会给小问配上答案与分值）</span>
        <input type="file" accept="application/pdf,.pdf" @change="onPick($event, 'a')">
      </label>
      <button class="primary" :disabled="busy || !fileQ" @click="parse">
        {{ busy ? '正在解析…' : '解析成结构化题面' }}
      </button>
    </div>
    <p class="muted small">
      解析在独立进程里跑（PyMuPDF 是 AGPL，主程序不 import），6 页卷约 6 秒。
      解析结果只是草稿，下面逐题看过再入库；题库里不会留下整页图。
    </p>
  </div>

  <div v-if="stats" class="card statbar">
    <span>页数 <b>{{ stats.pages }}</b></span>
    <span>大题 <b>{{ stats.items }}</b></span>
    <span>小问 <b>{{ stats.subs }}</b></span>
    <span>分值合计 <b>{{ stats.score }}</b></span>
    <span>图 <b>{{ stats.images }}</b> 张</span>
    <span class="muted">用时 {{ stats.seconds }} 秒 · 文献 {{ docId }}</span>
  </div>

  <div v-if="items.length" class="card">
    <div class="btnrow">
      <button class="primary" :disabled="busy" @click="doImport">
        入库勾选的 {{ picked }} 道题（合计 {{ totalScore }} 分）
      </button>
      <span class="muted">标题会一起进题干，题库里按题干就能搜到题名</span>
    </div>

    <table class="table">
      <thead>
        <tr>
          <th style="width:42px">用</th>
          <th style="width:56px">题号</th>
          <th>题名</th>
          <th style="width:70px">分值</th>
          <th style="width:70px">小问</th>
          <th style="width:70px">图</th>
          <th style="width:230px">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(it, i) in items" :key="i" :class="{ dim: !it.use }">
          <td><input v-model="it.use" type="checkbox"></td>
          <td>{{ it.doc.meta.number }}</td>
          <td>
            <a href="javascript:void(0)" @click="toggle(i)">{{ it.doc.meta.title || '(无题名)' }}</a>
          </td>
          <td>{{ it.doc.meta.score }} 分</td>
          <td>{{ subsOf(it.doc) }}</td>
          <td>{{ (it.doc.stem || []).filter((b) => b.t === 'img').length }}</td>
          <td class="ops">
            <button class="mini" @click="toggle(i)">{{ open === i ? '收起' : '预览' }}</button>
            <button class="mini" :disabled="i === 0" @click="mergeUp(i)">并入上一题</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="open >= 0 && items[open]" class="preview">
      <div class="pvhead">
        预览第 {{ items[open].doc.meta.number }} 题
        <button class="mini" @click="open = -1">关闭</button>
      </div>
      <QuestionSheet :doc="items[open].doc" variant="card" show-answers />
    </div>
  </div>
</template>

<style scoped>
.pickrow { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
.pick { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.pick span { color: #555; }
.small { font-size: 12px; }
.statbar { display: flex; gap: 22px; align-items: baseline; flex-wrap: wrap; font-size: 13px; }
.statbar b { font-size: 15px; }
.table td.ops { white-space: nowrap; }
.table tr.dim { opacity: .45; }
.preview { margin-top: 14px; border-top: 1px dashed var(--line); padding-top: 12px; }
.pvhead { display: flex; justify-content: space-between; align-items: center; font-size: 13px; color: #555; margin-bottom: 10px; }
.pvhead .mini { margin-left: 12px; }
</style>
