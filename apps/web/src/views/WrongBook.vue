<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const bid = computed(() => Number(route.params.id))
const book = ref(null)
const items = ref([])
const loading = ref(false)
const picked = ref([])
const err = ref('')
const busy = ref(false)
const noteDraft = ref({})

// 弹层：看大图
const zoom = ref('')

async function load() {
  loading.value = true
  err.value = ''
  try {
    const books = await api.get('/api/wb/wrongbooks')
    const list = books.books || books || []
    book.value = list.find((b) => b.id === bid.value) || null
    const d = await api.get(`/api/wb/wrongbooks/${bid.value}/items`)
    items.value = d.items || []
    picked.value = []
    noteDraft.value = Object.fromEntries(items.value.map((i) => [i.question_id, i.note || '']))
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(bid, load)

function toggle(id) {
  const i = picked.value.indexOf(id)
  if (i >= 0) picked.value.splice(i, 1)
  else picked.value.push(id)
}
const allPicked = computed(() => items.value.length > 0 && picked.value.length === items.value.length)
function toggleAll() {
  picked.value = allPicked.value ? [] : items.value.map((i) => i.question_id)
}

function imgUrl(ref0) {
  if (ref0 && typeof ref0 === 'object') {
    const p = String(ref0.path || ref0.name || ref0.url || ref0.file || '').trim()
    return p ? '/api/images/' + p.replace(/^\/+/, '') : ''
  }
  const v = String(ref0 || '').trim()
  if (!v) return ''
  if (v.startsWith('http') || v.startsWith('/api/')) return v
  return '/api/images/' + v.split(/[\\/]/).pop()
}

async function saveNote(it) {
  try {
    await api.patch(`/api/wb/wrongbooks/${bid.value}/items/${it.question_id}`, { note: noteDraft.value[it.question_id] || '' })
    it.note = noteDraft.value[it.question_id] || ''
    err.value = ''
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function removePicked() {
  if (!picked.value.length) return
  if (!confirm(`把选中的 ${picked.value.length} 道题移出错题集？（题目本身不会被删）`)) return
  busy.value = true
  try {
    await api.post(`/api/wb/wrongbooks/${bid.value}/items/delete`, { question_ids: picked.value })
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function exportAs(fmt) {
  const ids = picked.value.length ? picked.value : items.value.map((i) => i.question_id)
  try {
    const url = `/api/wb/wrongbooks/${bid.value}/export?fmt=${fmt}`
    const r = await fetch(url)
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `错题集-${book.value?.name || bid.value}.${fmt === 'csv' ? 'csv' : 'html'}`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) {
    err.value = String(e.message || e)
  }
}
</script>

<template>
  <div class="wbp">
    <header class="head">
      <button class="ghost sm" @click="router.back()">← 返回</button>
      <h2>错题集 · {{ book?.name || '…' }}</h2>
      <span class="cnt">{{ items.length }} 题</span>
      <span class="sp" />
      <button class="ghost sm" :disabled="!picked.length" @click="removePicked">
        移出所选（{{ picked.length }}）
      </button>
      <button class="ghost sm" @click="exportAs('html')">导出 HTML</button>
      <button class="ghost sm" @click="exportAs('csv')">导出 CSV</button>
    </header>

    <p v-if="err" class="err">{{ err }}</p>
    <p v-if="loading" class="hint">正在读取…</p>
    <p v-else-if="!items.length" class="hint">这本错题集还是空的：到题库卡片上点「+ 错题集」把题加进来。</p>

    <div v-else class="tools">
      <label class="pick"><input type="checkbox" :checked="allPicked" @change="toggleAll" /> 全选</label>
      <span class="hint">勾选题后可以只导出/只移出所选。</span>
    </div>

    <article v-for="(it, idx) in items" :key="it.question_id" class="wc" :class="{ on: picked.includes(it.question_id) }">
      <div class="wtop">
        <input type="checkbox" :checked="picked.includes(it.question_id)" @change="toggle(it.question_id)" />
        <span class="no">{{ idx + 1 }}</span>
        <span class="tag">{{ it.q_type || '题目' }}</span>
        <span class="tag lv">{{ it.difficulty }}</span>
        <span class="tag warn">错 {{ it.wrong_count }} 次</span>
        <span class="src">{{ it.source_title }}</span>
      </div>

      <div class="stem">{{ it.stem }}</div>

      <div v-if="(it.images || []).length" class="figs">
        <img v-for="im in it.images" :key="im" :src="imgUrl(im)" alt="题图" loading="lazy" @click="zoom = imgUrl(im)" />
      </div>

      <div v-if="(it.options || []).length" class="opts">
        <span v-for="o in it.options" :key="o">{{ o }}</span>
      </div>

      <details class="ans">
        <summary>答案与解析</summary>
        <p><b>答案：</b>{{ it.answer || '—' }}</p>
        <p v-if="it.analysis"><b>解析：</b>{{ it.analysis }}</p>
      </details>

      <div class="noterow">
        <input v-model="noteDraft[it.question_id]" placeholder="写一句错因备注，例如「盖斯定律用反了」" @keyup.enter="saveNote(it)" />
        <button class="ghost sm" @click="saveNote(it)">保存备注</button>
      </div>
    </article>

    <div v-if="zoom" class="zmask" @click.self="zoom = ''"><img :src="zoom" alt="题图" /></div>
  </div>
</template>

<style scoped>
.wbp { padding: 16px 20px 40px; max-width: 1080px; }
.head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.head h2 { margin: 0; font-size: 20px; }
.cnt { color: #64748b; font-size: 13px; }
.sp { flex: 1; }
.err { color: #b91c1c; font-size: 13px; }
.hint { color: #64748b; font-size: 13px; }
.tools { display: flex; align-items: center; gap: 12px; margin: 8px 0 12px; }
.pick { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #334155; }
.wc { border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; padding: 12px 14px; margin-bottom: 12px; }
.wc.on { border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59,130,246,.12); }
.wtop { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #64748b; flex-wrap: wrap; }
.no { font-weight: 700; color: #0f172a; }
.tag { background: #f1f5f9; border-radius: 999px; padding: 1px 8px; }
.tag.lv { background: #eef2ff; color: #4338ca; }
.tag.warn { background: #fef3c7; color: #92400e; }
.src { margin-left: auto; }
.stem { margin: 8px 0; font-size: 15px; line-height: 1.7; white-space: pre-wrap; }
.figs { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.figs img { max-height: 240px; max-width: 100%; border: 1px solid #e5e7eb; border-radius: 8px; cursor: zoom-in; }
.opts { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 14px; color: #334155; }
.ans { margin-top: 8px; font-size: 13.5px; color: #334155; }
.ans summary { cursor: pointer; color: #2563eb; }
.noterow { display: flex; gap: 8px; margin-top: 10px; }
.noterow input { flex: 1; border: 1px solid #cbd5e1; border-radius: 8px; padding: 6px 10px; font-size: 13px; }
.zmask { position: fixed; inset: 0; background: rgba(15,23,42,.72); display: flex; align-items: center; justify-content: center; z-index: 90; }
.zmask img { max-width: 92vw; max-height: 92vh; background: #fff; }
</style>
