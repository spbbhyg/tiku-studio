<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { api } from '../api.js'

const stats = ref(null)
const papers = ref([])
const err = ref('')

const SUGGESTIONS = [
  '这个网站能做什么？',
  '怎么从 PDF 出一道题？',
  '题库怎么组卷和导出？',
  '整卷 PDF 怎么导入？',
  '免费模型怎么开？',
  '随便解释一个化学概念',
]

const persona = ref({ name: '小卷', tone: '说话直接、先给步骤，不懂会直说', greeting: '' })
const DEFAULT_HELLO = '老师好，我是小卷，这个网站的 AI 助教。网站功能、出题页按钮、组卷导出、免费模型配置都可以问我；也可以像普通 AI 一样问化学、写作、编程等通用问题。需要最新信息时，打开「联网搜索」再提问。'

const chat = ref([{ role: 'assistant', content: DEFAULT_HELLO }])

async function loadPersona() {
  try {
    const d = await api.get('/api/wb/settings/persona')
    const v = d && d.value
    if (v && typeof v === 'object') {
      persona.value = {
        name: v.name || '小卷',
        tone: v.tone || persona.value.tone,
        greeting: v.greeting || '',
      }
      chat.value = [{ role: 'assistant', content: persona.value.greeting || DEFAULT_HELLO }]
    }
  } catch (e) { /* 用默认人格 */ }
}
const draft = ref('')
const chatBusy = ref(false)
const chatErr = ref('')
const chatLog = ref(null)
const searchOn = ref(false)
let lastScrollAt = 0

function scrollChat() {
  nextTick(() => {
    if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight
  })
}

function streamScroll() {
  const now = Date.now()
  if (now - lastScrollAt > 130) {
    lastScrollAt = now
    scrollChat()
  }
}

async function sendChat(text) {
  const q = String(text ?? draft.value).trim()
  if (!q || chatBusy.value) return
  draft.value = ''
  chatErr.value = ''
  chat.value.push({ role: 'user', content: q })
  const history = chat.value
    .filter((m) => m.content && (m.role === 'user' || m.role === 'assistant'))
    .slice(-20)
    .map((m) => ({ role: m.role, content: m.content }))
  chat.value.push({ role: 'assistant', content: '', sources: [], search_error: '' })
  const idx = chat.value.length - 1
  scrollChat()
  chatBusy.value = true
  try {
    const res = await fetch('/api/ai/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: history, page: '首页', search: searchOn.value }),
    })
    if (!res.ok) {
      const raw = await res.text()
      let msg = raw
      try { msg = JSON.parse(raw).detail || raw } catch (e) { /* keep raw */ }
      throw new Error(msg || `请求失败（${res.status}）`)
    }
    if (!res.body) throw new Error('当前浏览器不支持流式读取')
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let streamErr = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let cut = -1
      while ((cut = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, cut)
        buf = buf.slice(cut + 2)
        let event = 'message'
        const dataLines = []
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (!dataLines.length) continue
        let obj = null
        try { obj = JSON.parse(dataLines.join('\n')) } catch (e) { continue }
        if (event === 'meta') {
          chat.value[idx].sources = obj.sources || []
          chat.value[idx].search_error = obj.search_error || ''
        } else if (event === 'delta') {
          chat.value[idx].content += obj.text || ''
          streamScroll()
        } else if (event === 'error') {
          streamErr = obj.message || 'AI 出错了'
        }
      }
    }
    if (streamErr) throw new Error(streamErr)
    if (!chat.value[idx].content.trim()) throw new Error('AI 没有返回内容')
  } catch (e) {
    if (!chat.value[idx].content) chat.value.splice(idx, 1)
    chatErr.value = String(e.message || e)
  } finally {
    chatBusy.value = false
    scrollChat()
  }
}

function onChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendChat()
  }
}

function clearChat() {
  chat.value = [{ role: 'assistant', content: '对话已清空。你可以重新问：这个网站怎么用？' }]
  chatErr.value = ''
  draft.value = ''
}

onMounted(async () => {
  loadPersona()
  try {
    stats.value = await api.get('/api/stats')
    papers.value = (await api.get('/api/papers')).slice(0, 5)
  } catch (e) { err.value = String(e.message || e) }
})
</script>

<template>
  <h2>首页</h2>
  <div v-if="err" class="errbox">{{ err }}</div>

  <div class="card aichat">
    <div class="chathead">
      <div class="persona">
        <div class="avatar">卷</div>
        <div>
          <h3 style="margin:0">{{ persona.name }} <span class="chip brand">站内助教</span></h3>
          <p class="persona-sub">文献命题 · 题库 · 组卷 · 通用问答</p>
        </div>
      </div>
      <button class="ghost" :disabled="chatBusy" @click="clearChat">清空</button>
    </div>
    <p class="muted chatintro">
      {{ persona.name }}：{{ persona.tone }}。可以问本站功能、操作步骤和按钮位置，也可以问化学、写作、编程等通用问题。
      回答由「设置 → AI 模型」里当前的模型提供，默认逐字输出；需要最新信息时打开下面的「联网搜索」。
    </p>
    <div class="sugs">
      <button v-for="s in SUGGESTIONS" :key="s" class="chip sug" :disabled="chatBusy" @click="sendChat(s)">
        {{ s }}
      </button>
    </div>
    <div class="chattools">
      <label class="searchchk">
        <input v-model="searchOn" type="checkbox" :disabled="chatBusy" />
        联网搜索
      </label>
      <span class="muted">开启后先搜 Bing 网页再回答，并在回答下方附来源；搜索失败时会明确说明。</span>
    </div>
    <div ref="chatLog" class="chatlog">
      <div v-for="(m, i) in chat" :key="i" class="msg" :class="m.role">
        <div class="bubblewrap">
          <div class="bubble">
            <span v-if="!m.content && m.role === 'assistant' && chatBusy" class="typing">思考中…</span>
            <template v-else>{{ m.content }}</template>
          </div>
          <div v-if="m.sources && m.sources.length" class="sources">
            <span class="muted">参考来源：</span>
            <a v-for="(s, si) in m.sources" :key="s.url" :href="s.url" target="_blank" rel="noopener noreferrer">
              [{{ si + 1 }}] {{ s.title }}
            </a>
          </div>
          <p v-if="m.search_error" class="muted sourceerr">联网搜索没有成功：{{ m.search_error }}</p>
        </div>
      </div>
    </div>
    <div class="chatinput">
      <textarea v-model="draft" rows="2" placeholder="问本站用法，也可以问一般的化学/写作/编程问题…"
                @keydown="onChatKey"></textarea>
      <button class="primary" :disabled="chatBusy || !draft.trim()" @click="sendChat()">
        {{ chatBusy ? '回答中…' : '发送' }}
      </button>
    </div>
    <p v-if="chatErr" class="errbox" style="margin-top:10px">{{ chatErr }}</p>
    <p class="muted chatnote">
      支持多轮对话和逐字输出；对话过长时点「清空」开新对话即可。
      联网搜索来自 Bing，结果可能不完整或过期，引用链接请自行核对。
      用本机免费模型时，需要 DeepSeek Harness 和免费模型插件在后台运行；DSH 关掉后这里会连接失败。
    </p>
  </div>

  <template v-if="stats">
    <div class="grid cols4">
      <div class="card stat"><div class="num">{{ stats.books }}</div><div class="label">书册（含 {{ stats.textbooks }} 本课本纲）</div></div>
      <div class="card stat"><div class="num">{{ stats.questions }}</div><div class="label">题目（已确认 {{ stats.confirmed_questions }}）</div></div>
      <div class="card stat"><div class="num">{{ stats.draft_questions }}</div><div class="label">草稿题目</div></div>
      <div class="card stat"><div class="num">{{ stats.suspect_questions }}</div><div class="label">疑似超纲（对纲校验）</div></div>
    </div>
    <div class="card">
      <h3>科目分布</h3>
      <span v-for="(n, s) in stats.by_subject" :key="s" class="chip brand">{{ s }}：{{ n }} 题</span>
      <h3>题型分布</h3>
      <span v-for="(n, t) in stats.by_type" :key="t" class="chip">{{ t }} {{ n }}</span>
    </div>
    <div class="card">
      <h3>最近试卷</h3>
      <p v-if="!papers.length" class="muted">还没有试卷，去<a href="#/questions"> 题库 </a>选题组卷生成第一份。</p>
      <table v-else class="table">
        <tr><th>标题</th><th>题数</th><th>总分</th><th>限时</th></tr>
        <tr v-for="p in papers" :key="p.id">
          <td><router-link :to="`/papers/${p.id}`">{{ p.title }}</router-link></td>
          <td>{{ p.question_count }}</td><td>{{ p.total_score }}</td><td>{{ p.duration_minutes }} 分钟</td>
        </tr>
      </table>
    </div>
  </template>
</template>

<style scoped>
.aichat { margin-bottom: 14px; }
.chathead { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.persona { display: flex; align-items: center; gap: 10px; min-width: 0; }
.avatar {
  width: 38px; height: 38px; border-radius: 12px; flex: none;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 18px; font-weight: 700;
  background: linear-gradient(135deg, #2f6fed, #7c9cff);
  box-shadow: 0 2px 8px rgba(47, 111, 237, .28);
}
.persona-sub { margin: 2px 0 0; font-size: 12px; color: var(--muted); }
.chatintro { margin: 8px 0 10px; font-size: 13px; }
.sugs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.sug { cursor: pointer; background: #fff; }
.sug:hover { border-color: var(--brand); color: var(--brand); }
.chattools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; font-size: 12.5px; }
.searchchk {
  display: inline-flex; align-items: center; gap: 5px; cursor: pointer;
  padding: 4px 10px; border: 1px solid var(--line); border-radius: 999px; background: #fff;
}
.searchchk input { width: auto; margin: 0; }
.chatlog {
  max-height: 440px; overflow: auto; padding: 10px;
  background: var(--bg); border: 1px solid var(--line); border-radius: 10px;
}
.msg { display: flex; margin: 6px 0; }
.msg.user { justify-content: flex-end; }
.bubblewrap { max-width: 84%; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.bubble {
  white-space: pre-wrap; line-height: 1.65; font-size: 14px;
  padding: 9px 12px; border-radius: 10px; background: #fff; border: 1px solid var(--line);
  overflow-wrap: anywhere;
}
.msg.user .bubble { background: var(--brand-soft); border-color: #cfe0ff; }
.typing { color: var(--muted); }
.sources { display: flex; flex-wrap: wrap; gap: 6px 10px; font-size: 12.5px; line-height: 1.5; }
.sources a { color: var(--brand); text-decoration: none; }
.sources a:hover { text-decoration: underline; }
.sourceerr { margin: 0; font-size: 12px; }
.chatinput { display: flex; align-items: flex-end; gap: 8px; margin-top: 10px; }
.chatinput textarea { flex: 1; width: auto; min-height: 54px; resize: vertical; }
.chatinput button { height: 38px; }
.chatnote { margin: 8px 0 0; font-size: 12px; }
</style>
