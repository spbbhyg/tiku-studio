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
const PRESETS = [
  { k: 'zhipu', name: '智谱 GLM-4-Flash（免费）', base: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash', hint: '注册 bigmodel.cn 即送免费额度，OpenAI 兼容' },
  { k: 'dashscope', name: '阿里百炼 Qwen-Flash（有免费额度）', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-flash', hint: 'bailian.console.aliyun.com 开通后拿 Key' },
  { k: 'deepseek', name: 'DeepSeek 官方（便宜）', base: 'https://api.deepseek.com',
    model: 'deepseek-chat', hint: 'platform.deepseek.com 充值即用' },
  { k: 'dsh', name: '本机 DSH 免费网关（需开 DSH）', base: 'http://127.0.0.1:8326/v1',
    model: 'our-free-model/mimo-v2.6-flash-free', hint: '完全免费，但 DSH 必须在后台运行' },
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
    aiMsg.value = '已切换为本机免费模型，AI 功能立即生效（需要 DeepSeek Harness 保持运行）'
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

onMounted(() => {
  load()
  loadStore()
  loadAi()
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
    <button class="tab" :class="{ on: tab === 'cats' }" @click="tab = 'cats'">分类与标签</button>
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
    <h3>服务</h3>
    <p class="muted" style="margin:4px 0">
      本机启动用项目里的 <code>start.bat</code>；登录 Windows 后计划任务 <code>tiku-studio</code> 会自动拉起。
      打不开时先看 <code>data/server.log</code> 末尾几行。
    </p>
  </div>

  <!-- AI 模型 -->
  <div v-else-if="tab === 'ai'" class="card">
    <AiQuota />
    <h3>AI 模型</h3>
    <div v-if="aiInfo.env_override" class="warnbox" style="margin-bottom:10px">
      检测到环境变量 <code>DEEPSEEK_API_KEY</code>，它的优先级高于这里保存的 Key。
    </div>
    <div style="margin:10px 0 14px; padding:12px; border:1px solid var(--line); border-radius:10px">
      <div class="rownow">
        <span class="lbl">本机免费模型</span>
        <button :disabled="free.busy" @click="detectFree">{{ free.busy ? '检测中…' : '检测免费模型插件' }}</button>
        <span v-if="aiInfo.free_gateway" class="chip ok">正在使用</span>
        <span v-else-if="aiInfo.free_enabled" class="chip">已启用（网关未就绪）</span>
        <button v-if="aiInfo.free_enabled" class="ghost" :disabled="aiBusy" @click="restorePaid">切回付费 Key</button>
      </div>
      <p style="margin:8px 0 0; color:var(--bad); font-weight:700; line-height:1.65; font-size:13px">
        ⚠ 免费模型必须安装 DeepSeek Harness（并装好免费模型插件），且让 DSH 在后台运行才可用。
        DSH 关掉后，免费模型会连接失败，翻译 / 逐页精读 / 生成答案 / 归类解析都用不了。
      </p>
      <p class="muted" style="margin:6px 0 0; font-size:12.5px">
        如果你装了 DeepSeek Harness 的免费模型插件，点「检测」后选一个模型即可，不用填 API Key。
        免费额度由插件提供。
      </p>
      <div v-if="free.ok" class="rownow" style="margin-top:8px">
        <span class="lbl">选择模型</span>
        <select v-model="free.picked" class="pathinput">
          <option v-for="m in free.models" :key="m" :value="m">{{ m }}</option>
        </select>
        <button class="primary" :disabled="aiBusy" @click="useFree">使用免费模型</button>
      </div>
      <p style="margin:8px 0 0; font-size:12.5px; color:var(--muted)">
        网关 <code>{{ aiInfo.gateway_url || 'http://127.0.0.1:8326/v1' }}</code>
        <span v-if="aiInfo.gateway_key_found"> · 已找到本机网关密钥</span>
        <span v-else> · 未找到本机网关密钥</span>
      </p>
      <p v-if="free.error" class="warnbox" style="margin:8px 0 0">{{ free.error }}</p>
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
      在这里保存会切回付费 DeepSeek（免费通道的设置会保留，随时可以再点「使用免费模型」）。
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
        <h4>它能做什么</h4>
        <ul>
          <li>出题页「选中英文 → 翻译成中文」「AI 逐页精读（摘要 + 机理/重点）」</li>
          <li>「AI 生成答案」、保存时的题型归类、<code>AI 生成解析</code></li>
          <li>精读页逐句译文、智能出题（按级别/知识点/题型命题）、批量拆题的答案补全</li>
        </ul>
        <h4>Key 存在哪</h4>
        <p>只保存在本机 <code>data/tiku.db</code>，不上传；也可以用环境变量 <code>DEEPSEEK_API_KEY</code>（优先级更高）。</p>
        <h4>免费模型 vs 付费 Key</h4>
        <p>「本机免费模型」走 DeepSeek Harness 网关，需要 DSH 在后台运行；DSH 关掉后免费通道会失败（翻译、精读、生成答案都会失败）。点「检测免费模型插件」可以挑一个模型，不用填 Key。</p>
        <p>免费推理模型会把预算花在思考上：命题类调用把 <code>max_tokens</code> 提到 12000~16000 才稳定出结果，单次 40~100 秒属正常。</p>
        <h4>额度怎么看</h4>
        <p>顶部「免费模型用量」按本机实测统计（今日/累计调用、失败数、输出 tokens、首字延迟）；官方没有上限接口，所以这是观测值不是配额。付费余额来自本机快照。</p>
        <h4>常见报错</h4>
        <ul>
          <li>401：Key 复制不完整</li>
          <li>402：账户余额不足</li>
          <li>502（AI 没有返回可用的题目）：素材太长/输出被截断，调小数量或换更短的素材</li>
          <li>连接失败：DSH 没开，或网关 8326 端口不通</li>
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
    <h3 style="margin-top:22px">标签库</h3>
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

  <!-- 标签管理 -->
  <div v-else>
    <TagsAdmin embedded />
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
</style>
