<script setup>
/** 分类维度管理：维度（专题/章节/来源…）+ 取值，全部可自定义 */
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const dims = ref([])
const loading = ref(false)
const err = ref('')
const msg = ref('')
const newDim = ref('')
const editing = ref({ id: 0, value: '' })     // 正在改名的取值
const addTo = ref({ key: '', value: '' })     // 正在加取值的维度

async function load() {
  loading.value = true
  try {
    const d = await api.get('/api/wb/categories')
    dims.value = d.dims || []
    err.value = ''
  } catch (e) { err.value = e.message } finally { loading.value = false }
}
onMounted(load)

async function addDim() {
  const name = newDim.value.trim()
  if (!name) return
  try {
    await api.post('/api/wb/categories', { name })
    newDim.value = ''
    msg.value = '已添加维度'
    await load()
  } catch (e) { err.value = e.message }
}

async function renameDim(d, name) {
  const v = (name || '').trim()
  if (!v || v === d.name) return
  try { await api.patch(`/api/wb/categories/${d.key}`, { name: v }); await load() } catch (e) { err.value = e.message }
}

async function delDim(d) {
  if (!confirm(`删除维度「${d.name}」及其全部取值？题目本身的标签不受影响。`)) return
  try { await api.del(`/api/wb/categories/${d.key}`); await load() } catch (e) { err.value = e.message }
}

async function addValue(key) {
  const name = (addTo.value.value || '').trim()
  if (!name) return
  try {
    await api.post(`/api/wb/categories/${key}/values`, { name })
    addTo.value = { key: '', value: '' }
    await load()
  } catch (e) { err.value = e.message }
}

async function renameValue(v) {
  const name = (editing.value.value || '').trim()
  if (!name || editing.value.id !== v.id) { editing.value = { id: 0, value: '' }; return }
  try { await api.patch(`/api/wb/values/${v.id}`, { name }); editing.value = { id: 0, value: '' }; await load() }
  catch (e) { err.value = e.message }
}

async function delValue(v) {
  try { await api.del(`/api/wb/values/${v.id}`); await load() } catch (e) { err.value = e.message }
}
</script>

<template>
  <div class="catadmin">
    <p class="muted">
      这里定义题库左侧的「分类」。默认只有 Chemy 那套「专题」，你可以加「章节 / 来源 / 难度档 / 自己习惯的说法」，
      题目的标签串就是按这些取值来的。
    </p>
    <p v-if="err" class="errline">{{ err }}</p>
    <p v-if="msg" class="okline">{{ msg }}</p>

    <div class="addrow">
      <input v-model="newDim" placeholder="新维度名，如：章节 / 试卷来源" @keyup.enter="addDim" />
      <button class="primary sm" @click="addDim">添加维度</button>
      <span class="spacer"></span>
      <button class="ghost sm" @click="load">刷新</button>
    </div>

    <div v-if="loading" class="muted">加载中…</div>

    <div v-for="d in dims" :key="d.key" class="dimcard">
      <div class="dimhead">
        <input class="dimname" :value="d.name" @change="renameDim(d, $event.target.value)" />
        <span class="key muted">key: {{ d.key }}</span>
        <span class="chip sm2">{{ d.values.length }} 个取值</span>
        <span class="spacer"></span>
        <button class="ghost sm" @click="addTo = { key: d.key, value: '' }">+ 取值</button>
        <button class="ghost sm danger" :disabled="d.builtin && d.values.length" @click="delDim(d)">删除维度</button>
      </div>
      <div v-if="addTo.key === d.key" class="inrow">
        <input v-model="addTo.value" placeholder="新取值名" @keyup.enter="addValue(d.key)" />
        <button class="primary sm" @click="addValue(d.key)">加入</button>
        <button class="ghost sm" @click="addTo = { key: '', value: '' }">取消</button>
      </div>
      <div class="vals">
        <span v-for="v in d.values" :key="v.id" class="valchip">
          <template v-if="editing.id === v.id">
            <input v-model="editing.value" class="tinyinput" @keyup.enter="renameValue(v)" @blur="renameValue(v)" />
          </template>
          <template v-else>
            <span class="vname" @click="editing = { id: v.id, value: v.name }">{{ v.name }}</span>
            <button class="x" title="删除" @click="delValue(v)">×</button>
          </template>
        </span>
        <span v-if="!d.values.length" class="muted tiny">还没有取值</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.catadmin { display: flex; flex-direction: column; gap: 10px; }
.addrow, .inrow { display: flex; gap: 8px; align-items: center; }
.addrow input, .inrow input { flex: 1; max-width: 320px; }
.spacer { flex: 1; }
.dimcard { border: 1px solid var(--line, #e5e7eb); border-radius: 10px; padding: 10px 12px; background: #fff; }
.dimhead { display: flex; gap: 8px; align-items: center; }
.dimname { font-weight: 600; font-size: 14px; border: 1px solid transparent; background: transparent; padding: 2px 4px; border-radius: 6px; }
.dimname:hover, .dimname:focus { border-color: #d1d5db; background: #fff; }
.key { font-size: 11.5px; }
.vals { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.valchip { display: inline-flex; align-items: center; gap: 2px; background: #eef2ff; color: #3730a3;
  border-radius: 999px; padding: 2px 4px 2px 10px; font-size: 12.5px; }
.vname { cursor: text; }
.valchip .x { border: 0; background: transparent; color: #6366f1; cursor: pointer; font-size: 14px; line-height: 1; padding: 0 4px; }
.tinyinput { width: 90px; font-size: 12.5px; }
.errline { color: #b91c1c; font-size: 12.5px; }
.okline { color: #047857; font-size: 12.5px; }
.tiny { font-size: 11.5px; }
</style>
