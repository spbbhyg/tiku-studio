<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'

const settings = ref(null)
const bundle = ref(null)
const busy = ref(false)
const err = ref('')
const form = reactive({
  file: null, subject: '数学', grade: 1, semester: '上册', publisher: '人教版',
  edition_year: 2024, role: 'workbook', title: '',
})

async function loadSettings() {
  try { settings.value = await api.get('/api/settings') } catch (e) { err.value = e.message }
}

async function parse() {
  err.value = ''; bundle.value = null
  if (!form.file) { err.value = '请先选择 PDF 文件'; return }
  busy.value = true
  try {
    const fd = new FormData()
    fd.append('file', form.file)
    for (const k of ['subject', 'semester', 'publisher', 'role']) fd.append(k, form[k])
    fd.append('grade', String(form.grade))
    fd.append('edition_year', String(form.edition_year))
    if (form.title) fd.append('title', form.title)
    bundle.value = await api.postForm('/api/ingest/parse', fd)
  } catch (e) { err.value = e.message }
  busy.value = false
}

async function commit() {
  err.value = ''
  try {
    const r = await api.post('/api/ingest/import', bundle.value)
    alert(`导入成功：新增 ${r.questions_new} 题，更新 ${r.questions_updated} 题（以草稿状态入库，可在题库里确认）`)
    bundle.value = null
  } catch (e) { err.value = e.message }
}

onMounted(loadSettings)
</script>

<template>
  <h2>AI 拆解</h2>
  <div class="card">
    <h3>方式一：本地 Agent 拆解（推荐，免费）</h3>
    <p style="margin:6px 0">
      在项目目录打开 ZCode / Claude Code，按 <code>agent/AGENTS.md</code> 的手册操作，
      拆解产物 JSON 会投递到 <code>data/inbox/</code>，在 <a href="#/books">资料库</a> 页一键导入。
    </p>
    <p class="muted">适合：整册课本目录级拆解、整本教辅题目级拆解、从网站抓取资料。</p>
  </div>

  <div class="card">
    <h3>方式二：网页上传 PDF → 内置视觉模型拆解（实验性）</h3>
    <div v-if="settings && !settings.llm_api_key" class="warnbox">
      尚未配置 LLM API key。请先到 <a href="#/settings">设置</a> 填写（智谱 GLM / DeepSeek / OpenAI 兼容接口均可）。
    </div>
    <div class="grid cols3">
      <label class="f"><span>PDF 文件</span><input type="file" accept="application/pdf" @change="e => form.file = e.target.files[0]" /></label>
      <label class="f"><span>资料类型</span>
        <select v-model="form.role">
          <option value="textbook">课本（建纲）</option>
          <option value="workbook">教辅</option>
          <option value="paper_set">试卷集</option>
        </select>
      </label>
      <label class="f"><span>科目</span>
        <select v-model="form.subject"><option>数学</option><option>语文</option><option>英语</option></select>
      </label>
      <label class="f"><span>年级</span>
        <select v-model="form.grade">
          <option v-for="g in [1,2,3,4,5,6]" :key="g" :value="g">{{ g }}年级</option>
        </select>
      </label>
      <label class="f"><span>学期</span>
        <select v-model="form.semester"><option>上册</option><option>下册</option><option>全一册</option></select>
      </label>
      <label class="f"><span>出版社/版本</span><input v-model="form.publisher" /></label>
      <label class="f"><span>版别年份</span><input type="number" v-model.number="form.edition_year" /></label>
      <label class="f"><span>标题（留空用文件名）</span><input v-model="form.title" /></label>
    </div>
    <div v-if="err" class="errbox">{{ err }}</div>
    <div class="btnrow">
      <button class="primary" :disabled="busy || (settings && !settings.llm_api_key)" @click="parse">
        {{ busy ? '拆解中…（可能需要几分钟）' : '开始拆解' }}
      </button>
      <span class="muted">拆解结果先预览，确认后才入库；入库后先在题库里以草稿状态出现，确认后即可参与组卷。</span>
    </div>
    <template v-if="bundle">
      <h3>拆解预览：{{ bundle.book?.title }}（{{ (bundle.questions || []).length }} 题）</h3>
      <div v-for="(q, i) in (bundle.questions || []).slice(0, 10)" :key="i" class="q-answer" style="background:#f6f8fb;border-left-color:#b9c4d6">
        <b>{{ i + 1 }}. [{{ q.q_type }}·难度{{ q.difficulty }}]</b> {{ q.stem }}
        <div class="muted">答案：{{ q.answer }} ｜ 考点：{{ (q.knowledge_points || []).join('、') }}</div>
      </div>
      <p v-if="(bundle.questions || []).length > 10" class="muted">…仅显示前 10 题，导入后可在题库查看全部。</p>
      <button class="primary" @click="commit">确认无误，导入题库</button>
    </template>
  </div>
</template>
