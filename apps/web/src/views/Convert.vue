<script setup>
// 格式转换：文档互转（docx↔pdf）、PDF 拆分 / 合并、扫描件 OCR 转 Word。
// 全部在本机转，文件不出这台电脑。出错一律给中文原因（后端也是中文）。
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'

const engines = ref({})
const tab = ref('doc')          // doc | split | merge | ocr | dmerge | dsplit

// Word 合并
const wmFiles = ref([])          // {id, file, name, size}
const wmBusy = ref(false)
const wmBlob = ref(null)
const wmMsg = ref('')
// Word 拆分
const wsFile = ref(null)
const wsMode = ref('question')   // question | count
const wsCount = ref(2)
const wsBusy = ref(false)
const wsBlob = ref(null)
const wsMsg = ref('')
const wsName = ref('')
const dragOver = ref('')

// ---------- 文档互转队列 ----------
const jobs = ref([])            // {id, name, dir, state, msg, blob, outName, size}
let seq = 1

// ---------- PDF 拆分 ----------
const splitFile = ref(null)
const splitPages = ref('')
const splitBusy = ref(false)
const splitMsg = ref('')
const splitBlob = ref(null)
const splitName = ref('')

// ---------- PDF 合并 ----------
const mergeFiles = ref([])      // {id, file, name, size}
const mergeBusy = ref(false)
const mergeMsg = ref('')
const mergeBlob = ref(null)

// ---------- 扫描件 OCR ----------
const ocrFile = ref(null)
const ocrBusy = ref(false)
const ocrMsg = ref('')
const ocrBlob = ref(null)
const ocrName = ref('')

async function loadEngines() {
  try { engines.value = await api.get('/api/convert/engines') } catch { engines.value = {} }
}
onMounted(loadEngines)

function pickFile(accept, multiple, cb) {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = accept
  input.multiple = multiple
  input.onchange = () => cb(Array.from(input.files || []))
  input.click()
}

function errText(e) {
  const s = e && e.message ? e.message : String(e)
  if (/Failed to fetch|NetworkError|Load failed/i.test(s)) return '连不上本地服务：先把软件开着（或点设置里的「重启内置服务」）再试'
  if (/413/.test(s)) return '文件太大了：先拆成几份再转'
  return s
}

function kb(n) { return n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB' }

function saveBlob(blob, name) {
  if (!blob) return
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
}

// ---------------- 文档互转 ----------------
function addDocFiles(dir, files) {
  files.forEach((f) => {
    jobs.value.push({ id: seq++, name: f.name, dir, file: f, state: 'wait', msg: '', blob: null, size: 0 })
  })
  runJobs()
}

function onDocDrop(e, dir) {
  dragOver.value = ''
  const files = Array.from(e.dataTransfer?.files || [])
  if (!files.length) return
  const want = dir === 'docx2pdf' ? /\.docx$/i : /\.pdf$/i
  const ok = files.filter((f) => want.test(f.name))
  const bad = files.length - ok.length
  if (ok.length) addDocFiles(dir, ok)
  if (bad) {
    jobs.value.push({
      id: seq++, name: `${bad} 个文件类型不对`, dir, state: 'bad',
      msg: dir === 'docx2pdf' ? '这里只收 .docx（.doc 请先在 Word 里另存为 .docx）' : '这里只收 .pdf',
      blob: null, size: 0,
    })
  }
}

let running = false
async function runJobs() {
  if (running) return
  running = true
  try {
    for (;;) {
      const j = jobs.value.find((x) => x.state === 'wait')
      if (!j) break
      j.state = 'busy'
      j.msg = '正在转…'
      try {
        const fd = new FormData()
        fd.append('file', j.file)
        const resp = await fetch(`/api/convert/${j.dir}`, { method: 'POST', body: fd })
        if (!resp.ok) {
          let d = await resp.text()
          try { d = JSON.parse(d).detail || d } catch { /* 原样 */ }
          throw new Error(d)
        }
        const blob = await resp.blob()
        const eng = resp.headers.get('x-engine') || ''
        const cd = resp.headers.get('content-disposition') || ''
        const m = /filename\*=UTF-8''([^;]+)/.exec(cd)
        j.blob = blob
        j.size = blob.size
        j.outName = m ? decodeURIComponent(m[1])
          : j.name.replace(/\.[^.]+$/, '') + (j.dir === 'docx2pdf' ? '.pdf' : '.docx')
        j.state = 'done'
        j.msg = eng === 'office' ? '用本机 WPS / Office 转的（版式和原文件一致）'
          : eng === 'soffice' ? '用 LibreOffice 转的（版式最准）'
            : eng === 'builtin-chromium' ? '用内置渲染转的（版式近似；装了 WPS / LibreOffice 会更准）'
              : eng.startsWith('rapidocr') ? '扫描件按图认字重建（文字可编辑，图整页贴回）'
                : eng === 'pymupdf-layout' ? '按原 PDF 的文字与图片重建（双栏已拆开）'
                  : eng === 'pymupdf-rebuild' ? '按原 PDF 的文字与图片重建（版式近似）' : ''
      } catch (e) {
        j.state = 'bad'
        j.msg = errText(e)
      }
    }
  } finally { running = false }
}

function clearDone() { jobs.value = jobs.value.filter((j) => j.state !== 'done' && j.state !== 'bad') }

// ---------------- PDF 拆分 ----------------
function takeSplit(files) {
  const f = files[0]
  if (!f) return
  if (!/\.pdf$/i.test(f.name)) { splitMsg.value = '这里只收 .pdf'; return }
  splitFile.value = f
  splitMsg.value = ''
  splitBlob.value = null
}

async function doSplit() {
  if (!splitFile.value) return
  splitBusy.value = true
  splitMsg.value = ''
  splitBlob.value = null
  try {
    const fd = new FormData()
    fd.append('file', splitFile.value)
    const q = splitPages.value.trim()
    const resp = await fetch(`/api/convert/pdf-split?pages=${encodeURIComponent(q || 'each')}`, { method: 'POST', body: fd })
    if (!resp.ok) {
      let d = await resp.text()
      try { d = JSON.parse(d).detail || d } catch { /* 原样 */ }
      throw new Error(d)
    }
    splitBlob.value = await resp.blob()
    const n = resp.headers.get('x-parts') || ''
    splitName.value = splitFile.value.name.replace(/\.pdf$/i, '') + '-拆分.zip'
    splitMsg.value = `拆好了：${n} 个 PDF，打包成一个 zip（${kb(splitBlob.value.size)}）`
  } catch (e) {
    splitMsg.value = '拆分失败：' + errText(e)
  } finally { splitBusy.value = false }
}

// ---------------- PDF 合并 ----------------
function takeMerge(files) {
  const ok = files.filter((f) => /\.pdf$/i.test(f.name))
  const bad = files.length - ok.length
  ok.forEach((f) => mergeFiles.value.push({ id: seq++, file: f, name: f.name, size: f.size }))
  mergeMsg.value = bad ? `${bad} 个不是 PDF，已跳过` : ''
  mergeBlob.value = null
}

function moveMerge(i, d) {
  const j = i + d
  if (j < 0 || j >= mergeFiles.value.length) return
  const [x] = mergeFiles.value.splice(i, 1)
  mergeFiles.value.splice(j, 0, x)
}

async function doMerge() {
  if (mergeFiles.value.length < 2) { mergeMsg.value = '至少要两个 PDF 才能合并'; return }
  mergeBusy.value = true
  mergeMsg.value = ''
  mergeBlob.value = null
  try {
    const fd = new FormData()
    mergeFiles.value.forEach((m) => fd.append('files', m.file))
    const resp = await fetch('/api/convert/pdf-merge', { method: 'POST', body: fd })
    if (!resp.ok) {
      let d = await resp.text()
      try { d = JSON.parse(d).detail || d } catch { /* 原样 */ }
      throw new Error(d)
    }
    mergeBlob.value = await resp.blob()
    mergeMsg.value = `合并好了：${mergeFiles.value.length} 份 → 1 个 PDF（${kb(mergeBlob.value.size)}）`
  } catch (e) {
    mergeMsg.value = '合并失败：' + errText(e)
  } finally { mergeBusy.value = false }
}

// ---------------- Word 合并 ----------------
function takeWordMerge(files) {
  const ok = files.filter((f) => /\.docx?$/i.test(f.name))
  const bad = files.length - ok.length
  ok.forEach((f) => wmFiles.value.push({ id: seq++, file: f, name: f.name, size: f.size }))
  wmMsg.value = bad ? `${bad} 个不是 Word，已跳过（.doc 请先另存为 .docx）` : ''
  wmBlob.value = null
}
function moveWordMerge(i, d) {
  const j = i + d
  if (j < 0 || j >= wmFiles.value.length) return
  const [x] = wmFiles.value.splice(i, 1)
  wmFiles.value.splice(j, 0, x)
}
async function doWordMerge() {
  if (wmFiles.value.length < 2) { wmMsg.value = '至少要两份 Word 才能合并'; return }
  wmBusy.value = true
  wmMsg.value = ''
  wmBlob.value = null
  try {
    const fd = new FormData()
    wmFiles.value.forEach((m) => fd.append('files', m.file))
    const resp = await fetch('/api/convert/docx-merge', { method: 'POST', body: fd })
    if (!resp.ok) {
      let d = await resp.text()
      try { d = JSON.parse(d).detail || d } catch { /* 原样 */ }
      throw new Error(d)
    }
    wmBlob.value = await resp.blob()
    wmMsg.value = `合并好了：${wmFiles.value.length} 份 → 1 个 Word（${kb(wmBlob.value.size)}），每份之间自动分页`
  } catch (e) {
    wmMsg.value = '合并失败：' + errText(e)
  } finally { wmBusy.value = false }
}

// ---------------- Word 拆分 ----------------
function takeWordSplit(files) {
  const f = files[0]
  if (!f) return
  if (!/\.docx?$/i.test(f.name)) { wsMsg.value = '这里只收 .docx'; return }
  wsFile.value = f
  wsMsg.value = ''
  wsBlob.value = null
}
async function doWordSplit() {
  if (!wsFile.value) return
  wsBusy.value = true
  wsMsg.value = ''
  wsBlob.value = null
  try {
    const fd = new FormData()
    fd.append('file', wsFile.value)
    fd.append('mode', wsMode.value)
    fd.append('n', String(wsCount.value || 2))
    const resp = await fetch('/api/convert/docx-split', { method: 'POST', body: fd })
    if (!resp.ok) {
      let d = await resp.text()
      try { d = JSON.parse(d).detail || d } catch { /* 原样 */ }
      throw new Error(d)
    }
    wsBlob.value = await resp.blob()
    const n = resp.headers.get('x-parts') || ''
    wsName.value = wsFile.value.name.replace(/\.docx?$/i, '') + '-拆分.zip'
    wsMsg.value = `拆好了：${n} 份 Word，打包成一个 zip（${kb(wsBlob.value.size)}）`
  } catch (e) {
    wsMsg.value = '拆分失败：' + errText(e)
  } finally { wsBusy.value = false }
}

// ---------------- 扫描件 OCR ----------------
function takeOcr(files) {
  const f = files[0]
  if (!f) return
  if (!/\.pdf$/i.test(f.name)) { ocrMsg.value = '这里只收 .pdf'; return }
  ocrFile.value = f
  ocrMsg.value = ''
  ocrBlob.value = null
}

async function doOcr() {
  if (!ocrFile.value) return
  ocrBusy.value = true
  ocrMsg.value = '正在认字…一页大概 20 秒，页多要等一会儿'
  ocrBlob.value = null
  try {
    const fd = new FormData()
    fd.append('file', ocrFile.value)
    const resp = await fetch('/api/convert/pdf2docx-ocr', { method: 'POST', body: fd })
    if (!resp.ok) {
      let d = await resp.text()
      try { d = JSON.parse(d).detail || d } catch { /* 原样 */ }
      throw new Error(d)
    }
    ocrBlob.value = await resp.blob()
    ocrName.value = ocrFile.value.name.replace(/\.pdf$/i, '') + '-识别.docx'
    ocrMsg.value = `认字完成：${kb(ocrBlob.value.size)}，文字可编辑，认不出的页整页贴图`
  } catch (e) {
    ocrMsg.value = '识别失败：' + errText(e)
  } finally { ocrBusy.value = false }
}

const engineLine = computed(() => {
  const e = engines.value || {}
  const w = e.office ? 'WPS / Office（最准）' : e.soffice ? 'LibreOffice' : e.chromium ? '内置渲染（近似）' : '不可用'
  const p = e.pypdf ? 'pypdf' : e.pymupdf ? 'PyMuPDF' : '不可用'
  const o = e.ocr ? '可以用（本机认字）' : '不可用'
  return { word: w, pdf: p, ocr: o }
})
</script>

<template>
  <h2>格式转换</h2>
  <p class="muted" style="margin:2px 0 12px">
    全部在本机转，文件不出这台电脑。四件事：<b>文档互转</b>、<b>PDF 拆分</b>、<b>PDF 合并</b>、<b>扫描件认字转 Word</b>。
  </p>

  <div class="tabs">
    <button class="tab" :class="{ on: tab === 'doc' }" @click="tab = 'doc'">文档互转</button>
    <button class="tab" :class="{ on: tab === 'split' }" @click="tab = 'split'">PDF 拆分</button>
    <button class="tab" :class="{ on: tab === 'merge' }" @click="tab = 'merge'">PDF 合并</button>
    <button class="tab" :class="{ on: tab === 'dmerge' }" @click="tab = 'dmerge'">Word 合并</button>
    <button class="tab" :class="{ on: tab === 'dsplit' }" @click="tab = 'dsplit'">Word 拆分</button>
    <button class="tab" :class="{ on: tab === 'ocr' }" @click="tab = 'ocr'">扫描件转 Word</button>
  </div>

  <!-- 文档互转 -->
  <div v-show="tab === 'doc'" class="grid2">
    <section class="zone" :class="{ hot: dragOver === 'docx2pdf' }"
             @dragover.prevent="dragOver = 'docx2pdf'" @dragleave="dragOver = ''"
             @drop.prevent="onDocDrop($event, 'docx2pdf')" @click="pickFile('.docx', true, (fs) => addDocFiles('docx2pdf', fs))">
      <h3>Word → PDF</h3>
      <p class="tip">把 .docx 拖进来，或者点这里选文件（可以一次多个）</p>
      <p class="eng">走哪条路：<b>{{ engineLine.word }}</b></p>
    </section>
    <section class="zone" :class="{ hot: dragOver === 'pdf2docx' }"
             @dragover.prevent="dragOver = 'pdf2docx'" @dragleave="dragOver = ''"
             @drop.prevent="onDocDrop($event, 'pdf2docx')" @click="pickFile('.pdf', true, (fs) => addDocFiles('pdf2docx', fs))">
      <h3>PDF → Word</h3>
      <p class="tip">把 .pdf 拖进来，或者点这里选文件</p>
      <p class="eng">走哪条路：<b>重建段落</b>（双栏自动拆开，图整块插进来，文字可编辑）</p>
    </section>
  </div>

  <!-- PDF 拆分 -->
  <div v-show="tab === 'split'" class="card">
    <h3>PDF 拆分</h3>
    <p class="tip">选一份 PDF，写要保留的页码；留空 = 一页一个文件。输出是一个 zip，里面是拆好的 PDF。</p>
    <div class="dz" @dragover.prevent="dragOver = 'split'" @dragleave="dragOver = ''"
         @drop.prevent="dragOver = ''; takeSplit(Array.from($event.dataTransfer?.files || []))"
         @click="pickFile('.pdf', false, takeSplit)">
      <b>{{ splitFile ? splitFile.name : '点这里选 PDF，或把 PDF 拖进来' }}</b>
      <span v-if="splitFile" class="mut">{{ kb(splitFile.size) }}</span>
    </div>
    <div class="row">
      <label class="f"><span>页码范围（例如 1-3,5,8-）</span>
        <input v-model="splitPages" placeholder="留空 = 每页一个文件" /></label>
      <button class="primary" :disabled="!splitFile || splitBusy" @click="doSplit">{{ splitBusy ? '拆分中…' : '开始拆分' }}</button>
      <button v-if="splitBlob" class="ghost" @click="saveBlob(splitBlob, splitName)">下载 zip</button>
    </div>
    <p v-if="splitMsg" class="msg">{{ splitMsg }}</p>
  </div>

  <!-- PDF 合并 -->
  <div v-show="tab === 'merge'" class="card">
    <h3>PDF 合并</h3>
    <p class="tip">选两份以上 PDF，按列表顺序合并成一个。</p>
    <div class="dz" @dragover.prevent="dragOver = 'merge'" @dragleave="dragOver = ''"
         @drop.prevent="dragOver = ''; takeMerge(Array.from($event.dataTransfer?.files || []))"
         @click="pickFile('.pdf', true, takeMerge)">
      <b>{{ mergeFiles.length ? `已加 ${mergeFiles.length} 份` : '点这里选 PDF（可多选），或把 PDF 拖进来' }}</b>
    </div>
    <div v-if="mergeFiles.length" class="mlist">
      <div v-for="(m, i) in mergeFiles" :key="m.id" class="mrow">
        <span class="idx">{{ i + 1 }}</span>
        <span class="nm">{{ m.name }}</span>
        <span class="mut">{{ kb(m.size) }}</span>
        <button class="mini" :disabled="i === 0" @click="moveMerge(i, -1)">↑</button>
        <button class="mini" :disabled="i === mergeFiles.length - 1" @click="moveMerge(i, 1)">↓</button>
        <button class="mini" @click="mergeFiles.splice(i, 1)">×</button>
      </div>
    </div>
    <div class="row">
      <button class="primary" :disabled="mergeFiles.length < 2 || mergeBusy" @click="doMerge">{{ mergeBusy ? '合并中…' : '开始合并' }}</button>
      <button v-if="mergeBlob" class="ghost" @click="saveBlob(mergeBlob, 'merged.pdf')">下载 PDF</button>
      <button v-if="mergeFiles.length" class="ghost" @click="mergeFiles = []; mergeBlob = null">清空</button>
    </div>
    <p v-if="mergeMsg" class="msg">{{ mergeMsg }}</p>
  </div>

  <!-- Word 合并 -->
  <div v-show="tab === 'dmerge'" class="card">
    <h3>Word 合并</h3>
    <p class="tip">把各自出的题（.docx）按列表顺序并成一份，每份之间自动分页；文字、表格、图片都保留。</p>
    <div class="dz" @dragover.prevent="dragOver = 'dmerge'" @dragleave="dragOver = ''"
         @drop.prevent="dragOver = ''; takeWordMerge(Array.from($event.dataTransfer?.files || []))"
         @click="pickFile('.docx', true, takeWordMerge)">
      <b>{{ wmFiles.length ? `已加 ${wmFiles.length} 份` : '点这里选 Word（可多选），或把 .docx 拖进来' }}</b>
    </div>
    <div v-if="wmFiles.length" class="mlist">
      <div v-for="(m, i) in wmFiles" :key="m.id" class="mrow">
        <span class="idx">{{ i + 1 }}</span>
        <span class="nm">{{ m.name }}</span>
        <span class="mut">{{ kb(m.size) }}</span>
        <button class="mini" :disabled="i === 0" @click="moveWordMerge(i, -1)">↑</button>
        <button class="mini" :disabled="i === wmFiles.length - 1" @click="moveWordMerge(i, 1)">↓</button>
        <button class="mini" @click="wmFiles.splice(i, 1)">×</button>
      </div>
    </div>
    <div class="row">
      <button class="primary" :disabled="wmFiles.length < 2 || wmBusy" @click="doWordMerge">{{ wmBusy ? '合并中…' : '开始合并' }}</button>
      <button v-if="wmBlob" class="ghost" @click="saveBlob(wmBlob, '合并稿.docx')">下载 Word</button>
      <button v-if="wmFiles.length" class="ghost" @click="wmFiles = []; wmBlob = null">清空</button>
    </div>
    <p v-if="wmMsg" class="msg">{{ wmMsg }}</p>
  </div>

  <!-- Word 拆分 -->
  <div v-show="tab === 'dsplit'" class="card">
    <h3>Word 拆分</h3>
    <p class="tip">一份 Word 里有好几道题，想拆成每人一份：按题号切（识别「第 1 题 / 1. / （1）」这类开头），或者平均切成 N 份。输出是一个 zip。</p>
    <div class="dz" @dragover.prevent="dragOver = 'dsplit'" @dragleave="dragOver = ''"
         @drop.prevent="dragOver = ''; takeWordSplit(Array.from($event.dataTransfer?.files || []))"
         @click="pickFile('.docx', false, takeWordSplit)">
      <b>{{ wsFile ? wsFile.name : '点这里选 Word，或把 .docx 拖进来' }}</b>
      <span v-if="wsFile" class="mut">{{ kb(wsFile.size) }}</span>
    </div>
    <div class="row">
      <label class="f"><span>怎么拆</span>
        <select v-model="wsMode">
          <option value="question">按题号切（推荐）</option>
          <option value="count">平均切成 N 份</option>
        </select></label>
      <label v-if="wsMode === 'count'" class="f"><span>份数</span>
        <input v-model.number="wsCount" type="number" min="2" max="30" /></label>
      <button class="primary" :disabled="!wsFile || wsBusy" @click="doWordSplit">{{ wsBusy ? '拆分中…' : '开始拆分' }}</button>
      <button v-if="wsBlob" class="ghost" @click="saveBlob(wsBlob, wsName)">下载 zip</button>
    </div>
    <p v-if="wsMsg" class="msg">{{ wsMsg }}</p>
  </div>

  <!-- 扫描件 OCR -->
  <div v-show="tab === 'ocr'" class="card">
    <h3>扫描件转 Word（认字）</h3>
    <p class="tip">扫描版 / 图片版 PDF 没有文字层，走 OCR 逐页认字，转成可编辑的 Word；认不出的页会把整页图贴回去，内容不丢。</p>
    <div class="dz" @dragover.prevent="dragOver = 'ocr'" @dragleave="dragOver = ''"
         @drop.prevent="dragOver = ''; takeOcr(Array.from($event.dataTransfer?.files || []))"
         @click="pickFile('.pdf', false, takeOcr)">
      <b>{{ ocrFile ? ocrFile.name : '点这里选扫描版 PDF，或拖进来' }}</b>
      <span v-if="ocrFile" class="mut">{{ kb(ocrFile.size) }}</span>
    </div>
    <div class="row">
      <button class="primary" :disabled="!ocrFile || ocrBusy" @click="doOcr">{{ ocrBusy ? '认字中…' : '开始认字' }}</button>
      <button v-if="ocrBlob" class="ghost" @click="saveBlob(ocrBlob, ocrName)">下载 Word</button>
      <span class="mut">本机 OCR：{{ engineLine.ocr }}</span>
    </div>
    <p v-if="ocrMsg" class="msg">{{ ocrMsg }}</p>
  </div>

  <!-- 队列 -->
  <div v-if="jobs.length" class="card" style="margin-top:14px">
    <div class="qhead">
      <b>转换队列（{{ jobs.length }}）</b>
      <button class="ghost sm" @click="clearDone">清掉已完成的</button>
    </div>
    <div v-for="j in jobs" :key="j.id" class="job" :class="j.state">
      <span class="nm">{{ j.name }}</span>
      <span class="st">{{ j.state === 'wait' ? '排队中' : j.state === 'busy' ? '转…' : j.state === 'done' ? '好了' : '失败' }}</span>
      <span class="mut msg">{{ j.state === 'done' ? `${j.msg}　${kb(j.size)}` : j.msg }}</span>
      <button v-if="j.state === 'done'" class="primary sm" @click="saveBlob(j.blob, j.outName)">下载</button>
      <button v-if="j.state === 'bad'" class="mini" @click="jobs = jobs.filter((x) => x.id !== j.id)">去掉</button>
    </div>
  </div>
</template>

<style scoped>
.tabs { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.tab { height: 32px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--line); background: #fff; cursor: pointer; }
.tab.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.zone { background: #fff; border: 1.5px dashed #d0d5dd; border-radius: 14px; padding: 18px 20px; transition: border-color .15s, background .15s; cursor: pointer; }
.zone.hot { border-color: #2f6fed; background: #f5f9ff; }
.zone h3 { margin: 0 0 6px; font-size: 15px; }
.tip { margin: 0 0 8px; color: #667085; font-size: 12.5px; line-height: 1.7; }
.eng { margin: 0; font-size: 12px; color: #1c8c46; }
.card { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
.card h3 { margin: 0 0 8px; font-size: 15px; }
.dz { border: 1.5px dashed #d0d5dd; border-radius: 12px; padding: 16px; display: flex; align-items: center; justify-content: space-between; gap: 10px; cursor: pointer; background: #fcfcfd; }
.row { display: flex; align-items: flex-end; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.f { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: #667085; flex: 1 1 280px; }
.f input { height: 34px; border: 1px solid var(--line); border-radius: 8px; padding: 0 10px; font-size: 13px; }
.msg { margin: 10px 0 0; font-size: 12.5px; color: #475467; }
.mlist { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.mrow { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.mrow .idx { width: 20px; color: #98a2b3; }
.mrow .nm { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.job { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px dashed var(--line); font-size: 13px; }
.job .st { font-size: 12px; color: #667085; }
.job .msg { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job.done .st { color: #1c8c46; }
.job.bad .st { color: #b42318; }
.mut { color: #98a2b3; font-size: 12px; }
</style>
