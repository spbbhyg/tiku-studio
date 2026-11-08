<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const bid = computed(() => Number(route.params.id))

const books = ref([])          // 所有错题集
const items = ref([])          // 当前错题集的题
const active = ref(null)       // 当前阅读的题
const picked = ref([])         // 勾选的 question_id
const loading = ref(false)
const err = ref('')
const busy = ref(false)
const noteDraft = ref({})
const zoom = ref('')
const showAns = ref(false)
const renameId = ref(0)
const renameText = ref('')
const newName = ref('')

const sideOpen = ref(localStorage.getItem('tiku-wrong-side') !== '0')
const dirOpen = ref(localStorage.getItem('tiku-wrong-dir') !== '0')
const resetScroll = () => requestAnimationFrame(() => { const el = document.querySelector('.bread'); if (el) el.scrollTop = 0 })
const toggleSide = () => { sideOpen.value = !sideOpen.value; localStorage.setItem('tiku-wrong-side', sideOpen.value ? '1' : '0'); resetScroll() }
const toggleDir = () => { dirOpen.value = !dirOpen.value; localStorage.setItem('tiku-wrong-dir', dirOpen.value ? '1' : '0'); resetScroll() }

const vw = ref(typeof window !== 'undefined' ? window.innerWidth : 1400)
const mode = computed(() => (vw.value >= 1240 ? 'three' : vw.value >= 900 ? 'two' : 'one'))
const cols = computed(() => {
  if (mode.value === 'one') return 'minmax(0, 1fr)'
  if (mode.value === 'two') return `${dirOpen.value ? '320px' : '34px'} minmax(0, 1fr)`
  return `${sideOpen.value ? '200px' : '34px'} ${dirOpen.value ? '300px' : '34px'} minmax(0, 1fr)`
})
const onResize = () => { vw.value = window.innerWidth }

const book = computed(() => books.value.find((b) => b.id === bid.value) || null)

async function loadBooks() {
  const r = await api.get('/api/wb/wrongbooks')
  books.value = r.books || r || []
}
async function loadItems() {
  if (!bid.value) return
  loading.value = true
  err.value = ''
  try {
    const d = await api.get(`/api/wb/wrongbooks/${bid.value}/items`)
    items.value = d.items || []
    picked.value = []
    noteDraft.value = Object.fromEntries(items.value.map((i) => [i.question_id, i.note || '']))
    active.value = items.value.length && mode.value !== 'one' ? items.value[0] : null
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    loading.value = false
  }
}
onMounted(async () => {
  window.addEventListener('resize', onResize)
  try { await loadBooks() } catch (e) { err.value = String(e.message || e) }
  // 侧边栏「错题集」进来不带 id：自动落到第一本，没有就停在列表
  if (!bid.value && books.value.length) {
    router.replace('/wrong/' + books.value[0].id)
    return
  }
  await loadItems()
})
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
watch(bid, loadItems)

function openBook(b) {
  if (b.id === bid.value) return
  router.push('/wrong/' + b.id)
}
function pick(it) {
  active.value = it
  showAns.value = false
  requestAnimationFrame(() => {
    const el = document.querySelector('.bread')
    if (el) el.scrollTop = 0
  })
}
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
  return '/api/images/' + v.replace(/^\/+/, '')
}
const figs = (it) => {
  const out = []
  if (!it) return out
  if (typeof it.image === 'string') out.push(it.image)
  if (Array.isArray(it.images)) out.push(...it.images)
  return out.filter(Boolean).map((f) => (typeof f === 'string' ? f : (f.path || f.url || '')))
}
const opts = (it) => {
  const o = it && it.options
  if (!o) return []
  if (Array.isArray(o)) return o.map((x) => (typeof x === 'string' ? x : (x.text || ''))).filter(Boolean)
  if (typeof o === 'string') {
    try { const a = JSON.parse(o); return Array.isArray(a) ? a.map(String) : [] } catch { return [] }
  }
  return []
}
const titleOf = (it) => String(it.stem || '').replace(/^\s*\d+\s*[.、．)]\s*/, '').replace(/\s+/g, ' ').slice(0, 40)

async function saveNote(it) {
  try {
    await api.patch(`/api/wb/wrongbooks/${bid.value}/items/${it.question_id}`, { note: noteDraft.value[it.question_id] || '' })
    it.note = noteDraft.value[it.question_id] || ''
    err.value = ''
  } catch (e) { err.value = String(e.message || e) }
}

async function removeOne(it) {
  if (!confirm('把这道题移出错题集？（题目本身不会被删）')) return
  busy.value = true
  try {
    await api.post(`/api/wb/wrongbooks/${bid.value}/items/delete`, { question_ids: [it.question_id] })
    active.value = null
    await loadItems()
    await loadBooks()
  } catch (e) { err.value = String(e.message || e) } finally { busy.value = false }
}
async function removePicked() {
  if (!picked.value.length) return
  if (!confirm(`把选中的 ${picked.value.length} 道题移出错题集？（题目本身不会被删）`)) return
  busy.value = true
  try {
    await api.post(`/api/wb/wrongbooks/${bid.value}/items/delete`, { question_ids: picked.value })
    await loadItems()
    await loadBooks()
  } catch (e) { err.value = String(e.message || e) } finally { busy.value = false }
}
async function exportAs(fmt) {
  const url = `/api/wb/wrongbooks/${bid.value}/export?fmt=${fmt}`
  try {
    const r = await fetch(url)
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `错题集-${book.value?.name || bid.value}.${fmt === 'csv' ? 'csv' : 'html'}`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) { err.value = String(e.message || e) }
}

async function createBook() {
  const name = newName.value.trim()
  if (!name) return
  busy.value = true
  try {
    const r = await api.post('/api/wb/wrongbooks', { name, note: '' })
    newName.value = ''
    await loadBooks()
    router.push('/wrong/' + (r.id || r.book?.id))
  } catch (e) { err.value = String(e.message || e) } finally { busy.value = false }
}
function startRename(b) { renameId.value = b.id; renameText.value = b.name }
async function doRename(b) {
  const name = renameText.value.trim()
  if (!name) return
  try {
    await api.patch(`/api/wb/wrongbooks/${b.id}`, { name })
    renameId.value = 0
    await loadBooks()
  } catch (e) { err.value = String(e.message || e) }
}
async function delBook(b) {
  if (!confirm(`删除错题集「${b.name}」？题目本身不会被删。`)) return
  try {
    await api.delete(`/api/wb/wrongbooks/${b.id}`)
    await loadBooks()
    if (b.id === bid.value) {
      const first = books.value[0]
      router.push(first ? '/wrong/' + first.id : '/wrong/0')
    }
  } catch (e) { err.value = String(e.message || e) }
}
</script>

<template>
  <div class="wbpage" :class="'m-' + mode" :style="{ gridTemplateColumns: cols }">
    <!-- 左：错题集列表 -->
    <aside v-if="mode === 'three' && sideOpen" class="bside">
      <div class="bs-h">错题集</div>
      <button class="bs-sec">我的错题</button>
      <button v-for="b in books" :key="b.id" class="bs-item" :class="{ on: b.id === bid }" @click="openBook(b)">
        <span>{{ b.name }}</span><em>{{ b.count ?? 0 }}</em>
      </button>
      <p v-if="!books.length" class="bs-empty">还没有错题集</p>
      <div class="bs-new">
        <input v-model="newName" placeholder="新建，如：电化学易错" @keyup.enter="createBook" />
        <button class="primary sm" :disabled="busy" @click="createBook">建</button>
      </div>
      <div class="bs-foot">共 {{ books.length }} 本 · 移出题目不会删题</div>
      <button class="rail-btn left" title="收起错题集栏" @click="toggleSide">‹</button>
    </aside>
    <button v-if="mode === 'three' && !sideOpen" class="railbtn" title="展开错题集栏" @click="toggleSide"><span>›</span><i>错题集</i></button>

    <!-- 中：题目目录 -->
    <section v-if="dirOpen" class="bdir" :class="{ 'hide-one': mode === 'one' && active }">
      <div class="bd-h">
        {{ book?.name || '错题集' }}
        <span class="bd-cnt">{{ items.length }} 题</span>
      </div>

      <div v-if="mode !== 'three'" class="bd-row">
        <select class="bd-sel" style="margin:0;flex:1" :value="bid" @change="openBook(books.find(b => b.id === Number($event.target.value)))">
          <option v-for="b in books" :key="b.id" :value="b.id">{{ b.name }}（{{ b.count ?? 0 }}）</option>
        </select>
      </div>

      <div class="bd-tools">
        <label class="mini"><input type="checkbox" :checked="allPicked" @change="toggleAll" /> 全选</label>
        <button class="ghost sm" :disabled="!picked.length" @click="removePicked">移出所选（{{ picked.length }}）</button>
        <button class="ghost sm" @click="exportAs('html')">导出 HTML</button>
        <button class="ghost sm" @click="exportAs('csv')">导出 CSV</button>
      </div>

      <button class="rail-btn left" title="收起目录" @click="toggleDir">‹</button>
      <div class="bd-tree">
        <p v-if="loading" class="bd-empty">正在读取…</p>
        <p v-else-if="!items.length" class="bd-empty">这本还是空的：到题库卡片上点「+ 错题集」把题加进来。</p>
        <div v-for="(it, idx) in items" :key="it.question_id" class="bqrow" :class="{ on: active && active.question_id === it.question_id }">
          <input type="checkbox" :checked="picked.includes(it.question_id)" @change="toggle(it.question_id)" />
          <button class="bq" @click="pick(it)">
            <b>第{{ idx + 1 }}题</b> {{ titleOf(it) }}
            <span class="bq-meta">错 {{ it.wrong_count }} 次 · {{ it.q_type || '题目' }}</span>
          </button>
        </div>
      </div>
    </section>

    <button v-if="mode !== 'one' && !dirOpen" class="railbtn" title="展开目录" @click="toggleDir"><span>›</span><i>目录</i></button>

    <!-- 右：阅读 -->
    <section v-show="mode !== 'one' || active" class="bread" :class="{ 'hide-one': mode === 'one' && !active }">
      <article v-if="active" class="rd">
        <button v-if="mode === 'one'" class="ghost sm bd-back" @click="active = null">← 返回目录</button>

        <div class="rd-body">
          <p v-for="(line, i) in String(active.stem || '').split('\n')" :key="i" class="rd-line">{{ line }}</p>
          <img v-for="(f, i) in figs(active)" :key="'f' + i" :src="imgUrl(f)" class="rd-fig" alt="题图" @error="(e) => e.target.remove()" @click="zoom = imgUrl(f)" />
          <ol v-if="opts(active).length" class="rd-opts">
            <li v-for="(o, i) in opts(active)" :key="i">{{ o }}</li>
          </ol>
        </div>

        <button class="acc" @click="showAns = !showAns">
          <span>答案与解析</span><i>{{ showAns ? '⌃' : '⌄' }}</i>
        </button>
        <div v-show="showAns" class="acc-body">
          <p><b>答案：</b>{{ active.answer || '—' }}</p>
          <p v-if="active.analysis"><b>解析：</b>{{ active.analysis }}</p>
        </div>

        <div class="rd-meta">
          <div class="rd-tags">
            <span class="chip gray">错 {{ active.wrong_count }} 次</span>
            <span v-if="active.q_type" class="chip">{{ active.q_type }}</span>
            <span v-if="active.source_title" class="chip gray">{{ active.source_title }}</span>
          </div>
          <div class="noterow">
            <input v-model="noteDraft[active.question_id]" placeholder="写一句错因备注，例如「盖斯定律用反了」" @keyup.enter="saveNote(active)" />
            <button class="ghost sm" @click="saveNote(active)">保存备注</button>
            <button class="ghost sm" :disabled="busy" @click="removeOne(active)">移出错题集</button>
          </div>
        </div>
      </article>
      <p v-else class="rd-empty">左边选一道题开始看</p>
    </section>

    <div v-if="picked.length" class="pickbar">
      <span>已选 {{ picked.length }} 题</span>
      <button class="ghost sm" @click="picked = []">清空</button>
      <button class="ghost sm" @click="exportAs('html')">导出 HTML</button>
      <button class="ghost sm" :disabled="busy" @click="removePicked">移出所选</button>
    </div>
    <p v-if="err" class="errbar">{{ err }}</p>
    <div v-if="zoom" class="zmask" @click.self="zoom = ''"><img :src="zoom" alt="题图" /></div>
  </div>
</template>

<style scoped>
.wbpage { display: grid; grid-template-columns: 200px 300px minmax(0, 1fr); height: calc(100vh - 74px); min-width: 0; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: #fff; }
.wbpage.m-one { }
.wbpage .hide-one { display: none; }
.railbtn { border: 0; border-right: 1px solid var(--line); background: #fafcff; color: #94a3b8; cursor: pointer; padding: 14px 0 0; height: 100%; width: 100%; display: flex; align-items: flex-start; justify-content: center; font-size: 16px; }
.railbtn:hover { background: #eef4ff; color: var(--brand); }
.railbtn i { font-style: normal; writing-mode: vertical-rl; letter-spacing: 2px; font-size: 11px; margin-top: 6px; color: #b6c2d4; }
.rail-btn { border: 0; background: none; color: #cbd5e1; cursor: pointer; font-size: 14px; line-height: 1; padding: 2px 4px; border-radius: 4px; }
.rail-btn:hover { background: #eef4ff; color: var(--brand); }
.rail-btn.left { position: absolute; right: 4px; top: 6px; }
.bside, .bdir { position: relative; }
.bside { border-right: 1px solid var(--line); padding: 10px 8px; overflow: auto; background: #fcfdff; min-width: 0; }
.bs-h { font-size: 13px; font-weight: 700; padding: 4px 8px 8px; }
.bs-sec { font-size: 10.5px; color: #94a3b8; letter-spacing: .1em; padding: 8px 8px 4px; background: none; border: 0; text-align: left; }
.bs-item { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 6px; padding: 6px 8px; border: 0; background: none; border-radius: 5px; font-size: 12.5px; color: #334155; cursor: pointer; text-align: left; }
.bs-item em { font-style: normal; color: #9aa6b8; font-size: 11px; }
.bs-item:hover { background: #f1f5fb; }
.bs-item.on { background: #e8f5ec; color: #166534; font-weight: 600; }
.bs-empty { font-size: 12px; color: #94a3b8; padding: 6px 8px; }
.bs-new { display: flex; gap: 6px; padding: 10px 8px 0; }
.bs-new input { flex: 1; min-width: 0; height: 28px; border: 1px solid var(--line); border-radius: 6px; padding: 0 8px; font-size: 12px; }
.bs-foot { margin-top: 12px; padding: 8px; font-size: 11px; color: #94a3b8; border-top: 1px dashed var(--line); }
.bdir { border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; min-width: 0; overflow-x: hidden; }
.bd-h { padding: 10px 12px; font-weight: 700; font-size: 13.5px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }
.bd-cnt { font-size: 11px; color: #94a3b8; font-weight: 400; }
.bd-row { display: flex; gap: 6px; padding: 10px 12px 0; }
.bd-sel { width: calc(100% - 24px); height: 30px; border: 1px solid var(--line); border-radius: 6px; font-size: 12px; background: #fff; box-sizing: border-box; }
.bd-tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 10px 12px; border-bottom: 1px dashed var(--line); }
.mini { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #334155; }
.bd-tree { flex: 1; overflow: auto; padding: 6px 6px 12px; }
.bd-empty { font-size: 12px; color: #94a3b8; padding: 12px; line-height: 1.7; }
.bqrow { display: flex; align-items: flex-start; gap: 6px; padding: 4px 4px; border-radius: 6px; }
.bqrow.on { background: #e8f5ec; }
.bqrow input { margin-top: 7px; }
.bq { flex: 1; text-align: left; border: 0; background: none; padding: 4px 4px; font-size: 12px; color: #334155; cursor: pointer; line-height: 1.55; }
.bq b { color: #1e3a8a; font-weight: 600; margin-right: 4px; }
.bq-meta { display: block; font-size: 11px; color: #94a3b8; margin-top: 2px; }
.bread { overflow: auto; min-width: 0; padding: 18px 22px 90px; }
.bd-back { margin-bottom: 10px; }
.rd-empty { color: #94a3b8; font-size: 13px; }
.rd-body { font-size: 13.5px; line-height: 1.9; color: #0f172a; }
.rd-line { margin: 0 0 6px; white-space: pre-wrap; }
.rd-fig { max-width: 100%; max-height: 46vh; object-fit: contain; display: block; margin: 8px 0; cursor: zoom-in; }
.rd-opts { margin: 8px 0 0 18px; padding: 0; }
.rd-opts li { margin: 2px 0; }
.acc { width: 100%; display: flex; justify-content: space-between; align-items: center; background: none; border: 0; border-top: 1px solid var(--line); padding: 12px 2px; font-size: 12.5px; color: #334155; cursor: pointer; }
.acc i { font-style: normal; color: #94a3b8; }
.acc-body { padding: 0 2px 12px; font-size: 12.5px; line-height: 1.8; }
.rd-meta { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin-top: 14px; background: #fcfdff; }
.rd-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #eef4ff; color: #2563eb; }
.chip.gray { background: #f1f5f9; color: #64748b; }
.noterow { display: flex; gap: 8px; flex-wrap: wrap; }
.noterow input { flex: 1; min-width: 180px; border: 1px solid #cbd5e1; border-radius: 8px; padding: 6px 10px; font-size: 13px; }
.pickbar { position: fixed; left: 50%; bottom: 18px; transform: translateX(-50%); display: flex; align-items: center; gap: 10px; max-width: 92vw; flex-wrap: wrap; background: #fff; border: 1px solid var(--line); box-shadow: 0 10px 30px rgba(15, 23, 42, .12); border-radius: 999px; padding: 8px 14px; font-size: 12.5px; z-index: 30; }
.errbar { position: fixed; left: 50%; bottom: 66px; transform: translateX(-50%); background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; padding: 6px 12px; border-radius: 8px; font-size: 12px; }
.zmask { position: fixed; inset: 0; background: rgba(15,23,42,.72); display: flex; align-items: center; justify-content: center; z-index: 90; }
.zmask img { max-width: 92vw; max-height: 92vh; background: #fff; }
</style>
