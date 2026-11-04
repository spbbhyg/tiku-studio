<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../../api.js'
import RichField from '../../components/v2/RichField.vue'
import { emptyDoc, isDocEmpty, renderDoc, docText } from '../../components/v2/rich.js'

const STATUS = [
  ['draft', '草稿'],
  ['review', '待审'],
  ['approved', '已审'],
  ['published', '已入库'],
]
const statusLabel = (s) => (STATUS.find((x) => x[0] === s) || ['draft', s])[1]
const nextStatus = (s) => {
  const i = STATUS.findIndex((x) => x[0] === s)
  return i >= 0 && i < STATUS.length - 1 ? STATUS[i + 1][0] : ''
}

const groups = ref([])
const q = ref('')
const loading = ref(false)
const err = ref('')
const msg = ref('')
const g = ref(null)                 // 当前题组（含 subquestions）
const saving = ref(false)
const versions = ref([])
const showVersions = ref(false)
const openAns = reactive({})        // subId -> bool
const openRub = reactive({})

async function loadGroups() {
  loading.value = true
  try {
    const d = await api.get(`/api/v2/groups?q=${encodeURIComponent(q.value)}`)
    groups.value = d.items || []
  } catch (e) { err.value = e.message } finally { loading.value = false }
}

async function openGroup(id) {
  err.value = ''
  msg.value = ''
  try {
    g.value = await api.get(`/api/v2/groups/${id}`)
    versions.value = []
    showVersions.value = false
    for (const s of g.value.subquestions || []) {
      openAns[s.id] = false
      openRub[s.id] = false
    }
  } catch (e) { err.value = e.message }
}

async function createGroup() {
  err.value = ''
  try {
    const d = await api.post('/api/v2/groups', { title: '未命名题组', tags: [] })
    await loadGroups()
    await openGroup(d.id)
    // ⑧「新建即写正文」：直接把光标放到题干
    await nextTick()
    if (stemField.value && stemField.value.focus) stemField.value.focus()
  } catch (e) { err.value = e.message }
}

// ---------- 自动保存 ----------
let timer = null
let dirty = false
function scheduleSave() {
  dirty = true
  clearTimeout(timer)
  timer = setTimeout(flushSave, 1000)
}
async function flushSave() {
  if (!g.value || !dirty) return
  dirty = false
  saving.value = true
  try {
    const body = {
      title: g.value.title,
      stem: g.value.stem,
      source_title: g.value.source_title,
      source_citation: g.value.source_citation,
      locator: g.value.locator,
      tags: g.value.tags || [],
      material_ids: g.value.material_ids || [],
    }
    const d = await api.patch(`/api/v2/groups/${g.value.id}`, body)
    g.value.version = d.version
    msg.value = `已保存 · v${d.version}`
    const hit = groups.value.find((x) => x.id === g.value.id)
    if (hit) { hit.title = g.value.title; hit.sub_count = (g.value.subquestions || []).length }
    for (const s of g.value.subquestions || []) {
      await api.patch(`/api/v2/subquestions/${s.id}`, {
        stem: s.stem, answer: s.answer, rubric: s.rubric,
        score: s.score, q_type: s.q_type, tags: s.tags || [],
      })
    }
  } catch (e) {
    err.value = '保存失败：' + e.message
  } finally {
    saving.value = false
  }
}

function touch() { scheduleSave() }

watch(() => g.value && g.value.title, touch)

// ---------- 小问操作 ----------
const subFocus = ref(-1)
const metaOpen = ref(true)
const noInfer = ref(false)

async function suggestTags() {
  if (!g.value) return
  try {
    const text = [g.value.title, docText(g.value.stem)].filter(Boolean).join(' ')
    const r = await api.post('/api/ai/classify', { text, topics: [] })
    const tags = []
    if (r.q_type) tags.push(r.q_type)
    for (const t of r.topics || []) tags.push(t)
    if (!tags.length) { msg.value = 'AI 没给出可用标签'; return }
    const cur = new Set((g.value.tags || []).map((t) => String(t).trim()).filter(Boolean))
    for (const t of tags) cur.add(t)
    g.value.tags = Array.from(cur)
    touch()
    msg.value = `已推断标签：${tags.join('、')}`
  } catch (e) { msg.value = '标签推断失败：' + e.message }
}

const stemField = ref(null)
const lib = ref([])
const matBusy = ref(false)

function imgUrl(path) {
  if (!path) return ''
  if (path.startsWith('/') || path.startsWith('http') || path.startsWith('data:')) return path
  return '/api/images/' + path.split('/').map(encodeURIComponent).join('/')
}

const attached = computed(() => {
  const ids = new Set(g.value ? g.value.material_ids || [] : [])
  return lib.value.filter((m) => ids.has(m.id))
})

async function loadLib() {
  try {
    const r = await api.get('/api/v2/materials?limit=60')
    lib.value = r.items || []
  } catch (e) { /* 静默 */ }
}

async function uploadMat(ev) {
  const f = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!f || !g.value) return
  matBusy.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    const r = await fetch('/api/v2/materials/upload', { method: 'POST', body: fd }).then((x) => x.json())
    await loadLib()
    attach(r)
  } catch (e) { msg.value = '上传失败：' + e.message } finally { matBusy.value = false }
}

function insertMaterial(m) {
  if (!m) return
  const url = '/api/images/' + String(m.path || '').split('/').map(encodeURIComponent).join('/')
  if (stemField.value && stemField.value.insertImage) {
    stemField.value.insertImage(url, m.id)
    attach(m)
    msg.value = '已插入到题干'
  } else {
    msg.value = '题干编辑器还没就绪'
  }
}

function attach(m) {
  if (!g.value || !m) return
  const ids = new Set(g.value.material_ids || [])
  ids.add(m.id)
  g.value.material_ids = Array.from(ids)
  touch()
}

function detach(id) {
  if (!g.value) return
  g.value.material_ids = (g.value.material_ids || []).filter((x) => x !== id)
  touch()
}

function onTab(e) {
  // 只在普通输入框/编辑器之间跳，不劫持浏览器默认之外的场景
  const focusables = Array.from(document.querySelectorAll('.v2main input, .v2main .ProseMirror'))
  if (!focusables.length) return
  const i = focusables.indexOf(document.activeElement)
  if (i < 0) return
  e.preventDefault()
  const next = focusables[(i + (e.shiftKey ? -1 : 1) + focusables.length) % focusables.length]
  if (next) next.focus()
}

function onKey(e) {
  if (!g.value) return
  if (e.key === 'Tab' && !e.ctrlKey && !e.altKey && !e.metaKey) { onTab(e); return }
  const tag = (e.target && e.target.tagName) || ''
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    if (tag === 'INPUT' || tag === 'TEXTAREA') return
    e.preventDefault()
    addSub()
    return
  }
  if ((e.altKey || e.ctrlKey) && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
    if (tag === 'INPUT' || tag === 'TEXTAREA') return
    e.preventDefault()
    const list = g.value.subquestions || []
    const idx = subFocus.value
    if (idx == null || idx < 0) return
    move(list[idx], e.key === 'ArrowUp' ? -1 : 1)
  }
}

async function addSub() {
  if (!g.value) return
  try {
    const s = await api.post(`/api/v2/groups/${g.value.id}/subquestions`, { stem: emptyDoc(), score: 2 })
    g.value.subquestions.push(s)
    openAns[s.id] = false
    openRub[s.id] = false
    msg.value = `已加小问 ${s.seq}`
  } catch (e) { err.value = e.message }
}
async function delSub(s) {
  if (!confirm(`删除小问 ${s.seq}？`)) return
  try {
    await api.del(`/api/v2/subquestions/${s.id}`)
    g.value = await api.get(`/api/v2/groups/${g.value.id}`)
    msg.value = '已删除并重排'
  } catch (e) { err.value = e.message }
}
async function move(s, dir) {
  const arr = [...g.value.subquestions].sort((a, b) => a.seq - b.seq)
  const i = arr.findIndex((x) => x.id === s.id)
  const j = i + dir
  if (j < 0 || j >= arr.length) return
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
  try {
    await api.post(`/api/v2/groups/${g.value.id}/subquestions/reorder`, { ids: arr.map((x) => x.id) })
    g.value = await api.get(`/api/v2/groups/${g.value.id}`)
  } catch (e) { err.value = e.message }
}
async function advance() {
  const nx = nextStatus(g.value.status)
  if (!nx) return
  try {
    await api.post(`/api/v2/subquestions/${g.value.subquestions[0].id}/status`, { status: nx })
    g.value.status = nx
    g.value.version += 1
    msg.value = `状态 → ${statusLabel(nx)}`
  } catch (e) { err.value = e.message }
}
async function loadVersions() {
  if (!g.value) return
  try {
    const sid = g.value.subquestions[0].id
    versions.value = (await api.get(`/api/v2/subquestions/${sid}/versions`)).items || []
    showVersions.value = true
  } catch (e) { err.value = e.message }
}

const previewHtml = computed(() => (g.value ? renderDoc(g.value.stem) : ''))
const subPreview = (s) => renderDoc(s.stem)
const plainTitle = (gr) => docText(gr.stem) || gr.title

onMounted(async () => {
  loadLib()
  await loadGroups()
  if (groups.value.length) await openGroup(groups.value[0].id)
})
</script>

<template>
  <div class="v2wrap">
    <aside class="v2side">
      <div class="row">
        <input v-model="q" class="search" placeholder="搜题组…" @keyup.enter="loadGroups" />
        <button class="ghost sm" @click="loadGroups">查询</button>
      </div>
      <button class="primary wide" @click="createGroup">＋ 新建题组</button>
      <div class="glist">
        <button v-for="gr in groups" :key="gr.id" class="gitem" :class="{ on: g && g.id === gr.id }"
                @click="openGroup(gr.id)">
          <span class="gtitle">{{ gr.title || '未命名' }}</span>
          <span class="gmeta">{{ gr.sub_count }} 小问 · {{ statusLabel(gr.status) }}</span>
        </button>
        <div v-if="!groups.length && !loading" class="muted small">还没有题组，点上面新建。</div>
      </div>
    </aside>

    <main v-if="g" class="v2main" tabindex="-1" @keydown="onKey">
      <div class="head">
        <input v-model="g.title" class="title" placeholder="题组标题" @input="touch" />
        <span class="chip" :class="{ ok: g.status === 'approved' || g.status === 'published' }">
          {{ statusLabel(g.status) }} · v{{ g.version }}
        </span>
        <button class="ghost sm" :disabled="!nextStatus(g.status)" @click="advance">
          {{ nextStatus(g.status) ? '→ ' + statusLabel(nextStatus(g.status)) : '已到末端' }}
        </button>
        <button class="ghost sm" @click="loadVersions">版本</button>
        <span class="spacer"></span>
        <span class="muted small">{{ saving ? '保存中…' : (msg || '已同步') }}</span>
      </div>

      <div v-if="err" class="errbox">{{ err }}</div>

      <section class="card">
        <div class="sechead"><b>题干</b><span class="muted small">支持公式（∑ 公式）与列表</span></div>
        <RichField ref="stemField" v-model="g.stem" placeholder="在这里写题组引导语 / 材料…" :min-height="110"
                   @update:model-value="touch" />
      </section>

      <section class="card">
        <div class="sechead">
          <b>小问（{{ (g.subquestions || []).length }}）</b>
          <button class="ghost sm" @click="addSub">＋ 加小问</button>
        </div>

        <div v-for="(s, i) in g.subquestions" :key="s.id" class="sub" @focusin="subFocus = i" @mouseenter="subFocus = i">
          <div class="subhead">
            <span class="seq">{{ s.seq }}</span>
            <input v-model.number="s.score" type="number" step="0.5" min="0" class="score" @input="touch" />
            <span class="muted small">分</span>
            <input v-model="s.q_type" class="qtype" placeholder="题型" @input="touch" />
            <span class="spacer"></span>
            <button class="ghost sm" @click="move(s, -1)">↑</button>
            <button class="ghost sm" @click="move(s, 1)">↓</button>
            <button class="ghost sm danger" @click="delSub(s)">删除</button>
          </div>

          <RichField v-model="s.stem" placeholder="小问题干…" :min-height="70" @update:model-value="touch" />

          <div class="foldbar">
            <button class="ghost sm" @click="openAns[s.id] = !openAns[s.id]">
              {{ openAns[s.id] ? '▾' : '▸' }} 答案{{ isDocEmpty(s.answer) ? '（空）' : '' }}
            </button>
            <button class="ghost sm" @click="openRub[s.id] = !openRub[s.id]">
              {{ openRub[s.id] ? '▾' : '▸' }} 评分细则{{ isDocEmpty(s.rubric) ? '（空）' : '' }}
            </button>
          </div>
          <RichField v-if="openAns[s.id]" v-model="s.answer" placeholder="参考答案…" :min-height="70"
                     @update:model-value="touch" />
          <RichField v-if="openRub[s.id]" v-model="s.rubric" placeholder="评分细则 / 步骤分…" :min-height="70"
                     @update:model-value="touch" />
        </div>
      </section>

      <section class="card">
        <div class="sechead">
          <b>出处</b>
          <button class="ghost sm" @click="metaOpen = !metaOpen">{{ metaOpen ? '收起' : '展开' }}</button>
          <button class="ghost sm" @click="suggestTags">自动标签</button>
        </div>
        <div v-show="metaOpen" class="grid3">
          <label class="f"><span>文献 / 书名</span><input v-model="g.source_title" @input="touch" /></label>
          <label class="f"><span>作者·期刊·年份</span><input v-model="g.source_citation" @input="touch" /></label>
          <label class="f"><span>页码 / 题号</span><input v-model="g.locator" @input="touch" /></label>
        </div>
      </section>
    </main>



    <aside v-if="g" class="v2preview">
      <section class="card matcard">
        <div class="sechead">
          <b>素材</b>
          <label class="ghost sm filebtn">
            {{ matBusy ? '上传中…' : '上传图片' }}
            <input type="file" accept="image/*" style="display:none" @change="uploadMat" />
          </label>
        </div>
        <div v-if="attached.length" class="matlist">
          <div v-for="m in attached" :key="m.id" class="matitem">
            <img :src="imgUrl(m.path)" :alt="m.caption || ''" />
            <span class="mtitle">{{ m.caption || ('#' + m.id) }}</span>
            <button class="ghost sm" @click="insertMaterial(m)">插入正文</button>
            <button class="ghost sm" @click="detach(m.id)">移除</button>
          </div>
        </div>
        <p v-else class="muted small">还没有素材。上传图片后会挂到这道题上（按 ID 引用）。</p>

        <details v-if="lib.length">
          <summary class="muted small">从素材库选择（{{ lib.length }}）</summary>
          <div class="matpick">
            <button v-for="m in lib.slice(0, 24)" :key="m.id" class="matthumb"
                    :class="{ on: (g.material_ids || []).includes(m.id) }" @click="attach(m)">
              <img :src="imgUrl(m.path)" :alt="m.caption || ''" />
            </button>
          </div>
        </details>
      </section>

      <div class="phead"><b>预览</b><span class="muted small">（导出同款排版）</span></div>
      <div class="paper">
        <h3 class="ptitle">{{ g.title }}</h3>
        <div class="pstem" v-html="previewHtml"></div>
        <div v-for="s in g.subquestions" :key="s.id" class="psub">
          <div class="pseq">{{ s.seq }}. <span class="pts">{{ s.score }} 分</span></div>
          <div v-html="subPreview(s)"></div>
        </div>
      </div>

      <div v-if="showVersions" class="versions">
        <div class="phead"><b>版本（{{ versions.length }}）</b>
          <button class="ghost sm" @click="showVersions = false">收起</button>
        </div>
        <div v-if="!versions.length" class="muted small">还没有版本快照。</div>
        <div v-for="v in versions" :key="v.id" class="vrow">
          <span>v{{ v.version_no }}</span>
          <span class="muted small">{{ v.note || '—' }}</span>
          <span class="muted small">{{ (v.created_at || '').slice(0, 16).replace('T', ' ') }}</span>
        </div>
      </div>
    </aside>

    <div v-else class="v2empty">
      <p class="muted">左侧选一个题组，或新建一个。</p>
    </div>
  </div>
</template>

<style scoped>
.matcard { margin-bottom: 12px; }
.matlist { display: flex; flex-direction: column; gap: 8px; }
.matitem { display: flex; align-items: center; gap: 8px; }
.matitem img { width: 56px; height: 42px; object-fit: cover; border-radius: 6px; border: 1px solid var(--line); }
.matitem .mtitle { flex: 1; font-size: 12.5px; color: #475467; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.matpick { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.matthumb { padding: 0; border: 2px solid transparent; border-radius: 8px; background: none; cursor: pointer; }
.matthumb.on { border-color: var(--brand); }
.matthumb img { width: 62px; height: 46px; object-fit: cover; border-radius: 6px; display: block; }
.filebtn { display: inline-block; cursor: pointer; }
.v2wrap { display: grid; grid-template-columns: 240px minmax(0, 1fr) 420px; gap: 14px; align-items: start; }
@media (max-width: 1280px) { .v2wrap { grid-template-columns: 1fr; } .v2preview { order: 3; } }

.v2side { display: flex; flex-direction: column; gap: 8px; position: sticky; top: 12px; }
.row { display: flex; gap: 6px; }
.search { flex: 1; min-width: 0; padding: 6px 8px; border: 1px solid var(--line); border-radius: 8px; }
.wide { width: 100%; }
.glist { display: flex; flex-direction: column; gap: 4px; }
.gitem { text-align: left; padding: 8px 10px; border: 1px solid var(--line); background: #fff; border-radius: 8px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.gitem.on { border-color: var(--brand); background: var(--brand-soft); }
.gtitle { font-size: 13.5px; color: #101828; }
.gmeta { font-size: 12px; color: #667085; }

.v2main { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.head { display: flex; align-items: center; gap: 8px; }
.title { flex: 1; min-width: 260px; font-size: 17px; font-weight: 600; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; }
.spacer { flex: 1; }
.card { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
.sechead { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.sub { border: 1px solid var(--line); border-radius: 10px; padding: 10px; margin-bottom: 10px; background: #fcfdff; }
.subhead { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.seq { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 6px; background: var(--brand-soft); color: var(--brand); font-weight: 600; }
.score { width: 64px; padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px; }
.qtype { width: 92px; padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px; }
.foldbar { display: flex; gap: 8px; margin: 8px 0; }
.grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.f { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #475467; }
.f input { padding: 6px 8px; border: 1px solid var(--line); border-radius: 8px; }

.v2preview { position: sticky; top: 12px; display: flex; flex-direction: column; gap: 10px; }
.phead { display: flex; align-items: center; justify-content: space-between; }
.paper { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; max-height: 640px; overflow: auto; }
.ptitle { margin: 0 0 10px; font-size: 16px; }
.pstem :deep(p), .psub :deep(p) { margin: 0 0 6px; line-height: 1.75; }
.psub { border-top: 1px dashed var(--line); padding-top: 8px; margin-top: 8px; }
.pseq { font-weight: 600; margin-bottom: 4px; }
.pts { font-weight: 400; font-size: 12px; color: #667085; }
.versions { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }
.vrow { display: grid; grid-template-columns: 48px 1fr auto; gap: 8px; padding: 4px 0; border-bottom: 1px dashed var(--line); font-size: 13px; }
.v2empty { grid-column: 2 / 4; display: grid; place-items: center; padding: 80px 0; }
.small { font-size: 12px; }
.danger { color: #b42318; }
</style>
