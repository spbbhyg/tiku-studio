<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import TagsAdmin from './TagsAdmin.vue'
import { ACTIONS, comboOf, defaults, loadShortcuts, saveShortcuts } from '../shortcuts.js'

const tab = ref('general')          // general | keys | tags
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

onMounted(() => {
  load()
  loadStore()
  window.addEventListener('keydown', onKeydown, true)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown, true))
</script>

<template>
  <h2>设置</h2>

  <div class="tabs">
    <button class="tab" :class="{ on: tab === 'general' }" @click="tab = 'general'">常规</button>
    <button class="tab" :class="{ on: tab === 'keys' }" @click="tab = 'keys'">快捷键</button>
    <button class="tab" :class="{ on: tab === 'tags' }" @click="tab = 'tags'">标签管理</button>
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

  <!-- 快捷键 -->
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
