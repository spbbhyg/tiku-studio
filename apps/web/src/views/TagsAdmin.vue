<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'

const tags = ref([])
const groups = computed(() => {
  const m = new Map()
  for (const t of tags.value) {
    const g = t.group || '其它'
    if (!m.has(g)) m.set(g, [])
    m.get(g).push(t)
  }
  return [...m.entries()]
})

const form = reactive({ name: '', group: '专题' })
const bulk = ref('')
const bulkGroup = ref('专题')
const msg = ref('')
const err = ref('')
const editing = reactive({ id: 0, name: '' })
const q = ref('')

const shown = (list) => list.filter((t) => !q.value || t.name.includes(q.value))

async function load() {
  try {
    const d = await api.get('/api/authoring/tags')
    tags.value = Array.isArray(d) ? d : (d.tags || [])
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

async function add() {
  if (!form.name.trim()) return
  err.value = ''; msg.value = ''
  try {
    await api.post('/api/authoring/tags', { name: form.name.trim(), group: form.group || '专题' })
    form.name = ''
    await load()
    msg.value = '已新增标签'
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

function startRename(t) {
  editing.id = t.id
  editing.name = t.name
}

async function doRename(t) {
  if (!editing.name.trim() || editing.name === t.name) { editing.id = 0; return }
  try {
    await api.put(`/api/authoring/tags/${t.id}`, { name: editing.name.trim(), group: t.group })
    editing.id = 0
    await load()
    msg.value = '已改名（题目里的旧标签同步更新）'
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

async function remove(t) {
  if (!confirm(`删除标签「${t.name}」？已打该标签的题目会保留其余标签。`)) return
  try {
    await api.del(`/api/authoring/tags/${t.id}`)
    await load()
    msg.value = '已删除'
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

async function importBulk() {
  if (!bulk.value.trim()) return
  try {
    const r = await api.post('/api/authoring/tags/import', { text: bulk.value, names: [], group: bulkGroup.value })
    bulk.value = ''
    await load()
    msg.value = `批量导入完成：新增 ${r.created ?? 0} 个，跳过 ${r.skipped ?? 0} 个`
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

onMounted(load)
</script>

<template>
  <div class="tags-page">
    <h2>标签管理</h2>
    <p class="muted">题库与出题共用同一套标签。默认按「专题」分组（仿 Chemy 九大专题），也可以自建分组。</p>

    <div v-if="err" class="errbox">{{ err }}</div>
    <div v-if="msg" class="okbox">{{ msg }}</div>

    <div class="card">
      <div class="row">
        <input v-model="form.name" placeholder="新标签名" style="max-width:220px" @keyup.enter="add" />
        <select v-model="form.group" style="max-width:160px">
          <option value="专题">专题</option>
          <option value="自定义">自定义</option>
        </select>
        <button class="primary" @click="add">添加</button>
        <span class="spacer"></span>
        <input v-model="q" placeholder="搜索标签…" style="max-width:200px" />
        <span class="muted">共 {{ tags.length }} 个</span>
      </div>
    </div>

    <div class="card" v-for="[g, list] in groups" :key="g">
      <h3>{{ g }} <small class="muted">{{ list.length }} 个</small></h3>
      <div class="list">
        <div v-for="t in shown(list)" :key="t.id" class="tagrow">
          <template v-if="editing.id === t.id">
            <input v-model="editing.name" style="max-width:180px" @keyup.enter="doRename(t)" />
            <button class="ghost" @click="doRename(t)">保存</button>
            <button class="ghost" @click="editing.id = 0">取消</button>
          </template>
          <template v-else>
            <span class="chip">{{ t.name }}</span>
            <button class="ghost sm" @click="startRename(t)">改名</button>
            <button class="ghost sm danger" @click="remove(t)">删除</button>
          </template>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>批量导入</h3>
      <p class="muted">用空格、逗号、顿号或换行分隔，一次建多个。</p>
      <textarea v-model="bulk" rows="4" placeholder="有机化学 高分子化学 晶体化学"></textarea>
      <div class="row" style="margin-top:8px">
        <select v-model="bulkGroup" style="max-width:160px">
          <option value="专题">专题</option>
          <option value="自定义">自定义</option>
        </select>
        <button class="primary" @click="importBulk">导入</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tags-page { display: flex; flex-direction: column; gap: 12px; }
h2 { margin: 0; }
.card h3 { margin: 0 0 10px; font-size: 14px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.spacer { flex: 1; }
.list { display: flex; flex-wrap: wrap; gap: 8px; }
.tagrow { display: flex; align-items: center; gap: 6px; }
.chip { background: #eef4ff; color: var(--brand, #2f6fed); border-radius: 999px; padding: 3px 12px; font-size: 12.5px; }
.sm { padding: 2px 8px; font-size: 12px; }
.danger { color: #d92d20; }
</style>
