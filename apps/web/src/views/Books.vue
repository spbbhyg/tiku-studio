<script setup>
import { onMounted, ref } from 'vue'
import { api, ROLE_LABEL } from '../api.js'

const books = ref([])
const details = ref({})
const inbox = ref([])
const err = ref('')
const msg = ref('')

const ROLES = ['textbook', 'workbook', 'paper_set', 'self_made']
const ROLE_DESC = {
  textbook: '课本 = 『纲』。它的单元知识点是对纲校验与组卷范围的标尺。',
  workbook: '教辅（如 53天天练、实验班）',
  paper_set: '试卷集',
  self_made: '自编卷',
}

async function load() {
  try {
    books.value = await api.get('/api/books')
    inbox.value = await api.get('/api/ingest/inbox')
  } catch (e) { err.value = e.message }
}
async function toggle(b) {
  if (details.value[b.id]) { delete details.value[b.id]; return }
  try { details.value[b.id] = await api.get(`/api/books/${b.id}`) } catch (e) { err.value = e.message }
}
async function importInbox(name) {
  err.value = ''; msg.value = ''
  try {
    const r = await api.post(`/api/ingest/inbox/${encodeURIComponent(name)}/import`)
    msg.value = `已导入：${r.kind === 'catalog' ? '目录' : r.questions_new + ' 新题 / ' + r.questions_updated + ' 更新'}`
    await load()
  } catch (e) { err.value = e.message }
}
async function removeInbox(name) {
  try { await api.del(`/api/ingest/inbox/${encodeURIComponent(name)}`); await load() } catch (e) { err.value = e.message }
}
onMounted(load)
</script>

<template>
  <h2>资料库</h2>
  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="warnbox" style="background:#e7f7ec;border-color:#9bdcb0;color:#116632">{{ msg }}</div>

  <div class="card" v-if="inbox.length">
    <h3>收件箱（Agent 拆解产物 data/inbox/）</h3>
    <table class="table">
      <tr><th>文件</th><th>大小</th><th>操作</th></tr>
      <tr v-for="f in inbox" :key="f.file">
        <td>{{ f.file }}</td><td>{{ (f.size / 1024).toFixed(1) }} KB</td>
        <td class="btnrow">
          <button class="primary" @click="importInbox(f.file)">校验并导入</button>
          <button class="danger" @click="removeInbox(f.file)">丢弃</button>
        </td>
      </tr>
    </table>
  </div>

  <div v-for="role in ROLES" :key="role">
    <div v-if="books.some(b => b.role === role)" class="card">
      <h3>{{ ROLE_LABEL[role] }} <span class="muted">· {{ ROLE_DESC[role] }}</span></h3>
      <div v-for="b in books.filter(x => x.role === role)" :key="b.id" style="border-top:1px solid var(--line);padding:10px 0">
        <div class="btnrow" style="justify-content:space-between">
          <b>{{ b.title }}</b>
          <span class="muted">
            {{ b.publisher }} · {{ b.grade }}年级{{ b.semester }} · {{ b.edition_year }}版
            <button style="margin-left:10px" @click="toggle(b)">{{ details[b.id] ? '收起' : '目录' }}</button>
          </span>
        </div>
        <div v-if="details[b.id]" style="margin-top:10px">
          <div v-for="u in details[b.id].units" :key="u.id" style="margin-bottom:10px">
            <b>{{ u.seq }}. {{ u.name }}</b>
            <span class="chip" style="margin-left:8px">{{ u.question_count }} 题</span>
            <div><span v-for="kp in u.knowledge_points" :key="kp" class="chip">{{ kp }}</span></div>
            <div v-if="u.lessons.length" class="muted">课时：{{ u.lessons.map(l => l.name + (l.pages ? `(${l.pages})` : '')).join(' / ') }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
