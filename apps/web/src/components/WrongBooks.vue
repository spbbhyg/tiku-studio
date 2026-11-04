<script setup>
/** 错题集：可建多本、自己命名，题目从题库卡片「+ 错题集」加进来 */
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
import { useRouter } from 'vue-router'

const router = useRouter()
const books = ref([])
const name = ref('')
const err = ref('')
const msg = ref('')
const openId = ref(null)
const items = ref([])
const loadingItems = ref(false)

async function load() {
  err.value = ''
  try { const d = await api.get('/api/wb/wrongbooks'); books.value = d.books || d || [] } catch (e) { err.value = e.message }
}
onMounted(load)

async function create() {
  const n = name.value.trim()
  if (!n) return
  try {
    await api.post('/api/wb/wrongbooks', { name: n, note: '' })
    name.value = ''
    msg.value = `已创建「${n}」`
    await load()
  } catch (e) { err.value = e.message }
}

async function rename(b) {
  const n = window.prompt('改名', b.name)
  if (!n || n === b.name) return
  try { await api.patch(`/api/wb/wrongbooks/${b.id}`, { name: n.trim() }); await load() }
  catch (e) { err.value = e.message }
}

async function remove(b) {
  if (!window.confirm(`删除错题集「${b.name}」？里面的题目不会被删。`)) return
  try { await api.del(`/api/wb/wrongbooks/${b.id}`); if (openId.value === b.id) openId.value = null; await load() }
  catch (e) { err.value = e.message }
}

async function toggle(b) {
  if (openId.value === b.id) { openId.value = null; items.value = []; return }
  openId.value = b.id
  loadingItems.value = true
  try { items.value = await api.get(`/api/wb/wrongbooks/${b.id}/items`) }
  catch (e) { err.value = e.message }
  finally { loadingItems.value = false }
}

async function drop(qid) {
  try {
    await api.del(`/api/wb/wrongbooks/${openId.value}/items/${qid}`)
    items.value = items.value.filter(x => x.id !== qid)
    await load()
  } catch (e) { err.value = e.message }
}
</script>

<template>
  <div class="card wb">
    <div class="wbhead">
      <h3>错题集</h3>
      <span class="muted">{{ books.length }} 本</span>
      <div class="wbmake">
        <input v-model="name" placeholder="新建一本，如：电化学易错" @keyup.enter="create" />
        <button class="primary sm" @click="create">新建</button>
      </div>
    </div>
    <p class="muted hint">
      在题库卡片上点「+ 错题集」把题加进来；错题集只存引用，删错题集不会删题目。
    </p>
    <div v-if="err" class="errbox">{{ err }}</div>
    <div v-if="msg" class="okbox">{{ msg }}</div>

    <table class="tbl" v-if="books.length">
      <thead><tr><th>名称</th><th>题数</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="b in books" :key="b.id">
          <td><b>{{ b.name }}</b><span v-if="b.note" class="muted"> · {{ b.note }}</span></td>
          <td>{{ b.count }}</td>
          <td class="ops">
            <button class="ghost sm" @click="router.push(`/wrong/${b.id}`)">看题</button>
            <button class="ghost sm" @click="rename(b)">改名</button>
            <button class="ghost sm danger" @click="remove(b)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted">还没有错题集，上面输入名字建一本。</p>

    <div v-if="openId" class="wbitems">
      <div v-if="loadingItems" class="muted">加载中…</div>
      <div v-else-if="!items.length" class="muted">这本是空的。</div>
      <div v-for="it in items" :key="it.id" class="wbitem">
        <div class="stem" v-html="it.stem || '（纯图片题）'"></div>
        <span class="chip sm2" v-if="it.type">{{ it.type }}</span>
        <span class="muted">错 {{ it.wrong_count }} 次</span>
        <button class="ghost sm" @click="drop(it.id)">移出</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wbhead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.wbmake { margin-left: auto; display: flex; gap: 6px; }
.wbmake input { min-width: 200px; }
.hint { margin: 6px 0 10px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #f1f2f4; }
.tbl th { color: #6b7280; font-weight: 500; }
.ops { display: flex; gap: 6px; }
.wbitems { margin-top: 10px; border-top: 1px dashed #e6e8ee; padding-top: 10px; }
.wbitem { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid #f6f7f9; }
.stem { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.okbox { background: #ecfdf5; color: #065f46; border-radius: 8px; padding: 6px 10px; font-size: 13px; margin: 6px 0; }
</style>
