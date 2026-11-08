<script setup>
/**
 * 从文献裁图（共用组件）
 *
 * 出整卷的编辑器（PaperMaker）和单题模式（TextMode）都用这一个弹窗：
 * 选文献 → 翻页 → 拖框（Shift 等比、方向键微调、Esc 取消、Ctrl+Z 撤销）
 * → 按 300dpi 裁切 → 去白边 → emit('done', figure)。
 */
import { onBeforeUnmount, onMounted, reactive, watch } from 'vue'
import { api } from '../api'

const props = defineProps({
  open: { type: Boolean, default: false },
  // 已有的图片来源（重裁时定位回原页、还原选区）：{doc_id, page, rect}
  source: { type: Object, default: null },
  title: { type: String, default: '从文献裁图' },
})
const emit = defineEmits(['close', 'done'])

const st = reactive({
  docs: [], docId: '', page: 1, pages: 1, rect: null, drag: null,
  hist: [], busy: false, note: '', zoom: 2,
})

async function init() {
  st.busy = false
  st.note = ''
  st.hist = []
  st.drag = null
  if (!st.docs.length) {
    try {
      const r = await api.get('/api/manual/docs')
      st.docs = r.docs || []
    } catch (e) { st.docs = [] }
  }
  const src = props.source || null
  if (src && src.doc_id) {
    st.docId = src.doc_id
    st.page = Math.max(1, Number(src.page || 1))
    st.rect = src.rect ? [...src.rect] : null
  } else if (!st.docId && st.docs.length) {
    st.docId = st.docs[0].doc_id
    st.page = 1
    st.rect = null
  }
  syncPages()
}

function syncPages() {
  const d = st.docs.find((x) => x.doc_id === st.docId)
  st.pages = d?.page_count || 1
}

watch(() => props.open, (v) => { if (v) init() })

function close() {
  if (st.busy) return
  st.drag = null
  emit('close')
}
function pickDoc(id) {
  st.docId = id
  st.page = 1
  st.rect = null
  syncPages()
}
function imgUrl() {
  if (!st.docId) return ''
  return `/api/manual/doc/${st.docId}/page/${st.page}.png?zoom=${st.zoom}`
}
function norm(e) {
  const box = e.currentTarget.getBoundingClientRect()
  return [
    Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)),
    Math.min(1, Math.max(0, (e.clientY - box.top) / box.height)),
  ]
}
function dragStart(e) {
  e.preventDefault()
  const [x, y] = norm(e)
  st.drag = { x0: x, y0: y }
  st.rect = [x, y, x, y]
}
function dragMove(e) {
  const d = st.drag
  if (!d) return
  const [x, y] = norm(e)
  let x1 = x
  let y1 = y
  if (e.shiftKey) { // 等比：以起点为角，取较长边
    const w = Math.abs(x - d.x0)
    const h = Math.abs(y - d.y0)
    const s = Math.max(w, h)
    x1 = d.x0 + Math.sign(x - d.x0 || 1) * s
    y1 = d.y0 + Math.sign(y - d.y0 || 1) * s
  }
  st.rect = [Math.min(d.x0, x1), Math.min(d.y0, y1), Math.max(d.x0, x1), Math.max(d.y0, y1)]
}
function dragEnd() {
  if (!st.drag) return
  st.drag = null
  const r = st.rect
  if (r && r[2] - r[0] > 0.004 && r[3] - r[1] > 0.004) {
    st.hist.push([...r])
    if (st.hist.length > 30) st.hist.shift()
  } else {
    st.rect = null
  }
}
function nudge(dx, dy) {
  if (!st.rect) return
  const r = [...st.rect]
  const sx = dx ? 1 / 600 : 0
  const sy = dy ? 1 / 850 : 0
  for (const i of [0, 2]) r[i] = Math.min(1, Math.max(0, r[i] + dx * sx))
  for (const i of [1, 3]) r[i] = Math.min(1, Math.max(0, r[i] + dy * sy))
  st.rect = r
}
function onKey(e) {
  if (!props.open) return
  if (e.key === 'Escape') { close(); return }
  const step = e.shiftKey ? 10 : 1
  if (e.key === 'ArrowLeft') { e.preventDefault(); nudge(-step, 0) }
  else if (e.key === 'ArrowRight') { e.preventDefault(); nudge(step, 0) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); nudge(0, -step) }
  else if (e.key === 'ArrowDown') { e.preventDefault(); nudge(0, step) }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
    e.preventDefault()
    st.rect = st.hist.pop() || null
  }
}

async function doCrop(thenNext = false) {
  const r = st.rect
  if (!r || !st.docId) { st.note = '先在 PDF 上拖一个框'; return }
  st.busy = true
  st.note = '正在按 300dpi 裁切…'
  try {
    const c = await api.post('/api/manual/crop', {
      doc_id: st.docId, page_no: st.page, rect: r, dpi: 300,
    })
    let path = c.path
    let note = ''
    try {
      const t = await api.post('/api/manual/trim', { path, px_per_pt: 300 / 72 })
      path = t.path
      note = t.cropped ? '已去白边' : '无需去白边'
    } catch (e) { /* 去白边失败不影响插图 */ }
    emit('done', {
      path,
      caption: '',
      width: 415,
      source: { doc_id: st.docId, page: st.page, kind: 'figure', rect: [...r], dpi: 300 },
    })
    st.note = `已裁好（${c.width}×${c.height}px，${note}）`
    st.hist = []
    if (thenNext) {
      st.page = Math.min(st.pages, st.page + 1)
      st.rect = null
    } else {
      emit('close')
    }
  } catch (e) {
    st.note = '裁切失败：' + e.message
  } finally {
    st.busy = false
  }
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div v-if="open" class="cropmask" @click.self="close">
    <div class="cropcard">
      <div class="crophead">
        <b>{{ title }}</b>
        <select :value="st.docId" @change="pickDoc($event.target.value)">
          <option v-for="d in st.docs" :key="d.doc_id" :value="d.doc_id">{{ d.filename }}</option>
        </select>
        <button class="mini" @click="st.page = Math.max(1, st.page - 1)">上一页</button>
        <span>{{ st.page }} / {{ st.pages }}</span>
        <button class="mini" @click="st.page = Math.min(st.pages, st.page + 1)">下一页</button>
        <span class="muted">拖框选择；Shift 等比；方向键微调（Shift ×10），Esc 取消，Ctrl+Z 撤销</span>
      </div>
      <div class="cropbody">
        <div class="viewer" @mousedown="dragStart" @mousemove="dragMove" @mouseup="dragEnd" @mouseleave="dragEnd">
          <img v-if="st.docId" :src="imgUrl()" draggable="false" />
          <div v-if="st.rect" class="sel" :style="{
            left: st.rect[0] * 100 + '%', top: st.rect[1] * 100 + '%',
            width: (st.rect[2] - st.rect[0]) * 100 + '%',
            height: (st.rect[3] - st.rect[1]) * 100 + '%',
          }"></div>
          <p v-if="!st.docId" class="nodoc">还没有上传文献。先去「精读」上传一份 PDF。</p>
        </div>
      </div>
      <div class="cropfoot">
        <span class="muted">{{ st.note }}</span>
        <div class="grow"></div>
        <button @click="close">取消</button>
        <button :disabled="st.busy || !st.docId" @click="doCrop(true)">裁切并继续下一张</button>
        <button class="primary" :disabled="st.busy || !st.docId" @click="doCrop(false)">裁切并插入</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cropmask { position: fixed; inset: 0; background: rgba(17, 24, 39, .55); z-index: 60; display: flex; align-items: center; justify-content: center; }
.cropcard { background: #fff; border-radius: 12px; width: min(1080px, 94vw); height: min(880px, 92vh); display: flex; flex-direction: column; overflow: hidden; }
.crophead, .cropfoot { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-size: 13px; flex-wrap: wrap; }
.cropfoot { border-bottom: 0; border-top: 1px solid #e5e7eb; }
.crophead select { width: auto; max-width: 260px; font-size: 13px; }
.crophead .mini, .cropfoot button {
  height: 28px; padding: 0 10px; border-radius: 6px; border: 1px solid #d1d5db;
  background: #fff; font-size: 12.5px; cursor: pointer;
}
.cropfoot button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
.cropbody { flex: 1; overflow: hidden; background: #3f4550; display: flex; align-items: center; justify-content: center; padding: 12px; min-height: 0; }
.viewer { position: relative; user-select: none; cursor: crosshair; height: 100%; }
.viewer img { display: block; height: 100%; width: auto; max-width: 100%; }
.viewer .sel { position: absolute; border: 1.5px solid #ffd400; background: rgba(255, 212, 0, .18); box-shadow: 0 0 0 1px rgba(0, 0, 0, .25); pointer-events: none; }
.viewer .nodoc { color: #fff; font-size: 13px; }
.muted { color: #6b7280; font-size: 12px; }
.grow { flex: 1; }
</style>
