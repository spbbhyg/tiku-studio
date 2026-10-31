<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { renderMath } from '../math.js'
import { loadShortcuts, matchCombo } from '../shortcuts.js'
import CanvasPane from '../components/CanvasPane.vue'

const route = useRoute()
const router = useRouter()

// ---------- 左栏：PDF 与裁剪 ----------
const docs = ref([])
const activeDoc = ref(null)
const pageNo = ref(1)
const zoom = ref(2)
const sel = ref(null)
const busy = ref('')
const msg = ref('')
const err = ref('')
const pageImg = ref(null)
const qPane = ref(null)
const aPane = ref(null)
const activePane = ref('q')
const qOpen = ref(true)

const pageUrl = computed(() => (activeDoc.value
  ? `/api/manual/doc/${activeDoc.value.doc_id}/page/${pageNo.value}.png?zoom=${zoom.value}`
  : ''))
function imgUrl(p) { return p ? `/api/images/${p}` : '' }
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

async function upload(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  if (!files.length) return
  busy.value = `上传 ${files.length} 个文件…`
  err.value = ''
  try {
    for (const f of files) {
      const fd = new FormData()
      fd.append('file', f)
      const d = await api.postForm('/api/manual/upload', fd)
      docs.value.push({ doc_id: d.doc_id, filename: d.filename, page_count: d.page_count })
      activeDoc.value = docs.value[docs.value.length - 1]
      pageNo.value = 1
      sel.value = null
    }
    msg.value = '已放入。在页面上拖框选一块，点「裁剪到题目」或「裁剪到答案」'
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}

function turn(d) {
  if (!activeDoc.value) return
  pageNo.value = clamp(pageNo.value + d, 1, activeDoc.value.page_count)
  sel.value = null
}

let boxing = null
function localPos(e) {
  const r = pageImg.value.getBoundingClientRect()
  return {
    rx: clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 1),
    ry: clamp((e.clientY - r.top) / Math.max(1, r.height), 0, 1),
  }
}
function boxDown(e) {
  if (!activeDoc.value) return
  const p = localPos(e)
  boxing = { x0: p.rx, y0: p.ry, x1: p.rx, y1: p.ry }
  sel.value = { ...boxing }
  window.addEventListener('pointermove', boxMove)
  window.addEventListener('pointerup', boxUp)
}
function boxMove(e) {
  if (!boxing) return
  const p = localPos(e)
  sel.value = {
    x0: Math.min(boxing.x0, p.rx), y0: Math.min(boxing.y0, p.ry),
    x1: Math.max(boxing.x0, p.rx), y1: Math.max(boxing.y0, p.ry),
  }
}
function boxUp() {
  window.removeEventListener('pointermove', boxMove)
  window.removeEventListener('pointerup', boxUp)
  boxing = null
  if (sel.value && (sel.value.x1 - sel.value.x0) * (sel.value.y1 - sel.value.y0) < 0.00006) sel.value = null
}
function selStyle() {
  const s = sel.value || { x0: 0, y0: 0, x1: 0, y1: 0 }
  return {
    left: s.x0 * 100 + '%', top: s.y0 * 100 + '%',
    width: (s.x1 - s.x0) * 100 + '%', height: (s.y1 - s.y0) * 100 + '%',
  }
}

async function doCrop(target) {
  if (!sel.value || !activeDoc.value) return
  busy.value = '裁剪中…'
  err.value = ''
  try {
    const r = await api.post('/api/manual/crop', {
      doc_id: activeDoc.value.doc_id, page_no: pageNo.value,
      rect: [sel.value.x0, sel.value.y0, sel.value.x1, sel.value.y1], zoom: 3,
    })
    const pane = target === 'a' ? aPane.value : qPane.value
    if (pane) await pane.addImage(r.path, null, null, target === 'a' ? false : false)
    sel.value = null
    msg.value = target === 'a' ? '已裁剪答案图，放到下方「答案画布」' : '已裁剪题目图，放到上方「题目画布」'
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}
const crop = () => doCrop('q')
const cropAnswer = () => doCrop('a')

// ---------- 「其它功能」浮动面板 ----------
const auxOpen = ref(false)
const floatEl = ref(null)
const fpos = reactive({ x: 24, y: 110 })
function clampF() {
  const el = floatEl.value
  if (!el) return
  const w = el.offsetWidth || 348
  const h = el.offsetHeight || 200
  fpos.x = Math.max(8, Math.min(fpos.x, window.innerWidth - w - 8))
  fpos.y = Math.max(8, Math.min(fpos.y, window.innerHeight - h - 8))
}
function floatDown(e) {
  if (e.target.closest('button')) return
  const sx = e.clientX - fpos.x
  const sy = e.clientY - fpos.y
  const move = (ev) => { fpos.x = ev.clientX - sx; fpos.y = ev.clientY - sy; clampF() }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    try { localStorage.setItem('tiku-aux-pos', JSON.stringify({ x: fpos.x, y: fpos.y })) } catch (e2) {}
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
function toggleAux() { auxOpen.value = !auxOpen.value; nextTick(clampF) }

// ---------- 入库信息 ----------
const meta = reactive({ books: [], tags: [], qtypes: [], default_book_id: null })
const form = reactive({
  book_id: null, unit_id: null, q_type: '简答', difficulty: 2, tags: [],
  source_title: '', source_citation: '', locator: '', status: 'confirmed', answer: '',
})
const units = computed(() => {
  const b = meta.books.find((x) => x.id === form.book_id)
  return b ? b.units || [] : []
})
function pickTag(name) {
  const i = form.tags.indexOf(name)
  if (i >= 0) form.tags.splice(i, 1)
  else form.tags.push(name)
}
async function loadMeta() {
  try {
    const m = await api.get('/api/authoring/meta')
    meta.books = m.books || []
    meta.qtypes = m.qtypes || []
    meta.default_book_id = m.default_book_id || null
    let tags = []
    try {
      const t = await api.get('/api/authoring/tags')
      tags = Array.isArray(t) ? t : (t.tags || t.items || [])
    } catch (e2) { tags = [] }
    meta.tags = tags
    if (form.book_id == null) form.book_id = meta.default_book_id
  } catch (e) { err.value = e?.message || String(e) }
  if (!meta.qtypes.length) meta.qtypes = ['简答']
}

// ---------- 保存 / 载入 ----------
const saving = ref(false)
const editingQuestionId = ref(null)
const savedId = ref(null)

async function save() {
  const ql = qPane.value ? qPane.value.getLayout() : null
  if (!ql || !ql.blocks.length) { err.value = '题目画布是空的：先裁剪或写点文字'; return }
  saving.value = true
  err.value = ''; msg.value = ''
  try {
    const r = await api.post('/api/authoring/compose', {
      layout: ql,
      answer_layout: aPane.value ? aPane.value.getLayout() : null,
      question_id: editingQuestionId.value || null,
      book_id: form.book_id, unit_id: form.unit_id,
      q_type: form.q_type, difficulty: Number(form.difficulty),
      tags: form.tags, source_title: form.source_title,
      source_citation: form.source_citation, locator: form.locator,
      status: form.status, answer: form.answer,
    })
    editingQuestionId.value = r.id
    savedId.value = r.id
    msg.value = r.created ? `已入库 #${r.id}` : `已更新 #${r.id}`
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { saving.value = false }
}

function resetAll() {
  if (qPane.value) qPane.value.clear()
  if (aPane.value) aPane.value.clear()
  editingQuestionId.value = null
  savedId.value = null
  err.value = ''
  msg.value = '画布已清空，可以出下一题'
}

async function loadExisting(id) {
  try {
    const d = await api.get(`/api/authoring/compose/${id}`)
    if (qPane.value) qPane.value.setLayout(d.layout)
    if (aPane.value) aPane.value.setLayout(d.answer_layout || { blocks: [] })
    editingQuestionId.value = d.id
    savedId.value = d.id
    form.book_id = d.book_id || form.book_id
    form.unit_id = d.unit_id || null
    form.q_type = d.q_type || form.q_type
    form.difficulty = d.difficulty || 2
    form.tags = [...(d.tags || [])]
    form.source_title = d.source_title || ''
    form.source_citation = d.source_citation || ''
    form.locator = d.locator || ''
    form.status = d.status || 'confirmed'
    form.answer = d.answer || ''
    msg.value = `已载入 #${d.id}，可继续编辑`
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

function goBank() { router.push('/questions') }

// ---------- 快捷键：作用在「当前」画布 ----------
function paneOf(key) { return key === 'a' ? aPane.value : qPane.value }
function onKeys(e) {
  const t = e.target || {}
  const tag = (t.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || t.isContentEditable) return
  const p = paneOf(activePane.value)
  if (!p) return
  const m = loadShortcuts()
  const k = (e.key || '').toLowerCase()
  if (matchCombo(e, 'copy', m)) { p.copySel(); e.preventDefault(); return }
  if (matchCombo(e, 'paste', m)) { p.pasteClip(); e.preventDefault(); return }
  if (matchCombo(e, 'group', m)) { p.groupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'ungroup', m)) { p.ungroupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'rotate', m)) { p.rotateSel(); e.preventDefault(); return }
  if (matchCombo(e, 'del', m) || k === 'backspace') { p.removeSelected(); e.preventDefault(); return }
  if (matchCombo(e, 'esc', m)) { p.clearSel(); return }
  const step = e.shiftKey ? 10 : 1
  const delta = { arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step] }[k]
  if (delta) { p.nudge(delta[0], delta[1]); e.preventDefault() }
}

onMounted(async () => {
  await loadMeta()
  try {
    const saved = JSON.parse(localStorage.getItem('tiku-aux-pos') || 'null')
    if (saved && Number.isFinite(saved.x)) { fpos.x = saved.x; fpos.y = saved.y }
    else { fpos.x = Math.max(16, window.innerWidth - 72); fpos.y = Math.round(window.innerHeight / 2 - 20) }
  } catch (e2) {}
  setTimeout(clampF, 0)
  window.addEventListener('keydown', onKeys)
  const qid = Number(route.query.id || 0)
  if (qid) await nextTick().then(() => loadExisting(qid))
})
onBeforeUnmount(() => { window.removeEventListener('keydown', onKeys) })
</script>

<template>
  <div class="writer" :class="{ 'left-off': !qOpen }">
    <aside class="left" v-show="qOpen">
      <section class="pane">
        <h3>PDF 放入</h3>
        <label class="drop">
          <input type="file" accept="application/pdf,image/*" multiple @change="upload" />
          <span>＋ 选择 PDF / 图片（可多份）</span>
        </label>
        <div class="docs" v-if="docs.length">
          <button v-for="d in docs" :key="d.doc_id" class="doc"
                  :class="{ on: activeDoc && activeDoc.doc_id === d.doc_id }"
                  @click="activeDoc = d; pageNo = 1; sel = null">
            {{ d.filename }}<i>{{ d.page_count }} 页</i>
          </button>
        </div>
        <div class="pager" v-if="activeDoc">
          <button @click="turn(-1)" :disabled="pageNo <= 1">‹</button>
          <span>{{ pageNo }} / {{ activeDoc.page_count }}</span>
          <button @click="turn(1)" :disabled="pageNo >= activeDoc.page_count">›</button>
          <select v-model.number="zoom">
            <option :value="1.5">标准</option>
            <option :value="2">清晰</option>
            <option :value="3">放大</option>
          </select>
        </div>
        <div class="viewer" v-if="activeDoc">
          <div class="page-wrap" @pointerdown="boxDown">
            <img ref="pageImg" :src="pageUrl" draggable="false" @dragstart.prevent />
            <div v-if="sel" class="selbox" :style="selStyle()"></div>
          </div>
        </div>
        <div class="hint" v-else>放进来一份 PDF，在页面上拖框即可裁剪。</div>
        <div class="row">
          <button class="primary" :disabled="!sel" @click="crop">裁剪到题目</button>
          <button class="primary ghost" :disabled="!sel" @click="cropAnswer">裁剪到答案</button>
        </div>
        <div class="row">
          <button :disabled="!sel" @click="sel = null">清除选区</button>
        </div>
        <div class="busy" v-if="busy">{{ busy }}</div>
        <div class="hint" style="margin-top:8px">
          「裁剪到题目」进上方题目画布，「裁剪到答案」进下方答案画布；也可以把 PDF 页直接拖动到任一块画布。
        </div>
      </section>
    </aside>

    <div ref="floatEl" class="auxfloat" :class="{ min: !auxOpen }"
         :style="{ left: fpos.x + 'px', top: fpos.y + 'px' }">
      <div class="auxhead" @pointerdown="floatDown" @dblclick="toggleAux">
        <span v-if="!auxOpen" class="burger" title="入库信息（拖动可移动，双击展开）">≡</span>
        <template v-else>
          <b>入库信息</b>
          <span class="spacer"></span>
          <button class="icon" title="收起" @click.stop="toggleAux">—</button>
        </template>
      </div>
      <div v-if="auxOpen" class="auxbody">
        <div class="aux">
          <label class="f"><span>书册（入库位置）</span>
            <select v-model="form.book_id">
              <option v-for="b in meta.books" :key="b.id" :value="b.id">{{ b.title }}</option>
            </select>
          </label>
          <label class="f"><span>单元（可选）</span>
            <select v-model="form.unit_id">
              <option :value="null">不指定</option>
              <option v-for="u in units" :key="u.id" :value="u.id">{{ u.name }}</option>
            </select>
          </label>
          <div class="cols2">
            <label class="f"><span>题型</span>
              <select v-model="form.q_type">
                <option v-for="t in meta.qtypes" :key="t" :value="t">{{ t }}</option>
              </select>
            </label>
            <label class="f"><span>难度</span>
              <select v-model.number="form.difficulty">
                <option :value="1">基础</option><option :value="2">中等</option><option :value="3">较难</option>
              </select>
            </label>
          </div>
          <label class="f"><span>文献 / 教材 / 试卷</span>
            <input v-model="form.source_title" placeholder="论文名、书名或试卷名" />
          </label>
          <label class="f"><span>出处（作者·期刊·年份）</span>
            <input v-model="form.source_citation" placeholder="如：张三. 某研究[J]. 某学报, 2024, 12(3): 45-67" />
          </label>
          <label class="f"><span>位置（页码 / 题号）</span>
            <input v-model="form.locator" placeholder="如：p.128 / 第 3 题" />
          </label>
          <div class="f">
            <span>标签（去「设置 → 标签管理」维护）</span>
            <div class="tags">
              <button v-for="t in meta.tags" :key="t.id" class="chip"
                      :class="{ on: form.tags.includes(t.name) }" @click="pickTag(t.name)">{{ t.name }}</button>
            </div>
          </div>
          <label class="f"><span>状态</span>
            <select v-model="form.status">
              <option value="confirmed">已确认</option>
              <option value="draft">草稿</option>
            </select>
          </label>
          <label class="f"><span>参考答案（文字，可选）</span>
            <textarea v-model="form.answer" rows="3" placeholder="答案文字，支持 $公式$；也可以直接在下方答案画布排版"></textarea>
          </label>
          <div v-if="form.answer" class="apprev" v-html="renderMath(form.answer)"></div>
        </div>
      </div>
    </div>

    <section class="right">
      <div class="toolbar">
        <button class="icon" :title="qOpen ? '收起左栏' : '展开左栏'" @click="qOpen = !qOpen">
          {{ qOpen ? '⇤' : '⇥' }}
        </button>
        <span class="spacer"></span>
        <span class="muted" v-if="editingQuestionId">正在编辑 #{{ editingQuestionId }}</span>
        <button v-if="editingQuestionId" @click="resetAll">新建一题</button>
        <button class="primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : (editingQuestionId ? '更新题目' : '保存到题库') }}
        </button>
      </div>

      <div v-if="err" class="errbox">{{ err }}</div>
      <div v-if="msg" class="okbox">
        {{ msg }}
        <a v-if="savedId" href="javascript:void(0)" @click="goBank">去题库看看 →</a>
      </div>

      <CanvasPane ref="qPane" title="题目画布" metaable :max-pages="1"
                  hint="把左侧裁好的题目图拖进来，或点「＋ 文本」开始排版"
                  @activate="activePane = 'q'" />
      <CanvasPane ref="aPane" title="答案画布" :max-pages="2"
                  hint="把左侧「裁剪到答案」的图拖进来，或用「＋ 文本」写解析"
                  @activate="activePane = 'a'" />
    </section>
  </div>
</template>

<style scoped>
.writer { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); gap: 14px; align-items: start; }
.writer.left-off { grid-template-columns: minmax(0, 1fr); }
.left-off .left { display: none; }
.left { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.pane { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px; }
.pane h3 { margin: 0 0 10px; font-size: 14px; }
.drop { display: block; border: 1px dashed #c7d2e4; border-radius: 10px; padding: 12px; text-align: center; color: #4a5568; cursor: pointer; font-size: 13px; }
.drop input { display: none; }
.docs { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.doc { text-align: left; background: #f7f9fc; border: 1px solid #e7ebf2; border-radius: 8px; padding: 6px 8px; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc i { color: #8b95a5; font-style: normal; margin-left: 6px; }
.doc.on { border-color: var(--brand, #2f6fed); background: #eef4ff; color: var(--brand, #2f6fed); }
.pager { display: flex; align-items: center; gap: 6px; margin: 8px 0; font-size: 12.5px; }
.pager select { width: auto; }
.viewer { background: #f1f3f7; border-radius: 10px; padding: 8px; max-height: calc(100vh - 320px); overflow: auto; }
.page-wrap { position: relative; display: block; margin: 0 auto; cursor: crosshair; }
.page-wrap img { display: block; width: 100%; height: auto; touch-action: none; user-select: none; }
.selbox { position: absolute; border: 1px dashed var(--brand, #2f6fed); background: rgba(47, 111, 237, .12); pointer-events: none; }
.hint { color: #8b95a5; font-size: 12.5px; }
.row { display: flex; gap: 8px; margin-top: 10px; }
.row button { flex: 1; }
.busy { margin-top: 8px; font-size: 12px; color: var(--brand, #2f6fed); }
.aux { display: flex; flex-direction: column; gap: 8px; }
.f { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: #475467; }
.f input, .f select, .f textarea { font-size: 13px; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid #dfe5ee; background: #fff; border-radius: 999px; padding: 3px 10px; font-size: 12px; color: #475467; cursor: pointer; }
.chip.on { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; }
.apprev { margin-top: 8px; padding: 8px 10px; background: #f8fafc; border-radius: 8px; font-size: 13.5px; }

.right { background: #f7f9fc; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px; min-width: 0; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.icon { padding: 4px 8px; border: 1px solid #dfe5ee; background: #fff; border-radius: 7px; cursor: pointer; font-size: 12.5px; }
.spacer { flex: 1; }
.muted { font-size: 12px; color: #8b95a5; }

.auxfloat {
  position: fixed; z-index: 40; width: 348px; max-height: min(620px, calc(100vh - 40px));
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  box-shadow: 0 18px 40px rgba(16, 24, 40, .16); overflow: hidden;
  display: flex; flex-direction: column;
}
.auxfloat.min { width: auto; }
.auxhead {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  cursor: grab; user-select: none; background: #f8fafc; border-bottom: 1px solid var(--line, #e7ebf2);
}
.auxfloat.min .auxhead { border-bottom: none; }
.auxhead:active { cursor: grabbing; }
.auxhead b { font-size: 13px; }
.auxhead .burger { font-size: 18px; line-height: 1; padding: 2px 4px; }
.auxbody { padding: 12px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
@media (max-width: 1180px) {
  .writer { grid-template-columns: 1fr; }
}
</style>
