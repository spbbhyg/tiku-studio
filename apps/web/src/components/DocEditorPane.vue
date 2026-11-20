<template>
  <div class="doce" @paste.capture="onPaste" @drop.capture="onTextDrop">
    <div class="dbar">
      <div class="row">
        <b>文档编辑</b>
        <span class="mut">Word 原样编辑，改完「保存到项目」</span>
        <span class="grow" />
        <select v-model="current" class="pick wide" :disabled="busy || !files.length" @change="openByName(current)">
          <option value="">（选一份文档）</option>
          <option v-for="f in files" :key="f.name" :value="f.name">{{ f.name }} — {{ f.mtime }}</option>
        </select>
        <button class="btn" :disabled="busy" @click="pickFile">打开本机 .docx</button>
        <button class="btn" :disabled="busy" title="新建一份空白文档" @click="newBlank">新建空白</button>
        <input ref="fileEl" type="file" accept=".docx" hidden @change="onUpload" />
      </div>
      <div class="row">
        <select v-model="mode" class="pick" :disabled="!ready" @change="remount">
          <option value="editing">编辑</option>
          <option value="suggesting">修订建议</option>
          <option value="viewing">只读</option>
        </select>
        <button class="btn primary" :disabled="!ready || busy" @click="saveBack">保存到项目</button>
        <button class="btn" :disabled="!ready || busy" @click="download">导出 .docx</button>
        <span class="sep" />
        <button class="btn" :disabled="!ready" title="插入化学公式（支持 \ce{} 写法）" @click="openFormula">公式</button>
        <button class="btn" :disabled="!ready" title="画结构式 / 反应式插进文档（箭头、加号都在）" @click="openStruct">结构式</button>
        <button class="btn" :disabled="!ready" title="插图：选图片（可多选），或者直接把截图 Ctrl+V 粘到文档里" @click="pickImages">插图</button>
        <input ref="imgEl" type="file" accept="image/*" multiple hidden @change="onPickImages" />
        <button class="btn ghost" :disabled="!current || busy" @click="removeCurrent">删除</button>
        <span class="grow" />
        <span class="mut" v-if="ready && current">当前打开：{{ current }}</span>
      </div>
    </div>

    <div v-if="msg" class="ok">{{ msg }}</div>
    <div v-if="err" class="bad">{{ err }}</div>

    <div class="host">
      <div ref="tbEl" class="tb" />
      <div ref="edEl" class="ed" />
      <div v-if="!ready" class="empty">
        <p class="big">把 Word 文档拖进来，或者点右上角「打开本机 .docx」</p>
        <p class="mut">支持 .docx（.doc 请先在 Word 里另存为 .docx）。文档存在本机 <code>data/docx</code>，不上传任何服务器。</p>
      </div>
      <div v-if="busy" class="mask"><span>正在加载编辑器…第一次打开要等几秒</span></div>
    </div>

    <!-- 公式 -->
    <div v-if="fx.open" class="dlg">
      <div class="card">
        <div class="dhead">
          <b>插入公式</b>
          <span class="mut">写 LaTeX，化学式用 \ce{…}；也可以直接写 H2SO4 这种</span>
          <span class="grow" />
          <button class="btn ghost" @click="fx.open = false">关闭</button>
        </div>
        <textarea v-model="fx.tex" class="ta" rows="3" placeholder="例如：\ce{2H2 + O2 ->[点燃] 2H2O}　或　\Delta H = -285.8\ \mathrm{kJ/mol}" />
        <div class="chips">
          <button v-for="c in fxChips" :key="c.t" class="chip" @click="addChip(c)">{{ c.t }}</button>
        </div>
        <div class="prev">
          <img v-if="fx.url" :src="fx.url" alt="公式预览" />
          <span v-else class="mut">{{ fx.busy ? '正在渲染…' : '点「预览」看效果' }}</span>
        </div>
        <div class="dfoot">
          <button class="btn" :disabled="fx.busy" @click="previewFormula">预览</button>
          <span class="grow" />
          <label class="ck"><input v-model="fx.display" type="checkbox" @change="previewFormula" /> 独立成行（大公式）</label>
          <button class="btn primary" :disabled="!fx.data || fx.busy" @click="insertFormula">插到光标处</button>
        </div>
      </div>
    </div>

    <!-- 结构式 -->
    <div v-if="st.open" class="dlg">
      <div class="card wide2">
        <div class="dhead">
          <b>插入结构式</b>
          <span class="mut">在下面画板和 Ketcher 里画，箭头、加号、文字标注都会一起进文档</span>
          <span class="grow" />
          <button class="btn ghost" @click="st.open = false">关闭</button>
        </div>
        <iframe ref="stFrame" class="kframe" src="/ketcher/index.html" />
        <div class="dfoot">
          <span class="mut">{{ st.busy ? '正在取图…' : '画完点右边按钮' }}</span>
          <span class="grow" />
          <button class="btn primary" :disabled="st.busy" @click="takeStruct">取结构并插入</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue'
import { api } from '../api.js'
import { renderMath } from '../math.js'
import { extractFromFrame } from '../ketcherSvg.js'
import 'superdoc/style.css'

const files = ref([])
const current = ref('')
const mode = ref('editing')
const busy = ref(false)
const ready = ref(false)
const msg = ref('')
const err = ref('')
const tbEl = ref(null)
const edEl = ref(null)
const fileEl = ref(null)
const imgEl = ref(null)
const sd = shallowRef(null)

const fx = reactive({ open: false, tex: '', display: false, url: '', data: '', w: 0, h: 0, busy: false })
const st = reactive({ open: false, busy: false })
const stFrame = ref(null)

const fxChips = [
  { t: '\\ce{…}', ins: '\\ce{}', back: 1 },
  { t: '方程式 →', ins: '\\ce{A -> B}' },
  { t: '可逆 ⇌', ins: '\\ce{A <=> B}' },
  { t: '条件箭头', ins: '\\ce{A ->[\\Delta] B}' },
  { t: 'Δ', ins: '\\Delta ' },
  { t: '下标', ins: '_{}', back: 1 },
  { t: '上标', ins: '^{}', back: 1 },
  { t: '分数', ins: '\\frac{}{}', back: 3 },
  { t: '根号', ins: '\\sqrt{}', back: 1 },
  { t: 'α', ins: '\\alpha ' }, { t: 'β', ins: '\\beta ' }, { t: 'γ', ins: '\\gamma ' },
  { t: 'δ', ins: '\\delta ' }, { t: 'Δ', ins: '\\Delta ' }, { t: 'ε', ins: '\\varepsilon ' },
  { t: 'θ', ins: '\\theta ' }, { t: 'λ', ins: '\\lambda ' }, { t: 'μ', ins: '\\mu ' },
  { t: 'ν', ins: '\\nu ' }, { t: 'π', ins: '\\pi ' }, { t: 'ρ', ins: '\\rho ' },
  { t: 'σ', ins: '\\sigma ' }, { t: 'τ', ins: '\\tau ' }, { t: 'φ', ins: '\\varphi ' },
  { t: 'ω', ins: '\\omega ' }, { t: 'Ω', ins: '\\Omega ' },
  { t: '⇌', ins: '\\rightleftharpoons ' }, { t: '→', ins: '\\rightarrow ' },
  { t: '°C', ins: '^\\circ\\mathrm{C} ' }, { t: '↑', ins: '\\uparrow ' }, { t: '↓', ins: '\\downarrow ' },
  { t: '≈', ins: '\\approx ' }, { t: '≤', ins: '\\le ' }, { t: '≥', ins: '\\ge ' },
  { t: '单位', ins: '\\ \\mathrm{kJ/mol}' },
]

/** 工具栏全部中文（SuperDoc 自带英文，这里逐个覆盖） */
const CN = {
  bold: '加粗', italic: '斜体', underline: '下划线', strikethrough: '删除线', highlight: '底纹颜色',
  color: '字体颜色', fontFamily: '字体', fontSize: '字号', search: '查找替换', link: '插入链接',
  image: '插入图片', table: '插入表格', tableActions: '表格操作',
  addRowBefore: '在上方插入行', addRowAfter: '在下方插入行',
  addColumnBefore: '在左侧插入列', addColumnAfter: '在右侧插入列',
  deleteRow: '删除行', deleteColumn: '删除列', deleteTable: '删除表格',
  removeBorders: '去掉边框', mergeCells: '合并单元格', splitCell: '拆分单元格', fixTables: '修复表格',
  textAlign: '对齐方式', bulletList: '项目符号', numberedList: '编号列表',
  indentLeft: '减少缩进', indentRight: '增加缩进', lineHeight: '行距', zoom: '缩放',
  measurementUnit: '度量单位', undo: '撤销', redo: '重做', trackChanges: '修订',
  trackChangesAccept: '接受所选修订', trackChangesReject: '拒绝所选修订',
  trackChangesOriginal: '显示原文', trackChangesFinal: '显示改后',
  clearFormatting: '清除格式', copyFormat: '格式刷', formatText: '段落样式',
  ruler: '显示/隐藏标尺', formattingMarks: '显示/隐藏编辑标记', pageBreak: '插入分页符',
  documentEditingMode: '编辑', documentSuggestingMode: '修订建议', documentViewingMode: '只读',
  documentEditingModeDescription: '直接修改文档', documentSuggestingModeDescription: '修改会记成建议',
  documentViewingModeDescription: '只看不改', linkedStyles: '样式', updateLinkedStyle: '更新样式',
  ai: 'AI 生成', directionLtr: '从左到右', directionRtl: '从右到左',
  tableOfContents: '目录', watermark: '水印', pageNumber: '页码'
};

/* SuperDoc 自带的工具条/提示/加载遮罩里还有一批英文（aria-label 与提示文字是硬编码的），
   这里做一层轻量的 DOM 翻译：只替换字典里精确命中的这几个词，绝不碰文档正文。 */
const EN2CN = {
  Toolbar: '工具栏', 'Toolbar separator': '分隔线', Undo: '撤销', Redo: '重做',
  'Accept tracked changes': '接受修订', 'Reject tracked changes': '拒绝修订',
  Zoom: '缩放', 'Font family': '字体', 'Font family options': '字体', 'Font size': '字号',
  'Font size options': '字号', Bold: '加粗', Italic: '斜体', Underline: '下划线',
  Strikethrough: '删除线', Color: '字体颜色', Highlight: '底纹颜色', 'Link dropdown': '插入链接',
  Image: '插入图片', Table: '插入表格', 'Table actions': '表格操作', 'Text align': '对齐方式',
  'Bullet list': '项目符号', 'Bullet list options': '项目符号', 'Numbered list': '编号列表',
  'Numbered list options': '编号列表', 'Left indent': '减少缩进', 'Right indent': '增加缩进',
  'Line height': '行距', 'Linked styles': '样式', Ruler: '标尺', 'Copy formatting': '格式刷',
  'Clear formatting': '清除格式', Search: '查找替换', 'Format text': '段落样式',
  'Insert row above': '在上方插入行', 'Insert row below': '在下方插入行',
  'Insert column left': '在左侧插入列', 'Insert column right': '在右侧插入列',
  'Delete row': '删除行', 'Delete column': '删除列', 'Delete table': '删除表格',
  'Remove borders': '去掉表格边框', 'Merge cells': '合并单元格', 'Split cell': '拆分单元格',
  'Fix tables': '修复表格', 'Insert table': '插入表格',
  'Insert page break': '插入分页符', 'Show or hide ruler': '显示/隐藏标尺',
  'Show or hide formatting marks': '显示/隐藏编辑标记', 'Editing': '编辑', 'Suggesting': '修订建议',
  'Viewing': '只读', 'Loading document': '正在打开文档', 'Opening document...': '正在解析文档内容…',
  PROGRESS: '进度', unset: '', 'SuperDoc body (v2)': '文档正文', 'Text composition input': '输入'
};
function cnText(t) {
  const k = String(t || '').trim()
  if (!k) return null
  if (Object.prototype.hasOwnProperty.call(EN2CN, k)) return EN2CN[k]
  const m = k.match(/^(.+?)\s+unset$/)
  if (m) {
    if (Object.prototype.hasOwnProperty.call(EN2CN, m[1])) return EN2CN[m[1]]
    if (/[一-鿿]/.test(m[1])) return m[1]
  }
  return null
}
function translateOnce(root) {
  if (!root) return
  const skip = (el) => el && el.closest && el.closest('[contenteditable="true"], .ProseMirror, .dbar, .dlg')
  root.querySelectorAll('[aria-label], [title], [placeholder], [alt]').forEach((el) => {
    if (skip(el)) return
    for (const attr of ['aria-label', 'title', 'placeholder', 'alt']) {
      const v = el.getAttribute(attr)
      if (!v) continue
      const t = cnText(v)
      if (t !== null) el.setAttribute(attr, t)
    }
  })
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const todo = []
  let n
  while ((n = walk.nextNode())) {
    const t = cnText(n.nodeValue)
    if (t !== null && !skip(n.parentElement)) todo.push([n, t])
  }
  todo.forEach(([node, val]) => { node.nodeValue = val })
}
let cnTimer = 0
let cnObserver = null
function installCn(root) {
  stopCn()
  const run = () => translateOnce(root)
  run()
  const ob = new MutationObserver(() => {
    clearTimeout(cnTimer)
    cnTimer = setTimeout(run, 60)
  })
  ob.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['aria-label', 'title'] })
  cnObserver = ob
}
function stopCn() {
  clearTimeout(cnTimer)
  if (cnObserver) { try { cnObserver.disconnect() } catch { /* 忽略 */ } }
  cnObserver = null
}

function say(t) { msg.value = t; err.value = ''; setTimeout(() => { if (msg.value === t) msg.value = '' }, 5000) }
function fail(e) { err.value = String(e && e.message ? e.message : e); msg.value = '' }

async function load() {
  try {
    const r = await api.get('/api/doce/files')
    files.value = r.files || []
  } catch (e) { fail(e) }
}

function teardown() {
  stopCn()
  try { if (sd.value && sd.value.destroy) sd.value.destroy() } catch { /* 忽略 */ }
  sd.value = null
  ready.value = false
  if (tbEl.value) tbEl.value.innerHTML = ''
  if (edEl.value) edEl.value.innerHTML = ''
}

/** 真正创建编辑器：document 可以是 URL，也可以是 File/Blob；不传就是空白文档 */
async function boot(document, name) {
  teardown()
  busy.value = true
  try {
    const { SuperDoc } = await import('superdoc')
    const host = edEl.value
    if (!host) return
    document_el()
    const opts = {
      selector: '#sdmount',
      documentMode: mode.value,
      user: { name: '神奋用户', email: 'local@shenfen' },
      telemetry: { enabled: false },
      ui: {
        toolbar: {
          container: tbEl.value,
          texts: CN,
          excludeItems: ['ai', 'document-mode', 'watermark', 'page-number', 'table-of-contents', 'measurement-unit'],
        },
      },
      onReady: () => { busy.value = false; ready.value = true; const root = tbEl.value && tbEl.value.closest ? tbEl.value.closest('.doce') : host; installCn(root || host); say(`已打开「${name || '空白文档'}」`) },
      onContentError: (p) => { busy.value = false; fail(p && p.error ? p.error : '文档打不开') },
    }
    if (document) opts.document = document
    sd.value = new SuperDoc(opts)
    if (typeof window !== 'undefined') window.__sd = sd.value
    current.value = name || ''
  } catch (e) {
    busy.value = false
    fail(e)
  }
}

function document_el() {
  const host = edEl.value
  const el = document.createElement('div')
  el.id = 'sdmount'
  el.style.height = '100%'
  el.style.width = '100%'
  host.appendChild(el)
  return el
}

function openByName(name) {
  const f = files.value.find((x) => x.name === name)
  if (f) boot(f.url + '?t=' + Date.now(), f.name)
}

async function openDoc(f) {
  await load()
  await boot(f.url + '?t=' + Date.now(), f.name)
}

function pickFile() { fileEl.value && fileEl.value.click() }

async function onUpload(ev) {
  const f = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!f) return
  busy.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    const r = await api.postForm('/api/doce/upload', fd)
    await openDoc(r)
  } catch (e) { busy.value = false; fail(e) }
}

async function newBlank() {
  await load()
  await boot(null, '')
}

async function saveBack() {
  if (!sd.value) return
  busy.value = true
  try {
    const base = (current.value || '编辑稿').replace(/\.docx$/i, '')
    const blob = await sd.value.export({ exportType: ['docx'], triggerDownload: false, exportedName: base })
    const name = current.value || `${base}-${Date.now()}.docx`
    const fd = new FormData()
    fd.append('file', new File([blob], name, { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }))
    fd.append('name', name)
    fd.append('overwrite', 'true')
    const r = await api.postForm('/api/doce/save', fd)
    await load()
    current.value = r.name
    say(`已保存：${r.name}（${Math.round(r.size / 1024)} KB）`)
  } catch (e) { fail(e) } finally { busy.value = false }
}

async function download() {
  if (!sd.value) return
  try {
    await sd.value.export({ exportType: ['docx'], exportedName: (current.value || '编辑稿').replace(/\.docx$/i, '') })
    say('已导出到浏览器下载目录')
  } catch (e) { fail(e) }
}

async function removeCurrent() {
  if (!current.value) return
  if (!window.confirm(`删除「${current.value}」？删了就找不回来了。`)) return
  busy.value = true
  try {
    await api.del(`/api/doce/file/${encodeURIComponent(current.value)}`)
    teardown()
    current.value = ''
    await load()
    say('已删除')
  } catch (e) { fail(e) } finally { busy.value = false }
}

function remount() {
  const f = files.value.find((x) => x.name === current.value)
  if (f) boot(f.url + '?t=' + Date.now(), f.name)
  else if (!current.value) boot(null, '')
}

/* ---------------------------------------------------------------- 公式 */
function openFormula() { fx.open = true }

function addChip(c) {
  fx.tex += c.ins
  if (c.back) {
    const pos = fx.tex.length - c.back
    setTimeout(() => {
      const el = document.querySelector('.dlg .ta')
      if (el) { el.focus(); el.setSelectionRange(pos, pos) }
    }, 0)
  }
}

async function previewFormula() {
  if (!fx.tex.trim()) { fx.url = ''; fx.data = ''; return }
  fx.busy = true
  try {
    const r = await api.post('/api/doce/formula', { tex: fx.tex, display: fx.display })
    fx.url = r.url + '?t=' + Date.now()
    fx.data = r.data
    fx.w = r.width
    fx.h = r.height
  } catch (e) { fx.data = ''; fx.url = ''; fail(e) } finally { fx.busy = false }
}

async function insertFormula() {
  if (!fx.data) return
  const ok = await insertPng(fx.data, fx.w, fx.h, '公式')
  if (ok) { fx.open = false; say('公式已插到光标处') }
}

/* ---------------------------------------------------------------- 结构式 */
function openStruct() {
  st.open = true
}

async function takeStruct() {
  st.busy = true
  try {
    const fr = stFrame.value
    const whole = fr ? extractFromFrame(fr) : null
    if (!whole || !whole.svg) throw new Error('画板里还没有结构，先画一个再取')
    const r = await api.post('/api/doce/structure', { svg: whole.svg })
    const ok = await insertPng(r.data, r.width, r.height, '结构式')
    if (ok) { st.open = false; say('结构式已插到光标处') }
  } catch (e) { fail(e) } finally { st.busy = false }
}

/* ---------------------------------------------------------------- 插图 */
/** 从左侧「题目文本」拖到 Word：保留 $公式$，插入到当前光标处。 */
async function onTextDrop(e) {
  const dt = e && e.dataTransfer
  if (!dt || (dt.files && dt.files.length)) return
  const text = String(dt.getData('text/plain') || '').trim()
  if (!text || text.startsWith('images/')) return
  e.preventDefault()
  e.stopPropagation()
  const html = text.split(/\n+/).filter(Boolean).map((line) => `<p>${renderMath(line.trim())}</p>`).join('')
  const ok = await insertHtml(html, { atEnd: false })
  if (ok) say('已把文字插到光标处')
}

/** 粘贴：Word 引擎本身不处理图片粘贴，这里接住，走我们自己的插图通道。 */
async function onPaste(e) {
  const cd = e && e.clipboardData
  if (!cd) return
  const files = Array.from(cd.files || []).filter((f) => /^image\//.test(f.type))
  if (!files.length) return
  e.preventDefault()
  e.stopPropagation()
  for (const f of files) await insertFile(f)
}

function pickImages() { imgEl.value && imgEl.value.click() }

async function onPickImages(ev) {
  const list = Array.from((ev.target && ev.target.files) || [])
  ev.target.value = ''
  for (const f of list) await insertFile(f)
}

function readAsDataUrl(f) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result || ''))
    r.onerror = () => reject(new Error('读不了这张图：文件可能被别的程序占用，或者格式不支持'))
    r.readAsDataURL(f)
  })
}

function imageSize(dataUrl) {
  return new Promise((resolve) => {
    const im = new Image()
    im.onload = () => resolve({ w: im.naturalWidth || 0, h: im.naturalHeight || 0 })
    im.onerror = () => resolve({ w: 0, h: 0 })
    im.src = dataUrl
  })
}

/** 把一张本地图片（截图/照片）插到光标处；px → pt 用 0.75 折算，宽度上限 430pt。 */
async function insertFile(f) {
  if (!ready.value) { fail('文档还在加载，等正文出来再插'); return }
  try {
    const data = await readAsDataUrl(f)
    if (!data) { fail('这张图读不出来，换一张试试'); return }
    const { w, h } = await imageSize(data)
    const wpt = w ? Math.min(w * 0.75, 430) : 300
    const hpt = w && h ? wpt * (h / w) : wpt * 0.6
    const ok = await insertPng(data, wpt, hpt, f.name || '插图')
    if (ok) say(`已插入「${f.name || '图片'}」`)
  } catch (e) { fail(e) }
}

async function insertPng(dataUrl, wPt, hPt, alt) {
  const ed = sd.value && sd.value.activeEditor
  const doc = ed && ed.doc
  if (!doc || !doc.create || !doc.create.image) { fail('编辑器还没准备好，等文档显示出来再插'); return false }
  const w = Math.min(Number(wPt) || 120, 430)
  const ratio = Number(hPt) && Number(wPt) ? Number(hPt) / Number(wPt) : 1
  try {
    const res = await doc.create.image({
      src: dataUrl,
      alt: `${alt || '插图'}-${Date.now().toString(36)}`,
      size: { width: w, height: Math.max(8, w * ratio), unit: 'pt' },
    })
    if (res && res.success === false) {
      fail((res.failure && res.failure.message) || res.reason || '插入失败：文档引擎拒收了这张图')
      return false
    }
    return true
  } catch (e) { fail(e); return false }
}

/** 给三栏工作台用：把一段 HTML 写进文档（atEnd=false 时写进当前光标处） */
/** 等文档接口就绪（编辑器创建后 activeEditor 会晚一点才出现） */
async function whenReady(ms = 20000) {
  const t0 = Date.now()
  for (;;) {
    const ed = sd.value && sd.value.activeEditor
    if (ed && ed.doc && ed.doc.insert) return ed
    if (Date.now() - t0 > ms) return null
    await new Promise((r) => setTimeout(r, 200))
  }
}

async function insertHtml(html, { atEnd = true } = {}) {
  const ed = await whenReady()
  const doc = ed && ed.doc
  if (!doc || !doc.insert) { fail('编辑器还没准备好，等正文出来再试'); return false }
  try {
    const input = { value: html, type: 'html' }
    if (!atEnd && doc.selection && doc.selection.current) {
      const cur = doc.selection.current()
      if (cur && cur.target) input.target = cur.target
    }
    const res = await doc.insert(input)
    if (res && res.success === false) {
      fail((res.failure && res.failure.message) || '写入失败：文档引擎拒收了这段内容')
      return false
    }
    return true
  } catch (e) { fail(e); return false }
}

/** 把当前文档导出成 Blob（切换标签、存进题库时用） */
async function exportBlob(name) {
  if (!sd.value || !sd.value.export) return null
  try {
    return await sd.value.export({ exportType: ['docx'], triggerDownload: false, exportedName: name || 'doc' })
  } catch (e) { fail(e); return null }
}

/** 用一份 Blob/File 直接替换当前文档（不经过服务器） */
async function loadBlob(blob, name) { await boot(blob, name || '') }

function lastError() { return err.value }

defineExpose({ lastError, insertHtml, exportBlob, loadBlob, openByName, insertFile, newBlank, insertImageData: insertPng, isReady: () => ready.value, currentName: () => current.value })

onMounted(load)
onBeforeUnmount(teardown)
</script>

<style scoped>
.doce { position: relative; display: flex; flex-direction: column; width: 100%; height: calc(100vh - 118px); min-height: 0; }
.dbar { display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; background: #fff;
  border: 1px solid var(--line); border-radius: 12px; margin: 0 0 8px; }
.dbar .row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.dbar .row > .btn, .dbar .row > b, .dbar .row > .sep, .dbar .row > .pick:not(.wide) { flex: none; }
.dbar .row > .mut { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dbar b { font-size: 13px; }
.dbar .mut { font-size: 12px; color: #667085; }
.grow { flex: 1; }
.sep { width: 1px; height: 18px; background: var(--line); }
.pick.wide { flex: 0 1 380px; min-width: 200px; max-width: 420px; }
.pick { height: 30px; max-width: 240px; border: 1px solid var(--line); border-radius: 8px; padding: 0 8px; background: #fff; font-size: 12px; }
.btn { height: 30px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--line); background: #fff; cursor: pointer; font-size: 12px; }
.btn:hover { border-color: #b9c3d6; }
.btn.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
.btn.ghost { color: #b42318; }
.btn[disabled] { opacity: .5; cursor: not-allowed; }
.ok { margin: 0 0 8px; padding: 6px 10px; border-radius: 8px; background: #f4fbf6; color: #1c8c46; font-size: 12px; }
.bad { margin: 0 0 8px; padding: 6px 10px; border-radius: 8px; background: #fff5f4; color: #b42318; font-size: 12px; }
.host { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column;
  border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: #f7f8fa; }
.tb { flex: none; overflow-x: auto; overflow-y: hidden; scrollbar-width: thin; scrollbar-color: #cfd6e2 transparent; }
.tb::-webkit-scrollbar { height: 6px; }
.tb::-webkit-scrollbar-thumb { background: #cfd6e2; border-radius: 3px; }
.tb :deep(*) { max-width: 100%; }
.tb :deep(.superdoc-toolbar), .tb :deep(.toolbar), .tb :deep([class*="toolbar"]) {
  flex-wrap: nowrap !important;
  overflow-x: auto !important;
  overflow-y: hidden !important;
}
.tb :deep(.superdoc-toolbar) > *, .tb :deep(.toolbar) > * { flex: 0 0 auto !important; }
.ed { flex: 1; min-height: 0; overflow: auto; }
.empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 6px; text-align: center; padding: 40px; background: #f7f8fa; }
.empty .big { font-size: 14px; color: #344054; }
.empty .mut { font-size: 12px; color: #98a2b3; max-width: 460px; line-height: 1.7; }
.mask { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: rgba(247, 248, 250, .82); color: #475467; font-size: 13px; }
code { background: #eef1f6; padding: 1px 5px; border-radius: 4px; }

.dlg { position: absolute; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center;
  background: rgba(16, 24, 40, .35); }
.card { width: 620px; max-width: calc(100% - 40px); background: #fff; border-radius: 14px; padding: 14px 16px;
  box-shadow: 0 20px 50px rgba(16, 24, 40, .22); display: flex; flex-direction: column; gap: 10px; }
.card.wide2 { width: 900px; height: 640px; max-height: calc(100% - 40px); }
.dhead { display: flex; align-items: center; gap: 8px; min-width: 0; }
.dhead b { font-size: 14px; }
.dhead .mut { font-size: 12px; color: #667085; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ta { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px;
  font-size: 13px; font-family: ui-monospace, Consolas, monospace; line-height: 1.6; resize: vertical; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { height: 24px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--line); background: #f8fafc;
  cursor: pointer; font-size: 12px; color: #344054; }
.chip:hover { border-color: var(--brand); color: var(--brand); }
.prev { min-height: 76px; display: flex; align-items: center; justify-content: center; border: 1px dashed var(--line);
  border-radius: 10px; background: #fcfdff; padding: 8px; }
.prev img { max-width: 100%; max-height: 180px; }
.dfoot { display: flex; align-items: center; gap: 10px; }
.ck { font-size: 12px; color: #475467; display: inline-flex; align-items: center; gap: 5px; }
.kframe { flex: 1; min-height: 0; width: 100%; border: 1px solid var(--line); border-radius: 10px; background: #fff; }
</style>
