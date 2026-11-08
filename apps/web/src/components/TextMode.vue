<script setup>
// 单题模式：题干 + 小问（可嵌套）+ 分值 + 答案 + 图，实时 A4 预览。
// 不做富文本：纯文本框 + 轻量标记（$…$、_{}、^{}、空行分段），保住复制/搜索/原生撤销。
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { api } from '../api'
import CropDialog from './CropDialog.vue'
import FigureEdit from './FigureEdit.vue'
import ChemDock from './ChemDock.vue'
import QuestionSheet from './QuestionSheet.vue'

const props = defineProps({
  questionId: { type: Number, default: 0 },
  meta: { type: Object, default: () => ({}) },
  seed: { type: Object, default: null },
})
const emit = defineEmits(['saved'])

const doc = reactive(blankDoc())
const form = reactive({ book_id: null, unit_id: null, q_type: '简答', difficulty: 2,
  source_title: '', source_citation: '', locator: '', tags: [] })
const busy = ref(false)
const msg = ref('')
const err = ref('')
const dirty = ref(false)
const showPreview = ref(true)
const zoom = ref(0.72)
const openAns = reactive({})
const savedId = ref(props.questionId || 0)

function blankDoc() {
  return { v: 1, kind: 'text', meta: { title: '', score: 14, percent: 8, numbering: 'dash' },
    stem: [{ t: 'p', text: '' }], subs: [], source: {} }
}

function blankSub() {
  return { text: '', score: 2, answer: '', points: [], imgs: [], answer_images: [], subs: [] }
}

const units = computed(() => {
  const books = props.meta.books || []
  const b = books.find((x) => x.id === form.book_id)
  return (b && b.units) || props.meta.units || []
})
const topics = computed(() => props.meta.topics || [])
const qtypes = computed(() => props.meta.qtypes || ['简答'])

// ---------------------------------------------------------------- 结构操作
function walk(list, path, fn, parent = null) {
  const [i, ...rest] = path
  if (i == null || !list[i]) return
  if (!rest.length) fn(list, i, parent)
  else walk(list[i].subs || [], rest, fn, list[i])
}
function addSub(path) {
  push()
  if (!path.length) doc.subs.push(blankSub())
  else walk(doc.subs, path, (list, i) => { list[i].subs = list[i].subs || []; list[i].subs.push(blankSub()) })
  touch()
}
function delSub(path) {
  push()
  walk(doc.subs, path, (list, i) => list.splice(i, 1))
  touch()
}
// 编号：1-1 / 1-1-1，按树实时算，删掉自动重排
const flatSubs = computed(() => {
  const out = []
  const walkAll = (list, prefix, level) => {
    (list || []).forEach((s, i) => {
      const no = `${prefix}-${i + 1}`
      out.push({ sub: s, no, level, path: pathOf(s) })
      walkAll(s.subs, no, level + 1)
    })
  }
  const pathOf = (() => {
    const map = new WeakMap()
    const build = (list, base) => (list || []).forEach((s, i) => {
      const p = [...base, i]
      map.set(s, p)
      build(s.subs, p)
    })
    build(doc.subs, [])
    return (s) => map.get(s) || []
  })()
  walkAll(doc.subs, '1', 0)
  return out
})

function stemText() {
  return doc.stem.filter((b) => b.t === 'p').map((b) => b.text || '').join('\n\n')
}
function setStemText(v) {
  const imgs = doc.stem.filter((b) => b.t !== 'p')
  doc.stem = [{ t: 'p', text: v }, ...imgs]
  touch()
}
function paraText(i) { return doc.stem[i]?.text || '' }
function setPara(i, v) { if (doc.stem[i]) { push(); doc.stem[i].text = v } }

// ---------------------------------------------------------------- 图
function addStemImage(path, caption = '') {
  push()
  const n = doc.stem.filter((b) => b.t === 'img').length + 1
  doc.stem.push({ t: 'img', path, caption: caption || `图 ${n}`, w: 415 })
  touch()
}
function addSubImage(sub, path, caption = '') {
  push()
  sub.imgs = sub.imgs || []
  sub.imgs.push({ path, caption: caption || `图 ${sub.imgs.length + 1}`, w: 415 })
  touch()
}
/* 悬浮的公式/结构式面板：插入到「最后聚焦的那个输入框」 */
let lastField = null
function onFocusIn(e) {
  const t = e.target
  if (!t || (t.tagName !== 'TEXTAREA' && t.tagName !== 'INPUT')) return
  // 悬浮面板自己的输入框不算「插入目标」
  if (t.closest && t.closest('.dock')) return
  lastField = t
}
function insertAtCursor(text) {
  const el = lastField
  if (el && typeof el.selectionStart === 'number' && el.tagName === 'TEXTAREA') {
    const a = el.selectionStart
    const b = el.selectionEnd
    const v = String(el.value || '')
    el.value = v.slice(0, a) + text + v.slice(b)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.selectionStart = el.selectionEnd = a + text.length
    el.focus()
    return true
  }
  // 没有聚焦任何输入框：接到题干最后一段
  push()
  let last = doc.stem[doc.stem.length - 1]
  if (!last || last.t !== 'p') { last = { t: 'p', text: '' }; doc.stem.push(last) }
  last.text = (last.text || '') + text
  touch()
  return true
}
function moveStemBlock(i, d) {
  const j = i + d
  if (j < 0 || j >= doc.stem.length) return
  push()
  const a = doc.stem
  ;[a[i], a[j]] = [a[j], a[i]]
  touch()
}
function delAnswerImage(sub, j) {
  push()
  sub.answer_images.splice(j, 1)
  touch()
}
function delSubImage(f, im) {
  push()
  f.sub.imgs = (f.sub.imgs || []).filter((x) => x !== im)
  touch()
}
function onDockText(t) {
  insertAtCursor(t)
}
function onDockImage(path, w = 110) {
  const el = lastField
  const no = el && el.dataset ? el.dataset.no : ''
  const t = no ? flatSubs.value.find((x) => x.no === no) : null
  push()
  if (t) {
    t.sub.imgs = t.sub.imgs || []
    t.sub.imgs.push({ path, caption: '', w })
  } else {
    doc.stem.push({ t: 'img', path, caption: '', w })
  }
  touch()
}

/* 从文献裁图：题干/小问共用同一个弹窗 */
const crop = reactive({ open: false, target: null })
function openCrop(target) {
  crop.target = target
  crop.open = true
}
function onCropDone(fig) {
  const t = crop.target
  if (!t) return
  const w = fig.width || 300
  push()
  if (t === 'stem') {
    doc.stem.push({ t: 'img', path: fig.path, caption: '', w, align: 'center', source: fig.source })
  } else if (t && t.kind === 'ans') {
    t.sub.answer_images = t.sub.answer_images || []
    t.sub.answer_images.push({ path: fig.path, caption: '', w, align: 'center', source: fig.source })
  } else {
    const node = t
    node.imgs = node.imgs || []
    node.imgs.push({ path: fig.path, caption: '', w, align: 'center', source: fig.source })
  }
  touch()
}

function pickImage(target) {
  const inp = document.createElement('input')
  inp.type = 'file'
  inp.accept = 'image/*'
  inp.onchange = async () => {
    const f = inp.files && inp.files[0]
    if (!f) return
    const dataUrl = await new Promise((res) => {
      const fr = new FileReader()
      fr.onload = () => res(String(fr.result || ''))
      fr.readAsDataURL(f)
    })
    try {
      const r = await api.post('/api/manual/image', { data_url: dataUrl })
      if (target === 'stem') addStemImage(r.path)
      else addSubImage(target, r.path)
    } catch (e) { err.value = e?.message || String(e) }
  }
  inp.click()
}
function delStemBlock(i) { push(); doc.stem.splice(i, 1); touch() }

// 表格：一行一条，`|` 分隔，首行表头
function addTable(sub) {
  push()
  const blk = { t: 'table', head: ['', 'A', 'B'], rows: [['', '', '']], caption: '' }
  if (sub) { sub.tables = sub.tables || []; sub.tables.push(blk) } else doc.stem.push(blk)
  touch()
}
function tableToText(blk) {
  const head = (blk.head || []).join(' | ')
  const rows = (blk.rows || []).map((r) => (r || []).join(' | '))
  return [head, ...rows].join('\n')
}
function textToTable(blk, v) {
  const lines = String(v || '').split('\n').map((l) => l.trim()).filter(Boolean)
  const cells = (l) => l.split('|').map((c) => c.trim())
  blk.head = lines.length ? cells(lines[0]) : []
  blk.rows = lines.slice(1).map(cells)
  touch()
}

// ---------------------------------------------------------------- 答案 / 给分点
function toggleAns(no) { openAns[no] = !openAns[no] }
function addPoint(sub) { push(); sub.points = sub.points || []; sub.points.push(''); touch() }
function delPoint(sub, i) { push(); sub.points.splice(i, 1); touch() }

// ---------------------------------------------------------------- 撤销 / 保存
const undoStack = ref([])
const redoStack = ref([])
let lock = false
let timer = 0
let prev = ''
function snap() { return JSON.stringify({ doc, form }) }
function push() { touch() }
function commit() {
  const now = snap()
  if (lock || now === prev) { prev = now; return }
  clearTimeout(timer)
  timer = setTimeout(() => {
    undoStack.value.push(prev)          // 记住这一串编辑开始前的状态
    if (undoStack.value.length > 60) undoStack.value.shift()
    redoStack.value = []
    prev = snap()
  }, 700)
}
watch(() => snap(), commit)
function apply(s) {
  try {
    const o = JSON.parse(s)
    lock = true
    Object.assign(doc, o.doc)
    Object.assign(form, o.form)
    prev = snap()
    nextTick(() => { lock = false })
  } catch (e) { lock = false }
}
function undo() {
  if (!undoStack.value.length) return
  redoStack.value.push(snap())
  apply(undoStack.value.pop())
  dirty.value = true
}
function redo() {
  if (!redoStack.value.length) return
  undoStack.value.push(snap())
  apply(redoStack.value.pop())
  dirty.value = true
}
function touch() { dirty.value = true }

const totalScore = computed(() => {
  const leaf = (list) => (list || []).reduce((a, s) => a + (s.subs && s.subs.length ? leaf(s.subs) : Number(s.score || 0)), 0)
  return leaf(doc.subs)
})
const scoreWarn = computed(() => {
  const bad = []
  const walkWarn = (list, prefix) => (list || []).forEach((s, i) => {
    const no = `${prefix}-${i + 1}`
    if (s.subs && s.subs.length) {
      const inner = s.subs.reduce((a, k) => a + Number(k.score || 0), 0)
      if (Math.abs(inner - Number(s.score || 0)) > 1e-6) bad.push(`${no}：子和 ${inner} ≠ 父 ${s.score}`)
      walkWarn(s.subs, no)
    } else if (!Number(s.score)) bad.push(`${no}：0 分`)
  })
  walkWarn(doc.subs, '1')
  return bad
})

const previewDoc = computed(() => {
  const d = JSON.parse(JSON.stringify({ ...doc, meta: { ...doc.meta, number: 1 } }))
  return d
})
const figCount = computed(() => doc.stem.filter((b) => b.t === 'img').length
  + flatSubs.value.reduce((a, x) => a + (x.sub.imgs || []).length, 0))

async function save(st = 'confirmed') {
  if (busy.value) return
  if (!stemText().trim() && !doc.stem.some((b) => b.t === 'img')) { err.value = '题干是空的'; return }
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const body = {
      doc: JSON.parse(JSON.stringify(doc)),
      question_id: savedId.value || null,
      book_id: form.book_id, unit_id: form.unit_id || null,
      q_type: form.q_type, difficulty: form.difficulty,
      tags: form.tags || [], source_title: form.source_title,
      source_citation: form.source_citation, locator: form.locator,
      status: st,
    }
    const r = await api.post('/api/authoring/compose-text', body)
    savedId.value = r.id
    dirty.value = false
    msg.value = (r.created ? '已入库' : '已更新') + ` #${r.id}`
      + (r.warnings && r.warnings.length ? ` · ${r.warnings.length} 条提醒` : '')
    emit('saved', r)
  } catch (e) {
    err.value = e?.message || String(e)
  } finally {
    busy.value = false
  }
}

function onKeys(e) {
  const ctrl = e.ctrlKey || e.metaKey
  if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return }
  if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return }
  if (ctrl && e.key === 'Enter') { e.preventDefault(); addSub([]); return }
}

async function load() {
  if (props.questionId) {
    try {
      const d = await api.get(`/api/authoring/compose/${props.questionId}`)
      const lay = d.layout || {}
      if (lay.kind === 'text') Object.assign(doc, blankDoc(), lay)
      Object.assign(form, {
        book_id: d.book_id, unit_id: d.unit_id, q_type: d.q_type, difficulty: d.difficulty,
        source_title: d.source_title || '', source_citation: d.source_citation || '',
        locator: d.locator || '', tags: d.tags || [],
      })
      savedId.value = d.id
    } catch (e) { err.value = e?.message || String(e) }
  } else {
    form.book_id = props.meta.defaultBookId || (props.meta.books && props.meta.books[0]?.id) || null
    if (props.seed) {
      if (props.seed.text) doc.stem = [{ t: 'p', text: props.seed.text }]
      if (props.seed.source) {
        form.source_title = props.seed.source.title || ''
        form.source_citation = props.seed.source.quote || ''
        form.locator = props.seed.source.page ? String(props.seed.source.page) : ''
      }
    }
  }
  prev = snap()
}

// 路由带着 id 变化时要重载：同一组件复用，onMounted 只跑一次
watch(() => props.questionId, () => { load() }, { immediate: true })

defineExpose({ save, isDirty: () => dirty.value, doc })
</script>

<template>
  <div class="tm" @keydown="onKeys">
    <div class="tmbar">
      <span class="sum">总分 {{ doc.meta.score || 0 }} ｜ 小问分 {{ totalScore }} ｜ 图 {{ figCount }}</span>
      <span class="wsp" />
      <button class="ghost" :disabled="busy" @click="save('draft')">存草稿</button>
      <button class="primary" :disabled="busy" @click="save('confirmed')">保存到题库</button>
    </div>

    <div class="tmbody">
      <div class="tmleft">
        <p v-if="scoreWarn.length" class="warnbar">
          <b>分值提醒</b>（不拦保存）：{{ scoreWarn.join('；') }}
        </p>

        <div class="card">
          <div class="crow">
            <input v-model="doc.meta.title" class="title" placeholder="大题标题，如：未知物的推断" @input="touch" />
            <label class="mini"><span>分值</span><input v-model.number="doc.meta.score" type="number" min="0" @input="touch" /></label>
            <label class="mini"><span>占比%</span><input v-model.number="doc.meta.percent" type="number" min="0" @input="touch" /></label>
            <label class="mini"><span>编号</span>
              <select v-model="doc.meta.numbering"><option value="dash">1-1</option><option value="paren">(1)</option></select>
            </label>
          </div>
          <textarea class="stem" :value="stemText()" rows="8"
                    placeholder="题干正文。支持 $…$ 公式、\ce{} 化学式、H_{2}O 上下标、空行分段"
                    @input="setStemText($event.target.value)" />
          <div class="figrow">
            <button class="sm" @click="openCrop('stem')">✂ 文献裁图</button>
            <button class="sm" @click="pickImage('stem')">＋ 图</button>
            <button class="sm" @click="addTable(null)">＋ 表</button>
            <span class="hint">图号自动编，图注留空则生成「图 N」</span>
          </div>
          <div v-for="(b, i) in doc.stem" :key="'st' + i" class="blk">
            <template v-if="b.t === 'img'">
              <FigureEdit :fig="b" :can-up="i > 0" :can-down="i < doc.stem.length - 1"
                          @change="touch" @del="delStemBlock(i)"
                          @up="moveStemBlock(i, -1)" @down="moveStemBlock(i, 1)" />
            </template>
            <template v-else-if="b.t === 'table'">
              <textarea class="tbl" :value="tableToText(b)" rows="3"
                        placeholder="一行一条，| 分隔，首行表头"
                        @input="textToTable(b, $event.target.value)" />
              <button class="x" @click="delStemBlock(i)">×</button>
            </template>
          </div>
        </div>

        <div v-for="(f, idx) in flatSubs" :key="'sb' + idx" class="card sub" :style="{ marginLeft: f.level * 18 + 'px' }">
          <div class="shead">
            <b class="no">{{ f.no }}</b>
            <input v-model.number="f.sub.score" type="number" min="0" class="sc" @input="touch" />
            <span class="mut">分</span>
            <span class="wsp" />
            <button class="sm" @click="addSub(f.path)">＋ 子问</button>
            <button class="sm danger" @click="delSub(f.path)">删除</button>
          </div>
          <textarea v-model="f.sub.text" class="subtext" rows="2" placeholder="小问题干"
                    :data-no="f.no" @input="touch" />
          <div class="figrow">
            <button class="sm" @click="openCrop(f.sub)">✂ 文献裁图</button>
            <button class="sm" @click="pickImage(f.sub)">＋ 图</button>
            <button class="sm" @click="toggleAns(f.no)">{{ openAns[f.no] ? '收起答案' : '写答案' }}</button>
          </div>
          <div v-if="(f.sub.imgs || []).length" class="thumbs">
            <FigureEdit v-for="(im, j) in f.sub.imgs" :key="j" :fig="im"
                        :can-up="false" :can-down="false"
                        @change="touch" @del="delSubImage(f, im)" />
          </div>
          <div v-if="openAns[f.no]" class="ansbox">
            <textarea v-model="f.sub.answer" class="ans" rows="2" placeholder="参考答案（导出时红字）" @input="touch" />
            <div v-for="(p, j) in f.sub.points || []" :key="'pt' + j" class="ptrow">
              <input v-model="f.sub.points[j]" class="pt" placeholder="给分点，如：X₁ 2 分，其余每个 1 分" @input="touch" />
              <button class="x" @click="delPoint(f.sub, j)">×</button>
            </div>
            <button class="sm" @click="addPoint(f.sub)">＋ 给分点</button>
            <div class="figrow">
              <button class="sm" @click="openCrop({ kind: 'ans', sub: f.sub })">✂ 答案配图</button>
              <span class="hint">答案里的结构图，宽度/对齐同样可调</span>
            </div>
            <div v-if="(f.sub.answer_images || []).length" class="thumbs">
              <FigureEdit v-for="(im, j) in f.sub.answer_images" :key="'ai' + j" :fig="im"
                          @change="touch" @del="delAnswerImage(f.sub, j)" />
            </div>
          </div>
        </div>

        <div class="addrow">
          <button class="primary" @click="addSub([])">＋ 小问</button>
          <span class="hint">Ctrl+Enter 也能加；Ctrl+S 保存、Ctrl+Z 撤销</span>
        </div>

        <p v-if="err" class="errbar">{{ err }}</p>
        <p v-if="msg" class="okbar">{{ msg }}</p>
      </div>

      <div class="tmright" :class="{ off: !showPreview }">
        <div class="phead">
          <b>预览（A4）</b>
          <button class="ghost sm" @click="zoom = Math.max(0.4, zoom - 0.1)">－</button>
          <span class="mut">{{ Math.round(zoom * 100) }}%</span>
          <button class="ghost sm" @click="zoom = Math.min(1.4, zoom + 0.1)">＋</button>
          <button class="ghost sm" @click="showPreview = !showPreview">{{ showPreview ? '收起' : '展开' }}</button>
        </div>
        <div v-show="showPreview" class="sheetwrap">
          <div class="scaler" :style="{ transform: `scale(${zoom})` }">
            <QuestionSheet :doc="previewDoc" variant="a4" show-answers />
          </div>
        </div>
      </div>
    </div>

    <CropDialog :open="crop.open" @close="crop.open = false" @done="onCropDone" />
    <ChemDock @text="onDockText" @image="onDockImage" />
  </div>
</template>

<style scoped>
.tm { display: flex; flex-direction: column; height: 100%; min-height: 0; flex: 1 1 auto; width: 100%; min-width: 0; }
.tmbar { display: flex; align-items: center; gap: 10px; padding: 8px 14px; background: #fff;
  border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
.tmbar .fi { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: #6b7280; }
.tmbar .fi select, .tmbar .fi input { height: 28px; border: 1px solid #d1d5db; border-radius: 6px; padding: 0 8px; font-size: 12.5px; }
.tmbar .fi.grow input { width: 190px; }
.tmbar .sum { font-size: 12.5px; color: #374151; }
.tmbar .wsp { flex: 1; }
.tmbody { flex: 1; display: flex; min-height: 0; }
.tmleft { flex: 1; overflow: auto; padding: 14px; background: #f8fafc; }
.tmright { flex: 0 0 520px; width: 520px; border-left: 1px solid #e5e7eb; background: #eef2f7; display: flex; flex-direction: column; }
.tmright.off { flex: 0 0 150px; width: 150px; }
.phead { display: flex; align-items: center; gap: 8px; padding: 6px 10px; font-size: 12.5px; background: #fff; border-bottom: 1px solid #e5e7eb; }
.sheetwrap { flex: 1; overflow: auto; padding: 10px; }
.scaler { transform-origin: top left; width: 595.3pt; background: #fff; box-shadow: 0 4px 18px rgba(15,23,42,.12); }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; }
.crow { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.crow .title { flex: 1; min-width: 220px; height: 32px; border: 1px solid #d1d5db; border-radius: 6px; padding: 0 10px; font-size: 14px; font-weight: 600; }
.mini { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #6b7280; }
.mini input { width: 56px; height: 28px; border: 1px solid #d1d5db; border-radius: 6px; padding: 0 6px; }
.mini select { height: 28px; border: 1px solid #d1d5db; border-radius: 6px; }
.stem, .subtext, .ans, .tbl { width: 100%; border: 1px solid #d1d5db; border-radius: 8px; padding: 8px 10px;
  font-size: 13px; line-height: 1.7; font-family: inherit; resize: vertical; box-sizing: border-box; }
.stem:focus, .subtext:focus, .ans:focus { outline: 2px solid #bfdbfe; border-color: #93c5fd; }
.figrow { display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.hint { font-size: 11.5px; color: #9ca3af; }
.blk, .th { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.thumb { max-height: 54px; max-width: 120px; border: 1px solid #e5e7eb; border-radius: 4px; background: #fff; }
.cap { flex: 1; height: 26px; border: 1px solid #d1d5db; border-radius: 6px; padding: 0 8px; font-size: 12px; }
.thumbs { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.sub .shead { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.sub .no { font-family: "Times New Roman", serif; font-size: 14px; color: #111827; }
.sub .sc { width: 52px; height: 26px; border: 1px solid #d1d5db; border-radius: 6px; padding: 0 6px; }
.mut { font-size: 12px; color: #9ca3af; }
.wsp { flex: 1; }
.ansbox { margin-top: 8px; border-top: 1px dashed #e5e7eb; padding-top: 8px; }
.ptrow { display: flex; gap: 6px; margin-top: 6px; }
.pt { flex: 1; height: 26px; border: 1px solid #d1d5db; border-radius: 6px; padding: 0 8px; font-size: 12px; }
.x { border: 0; background: transparent; color: #9ca3af; cursor: pointer; font-size: 14px; }
.x:hover { color: #dc2626; }
.addrow { display: flex; align-items: center; gap: 10px; }
.warnbar { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; padding: 6px 10px; border-radius: 8px; font-size: 12.5px; }
.errbar { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; padding: 6px 10px; border-radius: 8px; font-size: 12.5px; }
.okbar { background: #ecfdf5; border: 1px solid #a7f3d0; color: #047857; padding: 6px 10px; border-radius: 8px; font-size: 12.5px; }
.tmbar button, .tmleft button { height: 28px; padding: 0 10px; border-radius: 6px; border: 1px solid #d1d5db; background: #fff; font-size: 12.5px; cursor: pointer; }
.tmbar button.primary, .tmleft button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
.tmleft button.danger { color: #b91c1c; border-color: #fecaca; }
.tmleft button.sm { height: 26px; padding: 0 8px; font-size: 12px; }
</style>
