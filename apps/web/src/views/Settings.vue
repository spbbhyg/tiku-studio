<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'

const form = reactive({ llm_provider: 'glm', llm_base_url: '', llm_api_key: '', llm_model: '', max_pages: 30 })
const err = ref('')
const ok = ref('')

async function load() {
  try { Object.assign(form, await api.get('/api/settings')) } catch (e) { err.value = e.message }
}
async function save() {
  err.value = ''; ok.value = ''
  try {
    Object.assign(form, await api.put('/api/settings', { ...form }))
    ok.value = '已保存'
  } catch (e) { err.value = e.message }
}

onMounted(load)
</script>

<template>
  <h2>设置</h2>
  <div class="card" style="max-width:560px">
    <h3>内置 LLM 拆解引擎（可选）</h3>
    <p class="muted">不配置也能正常使用：组卷、Agent 拆解、打印导出都不依赖它。配置后可在「AI 拆解」页直接上传 PDF 自动拆题。</p>
    <label class="f"><span>供应商</span>
      <select v-model="form.llm_provider">
        <option value="glm">智谱 GLM（视觉：glm-4v-plus）</option>
        <option value="deepseek">DeepSeek</option>
        <option value="openai">OpenAI</option>
        <option value="custom">自定义（OpenAI 兼容）</option>
      </select>
    </label>
    <label class="f"><span>Base URL（custom 时必填）</span><input v-model="form.llm_base_url" placeholder="https://..." /></label>
    <label class="f"><span>API Key</span><input type="password" v-model="form.llm_api_key" placeholder="已保存时显示掩码" /></label>
    <label class="f"><span>模型（留空用供应商默认）</span><input v-model="form.llm_model" /></label>
    <label class="f"><span>每次最多拆解页数</span><input type="number" v-model.number="form.max_pages" /></label>
    <div v-if="err" class="errbox">{{ err }}</div>
    <div class="btnrow">
      <button class="primary" @click="save">保存</button>
      <span v-if="ok" style="color:var(--ok)">{{ ok }}</span>
    </div>
  </div>
  <div class="card" style="max-width:560px">
    <h3>数据位置</h3>
    <p class="muted" style="margin:4px 0">
      数据库与资料：<code>data/</code>（不入 git）· Agent 投递箱：<code>data/inbox/</code> ·
      题图与原件库：<code>data/library/</code>
    </p>
  </div>
</template>
