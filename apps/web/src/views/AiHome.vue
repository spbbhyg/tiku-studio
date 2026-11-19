<script setup>
// AI 工作台：一个入口，上方切子页签（智能出题 / 对话 / 模型状态）。
// 以前「智能出题」是一个独立导航，现在收进这里，以后加功能也往这里加。
import { nextTick, onMounted, reactive, ref } from 'vue'
import SmartPaper from './SmartPaper.vue'
import Reader from './Reader.vue'
import AiQuota from '../components/AiQuota.vue'
import { api } from '../api.js'

const idea = ref('')
const ideaStyle = ref('intro')
const ideaOut = ref('')
const srcText = ref('')
const direction = ref('en2zh')
const transOut = ref('')
const toolBusy = ref(false)
let toolErr = ''

async function askAi(system, user) {
  const r = await api.post('/api/ai/chat', {
    messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
    page: 'tools', search: false,
  })
  return String((r && (r.text || r.content || r.answer)) || '').trim()
}

async function runIdea() {
  toolBusy.value = true; ideaOut.value = ''
  const sys = '你是化学竞赛命题老师。用中文写作，公式用 LaTeX（行内用 $...$，反应式用 \ce{...}）。不要客套，直接给成品。'
  const user = ideaStyle.value === 'intro'
    ? '把下面这个想法写成一道化学竞赛题的「题干引子」：3~5 句，先给真实背景/文献事实，再给必需的结构与数据，最后一句「据此回答下列问题」。想法：' + idea.value
    : ideaStyle.value === 'full'
      ? '把下面这个想法扩成一整道化学竞赛大题：题干（背景+数据）+ 3 个小问（1-1/1-2/1-3），并在最后给参考答案要点。想法：' + idea.value
      : '下面这个想法想考什么？用 3 条说清考察意图、易错点、需要的先备知识：' + idea.value
  try { ideaOut.value = await askAi(sys, user) || '（AI 没有返回内容，换个说法再试）' }
  catch (e) { ideaOut.value = 'AI 调用失败：' + apiErr(e) }
  finally { toolBusy.value = false }
}

async function runTrans() {
  toolBusy.value = true; transOut.value = ''
  const sys = '你是化学专业译者。保留原文里的数字、单位、化学式与公式（LaTeX 原样保留），中文用竞赛/教材习惯译法，不要解释、不要加注。'
  const user = (direction.value === 'en2zh' ? '翻译成中文：\n' : '翻译成英文：\n') + srcText.value
  try { transOut.value = await askAi(sys, user) || '（AI 没有返回内容）' }
  catch (e) { transOut.value = 'AI 调用失败：' + apiErr(e) }
  finally { toolBusy.value = false }
}

function apiErr(e) {
  const m = String((e && e.message) || e || '')
  if (/Failed to fetch|NetworkError|Load failed/.test(m)) return '连不上本地服务：先确认软件开着，或去设置 →「AI 模型」点「重启内置服务」'
  if (/401/.test(m)) return 'API Key 不对（401）：去设置 →「AI 模型」检查 Key 是否复制完整'
  if (/402/.test(m)) return '账户余额不足（402）'
  if (/429/.test(m)) return '调用太频繁（429）：等半分钟再试，或改用本地模型'
  return m || '未知错误'
}

function copyText(t) { try { navigator.clipboard.writeText(t) } catch { /* 忽略 */ } }

function toDesk(t) {
  try {
    const raw = localStorage.getItem('tiku-desk-text')
    const cur = raw ? JSON.parse(raw) : { title: '', stem: '', answer: '' }
    cur.stem = (cur.stem ? cur.stem + '\n' : '') + t
    localStorage.setItem('tiku-desk-text', JSON.stringify(cur))
  } catch { /* 忽略 */ }
  location.hash = '#/author?mode=doc'
}

const tab = ref(new URLSearchParams(location.hash.split('?')[1] || '').get('tab') || 'gen')  // gen | read | chat | model

// ---------------------------------------------------------------- 对话
const msgs = reactive([])
const input = ref('')
const busy = ref(false)
const err = ref('')
const boxRef = ref(null)

async function scrollDown() {
  await nextTick()
  const el = boxRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  err.value = ''
  msgs.push({ role: 'user', text })
  await scrollDown()
  busy.value = true
  const reply = reactive({ role: 'assistant', text: '' })
  msgs.push(reply)
  try {
    const res = await fetch('/api/ai/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: msgs.filter(m => m.text).map(m => ({ role: m.role, content: m.text })),
        page: 'AI 工作台',
        search: false,
      }),
    })
    if (!res.ok || !res.body) throw new Error((await res.text()) || 'AI 没有响应')
    const reader = res.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() || ''
      for (const ln of lines) {
        const s = ln.trim()
        if (!s.startsWith('data:')) continue
        const payload = s.slice(5).trim()
        if (!payload || payload === '[DONE]') continue
        try {
          const j = JSON.parse(payload)
          const delta = j.delta || j.text || j.content || (j.choices && j.choices[0]?.delta?.content) || ''
          if (delta) { reply.text += delta; await scrollDown() }
        } catch { /* 非 JSON 的分片直接忽略 */ }
      }
    }
    if (!reply.text) reply.text = '（这次没有返回内容，换个问法或去设置里检查模型配置）'
  } catch (e) {
    err.value = e?.message || String(e)
    reply.text = reply.text || '（出错了）'
  } finally {
    busy.value = false
    await scrollDown()
  }
}

function clearChat() { msgs.splice(0, msgs.length); err.value = '' }

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() }
}

// ---------------------------------------------------------------- 模型状态
const status = ref(null)
onMounted(async () => {
  try { status.value = await api.get('/api/ai/status') } catch { status.value = null }
})
</script>

<template>
  <div class="ai">
    <div class="ahead">
      <h2>AI 工作台</h2>
      <div class="subtabs">
        <button :class="{ on: tab === 'gen' }" @click="tab = 'gen'">智能出题</button>
        <button :class="{ on: tab === 'read' }" @click="tab = 'read'">精读文献</button>
        <button :class="{ on: tab === 'tools' }" @click="tab = 'tools'">小工具</button>
        <button :class="{ on: tab === 'chat' }" @click="tab = 'chat'">对话</button>
        <button :class="{ on: tab === 'model' }" @click="tab = 'model'">模型与用量</button>
      </div>
    </div>

    <div v-show="tab === 'gen'" class="pane">
      <SmartPaper @go-read="tab = 'read'" />
    </div>

    <div v-show="tab === 'read'" class="pane read">
      <Reader />
    </div>

    <div v-show="tab === 'tools'" class="pane tools">
      <div class="tool">
        <div class="ttl"><b>给想法，写题干</b><span class="mut">把一句话的想法扩成竞赛题的引子/背景，可直接粘进文档模式</span></div>
        <textarea v-model="idea" rows="3" placeholder="例如：想让考生用格氏试剂做一个环丙烷并环，考立体化学"></textarea>
        <div class="trow">
          <select v-model="ideaStyle">
            <option value="intro">写成题干引子（背景+已知信息）</option>
            <option value="full">写成一整道大题（含 3 小问）</option>
            <option value="hint">只写「考察意图」给命题人看</option>
          </select>
          <button class="primary sm" :disabled="toolBusy || !idea.trim()" @click="runIdea">{{ toolBusy ? '正在写…' : '生成' }}</button>
          <span class="spacer"></span>
          <button class="ghost sm" :disabled="!ideaOut" @click="copyText(ideaOut)">复制</button>
          <button class="ghost sm" :disabled="!ideaOut" @click="toDesk(ideaOut)">放进题目文本</button>
        </div>
        <pre v-if="ideaOut" class="out">{{ ideaOut }}</pre>
      </div>

      <div class="tool">
        <div class="ttl"><b>翻译（保留公式与上下标）</b><span class="mut">英文文献/题干 → 中文；也可中译英</span></div>
        <textarea v-model="srcText" rows="4" placeholder="粘一段英文（公式写成 $K_a$ 或 \ce{...} 都行）"></textarea>
        <div class="trow">
          <select v-model="direction">
            <option value="en2zh">英文 → 中文（化学用词按竞赛习惯）</option>
            <option value="zh2en">中文 → 英文</option>
          </select>
          <button class="primary sm" :disabled="toolBusy || !srcText.trim()" @click="runTrans">{{ toolBusy ? '正在翻…' : '翻译' }}</button>
          <span class="spacer"></span>
          <button class="ghost sm" :disabled="!transOut" @click="copyText(transOut)">复制</button>
        </div>
        <pre v-if="transOut" class="out">{{ transOut }}</pre>
      </div>
    </div>

    <div v-show="tab === 'chat'" class="pane chat">
      <div class="chead">
        <span class="hint">问化学问题、让它解释答案、帮你改题干都行。回答里会尽量保留公式排版。</span>
        <span class="spacer"></span>
        <button class="ghost sm" :disabled="busy || !msgs.length" @click="clearChat">清空</button>
      </div>
      <div ref="boxRef" class="cbox">
        <div v-if="!msgs.length" class="empty">
          <p>可以这样问：</p>
          <ul>
            <li>「解释一下为什么苯甲酸比苯酚酸性强」</li>
            <li>「把这段题干改得更像竞赛风格：……」</li>
            <li>「这道题的答案为什么要分三步算？」</li>
          </ul>
        </div>
        <div v-for="(m, i) in msgs" :key="i" class="msg" :class="m.role">
          <div class="who">{{ m.role === 'user' ? '我' : 'AI' }}</div>
          <div class="bub"><span style="white-space:pre-wrap">{{ m.text || (busy ? '…' : '') }}</span></div>
        </div>
      </div>
      <div class="cinput">
        <textarea v-model="input" rows="2" placeholder="Enter 发送，Shift+Enter 换行"
                  :disabled="busy" @keydown="onKey"></textarea>
        <button class="primary" :disabled="busy || !input.trim()" @click="send">{{ busy ? '思考中…' : '发送' }}</button>
      </div>
      <p v-if="err" class="bad">{{ err }}</p>
    </div>

    <div v-show="tab === 'model'" class="pane model">
      <div class="card">
        <h3>当前模型</h3>
        <p v-if="status" class="muted">
          状态：{{ status.ok === false ? '没配好' : (status.enabled === false ? '没启用' : '可用') }}
          <template v-if="status.model">　模型：{{ status.model }}</template>
          <template v-if="status.base">　接口：{{ status.base }}</template>
        </p>
        <p v-else class="muted">读不到 AI 状态（后端可能没起来）。</p>
        <p class="muted">换模型、填 key 在「设置 → AI 模型」里；这里只看用量。</p>
      </div>
      <AiQuota />
    </div>
  </div>
</template>

<style scoped>
.pane.read { min-height: 0; }
.ai { display: flex; flex-direction: column; min-height: calc(100vh - 56px); }
.ahead { display: flex; align-items: center; gap: 16px; margin-bottom: 10px; }
.ahead h2 { margin: 0; }
.subtabs { display: flex; gap: 6px; }
.subtabs button { height: 30px; padding: 0 14px; border-radius: 999px; border: 1px solid #e4e7ec; background: #fff; font-size: 13px; cursor: pointer; }
.subtabs button.on { background: #2f6fed; border-color: #2f6fed; color: #fff; font-weight: 600; }
.pane { flex: 1; min-height: 0; }
.pane.tools { overflow: auto; padding: 4px 2px 20px; display: grid; gap: 12px; }
.tool { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }
.tool .ttl { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.tool .ttl .mut { font-size: 12px; }
.tool textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 10px; padding: 9px 10px; font: inherit; font-size: 13px; line-height: 1.7; resize: vertical; }
.tool .trow { display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.tool .trow select { height: 30px; border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 0 8px; font-size: 13px; }
.tool .out { white-space: pre-wrap; background: #f8fafc; border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; font: inherit; font-size: 13px; line-height: 1.8; margin: 10px 0 0; }
.chat { display: flex; flex-direction: column; background: #fff; border: 1px solid #e9edf3; border-radius: 12px; overflow: hidden; }
.chead { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #eef1f6; }
.chead .spacer { flex: 1; }
.hint { font-size: 12.5px; color: #667085; }
.cbox { flex: 1; overflow: auto; padding: 14px; background: #fbfcfe; }
.empty { color: #98a2b3; font-size: 13px; }
.empty ul { margin: 6px 0 0; padding-left: 20px; }
.empty li { margin: 3px 0; }
.msg { display: flex; gap: 10px; margin-bottom: 12px; align-items: flex-start; }
.msg .who { flex: 0 0 30px; text-align: center; font-size: 12px; color: #667085; background: #eef1f6; border-radius: 50%; width: 30px; height: 30px; line-height: 30px; }
.msg.user .who { background: #e8f0ff; color: #2f6fed; }
.bub { background: #fff; border: 1px solid #e9edf3; border-radius: 10px; padding: 8px 12px; font-size: 13.5px; line-height: 1.75; max-width: 860px; }
.msg.user .bub { background: #f2f7ff; border-color: #dbe8ff; }
.cinput { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #eef1f6; align-items: flex-end; }
.cinput textarea { flex: 1; border: 1px solid #e4e7ec; border-radius: 10px; padding: 8px 10px; font-size: 13.5px; resize: vertical; font-family: inherit; }
.cinput .primary { height: 36px; padding: 0 18px; }
.bad { margin: 0 12px 10px; color: #b42318; font-size: 12.5px; }
.model .card { margin-bottom: 12px; }
</style>
