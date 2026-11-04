<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../api.js'

const emit = defineEmits(['insert', 'close'])

const mode = ref('smiles')          // smiles | draw
const src = ref('')
const svg = ref('')
const canon = ref('')
const err = ref('')
const busy = ref(false)

// ---- SMILES/MOL 模式 ----
let timer = null
watch(src, () => {
  clearTimeout(timer)
  timer = setTimeout(preview, 500)
})

async function preview() {
  const s = (src.value || '').trim()
  if (!s) { svg.value = ''; canon.value = ''; return }
  try {
    const r = await api.post('/api/v2/chem/preview', { source: s })
    svg.value = String(r.svg || '').replace(/^<\?xml[^>]*\?>\s*/i, '')
    canon.value = r.smiles || ''
    err.value = ''
  } catch (e) { err.value = '识别失败：' + e.message }
}

async function insertFromSmiles() {
  const s = (src.value || '').trim()
  if (!s) { err.value = '先贴 SMILES 或 MOL'; return }
  busy.value = true
  try {
    const r = await api.post('/api/v2/chem', { kind: 'structure', smiles: s })
    emit('insert', { chemId: r.id, svg: r.svg || svg.value, molfile: '', smiles: canon.value || s })
  } catch (e) { err.value = '插入失败：' + e.message } finally { busy.value = false }
}

// ---- Ketcher 手绘模式（iframe + postMessage）----
const frame = ref(null)
const ready = ref(false)
const drawErr = ref('')
let seq = 0
const pending = new Map()

function onMessage(ev) {
  const d = ev.data || {}
  if (d.type === 'ketcher:ready') { ready.value = true; return }
  if (d.type === 'ketcher:result' && pending.has(d.id)) {
    const { resolve } = pending.get(d.id)
    pending.delete(d.id)
    resolve(d)
  }
}

function callKetcher(payload, timeout = 20000) {
  return new Promise((resolve) => {
    const w = frame.value && frame.value.contentWindow
    if (!w) { resolve({ ok: false, error: '画板还没加载' }); return }
    const id = ++seq
    pending.set(id, { resolve })
    w.postMessage({ ...payload, id }, '*')
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); resolve({ ok: false, error: '画板超时' }) }
    }, timeout)
  })
}

async function insertFromDraw() {
  busy.value = true; drawErr.value = ''
  try {
    const get = await callKetcher({ type: 'ketcher:get' })
    if (!get.ok) { drawErr.value = get.error || '取结构失败'; return }
    if (get.empty) { drawErr.value = '画板是空的，先画一个结构'; return }
    const r = await api.post('/api/v2/chem', {
      kind: 'structure', molfile: get.molfile, svg: get.svg, renderer: 'ketcher',
    })
    emit('insert', { chemId: r.id, svg: r.svg || get.svg, molfile: get.molfile, smiles: '' })
  } catch (e) { drawErr.value = '插入失败：' + e.message } finally { busy.value = false }
}

async function loadIntoDraw() {
  const s = (src.value || '').trim()
  if (!s) { drawErr.value = '先在上一个标签填 SMILES/MOL，或直接画'; return }
  const r = await callKetcher({ type: 'ketcher:set', molfile: s })
  if (!r.ok) drawErr.value = r.error || '载入失败'
}

onMounted(() => window.addEventListener('message', onMessage))
onBeforeUnmount(() => { window.removeEventListener('message', onMessage); pending.clear() })
</script>

<template>
  <div class="chempanel">
    <div class="chead">
      <b>结构式</b>
      <div class="mtabs">
        <button class="mtab" :class="{ on: mode === 'smiles' }" @click="mode = 'smiles'">SMILES / MOL</button>
        <button class="mtab" :class="{ on: mode === 'draw' }" @click="mode = 'draw'">手绘（Ketcher）</button>
      </div>
      <span class="spacer" />
      <button class="ghost sm" @click="$emit('close')">×</button>
    </div>

    <template v-if="mode === 'smiles'">
      <textarea id="chem-src" v-model="src" class="csrc" spellcheck="false"
                placeholder="粘 SMILES，如 c1ccccc1C(=O)O；也可以粘 MOL(V2000/V3000)" />
      <div class="crow">
        <button class="ghost sm" :disabled="busy" @click="preview">重新渲染</button>
        <button class="primary sm" :disabled="busy" @click="insertFromSmiles">插入到正文</button>
        <span v-if="canon" class="muted">已识别：{{ canon }}</span>
      </div>
      <div class="cprev" v-html="svg" />
      <div v-if="err" class="errbox">{{ err }}</div>
    </template>

    <template v-else>
      <div class="drawbox">
        <iframe ref="frame" src="/ketcher/index.html?theme=light" class="kframe" />
      </div>
      <div class="crow">
        <button class="ghost sm" @click="loadIntoDraw">把左侧 SMILES 载入画板</button>
        <button class="primary sm" :disabled="busy || !ready" @click="insertFromDraw">
          {{ ready ? (busy ? '取结构中…' : '插入到正文') : '画板加载中…' }}
        </button>
        <span class="muted">画完点插入；服务端会自动 Clean Up 出图</span>
      </div>
      <div v-if="drawErr" class="errbox">{{ drawErr }}</div>
    </template>
  </div>
</template>

<style scoped>
.chempanel { position: fixed; right: 24px; bottom: 24px; z-index: 60; width: min(760px, 52vw); max-height: min(680px, 88vh);
  background: #fff; border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 32px rgba(16,24,40,.18);
  display: flex; flex-direction: column; overflow: hidden; }
.chead { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.mtabs { display: flex; gap: 4px; }
.mtab { border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.mtab.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.spacer { flex: 1; }
.csrc { margin: 10px 12px 0; min-height: 96px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12.5px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; resize: vertical; }
.crow { display: flex; align-items: center; gap: 8px; padding: 10px 12px; flex-wrap: wrap; }
.cprev { flex: 1; min-height: 160px; max-height: 340px; overflow: auto; padding: 0 12px 12px; }
.cprev :deep(svg) { max-width: 100%; height: auto; }
.drawbox { padding: 10px 12px 0; }
.kframe { width: 100%; height: min(460px, 56vh); border: 1px solid var(--line); border-radius: 10px; background: #fff; }
.primary.sm, .ghost.sm { padding: 4px 10px; font-size: 12.5px; }
</style>
