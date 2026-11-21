<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { renderMath } from '../math.js'
import { loadShortcuts, matchCombo } from '../shortcuts.js'
import { flowText } from '../textflow.js'
import CanvasPane from '../components/CanvasPane.vue'
import FormulaPad from '../components/FormulaPad.vue'
import { extractFromFrame, recolorSvg } from '../ketcherSvg.js'
import Icon from '../components/Icon.vue'
import DocDesk from '../components/DocDesk.vue'

const drawer = ref('')            // '' | 'formula' | 'struct' | 'pdf' | 'subs'

const cropOpen = ref(false)
// 裁剪大窗的滚轮：真正能滚的是里面那个 .viewer（cropbody 自己是 flex 容器，scrollTop 恒为 0），
// 所以这里手动滚 viewer；deltaMode=1（按行）要乘行高，否则滚一格只动几个像素。
const onCropWheel = (ev) => {
  const box = viewerEl.value
  if (!box) return
  const k = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? box.clientHeight : 1
  if (ev.ctrlKey) { viewZoom.value = Math.min(320, Math.max(50, viewZoom.value - Math.sign(ev.deltaY) * 10)); return }
  box.scrollTop += ev.deltaY * k
  if (ev.deltaX) box.scrollLeft += ev.deltaX * k
  else if (ev.shiftKey) box.scrollLeft += ev.deltaY * k
}
const openCrop = (quiet = 0) => { if (!activeDoc.value) { drawer.value = 'pdf'; msg.value = '先导入一份 PDF'; return } if (quiet) { zoom.value = 1.5; viewZoom.value = 100 } cropOpen.value = true }
const cropInModal = async () => { await crop(); if (!err.value) cropOpen.value = false }
const cropAnswerInModal = async () => { await cropAnswer(); if (!err.value) cropOpen.value = false }
const toggleTool = (k) => { drawer.value = drawer.value === k ? '' : k; if (drawer.value) qOpen.value = false }        // 左侧功能轨
const smiles = ref('')
const smilesSvg = ref('')
const smilesMsg = ref('')

function insertFormula(p) {
  const text = (p && p.text) || ''
  if (!text) return
  const target = (p && p.target) || 'stem'
  const toAnswer = target.startsWith('answer')
  if (toAnswer && aPane.value) aPane.value.addText(text)
  else if (qPane.value) qPane.value.addText(text)
  // 不再自动收起右侧面板：收起会让画布变宽、重新缩放，视觉上整页抖一下
  msg.value = `已插入公式到${toAnswer ? '答案' : '题目'}画布`
}

async function previewSmiles() {
  smilesMsg.value = ''
  smilesSvg.value = ''
  const v = smiles.value.trim()
  if (!v) return
  try {
    const r = await api.post('/api/v2/chem/preview', { source: v, width: 360, height: 240 })
    smilesSvg.value = String(r.svg || '').replace(/^<\?xml[^>]*\?>\s*/i, '')
  } catch (e) {
    smilesMsg.value = '渲染失败：' + (e.message || e)
  }
}

// ---------- 画布标签页 + 小问 ----------
const ctab = ref('q')
const subs = ref([])
const subTarget = ref('q')
const addSub = () => subs.value.push({ hint: '' })
const delSub = (i) => subs.value.splice(i, 1)
function moveSub(i, d) {
  const j = i + d
  if (j < 0 || j >= subs.value.length) return
  const arr = subs.value
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}
function insertSub(i) {
  const pane = subTarget.value === 'q' ? qPane.value : aPane.value
  if (!pane || !pane.addText) return
  ctab.value = subTarget.value
  const sb = subs.value[i] || {}
  const hint = String(sb.hint || '').trim()
  // 之前只插入编号，输入框里的内容被丢掉；现在把「(n) + 内容」一起放进画布。
  pane.addText(hint ? `(${i + 1}) ${hint}` : `(${i + 1})`)
}

// ---------- Ketcher 手绘板（iframe + postMessage） ----------
const structTab = ref('smiles')
const structColor = ref('')
const structAlias = ref('')
const STRUCT_COLORS = [
  { c: '', n: '默认黑' }, { c: '#d21f1f', n: '红' }, { c: '#1a5fd0', n: '蓝' },
  { c: '#1c8c46', n: '绿' }, { c: '#e07b12', n: '橙' }, { c: '#7a4bd0', n: '紫' },
]
const kframe = ref(null)
const ketcherReady = ref(false)
let kseq = 0
const kpending = new Map()

function ketcherPost(payload, timeout = 20000) {
  return new Promise((resolve) => {
    const id = 'k' + (++kseq)
    kpending.set(id, resolve)
    const win = kframe.value && kframe.value.contentWindow
    if (!win) return resolve({ ok: false, error: '画板未就绪' })
    win.postMessage({ ...payload, id }, '*')
    setTimeout(() => { if (kpending.has(id)) { kpending.delete(id); resolve({ ok: false, error: '超时' }) } }, timeout)
  })
}

async function ketcherPing() {
  ketcherReady.value = false
  // Ketcher 的 30MB 包加载较慢，且只在挂载时主动 post 一次 ready；
  // 用 ping/pong 轮询确认，避免“画板已经能画，但按钮一直显示加载中”。
  for (let i = 0; i < 10 && !ketcherReady.value; i++) {
    await new Promise((r) => setTimeout(r, i === 0 ? 500 : 1200))
    const r = await ketcherPost({ type: 'ketcher:ping' }, 3000)
    if (r && r.ok) { ketcherReady.value = true; break }
  }
}

function switchStruct(k) {
  structTab.value = k
  if (k === 'draw') setTimeout(ketcherPing, 100)
}

async function ketcherSetFromInput() {
  const v = smiles.value.trim()
  if (!v) { smilesMsg.value = '先在上面填 SMILES，或直接在画板上画'; return }
  const r = await ketcherPost({ type: 'ketcher:set', molfile: v })
  smilesMsg.value = r.ok ? '已载入画板，可继续增删原子' : ('载入失败：' + (r.error || ''))
}

async function ketcherAlias() {
  const v = structAlias.value.trim()
  if (!v) { smilesMsg.value = '先输入基团缩写，例如 Ac、OAc、NO2'; return }
  const r = await ketcherPost({ type: 'ketcher:alias', alias: v })
  smilesMsg.value = r && r.ok
    ? `已把选中原子改写成 ${v}（可以继续成键，导出也不丢）`
    : ('改写失败：' + ((r && r.error) || '先在画板里点选末端原子'))
}

async function ketcherTake() {
  // 先整块画布出图：直接读同源 iframe 里的 SVG。箭头/加号/文字不是化学图元，
  // Ketcher 自己也会拒绝把带箭头的结构存成 MOL（"cannot be saved as *.MOL due to reaction arrows"），
  // 所以这里不能等 molfile —— SVG 才是唯一能完整保留画面的东西。
  const whole = extractFromFrame(kframe.value)
  const r = whole ? { ok: true, molfile: '' } : await ketcherPost({ type: 'ketcher:get' })
  let svgPayload = (whole && whole.svg) || r.svg || ''
  if (svgPayload && structColor.value) svgPayload = recolorSvg(svgPayload, structColor.value)
  if (!svgPayload) {
    smilesMsg.value = '画板里还没有结构'
    return
  }
  smilesMsg.value = '正在生成图…'
  try {
    const res = await api.post('/api/v2/chem', {
      kind: 'structure', molfile: r.molfile || null, svg: svgPayload, renderer: 'ketcher',
    })
    const svgOut = (res && res.svg) || svgPayload
    if (svgOut) smilesSvg.value = String(svgOut).replace(/^<\?xml[^>]*\?>\s*/i, '')

    // 直接把结构图插入当前画布（题目/答案按当前标签页决定）
    let inserted = false
    if (res && res.id) {
      try {
        const png = await api.post('/api/v2/chem/' + res.id + '/png')
        const pane = ctab.value === 'a' ? aPane.value : qPane.value
        if (png && png.path && pane) {
          await pane.addImage(png.path)
          inserted = true
        }
      } catch (e) {
        smilesMsg.value = '出图失败：' + (e.message || e)
      }
    }
    smilesMsg.value = '已保存结构对象 #' + (res && res.id ? res.id : '') +
      (inserted ? '，已插入' + (ctab.value === 'a' ? '答案' : '题目') + '画布' : '（可复制 SVG 或截图贴进题图）')
  } catch (e) {
    smilesMsg.value = '保存失败：' + (e.message || e)
  }
}

function onKeyEsc(e) { if (e.key === 'Escape' && cropOpen.value) cropOpen.value = false }
onMounted(() => {
  window.addEventListener('keydown', onKeyEsc)
  window.addEventListener('message', (ev) => {
    const d = ev.data || {}
    if (d.type === 'ketcher:ready') { ketcherReady.value = true; return }
    if (d.type === 'ketcher:result' && d.id && kpending.has(d.id)) {
      kpending.get(d.id)(d)
      kpending.delete(d.id)
    }
  })
})

async function insertStructure() {
  const v = smiles.value.trim()
  if (!v) return
  smilesMsg.value = ''
  try {
    const r = await api.post('/api/v2/chem', { kind: 'structure', smiles: v, renderer: 'rdkit' })
    if (r && r.svg) {
      smilesSvg.value = String(r.svg).replace(/^<\?xml[^>]*\?>\s*/i, '')
      smilesMsg.value = '已生成结构图，点「复制 SVG」或截图贴进题图'
    } else {
      smilesMsg.value = '已保存结构对象 #' + (r && r.id ? r.id : '')
    }
  } catch (e) {
    smilesMsg.value = '保存失败：' + (e.message || e)
  }
}

function copySvg() {
  if (!smilesSvg.value) return
  navigator.clipboard.writeText(smilesSvg.value)
  smilesMsg.value = 'SVG 已复制到剪贴板'
}

const route = useRoute()
const router = useRouter()

// 出题两个模式：doc = 文档（默认，Word 排版），canvas = 自由画布
const _m0 = String(route.query.mode || '')
const _savedMode = String(localStorage.getItem('tiku-author-mode') || '')
const editMode = ref(_m0 === 'canvas'
  ? 'canvas'
  : (_savedMode === 'canvas' ? 'canvas' : 'doc'))
const canvasDirty = ref(false)
const textRef = ref(null)
const textOnly = ref(false)        // 库里这道题是单题模式录的（旧数据兼容）
const textSeed = ref(null)
function onTextSaved(r) {
  editingQuestionId.value = r.id
  savedId.value = r.id
  textOnly.value = true
  msg.value = `已入库 #${r.id}`
}

function switchMode(m) {
  if (m === editMode.value) return
  const dirty = editMode.value === 'canvas' ? canvasDirty.value : false
  if (dirty && !confirm('切换模式会丢掉画布上没保存的改动，确定切换？')) return
  editMode.value = m
  canvasDirty.value = false
  if (m === 'doc') syncDocFromQ()
  localStorage.setItem('tiku-author-mode', m)
  const q = { ...route.query }
  if (m === 'canvas') q.mode = 'canvas'; else delete q.mode
  router.replace({ path: route.path, query: q })
}
const phoneHint = ref(typeof window !== 'undefined' && window.innerWidth < 820)
const hintClosed = ref(false)
const railOpen = ref(true)

// ---------- 左栏：PDF 与裁剪 ----------
const docs = ref([])
const activeDoc = ref(null)
const pageNo = ref(1)
const zoom = ref(2)
const viewZoom = ref(100)
const sel = ref(null)
const busy = ref('')
const msg = ref('')
const err = ref('')
const pageImg = ref(null)
// ── AI 拆题：试卷 + 答案 → 结构化预览 ───────────────────────────────
const qPane = ref(null)
const aPane = ref(null)
// 文档模式右侧画布和「题目画布」共用一份排版：
// 进文档模式 / 载入草稿时把题目画布推过去，文档画布上改完再回写题目画布 + 存草稿。
const docCanvasLayout = ref(null)
function syncDocFromQ() {
  if (qPane.value) docCanvasLayout.value = qPane.value.getLayout()
}
function onDocCanvasLayout(lay) {
  if (!lay) return
  docCanvasLayout.value = lay
  if (qPane.value) qPane.value.setLayout(lay)
  scheduleDraft()
}
const activePane = ref('q')
const qOpen = ref(false)
const mode = ref('crop')            // crop = 框选裁剪；text = 选择文字 / 翻译
const textLines = ref([])
const textLoading = ref(false)
const imgBox = reactive({ w: 0, h: 0 })
const readFrom = ref(1)             // AI 逐页精读：起始页
const readCount = ref(4)            // 一次连续读几页（0=读到末尾）
const readBusy = ref(false)
const readDone = ref(0)
const readTotal = ref(0)
const pageSummaries = ref([])       // [{page_no, summary, key_points, has_text}]
const answerBusy = ref(false)
const ocrAfterCrop = ref(false)      // 裁剪后直接把图片 OCR 成可编辑文字
const aiStem = ref('')               // 给 AI 读的题干文字（扫描图/图片题用）
const genCount = ref(5)              // AI 一次出几道题
const genTitle = ref('')             // 新试卷名（可空）
const genBusy = ref(false)
const paperPlan = reactive({ open: false })   // 「出题」先选 1-11 题
const paperStep = reactive({ active: false, total: 0, idx: 0, items: [], title: '', busy: false })
const viewerEl = ref(null)
const keyPoints = computed(() => (pageSummaries.value || []).flatMap(
  (s) => (s.key_points || []).map((k) => ({ ...k, page_no: s.page_no }))))
const analysisBusy = ref(false)
const analysisText = ref('')
const pop = reactive({ open: false, x: 0, y: 0, text: '', trans: '', busy: false, err: '', msg: '' })
const textCache = new Map()
let textSeq = 0

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
    let lastId = ''
    for (const f of files) {
      const fd = new FormData()
      fd.append('file', f)
      const d = await api.postForm('/api/manual/upload', fd)
      lastId = d.doc_id
      const i = docs.value.findIndex((x) => x.doc_id === d.doc_id)
      const item = { doc_id: d.doc_id, filename: d.filename, page_count: d.page_count }
      if (i >= 0) docs.value.splice(i, 1, item)
      else docs.value.unshift(item)
    }
    await loadDocs()
    activeDoc.value = docs.value.find((d) => d.doc_id === lastId) || docs.value[0] || null
    pageNo.value = 1
    sel.value = null
    msg.value = '已放入。在页面上拖框裁图；图片固定后，在图上左键拖选一小块，可裁成独立图片再复制/修改/转文字'
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}

async function loadDocs() {
  try {
    const r = await api.get('/api/manual/docs')
    docs.value = r.docs || []
    if (!activeDoc.value && docs.value.length) activeDoc.value = docs.value[0]
  } catch (e2) { /* 列表接口失败不影响主流程 */ }
}

const pdfDrag = ref(false)
// 出题四步：上一步/下一步只是把该开的抽屉、该切的画布摆好，不拦人，也不改内容
const STEPS = [
  { n: 1, t: '导入文献', d: '第 1 步：把 PDF 拖进右侧「PDF 文献」栏（题目多就直接拖整份试卷）' },
  { n: 2, t: '裁图排版', d: '第 2 步：在页面上框选题目，拖进题目画布，调位置和字号' },
  { n: 3, t: '写答案', d: '第 3 步：切到「答案」画布，裁剪答案图或手写解析' },
  { n: 4, t: '收入题库', d: '第 4 步：填出题人，点右上角保存，题目直接进本地题库' },
]
const step = ref(1)
// 答案画布：把题目画布的整体排版垫到上面，光标停在下面写解析（不用自己再复制一遍题目）
function draftAnswerOnQuestion() {
  const q = qPane.value
  const a = aPane.value
  if (!q || !a) return
  const lay = q.getLayout()
  const list = lay.blocks || []
  if (!list.length) { msg.value = '题目画布还是空的，先把题目放进来再点这个'; return }
  const bottom = list.reduce((m, b) => Math.max(m, (Number(b.y) || 0) + (b.type === 'image' ? (b.h || 0) : 40)), 0)
  const gid = `tpl-${Date.now().toString(36)}`
  const n = a.addBlocks(list, { dy: 0, gid, tip: `已把题目（${list.length} 个元素）垫在答案画布上，在它下面写解析就行` })
  if (n) {
    ctab.value = 'a'
    step.value = Math.max(step.value, 3)
    msg.value = `题目已垫在上面（已组合，可整体拖动）：题目底边在 ${Math.round(bottom)}pt，往下拖一点再在下面加解析`
  }
}

function goStep(n) {
  step.value = clamp(Math.round(n) || 1, 1, STEPS.length)
  if (step.value === 1) { drawer.value = 'pdf' }
  if (step.value === 2) { drawer.value = ''; ctab.value = 'q' }
  if (step.value === 3) { drawer.value = ''; ctab.value = 'a' }
  if (step.value === 4) { drawer.value = '' }
  msg.value = STEPS[step.value - 1].d
}

// 从电脑里把文件拖到画布上：图片直接落成图片块；PDF 存进文献，右栏立刻打开它等着框选
async function onCanvasFiles({ files, x, y }, which = 'q') {
  const pane = which === 'q' ? qPane.value : aPane.value
  if (!pane) return
  const list = Array.from(files || [])
  const imgs = list.filter((f) => /^image\//i.test(f.type || '') || /\.(png|jpe?g|webp|gif|bmp)$/i.test(f.name || ''))
  const pdfs = list.filter((f) => /pdf/i.test(f.type || '') || /\.pdf$/i.test(f.name || ''))
  if (!imgs.length && !pdfs.length) { toast('只认图片和 PDF'); return }
  err.value = ''
  for (const f of imgs) {
    busy.value = `正在放入 ${f.name}…`
    try {
      const fd = new FormData()
      fd.append('file', f, f.name || 'paste.png')
      const r = await api.postForm('/api/v2/materials/upload', fd)
      await pane.addImage(r.path, x, y)
    } catch (e2) {
      err.value = `图片「${f.name}」放不进去：${e2?.message || e2}`
    }
  }
  for (const f of pdfs) {
    busy.value = `正在读入 ${f.name}…`
    try {
      const fd = new FormData()
      fd.append('file', f, f.name || 'doc.pdf')
      const d = await api.postForm('/api/manual/upload', fd)
      const i = docs.value.findIndex((x2) => x2.doc_id === d.doc_id)
      const item = { doc_id: d.doc_id, filename: d.filename, page_count: d.page_count }
      if (i >= 0) docs.value.splice(i, 1, item)
      else docs.value.unshift(item)
      await loadDocs()
      activeDoc.value = docs.value.find((x2) => x2.doc_id === d.doc_id) || docs.value[0] || null
      pageNo.value = 1
      sel.value = null
      drawer.value = 'pdf'
      msg.value = `已放入「${d.filename}」（${d.page_count} 页）：在右边页面上拖框，就能像文献裁图一样裁进画布`
    } catch (e2) {
      err.value = `PDF「${f.name}」读不进来：${e2?.message || e2}`
    }
  }
  busy.value = ''
  if (imgs.length && !pdfs.length) msg.value = `已放入 ${imgs.length} 张图`
}

async function onPdfDrop(e) {
  pdfDrag.value = false
  const fl = Array.from(e.dataTransfer?.files || []).filter((f) => /pdf|image\//i.test(f.type || f.name))
  if (!fl.length) { toast('只支持 PDF 或图片'); return }
  const fd = new FormData()
  fl.forEach((f) => fd.append('files', f))
  try {
    await api.postForm('/api/manual/upload', fd)
    await loadDocs()
    toast(`已导入 ${fl.length} 份文献`)
  } catch (err) { toast(String(err.message || err)) }
}
async function delDoc(d) {
  if (!confirm(`删除「${d.filename}」？只删文献文件，已入库的题目不受影响。`)) return
  try {
    await api.del(`/api/manual/doc/${d.doc_id}`)
    const i = docs.value.findIndex((x) => x.doc_id === d.doc_id)
    if (i >= 0) docs.value.splice(i, 1)
    if (activeDoc.value?.doc_id === d.doc_id) {
      activeDoc.value = docs.value[0] || null
      pageNo.value = 1
      sel.value = null
      pageSummaries.value = []
    }
    msg.value = `已删除「${d.filename}」`
  } catch (e2) { err.value = e2?.message || String(e2) }
}

function turn(d) {
  if (!activeDoc.value) return
  pageNo.value = clamp(pageNo.value + d, 1, activeDoc.value.page_count)
  sel.value = null
}

let boxing = null
let boxScrollTimer = null
let lastBoxPointer = null
function localPos(e) {
  const r = pageImg.value.getBoundingClientRect()
  return {
    rx: clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 1),
    ry: clamp((e.clientY - r.top) / Math.max(1, r.height), 0, 1),
  }
}
function updateBoxFromPointer() {
  if (!boxing || !lastBoxPointer) return
  const p = localPos(lastBoxPointer)
  sel.value = {
    x0: Math.min(boxing.x0, p.rx), y0: Math.min(boxing.y0, p.ry),
    x1: Math.max(boxing.x0, p.rx), y1: Math.max(boxing.y0, p.ry),
  }
}
function stepBoxScroll() {
  const v = viewerEl.value
  if (!boxing || !v || !lastBoxPointer) return
  const r = v.getBoundingClientRect()
  const edge = 54
  let dy = 0
  if (lastBoxPointer.clientY > r.bottom - edge) {
    dy = Math.min(34, 8 + (lastBoxPointer.clientY - (r.bottom - edge)) * 0.4)
  } else if (lastBoxPointer.clientY < r.top + edge) {
    dy = -Math.min(34, 8 + ((r.top + edge) - lastBoxPointer.clientY) * 0.4)
  }
  if (!dy) return
  const before = v.scrollTop
  v.scrollTop = before + dy
  if (v.scrollTop !== before) updateBoxFromPointer()
}
function startBoxScroll() {
  if (boxScrollTimer) return
  boxScrollTimer = window.setInterval(stepBoxScroll, 30)
}
function stopBoxScroll() {
  if (boxScrollTimer) window.clearInterval(boxScrollTimer)
  boxScrollTimer = null
  lastBoxPointer = null
}
function boxDown(e) {
  if (mode.value !== 'crop') return
  if (!activeDoc.value) return
  const p = localPos(e)
  boxing = { x0: p.rx, y0: p.ry, x1: p.rx, y1: p.ry }
  sel.value = { ...boxing }
  lastBoxPointer = e
  startBoxScroll()
  window.addEventListener('pointermove', boxMove)
  window.addEventListener('pointerup', boxUp)
}
function boxMove(e) {
  if (!boxing) return
  lastBoxPointer = e
  updateBoxFromPointer()
}
function boxUp() {
  window.removeEventListener('pointermove', boxMove)
  window.removeEventListener('pointerup', boxUp)
  stopBoxScroll()
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
    if (pane) {
      let path = r.path
      try {
        const t = await api.post('/api/manual/trim', { path })
        if (t && t.path) path = t.path
      } catch (e2) { /* 裁空白失败就用原图 */ }
      await pane.addImage(path)
      if (ocrAfterCrop.value && pane.imageToText) await pane.imageToText(false)
    }
    sel.value = null
    msg.value = ocrAfterCrop.value
      ? '已裁剪并识别为文字，可直接改字号/颜色'
      : (target === 'a' ? '已裁剪答案图，放到下方「答案画布」' : '已裁剪题目图，放到上方「题目画布」')
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { busy.value = '' }
}
const crop = () => doCrop('q')
const cropAnswer = () => doCrop('a')

// ---------- 文字层：原生选中 → 复制 / 翻译 → 插入画布 ----------
function syncImgBox() {
  const el = pageImg.value
  if (!el) return
  imgBox.w = el.clientWidth
  imgBox.h = el.clientHeight
}
function setMode(m) {
  mode.value = m
  sel.value = null
  pop.open = false
  if (m === 'text') loadPageText()
  else textLines.value = []
}
async function loadPageText() {
  if (!activeDoc.value || mode.value !== 'text') { textLines.value = []; return }
  const key = `${activeDoc.value.doc_id}:${pageNo.value}`
  if (textCache.has(key)) { textLines.value = textCache.get(key); return }
  const seq = ++textSeq
  textLoading.value = true
  try {
    const r = await api.get(`/api/manual/doc/${activeDoc.value.doc_id}/page/${pageNo.value}/text`)
    if (seq !== textSeq) return
    textLines.value = r.lines || []
    textCache.set(key, textLines.value)
    if (!r.has_text) msg.value = '这一页没有文字层（扫描版 PDF），只能裁剪成图片，或用 AI 直接识别整页'
  } catch (e2) {
    if (seq === textSeq) textLines.value = []
  } finally {
    if (seq === textSeq) textLoading.value = false
  }
}
function tlStyle(l) {
  const h = imgBox.h || 0
  const fh = Math.max(1, (l.y1 - l.y0) * h)
  return {
    left: l.x0 * 100 + '%', top: l.y0 * 100 + '%',
    width: (l.x1 - l.x0) * 100 + '%', height: fh + 'px',
    fontSize: fh * 0.92 + 'px', lineHeight: fh + 'px',
  }
}
function normRects(rects) {
  const box = pageImg.value ? pageImg.value.getBoundingClientRect() : null
  if (!box || !box.width) return []
  const out = []
  for (const r of rects) {
    const x0 = Math.max(box.left, r.left)
    const x1 = Math.min(box.right, r.right)
    const y0 = Math.max(box.top, r.top)
    const y1 = Math.min(box.bottom, r.bottom)
    if (x1 - x0 < 2 || y1 - y0 < 2) continue
    out.push([(x0 - box.left) / box.width, (y0 - box.top) / box.height,
              (x1 - box.left) / box.width, (y1 - box.top) / box.height])
  }
  return out
}
function onPagePointerUp(e) {
  if (mode.value !== 'text' || !activeDoc.value) return
  const s = window.getSelection()
  // 直接用浏览器原生选区的文字，不再按矩形回抽样：按行回抽会把相邻行/整段一起带进来，
  // 造成「只选了一点却插入了很多」。
  let text = ''
  if (s && !s.isCollapsed && s.rangeCount) {
    text = s.toString()
  } else {
    const el = document.elementFromPoint(e.clientX, e.clientY)
    const lineEl = el && el.closest ? el.closest('.tl') : null
    if (lineEl) text = lineEl.textContent || ''
  }
  text = flowText(text)
  if (!text) return
  pop.open = true
  pop.text = text
  pop.trans = ''; pop.err = ''; pop.msg = ''; pop.busy = false
  pop.x = clamp(e.clientX + 12, 8, Math.max(8, window.innerWidth - 390))
  pop.y = clamp(e.clientY + 12, 8, Math.max(8, window.innerHeight - 300))
}
function closePop() {
  pop.open = false
  pop.err = ''
  pop.msg = ''
  try { window.getSelection()?.removeAllRanges() } catch (e2) {}
}
async function translatePop() {
  if (!pop.text.trim()) return
  pop.busy = true
  pop.err = ''
  pop.msg = ''
  try {
    const r = await api.post('/api/ai/translate', { text: pop.text, target: '简体中文' })
    pop.trans = flowText(r.text || '')
    if (!pop.trans) pop.msg = '模型没有返回译文，请重试'
  } catch (e2) {
    pop.err = e2?.message || String(e2)
  } finally {
    pop.busy = false
  }
}
async function copyPop(text) {
  try {
    await navigator.clipboard.writeText(text || '')
    pop.msg = '已复制到剪贴板'
  } catch (e2) {
    pop.msg = '浏览器不允许直接复制，请在文本框里全选复制'
  }
}
function insertPop(where, useTrans) {
  const text = ((useTrans ? pop.trans : pop.text) || '').trim()
  if (!text) return
  const pane = where === 'a' ? aPane.value : qPane.value
  if (!pane) return
  pane.addTextFlow(text)
  msg.value = `已插入${where === 'a' ? '答案' : '题目'}画布，可继续排版`
  closePop()
}

// ---------- AI：逐页精读 → 每页摘要 → 机理/重点 ----------
const keyLines = computed(() => keyPoints.value.filter((p) => p.page_no === pageNo.value))
function keyStyle(p) {
  return { top: Math.max(0, p.y * 100 - 1.2) + '%', height: '2.4%' }
}
function gotoKeyPoint(p) {
  if (p.page_no && p.page_no !== pageNo.value) pageNo.value = p.page_no
  if (mode.value !== 'text') setMode('text')
}
async function translateSelection() {
  if (mode.value !== 'text') setMode('text')
  const s = window.getSelection()
  let text = s && !s.isCollapsed ? String(s.toString() || '').trim() : ''
  if (!text && pop.open) text = pop.text
  if (!text) {
    msg.value = '先用鼠标在 PDF 上拖选要翻译的英文（可跨行），松手会自动弹出翻译框；再点「翻译成中文」'
    return
  }
  pop.open = true
  pop.text = text
  pop.trans = ''
  pop.err = ''
  pop.msg = ''
  pop.x = clamp(window.innerWidth / 2 - 190, 8, Math.max(8, window.innerWidth - 400))
  pop.y = 120
  await translatePop()
}

async function readPages() {
  if (!activeDoc.value || readBusy.value) return
  const doc = activeDoc.value
  const total = Math.max(1, doc.page_count)
  const from = clamp(Number(readFrom.value) || 1, 1, total)
  readFrom.value = from
  const count = Number(readCount.value) || 0
  const to = count <= 0 ? total : Math.min(total, from + count - 1)
  readBusy.value = true
  readDone.value = 0
  readTotal.value = to - from + 1
  err.value = ''
  try {
    for (let p = from; p <= to; p++) {
      msg.value = `AI 逐页精读：第 ${p} 页（${readDone.value + 1}/${readTotal.value}）…`
      try {
        const r = await api.post('/api/ai/summarize-page', { doc_id: doc.doc_id, page_no: p })
        const rest = pageSummaries.value.filter((s) => s.page_no !== p)
        rest.push({
          page_no: p,
          summary: r.summary || '',
          key_points: Array.isArray(r.key_points) ? r.key_points : [],
          has_text: !!r.has_text,
          message: r.message || '',
        })
        rest.sort((a, b) => a.page_no - b.page_no)
        pageSummaries.value = rest
      } catch (e2) {
        err.value = `第 ${p} 页精读失败：${e2?.message || e2}`
        break
      }
      readDone.value += 1
      await nextTick()
    }
    const textPages = pageSummaries.value.filter((s) => s.has_text).length
    if (to < total) readFrom.value = to + 1
    msg.value = `已读第 ${from}–${to} 页；${textPages} 页有摘要。黄线是 AI 标出的机理/重点解释。`
      + (to < total ? `再点一次「开始逐页精读」会从第 ${to + 1} 页继续。` : '已经读到最后一页。')
  } finally {
    readBusy.value = false
  }
}
function clearAiRead() {
  pageSummaries.value = []
  readDone.value = 0
  readTotal.value = 0
  msg.value = '已清除 AI 逐页摘要和重点标记'
}

watch(() => activeDoc.value?.doc_id, () => {
  pageSummaries.value = []
  textLines.value = []
  nextTick(() => { syncImgBox(); loadPageText() })
})
watch(pageNo, () => {
  const total = activeDoc.value?.page_count || 1
  if (!Number.isFinite(pageNo.value)) pageNo.value = 1
  if (pageNo.value < 1) pageNo.value = 1
  if (pageNo.value > total) pageNo.value = total
  sel.value = null
  pop.open = false
  nextTick(() => { syncImgBox(); loadPageText() })
})
watch(mode, () => { pop.open = false; loadPageText() })

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
  book_id: null, unit_id: null, q_type: '综合', difficulty: 2, tags: [],
  source_title: '', source_citation: '', locator: '', status: 'confirmed', answer: '',
  author: localStorage.getItem('tiku-author-name') || '',
  topic: 'organic', year: null,
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

// ---------- 入库：出的题直接进本地题库（D:\化学竞赛题库） ----------
const bankBase = ref('http://127.0.0.1:8080')
async function loadBankBase() {
  try {
    const s = await api.get('/api/chemy/status')
    if (s && s.base) bankBase.value = s.base
  } catch (e2) { /* 题库没起来也不挡出题 */ }
}
function layoutText(lay) {
  const blocks = (lay && lay.blocks ? lay.blocks : []).filter((b) => b.type === 'text' && String(b.text || '').trim())
  blocks.sort((a, b) => (Number(a.y) || 0) - (Number(b.y) || 0) || (Number(a.x) || 0) - (Number(b.x) || 0))
  return blocks.map((b) => String(b.text || '').trim()).join('\n')
}
async function layoutImages(lay) {
  const out = []
  for (const b of (lay && lay.blocks ? lay.blocks : []).filter((x) => x.type === 'image' && x.path)) {
    try {
      const r = await fetch(imgUrl(b.path))
      if (!r.ok) continue
      const blob = await r.blob()
      const data = await new Promise((res) => {
        const fr = new FileReader()
        fr.onload = () => res(String(fr.result || ''))
        fr.onerror = () => res('')
        fr.readAsDataURL(blob)
      })
      if (data) out.push({ name: String(b.path).split('/').pop() || 'figure.png', data })
    } catch (e2) { /* 单张图失败不影响入库 */ }
  }
  return out
}
function rememberAuthor() {
  try { localStorage.setItem('tiku-author-name', String(form.author || '').trim()) } catch (e2) { /* 隐私模式下写不了也不影响出题 */ }
}
function requireAuthor() {
  const a = String(form.author || '').trim()
  if (!a) {
    err.value = '请先在右下角「入库信息」里填出题人：入库的题目必须能追到是谁出的'
    auxOpen.value = true
    return ''
  }
  localStorage.setItem('tiku-author-name', a)
  return a
}
async function publishToBank() {
  const ql = qPane.value ? qPane.value.getLayout() : null
  if (!ql || !ql.blocks.length) { err.value = '题目画布是空的：先裁剪或写点文字'; return null }
  const author = requireAuthor()
  if (!author) return null
  const al = aPane.value ? aPane.value.getLayout() : null
  const stem = layoutText(ql)
  let answer = layoutText(al) || form.answer || ''
  // 题库的答案只有文字，没有图片字段：答案画布上的图并进题图，答案文本里写清楚是第几张
  const qImgs = await layoutImages(ql)
  const aImgs = await layoutImages(al)
  const images = [...qImgs, ...aImgs]
  if (aImgs.length) {
    const notes = aImgs.map((_, i) => `答案配图见题图 ${qImgs.length + i + 1}`)
    answer = [answer, ...notes].filter(Boolean).join(String.fromCharCode(10))
  }
  const firstLine = (stem.split('\n')[0] || '').slice(0, 40)
  return api.post('/api/chemy/publish', {
    title: form.source_title || firstLine || '未命名题目',
    topic: form.topic || 'organic',
    year: form.year ? Number(form.year) : null,
    stem, answer, author, images, publish: true,
    paper_title: `神奋出题 · ${author}`,
  })
}

const TOPIC_CN = {
  organic: '有机化学', polymer: '高分子化学', crystal: '晶体化学',
  'inorg-struct': '无机综合与结构推断', equation: '方程式与元素化学', thermo: '热力学与化学平衡',
  electro: '电化学', kinetics: '动力学', analytical: '分析化学',
}
function topicName(t) { return TOPIC_CN[String(t || 'organic')] || '有机化学' }

const inbank = reactive({ open: false, author: '', topic: 'organic', year: '', title: '', busy: false, err: '', then: 'save' })

function openInbank(then = 'save') {
  inbank.author = String(form.author || localStorage.getItem('tiku-author-name') || '').trim()
  inbank.topic = String(form.topic || 'organic')
  inbank.year = form.year ? String(form.year) : ''
  inbank.title = String(form.source_title || '')
  inbank.err = ''
  inbank.busy = false
  inbank.then = then
  inbank.open = true
}

/** 弹窗里确认：把填写的东西写回 form，再真正入库 */
async function confirmInbank() {
  const a = String(inbank.author || '').trim()
  if (!a) { inbank.err = '出题人必填：入库的题目要能追到是谁出的'; return }
  form.author = a
  form.topic = inbank.topic
  form.year = inbank.year === '' ? null : Number(inbank.year)
  form.source_title = String(inbank.title || '')
  rememberAuthor()
  inbank.busy = true
  inbank.err = ''
  try {
    if (inbank.then === 'export') await saveAndExport(true)
    else await save(true)
    if (!err.value) inbank.open = false
    else inbank.err = err.value
  } catch (e2) {
    inbank.err = e2?.message || String(e2)
  } finally { inbank.busy = false }
}

async function save(fromDialog = false) {
  if (!fromDialog && !String(form.author || '').trim()) { openInbank('save'); return }
  saving.value = true
  err.value = ''
  msg.value = ''
  try {
    const r = await publishToBank()
    if (!r) return
    savedId.value = (r.qids && r.qids[0]) || ''
    editingQuestionId.value = ''
    canvasDirty.value = false
    clearDraft()
    msg.value = `已存入本地题库：批次 ${r.batch}，导入了 ${r.count} 题，已发布 ${r.published} 题`
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { saving.value = false }
}

// 导出 = 先入库，再按题库的方式出打印页（本项目的导出器不用）
async function saveAndExport(fromDialog = false) {
  if (!fromDialog && !String(form.author || '').trim()) { openInbank('export'); return }
  saving.value = true
  err.value = ''
  msg.value = ''
  try {
    const r = await publishToBank()
    if (!r) return
    const qid = (r.qids && r.qids[0]) || ''
    savedId.value = qid
    canvasDirty.value = false
    clearDraft()
    if (!qid) { err.value = '题库没给出题号，导出没打开'; return }
    const url = `${bankBase.value}/print/q/${encodeURIComponent(qid)}?answers=1`
    window.open(url, '_blank')
    msg.value = `已存入本地题库（批次 ${r.batch}）→ 已打开题库的打印页；要整卷就在题库里勾选后打印`
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { saving.value = false }
}

async function genAnalysis() {
  if (!savedId.value) { msg.value = '先点「保存到题库」，AI 才能把解析写回题目'; return }
  const stem = stemText()
  if (!stem && !form.answer.trim()) { err.value = '画布里还没有题干文字：先用「＋ 文本」写题干，再生成解析'; return }
  analysisBusy.value = true
  err.value = ''
  try {
    const r = await api.post('/api/ai/analysis', {
      stem: stem || form.answer, answer: form.answer, q_type: form.q_type,
    })
    analysisText.value = r.text || ''
    if (!analysisText.value) { msg.value = '模型没有返回解析，可以稍后重试'; return }
    await api.patch(`/api/questions/${savedId.value}`, { analysis: analysisText.value })
    msg.value = `AI 解析已生成，并写入题库 #${savedId.value}`
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { analysisBusy.value = false }
}

function stemText() {
  const ql = qPane.value ? qPane.value.getLayout() : null
  const canvas = (ql?.blocks || []).filter((b) => b.type === 'text').map((b) => b.text || '').join('\n').trim()
  return canvas || aiStem.value.trim()
}
function stemImage() {
  const ql = qPane.value ? qPane.value.getLayout() : null
  const img = (ql?.blocks || []).find((b) => b.type === 'image' && b.path)
  return img ? img.path : ''
}
async function genAnswer() {
  const stem = stemText()
  const imagePath = stem ? '' : stemImage()
  if (!stem && !imagePath) {
    err.value = '题目画布还没有题干：先裁剪题图、写文字块，或在「题干文字」里粘贴题目'
    return
  }
  answerBusy.value = true
  err.value = ''
  msg.value = imagePath ? '题目是图片：AI 先用本机 OCR 读题，再解题并复核（约 30-60 秒）…' : 'AI 正在解题并自动复核（约 30-60 秒）…'
  try {
    const r = await api.post('/api/ai/answer', {
      stem, q_type: form.q_type, answer_hint: form.answer.trim(), image_path: imagePath || null,
    })
    const ans = (r.answer || r.text || '').trim()
    const ana = (r.analysis || '').trim()
    const refs = (r.references || []).map((x) => String(x).trim()).filter(Boolean)
    const refsText = refs.length ? refs.map((x, i) => `[${i + 1}] ${x}`).join('\n') : '无参考文献'
    if (!ans) {
      msg.value = '模型没有返回答案，可以稍后重试'
      return
    }
    let packed = ans
    if (ana) packed += `\n\n【解析】\n${ana}`
    packed += `\n\n【参考文献】\n${refsText}`
    form.answer = packed
    if (aPane.value) aPane.value.addText(packed)
    analysisText.value = (ana ? `【解析】\n${ana}\n\n` : '') + `【参考文献】\n${refsText}`
    const secs = r.usage?.elapsed_ms ? `，用时约 ${Math.round(r.usage.elapsed_ms / 1000)} 秒` : ''
    msg.value = `AI 已把答案、解析和参考文献填入「参考答案」并自动排到答案画布${r.usage?.verified ? '（已自动复核）' : ''}${secs}`
      + `${r.ocr_used ? '；题目图已用本机 OCR 识别，若有错字请顺手改' : ''}，可直接修改`
  } catch (e2) {
    err.value = e2?.message || String(e2)
    msg.value = ''
  } finally { answerBusy.value = false }
}
function answerToCanvas() {
  const text = form.answer.trim()
  if (!text) { msg.value = '参考答案还是空的：先点「AI 生成答案」或自己写'; return }
  if (aPane.value) aPane.value.addText(text)
  msg.value = '已把参考答案插入答案画布；双击文字块即可修改字号、颜色或译文'
}

// AI 出几道题：只进试卷，不写题库；一次生成 = 一套可直接命名/导出/融合的试卷
async function genPaper() {
  if (genBusy.value) return
  err.value = ''
  msg.value = ''
  const summary = (pageSummaries.value || [])
    .filter((s) => s.page_no === pageNo.value)
    .map((s) => s.summary || (s.message || ''))
    .filter(Boolean).join('\n')
  const src = [aiStem.value.trim(), summary,
    form.answer.trim() ? `参考答案参考：\n${form.answer.trim()}` : ''].filter(Boolean).join('\n\n')
  if (!src && !activeDoc.value) {
    err.value = '先在「题干文字」里粘贴题目/素材，或从左侧选一份 PDF'
    return
  }
  genBusy.value = true
  try {
    const body = { count: genCount.value, title: genTitle.value || undefined, q_type: form.q_type }
    if (src) body.source = src
    else body.doc_id = activeDoc.value.doc_id
    const r = await api.post('/api/papers/ai', body)
    msg.value = `AI 已出 ${r.items.length} 题并直接生成试卷「${r.title}」，可在试卷页改名、导出或融合`
    router.push(`/papers/${r.id}`)
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally {
    genBusy.value = false
  }
}

// 分步出题：先选 1-11 道 → 一次生成题目快照（不进库）→ 逐题显示在画布上改 → 「下一题」
function openPaperPlan() {
  err.value = ''
  msg.value = ''
  paperPlan.open = true
}

function paperSourceText() {
  const summary = (pageSummaries.value || [])
    .filter((s) => s.page_no === pageNo.value)
    .map((s) => s.summary || (s.message || ''))
    .filter(Boolean).join('\n')
  return [aiStem.value.trim(), summary,
    form.answer.trim() ? `参考答案参考：\n${form.answer.trim()}` : ''].filter(Boolean).join('\n\n')
}

function paperCanvasText(pane) {
  if (!pane) return ''
  const layout = pane.getLayout ? pane.getLayout() : {}
  return (layout.blocks || []).filter((b) => b.type === 'text').map((b) => b.text || '').join('\n\n').trim()
}

function showPaperItem(i) {
  if (!paperStep.items[i]) return
  paperStep.idx = i
  const snap = paperStep.items[i].snapshot || {}
  if (qPane.value) {
    qPane.value.clear()
    if (snap.stem) qPane.value.addText(snap.stem)
  }
  if (aPane.value) {
    aPane.value.clear()
    const ans = [snap.answer || '', snap.analysis ? `\n【解析】\n${snap.analysis}` : ''].join('').trim()
    if (ans) aPane.value.addText(ans)
  }
}

function capturePaperItem() {
  const it = paperStep.items[paperStep.idx]
  if (!it) return
  const stem = paperCanvasText(qPane.value)
  const raw = paperCanvasText(aPane.value)
  if (stem) it.snapshot.stem = stem
  const parts = raw.split(/\n*【解析】\n*/)
  it.snapshot.answer = (parts[0] || '').trim()
  if (parts.length > 1) it.snapshot.analysis = parts.slice(1).join('\n【解析】\n').trim()
}

let paperJobTimer = null
const paperJobId = ref('')

function stopPaperJobPoll() {
  if (paperJobTimer) { clearInterval(paperJobTimer); paperJobTimer = null }
}

function applyPaperJob(r) {
  paperStep.items = r.items || []
  paperStep.total = paperStep.items.length || Number(r.count || 1)
  paperStep.title = r.title || genTitle.value || `AI 命题 · ${paperStep.total} 题`
  paperStep.active = true
  showPaperItem(0)
  msg.value = `已生成 ${paperStep.total} 题：第 1 题显示在画布上，改完点右上角「出下一个题」`
}

async function pollPaperJob(jid) {
  try {
    const r = await api.get('/api/papers/ai/jobs/' + jid)
    if (r.status === 'running') {
      msg.value = 'AI 在后台命题中…可以继续改别的题，切走页面也不会中断'
      return
    }
    stopPaperJobPoll()
    paperStep.busy = false
    paperJobId.value = ''
    localStorage.removeItem('tiku-paper-job')
    if (r.status === 'error') throw new Error(r.error || 'AI 命题失败')
    applyPaperJob(r)
  } catch (e2) {
    stopPaperJobPoll()
    paperStep.busy = false
    paperJobId.value = ''
    localStorage.removeItem('tiku-paper-job')
    err.value = e2?.message || String(e2)
  }
}

function resumePaperJob() {
  let jid = ''
  try { jid = localStorage.getItem('tiku-paper-job') || '' } catch (e2) {}
  if (!jid) return
  paperJobId.value = jid
  paperStep.busy = true
  msg.value = '继续等待后台 AI 命题…'
  pollPaperJob(jid)
  paperJobTimer = setInterval(() => pollPaperJob(jid), 2500)
}

async function pickPaperCount(n) {
  paperPlan.open = false
  stopPaperJobPoll()
  paperStep.busy = true
  paperStep.total = n
  paperStep.idx = 0
  paperStep.items = []
  paperStep.active = false
  err.value = ''
  msg.value = 'AI 已转到后台命题，约 30-60 秒…可以继续做别的事'
  try {
    const src = paperSourceText()
    const body = { count: n, title: genTitle.value || undefined, q_type: form.q_type }
    if (src) body.source = src
    else if (activeDoc.value) body.doc_id = activeDoc.value.doc_id
    else throw new Error('先在「题干文字」里粘贴题目/素材，或从左侧选一份 PDF')
    const r = await api.post('/api/papers/ai/jobs', body)
    paperJobId.value = r.job_id
    try { localStorage.setItem('tiku-paper-job', r.job_id) } catch (e2) {}
    pollPaperJob(r.job_id)
    paperJobTimer = setInterval(() => pollPaperJob(r.job_id), 2500)
  } catch (e2) {
    err.value = e2?.message || String(e2)
    paperStep.busy = false
  }
}

async function nextPaperQuestion() {
  if (paperStep.busy) return
  capturePaperItem()
  if (paperStep.idx + 1 < paperStep.total) {
    showPaperItem(paperStep.idx + 1)
    msg.value = `第 ${paperStep.idx + 1}/${paperStep.total} 题；可以继续改，改完再点「出下一个题」`
    return
  }
  paperStep.busy = true
  try {
    const r = await api.post('/api/papers/ai/build', {
      title: paperStep.title,
      items: paperStep.items.map((x) => ({ snapshot: x.snapshot, score: x.score })),
    })
    paperStep.active = false
    msg.value = `已生成试卷「${r.title}」，正在打开…`
    router.push(`/papers/${r.id}`)
  } catch (e2) {
    err.value = e2?.message || String(e2)
  } finally { paperStep.busy = false }
}

function cancelPaperStep() {
  paperStep.active = false
  paperStep.items = []
  paperStep.total = 0
  msg.value = '已退出分步出题；已生成的题目没有写入题库'
}

function resetAll() {
  if (qPane.value) qPane.value.clear()
  if (aPane.value) aPane.value.clear()
  resetHist('q')
  resetHist('a')
  editingQuestionId.value = null
  savedId.value = null
  analysisText.value = ''
  err.value = ''
  clearDraft()
  msg.value = '画布已清空，可以出下一题'
}

async function loadExisting(id) {
  try {
    const d = await api.get(`/api/authoring/compose/${id}`)
    textOnly.value = (d.layout || {}).kind === 'text'
    if (qPane.value && !textOnly.value) qPane.value.setLayout(d.layout)
    if (qPane.value && textOnly.value) qPane.value.setLayout({ kind: 'canvas', blocks: [] })
    if (aPane.value) aPane.value.setLayout(d.answer_layout || { blocks: [] })
    resetHist('q')
    resetHist('a')
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
    analysisText.value = d.analysis || ''
    aiStem.value = d.stem_text || ''
    msg.value = `已载入 #${d.id}，可继续编辑`
  } catch (e) {
    err.value = e?.message || String(e)
  }
}

function goBank() { router.push('/bank') }

// ---------- 快捷键：作用在「当前」画布 ----------
function paneOf(key) { return key === 'a' ? aPane.value : qPane.value }

// ---------- 撤销 / 重做：每个画布各自一份快照栈 ----------
const histVer = ref(0)
const hist = { q: { stack: [], idx: -1, t: null }, a: { stack: [], idx: -1, t: null } }
let histLock = false
function paneHist(key) { return key === 'a' ? hist.a : hist.q }

function takeSnap(key) {
  const p = paneOf(key)
  if (!p || histLock || !p.getLayout) return
  let s = ''
  try { s = JSON.stringify(p.getLayout()) } catch (e) { return }
  const h = paneHist(key)
  if (h.idx >= 0 && h.stack[h.idx] === s) return
  h.stack = h.stack.slice(0, h.idx + 1)
  h.stack.push(s)
  if (h.stack.length > 80) h.stack.shift()
  h.idx = h.stack.length - 1
  histVer.value++
}

// 画布每次 emit('changed') 都排一次快照；连续拖动只在停手 400ms 后记一帧
function onPaneChanged(key) {
  if (histLock) return
  canvasDirty.value = true
  const h = paneHist(key)
  clearTimeout(h.t)
  h.t = setTimeout(() => { h.t = null; takeSnap(key) }, 400)
  scheduleDraft()
}

// ───────── 草稿：出到一半关掉页面也不丢 ─────────
const draftId = ref(localStorage.getItem('tiku-draft-id') || '')
const draftAt = ref('')
const draftFound = ref(null)
let draftTimer = 0

function firstLine(lay) {
  const blocks = (lay && lay.blocks) || []
  const t = blocks.find(b => b && b.type === 'text' && String(b.text || '').trim())
  return t ? String(t.text).trim().split('\n')[0].slice(0, 40) : ''
}

function draftSnapshot() {
  const q = qPane.value ? qPane.value.getLayout() : null
  const a = aPane.value ? aPane.value.getLayout() : null
  const n = ((q && q.blocks) || []).length + ((a && a.blocks) || []).length
  if (!n) return null
  return {
    id: draftId.value, mode: editMode.value,
    title: form.source_title || firstLine(q) || '未命名草稿',
    payload: {
      q_layout: q, a_layout: a, step: step.value, ctab: ctab.value,
      form: { author: form.author, topic: form.topic, year: form.year, answer: form.answer, source_title: form.source_title },
    },
  }
}

function scheduleDraft() {
  clearTimeout(draftTimer)
  draftTimer = setTimeout(saveDraftNow, 3000)
}

async function saveDraftNow() {
  const snap = draftSnapshot()
  if (!snap) return
  try {
    const r = await api.post('/api/drafts', snap)
    draftId.value = r.id
    draftAt.value = r.updated_at
    try { localStorage.setItem('tiku-draft-id', r.id) } catch (e2) { /* 隐私模式忽略 */ }
  } catch (e2) { /* 自动保存失败不打扰，下次改动再试 */ }
}

async function findDraft() {
  try {
    const r = await api.get('/api/drafts')
    const list = r.drafts || []
    if (!list.length) return
    const mine = list.find(d => d.id === draftId.value) || list[0]
    if ((mine.blocks || 0) > 0) draftFound.value = mine
  } catch (e2) { /* 后端没起来就不提示 */ }
}

async function loadDraftById(id) {
  const d = await api.get('/api/drafts/' + id)
  const pl = d.payload || {}
  if (pl.q_layout && qPane.value) qPane.value.setLayout(pl.q_layout)
  if (pl.a_layout && aPane.value) aPane.value.setLayout(pl.a_layout)
  syncDocFromQ()
  const f = pl.form || {}
  form.author = f.author || form.author
  form.topic = f.topic || form.topic
  form.year = (f.year === null || f.year === undefined) ? form.year : f.year
  form.answer = f.answer || form.answer
  form.source_title = f.source_title || form.source_title
  step.value = Math.max(1, Math.min(STEPS.length, Number(pl.step) || 1))
  draftId.value = id
  try { localStorage.setItem('tiku-draft-id', id) } catch (e2) {}
  resetHist('q')
  resetHist('a')
  canvasDirty.value = false
  draftFound.value = null
  msg.value = `已继续草稿「${d.title || ''}」（${d.updated_at || ''}），改完点保存就进本地题库`
}

async function useDraft() {
  const found = draftFound.value
  if (!found) return
  draftFound.value = null
  try {
    await loadDraftById(found.id)
  } catch (e2) { /* 草稿坏了就当没有 */ }
}

function dropDraft() {
  const found = draftFound.value
  draftFound.value = null
  if (found) api.delete('/api/drafts/' + found.id).catch(() => {})
}

function clearDraft() {
  const old = draftId.value
  draftId.value = ''
  draftAt.value = ''
  try { localStorage.removeItem('tiku-draft-id') } catch (e2) {}
  if (old) api.delete('/api/drafts/' + old).catch(() => {})
}

function flushHist(key) {
  const h = paneHist(key)
  if (h.t) { clearTimeout(h.t); h.t = null; takeSnap(key) }
}

function applySnap(key, s) {
  const p = paneOf(key)
  if (!p || s == null || !p.setLayout) return
  const h = paneHist(key)
  if (h.t) { clearTimeout(h.t); h.t = null }
  histLock = true
  try { p.setLayout(JSON.parse(s)) } catch (e) { histLock = false; return }
  setTimeout(() => { histLock = false }, 120)
}

function undo() {
  const key = activePane.value
  flushHist(key)
  const h = paneHist(key)
  if (h.idx <= 0) { msg.value = '已经回到最早的一步了'; return }
  h.idx -= 1
  applySnap(key, h.stack[h.idx])
  histVer.value++
  msg.value = `已撤销（${h.idx + 1}/${h.stack.length}）`
}

function redo() {
  const key = activePane.value
  flushHist(key)
  const h = paneHist(key)
  if (h.idx < 0 || h.idx >= h.stack.length - 1) { msg.value = '没有可重做的操作'; return }
  h.idx += 1
  applySnap(key, h.stack[h.idx])
  histVer.value++
  msg.value = `已重做（${h.idx + 1}/${h.stack.length}）`
}

function resetHist(key) {
  const h = paneHist(key)
  if (h.t) { clearTimeout(h.t); h.t = null }
  h.stack = []
  h.idx = -1
  takeSnap(key)
  histVer.value++
}

const canUndo = computed(() => { void histVer.value; return paneHist(activePane.value).idx > 0 })
const canRedo = computed(() => { void histVer.value; const h = paneHist(activePane.value); return h.idx >= 0 && h.idx < h.stack.length - 1 })

function onKeys(e) {
  if (editMode.value === 'text') return   // 文本模式有自己的一套快捷键
  const t = e.target || {}
  const tag = (t.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || t.isContentEditable) return
  const p = paneOf(activePane.value)
  if (!p) return
  const m = loadShortcuts()
  const k = (e.key || '').toLowerCase()
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (k === 'z' || k === 'y')) {
    if (k === 'y' || e.shiftKey) redo(); else undo()
    e.preventDefault(); return
  }
  if (matchCombo(e, 'copy', m)) { p.copySel(); e.preventDefault(); return }
  if (matchCombo(e, 'paste', m)) { p.pasteClip(); e.preventDefault(); return }
  if (matchCombo(e, 'group', m)) { p.groupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'ungroup', m)) { p.ungroupSel(); e.preventDefault(); return }
  if (matchCombo(e, 'rotate', m)) { p.rotateSel(); e.preventDefault(); return }
  if (matchCombo(e, 'del', m) || k === 'backspace') { p.removeSelected(); e.preventDefault(); return }
  if ((e.ctrlKey || e.metaKey) && k === 'a') { p.selectAll(); e.preventDefault(); return }
  if (matchCombo(e, 'esc', m)) { if (pop.open) closePop(); p.clearSel(); return }
  const step = e.shiftKey ? 10 : 1
  const delta = { arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step] }[k]
  if (delta) { p.nudge(delta[0], delta[1]); e.preventDefault() }
}

function panViewer(dir) {
  if (viewerEl.value) viewerEl.value.scrollLeft += dir * 260
}

watch(viewZoom, () => nextTick(syncImgBox))

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader()
    fr.onload = () => resolve(String(fr.result || ''))
    fr.onerror = () => reject(new Error('读取剪贴板图片失败'))
    fr.readAsDataURL(file)
  })
}

async function onPaste(e) {
  const t = e.target
  if (t && t.closest && t.closest('input, textarea, [contenteditable="true"]')) return
  const items = Array.from(e.clipboardData?.items || [])
  const img = items.find((it) => String(it.type || '').startsWith('image/'))
  if (img) {
    const file = img.getAsFile()
    if (!file) return
    e.preventDefault()
    try {
      const dataUrl = await fileToDataUrl(file)
      const r = await api.post('/api/manual/image', { data_url: dataUrl })
      const p = ctab.value === 'a' ? aPane.value : qPane.value
      if (p) p.addImage(r.path)
      msg.value = '已把剪贴板图片插入画布；可拖动、缩放或固定'
    } catch (e2) { err.value = e2?.message || String(e2) }
    return
  }
  const text = e.clipboardData?.getData('text/plain') || ''
  if (text.trim()) {
    e.preventDefault()
    const target = ctab.value === 'a' ? aPane.value : qPane.value
    if (target) target.addTextFlow(flowText(text))
    msg.value = '已把剪贴板文字插入画布（自动连句）'
  }
}

onMounted(async () => {
  await loadMeta()
  await loadDocs()
  loadBankBase()
  try {
    const saved = JSON.parse(localStorage.getItem('tiku-aux-pos') || 'null')
    if (saved && Number.isFinite(saved.x)) { fpos.x = saved.x; fpos.y = saved.y }
    else { fpos.x = Math.max(16, window.innerWidth - 72); fpos.y = Math.round(window.innerHeight / 2 - 20) }
  } catch (e2) {}
  setTimeout(clampF, 0)
  window.addEventListener('keydown', onKeys)
  window.addEventListener('paste', onPaste)
  window.addEventListener('resize', syncImgBox)
  const qid = Number(route.query.id || 0)
  if (qid) await nextTick().then(() => loadExisting(qid))
  resumePaperJob()
  if (!qid) { resetHist('q'); resetHist('a') }
  if (!qid) findDraft()
  const did = String(route.query.draft || '')
  if (did) { try { await loadDraftById(did) } catch (e2) { /* 草稿不存在就算了 */ } }
})
onBeforeUnmount(() => {
  if (canvasDirty.value) saveDraftNow()
  clearTimeout(draftTimer)
  stopPaperJobPoll()
  window.removeEventListener('keydown', onKeys)
  window.removeEventListener('paste', onPaste)
  window.removeEventListener('resize', syncImgBox)
})
</script>

<template>
  <div v-if="phoneHint && !hintClosed" class="phonehint">
    手机屏幕画布比较挤，建议横屏或在电脑上排版；这里可以先裁图和预览。
    <button class="ghost sm" @click="hintClosed = true">知道了</button>
  </div>

  <div v-if="draftFound" class="draftbar">
    <span>发现未完成的草稿：<b>{{ draftFound.title }}</b>（{{ draftFound.updated_at }}，{{ draftFound.blocks }} 个元素）。要继续吗？</span>
    <button class="ghost sm" @click="useDraft">继续编辑</button>
    <button class="ghost sm" @click="dropDraft">丢弃</button>
  </div>

  <div class="writer wps" :class="{ 'left-off': !qOpen, 'focus-mode': true }">

    <div class="wtools" v-if="editMode !== 'doc'">
      <button class="wt" :class="{ on: drawer === 'pdf' }" title="PDF 文献（导入 / 预览 / 拖入）" @click="toggleTool('pdf')"><Icon name="pdf" /></button>
      <button class="wt" :disabled="!activeDoc" title="放大并裁剪（框选题目或答案）" @click="openCrop"><Icon name="crop" /></button>
      <button class="wt" :class="{ on: drawer === 'formula' }" title="公式面板（上下标 / 分式 / 希腊字母）" @click="toggleTool('formula')"><Icon name="formula" /></button>
      <button class="wt" :class="{ on: drawer === 'struct' }" title="结构式（SMILES/MOL 出图，或手绘）" @click="toggleTool('struct')"><Icon name="structure" /></button>
      <span class="wsep" />
      <button class="wt" title="新建一题（清空画布）" @click="resetAll"><Icon name="newdoc" /></button>
      <span class="wsep" />
      <button class="wt" :disabled="!canUndo" title="撤销（Ctrl+Z）" @click="undo"><Icon name="undo" /></button>
      <button class="wt" :disabled="!canRedo" title="重做（Ctrl+Shift+Z）" @click="redo"><Icon name="redo" /></button>
      <span class="wsep" />
      <button class="wt go" :disabled="saving" :title="'保存：直接存进本地题库（要填出题人）'" @click="save()">
        <Icon name="save" />
      </button>
      <button class="wt" :disabled="saving" title="导出：先存进本地题库，再打开题库的打印页" @click="saveAndExport()">
        <Icon name="download" />
      </button>
      <span v-if="draftAt" class="draftat" title="出题过程自动留草稿，关掉页面也不会丢">草稿已存 {{ draftAt.slice(11) }}</span>
      <span class="wsp" />
      <div class="modesw">
        <button :class="{ on: editMode === 'doc' }" title="文档：直接用 Word 排版（表格、修订、页眉页脚都保留）"
                @click="switchMode('doc')">文档</button>
        <button :class="{ on: editMode === 'canvas' }" title="画布：自由拖图、裁剪、结构式" @click="switchMode('canvas')">画布</button>
      </div>
      <span class="whint">{{ editMode === 'doc'
        ? '文档模式：像 Word 一样排版，改完自动保存'
        : (drawer ? '右侧面板打开时，画布会自动缩窄' : '点上方按钮在右侧打开工具面板') }}</span>
    </div>

    <div class="wbody" v-show="editMode === 'canvas'">
      <div v-if="textOnly" class="textonly">
        <b>这是老版本「单题模式」录的题</b>
        <p>新版只保留「文档」和「画布」两个模式；要改这道题请用文档模式（Word 排版）。</p>
        <button class="primary" @click="switchMode('doc')">切到文档模式</button>
      </div>


    <aside v-if="drawer === 'pdf'" class="wdrawer dock pdfdock"
           :class="{ hot: pdfDrag }"
           @dragover.prevent="pdfDrag = true"
           @dragleave.prevent="pdfDrag = false"
           @drop.prevent="onPdfDrop">
      <div class="wdhead">
        <b>PDF 文献</b>
        <button class="ghost sm" @click="drawer = ''">收起</button>
      </div>
      <label class="drop">
        <input type="file" accept="application/pdf,image/*" multiple @change="upload" />
        <span>＋ 拖入 PDF 或点这里选择（可多份）</span>
      </label>
      <p class="tiphint">把 PDF 直接拖到这块面板里也能导入</p>
    <div class="docs" v-if="docs.length">
      <div class="docrow" v-for="d in docs" :key="d.doc_id">
        <button class="doc" :class="{ on: activeDoc && activeDoc.doc_id === d.doc_id }"
                @click="activeDoc = d; pageNo = 1; sel = null">
          {{ d.filename }}<i>{{ d.page_count }} 页</i>
        </button>
        <button class="docx" title="删除这份文献" @click.stop="delDoc(d)">×</button>
      </div>
    </div>
      <div class="preview" v-if="activeDoc">
        <img :src="pageUrl" alt="预览" />
        <div class="pmask">
          <button class="primary" @click="openCrop">✂ 放大并裁剪</button>
          <button class="ghost" @click="openCrop(1)">⤢ 原图裁剪</button>
        </div>
      </div>
      <div class="hint" v-else>导入 PDF 后，这里显示当前页预览；点「放大并裁剪」在大窗口里框选。</div>
      <div class="hint small">裁剪会直接进题目画布；答案用大窗口里的「裁剪到答案」。</div>
    </aside>

    <aside v-if="['formula', 'struct', 'subs'].includes(drawer)" class="wdrawer dock" :class="{ wide: (drawer === 'struct' && structTab === 'draw') || drawer === 'subs' }">
      <div class="wdhead">
        <b>{{ drawer === 'formula' ? '公式' : drawer === 'subs' ? '小问' : '结构式' }}</b>
        <button class="ghost sm" @click="drawer = ''">收起</button>
      </div>
      <div class="wdbody">
        <FormulaPad v-if="drawer === 'formula'" @send="insertFormula" @close="drawer = ''" />
        <div v-else-if="drawer === 'subs'" class="subsbox">
          <p class="muted">给这道题加小问编号（(1)(2)(3)…），填入小问内容后点「插入」，会把「(n) 内容」放到对应画布；留空则只插入编号。</p>
          <div class="subbar">
            <button class="ghost sm" @click="addSub">＋ 加一个小问</button>
            <span class="muted">插到：</span>
            <button class="ghost sm" :class="{ on: subTarget === 'q' }" @click="subTarget = 'q'">题目</button>
            <button class="ghost sm" :class="{ on: subTarget === 'a' }" @click="subTarget = 'a'">答案</button>
          </div>
          <div v-if="!subs.length" class="muted">还没有小问，点上面「＋ 加一个小问」。</div>
          <div class="subrow" v-for="(sb, i) in subs" :key="i">
            <b class="subno">({{ i + 1 }})</b>
            <input v-model="sb.hint" placeholder="小问内容（可留空，会一起插入画布）" />
            <button class="ghost sm" @click="insertSub(i)">插入</button>
            <button class="ghost sm" :disabled="i === 0" @click="moveSub(i, -1)">↑</button>
            <button class="ghost sm" :disabled="i === subs.length - 1" @click="moveSub(i, 1)">↓</button>
            <button class="ghost sm" @click="delSub(i)">删</button>
          </div>
        </div>
        <div v-else class="structbox">
          <div class="stabs">
            <button :class="{ on: structTab === 'smiles' }" @click="structTab = 'smiles'">SMILES / MOL</button>
            <button :class="{ on: structTab === 'draw' }" @click="switchStruct('draw')">手绘（Ketcher）</button>
          </div>

          <template v-if="structTab === 'smiles'">
            <p class="muted">输入 SMILES（如 <code>O=C(O)c1ccccc1</code>），服务端 RDKit 出图，确认无误后可复制 SVG 或截图贴进题图。</p>
            <input v-model="smiles" placeholder="SMILES 或 MOL" @keyup.enter="previewSmiles" />
            <div class="srow">
              <button class="ghost sm" @click="previewSmiles">预览</button>
              <button class="ghost sm" :disabled="!smilesSvg" @click="insertStructure">保存结构</button>
              <button class="ghost sm" :disabled="!smilesSvg" @click="copySvg">复制 SVG</button>
            </div>
          </template>

          <template v-else>
            <div class="kbar">
              <button class="ghost sm" @click="ketcherSetFromInput">把 SMILES 载入画板</button>
              <button class="ghost sm" :disabled="!ketcherReady" @click="ketcherTake">取结构出图</button>
              <span class="muted" v-if="!ketcherReady">画板加载中…</span>
            </div>
            <div class="kbar scolors">
              <span class="muted">结构颜色</span>
              <button v-for="c in STRUCT_COLORS" :key="c.n" class="csw" :class="{ on: structColor === c.c }"
                      :style="{ background: c.c || '#111' }" :title="c.n" @click="structColor = c.c"></button>
              <span class="muted">取图时生效</span>
            </div>
            <div class="kbar">
              <input v-model="structAlias" class="ta alias" placeholder="末端基团，如 Ac / OAc / NO2"
                     @keyup.enter="ketcherAlias" />
              <button class="ghost sm" :disabled="!ketcherReady"
                      title="先在画板里点选末端原子，再点这里：它变成可继续成键的原子标签"
                      @click="ketcherAlias">设为基团标签</button>
            </div>
            <iframe ref="kframe" class="kframe" src="/ketcher/index.html"
                    @load="ketcherPing" title="结构式画板"></iframe>
          </template>

          <div v-if="smilesMsg" class="okbox sm">{{ smilesMsg }}</div>
          <div v-if="smilesSvg" class="sprev" v-html="smilesSvg" />
        </div>
      </div>
    </aside>


  <div v-if="cropOpen" class="cropmask" @click.self="cropOpen = false">
      <div class="cropcard">
        <div class="crophead">
          <b>裁剪文献</b>
          <span class="muted" v-if="activeDoc">{{ activeDoc.filename }} · 第 {{ pageNo }} / {{ activeDoc.page_count }} 页</span>
          <span class="wsp" />
          <button class="ghost sm" @click="cropOpen = false">关闭（Esc）</button>
        </div>
        <div class="cropbar">
    <div class="pager" v-if="activeDoc">
      <button @click="turn(-1)" :disabled="pageNo <= 1">‹</button>
      <input v-model.number="pageNo" class="pageno" type="number" min="1" :max="activeDoc.page_count" />
      <span>/ {{ activeDoc.page_count }}</span>
      <button @click="turn(1)" :disabled="pageNo >= activeDoc.page_count">›</button>
      <select v-model.number="zoom">
        <option :value="1.5">标准</option>
        <option :value="2">清晰</option>
        <option :value="3">放大</option>
      </select>
      <button class="ghost sm" title="缩小页面显示" @click="viewZoom = Math.max(50, viewZoom - 15)">A－</button>
      <button class="ghost sm" title="放大页面显示" @click="viewZoom = Math.min(320, viewZoom + 15)">A＋</button>
      <span class="muted" style="font-size:12px">{{ viewZoom }}%</span>
      <button class="ghost sm" title="向左移动" @click="panViewer(-1)">◀</button>
      <button class="ghost sm" title="向右移动" @click="panViewer(1)">▶</button>
    </div>
        </div>
        <div class="cropbody" @wheel.prevent="onCropWheel">
    <div class="viewer" ref="viewerEl" v-if="activeDoc">
      <div class="page-wrap" :class="{ textmode: mode === 'text' }"
           :style="{ width: viewZoom + '%' }"
           @pointerdown="boxDown" @pointerup="onPagePointerUp">
        <img ref="pageImg" :src="pageUrl" draggable="false" @dragstart.prevent @load="syncImgBox" />
        <div v-if="textLines.length" class="textlayer" :class="{ on: mode === 'text' }">
          <div v-for="(l, i) in textLines" :key="i" class="tl" :style="tlStyle(l)">{{ l.text }}</div>
        </div>
        <div v-if="sel" class="selbox" :style="selStyle()"></div>
        <div v-for="(p, i) in keyLines" :key="'kp' + i" class="keyline" :style="keyStyle(p)"></div>
      </div>
    </div>
        </div>
        <div class="cropfoot">
          <label class="chk">
            <input type="checkbox" v-model="ocrAfterCrop" /> 裁剪后识别为文字
          </label>
          <span class="wsp" />
          <button @click="sel = null" :disabled="!sel">清除选区</button>
          <button class="primary ghost" :disabled="!sel" @click="cropAnswerInModal">裁剪到答案</button>
          <button class="primary" :disabled="!sel" @click="cropInModal">裁剪到题目</button>
        </div>
        <div class="busy" v-if="busy">{{ busy }}</div>
      </div>
    </div>

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
          <div class="metabox">
            <div class="metal">
              <span>出题人</span><b>{{ form.author || '未填' }}</b>
              <span>专题</span><b>{{ topicName(form.topic) }}</b>
              <span>年份</span><b>{{ form.year || '—' }}</b>
              <span>标题</span><b>{{ form.source_title || '自动取题干首行' }}</b>
            </div>
            <button class="ghost" @click="openInbank">填入库信息 / 入库…</button>
          </div>
          <label class="f"><span>题干文字（给 AI 读；扫描图/图片题可把题目粘贴到这里）</span>
            <textarea v-model="aiStem" rows="3" placeholder="例如：第1题 多芳甲烷隐色体化学（21分）1-1 … 1-2 …（也可留空，AI 会读题目画布里的文字块）"></textarea>
          </label>
          <label class="f"><span>参考答案（文字，可选）</span>
            <textarea v-model="form.answer" rows="3" placeholder="答案文字，支持 $公式$；也可以直接在下方答案画布排版"></textarea>
          </label>
          <div class="btnrow ansbtns">
            <button class="primary ghost" :disabled="answerBusy" @click="genAnswer">
              {{ answerBusy ? 'AI 解题复核中…' : 'AI 生成答案' }}
            </button>
            <button class="ghost" :disabled="!form.answer.trim()" @click="answerToCanvas">插入答案画布</button>
          </div>
          <div v-if="form.answer" class="apprev" v-html="renderMath(form.answer)"></div>
          <div v-if="analysisText" class="analysisbox">
            <div class="analysishead">AI 解析（已写入题库）</div>
            <div v-html="renderMath(analysisText)"></div>
          </div>
          <div class="aigen">
            <div class="analysishead">AI 出题 → 直接生成试卷（不写题库）</div>
            <div class="aigenrow">
              <label>题数 <input v-model.number="genCount" type="number" min="1" max="12" style="width:62px" /></label>
              <input v-model="genTitle" placeholder="试卷名（可空，之后也能改）" />
            </div>
            <button class="primary" :disabled="genBusy || paperStep.busy" @click="openPaperPlan">
              出题…（先选 1-11 题）
            </button>
            <div class="hint">点「出题」先选出几道；生成后逐题显示在画布上，改完点右上角「出下一个题」，全部完成自动成卷，不写题库。</div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="inbank.open" class="imask" @click.self="inbank.open = false">
      <div class="icard">
        <div class="ihead">
          <b>存进本地题库</b>
          <span class="mut">题目的分类与出题人都记在题库里，以后好查</span>
          <button class="icon" title="关闭" @click="inbank.open = false">×</button>
        </div>
        <label class="if"><span>出题人 *</span>
          <input v-model="inbank.author" placeholder="必填：入库后要能追到是谁出的" />
        </label>
        <div class="irow">
          <label class="if"><span>分类（题库九大专题）</span>
            <select v-model="inbank.topic">
              <option value="organic">有机化学</option>
              <option value="polymer">高分子化学</option>
              <option value="crystal">晶体化学</option>
              <option value="inorg-struct">无机综合与结构推断</option>
              <option value="equation">方程式与元素化学</option>
              <option value="thermo">热力学与化学平衡</option>
              <option value="electro">电化学</option>
              <option value="kinetics">动力学</option>
              <option value="analytical">分析化学</option>
            </select>
          </label>
          <label class="if small"><span>年份（可空）</span>
            <input v-model="inbank.year" type="number" min="1990" max="2100" placeholder="如 2024" />
          </label>
        </div>
        <label class="if"><span>标题</span>
          <input v-model="inbank.title" placeholder="留空就取题干第一行" />
        </label>
        <p class="ihint">题目从题目画布取文字和图片，答案从答案画布取文字（答案里的图会并进题图，并在答案里标「见题图 N」）。</p>
        <p v-if="inbank.err" class="errbox">{{ inbank.err }}</p>
        <div class="ibtn">
          <button class="ghost" @click="inbank.open = false">取消</button>
          <button class="primary" :disabled="inbank.busy" @click="confirmInbank">
            {{ inbank.busy ? '正在存…' : '存进题库' }}
          </button>
        </div>
      </div>
    </div>

    <section class="right">
      <div class="toolbar" v-if="editingQuestionId || savedId">
        <span class="spacer"></span>
        <span class="muted" v-if="editingQuestionId">正在编辑 #{{ editingQuestionId }}</span>
        <button v-if="savedId" :disabled="analysisBusy" @click="genAnalysis">
          {{ analysisBusy ? 'AI 写解析中…' : 'AI 生成解析' }}
        </button>
      </div>

      <div v-if="paperStep.active" class="stepbar">
        <span>分步出题 · 第 {{ paperStep.idx + 1 }}/{{ paperStep.total }} 题</span>
        <span class="spacer"></span>
        <button class="primary" :disabled="paperStep.busy" @click="nextPaperQuestion">
          {{ paperStep.busy ? '生成试卷中…' : (paperStep.idx + 1 < paperStep.total ? '出下一个题 →' : '完成并生成试卷') }}
        </button>
        <button class="ghost" :disabled="paperStep.busy" @click="cancelPaperStep">退出</button>
      </div>

      <div v-if="err" class="errbox">{{ err }}</div>
      <div v-if="msg" class="okbox">
        {{ msg }}
        <a v-if="savedId" href="javascript:void(0)" @click="goBank">去题库看看 →</a>
      </div>

      <div class="ctabs">
        <button class="ctab" :class="{ on: ctab === 'q' }" @click="ctab = 'q'">📝 题目</button>
        <button class="ctab" :class="{ on: ctab === 'a' }" @click="ctab = 'a'">✅ 答案</button>
        <button v-if="ctab === 'a'" class="ghost sm pad" title="把题目画布的内容整段垫到答案画布上面，然后在下面直接写解析"
                @click="draftAnswerOnQuestion">↧ 把题目垫到上面</button>
        <span class="spacer" />
        <span class="muted">像浏览器标签页一样切换，两边内容各自保留</span>
      </div>

      <div class="cwrap" v-show="ctab === 'q'">
        <CanvasPane ref="qPane" title="题目画布" metaable :max-pages="1"
                    hint="把右侧裁剪好的题目图拖进来，或点「＋ 文本」开始排版；也能把电脑里的图片 / PDF 直接拖到这块画布上"
                    @activate="activePane = 'q'" @changed="onPaneChanged('q')"
                    @files="onCanvasFiles($event, 'q')" />
      </div>
      <div class="cwrap" v-show="ctab === 'a'">
        <CanvasPane ref="aPane" title="答案画布" :max-pages="2"
                    hint="把右侧「裁剪到答案」的图拖进来，或用「＋ 文本」写解析；图片 / PDF 也能直接从电脑拖进来"
                    @activate="activePane = 'a'" @changed="onPaneChanged('a')"
                    @files="onCanvasFiles($event, 'a')" />
      </div>
    </section>
    </div>

    <div class="wbody dmode" v-show="editMode === 'doc'">
      <DocDesk :mode="editMode" :layout="docCanvasLayout" @layout="onDocCanvasLayout" @mode="switchMode($event)" />
    </div>

    <div v-if="pop.open" class="textpop" :style="{ left: pop.x + 'px', top: pop.y + 'px' }"
         @pointerdown.stop>
      <div class="pophead">
        <b>选中的文字</b>
        <span class="spacer"></span>
        <button class="icon" title="关闭" @click="closePop">✕</button>
      </div>
      <div class="popbody">
        <textarea v-model="pop.text" rows="4" placeholder="选区文字（可手动修改）"></textarea>
        <div class="poprow">
          <button @click="copyPop(pop.text)">复制</button>
          <button class="primary" :disabled="pop.busy || !pop.text.trim()" @click="translatePop">
            {{ pop.busy ? '处理中…' : '翻译成中文' }}
          </button>
          <button :disabled="!pop.text.trim()" @click="insertPop('q', false)">插入题目</button>
          <button :disabled="!pop.text.trim()" @click="insertPop('a', false)">插入答案</button>
        </div>
        <template v-if="pop.trans">
          <div class="popsub">译文（可修改）</div>
          <textarea v-model="pop.trans" rows="4"></textarea>
          <div class="poprow">
            <button @click="copyPop(pop.trans)">复制译文</button>
            <button class="primary" @click="insertPop('q', true)">译文插入题目</button>
            <button @click="insertPop('a', true)">译文插入答案</button>
          </div>
        </template>
        <div class="popmsg" v-if="pop.msg">{{ pop.msg }}</div>
        <div class="poperr" v-if="pop.err">{{ pop.err }}</div>
      </div>
    </div>
  </div>

  <div v-if="paperPlan.open" class="modal" @click.self="paperPlan.open = false">
    <div class="modal-card planpick">
      <h3>这次出几道题？</h3>
      <p class="muted">选一个数量，AI 按当前素材一次生成；生成后逐题显示在画布上，可改题干/答案，再点右上角「出下一个题」。</p>
      <div class="numpick">
        <button v-for="n in 11" :key="n" class="numbtn" @click="pickPaperCount(n)">{{ n }}</button>
      </div>
      <div class="hint">全部完成后自动生成一套试卷（不进题库），可在试卷页改名、导出或与别的卷融合。</div>
      <div class="btnrow" style="margin-top:12px">
        <button class="ghost" @click="paperPlan.open = false">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wt { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-width: 34px; height: 30px; padding: 0 9px; }
.wt .ic { width: 17px; height: 17px; }
.wt:disabled { opacity: .45; }

/* ---- WPS 式：顶部工具栏 + 右侧内嵌面板 ---- */
.writer.wps { display: flex; flex-direction: column; gap: 10px; align-items: stretch; }
.wt.ai { border-color: #c7d2fe; color: #3730a3; background: #eef2ff; }
.xmask { position: fixed; inset: 0; background: rgba(15, 23, 42, .45); display: flex; align-items: center; justify-content: center; z-index: 80; }
.xcard { width: min(920px, 94vw); max-height: 88vh; overflow: auto; background: #fff; border-radius: 14px; box-shadow: 0 24px 60px rgba(15, 23, 42, .25); }
.xhead { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--line); position: sticky; top: 0; background: #fff; z-index: 2; }
.xhead h3 { margin: 0; font-size: 15px; }
.xnote { margin: 12px 18px 0; font-size: 12.5px; color: #64748b; line-height: 1.7; }
.xrow { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 12px 18px; }
.xfile { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: #334155; }
.xfile input { font-size: 12px; max-width: 260px; }
.xerr { margin: 0 18px; color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; }
.xok { margin: 0 18px; color: #166534; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; }
.xlist { padding: 12px 18px 4px; display: flex; flex-direction: column; gap: 8px; }
.xitem { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; }
.xitem .crops { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; align-items: center; }
.crops-tip { font-size: 12px; color: #94a3b8; }
.cropimg { height: 130px; border: 1px solid var(--line); border-radius: 6px; cursor: zoom-in; background: #fff; box-shadow: 0 1px 3px rgba(15,23,42,.06); }
.cropimg:hover { border-color: var(--brand); }
.xi-h { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.xi-t { font-size: 12.5px; color: #334155; }
.xi-go { margin-left: auto; }
.xi-s { margin: 6px 0 0; font-size: 12.5px; color: #0f172a; line-height: 1.6; }
.xi-a { margin: 4px 0 0; font-size: 12px; color: #166534; line-height: 1.6; }
.xfoot { display: flex; align-items: center; gap: 8px; padding: 10px 18px 16px; border-top: 1px dashed var(--line); position: sticky; bottom: 0; background: #fff; }
.xsel { height: 30px; border: 1px solid var(--line); border-radius: 6px; font-size: 12px; padding: 0 8px; }
.wbody.pmode { padding: 0; overflow: auto; }
.wbody.dmode { padding: 0; overflow: hidden; }
.pmwrap { padding: 18px 22px 40px; }
.metabox { display: flex; flex-direction: column; gap: 6px; padding-bottom: 6px; border-bottom: 1px dashed var(--line); margin-bottom: 8px; }
.metal { display: flex; flex-wrap: wrap; gap: 4px 8px; font-size: 12px; color: #667085; }
.metal b { color: #344054; font-weight: 600; }
.imask { position: fixed; inset: 0; background: rgba(16, 24, 40, .32); display: flex; align-items: center; justify-content: center; z-index: 300; }
.icard { width: 520px; max-width: calc(100vw - 40px); background: #fff; border-radius: 14px; padding: 16px 18px 14px; box-shadow: 0 18px 60px rgba(16, 24, 40, .22); }
.ihead { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; }
.ihead .mut { font-size: 12px; color: #98a2b3; flex: 1; }
.ihead .icon { border: 0; background: transparent; font-size: 18px; cursor: pointer; color: #98a2b3; }
.if { display: block; margin-bottom: 10px; }
.if > span { display: block; font-size: 12px; color: #667085; margin-bottom: 4px; }
.if input, .if select { width: 100%; height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; }
.if.small { width: 150px; }
.irow { display: flex; gap: 10px; align-items: flex-end; }
.irow .if:first-child { flex: 1; }
.ihint { margin: 2px 0 12px; font-size: 12px; color: #98a2b3; line-height: 1.7; }
.ibtn { display: flex; justify-content: flex-end; gap: 8px; }
.stepbar {
  display: flex; align-items: center; gap: 6px; margin-bottom: 8px;
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  padding: 6px 10px;
}
.draftbar { display: flex; align-items: center; gap: 10px; margin: 0 0 10px; padding: 8px 12px; border: 1px solid #ffe0a3; background: #fffaf0; border-radius: 10px; font-size: 13px; color: #7a5200; }
.draftbar b { font-weight: 600; }
.draftat { font-size: 11px; color: #98a2b3; margin-left: 8px; }
.sbl { font-size: 12.5px; color: #6b7280; margin-right: 2px; }
.stepbar .spacer { flex: 1; }
.sbtn {
  display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 12px;
  border: 1px solid var(--line, #e7ebf2); background: #fff; color: #374151;
  border-radius: 999px; font-size: 12.5px; cursor: pointer;
}
.sbtn b { display: inline-grid; place-items: center; width: 16px; height: 16px; border-radius: 50%;
  background: #eef2f7; color: #4b5563; font-size: 11px; }
.sbtn.done { color: #1c8c46; border-color: #cfe9d8; background: #f4fbf6; }
.sbtn.done b { background: #1c8c46; color: #fff; }
.sbtn.on { color: #fff; background: var(--brand, #2563eb); border-color: var(--brand, #2563eb); }
.sbtn.on b { background: rgba(255, 255, 255, .25); color: #fff; }

/* #3c：工具栏以前 flex-wrap 换行，窗口一窄就被左右面板挤掉几个按钮（换行后落到面板下面看不见）。
   改成单行不换行 + 横向滚动：按钮保持原宽，放不下就横着滑，永远点得到。 */
.wtools {
  display: flex; align-items: center; gap: 6px;
  flex-wrap: nowrap; overflow-x: auto; overflow-y: hidden;
  scrollbar-width: thin; scrollbar-color: #cfd6e2 transparent;
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  padding: 6px 10px; position: sticky; top: 8px; z-index: 30;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
.wtools::-webkit-scrollbar { height: 6px; }
.wtools::-webkit-scrollbar-thumb { background: #cfd6e2; border-radius: 3px; }
.wtools::-webkit-scrollbar-track { background: transparent; }
.wt {
  display: inline-flex; align-items: center; gap: 4px; height: 30px; padding: 0 12px;
  flex: 0 0 auto;                       /* 不许被压缩，保证文字完整 */
  border: 1px solid var(--line, #e7ebf2); background: #fff; color: #1f2937;
  border-radius: 8px; font-size: 13px; cursor: pointer;
  white-space: nowrap;
}
.wt:hover { border-color: var(--brand, #2563eb); color: var(--brand, #2563eb); }
.wt.on { background: var(--brand, #2563eb); border-color: var(--brand, #2563eb); color: #fff; }
.wt.go { margin-left: 6px; }
.wt:disabled { opacity: .5; cursor: default; }
.wsep { width: 1px; height: 18px; background: var(--line, #e7ebf2); margin: 0 4px; }
.wsp { flex: 1 1 auto; }
.modesw { display: inline-flex; border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; }
.modesw button { border: 0; background: #fff; color: #4b5563; font-size: 12.5px; padding: 5px 12px; cursor: pointer; }
.modesw button.on { background: #2563eb; color: #fff; }
.wbody.tmode { padding: 0; overflow: hidden; }
.textonly { margin: 18px; padding: 16px 18px; border: 1px solid #fde68a; background: #fffbeb; border-radius: 10px; }
.textonly p { margin: 6px 0 10px; color: #92400e; font-size: 13px; }
.whint { font-size: 12px; color: #94a3b8; }
.wbody { display: flex; gap: 12px; align-items: flex-start; width: 100%; }
/* 单题模式要占满整行：否则会被 .writer 的两列网格挤到左半边，右边留一大块空白 */
.writer.wps > .wbody.tmode { grid-column: 1 / -1; width: 100%; height: calc(100vh - 132px); min-height: 0; align-items: stretch; }
.wbody > .right { order: 2; flex: 1 1 auto; min-width: 0; }
.wbody > .dock-left { order: 1; }
.wbody > .wdrawer { order: 3; }
.wbody > .left.dock-left {
  position: static; flex: 0 0 372px; width: 372px; max-height: calc(100vh - 150px);
  overflow: auto; border-right: none; box-shadow: none;
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px;
}
.wbody > .wdrawer.dock {
  position: static; flex: 0 0 420px; width: 420px; max-height: calc(100vh - 150px);
  overflow: auto; border-left: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  background: #fff; box-shadow: none;
}
@media (max-width: 1100px) {
  .wbody { flex-direction: column; }
  .wbody > .left.dock-left, .wbody > .wdrawer.dock { flex: none; width: 100%; max-height: 46vh; }
}
.phonehint {
  display: flex; align-items: center; gap: 10px; justify-content: space-between;
  background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
  padding: 8px 12px; border-radius: 10px; font-size: 13px; margin-bottom: 10px;
}
@media (max-width: 820px) {
  .writer.focus-mode { display: block; }
  .wrail { position: fixed; left: 0; top: 56px; bottom: 0; height: auto; width: 46px; }
  .writer.focus-mode .left { left: 46px; width: calc(100vw - 46px); }
  .writer.focus-mode .right { margin-left: 46px; overflow-x: auto; }
  .wdrawer { width: 100vw; }
}

.wrail {
  position: sticky; top: 0; align-self: flex-start; width: 52px; flex: none;
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 10px 6px; border-right: 1px solid var(--line); background: #fff; height: 100vh; z-index: 60;
}
.wrail.slim { width: 34px; }
.wrail .rt { border: 0; background: none; color: #94a3b8; font-size: 16px; cursor: pointer; padding: 2px 0 6px; }
.wrail .rb {
  width: 34px; height: 34px; border-radius: 9px; border: 1px solid transparent; background: none;
  cursor: pointer; font-size: 15px; color: #334155; display: flex; align-items: center; justify-content: center;
}
.wrail .rb:hover { background: #f1f5f9; }
.wrail .rb.on { background: var(--brand-soft); color: var(--brand); border-color: #dbeafe; }
.wrail .rb.primary { background: var(--brand); color: #fff; }
.wrail .rb.new { font-size: 19px; font-weight: 700; color: var(--brand); }
.wrail .rsp { flex: 1; }

.wdrawer {
  position: fixed; right: 0; top: 0; bottom: 0; width: min(440px, 40vw);
  background: #fff; border-left: 1px solid var(--line); box-shadow: -8px 0 24px rgba(15,23,42,.06);
  display: flex; flex-direction: column; z-index: 45;
}
.wdhead { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--line); }
.wdbody { flex: 1; overflow: auto; padding: 12px 14px 24px; }
.structbox input { width: 100%; margin: 8px 0; }
.srow { display: flex; gap: 8px; margin-bottom: 10px; }
.sprev { border: 1px solid var(--line); border-radius: 10px; padding: 10px; text-align: center; background: #fff; }
.sprev svg { max-width: 100%; max-height: 260px; }
.okbox.sm { font-size: 12.5px; padding: 6px 10px; }

.writer.focus-mode { display: flex; align-items: flex-start; gap: 14px; }
.writer.focus-mode .right { flex: 1 1 auto; min-width: 0; }
.writer.focus-mode .left {
  position: fixed; left: 52px; top: 0; bottom: 0; width: min(420px, 40vw); z-index: 40;
  background: #fff; border-right: 1px solid var(--line); box-shadow: 8px 0 24px rgba(15,23,42,.06);
  overflow: auto; padding: 12px 14px 24px;
}

.writer { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); gap: 14px; align-items: start; }
.writer.left-off { grid-template-columns: minmax(0, 1fr); }
.left-off .left { display: none; }
.left { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.pane { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px; }
.pane h3 { margin: 0 0 10px; font-size: 14px; }
.drop { display: block; border: 1px dashed #c7d2e4; border-radius: 10px; padding: 12px; text-align: center; color: #4a5568; cursor: pointer; font-size: 13px; }
.drop input { display: none; }
.docs { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.docrow { display: flex; align-items: stretch; gap: 4px; }
.docrow .doc { flex: 1; min-width: 0; }
.docx { flex: 0 0 26px; padding: 0; border: 1px solid #e7ebf2; background: #fff; color: #98a2b3; border-radius: 8px; cursor: pointer; font-size: 15px; line-height: 1; }
.docx:hover { color: #dc2626; border-color: #fca5a5; background: #fff5f5; }
.doc { text-align: left; background: #f7f9fc; border: 1px solid #e7ebf2; border-radius: 8px; padding: 6px 8px; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc i { color: #8b95a5; font-style: normal; margin-left: 6px; }
.doc.on { border-color: var(--brand, #2f6fed); background: #eef4ff; color: var(--brand, #2f6fed); }
.pager { display: flex; align-items: center; gap: 6px; margin: 8px 0; font-size: 12.5px; }
.pager input.pageno { width: 58px; padding: 3px 6px; font-size: 12.5px; text-align: center; }
.pager select { width: auto; }
.viewer { overscroll-behavior: contain; background: #f1f3f7; border-radius: 10px; padding: 8px; max-height: calc(100vh - 320px); overflow: auto; }
.page-wrap { position: relative; display: block; margin: 0 auto; cursor: crosshair; }
.page-wrap img { display: block; width: 100%; height: auto; touch-action: none; user-select: none; }
.selbox { position: absolute; border: 1px dashed var(--brand, #2f6fed); background: rgba(47, 111, 237, .12); pointer-events: none; }
.page-wrap.textmode { cursor: text; }
.page-wrap.textmode img { pointer-events: none; }
.textlayer { position: absolute; inset: 0; pointer-events: none; user-select: none; overflow: hidden; }
.textlayer.on { user-select: text; }
.tl { position: absolute; color: transparent; white-space: pre; pointer-events: none; }
.textlayer.on .tl { pointer-events: auto; cursor: text; }
.tl::selection { background: rgba(47, 111, 237, .3); color: transparent; }
.modes { display: flex; gap: 6px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.modes .chk { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #475467; white-space: nowrap; }
.modes .chk input { width: auto; }
.modes button { padding: 4px 10px; font-size: 12.5px; border: 1px solid #dfe5ee; background: #fff; border-radius: 7px; cursor: pointer; }
.modes button.on { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; }
.modes button.primary { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; font-weight: 600; }
.aipanel { border: 1px solid #dbe4f3; background: #f8fbff; border-radius: 10px; padding: 8px 10px; margin: 8px 0; }
.aihead { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.aihead b { font-size: 13px; flex: 1; }
.aihead .icon { padding: 3px 7px; font-size: 12px; }
.readrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 12.5px; color: #475467; margin-bottom: 6px; }
.readrow input.pagenum { width: 58px; padding: 3px 6px; font-size: 12.5px; text-align: center; }
.readrow select.readcount { width: auto; padding: 3px 6px; font-size: 12.5px; }
.sumlist { overscroll-behavior: contain; display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow: auto; margin-top: 6px; }
.sumcard { border: 1px solid #e3e8f0; background: #fff; border-radius: 8px; padding: 6px 8px; cursor: pointer; }
.sumcard.on { border-color: var(--brand, #2f6fed); box-shadow: inset 0 0 0 1px rgba(47, 111, 237, .18); }
.sumhead { display: flex; align-items: baseline; gap: 6px; font-size: 12px; color: #475467; }
.sumhead b { font-size: 12.5px; color: #1f2430; }
.aisum { margin-top: 4px; font-size: 12.5px; color: #344054; line-height: 1.55; }
.kplist { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
.kplist li { display: flex; gap: 6px; font-size: 12px; color: #475467; cursor: pointer; padding: 3px 4px; border-radius: 5px; }
.kplist li:hover { background: #eef4ff; }
.kppage { flex: 0 0 auto; color: var(--brand, #2f6fed); font-weight: 600; }
.kptext { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.aiempty { font-size: 12px; color: #8b95a5; line-height: 1.55; }
.keyline { position: absolute; left: 0; right: 0; background: rgba(250, 204, 21, .28); border-top: 1px solid rgba(202, 138, 4, .55); pointer-events: none; z-index: 3; }
.analysisbox { margin-top: 8px; padding: 8px 10px; background: #f0f9f4; border: 1px solid #cdebd9; border-radius: 8px; font-size: 13px; white-space: pre-wrap; }
.analysishead { font-size: 12px; color: #3b7a57; margin-bottom: 4px; font-weight: 600; }
.aigen { margin-top: 10px; padding: 9px 10px; background: #f7f4ff; border: 1px solid #e2d9fb; border-radius: 8px; }
.aigenrow { display: flex; align-items: center; gap: 8px; margin: 5px 0 7px; }
.aigenrow label { display: flex; align-items: center; gap: 4px; font-size: 12px; white-space: nowrap; }
.aigen button { width: 100%; }
.aigen .hint { margin-top: 5px; font-size: 11.5px; color: #7b6a9e; line-height: 1.5; }

.stepbar {
  position: sticky; top: 8px; z-index: 9;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin: 6px 0 8px; padding: 8px 12px;
  background: #fffbe6; border: 1px solid #f5d97a; border-radius: 10px;
  font-size: 13px; box-shadow: 0 4px 12px rgba(146, 110, 0, .08);
}
.stepbar .spacer { flex: 1; }
.modal { position: fixed; inset: 0; background: rgba(16, 24, 40, .45); display: flex; align-items: center; justify-content: center; z-index: 90; padding: 20px; }
.modal-card { background: #fff; border-radius: 14px; padding: 20px 22px; width: min(460px, 94vw); box-shadow: 0 20px 50px rgba(16, 24, 40, .2); }
.modal-card h3 { margin: 0 0 6px; font-size: 17px; }
.planpick .muted { font-size: 12.5px; color: #667085; line-height: 1.6; margin: 0 0 12px; }
.numpick { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.numbtn { padding: 12px 0; font-size: 16px; font-weight: 600; border: 1px solid #dbe3ff; background: #f4f7ff; color: #2f6fed; border-radius: 9px; cursor: pointer; }
.numbtn:hover { background: #2f6fed; color: #fff; border-color: #2f6fed; }
.planpick .hint { margin-top: 12px; font-size: 12px; color: #8a6d1a; background: #fffbe6; padding: 8px 10px; border-radius: 8px; }
.textpop {
  position: fixed; z-index: 60; width: 372px; max-height: calc(100vh - 24px);
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px;
  box-shadow: 0 18px 40px rgba(16, 24, 40, .18); display: flex; flex-direction: column;
}
.pophead { display: flex; align-items: center; gap: 8px; padding: 8px 10px; background: #f8fafc; border-bottom: 1px solid var(--line, #e7ebf2); }
.pophead b { font-size: 13px; }
.popbody { overscroll-behavior: contain; padding: 10px; display: flex; flex-direction: column; gap: 8px; overflow: auto; }
.popbody textarea { font-size: 13px; }
.poprow { display: flex; gap: 6px; flex-wrap: wrap; }
.poprow button { padding: 5px 10px; font-size: 12.5px; }
.popsub { font-size: 12px; color: #667085; }
.popmsg { font-size: 12px; color: #3b7a57; }
.poperr { font-size: 12px; color: #dc2626; white-space: pre-wrap; }
.hint { color: #8b95a5; font-size: 12.5px; }
.row { display: flex; gap: 8px; margin-top: 10px; }
.row button { flex: 1; }
.busy { margin-top: 8px; font-size: 12px; color: var(--brand, #2f6fed); }
.aux { display: flex; flex-direction: column; gap: 8px; }
.f { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: #475467; }
.f input, .f select, .f textarea { font-size: 13px; }
.f.req span { color: #b42318; font-weight: 600; }
.f.req input { border-color: #f0b4ae; background: #fffbfa; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid #dfe5ee; background: #fff; border-radius: 999px; padding: 3px 10px; font-size: 12px; color: #475467; cursor: pointer; }
.chip.on { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; }
.apprev { margin-top: 8px; padding: 8px 10px; background: #f8fafc; border-radius: 8px; font-size: 13.5px; }
.ansbtns { display: flex; gap: 6px; margin-top: 2px; }
.ansbtns button { flex: 1; font-size: 12.5px; padding: 5px 8px; }
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
.auxbody { overscroll-behavior: contain; padding: 12px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
@media (max-width: 1180px) {
  .writer { grid-template-columns: 1fr; }
}

/* ---- 画布标签页 / 小问 ---- */
.ctabs { display: flex; align-items: center; gap: 6px; margin: 2px 0 8px; }
.ctabs .ghost.sm.pad { height: 26px; padding: 0 10px; font-size: 12.5px; }
.ctab {
  display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 14px;
  border: 1px solid var(--line, #e7ebf2); border-bottom: none; background: #eef2f8; color: #475467;
  border-radius: 10px 10px 0 0; font-size: 13px; cursor: pointer;
}
.ctab.on { background: #fff; color: var(--brand, #2563eb); font-weight: 600; box-shadow: 0 -1px 0 var(--brand, #2563eb) inset; }
.cwrap { background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 0 12px 12px 12px; padding: 10px; }
.subsbox { display: flex; flex-direction: column; gap: 8px; }
.subbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.subrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.subrow input { min-width: 110px; }
.subrow input { flex: 1 1 auto; }
.subno { width: 34px; color: var(--brand, #2563eb); }
.ghost.sm.on { background: var(--brand, #2563eb); border-color: var(--brand, #2563eb); color: #fff; }

/* ---- 结构式画板 ---- */
.writer.wps .wbody > .wdrawer.dock.wide { flex: 0 0 560px; width: 560px; }
.stabs { display: flex; gap: 6px; margin-bottom: 8px; }
.stabs button { padding: 4px 10px; font-size: 12.5px; border: 1px solid #dfe5ee; background: #fff; border-radius: 999px; cursor: pointer; color: #475467; }
.stabs button.on { background: var(--brand, #2563eb); border-color: var(--brand, #2563eb); color: #fff; }
.kbar { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; flex-wrap: wrap; }
.scolors { display: flex; align-items: center; gap: 6px; }
.scolors .csw { width: 18px; height: 18px; border-radius: 50%; border: 1px solid rgba(0,0,0,.25); cursor: pointer; }
.scolors .csw.on { outline: 2px solid #2f6fed; }
.ta.alias { flex: 1 1 140px; min-width: 0; height: 28px; padding: 0 8px; }
.kframe { width: 100%; height: 460px; border: 1px solid var(--line, #e7ebf2); border-radius: 10px; background: #fff; }

/* ---- WPS 式覆盖（放最后，确保优先级） ---- */
.writer.wps { display: flex; flex-direction: column; gap: 10px; align-items: stretch; }
.writer.wps .wbody { display: flex; gap: 12px; align-items: flex-start; width: 100%; }
.writer.wps .wbody > .right { order: 2; flex: 1 1 auto; min-width: 0; }
.writer.wps .wbody > .left.dock-left {
  order: 3; position: static !important; left: auto; top: auto; bottom: auto;
  flex: 0 0 372px; width: 372px; max-height: calc(100vh - 150px); overflow: auto;
  border-right: none; box-shadow: none; z-index: auto;
  background: #fff; border: 1px solid var(--line, #e7ebf2); border-radius: 12px; padding: 12px;
}
.writer.wps .wbody > .wdrawer.dock {
  order: 3; position: static !important; right: auto; top: auto; bottom: auto;
  flex: 0 0 420px; width: 420px; max-height: calc(100vh - 150px); overflow: auto;
  border-radius: 12px; box-shadow: none; background: #fff;
}
/* 裁剪面板里文档列表限高，保证裁剪按钮始终可见 */
.writer.wps .wbody > .left.dock-left .docs { max-height: 30vh; overflow: auto; }
@media (max-width: 1100px) {
  .writer.wps .wbody { flex-direction: column; }
  .writer.wps .wbody > .left.dock-left,
  .writer.wps .wbody > .wdrawer.dock { flex: none; width: 100%; max-height: 46vh; }
}

.pdfdock { flex: 0 0 336px; }
.pdfdock .drop { padding: 10px; }
.pdfdock .docs { max-height: 26vh; overflow: auto; }
.preview { position: relative; margin-top: 8px; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: #f1f3f7; }
.preview img { display: block; width: 100%; height: auto; max-height: 46vh; object-fit: contain; }
.preview .pmask { position: absolute; inset: auto 0 0 0; padding: 8px; background: linear-gradient(transparent, rgba(15,23,42,.55)); display: flex; justify-content: center; }
.hint.small { font-size: 11px; }

.cropmask { position: fixed; inset: 0; background: rgba(15, 23, 42, .55); z-index: 120; display: flex; align-items: center; justify-content: center; }
.cropcard { width: 94vw; height: 92vh; background: #fff; border-radius: 14px; box-shadow: 0 24px 60px rgba(2, 8, 23, .35); display: flex; flex-direction: column; overflow: hidden; }
.crophead { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--line); }
.cropbar { display: flex; align-items: center; gap: 8px; padding: 8px 14px; border-bottom: 1px solid var(--line); background: #f8fafc; flex-wrap: wrap; }
.cropbody { flex: 1; min-height: 0; display: flex; padding: 10px 14px; }
.cropbody .viewer { flex: 1; max-height: none; }
.cropbody .page-wrap { max-width: 100%; }
.cropfoot { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-top: 1px solid var(--line); background: #f8fafc; }
</style>
