<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const papers = ref([])
const err = ref('')

async function load() {
  try { papers.value = await api.get('/api/papers') } catch (e) { err.value = e.message }
}
async function remove(id) {
  if (!confirm('删除这份试卷？')) return
  try { await api.del(`/api/papers/${id}`); load() } catch (e) { err.value = e.message }
}
onMounted(load)
</script>

<template>
  <h2>试卷</h2>
  <div v-if="err" class="errbox">{{ err }}</div>
  <div class="card">
    <p v-if="!papers.length" class="muted">还没有试卷，去<a href="#/compose"> 组卷 </a>生成。</p>
    <table v-else class="table">
      <tr><th>标题</th><th>题数</th><th>总分</th><th>限时</th><th>操作</th></tr>
      <tr v-for="p in papers" :key="p.id">
        <td><router-link :to="`/papers/${p.id}`">{{ p.title }}</router-link></td>
        <td>{{ p.question_count }}</td><td>{{ p.total_score }}</td><td>{{ p.duration_minutes }} 分钟</td>
        <td class="btnrow">
          <a :href="`/api/papers/${p.id}/export`"><button>下载 PDF</button></a>
          <a :href="`/api/papers/${p.id}/view`" target="_blank"><button>打印视图</button></a>
          <button class="danger" @click="remove(p.id)">删除</button>
        </td>
      </tr>
    </table>
  </div>
</template>
