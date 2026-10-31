<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import { renderMath } from '../math.js'

const props = defineProps({
  title: { type: String, default: '画布' },
  hint: { type: String, default: '把左侧裁好的图拖进来，或点「＋ 文本」开始排版' },
  maxPages: { type: Number, default: 1 },
  metaable: { type: Boolean, default: false },   // 是否带页眉页脚（只有题目画布需要）
})
const emit = defineEmits(['activate', 'changed'])

const PW = 595
const PH = 842
const MARGIN = 48

const blocks = ref([])
const selectedId = ref('')
const selIds = ref([])
const clip = ref([])
const editingId = ref('')
const scale = ref(1)
const autoAlign = ref(true)
const trimBlank = ref(true)
const fontTarget = ref(12)          // 统一字号（pt）
const textColor = ref('#111111')    // 文字颜色（选中块或新建块）
const tip = ref('')                 // 画布内提示（统一字号结果等）
const imgPxPerPt = ref(3)           // 裁剪图默认 zoom=3（3px/pt）
const collapsed = ref(false)
const metaOpen = ref(false)
const band = reactive({ header_left: '', header_center: '', header_right: '', footer_center: '', name_line: false })
const hasMeta = computed(() => !!(band.header_left || band.header_center || band.header_right || band.footer_center || band.name_line))

let seq = 0
const uid = () => `b${Date.now().toString(36)}${(seq++).toString(36)}`
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
const imgUrl = (p) => (p ? `/api/images/${p}` : '')

function blockH(b) {
  if (b.type === 'image') return b.h || b.w * 0.4
  const size = b.size || 13
  const per = Math.max(8, Math.floor((b.w || 400) / (size * 0.98)))
  return Math.max(size * 1.6, Math.ceil((b.text || '').length / per) * size * 1.55)
}

const limit = computed(() => props.maxPages * PH)
const contentBottom = computed(() => {
  const m = blocks.value.reduce((x, b) => Math.max(x, b.y + blockH(b)), 0)
  if (m <= 0) return Math.min(limit.value, 420)      // 空画布给个短画布，不用整页
  return Math.min(limit.value, Math.max(260, m + 24))
})
const pageHeight = computed(() => (trimBlank.value ? contentBottom.value : limit.value))
const selected = computed(() => blocks.value.find((b) => b.id === selectedId.value) || null)
const selectedText = computed(() => (selected.value && selected.value.type === 'text' ? selected.value : null))

function isSel(id) { return selIds.value.includes(id) }
function selectOnly(id) { selectedId.value = id || ''; selIds.value = id ? [id] : [] }
function selectBlocks(list) { selIds.value = list.map((b) => b.id); selectedId.value = selIds.value[0] || '' }
function selectGroupOf(b) { if (b.gid) selectBlocks(blocks.value.filter((x) => x.gid === b.gid)); else selectOnly(b.id) }
function toggleSel(b) {
  if (isSel(b.id)) { selIds.value = selIds.value.filter((x) => x !== b.id); selectedId.value = selIds.value[0] || '' }
  else { selIds.value = [...selIds.value, b.id]; selectedId.value = b.id }
}
function clearSel() { selectedId.value = ''; selIds.value = []; editingId.value = '' }

function groupSel() {
  if (selIds.value.length < 2) return
  const gid = 'g' + Date.now().toString(36)
  for (const b of blocks.value) if (isSel(b.id)) b.gid = gid
  emit('changed')
}
function ungroupSel() {
  for (const b of blocks.value) if (isSel(b.id) && b.gid) b.gid = null
  emit('changed')
}
function copySel() {
  const list = blocks.value.filter((b) => isSel(b.id))
  if (list.length) clip.value = list.map((b) => ({ ...b }))
}
function pasteClip() {
  if (!clip.value.length) return
  const gmap = {}
  const made = []
  for (const src of clip.value) {
    const b = reactive({ ...src, id: uid(), x: Math.min(src.x + 16, PW - 40), y: Math.min(src.y + 16, limit.value - 40) })
    if (src.gid) { gmap[src.gid] = gmap[src.gid] || 'g' + uid(); b.gid = gmap[src.gid] }
    blocks.value.push(b)
    made.push(b)
  }
  selectBlocks(made)
  emit('changed')
}
function removeSelected() {
  if (!selIds.value.length) return
  const ids = new Set(selIds.value)
  blocks.value = blocks.value.filter((b) => !ids.has(b.id))
  clearSel()
  emit('changed')
}
function removeBlock(id) {
  blocks.value = blocks.value.filter((b) => b.id !== id)
  if (isSel(id)) { selIds.value = selIds.value.filter((x) => x !== id); selectedId.value = selIds.value[0] || '' }
  emit('changed')
}
function bringTop(id) {
  const b = blocks.value.find((x) => x.id === id)
  if (!b) return
  blocks.value = [...blocks.value.filter((x) => x.id !== id), b]
}
function rotateSel() {
  const target = selected.value && selected.value.type === 'image'
    ? selected.value
    : blocks.value.find((b) => b.type === 'image' && selIds.value.includes(b.id))
  if (!target) return
  target.rotate = ((target.rotate || 0) + 90) % 360
  if (target.rotate % 180 === 90) { const w = target.w; target.w = target.h; target.h = w }
  emit('changed')
}
function bumpSize(delta) {
  const b = selectedText.value
  if (!b) return
  b.size = clamp(Math.round((b.size || 13) + delta), 6, 48)
}
function toggleBold() { const b = selectedText.value; if (b) b.bold = !b.bold }
function setAlign(a) { const b = selectedText.value; if (b) b.align = a }
function nudge(dx, dy) {
  const ids = selIds.value.length ? selIds.value : (selectedId.value ? [selectedId.value] : [])
  for (const id of ids) {
    const b = blocks.value.find((x) => x.id === id)
    if (!b) continue
    b.x = clamp(Math.round(b.x + dx), 0, PW - (b.w || 100))
    b.y = clamp(Math.round(b.y + dy), 0, limit.value - 40)
  }
  emit('changed')
}

function nextY() {
  const bottom = blocks.value.reduce((m, b) => Math.max(m, b.y + blockH(b)), 40)
  return Math.min(limit.value - 60, Math.round(bottom + 10))
}

function addText(text = '') {
  const b = reactive({ id: uid(), type: 'text', text: text || '', x: MARGIN, y: nextY(), w: PW - MARGIN * 2, size: 13, align: 'left', bold: false })
  blocks.value.push(b)
  selectOnly(b.id)
  if (!text) nextTick(() => { editingId.value = b.id })
  emit('changed')
  return b
}

function imageSize(path) {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve({ w: img.naturalWidth || 600, h: img.naturalHeight || 400 })
    img.onerror = () => resolve({ w: 600, h: 400 })
    img.src = imgUrl(path)
  })
}

async function addImage(path, atX, atY, fullWidth = false, opts = {}) {
  const pxPerPt = opts.pxPerPt || imgPxPerPt.value || 3
  const target = opts.targetFont || fontTarget.value || 12

  // 先裁掉白边（pad 取 2px，让墨迹几乎贴边，方便按文字对齐）
  let p = path
  let natW = 0
  let natH = 0
  let fontPt = 0
  try {
    const t = await api.post('/api/manual/trim', { path, px_per_pt: pxPerPt, pad: 2 })
    p = t.path; natW = t.width; natH = t.height; fontPt = t.font_pt || 0
  } catch (e) {
    const nat = await imageSize(path)
    natW = nat.w; natH = nat.h
  }
  if (!natW || !natH) { const nat = await imageSize(p); natW = nat.w; natH = nat.h }

  const ratio = natH / Math.max(1, natW)
  // 默认按 1:1 原始字号插入（同一份 PDF 裁出来的图字号天然一致、清晰不失真）
  // 只有显式传 targetFont 时才按测得字号缩放
  const k = (opts.targetFont && fontPt > 0) ? target / fontPt : 1
  let w = (natW / pxPerPt) * k
  w = Math.round(clamp(w, 120, PW - MARGIN * 2))
  const h = Math.round(w * ratio)
  const b = reactive({ id: uid(), type: 'image', path: p, x: MARGIN, y: 0, w, h,
                       pxPerPt, fontPt })
  b.y = atY != null ? clamp(Math.round(atY - h / 2), 0, limit.value - h - 10) : Math.min(limit.value - h - 20, nextY())
  if (atX != null) {
    const cx = clamp(atX - w / 2, 0, Math.max(0, PW - w))
    if (autoAlign.value) {
      const cands = [MARGIN, Math.round((PW - w) / 2), Math.round(PW - MARGIN - w)]
      b.x = cands.reduce((a, c) => (Math.abs(c - cx) < Math.abs(a - cx) ? c : a), cands[0])
    } else b.x = Math.round(cx)
  }
  blocks.value.push(b)
  selectOnly(b.id)
  emit('changed')
  return b
}


async function unifyFonts() {
  const imgs = blocks.value.filter((b) => b.type === 'image')
  if (!imgs.length) return
  for (const b of imgs) {
    try {
      const t = await api.post('/api/manual/trim', {
        path: b.path, px_per_pt: b.pxPerPt || imgPxPerPt.value || 3, pad: 2,
      })
      if (t.path && t.path !== b.path) b.path = t.path
      const fp = t.font_pt || 0
      if (!fp) continue
      const k = fontTarget.value / fp
      b.w = Math.round(clamp(b.w * k, 120, PW - MARGIN * 2))
      b.h = Math.round(b.w * ((b.h || 1) / Math.max(1, b.w / k)))
      b.fontPt = fp
    } catch (e) { /* 忽略单块失败 */ }
  }
  // 按文字左边缘对齐：裁过白边后块左边即文字左边
  imgs.sort((a, b2) => a.y - b2.y)
  for (const b of imgs) b.x = MARGIN
  emit('changed')
  tip.value = `已把 ${imgs.length} 张图统一到 ${fontTarget.value}pt 并左对齐`
}

function applyColor() {
  const targets = blocks.value.filter((b) => b.type === 'text' && isSel(b.id))
  const list = targets.length ? targets : blocks.value.filter((b) => b.type === 'text')
  for (const b of list) b.color = textColor.value
  tip.value = targets.length
    ? `已把 ${targets.length} 个文字块设成 ${textColor.value}`
    : (blocks.value.some((b) => b.type === 'text') ? `已把全部文字块设成 ${textColor.value}` : '画布还没有文字块')
  emit('changed')
}

// ---------- 拖动 / 缩放 ----------
let drag = null
function startDrag(e, b) {
  emit('activate')
  if (editingId.value === b.id) return
  if (e.shiftKey) { toggleSel(b); return }
  if (!isSel(b.id)) selectGroupOf(b)
  else if (b.gid && !e.altKey) selectGroupOf(b)
  const moving = blocks.value.filter((x) => isSel(x.id)).map((x) => ({ id: x.id, x: x.x, y: x.y }))
  drag = { ids: moving.length ? moving : [{ id: b.id, x: b.x, y: b.y }], sx: e.clientX, sy: e.clientY, kind: 'move' }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', endDrag)
}
function startResize(e, b) {
  emit('activate')
  selectOnly(b.id)
  drag = { id: b.id, sx: e.clientX, sy: e.clientY, w: b.w, h: b.h, kind: 'size' }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', endDrag)
}
function onMove(e) {
  if (!drag) return
  const dx = (e.clientX - drag.sx) / scale.value
  const dy = (e.clientY - drag.sy) / scale.value
  if (drag.kind === 'move') {
    for (const it of drag.ids) {
      const t = blocks.value.find((x) => x.id === it.id)
      if (!t) continue
      t.x = clamp(Math.round(it.x + dx), 0, Math.max(0, PW - (t.w || 40)))
      t.y = clamp(Math.round(it.y + dy), 0, Math.max(0, limit.value - (t.h || 40)))
    }
    if (autoAlign.value) {
      const p = blocks.value.find((x) => x.id === drag.ids[0].id)
      if (p) {
        if (Math.abs(p.x - MARGIN) < 10) p.x = MARGIN
        if (Math.abs(p.y - Math.round(p.y / 4) * 4) < 3) p.y = Math.round(p.y / 4) * 4
      }
    }
  } else {
    const b = blocks.value.find((x) => x.id === drag.id)
    if (!b) return
    const nw = clamp(Math.round(drag.w + dx), 80, PW - b.x - 8)
    b.w = nw
    if (b.type === 'image') b.h = Math.round(nw * (drag.h / Math.max(1, drag.w)))
  }
  emit('changed')
}
function endDrag() {
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', endDrag)
  drag = null
}

// ---------- 缩放适配 ----------
const canvasEl = ref(null)
function fitScale() {
  const host = canvasEl.value ? canvasEl.value.parentElement : null
  const w = host ? host.clientWidth : 760
  scale.value = clamp((w - 48) / PW, 0.35, 1.15)
}
let ro = null
onMounted(() => { fitScale(); ro = new ResizeObserver(fitScale); if (canvasEl.value?.parentElement) ro.observe(canvasEl.value.parentElement) })
onBeforeUnmount(() => { if (ro) ro.disconnect() })

// ---------- 拖放（来自左栏裁剪） ----------
function dropOnCanvas(e) {
  if (e.dataTransfer.files && e.dataTransfer.files.length) return
  const p = e.dataTransfer.getData('text/plain')
  if (!p) return
  const host = e.currentTarget.getBoundingClientRect()
  addImage(p, (e.clientX - host.left) / scale.value, (e.clientY - host.top) / scale.value)
}

// ---------- 对外接口 ----------
function getLayout() {
  return {
    width: PW,
    height: Math.round(pageHeight.value),
    blocks: blocks.value.map((b) => (b.type === 'image'
      ? { type: 'image', path: b.path, x: b.x, y: b.y, w: b.w, h: b.h, rotate: b.rotate || 0 }
      : { type: 'text', text: b.text, x: b.x, y: b.y, w: b.w, size: b.size, align: b.align, bold: b.bold })),
    meta: props.metaable && hasMeta.value
      ? { header: { left: band.header_left, center: band.header_center, right: band.header_right },
          footer: { center: band.footer_center }, name_line: band.name_line }
      : {},
    trim: trimBlank.value,
    trim_pad: 24,
  }
}
function setLayout(layout) {
  const l = layout || {}
  blocks.value = (l.blocks || []).map((b) => reactive({ ...b, id: uid() }))
  const m = l.meta || {}
  const h = m.header || {}
  band.header_left = h.left || ''
  band.header_center = h.center || ''
  band.header_right = h.right || ''
  band.footer_center = (m.footer || {}).center || ''
  band.name_line = !!m.name_line
  clearSel()
  nextTick(fitScale)
}
function clear() { blocks.value = []; clearSel(); clip.value = [] }

defineExpose({ getLayout, setLayout, addImage, clear, rotateSel, copySel, pasteClip, groupSel, ungroupSel, removeSelected, clearSel, nudge, addText, unifyFonts })
</script>

<template>
  <section class="cvpane" :class="{ collapsed }">
    <header class="cvhead">
      <b>{{ title }}</b>
      <span class="muted">{{ blocks.length }} 个元素 · {{ Math.round(pageHeight) }}pt</span>
      <span class="spacer"></span>
      <label class="chk" v-if="!collapsed"><input type="checkbox" v-model="autoAlign" /> 自动对齐</label>
      <label class="chk" v-if="!collapsed" title="保存时按内容裁掉下方空白"><input type="checkbox" v-model="trimBlank" /> 裁空白</label>
      <span class="muted" v-if="!collapsed" style="margin-left:8px">字号</span>
      <input v-if="!collapsed" v-model.number="fontTarget" type="number" min="6" max="24" step="0.5"
             style="width:56px;padding:3px 6px" title="统一到多少 pt" />
      <span class="muted" v-if="!collapsed" style="margin-left:8px">颜色</span>
      <input v-if="!collapsed" v-model="textColor" type="color" class="colorin" title="文字颜色" />
      <button class="ghost sm" v-if="!collapsed" @click="applyColor" title="给选中的文字块上色（未选中则全部）">上色</button>
      <button class="ghost sm" v-if="!collapsed" @click="unifyFonts" title="按测得字号统一并左对齐">统一字号</button>
      <button class="icon" :title="collapsed ? '展开' : '收起'" @click="collapsed = !collapsed">{{ collapsed ? '▾' : '▴' }}</button>
    </header>
    <div v-if="tip && !collapsed" class="cvhint">{{ tip }}</div>

    <div v-show="!collapsed" class="cvbody" @pointerdown="emit('activate')">
      <div class="toolbar">
        <div class="tgroup">
          <span class="lbl">字号</span>
          <button class="icon" @click="bumpSize(-1)">A－</button>
          <b class="size">{{ selectedText ? selectedText.size : 13 }}</b>
          <button class="icon" @click="bumpSize(1)">A＋</button>
        </div>
        <span class="tdiv"></span>
        <div class="tgroup">
          <button class="icon" :class="{ on: selectedText && selectedText.bold }" @click="toggleBold"><b>B</b></button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'left' }" @click="setAlign('left')">⇤</button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'center' }" @click="setAlign('center')">↔</button>
          <button class="icon" :class="{ on: selectedText && selectedText.align === 'right' }" @click="setAlign('right')">⇥</button>
        </div>
        <span class="tdiv"></span>
        <button class="ghost" @click="addText('')">＋ 文本</button>
        <button v-if="metaable" class="ghost" :class="{ on: metaOpen }" @click="metaOpen = !metaOpen">页眉页脚</button>
        <button class="ghost" :disabled="!selected" @click="rotateSel">旋转 90°</button>
        <button class="ghost" :disabled="!selIds.length" @click="copySel">复制</button>
        <button class="ghost" :disabled="!clip.length" @click="pasteClip">粘贴</button>
        <button class="ghost" :disabled="selIds.length < 2" @click="groupSel">组合</button>
        <button class="ghost" :disabled="!selIds.length" @click="ungroupSel">拆分</button>
        <button class="ghost" :disabled="!selIds.length" @click="removeSelected">
          删除所选{{ selIds.length > 1 ? `(${selIds.length})` : '' }}
        </button>
      </div>

      <div v-if="metaable && metaOpen" class="metabox">
        <div class="bandrow">
          <input v-model="band.header_left" placeholder="页眉左（可空）" />
          <input v-model="band.header_center" placeholder="页眉中，如：XX 大学 2026 年秋季期中考试" />
          <input v-model="band.header_right" placeholder="页眉右，如：A 卷" />
        </div>
        <div class="bandrow">
          <input v-model="band.footer_center" placeholder="页脚中，如：第 1 页 共 1 页" />
          <label class="chk"><input v-model="band.name_line" type="checkbox" /> 加「姓名 / 班级 / 得分」栏</label>
        </div>
      </div>

      <div class="canvas-wrap">
        <div ref="canvasEl" class="page"
             :style="{ width: PW * scale + 'px', height: pageHeight * scale + 'px' }"
             @dragover.prevent @drop.prevent="dropOnCanvas" @pointerdown.self="clearSel">
          <div v-if="!blocks.length" class="empty">{{ hint }}</div>
          <div v-for="b in blocks" :key="b.id" class="blk"
               :class="{ sel: isSel(b.id), img: b.type === 'image', grouped: !!b.gid }"
               :data-gid="b.gid || ''"
               :style="{ left: b.x * scale + 'px', top: b.y * scale + 'px', width: b.w * scale + 'px',
                         height: b.type === 'image' ? (b.h || 0) * scale + 'px' : 'auto' }"
               @pointerdown="startDrag($event, b)"
               @dblclick="b.type === 'text' ? (editingId = b.id) : null">
            <template v-if="b.type === 'image'">
              <img :src="imgUrl(b.path)" draggable="false" @dragstart.prevent />
              <span class="handle" @pointerdown.stop="startResize($event, b)"></span>
            </template>
            <template v-else>
              <textarea v-if="editingId === b.id" v-model="b.text" class="etext" autofocus
                        :style="{ fontSize: b.size * scale + 'px', textAlign: b.align, fontWeight: b.bold ? 700 : 400, color: b.color || undefined }"
                        @pointerdown.stop @blur="editingId = ''"></textarea>
              <div v-else class="tt"
                   :style="{ fontSize: b.size * scale + 'px', textAlign: b.align, fontWeight: b.bold ? 700 : 400, color: b.color || undefined }"
                   v-html="renderMath(b.text) || '<span class=\'ph\'>（双击输入文字）</span>'"></div>
              <span class="handle text" @pointerdown.stop="startResize($event, b)"></span>
            </template>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ghost.sm { padding: 3px 9px; font-size: 12.5px; }
.colorin {
  width: 30px; height: 24px; padding: 0; border: 1px solid var(--line); border-radius: 6px; background: #fff;
}

.cvhint {
  padding: 4px 12px 6px; font-size: 12.5px; color: #3b7a57; background: #f2faf5;
}

.cvpane { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; margin-bottom: 12px; }
.cvhead { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid #eef1f6; }
.cvhead b { font-size: 14px; }
.cvpane.collapsed .cvhead { border-bottom: none; }
.cvbody { padding: 10px 12px 12px; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding-bottom: 8px; }
.tgroup { display: flex; align-items: center; gap: 4px; }
.tgroup .lbl { font-size: 12px; color: #667085; }
.tgroup .size { min-width: 22px; text-align: center; font-size: 12.5px; }
.tdiv { width: 1px; height: 18px; background: #e7ebf2; }
.icon { padding: 4px 8px; border: 1px solid #dfe5ee; background: #fff; border-radius: 7px; cursor: pointer; font-size: 12.5px; }
.icon.on { background: #eef4ff; border-color: #b9cdf7; color: var(--brand, #2f6fed); }
.spacer { flex: 1; }
.chk { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: #475467; }
.muted { font-size: 12px; color: #8b95a5; }
.metabox { background: #f8fafc; border: 1px solid var(--line, #e7ebf2); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
.bandrow { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 6px; }
.bandrow input { flex: 1 1 180px; min-width: 140px; }
.canvas-wrap { background: #f1f3f7; border-radius: 10px; padding: 14px; display: flex; justify-content: center; max-height: calc(100vh - 260px); overflow: auto; }
.page { position: relative; background: #fff; box-shadow: 0 6px 24px rgba(16, 24, 40, .1); flex: 0 0 auto; }
.page .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #98a2b3; font-size: 13px; text-align: center; padding: 0 40px; }
.blk { position: absolute; cursor: move; }
.blk.sel { outline: 1px dashed var(--brand, #2f6fed); outline-offset: 1px; }
.blk.grouped { outline: 1px dashed rgba(47, 111, 237, .45); outline-offset: 3px; }
.blk.img img { display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; }
.blk .tt { white-space: pre-wrap; line-height: 1.55; color: #101828; min-height: 18px; }
.blk :deep(.ph) { color: #b6bdc9; }
.blk .etext { width: 100%; border: 1px solid var(--brand, #2f6fed); border-radius: 4px; padding: 2px 4px; line-height: 1.55; resize: vertical; font-family: inherit; }
.handle { position: absolute; right: -5px; bottom: -5px; width: 10px; height: 10px; background: #fff; border: 1px solid var(--brand, #2f6fed); border-radius: 2px; cursor: nwse-resize; }
.handle.text { bottom: -6px; }
</style>
