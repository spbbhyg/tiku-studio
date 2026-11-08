<script setup>
/**
 * 悬浮的「结构式 / 公式」导入面板（出题·单题模式 与 出整卷 共用）
 *
 * 右下角常驻两个小按钮，点开是一张浮层：
 *  - 公式：手写 LaTeX 或点常用符号 → 实时 KaTeX 预览 → 插入到光标处（`$...$`）
 *  - 结构式：填 SMILES（或直接粘贴会图）→ 服务端 RDKit 出 SVG → 转 PNG 入库 → 插入为图
 *
 * 插入位置由外层决定：emit('text', '$…$') / emit('image', 'chem/xxx.png')。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, nextTick } from 'vue'
import katex from 'katex'
import { api } from '../api'

const emit = defineEmits(['text', 'image'])

/* 插图宽度档位（pt）：按第 40 届样卷实测，正文级结构图 P25=86 / P50=130，单张结构取中档 110 最像 */
const WSIZES = [{ k: 80, t: '小' }, { k: 110, t: '中' }, { k: 160, t: '大' }]
const wsize = ref(Number(localStorage.getItem('tiku:chem-w')) || 110)
function setW(v) { wsize.value = v; localStorage.setItem('tiku:chem-w', String(v)) }

const open = ref('')          // '' | 'formula' | 'struct' | 'draw'
const fx = reactive({ latex: '', err: '' })
const ch = reactive({ smiles: '', svg: '', err: '', busy: false, sized: '' })

/* ---- 手绘（Ketcher，iframe + postMessage） ---- */
const kframe = ref(null)
const kReady = ref(false)
const kMounted = ref(false)   // 30MB 的包，只有第一次点「手绘」才挂 iframe
const kMsg = ref('')
let kseq = 0
const kpending = new Map()

function kpost(payload, timeout = 20000) {
  return new Promise((resolve) => {
    const id = 'k' + (++kseq)
    kpending.set(id, resolve)
    const win = kframe.value && kframe.value.contentWindow
    if (!win) return resolve({ ok: false, error: '画板未就绪' })
    win.postMessage({ ...payload, id }, '*')
    setTimeout(() => {
      if (kpending.has(id)) { kpending.delete(id); resolve({ ok: false, error: '超时' }) }
    }, timeout)
  })
}
async function kping() {
  kReady.value = false
  for (let i = 0; i < 10 && !kReady.value; i++) {
    await new Promise((r) => setTimeout(r, i === 0 ? 500 : 1200))
    const r = await kpost({ type: 'ketcher:ping' }, 3000)
    if (r && r.ok) { kReady.value = true; break }
  }
  if (!kReady.value) kMsg.value = '画板还在加载，稍等一下再点'
}
async function kopenDraw() {
  kMounted.value = true
  await nextTick()
  if (!kReady.value) kping()
}
async function ksetFromSmiles() {
  const v = ch.smiles.trim()
  if (!v) { kMsg.value = '先在上面的「结构式」里填 SMILES，或在画板上直接画'; return }
  const r = await kpost({ type: 'ketcher:set', molfile: v })
  kMsg.value = r.ok ? '已载入画板，可继续增删原子' : ('载入失败：' + (r.error || ''))
}
async function ktake() {
  const r = await kpost({ type: 'ketcher:get' })
  if (!r.ok || !r.molfile) { kMsg.value = '画板里还没有结构'; return }
  kMsg.value = '正在生成图…'
  try {
    const res = await api.post('/api/v2/chem', {
      kind: 'structure', molfile: r.molfile, svg: r.svg || '', renderer: 'ketcher',
    })
    if (!res || !res.id) throw new Error('没有拿到结构对象')
    const png = await api.post(`/api/v2/chem/${res.id}/png`, {})
    const rel = String(png.url || '').replace(/^\/api\/images\//, '')
    if (!rel) throw new Error('没有拿到图片路径')
    emit('image', rel, wsize.value)
    kMsg.value = '已插入'
  } catch (e) {
    kMsg.value = '出图失败：' + e.message
  }
}
function onMessage(ev) {
  const d = ev.data || {}
  if (d.type === 'ketcher:ready') { kReady.value = true; return }
  if (d.type === 'ketcher:result' && d.id && kpending.has(d.id)) {
    kpending.get(d.id)(d)
    kpending.delete(d.id)
  }
}
onMounted(() => window.addEventListener('message', onMessage))
onBeforeUnmount(() => window.removeEventListener('message', onMessage))

const QUICK = [
  { t: 'x₂', i: '_{2}' }, { t: 'x²', i: '^{2}' }, { t: 'xⁿ', i: '^{n}' },
  { t: 'a/b', i: '\\frac{a}{b}' }, { t: '√', i: '\\sqrt{x}' },
  { t: 'Δ', i: '\\Delta ' }, { t: '→', i: '\\rightarrow ' }, { t: '⇌', i: '\\rightleftharpoons ' },
  { t: '↑', i: '\\uparrow ' }, { t: '↓', i: '\\downarrow ' }, { t: '°', i: '^{\\circ}' },
  { t: 'α', i: '\\alpha ' }, { t: 'β', i: '\\beta ' }, { t: 'γ', i: '\\gamma ' },
  { t: 'Σ', i: '\\sum ' }, { t: '∫', i: '\\int ' }, { t: '±', i: '\\pm ' }, { t: '×', i: '\\times ' },
]

const preview = computed(() => {
  const src = fx.latex.trim()
  if (!src) return ''
  try {
    return katex.renderToString(src, { throwOnError: false, displayMode: false })
  } catch (e) {
    return ''
  }
})

function tab(k) {
  open.value = open.value === k ? '' : k
  if (open.value === 'draw') kopenDraw()
}
function put(seg) {
  fx.latex += seg
}
function insertFormula() {
  const src = fx.latex.trim()
  if (!src) { fx.err = '先写公式'; return }
  fx.err = ''
  emit('text', `$${src}$`)
}
async function previewStruct() {
  ch.err = ''
  ch.svg = ''
  ch.sized = ''
  const v = ch.smiles.trim()
  if (!v) return
  ch.busy = true
  try {
    const r = await api.post('/api/v2/chem/preview', { source: v, width: 360, height: 240 })
    ch.svg = String(r.svg || '').replace(/^<\?xml[^>]*\?>\s*/i, '')
    if (!ch.svg) ch.err = '这个 SMILES 没画出来，检查一下写法'
    else ch.sized = r.smiles || ''
  } catch (e) {
    ch.err = '渲染失败：' + e.message
  } finally {
    ch.busy = false
  }
}
async function insertStruct() {
  const v = ch.smiles.trim()
  if (!v) { ch.err = '先填 SMILES'; return }
  ch.busy = true
  ch.err = ''
  try {
    const obj = await api.post('/api/v2/chem', { kind: 'structure', smiles: v })
    if (!obj || !obj.id) throw new Error('没有拿到结构对象')
    const png = await api.post(`/api/v2/chem/${obj.id}/png`, {})
    const rel = String(png.url || '').replace(/^\/api\/images\//, '')
    if (!rel) throw new Error('没有拿到图片路径')
    emit('image', rel, wsize.value)
  } catch (e) {
    ch.err = '插入失败：' + e.message
  } finally {
    ch.busy = false
  }
}
</script>

<template>
  <div class="dock">
    <div v-if="open" class="card" :class="{ wide: open === 'draw' }">
      <div class="head">
        <b>{{ open === 'formula' ? '公式' : (open === 'struct' ? '结构式' : '手绘结构式') }}</b>
        <span class="muted">{{ open === 'formula' ? '写 LaTeX，或点常用符号' : (open === 'struct' ? '填 SMILES，回车预览' : '在画板上画，画完取结构出图') }}</span>
        <button class="x" @click="open = ''">×</button>
      </div>

      <template v-if="open === 'formula'">
        <div class="keys">
          <button v-for="(k, i) in QUICK" :key="i" class="key" :title="k.i" @click="put(k.i)">{{ k.t }}</button>
        </div>
        <textarea v-model="fx.latex" class="ta" rows="2" spellcheck="false"
                  placeholder="例如 K_{sp}=\frac{c^2}{1-\alpha}，或 \ce{2H2 + O2 -> 2H2O}"
                  @keydown.ctrl.enter.prevent="insertFormula"
                  @keydown.meta.enter.prevent="insertFormula"></textarea>
        <div class="pv">
          <span v-if="preview" v-html="preview"></span>
          <span v-else class="muted">预览</span>
        </div>
        <div v-if="fx.err" class="err">{{ fx.err }}</div>
        <div class="acts">
          <button class="primary" @click="insertFormula">插入到光标</button>
          <button class="ghost" @click="fx.latex = ''">清空</button>
          <span class="muted">Ctrl+Enter</span>
        </div>
      </template>

      <template v-else-if="open === 'struct'">
        <textarea v-model="ch.smiles" class="ta" rows="2" spellcheck="false"
                  placeholder="例如 CC(=O)Oc1ccccc1C(=O)O（阿司匹林）"
                  @keydown.enter.prevent="previewStruct"></textarea>
        <div class="pv struct">
          <span v-if="ch.svg" v-html="ch.svg" class="svgbox"></span>
          <span v-else class="muted">填 SMILES 后回车预览</span>
        </div>
        <div v-if="ch.err" class="err">{{ ch.err }}</div>
        <div class="acts">
          <button :disabled="ch.busy" @click="previewStruct">预览</button>
          <button class="primary" :disabled="ch.busy" @click="insertStruct">插入到光标</button>
          <span class="muted">{{ ch.busy ? '处理中…' : (ch.sized ? '已识别：' + ch.sized : '') }}</span>
        </div>
      </template>

      <template v-else>
        <div class="kbar">
          <button :disabled="!kReady" @click="ksetFromSmiles">把 SMILES 载入画板</button>
          <button class="primary" :disabled="!kReady" @click="ktake">取结构出图并插入</button>
          <span class="muted">{{ kReady ? '画完后点右边那个' : '画板加载中…' }}</span>
        </div>
        <iframe v-if="kMounted" ref="kframe" class="kframe" src="/ketcher/index.html"
                title="结构式画板" @load="kping"></iframe>
        <div v-if="kMsg" class="muted kmsg">{{ kMsg }}</div>
      </template>
    </div>

    <div class="sizer" v-if="open && open !== 'formula'">
      <span class="muted">插入宽度</span>
      <button v-for="w in WSIZES" :key="w.k" :class="{ on: wsize === w.k }" @click="setW(w.k)">
        {{ w.t }} {{ w.k }}pt
      </button>
    </div>

    <div class="bar">
      <button :class="{ on: open === 'formula' }" title="插入公式" @click="tab('formula')">ƒx 公式</button>
      <button :class="{ on: open === 'struct' }" title="插入结构式" @click="tab('struct')">⌬ 结构式</button>
      <button :class="{ on: open === 'draw' }" title="手绘结构式" @click="tab('draw')">✏ 手绘</button>
    </div>
  </div>
</template>

<style scoped>
.dock { position: fixed; right: 18px; bottom: 18px; z-index: 50; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.bar { display: flex; gap: 6px; }
.bar button {
  height: 32px; padding: 0 12px; border-radius: 999px; border: 1px solid #d1d5db;
  background: #fff; font-size: 13px; cursor: pointer; box-shadow: 0 4px 14px rgba(16, 24, 40, .12);
}
.bar button.on, .bar button:hover { border-color: #2563eb; color: #2563eb; }
.card {
  width: 360px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
  box-shadow: 0 12px 32px rgba(16, 24, 40, .18); padding: 10px 12px;
}
.head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.head .x { margin-left: auto; border: 0; background: transparent; font-size: 16px; color: #9ca3af; cursor: pointer; }
.muted { color: #9ca3af; font-size: 12px; }
.keys { display: grid; grid-template-columns: repeat(auto-fill, minmax(40px, 1fr)); gap: 4px; margin-bottom: 8px; }
.key { height: 26px; border: 1px solid #e5e7eb; border-radius: 6px; background: #fff; font-size: 12.5px; cursor: pointer; }
.key:hover { border-color: #2563eb; color: #2563eb; }
.ta { width: 100%; box-sizing: border-box; border: 1px solid #d1d5db; border-radius: 8px; padding: 7px 9px;
  font-family: Consolas, Menlo, monospace; font-size: 12.5px; resize: vertical; }
.pv { min-height: 40px; margin: 8px 0; padding: 8px; background: #fbfcff; border: 1px dashed #dfe4ee;
  border-radius: 8px; overflow-x: auto; font-size: 14px; }
.pv.struct { display: flex; align-items: center; justify-content: center; }
.svgbox :deep(svg) { max-width: 100%; height: auto; }
.err { color: #b91c1c; font-size: 12px; margin-bottom: 6px; }
.acts { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.acts button { height: 28px; padding: 0 10px; border-radius: 6px; border: 1px solid #d1d5db; background: #fff; font-size: 12.5px; cursor: pointer; }
.acts button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
.acts button.ghost { color: #6b7280; }
.card.wide { width: 820px; }
.sizer { display: flex; align-items: center; gap: 6px; margin: 0 0 6px 8px; font-size: 12.5px; }
.sizer button { height: 24px; padding: 0 8px; border-radius: 6px; border: 1px solid #d1d5db; background: #fff; color: #374151; font-size: 12px; cursor: pointer; }
.sizer button.on { border-color: #2563eb; color: #1d4ed8; background: #eff6ff; }
.kbar { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.kbar button { height: 28px; padding: 0 10px; border-radius: 6px; border: 1px solid #d1d5db; background: #fff; font-size: 12.5px; cursor: pointer; }
.kbar button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
.kbar button:disabled { opacity: .55; cursor: default; }
.kframe { width: 100%; height: 430px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; }
.kmsg { margin-top: 6px; }
</style>
