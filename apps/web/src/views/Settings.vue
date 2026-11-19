<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import CategoryAdmin from '../components/CategoryAdmin.vue'
import AiQuota from '../components/AiQuota.vue'
import TagsAdmin from './TagsAdmin.vue'
import { ACTIONS, comboOf, defaults, loadShortcuts, saveShortcuts } from '../shortcuts.js'


// ── 本地模型（千问） ──
const loc = reactive({ installed: false, running: false, exe: '', models: [], tiers: [], error: '', download_url: 'https://ollama.com/download' })
const pull = ref(null)
const pullLine = computed(() => (pull.value && (pull.value.line || pull.value.error)) || '正在下载…')
const locMsg = ref('')
const locErr = ref('')
const localBusy = ref(false)
let pullTimer = 0

async function loadLocal() {
  locErr.value = ''
  if (typeof window !== 'undefined') window.__loc = loc
  try {
    const r = await api.get('/api/local/status')
    Object.assign(loc, r)
    const cur = (r.current && r.current.model) || ''
    if (pull.value && pull.value.state === 'running') return
    const hit = (r.tiers || []).find((t) => t.pulled && t.tag === cur)
    if (hit) locMsg.value = '当前 AI 模型就是本地的 ' + hit.name
  } catch (e) { locErr.value = api.errText ? api.errText(e) : String(e) }
}

function reloadLocal() { locMsg.value = ''; loadLocal() }

async function launchLocal() {
  localBusy.value = true
  try {
    const r = await api.post('/api/local/launch', {})
    if (r.ok) { locMsg.value = r.note; await loadLocal() } else locErr.value = r.note || '启动失败'
  } catch (e) { locErr.value = api.errText(e) } finally { localBusy.value = false }
}

async function pullTier(tag) {
  localBusy.value = true; locErr.value = ''; locMsg.value = ''
  try {
    const r = await api.post('/api/local/pull', { tag })
    if (!r.ok) { locErr.value = r.error || '下载失败'; return }
    pollPull(tag)
  } catch (e) { locErr.value = api.errText(e) } finally { localBusy.value = false }
}

function pollPull(tag) {
  clearTimeout(pullTimer)
  const tick = async () => {
    try {
      const r = await api.get('/api/local/pull/' + encodeURIComponent(tag))
      pull.value = r
      if (r.state === 'done') { locMsg.value = '下好了：' + tag + '。点「用这一档」就能开始用。'; pull.value = null; await loadLocal(); return }
      if (r.state === 'error') { locErr.value = r.error || '下载失败'; pull.value = null; return }
    } catch { /* 忽略单次失败 */ }
    pullTimer = setTimeout(tick, 1500)
  }
  tick()
}

async function useTier(tag) {
  localBusy.value = true; locErr.value = ''
  try {
    const r = await api.post('/api/local/use', { tag })
    if (r.ok) { locMsg.value = r.note; await loadLocal() } else locErr.value = r.error || '切换失败'
  } catch (e) { locErr.value = api.errText(e) } finally { localBusy.value = false }
}

onBeforeUnmount(() => clearTimeout(pullTimer))

const tab = ref('general')          // general | export | ai | keys | cats | tags | persona | guide

// ---------------------------------------------------------------- 导出选项
const exp = reactive({
  font_size: 10.5, line_height: 1.5, margin_lr: 2.0, margin_tb: 1.8,
  numbering: 'arabic', scores: true, answers: 'end', footer: '',
})
const expBusy = ref(false)
const expMsg = ref('')

async function loadExport() {
  try {
    const d = await api.get('/api/settings/export')
    Object.assign(exp, d || {})
  } catch (e) { /* 设置读不到就用默认值 */ }
}

async function saveExport() {
  expBusy.value = true
  expMsg.value = ''
  try {
    const r = await api.put('/api/settings/export', { opts: { ...exp } })
    Object.assign(exp, r.opts || {})
    expMsg.value = '已保存：组卷页导出 PDF / Word 会按这里的设置排版'
  } catch (e) { expMsg.value = e.message || String(e) } finally { expBusy.value = false }
}

async function resetExport() {
  Object.assign(exp, { font_size: 10.5, line_height: 1.5, margin_lr: 2.0, margin_tb: 1.8, numbering: 'arabic', scores: true, answers: 'end', footer: '' })
  await saveExport()
}
const helpOpen = ref(false)

const persona = ref({ name: '小卷', tone: '', greeting: '' })
const personaBusy = ref(false)
const personaOk = ref(false)

async function loadPersona() {
  try {
    const d = await api.get('/api/wb/settings/persona')
    if (d && d.value && typeof d.value === 'object') {
      persona.value = { name: d.value.name || '小卷', tone: d.value.tone || '', greeting: d.value.greeting || '' }
    }
  } catch (e) { /* 没设置过就用默认 */ }
}

async function savePersona() {
  personaBusy.value = true
  personaOk.value = false
  try {
    await api.put('/api/wb/settings/persona', { value: persona.value })
    personaOk.value = true
    setTimeout(() => (personaOk.value = false), 2500)
  } catch (e) {
    err.value = '保存失败：' + (e.message || e)
  } finally {
    personaBusy.value = false
  }
}
const err = ref('')
const ok = ref('')
const stats = ref(null)

const store = ref(null)              // /api/settings/storage
const newDir = ref('')
const clean = ref(null)
const cleaning = ref(false)
const cleanMsg = ref('')
const fmtBytes = (n) => {
  if (!n) return '0 B'
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1048576).toFixed(1) + ' MB'
}
async function scanClean() {
  try { clean.value = await api.get('/api/wb/cleanup/scan') } catch (e) { cleanMsg.value = String(e.message || e) }
}
async function doClean() {
  if (!clean.value) return
  if (!confirm('清理后可回收的废弃图片与 7 天前的临时 PDF / 导出文件？（题库里在用的不会删）')) return
  cleaning.value = true
  try {
    const r = await api.post('/api/wb/cleanup', {})
    cleanMsg.value = `已清理 ${r.removed} 个文件，释放 ${fmtBytes(r.freed_bytes)}`
    await scanClean()
  } catch (e) { cleanMsg.value = String(e.message || e) } finally { cleaning.value = false }
}
const busy = ref(false)
const restarting = ref(false)

async function loadStore() {
  try {
    store.value = await api.get('/api/settings/storage')
    newDir.value = store.value.data_dir
  } catch (e) { err.value = e.message }
}

async function saveStorage(useDefault = false) {
  err.value = ''
  busy.value = true
  try {
    const r = useDefault
      ? await api.post('/api/settings/storage/reset', {})
      : await api.post('/api/settings/storage', { data_dir: newDir.value, move: true })
    ok.value = r.restart_required
      ? `已保存到 ${r.data_dir}，服务将在约 1 分钟内自动重启…`
      : `已保存到 ${r.data_dir}`
    await restartNow()
  } catch (e) { err.value = e.message } finally { busy.value = false }
}

async function restartNow() {
  restarting.value = true
  try { await api.post('/api/settings/restart', {}) } catch (e) { /* 进程可能直接退出 */ }
  // 轮询等它起来
  for (let i = 0; i < 100; i++) {
    await new Promise((r) => setTimeout(r, 1000))
    try {
      const st = await api.get('/api/stats')
      if (st) { restarting.value = false; await loadStore(); await load(); flash('服务已在新位置启动'); return }
    } catch (e) { /* 还没起来 */ }
  }
  restarting.value = false
  err.value = '服务重启超时，请手动关掉窗口后重新运行 start.bat'
}

const keys = reactive(loadShortcuts())
const capturing = ref('')           // 正在录制的动作 key

async function load() {
  try { stats.value = await api.get('/api/stats') } catch (e) { err.value = e.message }
}

function flash(t) { ok.value = t; setTimeout(() => { ok.value = '' }, 1800) }

function startCapture(action) {
  capturing.value = action
  ok.value = ''
}

function onKeydown(e) {
  if (!capturing.value) return
  e.preventDefault()
  const combo = comboOf(e)
  if (!combo) return
  if (combo === 'Escape') { capturing.value = ''; return }
  keys[capturing.value] = combo
  capturing.value = ''
}

function resetOne(action) { keys[action] = defaults()[action] }
function resetAll() { Object.assign(keys, defaults()); flash('已恢复默认快捷键') }
function saveKeys() { saveShortcuts({ ...keys }); flash('快捷键已保存（立即生效）') }

const dupWarn = computed(() => {
  const seen = {}
  const dup = []
  for (const a of ACTIONS) {
    const v = keys[a.key]
    if (!v) continue
    if (seen[v]) dup.push(v)
    seen[v] = true
  }
  return [...new Set(dup)]
})

// ---------- AI 模型（DeepSeek） ----------
const ai = reactive({ api_key: '', base_url: 'https://api.deepseek.com', model: 'deepseek-chat' })

// 服务商预设：填好 Base URL 与模型名，只用粘一个 Key（都不需要 DSH）
const emb = ref({ ok: false, note: '', models: [], model: '' })
const embBusy = ref(false)
const embMsg = ref('')

async function loadEmbedded() {
  try {
    const d = await api.get('/api/ai/embedded')
    emb.value = { ok: !!d.ok, note: d.note || '', models: d.models || [], model: d.model || '' }
  } catch (e) { emb.value = { ok: false, note: String(e), models: [], model: '' } }
}
async function startEmbedded() {
  embBusy.value = true; embMsg.value = ''
  try {
    const d = await api.post('/api/ai/embedded/start', {})
    embMsg.value = d.ok ? '内置服务已就绪' : (d.error || '启动失败')
    await loadEmbedded()
  } catch (e) { embMsg.value = String(e) } finally { embBusy.value = false }
}
async function useEmbedded() {
  embBusy.value = true; embMsg.value = ''
  try {
    const d = await api.post('/api/ai/embedded/use', { model: emb.value.model || '' })
    embMsg.value = '已切到内置模型：' + (d.model || '')
    await load()
  } catch (e) { embMsg.value = String(e) } finally { embBusy.value = false }
}
onMounted(loadEmbedded)

const PRESETS = [
  { k: 'zhipu', name: '智谱 GLM-4-Flash（免费）', base: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash', hint: '注册 bigmodel.cn 即送免费额度，OpenAI 兼容' },
  { k: 'dashscope', name: '阿里百炼 Qwen-Flash（有免费额度）', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-flash', hint: 'bailian.console.aliyun.com 开通后拿 Key' },
  { k: 'deepseek', name: 'DeepSeek 官方（便宜）', base: 'https://api.deepseek.com',
    model: 'deepseek-chat', hint: 'platform.deepseek.com 充值即用' },
  { k: 'builtin', name: '内置免费模型（随应用启动）', base: 'http://127.0.0.1:18903/v1',
    model: 'deepseek-v4.1-flash-free', hint: '应用自带，不用 Key、不用装别的，直接可用' },
  { k: 'custom', name: '自定义（任意 OpenAI 兼容）', base: '', model: '', hint: '自己填 Base URL 与模型名' },
]
const preset = ref('zhipu')
const dg = ref(null)
const dgBusy = ref(false)
const dgErr = ref('')
async function runDiagnose() {
  dgBusy.value = true
  dgErr.value = ''
  try {
    dg.value = await api.get('/api/ai/diagnose')
  } catch (e) {
    dgErr.value = e?.message || String(e)
  } finally { dgBusy.value = false }
}

function applyPreset() {
  const p = PRESETS.find((x) => x.k === preset.value)
  if (!p) return
  if (p.base) ai.base_url = p.base
  if (p.model) ai.model = p.model
}
const presetHint = computed(() => (PRESETS.find((x) => x.k === preset.value) || {}).hint || '')
function guessPreset() {
  const u = String(ai.base_url || '')
  const hit = PRESETS.find((p) => p.base && u.startsWith(p.base.replace(/\/+$/, '')))
  preset.value = hit ? hit.k : 'custom'
}
const aiInfo = reactive({ has_key: false, key_hint: '', source: '', env_override: false, configured: false, ocr_available: false })
const aiBusy = ref(false)
const aiMsg = ref('')
const aiErr = ref('')
const free = reactive({ busy: false, ok: false, models: [], recommended: '', error: '', picked: '', base_url: '' })

async function detectFree() {
  free.busy = true
  free.error = ''
  try {
    const r = await api.get('/api/ai/free-models')
    free.ok = !!r.ok
    free.models = r.models || []
    free.recommended = r.recommended || ''
    free.base_url = r.base_url || ''
    free.picked = (r.models || []).includes(ai.model)
      ? ai.model                                   // 已经是免费模型时，下拉默认停在实际在用的那个
      : (r.recommended || (r.models && r.models[0]) || '')
    free.error = r.error || ''
  } catch (e) {
    free.ok = false
    free.error = e.message
  } finally { free.busy = false }
}

async function useFree() {
  if (!free.picked) { free.error = '请先检测并选择一个免费模型'; return }
  aiBusy.value = true
  aiErr.value = ''
  aiMsg.value = ''
  try {
    const r = await api.post('/api/ai/config', {
      free_gateway: true,
      base_url: free.base_url || undefined,
      model: free.picked,
    })
    Object.assign(aiInfo, r)
    ai.base_url = r.base_url || ai.base_url
    ai.model = r.model || ai.model
    ai.api_key = ''
    guessPreset()
    aiMsg.value = '已切换为内置免费模型，AI 功能立即生效'
  } catch (e) { aiErr.value = e.message } finally { aiBusy.value = false }
}

async function restorePaid() {
  aiBusy.value = true
  aiErr.value = ''
  aiMsg.value = ''
  try {
    const r = await api.post('/api/ai/config', {
      free_gateway: false,
      base_url: aiInfo.paid_base_url || 'https://api.deepseek.com',
      model: aiInfo.paid_model || 'deepseek-chat',
    })
    Object.assign(aiInfo, r)
    ai.base_url = r.base_url || ai.base_url
    ai.model = r.model || ai.model
    ai.api_key = ''
    aiMsg.value = r.has_key ? '已切回之前保存的付费 DeepSeek 配置' : '已关闭免费通道；要使用付费模型请填写 API Key 后保存'
  } catch (e) { aiErr.value = e.message } finally { aiBusy.value = false }
}

async function loadAi() {
  try {
    const r = await api.get('/api/ai/status')
    ai.base_url = r.base_url || ai.base_url
    ai.model = r.model || ai.model
    Object.assign(aiInfo, r)
    ai.api_key = ''
    guessPreset()
  } catch (e) { aiErr.value = e.message }
}

async function saveAi(clear = false) {
  aiBusy.value = true
  aiErr.value = ''
  aiMsg.value = ''
  try {
    const r = await api.post('/api/ai/config', {
      api_key: ai.api_key || null,
      base_url: ai.base_url,
      model: ai.model,
      clear_key: clear,
      free_gateway: false,
    })
    Object.assign(aiInfo, r)
    ai.api_key = ''
    aiMsg.value = clear ? '已清除保存的 API Key' : '已保存，AI 功能立即生效'
  } catch (e) { aiErr.value = e.message } finally { aiBusy.value = false }
}

async function testAi() {
  aiBusy.value = true
  aiErr.value = ''
  aiMsg.value = ''
  try {
    const r = await api.post('/api/ai/test', {})
    const ms = r.usage && r.usage.elapsed_ms
    aiMsg.value = `连接成功（模型 ${r.usage && r.usage.model ? r.usage.model : ai.model}${ms ? `，${ms} ms` : ''}）`
  } catch (e) { aiErr.value = e.message } finally { aiBusy.value = false }
}

/* ---------------- 公共题库（GitHub 同步） ---------------- */
const gh = ref(null)
const ghBusy = ref('')
const ghMsg = ref('')
const ghErr = ref('')
const ghTok = ref('')
const ghOwner = ref('')
const ghRepo = ref('')
const ghMirror = ref('')
const ghDrafts = ref(false)
const ghForce = ref(false)

async function loadGh() {
  try {
    gh.value = await api.get('/api/github/status')
    ghOwner.value = gh.value.owner
    ghRepo.value = gh.value.repo
    ghMirror.value = gh.value.mirror
  } catch (e) { ghErr.value = e.message || String(e) }
}

async function ghSaveCfg() {
  ghBusy.value = 'cfg'; ghErr.value = ''; ghMsg.value = ''
  try {
    await api.post('/api/github/config', { owner: ghOwner.value, repo: ghRepo.value, mirror: ghMirror.value })
    await loadGh()
    ghMsg.value = '仓库设置已保存'
  } catch (e) { ghErr.value = e.message || String(e) } finally { ghBusy.value = '' }
}

async function ghSaveToken() {
  if (!ghTok.value.trim()) { ghErr.value = '先把令牌粘贴进来'; return }
  ghBusy.value = 'token'; ghErr.value = ''; ghMsg.value = ''
  try {
    const r = await api.post('/api/github/token', { token: ghTok.value.trim() })
    ghTok.value = ''
    await loadGh()
    ghMsg.value = `令牌已保存（GitHub 账号 ${r.login}）`
  } catch (e) { ghErr.value = e.message || String(e) } finally { ghBusy.value = '' }
}

async function ghClearToken() {
  if (!confirm('删除本机保存的令牌？删除后将无法发布，但仍可一键更新。')) return
  ghBusy.value = 'token'
  try {
    await api.del('/api/github/token')
    await loadGh()
    ghMsg.value = '令牌已删除'
  } catch (e) { ghErr.value = e.message || String(e) } finally { ghBusy.value = '' }
}

async function ghPublish() {
  const n = ghDrafts.value ? gh.value.local.confirmed + gh.value.local.drafts : gh.value.local.confirmed
  if (!confirm(`把 ${n} 道题发布到 ${ghOwner.value}/${ghRepo.value}？\n发布后会追加一个新包，别人点「一键更新」就能拿到。` + (ghForce.value ? '\n\n已勾选「重新发布全部」：会忽略本机的发布记录，把当前所有题目重新推一遍。' : ''))) return
  ghBusy.value = 'publish'; ghErr.value = ''; ghMsg.value = ''
  try {
    const r = await api.post('/api/github/publish', { include_drafts: ghDrafts.value, force: ghForce.value })
    await loadGh()
    ghMsg.value = `已发布第 ${r.seq} 包：${r.questions} 题、${r.images} 张图（${(r.bytes / 1048576).toFixed(2)} MB）`
  } catch (e) { ghErr.value = e.message || String(e) } finally { ghBusy.value = '' }
}

async function ghPull() {
  ghBusy.value = 'pull'; ghErr.value = ''; ghMsg.value = ''
  try {
    const r = await api.post('/api/github/pull', {})
    await loadGh()
    ghMsg.value = r.up_to_date ? '已经是最新的了' : `已更新：新增 ${r.updated} 题（跳过重复 ${r.skipped} 题），当前第 ${r.seq} 包`
  } catch (e) { ghErr.value = e.message || String(e) } finally { ghBusy.value = '' }
}

// ---------------------------------------------------------------- 局域网共备
const lan = reactive({ hosts: [], urls: [], code: '', codeSet: false, busy: '', msg: '', err: '', copied: '' })
async function loadLan() {
  try {
    const r = await api.get('/api/lan/info')
    lan.hosts = r.hosts || []
    lan.urls = r.urls || []
    lan.codeSet = !!r.code_set
    lan.code = r.code || ''
  } catch (e) { lan.err = e.message || String(e) }
}
async function saveLanCode(code) {
  lan.busy = 'code'; lan.err = ''; lan.msg = ''
  try {
    await api.post('/api/lan/code', { code })
    await loadLan()
    lan.msg = code ? '已设访问码：同事第一次打开链接要输这串码，之后浏览器会记住' : '已关掉访问码：同一网络里谁都能打开'
  } catch (e) { lan.err = e.message || String(e) } finally { lan.busy = '' }
}
async function copyText(t, tag) {
  try {
    await navigator.clipboard.writeText(t)
    lan.copied = tag
    setTimeout(() => { if (lan.copied === tag) lan.copied = '' }, 1500)
  } catch (e) { lan.err = '复制不了，手动选中吧：' + t }
}

// ---------------------------------------------------------------- 软件更新
const up = reactive({ cur: '', latest: '', has: false, notes: '', page: '', zip: null,
                      busy: '', err: '', msg: '', pending: null, cachedAt: '' })
async function checkUpdate(force) {
  up.busy = 'check'; up.err = ''; up.msg = ''
  try {
    const r = await api.get('/api/update/check' + (force ? '?force=1' : ''))
    if (r.ok === false) throw new Error(r.error || '查不到')
    up.cur = r.current; up.latest = r.latest; up.has = !!r.has_update
    up.notes = r.notes || ''; up.page = r.page || ''; up.zip = r.zip || null
    up.cachedAt = r.cached_at || ''
    up.msg = r.has_update ? `发现新版本 ${r.latest}` : (r.note || '已经是最新版了')
  } catch (e) {
    up.err = e.message || String(e)
    if (/限流/.test(up.err)) up.msg = ''
  } finally { up.busy = '' }
}
async function downloadUpdate() {
  up.busy = 'dl'; up.err = ''; up.msg = '正在下载…（安装包有点大，等一会儿）'
  try {
    const r = await api.post('/api/update/download', {})
    up.msg = `已下载 ${r.tag}（${r.files} 个文件），下一步点「应用并重启」`
    await loadUpdateStatus()
  } catch (e) { up.err = e.message || String(e); up.msg = '' } finally { up.busy = '' }
}
async function applyUpdate() {
  if (!window.confirm('应用更新会关闭软件、覆盖文件、再自动打开。现在保存好手上的活，继续吗？')) return
  up.busy = 'apply'; up.err = ''
  try {
    const r = await api.post('/api/update/apply', {})
    up.msg = r.note || '更新脚本已启动'
  } catch (e) { up.err = e.message || String(e) } finally { up.busy = '' }
}
async function loadUpdateStatus() {
  try { up.pending = (await api.get('/api/update/status')).pending } catch { up.pending = null }
}

onMounted(() => {
  load()
  loadLan()
  loadUpdateStatus()
  loadExport()
  loadStore()
  loadAi()
  loadGh()
  window.addEventListener('keydown', onKeydown, true)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown, true))
</script>

<template>
  <h2>设置</h2>

  <div class="tabs">
    <button class="tab" :class="{ on: tab === 'general' }" @click="tab = 'general'">常规</button>
    <button class="tab" :class="{ on: tab === 'ai' }" @click="tab = 'ai'">AI 模型</button>
    <button class="tab" :class="{ on: tab === 'local' }" @click="tab = 'local'; loadLocal()">本地模型</button>
    <button class="tab" :class="{ on: tab === 'persona' }" @click="tab = 'persona'">AI 助手</button>
    <button class="tab" :class="{ on: tab === 'lan' }" @click="tab = 'lan'">局域网</button>
    <button class="tab" :class="{ on: tab === 'update' }" @click="tab = 'update'">软件更新</button>
    <button class="tab" :class="{ on: tab === 'guide' }" @click="tab = 'guide'">上手指引</button>
  </div>


  <!-- 上手指引 -->
  <!-- 局域网共备 -->
  <div v-if="tab === 'lan'" class="card">
    <h3>局域网共备</h3>
    <p class="muted" style="margin:4px 0 10px">
      软件本身就是个本机服务，同一网络的同事用浏览器打开下面的地址，看到的就是<b>同一份数据</b>：
      能看你出的草稿、能一起在「组卷」里勾题、导出的 Word/PDF 也都能直接拿走。不用装客户端。
    </p>
    <div v-if="!lan.hosts.length" class="warnbox">没查到局域网地址：确认这台机器连着路由器/交换机（或者先关掉虚拟网卡再试）。</div>
    <div v-else class="lanlist">
      <div v-for="(u, i) in lan.urls" :key="u" class="rownow" style="margin-top:8px">
        <span class="lbl">发给同事</span>
        <code class="path">{{ u }}</code>
        <button class="ghost sm" @click="copyText(u, 'u' + i)">{{ lan.copied === 'u' + i ? '已复制' : '复制' }}</button>
      </div>
      <p class="muted" style="margin:8px 0 0; font-size:12.5px">
        同事的电脑要跟你在同一个网络里（同一个 WiFi / 同一个路由器）。Windows 第一次可能会弹「是否允许访问网络」，选允许。
      </p>
    </div>

    <h3 style="margin-top:18px">访问码</h3>
    <p class="muted" style="margin:4px 0 10px">
      办公室人多时建议设一个：设了以后，别人第一次打开链接要输这串码（只用字母和数字），浏览器会记住 7 天。
    </p>
    <div class="rownow">
      <input v-model="lan.code" class="pathinput" maxlength="8" placeholder="例如 6537，留空=不设码" />
      <button class="primary" :disabled="lan.busy === 'code'" @click="saveLanCode(lan.code.trim())">
        {{ lan.busy === 'code' ? '保存中…' : '保存访问码' }}
      </button>
      <button class="ghost" :disabled="lan.busy === 'code' || !lan.codeSet" @click="lan.code = ''; saveLanCode('')">关掉访问码</button>
    </div>
    <p v-if="lan.codeSet" class="chip brand" style="margin-top:8px">当前已启用访问码</p>
    <p v-if="lan.msg" class="okbox">{{ lan.msg }}</p>
    <p v-if="lan.err" class="errbox">{{ lan.err }}</p>
  </div>

  <!-- 软件更新 -->
  <div v-else-if="tab === 'update'" class="card">
    <h3>软件更新</h3>
    <p class="muted" style="margin:4px 0 10px">
      三步：<b>检查更新</b> → <b>下载更新</b> → <b>应用并重启</b>。全程不用登录 GitHub，数据在 <code>data/</code> 里不会被覆盖。
    </p>
    <p class="muted" style="margin:4px 0 10px; font-size:12.5px">
      小提示：GitHub 对未登录查询有「每小时 60 次」的限制，超了会提示<b>限流</b>（等一小时或换手机热点就好）。
      所以这里查到的结果会缓存 10 分钟，反复点不会重复消耗额度；确实要立刻重查再点「强制刷新」。
    </p>
    <div class="rownow">
      <span class="lbl">当前版本</span>
      <code>{{ up.cur || '—' }}</code>
      <button class="primary" :disabled="!!up.busy" @click="checkUpdate(false)">{{ up.busy === 'check' ? '检查中…' : '检查更新' }}</button>
      <button :disabled="!!up.busy" @click="checkUpdate(true)">强制刷新</button>
      <a v-if="up.page" :href="up.page" target="_blank" class="ghost sm" style="display:inline-flex;align-items:center">打开发布页</a>
    </div>
    <p v-if="up.latest" class="muted" style="margin-top:8px">最新版本：<b>{{ up.latest }}</b><span v-if="up.has" class="chip brand" style="margin-left:6px">有新版本</span></p>
    <p v-if="up.msg" class="okbox">{{ up.msg }}</p>
    <p v-if="up.err" class="errbox">{{ up.err }}</p>
    <pre v-if="up.notes" class="notes">{{ up.notes }}</pre>
    <div v-if="up.has" class="rownow" style="margin-top:10px">
      <button class="primary" :disabled="!!up.busy || !up.zip" @click="downloadUpdate">
        {{ up.busy === 'dl' ? '下载中…' : '下载更新' }}
      </button>
      <button class="ghost" :disabled="!!up.busy || !up.pending" @click="applyUpdate">应用并重启</button>
      <span v-if="!up.zip" class="muted" style="font-size:12.5px">这个版本没带 zip 包，去发布页手动下载</span>
    </div>
    <p v-if="up.pending" class="muted" style="margin-top:10px">
      已下载待应用：<b>{{ up.pending.tag }}</b>（{{ up.pending.at }}）。点「应用并重启」后别再动鼠标，等它自己回来。
    </p>
    <p class="muted" style="margin-top:14px; font-size:12.5px">
      网络不通就手动更新：去 <code>github.com/spbbhyg/tiku-studio/releases</code> 下 zip，解压覆盖安装目录即可，数据在 <code>data/</code> 里不会被动。
    </p>
  </div>

  <div v-else-if="tab === 'guide'" class="card guide">
    <h3>五分钟上手</h3>
    <p class="lead">
      这套软件只做三件事：<b>出题</b>（把文献里的题裁进画布、排好版）、<b>存题</b>（存进你本机的化学竞赛题库）、
      <b>组卷</b>（从题库挑题，排成 A4 试卷，导出 PDF / Word 发给学生）。
      所有数据都在你自己电脑上，不联网也能用。
    </p>

    <div class="g2">
      <div>
        <h4>开始前先确认两件事</h4>
        <ol class="steps">
          <li><b>题库在跑</b>：打开「题库」页，顶上显示「v1.0.2 · 204 题」就对了。没显示就点页面里的「启动题库」；
            还不行就手动双击题库文件夹里的 <code>chemy.exe</code>。</li>
          <li><b>填出题人</b>：出题页右下角「入库信息」里的<b>出题人</b>是必填的。存进题库的每道题都会带上
            「出题人：你的名字」这个标签，以后才知道是谁出的。</li>
        </ol>
      </div>
      <div>
        <h4>三条最常用的路</h4>
        <table class="route">
          <tr><td class="k">出单题</td><td>出题 → 画布 / 单题</td></tr>
          <tr><td class="k">出一整卷</td><td>出题 → 整卷（自己的大题）／组卷（从题库勾题）</td></tr>
          <tr><td class="k">用 Word 改题</td><td>出题 → 文档（三栏：题目文本 / Word / 画布）</td></tr>
          <tr><td class="k">翻题库</td><td>题库 → 搜索 / 按专题翻</td></tr>
        </table>
      </div>
    </div>

    <h4>一、出题：把文献里的题变成画布上的版面</h4>
    <p>
      出题页有<b>四个模式</b>，右上角切换：<b>画布</b>（自由排版）／<b>单题</b>（纯文字自动排版 A4）／
      <b>整卷</b>（自己录大题小问、导题目卷+答案卷）／<b>文档</b>（三栏：左题目文本、中 Word、右画布）。
    </p>
    <ol class="steps">
      <li><b>导入文献</b>：点工具栏的 PDF 按钮，或者直接把 PDF 拖到画布上。右边会显示原页面。</li>
      <li><b>裁图排版</b>：在右边页面上按住拖一个框 →「放大并裁剪」→ 图就进画布了。
        图可以拖着摆位置、拉四角改大小；按住 <kbd>Shift</kbd> 点多个元素可以一起选。</li>
      <li><b>写答案</b>：切到「答案」标签，点「↧ 把题目垫到上面」，题目会<b>整块</b>垫在答案画布顶部（已组合，可整体拖），
        你在它下面直接写解析就行，不用再把题目复制一遍。</li>
      <li><b>收入题库</b>：点右上角保存（软盘图标）→ 弹窗里填<b>出题人</b>（必填）、分类（题库九大专题）、年份、标题
        →「存进题库」。题目直接进本地题库，不再存本软件自己的库。</li>
    </ol>
    <p class="tip2">
      画布上的小技巧：<b>上色要先选字</b>（拖选中一段文字，再点上面那排色块，立刻变色）；
      <b>组合</b>=选中多个后点组合按钮，之后拖一个就是拖整组、拆分也按整组拆；
      <b>撤销/重做</b>在工具栏中间那两个箭头，最多 60 步。
    </p>

    <h4>二、组卷：勾题 → A4 预览 → 导出</h4>
    <ol class="steps">
      <li>「组卷」页左边按专题浏览题库，或输入关键词跨专题搜；点一行就收进卷子。</li>
      <li>中间是真实 A4 预览：勾一道立刻重排，看到的就是印出来的样子。</li>
      <li>右边填卷名、学校、命题人；页脚留空就自动印「第 X 页 共 Y 页」。</li>
      <li>卷名留空就不印标题；分值默认不印，勾「标分值」才印。</li>
      <li>删题有两种：右栏的「×」只是<b>从卷子里移出</b>；「从题库删除」是<b>真删</b>（要勾确认，删了就没了）。</li>
      <li>「导出 PDF」拿去打印，「导出 Word」拿去改。</li>
    </ol>

    <h4>三、题库：不用离开软件</h4>
    <p>
      「题库」页就是把题库自己的界面嵌进来了，和单独打开题库一模一样：搜索、按专题翻、勾选、
      导入（粘贴 / docx / pdf）、看答案、打印。这里做的任何改动都直接落在题库文件上，
      所以它是所有题目的唯一出处——本软件不再自己存一份。
    </p>

    <h4>四、文档模式：像 Word 一样改题</h4>
    <p>
      「出题 → 文档」是三栏：<b>左</b>写题目文本，<b>中</b>是真正的 Word（表格、页眉页脚、修订都在），
      <b>右</b>是画布。左上「«」和右上「»」可以把左右两栏收起，专心写。
    </p>
    <ul>
      <li><b>生成排版稿</b>：左边写完题干/答案，点「生成排版稿 →」，自动排成 Word 段落写进中间文档；</li>
      <li><b>公式</b>：点顶栏「公式」，写 <code>\ce{...}</code> 或 LaTeX（<code>$K_a$</code>、<code>\frac{}{}</code>），
        下面有一排希腊字母和常用符号，点「预览」看效果，再「插到光标处」；</li>
      <li><b>结构式</b>：点「结构式」弹出 Ketcher 画板，画完（含反应箭头）点「取结构并插入」，整块进文档；</li>
      <li><b>插图</b>：直接 <kbd>Ctrl</kbd>+<kbd>V</kbd> 粘截图，或点「插图」选文件，连插多张都行；</li>
      <li><b>题目文档 / 答案文档</b>两个标签各自记着自己的内容，来回切不会丢、也不会弹提示；</li>
      <li>改完点「保存到项目」（存到 <code>data/docx</code>），下次从左上「选一份文档」直接打开。</li>
    </ul>

    <h4>五、出到一半关掉页面也不会丢</h4>
    <p>
      出题时每 3 秒自动留一份草稿。重新打开出题页，顶部会提示「发现未完成的草稿（时间，N 个元素），要继续吗？」；
      也可以去「草稿」页，那里列着所有没写完的题，能继续写或删掉。点保存进题库后草稿自动清掉。
    </p>

    <h4>六、格式转换（四合一）</h4>
    <p>
      「格式转换」页有四件事：<b>文档互转</b>（Word↔PDF）、<b>PDF 拆分</b>（按页码拆，打包成 zip）、
      <b>PDF 合并</b>（多份合成一份，可调顺序）、<b>扫描件转 Word</b>（OCR 认字，认不出的整页当图片放进去）。
      全都在这台电脑上转，文件不出门；每一步都会告诉你用了哪个引擎，转坏了可以换一个再试。
    </p>

    <h4>七、AI 与本地模型</h4>
    <p>
      AI 功能都在「AI 工作台」里：<b>智能出题</b>（按级别/知识点出题）、<b>精读文献</b>、
      <b>小工具</b>（给想法→写题干、翻译并保留公式）、<b>对话</b>、<b>模型与用量</b>。
    </p>
    <p>
      模型两种来源：<b>云端</b>（设置 →「AI 模型」填 Key，或直接用内置免费模型）／
      <b>本机千问</b>（设置 →「本地模型」，三档 0.5B / 1.5B / 3B，下完断网也能用）。
      本地档位越高越聪明也越慢，纯 CPU 的电脑建议 0.5B。
    </p>
    <p class="tip2">
      任何功能报错，弹出来的第一行都是中文说明该做什么（比如「连不上本地服务：先确认软件开着」）。
    </p>

    <h4>快捷键</h4>
    <table class="keys">
      <tr><td><kbd>Ctrl</kbd> + <kbd>S</kbd></td><td>保存（存进题库）</td></tr>
      <tr><td><kbd>Ctrl</kbd> + <kbd>Z</kbd> / <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>Z</kbd></td><td>撤销 / 重做</td></tr>
      <tr><td><kbd>Ctrl</kbd> + <kbd>C</kbd> / <kbd>V</kbd></td><td>复制 / 粘贴选中的画布元素</td></tr>
      <tr><td><kbd>Delete</kbd></td><td>删掉选中的元素</td></tr>
      <tr><td>双击文字块</td><td>改文字；编辑时拖选文字再点色块，只给这几个字上色</td></tr>
      <tr><td>双击图片</td><td>（单题预览里）把图的位置复位</td></tr>
    </table>

    <h4>常见问题</h4>
    <dl class="faq">
      <dt>题库显示「连不上」</dt>
      <dd>先点「启动题库」；还不行就手动运行题库的 <code>chemy.exe</code>，等它窗口里出现 8080 再回来点「重试连接」。</dd>
      <dt>导出 Word 里页码是「1」不变</dt>
      <dd>Word 打开后按 <kbd>Ctrl</kbd> + <kbd>A</kbd> 再 <kbd>F9</kbd> 更新域；或者直接打印，打印时会自动更新。</dd>
      <dt>结构式里有反应箭头，插图后箭头没了</dt>
      <dd>已经修好：现在取结构式时整块画板（含箭头、文字、加号）一起出图，不再依赖 molfile。</dd>
      <dt>题目的分类和专题对不上</dt>
      <dd>分类完全跟着本地题库走。要改分类，去题库页改，本软件这边是以题库为准的。</dd>
    </dl>
  </div>

  <!-- 导出选项 -->
  

  <!-- 常规 -->
  <div v-else-if="tab === 'general'" class="card">
    <h3>数据位置</h3>
    <p class="muted" style="margin:4px 0 10px">
      题库（<code>tiku.db</code>）、题图与原件（<code>library/</code>）都放在这里；试卷 PDF 由浏览器下载，存到你自己选的下载目录。
    </p>
    <div v-if="store" class="storebox">
      <div class="rownow">
        <span class="lbl">当前位置</span>
        <code class="path">{{ store.data_dir }}</code>
        <span v-if="store.custom" class="chip brand">自定义</span>
        <span v-else class="chip">默认</span>
      </div>
      <div class="rownow" style="margin-top:8px">
        <span class="lbl">改到</span>
        <input v-model="newDir" class="pathinput" placeholder="绝对路径，例如 D:\tiku-data" />
        <button class="primary" :disabled="busy || restarting" @click="saveStorage(false)">
          {{ busy || restarting ? '处理中…' : '保存并重启' }}
        </button>
        <button class="ghost" :disabled="busy || restarting" @click="saveStorage(true)">恢复默认位置</button>
      </div>
      <p class="muted" style="margin:8px 0 0; font-size:12.5px">
        保存时会把现有数据库与题图复制到新目录（不删除旧数据），随后服务自动重启（约 1 分钟，页面会自动等你回来）。
        环境变量 <code>TIKU_DATA_DIR</code> 优先级更高，设置后这里的修改不生效。
      </p>
      <p class="muted" style="margin:6px 0 0; font-size:12.5px">
        数据库 <code>{{ store.db_path }}</code><br />
        题图目录 <code>{{ store.library_dir }}</code><br />
        配置文件 <code>{{ store.config_path }}</code>
      </p>
    </div>
    <p v-if="restarting" class="warnbox">正在重启服务，页面稍后会自动恢复…</p>
    <h3>当前数据</h3>
    <p v-if="stats" class="muted" style="margin:4px 0">
      试卷 {{ stats.papers }} 张 · 组卷用到的题目都在本地题库里（<a href="#/bank">题库</a>那一页）
    </p>
    <h3>存储清理</h3>
    <p class="muted" style="margin:4px 0 10px">
      清掉没被任何题目引用的裁剪图片、7 天前的导入临时 PDF 与导出的标注版 PDF。先点「扫描」看看能省多少。
    </p>
    <div class="rownow">
      <button class="ghost" @click="scanClean">扫描</button>
      <button class="primary" :disabled="cleaning || !clean || !clean.total_bytes" @click="doClean">
        {{ cleaning ? '清理中…' : '立即清理' }}
      </button>
      <span v-if="clean" class="muted" style="font-size:12.5px">
        废弃图片 {{ clean.orphan_images.count }} 个（{{ fmtBytes(clean.orphan_images.bytes) }}）·
        旧临时 PDF {{ clean.old_uploads.count }} 个（{{ fmtBytes(clean.old_uploads.bytes) }}）·
        旧导出 {{ clean.old_exports.count }} 个（{{ fmtBytes(clean.old_exports.bytes) }}）
        <b v-if="clean.total_bytes"> · 可省 {{ fmtBytes(clean.total_bytes) }}</b>
        <b v-else> · 没有可清理的</b>
      </span>
    </div>
    <p v-if="cleanMsg" class="okbox" style="margin-top:10px">{{ cleanMsg }}</p>

    <!-- 公共题库（GitHub 同步）已下线：题目统一进本地题库，发布/更新走「软件更新」 -->
    <template v-if="false">
    <h3>公共题库（GitHub 同步）</h3>
    <p class="muted" style="margin:4px 0 10px">
      题目发布到一个公开的 GitHub 仓库，别人点「一键更新」就能把新题同步到自己的题库。
      <b>只有在这台电脑上</b>（且令牌属于仓库主人）才能发布；更新任何人都能做。
    </p>
    <div v-if="gh" class="storebox">
      <div class="rownow">
        <span class="lbl">仓库</span>
        <input v-model="ghOwner" class="pathinput" style="max-width:170px" placeholder="GitHub 用户名" />
        <span class="muted">/</span>
        <input v-model="ghRepo" class="pathinput" style="max-width:200px" placeholder="仓库名" />
        <select v-model="ghMirror" style="margin-left:6px; max-width:300px">
          <option value="jsdelivr">下载源：jsDelivr（国内较快）</option>
          <option value="raw">下载源：raw.githubusercontent</option>
          <option value="ghproxy">下载源：ghproxy 镜像</option>
        </select>
        <button class="ghost" :disabled="!!ghBusy" @click="ghSaveCfg">{{ ghBusy === 'cfg' ? '保存中…' : '保存' }}</button>
      </div>
      <div class="rownow" style="margin-top:8px; flex-wrap:wrap; gap:8px">
        <span class="chip" :class="gh.token.present ? 'brand' : ''">
          令牌：{{ gh.token.present ? (gh.token.login || gh.token.source) : '未配置' }}
        </span>
        <span v-if="gh.token.present && gh.token.valid === false" class="chip" style="color:#b91c1c">令牌无效</span>
        <span class="chip" :title="gh.remote && gh.remote.reachable ? '刚从 GitHub 读到的' : '这次没连上，显示上次读到的'">
          远端第 {{ gh.remote ? gh.remote.seq : '—' }} 包<span v-if="gh.remote && !gh.remote.reachable">（上次）</span>
        </span>
        <span class="chip">本地第 {{ gh.last.seq }} 包</span>
        <span v-if="gh.last.publish_at" class="chip">上次发布 {{ gh.last.publish_at.replace('T', ' ').replace('Z', '') }}</span>
        <span v-if="gh.last.pull_at" class="chip">上次更新 {{ gh.last.pull_at.replace('T', ' ').replace('Z', '') }}</span>
        <a :href="gh.url" target="_blank" rel="noreferrer" class="muted" style="font-size:12.5px">打开仓库 ↗</a>
      </div>
      <p v-if="gh.remote && !gh.remote.reachable" class="warnbox" style="margin:8px 0 0">
        这次没连上远端仓库，上面显示的是上次读到的值：{{ gh.remote.error }}
      </p>

      <div class="rownow" style="margin-top:10px">
        <span class="lbl">令牌</span>
        <input v-model="ghTok" type="password" class="pathinput" style="flex:1"
               placeholder="粘贴 GitHub Personal Access Token（只存在本机，DPAPI 加密）" />
        <button class="ghost" :disabled="!!ghBusy" @click="ghSaveToken">{{ ghBusy === 'token' ? '保存中…' : '保存令牌' }}</button>
        <button v-if="gh.token.present" class="ghost" :disabled="!!ghBusy" @click="ghClearToken">删除</button>
      </div>
      <p class="muted" style="margin:6px 0 0; font-size:12.5px">
        生成地址：github.com/settings/tokens → Fine-grained token，只勾选这一个仓库的
        <b>Contents: Read and write</b>。换电脑要重新粘一次。
      </p>

      <div class="rownow" style="margin-top:10px; flex-wrap:wrap; gap:10px">
        <label class="muted" style="font-size:12.5px; display:flex; align-items:center; gap:4px">
          <input v-model="ghDrafts" type="checkbox" /> 连草稿一起发布（{{ gh.local.drafts }} 道）
        </label>
        <label class="muted" style="font-size:12.5px; display:flex; align-items:center; gap:4px"
               title="远端被清空、或想覆盖已发布内容时用；平时不用勾">
          <input v-model="ghForce" type="checkbox" /> 重新发布全部
        </label>
        <button class="primary" :disabled="!!ghBusy || !gh.token.present"
                :title="gh.token.present ? '' : '先配置令牌'"
                @click="ghPublish">
          {{ ghBusy === 'publish' ? '发布中…' : `立即发布（${ghDrafts ? gh.local.confirmed + gh.local.drafts : gh.local.confirmed} 题）` }}
        </button>
        <button class="ghost" :disabled="!!ghBusy" @click="ghPull">{{ ghBusy === 'pull' ? '更新中…' : '一键更新题库' }}</button>
        <button class="ghost" :disabled="!!ghBusy" @click="loadGh">刷新状态</button>
      </div>
      <p v-if="ghMsg" class="okbox" style="margin-top:10px">{{ ghMsg }}</p>
      <p v-if="ghErr" class="warnbox" style="margin-top:10px">{{ ghErr }}</p>
    </div>
    </template>

    <h3>服务</h3>
    <p class="muted" style="margin:4px 0">
      安装版双击「启动-神奋.cmd」即可（题库会一起起来）；打不开时先看 <code>data/server.log</code> 末尾几行。
    </p>
  </div>

  <!-- AI 模型 -->
  <div v-else-if="tab === 'local'" class="card">
    <h3>本机千问（不联网也能用）</h3>
    <p class="muted" style="margin:4px 0 12px">
      把千问下到本机，断网也能用 AI：写引子、翻译、改错字都行。三档按电脑配置选，换台有显卡的电脑装同一份软件也能跑。
    </p>

    <div class="locbar">
      <span class="dot" :class="loc.running ? 'ok' : 'bad'" />
      <b>{{ loc.running ? '本地模型服务在跑' : (loc.installed ? '本地模型服务没启动' : '这台电脑还没装本地模型引擎') }}</b>
      <span class="muted" style="margin-left:8px">{{ loc.running ? (loc.models.length + ' 个模型已下载') : (loc.installed ? '点「启动服务」' : '先装 Ollama，再来这里点「重新检查」') }}</span>
      <span style="flex:1" />
      <button class="ghost sm" @click="reloadLocal">重新检查</button>
      <button v-if="loc.installed && !loc.running" class="sm" @click="launchLocal">启动服务</button>
      <a v-if="!loc.installed" class="btn sm" :href="loc.download_url" target="_blank" rel="noreferrer">下载 Ollama 引擎</a>
    </div>
    <p v-if="!loc.installed" class="tip2">
      装的时候一路下一步就行（不用管理员权限）。装完它会自己常驻，回到这一页点「重新检查」。
      如果你不想装，也可以继续用上面「AI 模型」页里的云端模型。
    </p>

    <div class="tiers">
      <div v-for="t in loc.tiers" :key="t.tag" class="tier" :class="{ got: t.pulled }">
        <div class="tn"><b>{{ t.name }}</b><span class="sz">{{ t.size_mb }} MB</span></div>
        <div class="th">{{ t.hint }}</div>
        <div class="ta">
          <span v-if="t.pulled" class="ok2">已下载</span>
          <span v-else-if="pull && pull.tag === t.tag" class="dl">{{ pullLine }}</span>
          <span v-else class="muted">未下载</span>
          <span style="flex:1" />
          <button v-if="!t.pulled && !(pull && pull.tag === t.tag && pull.state === 'running')"
                  class="sm" :disabled="!loc.installed || localBusy" @click="pullTier(t.tag)">下载</button>
          <button v-if="t.pulled" class="sm primary" :disabled="localBusy" @click="useTier(t.tag)">用这一档</button>
        </div>
      </div>
    </div>

    <p v-if="locMsg" class="okbox" style="margin-top:10px">{{ locMsg }}</p>
    <p v-if="locErr" class="errbox" style="margin-top:10px">{{ locErr }}</p>
    <p class="tip2" style="margin-top:10px">
      这台电脑是 CPU 推理：0.5B 约 8–12 字/秒，1.5B 约 3–5 字/秒，3B 约 1–2 字/秒。有独立显卡的电脑会自动用显卡，快很多。
      下载走官方模型仓库，0.5B 约 400MB、1.5B 约 1GB、3B 约 1.9GB，中途关掉窗口也不会白下（已下的部分会保留）。
    </p>
  </div>

  <div v-else-if="tab === 'ai'" class="card">
    <div class="embcard">
      <div class="embhead">
        <b>内置免费模型</b>
        <span class="tag" :class="emb.ok ? 'ok' : 'bad'">{{ emb.ok ? '运行中' : '未运行' }}</span>
        <span class="muted">{{ emb.ok ? '随应用启动，不需要 DSH / 任何后台程序' : emb.note }}</span>
      </div>
      <div class="embrow">
        <select v-model="emb.model" :disabled="!emb.models.length">
          <option v-if="!emb.models.length" value="">（还没有可用模型）</option>
          <option v-for="m in emb.models" :key="m" :value="m">{{ m }}</option>
        </select>
        <button class="btn" :disabled="embBusy" @click="startEmbedded">重启内置服务</button>
        <button class="btn primary" :disabled="embBusy || !emb.ok" @click="useEmbedded">用它当默认模型</button>
        <span v-if="embMsg" class="embmsg">{{ embMsg }}</span>
      </div>
      <div class="embnote">内置服务是本机自带的一份独立免费模型代理（Node 进程，端口 18903），装好即用、无需登录；网页管理入口见 data/ofm.log。</div>
    </div>
    <AiQuota />
    <div class="dgcard">
      <div class="dghead">
        <b>AI 自检</b>
        <span class="tag" :class="dg && dg.usable ? 'ok' : 'bad'">{{ dgBusy ? '检查中…' : (dg && dg.usable ? '可用' : (dg ? '不可用' : '未检查')) }}</span>
        <span class="grow" />
        <button class="btn" :disabled="dgBusy" @click="runDiagnose">{{ dg ? '重新检查' : '开始自检' }}</button>
      </div>
      <p v-if="dg" class="dgadvice" :class="dg.usable ? 'ok' : 'bad'">{{ dg.advice }}</p>
      <ul v-if="dg" class="dglist">
        <li v-for="c in dg.checks" :key="c.id" :class="c.level">
          <span class="ico">{{ c.level === 'ok' ? '✓' : (c.level === 'warn' ? '!' : (c.level === 'error' ? '×' : '·')) }}</span>
          <b>{{ c.name }}</b>
          <span class="txt">{{ c.detail }}</span>
          <div v-if="c.fix" class="fix">怎么办：{{ c.fix }}</div>
        </li>
      </ul>
      <p v-if="dgErr" class="errbox">{{ dgErr }}</p>
    </div>
    <h3>AI 模型</h3>
    <div v-if="aiInfo.env_override" class="warnbox" style="margin-bottom:10px">
      检测到环境变量 <code>DEEPSEEK_API_KEY</code>，它的优先级高于这里保存的 Key。
    </div>
    <div class="rownow">
      <span class="lbl">当前 Key</span>
      <template v-if="aiInfo.has_key">
        <code class="path">{{ aiInfo.key_hint || '已配置' }}</code>
        <span class="chip brand">
          {{ aiInfo.source === 'env' ? '来自环境变量' : (aiInfo.source === 'dsh_gateway' ? '本机免费模型' : '已保存') }}
        </span>
      </template>
      <span v-else class="muted">未配置（翻译与 AI 识别暂不可用）</span>
    </div>
    <div class="rownow" style="margin-top:8px">
      <span class="lbl">本机 OCR</span>
      <span v-if="aiInfo.ocr_available" class="chip ok">已就绪</span>
      <span v-else class="muted">
        未安装：扫描件/图片题会提示先安装
        <code>rapidocr_onnxruntime</code>（重新运行 start.bat 可自动补装）
      </span>
    </div>
    <div class="rownow" style="margin-top:8px">
      <span class="lbl">服务商</span>
      <select v-model="preset" class="pathinput" style="max-width:360px" @change="applyPreset">
        <option v-for="p in PRESETS" :key="p.k" :value="p.k">{{ p.name }}</option>
      </select>
      <span class="muted" style="font-size:12px">{{ presetHint }}</span>
    </div>
    <div class="rownow" style="margin-top:8px">
      <span class="lbl">API Key</span>
      <input v-model="ai.api_key" type="password" class="pathinput" autocomplete="off"
             placeholder="sk-…（留空表示不修改）" />
    </div>
    <div class="rownow" style="margin-top:8px">
      <span class="lbl">Base URL</span>
      <input v-model="ai.base_url" class="pathinput" placeholder="https://api.deepseek.com" />
    </div>
    <div class="rownow" style="margin-top:8px">
      <span class="lbl">模型</span>
      <input v-model="ai.model" class="pathinput" placeholder="deepseek-chat" style="max-width:220px" />
      <button class="primary" :disabled="aiBusy" @click="saveAi(false)">{{ aiBusy ? '处理中…' : '保存' }}</button>
      <button :disabled="aiBusy" @click="testAi">测试连接</button>
      <button class="ghost" :disabled="aiBusy || !aiInfo.has_key || aiInfo.free_gateway"
              :title="aiInfo.free_gateway ? '当前用的是本机免费模型；要删除付费 Key 请先切回付费 Key' : ''"
              @click="saveAi(true)">清除 Key</button>
    </div>
    <p class="muted" style="margin:10px 0 0; font-size:12.5px">
      Key 申请地址 <code>platform.deepseek.com</code>；保存后无需重启服务，出题页马上可用。
      若报 401，多半是 Key 复制不完整；报 402 是账户余额不足。
      在这里保存会切回付费 Key。想用内置免费模型，点上方的「用它当默认模型」即可，随时可以切回来。
    </p>
    <p v-if="aiErr" class="errbox" style="margin-top:10px">{{ aiErr }}</p>
    <p v-if="aiMsg" class="okbox" style="margin-top:10px">{{ aiMsg }}</p>
  </div>

  <!-- 快捷键 -->
  <div v-if="tab === 'ai'" class="aihelpbar">
    <button class="ghost" @click="helpOpen = true">使用说明与注意事项</button>
    <span class="muted">（模型怎么选、Key 存哪、免费额度怎么看）</span>
  </div>
  <div v-if="helpOpen" class="helpmask" @click.self="helpOpen = false">
    <div class="helpcard">
      <div class="helphead">
        <h3>AI 模型：使用说明与注意事项</h3>
        <button class="ghost sm" @click="helpOpen = false">关闭</button>
      </div>
      <div class="helpbody">
        <h4>三种用法，按需选一个</h4>
        <ul>
          <li><b>内置免费模型（推荐，零配置）</b>：应用自带一份免费模型代理，随应用启动，不用装别的东西、不用填 Key。</li>
          <li><b>自己的 API Key</b>：智谱 GLM-4-Flash、百炼 qwen-flash、DeepSeek 都行，填 Key + Base URL + 模型名。</li>
          <li><b>两者都配</b>：平时用内置免费模型，等免费额度用完再点「保存」切付费 Key（内置设置会保留）。</li>
        </ul>

        <h4>内置免费模型怎么用</h4>
        <p>顶部「内置免费模型」卡片会显示状态。首次进入应用时它会在后台自动启动（约 10~30 秒），状态变「运行中」即可用。</p>
        <ul>
          <li>下拉里挑一个模型 → 点「用它当默认模型」，AI 功能立即生效；</li>
          <li>换模型或服务卡住时，点「重启内置服务」；</li>
          <li>它不是无限额度：走的是上游免费通道，偶发 429/超时属正常，重试即可。</li>
        </ul>

        <h4>它具体用在哪些地方</h4>
        <ul>
          <li>出题页：「选中英文→翻译」、AI 生成答案、AI 生成解析、保存时题型归类</li>
          <li>精读页：逐句译文、重点句解释</li>
          <li>AI 工作台：按级别/知识点/题型命题（智能出题子页签）</li>
        </ul>

        <h4>Key 存哪</h4>
        <p>只写在本机 <code>data/tiku.db</code>，不上传、不联网校验。也可以用环境变量 <code>DEEPSEEK_API_KEY</code>（优先级更高，界面上会显示「来自环境变量」）。</p>

        <h4>额度怎么看</h4>
        <p>顶部「免费模型用量」是按本机实际调用统计的观测值（今日/累计调用、失败数、输出 tokens、首字延迟），官方没有上限接口，所以它不是剩余额度。下方「官方账户余额」来自本机快照，仅对填了付费 Key 的账号有意义。</p>

        <h4>速度与稳定性</h4>
        <ul>
          <li>免费推理模型会先「思考」再出字：单次问了 40~100 秒是正常的，命题类调用已把 <code>max_tokens</code> 提到 12000~16000 才稳定；</li>
          <li>批量任务（如一次出 3 题）耗时会叠加，建议一次 1~2 题；</li>
          <li>开着代理/VPN 时偶尔连不上上游，关掉重试。</li>
        </ul>

        <h4>常见报错</h4>
        <ul>
          <li><b>401</b>：Key 复制不完整（常见于复制时少了尾部字符）</li>
          <li><b>402</b>：账户余额不足</li>
          <li><b>502 / AI 没有返回可用的题目</b>：素材太长或输出被截断，调小数量、换短一点的素材</li>
          <li><b>内置服务「未启动」</b>：点「重启内置服务」；仍不行就看 <code>data/ofm.log</code> 末尾几行</li>
        </ul>
      </div>
    </div>
  </div>

  

  

  <div v-if="tab === 'persona'" class="card">
    <h3>AI 助手（小卷）</h3>
    <p class="muted" style="margin:4px 0 12px">首页那个问答框的人格就按这里的设置走，改完保存即可。</p>
    <div class="prow">
      <label>名字</label>
      <input v-model="persona.name" placeholder="如：小卷" />
    </div>
    <div class="prow">
      <label>说话风格</label>
      <input v-model="persona.tone" placeholder="如：说话直接、先给步骤、不懂会说明" />
    </div>
    <div class="prow">
      <label>开场白</label>
      <textarea v-model="persona.greeting" rows="3" placeholder="第一次打开首页时显示的那句话" />
    </div>
    <div class="prow">
      <label></label>
      <div>
        <button :disabled="personaBusy" @click="savePersona">{{ personaBusy ? '保存中…' : '保存' }}</button>
        <span v-if="personaOk" class="chip ok">已保存</span>
      </div>
    </div>
  </div>

  

  <div v-if="err" class="errbox" style="margin-top:12px">{{ err }}</div>
</template>

<style scoped>
.locbar { display: flex; align-items: center; gap: 8px; background: #f8fafc; border: 1px solid var(--line); border-radius: 10px; padding: 9px 12px; margin-bottom: 10px; flex-wrap: wrap; }
.locbar .dot { width: 8px; height: 8px; border-radius: 50%; background: #b7bdc8; }
.locbar .dot.ok { background: #16a34a; }
.locbar .dot.bad { background: #ef4444; }
.locbar a.btn { text-decoration: none; }
.tiers { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); margin-top: 6px; }
.tier { border: 1px solid var(--line); border-radius: 12px; padding: 12px; background: #fff; display: flex; flex-direction: column; gap: 6px; }
.tier.got { border-color: #b8e6c8; background: #fbfffc; }
.tier .tn { display: flex; align-items: baseline; gap: 8px; }
.tier .sz { font-size: 12px; color: #98a2b3; }
.tier .th { font-size: 12.5px; color: #667085; line-height: 1.6; min-height: 40px; }
.tier .ta { display: flex; align-items: center; gap: 8px; }
.tier .ok2 { font-size: 12px; color: #16a34a; font-weight: 600; }
.tier .dl { font-size: 12px; color: #2f6fed; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.prow { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
.prow > label { width: 84px; flex: none; color: var(--muted); font-size: 13px; padding-top: 8px; }
.prow input, .prow textarea { flex: 1; max-width: 520px; }
.dgcard { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; margin: 0 0 14px; background: #fbfcfe; }
.dghead { display: flex; align-items: center; gap: 8px; }
.dghead .grow { flex: 1; }
.dgadvice { margin: 8px 0 6px; font-size: 13px; line-height: 1.7; }
.dgadvice.ok { color: #1c8c46; }
.dgadvice.bad { color: #b42318; }
.dglist { list-style: none; margin: 0; padding: 0; }
.dglist li { position: relative; padding: 5px 0 5px 22px; font-size: 12.8px; line-height: 1.7; color: #344054; border-top: 1px dashed #eef1f5; }
.dglist li:first-child { border-top: 0; }
.dglist li .ico { position: absolute; left: 2px; top: 5px; font-weight: 700; }
.dglist li.ok .ico { color: #1c8c46; }
.dglist li.warn .ico { color: #b54708; }
.dglist li.error .ico { color: #b42318; }
.dglist li.info .ico { color: #98a2b3; }
.dglist li b { margin-right: 6px; }
.dglist li .fix { margin-top: 2px; color: #b54708; font-size: 12.4px; }
.aihelpbar { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.helpmask { position: fixed; inset: 0; background: rgba(15,23,42,.5); display: flex; align-items: center; justify-content: center; z-index: 80; }
.helpcard { width: min(760px, 92vw); max-height: 86vh; background: #fff; border-radius: 14px; display: flex; flex-direction: column; overflow: hidden; }
.helphead { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--line); }
.helphead h3 { margin: 0; font-size: 16px; }
.helpbody { padding: 14px 18px 22px; overflow: auto; font-size: 13.5px; line-height: 1.85; color: #334155; }
.helpbody h4 { margin: 16px 0 6px; font-size: 14px; color: #0f172a; }
.helpbody ul { margin: 4px 0 8px 18px; }
.helpbody code { background: #f1f5f9; padding: 0 4px; border-radius: 4px; }

.storebox { margin: 6px 0 4px; }
.rownow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.rownow .lbl { width: 68px; color: #667085; font-size: 13px; }
.path { background: #f2f4f7; padding: 2px 8px; border-radius: 6px; font-size: 12.5px; }
.pathinput { flex: 1; min-width: 260px; }
.warnbox { margin-top: 10px; }

.tabs { display: flex; gap: 6px; margin: 6px 0 14px; flex-wrap: wrap; }
.exgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px 16px; }
.exgrid label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #475467; }
.exgrid label.wide { grid-column: 1 / -1; }
.exgrid label.chk { flex-direction: row; align-items: center; gap: 6px; }
.exgrid input, .exgrid select { height: 30px; border: 1px solid #e4e7ec; border-radius: 8px; padding: 0 8px; font-size: 13px; }
.guide { line-height: 1.75; }
.guide h3 { margin: 0 0 6px; }
.guide h4 { margin: 20px 0 6px; font-size: 14.5px; }
.guide .lead { color: #344054; background: #f7f9fc; border-left: 3px solid #2f6fed; padding: 10px 12px; border-radius: 0 8px 8px 0; margin: 8px 0 6px; }
.guide .g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 8px; }
.guide ol.steps { margin: 6px 0; padding-left: 22px; }
.guide ol.steps li { margin: 4px 0; }
.guide code { background: #f2f4f7; border-radius: 4px; padding: 1px 5px; font-size: 12.5px; }
.guide kbd { border: 1px solid #d0d5dd; border-bottom-width: 2px; border-radius: 5px; padding: 0 5px; font-size: 12px; background: #fff; }
.guide table { border-collapse: collapse; font-size: 13px; }
.guide table.route td, .guide table.keys td { padding: 4px 10px 4px 0; vertical-align: top; }
.guide table.route .k { color: #667085; white-space: nowrap; }
.guide table.keys td:first-child { white-space: nowrap; color: #344054; }
.guide .tip2 { font-size: 12.8px; color: #475467; background: #fffaf0; border-radius: 8px; padding: 8px 12px; }
.guide dl.faq { margin: 6px 0 0; }
.guide dl.faq dt { font-weight: 600; margin-top: 10px; font-size: 13.2px; }
.guide dl.faq dd { margin: 2px 0 0; color: #475467; font-size: 13px; }
.exact { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.tab {
  padding: 7px 16px; border-radius: 999px; border: 1px solid var(--line);
  background: #fff; cursor: pointer; font-size: 13.5px; color: #344054;
}
.tab.on { background: var(--brand); border-color: var(--brand); color: #fff; }
.ktable { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.ktable th, .ktable td { border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; }
.ktable th { color: #667085; font-weight: 600; font-size: 12.5px; }
.ktable .right { text-align: right; white-space: nowrap; }
kbd {
  display: inline-block; min-width: 90px; padding: 3px 9px; border-radius: 6px;
  border: 1px solid var(--line); background: #f8fafc; font-family: ui-monospace, Consolas, monospace;
  font-size: 12.5px; color: #1f2937;
}
kbd.cap { border-color: var(--brand); background: var(--brand-soft); color: var(--brand); }
.embcard { border: 1px solid #dbe6ff; background: #f6f9ff; border-radius: 12px; padding: 12px 14px; margin-bottom: 14px; }
.embhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.embhead .tag { font-size: 12px; padding: 1px 8px; border-radius: 999px; }
.embhead .tag.ok { background: #e7f7ec; color: #1f7a3f; }
.embhead .tag.bad { background: #fdecec; color: #b42318; }
.embrow { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.embrow select { min-width: 280px; }
.embmsg { color: #2f6fed; font-size: 13px; }
.embnote { color: #667085; font-size: 12px; margin-top: 8px; line-height: 1.6; }
.lanlist .path { max-width: 420px; }
.notes { background: #f8fafc; border: 1px solid #eef1f6; border-radius: 8px; padding: 10px; font-size: 12.5px; white-space: pre-wrap; max-height: 220px; overflow: auto; }
.errbox { background: #fef3f2; border: 1px solid #fecdca; color: #b42318; border-radius: 8px; padding: 8px 12px; font-size: 13px; }
</style>