<script setup>
// 格式转换：docx → pdf、pdf → docx。左边拖 docx 出 pdf，右边拖 pdf 出 word。
// 都是本地转换，不上传外网；一次可以拖多个，排队转。
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const engines = ref({})
const jobs = ref([])          // {id, name, dir, state, msg, blob, outName, size}
const dragOver = ref('')
let seq = 1

async function loadEngines() {
  try { engines.value = await api.get('/api/convert/engines') } catch { engines.value = {} }
}

function pick(dir) {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = dir === 'docx2pdf' ? '.docx' : '.pdf'
  input.multiple = true
  input.onchange = () => addFiles(dir, Array.from(input.files || []))
  input.click()
}

function addFiles(dir, files) {
  files.forEach((f) => {
    jobs.value.push({ id: seq++, name: f.name, dir, file: f, state: 'wait', msg: '', blob: null, size: 0 })
  })
  run()
}

function onDrop(e, dir) {
  dragOver.value = ''
  const files = Array.from(e.dataTransfer?.files || [])
  if (!files.length) return
  const want = dir === 'docx2pdf' ? /\.docx$/i : /\.pdf$/i
  const ok = files.filter((f) => want.test(f.name))
  const bad = files.length - ok.length
  addFiles(dir, ok)
  if (bad) jobs.value.push({ id: seq++, name: `${bad} 个文件类型不对`, dir, state: 'bad', msg: dir === 'docx2pdf' ? '这里只收 .docx' : '这里只收 .pdf', blob: null, size: 0 })
}

let running = false
async function run() {
  if (running) return
  running = true
  try {
    for (;;) {
      const j = jobs.value.find((x) => x.state === 'wait')
      if (!j) break
      j.state = 'busy'
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
        j.outName = m ? decodeURIComponent(m[1]) : j.name.replace(/\.[^.]+$/, '') + (j.dir === 'docx2pdf' ? '.pdf' : '.docx')
        j.state = 'done'
        j.msg = eng === 'soffice' ? '用 LibreOffice 转的（版式最准）'
          : eng === 'builtin-chromium' ? '用内置渲染转的（版式近似；装 LibreOffice 会更准）'
            : eng === 'pymupdf-rebuild' ? '按原 PDF 的文字与图片重建（版式近似）' : ''
      } catch (e) {
        j.state = 'bad'
        j.msg = e?.message || String(e)
      }
    }
  } finally { running = false }
}

function download(j) {
  if (!j.blob) return
  const url = URL.createObjectURL(j.blob)
  const a = document.createElement('a')
  a.href = url
  a.download = j.outName
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
}

function clearDone() { jobs.value = jobs.value.filter((j) => j.state !== 'done' && j.state !== 'bad') }

function kb(n) { return n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB' }

onMounted(loadEngines)
</script>

<template>
  <h2>格式转换</h2>
  <p class="muted" style="margin:2px 0 14px">
    在本地转换，文件不出这台电脑。一次可以拖一串文件，慢慢转，转完点「下载」。
  </p>

  <div class="pair">
    <section class="zone" :class="{ hot: dragOver === 'docx2pdf' }"
             @dragover.prevent="dragOver = 'docx2pdf'" @dragleave.prevent="dragOver = ''"
             @drop.prevent="onDrop($event, 'docx2pdf')">
      <div class="zh">
        <b>Word → PDF</b>
        <span class="tag">.docx → .pdf</span>
      </div>
      <p class="hint">把 Word 文件拖到这里，或者</p>
      <button class="primary" @click="pick('docx2pdf')">选 Word 文件</button>
      <p class="eng">
        引擎：<b v-if="engines.office">本机 WPS / Office（版式和原文件一致）</b>
        <b v-else-if="engines.soffice">LibreOffice（版式最准）</b>
        <b v-else-if="engines.chromium">内置渲染（Chromium，版式近似）</b>
        <b v-else>不可用</b>
      </p>
      <p v-if="!engines.office && !engines.soffice && engines.chromium" class="tip">
        想更准可以装一个 LibreOffice（免费），装完这里会自动变成「LibreOffice」。
      </p>
    </section>

    <section class="zone" :class="{ hot: dragOver === 'pdf2docx' }"
             @dragover.prevent="dragOver = 'pdf2docx'" @dragleave.prevent="dragOver = ''"
             @drop.prevent="onDrop($event, 'pdf2docx')">
      <div class="zh">
        <b>PDF → Word</b>
        <span class="tag">.pdf → .docx</span>
      </div>
      <p class="hint">把 PDF 拖到这里，或者</p>
      <button class="primary" @click="pick('pdf2docx')">选 PDF 文件</button>
      <p class="eng">引擎：<b>{{ engines.pymupdf ? '重建段落（双栏自动拆开，图整块插进来，文字可编辑）' : '不可用' }}</b></p>
      <p class="tip">扫描版 PDF 只有图、没有文字层，转出来也是图；要可编辑得先 OCR。</p>
    </section>
  </div>

  <div v-if="jobs.length" class="card" style="margin-top:14px">
    <div class="qhead">
      <b>转换队列（{{ jobs.length }}）</b>
      <span class="spacer"></span>
      <button class="ghost sm" @click="clearDone">清掉已完成</button>
    </div>
    <div v-for="j in jobs" :key="j.id" class="job" :class="j.state">
      <span class="dir">{{ j.dir === 'docx2pdf' ? 'W→P' : 'P→W' }}</span>
      <span class="nm">{{ j.name }}</span>
      <span class="st">
        <template v-if="j.state === 'wait'">排队中</template>
        <template v-else-if="j.state === 'busy'">转换中…</template>
        <template v-else-if="j.state === 'done'">{{ j.outName }} · {{ kb(j.size) }}</template>
        <template v-else>{{ j.msg }}</template>
      </span>
      <span v-if="j.state === 'done'" class="eng2">{{ j.msg }}</span>
      <button v-if="j.state === 'done'" class="primary sm" @click="download(j)">下载</button>
    </div>
  </div>
</template>

<style scoped>
.pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
.zone { background: #fff; border: 1.5px dashed #d0d5dd; border-radius: 14px; padding: 18px 20px; transition: border-color .15s, background .15s; }
.zone.hot { border-color: #2f6fed; background: #f5f9ff; }
.zh { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.zh b { font-size: 16px; }
.tag { font-size: 12px; color: #667085; background: #f2f4f7; border-radius: 999px; padding: 2px 8px; }
.hint { color: #667085; font-size: 13px; margin: 6px 0 10px; }
.eng { font-size: 12.5px; color: #475467; margin: 12px 0 0; }
.tip { font-size: 11.5px; color: #98a2b3; margin: 6px 0 0; line-height: 1.6; }
.qhead { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.qhead .spacer { flex: 1; }
.job { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-top: 1px dashed #eef1f6; font-size: 13px; }
.dir { font-size: 11px; color: #667085; background: #f2f4f7; border-radius: 6px; padding: 2px 6px; }
.nm { flex: 0 0 auto; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st { flex: 1; color: #475467; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job.bad .st { color: #b42318; }
.eng2 { font-size: 11.5px; color: #98a2b3; }
.primary.sm { height: 26px; font-size: 12px; padding: 0 10px; }
</style>
