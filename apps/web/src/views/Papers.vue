<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()
const papers = ref([])
const err = ref('')
const picked = ref(new Set())
const mergeBusy = ref(false)
const mergeTitle = ref('')
const exportlogs = ref([])
const logPicked = ref([])
const logAll = computed(() => exportlogs.value.length > 0 && logPicked.value.length === exportlogs.value.length)
function toggleLog(id) {
  const i = logPicked.value.indexOf(id)
  if (i >= 0) logPicked.value.splice(i, 1)
  else logPicked.value.push(id)
}
function toggleAllLogs() {
  logPicked.value = logAll.value ? [] : exportlogs.value.map((l) => l.id)
}
async function delLogs() {
  if (!logPicked.value.length) return
  if (!confirm(`删除选中的 ${logPicked.value.length} 条导出记录？（只删记录，不动 PDF 文件）`)) return
  try {
    await api.post('/api/wb/export-logs/delete', { ids: logPicked.value })
    logPicked.value = []
    await loadLogs()
  } catch (e) { err.value = String(e.message || e) }
}
const showLogs = ref(false)
const draftCount = ref(0)

async function loadDrafts() {
  try { const d = await api.get('/api/exam/drafts'); draftCount.value = d.count || 0 }
  catch (e) { /* 没有草稿接口时不影响试卷列表 */ }
}

async function load() {
  try { papers.value = await api.get('/api/papers') } catch (e) { err.value = e.message }
  loadDrafts()
}
async function remove(id) {
  if (!confirm('删除这份试卷？')) return
  try {
    await api.del(`/api/papers/${id}`)
    const s = new Set(picked.value); s.delete(id); picked.value = s
    load()
  } catch (e) { err.value = e.message }
}
async function backToDraft(id) {
  if (!confirm('退回草稿？退回后会从试卷列表消失，回到「出整卷」里继续改。')) return
  try {
    await api.post(`/api/exam/papers/${id}/reopen`)
    await load()
  } catch (e) { err.value = e.message }
}
function togglePick(id) {
  const s = new Set(picked.value)
  if (s.has(id)) s.delete(id); else s.add(id)
  picked.value = s
}
async function mergePapers() {
  const ids = [...picked.value]
  if (ids.length < 2) { err.value = '至少勾选两套试卷'; return }
  mergeBusy.value = true
  err.value = ''
  try {
    const r = await api.post('/api/papers/merge', { paper_ids: ids, title: mergeTitle.value || undefined })
    router.push(`/papers/${r.id}`)
  } catch (e) { err.value = e.message } finally { mergeBusy.value = false }
}
async function loadLogs() {
  try { const d = await api.get('/api/wb/export-logs?limit=20'); exportlogs.value = d.logs || [] }
  catch (e) { err.value = e.message }
}
function toggleLogs() { showLogs.value = !showLogs.value; if (showLogs.value) loadLogs() }

onMounted(load)
</script>

<template>
  <h2>试卷</h2>

<div class="card logbar">
  <button class="ghost" @click="toggleLogs">{{ showLogs ? '收起导出记录' : '导出记录（哪版题目生成哪版 PDF）' }}</button>
  <span class="muted" v-if="showLogs">最近 {{ exportlogs.length }} 条</span>
  <button v-if="showLogs && exportlogs.length" class="ghost sm" @click="toggleAllLogs">{{ logAll ? '取消全选' : '全选' }}</button>
  <button v-if="showLogs && logPicked.length" class="ghost sm" @click="delLogs">删除所选（{{ logPicked.length }}）</button>
</div>

<div v-if="showLogs" class="card">
  <table class="tbl" v-if="exportlogs.length">
    <thead>
      <tr><th style="width:28px"></th><th>时间</th><th>试卷</th><th>类型</th><th>题数</th><th>总分</th><th>题目版本指纹</th><th>文件</th></tr>
    </thead>
    <tbody>
      <tr v-for="l in exportlogs" :key="l.id">
        <td><input type="checkbox" :checked="logPicked.includes(l.id)" @change="toggleLog(l.id)" /></td>
        <td class="muted">{{ new Date(l.created_at).toLocaleString() }}</td>
        <td>{{ l.title }}</td>
        <td>{{ l.kind }}</td>
        <td>{{ l.item_count }}</td>
        <td>{{ l.total_score }}</td>
        <td class="mono">{{ l.fingerprint }}</td>
        <td class="muted">{{ l.file_name || '—' }}<template v-if="l.file_size"> · {{ (l.file_size/1024).toFixed(0) }} KB</template></td>
      </tr>
    </tbody>
  </table>
  <p v-else class="muted">还没有导出记录。到试卷详情页导出一次 PDF 就会记下来。</p>
</div>
  <div v-if="err" class="errbox">{{ err }}</div>
  <div class="card">
    <div class="btnrow" style="margin-bottom:10px">
      <button class="primary" @click="router.push('/exam')">出整卷</button>
      <button class="ghost" @click="router.push('/exam')">
        查看草稿<template v-if="draftCount">（{{ draftCount }}）</template>
      </button>
      <span class="muted">人工整卷在「出整卷」里编辑；没出完的算草稿，出完后才出现在下面这张表里</span>
    </div>
  </div>
  <div class="card">
    <p v-if="!papers.length" class="muted">还没有试卷，去<a href="#/questions"> 题库 </a>选题组卷生成。</p>
    <template v-else>
      <div class="btnrow" style="margin-bottom:10px">
        <span class="muted">勾选两套及以上可融合；融合后大题号自动顺延（1-2 → 2-2，小问号不变）</span>
        <span class="spacer"></span>
        <input v-model="mergeTitle" placeholder="融合卷名（可空）" style="max-width:220px" />
        <button class="primary" :disabled="mergeBusy || picked.size < 2" @click="mergePapers">
          {{ mergeBusy ? '融合中…' : `融合所选试卷（${picked.size}）` }}
        </button>
      </div>
      <table class="table">
        <tr><th></th><th>标题</th><th>题数</th><th>总分</th><th>限时</th><th>操作</th></tr>
        <tr v-for="p in papers" :key="p.id">
          <td><input type="checkbox" :checked="picked.has(p.id)" @change="togglePick(p.id)" /></td>
          <td>
            <router-link :to="p.kind === 'manual' ? `/papers/${p.id}/edit` : `/papers/${p.id}`">{{ p.title }}</router-link>
            <span v-if="p.kind === 'manual'" class="chip brand" style="margin-left:6px">人工整卷</span>
          </td>
          <td>{{ p.question_count }}</td><td>{{ p.total_score }}</td><td>{{ p.duration_minutes }} 分钟</td>
          <td class="btnrow">
            <template v-if="p.kind === 'manual'">
              <button @click="router.push(`/papers/${p.id}/edit`)">编辑</button>
              <button class="ghost" title="退回「出整卷」继续改" @click="backToDraft(p.id)">改回草稿</button>
            </template>
            <router-link :to="`/papers/${p.id}`"><button>打开 / 导出</button></router-link>
            <button class="danger" @click="remove(p.id)">删除</button>
          </td>
        </tr>
      </table>
    </template>
  </div>
</template>
