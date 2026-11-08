<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()
const drafts = ref([])
const busy = ref(false)
const err = ref('')
const tip = ref('')

async function load() {
  try {
    const d = await api.get('/api/exam/drafts')
    drafts.value = d.drafts || []
  } catch (e) { err.value = e.message }
}

async function create() {
  busy.value = true
  err.value = ''
  try {
    const r = await api.post('/api/exam/papers', { title: '新建整卷' })
    router.push(`/papers/${r.id}/edit`)
  } catch (e) { err.value = e.message } finally { busy.value = false }
}

function cont(id) { router.push(`/papers/${id}/edit`) }

async function finish(d) {
  try {
    await api.post(`/api/exam/papers/${d.id}/finish`)
    tip.value = `《${d.title}》已出完，已放进试卷列表`
    await load()
    setTimeout(() => router.push('/papers'), 700)
  } catch (e) { err.value = e.message }
}

async function remove(d) {
  if (!confirm(`删除草稿《${d.title}》？删了就找不回来了。`)) return
  try {
    await api.del(`/api/papers/${d.id}`)
    await load()
  } catch (e) { err.value = e.message }
}

function when(s) {
  if (!s) return ''
  const t = new Date(s.replace(' ', 'T'))
  if (Number.isNaN(t.getTime())) return s.slice(0, 16)
  return t.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(load)
</script>

<template>
  <h2>出整卷</h2>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="tip" class="card tipcard">{{ tip }}</div>

  <div class="card">
    <div class="btnrow">
      <button class="primary" :disabled="busy" @click="create">
        {{ busy ? '正在新建…' : '+ 新建整卷' }}
      </button>
      <span class="muted">
        在这里录大题 / 小问、从文献裁图、插公式，导出「题目卷 + 答案卷」。
        <b>没出完就是草稿</b>，出完（导出 PDF 或点「完成出卷」）才进试卷列表。
      </span>
    </div>
  </div>

  <div class="card">
    <div class="btnrow" style="margin-bottom:10px">
      <b>草稿（{{ drafts.length }}）</b>
      <span class="muted">可留多份，随时接着改</span>
      <span class="spacer"></span>
      <button class="ghost sm" @click="load">刷新</button>
    </div>

    <p v-if="!drafts.length" class="muted">
      还没有草稿。点上面的「+ 新建整卷」开始出卷；出完的卷子在<a href="#/papers"> 试卷 </a>里。
    </p>

    <table v-else class="table">
      <tr><th>卷名</th><th>大题</th><th>小问</th><th>总分</th><th>建卷时间</th><th>操作</th></tr>
      <tr v-for="d in drafts" :key="d.id">
        <td>
          <a href="javascript:void 0" @click="cont(d.id)">{{ d.title }}</a>
          <span class="chip" style="margin-left:6px">草稿</span>
        </td>
        <td>{{ d.questions }}</td>
        <td>{{ d.subs }}</td>
        <td>{{ d.total_score }}</td>
        <td class="muted">{{ when(d.created_at) }}</td>
        <td class="btnrow">
          <button class="primary" @click="cont(d.id)">继续编辑</button>
          <button @click="finish(d)">完成出卷</button>
          <button class="danger" @click="remove(d)">删除</button>
        </td>
      </tr>
    </table>
  </div>
</template>

<style scoped>
.tipcard { border-left: 3px solid var(--brand); color: var(--brand); }
.table a { color: var(--brand); text-decoration: none; }
</style>
