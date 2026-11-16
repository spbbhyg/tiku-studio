<script setup>
// 草稿箱：出到一半关掉页面也不丢，回来在这儿接着写（第 11 条）
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()
const rows = ref([])
const busy = ref(false)
const msg = ref('')
const err = ref('')

async function load() {
  busy.value = true
  err.value = ''
  try {
    const r = await api.get('/api/drafts')
    rows.value = r.drafts || []
  } catch (e) {
    err.value = e?.message || String(e)
  } finally { busy.value = false }
}

function cont(d) {
  router.push({ path: '/author', query: { draft: d.id } })
}

async function del(d) {
  if (!window.confirm(`删掉草稿「${d.title}」？删了就找不回来了。`)) return
  try {
    await api.del('/api/drafts/' + d.id)
    msg.value = '已删除'
    await load()
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

function ago(s) {
  if (!s) return ''
  const t = new Date(String(s).replace(' ', 'T')).getTime()
  if (!t) return s
  const m = Math.round((Date.now() - t) / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  if (m < 60 * 24) return `${Math.round(m / 60)} 小时前`
  return String(s).slice(0, 10)
}

onMounted(load)
</script>

<template>
  <div class="pg">
    <div class="head">
      <div>
        <h2>草稿箱</h2>
        <p class="sub">出题过程每 3 秒自动留一份在这里；关掉页面、断电重开都能接着写。保存进本地题库之后草稿会自动清掉。</p>
      </div>
      <div class="acts">
        <button class="ghost" :disabled="busy" @click="load">{{ busy ? '刷新中…' : '刷新' }}</button>
        <button class="pri" @click="router.push('/author')">＋ 新出一题</button>
      </div>
    </div>

    <div v-if="msg" class="ok">{{ msg }}</div>
    <div v-if="err" class="bad">{{ err }}</div>

    <div v-if="!rows.length && !busy" class="empty">
      <p>还没有草稿。</p>
      <p class="tip">去「出题」写一道，写一半直接关掉页面，回来这里就能接着写。</p>
    </div>

    <div class="grid">
      <div v-for="d in rows" :key="d.id" class="card">
        <div class="ct">{{ d.title }}</div>
        <div class="meta">
          <span>{{ d.mode === 'text' ? '单题' : '画布' }}</span>
          <span>{{ d.blocks }} 个元素</span>
          <span>{{ ago(d.updated_at) }}</span>
        </div>
        <div class="row">
          <button class="pri sm" @click="cont(d)">继续编辑</button>
          <button class="ghost sm" @click="del(d)">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pg { max-width: 1080px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.head h2 { margin: 0 0 6px; font-size: 20px; }
.sub { margin: 0; color: #667085; font-size: 13px; line-height: 1.6; }
.acts { display: flex; gap: 8px; flex: 0 0 auto; }
.ok { margin: 0 0 12px; padding: 8px 12px; border-radius: 8px; background: #f2fbf5; color: #157347; font-size: 13px; border: 1px solid #cdeed8; }
.bad { margin: 0 0 12px; padding: 8px 12px; border-radius: 8px; background: #fef3f2; color: #b42318; font-size: 13px; border: 1px solid #fbd5d2; }
.empty { padding: 40px; text-align: center; color: #667085; background: #fff; border: 1px dashed #e4e7ec; border-radius: 12px; }
.empty .tip { font-size: 12.5px; color: #98a2b3; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.card { background: #fff; border: 1px solid #e9edf3; border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.ct { font-size: 14px; font-weight: 600; color: #1d2939; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { display: flex; gap: 10px; font-size: 12px; color: #98a2b3; }
.row { display: flex; gap: 8px; margin-top: auto; }
</style>
