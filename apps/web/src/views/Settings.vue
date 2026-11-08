<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import CategoryAdmin from '../components/CategoryAdmin.vue'
import AiQuota from '../components/AiQuota.vue'
import TagsAdmin from './TagsAdmin.vue'
import { ACTIONS, comboOf, defaults, loadShortcuts, saveShortcuts } from '../shortcuts.js'

const tab = ref('general')          // general | ai | keys | cats | persona
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
  if (!confirm(`把 ${n} 道题发布到 ${ghOwner.value}/${ghRepo.value}？\n发布后会追加一个新包，别人点「一键更新」就能拿到。`)) return
  ghBusy.value = 'publish'; ghErr.value = ''; ghMsg.value = ''
  try {
    const r = await api.post('/api/github/publish', { include_drafts: ghDrafts.value })
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

onMounted(() => {
  load()
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
    <button class="tab" :class="{ on: tab === 'keys' }" @click="tab = 'keys'">快捷键</button>
    <button class="tab" :class="{ on: tab === 'cats' }" @click="tab = 'cats'">分类</button>
    <button class="tab" :class="{ on: tab === 'tags' }" @click="tab = 'tags'">标签管理</button>
    <button class="tab" :class="{ on: tab === 'persona' }" @click="tab = 'persona'">AI 助手</button>
  </div>

  <!-- 常规 -->
  <div v-if="tab === 'general'" class="card">
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
      书册 {{ stats.books }} 个 · 题目 {{ stats.questions }} 道（已确认 {{ stats.confirmed_questions }}、
      草稿 {{ stats.draft_questions }}）· 试卷 {{ stats.papers }} 张
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

    <h3>服务</h3>
    <p class="muted" style="margin:4px 0">
      本机启动用项目里的 <code>start.bat</code>；登录 Windows 后计划任务 <code>tiku-studio</code>（服务内部名，未改）会自动拉起。
      打不开时先看 <code>data/server.log</code> 末尾几行。
    </p>
  </div>

  <!-- AI 模型 -->
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
          <li>智能出题：按级别/知识点/题型命题；批量拆题：补全缺失答案</li>
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

  <div v-else-if="tab === 'cats'" class="card">
    <h3>分类与标签</h3>
    <p class="muted" style="margin:4px 0 12px">
      分类维度自己定；标签就是分类里的取值，题库左栏、筛选、组卷都按这里的设置走。
    </p>
    <CategoryAdmin />
  </div>

  <div v-else-if="tab === 'tags'" class="card">
    <h3>标签管理</h3>
    <p class="muted" style="margin:4px 0 12px">题库左栏、筛选、组卷都按这里的标签走；默认按「专题」分组，也可以自建分组。</p>
    <TagsAdmin embedded />
  </div>

  <div v-else-if="tab === 'persona'" class="card">
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

  <div v-else-if="tab === 'keys'" class="card">
    <h3>快捷键</h3>
    <p class="muted" style="margin:4px 0 12px">
      点某一行右侧的「设置」，然后直接按你想要的组合键；`Esc` 取消录制。
      快捷键保存在本机浏览器（localStorage），只影响出题画布。
    </p>
    <div v-if="dupWarn.length" class="warnbox" style="margin-bottom:10px">
      有重复的组合键：{{ dupWarn.join('、') }}，请改掉其中一个。
    </div>
    <table class="ktable">
      <thead>
        <tr><th>动作</th><th style="width:200px">当前组合键</th><th style="width:150px"></th></tr>
      </thead>
      <tbody>
        <tr v-for="a in ACTIONS" :key="a.key">
          <td>{{ a.label }}</td>
          <td>
            <kbd :class="{ cap: capturing === a.key }">
              {{ capturing === a.key ? '按下组合键…' : (keys[a.key] || '（未设置）') }}
            </kbd>
          </td>
          <td class="right">
            <button class="ghost sm" @click="startCapture(a.key)">设置</button>
            <button class="ghost sm" @click="resetOne(a.key)">默认</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="btnrow" style="margin-top:12px">
      <button class="primary" @click="saveKeys">保存快捷键</button>
      <button class="ghost" @click="resetAll">全部恢复默认</button>
      <span v-if="ok" style="color:var(--ok)">{{ ok }}</span>
    </div>
  </div>

  <div v-if="err" class="errbox" style="margin-top:12px">{{ err }}</div>
</template>

<style scoped>
.prow { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
.prow > label { width: 84px; flex: none; color: var(--muted); font-size: 13px; padding-top: 8px; }
.prow input, .prow textarea { flex: 1; max-width: 520px; }
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

.tabs { display: flex; gap: 6px; margin: 6px 0 14px; }
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
</style>