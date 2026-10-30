<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const stats = ref(null)
const papers = ref([])
const err = ref('')

onMounted(async () => {
  try {
    stats.value = await api.get('/api/stats')
    papers.value = (await api.get('/api/papers')).slice(0, 5)
  } catch (e) { err.value = String(e.message || e) }
})
</script>

<template>
  <h2>首页</h2>
  <div v-if="err" class="errbox">{{ err }}</div>
  <template v-if="stats">
    <div class="grid cols4">
      <div class="card stat"><div class="num">{{ stats.books }}</div><div class="label">书册（含 {{ stats.textbooks }} 本课本纲）</div></div>
      <div class="card stat"><div class="num">{{ stats.questions }}</div><div class="label">题目（已确认 {{ stats.confirmed_questions }}）</div></div>
      <div class="card stat"><div class="num">{{ stats.draft_questions }}</div><div class="label">草稿题目</div></div>
      <div class="card stat"><div class="num">{{ stats.suspect_questions }}</div><div class="label">疑似超纲（对纲校验）</div></div>
    </div>
    <div class="card">
      <h3>科目分布</h3>
      <span v-for="(n, s) in stats.by_subject" :key="s" class="chip brand">{{ s }}：{{ n }} 题</span>
      <h3>题型分布</h3>
      <span v-for="(n, t) in stats.by_type" :key="t" class="chip">{{ t }} {{ n }}</span>
    </div>
    <div class="card">
      <h3>最近试卷</h3>
      <p v-if="!papers.length" class="muted">还没有试卷，去<a href="#/compose"> 组卷 </a>生成第一份。</p>
      <table v-else class="table">
        <tr><th>标题</th><th>题数</th><th>总分</th><th>限时</th></tr>
        <tr v-for="p in papers" :key="p.id">
          <td><router-link :to="`/papers/${p.id}`">{{ p.title }}</router-link></td>
          <td>{{ p.question_count }}</td><td>{{ p.total_score }}</td><td>{{ p.duration_minutes }} 分钟</td>
        </tr>
      </table>
    </div>
  </template>
</template>
