<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api.js'

const route = useRoute()
const paper = ref(null)
const err = ref('')
const showAns = ref({})

function imgUrl(im) {
  const raw = typeof im === 'string' ? im : (im && im.path) || ''
  if (!raw) return ''
  if (raw.startsWith('/') || raw.startsWith('http')) return raw
  return '/api/images/' + raw.split('/').map(encodeURIComponent).join('/')
}

const sections = computed(() => {
  if (!paper.value) return []
  const by = {}
  for (const it of paper.value.items) {
    (by[it.snapshot.q_type] ||= []).push(it)
  }
  return Object.entries(by).map(([t, items]) => ({ type: t, items }))
})
const isCanvas = computed(() => !!(paper.value && paper.value.config && paper.value.config.source === 'canvas'))
const canvasImg = computed(() => (paper.value && paper.value.config && paper.value.config.image) || '')

const scoreSum = computed(() => (paper.value ? paper.value.items.reduce((s, i) => s + i.score, 0) : 0))

async function replace(item) {
  err.value = ''
  try {
    await api.post(`/api/papers/${paper.value.id}/replace`, { paper_question_id: item.id })
    paper.value = await api.get(`/api/papers/${paper.value.id}`)
  } catch (e) { err.value = e.message }
}
function toggleAns(item) {
  showAns.value[item.id] = !showAns.value[item.id]
}

onMounted(async () => {
  try { paper.value = await api.get(`/api/papers/${route.params.id}`) }
  catch (e) { err.value = e.message }
})
</script>

<template>
  <div v-if="err" class="errbox">{{ err }}</div>
  <template v-if="paper && isCanvas">
    <div class="btnrow" style="margin-bottom:12px">
      <a :href="`#/compose/canvas?id=${paper.id}`"><button class="primary">继续编排</button></a>
      <a :href="`/api/papers/${paper.id}/export-canvas`"><button>下载 PDF</button></a>
      <a href="#/compose/canvas"><button>新建一张</button></a>
      <a href="#/papers"><button>← 试卷列表</button></a>
    </div>
    <div class="card">
      <h2 style="text-align:center">{{ paper.title }}</h2>
      <p class="muted" style="text-align:center">
        {{ paper.subject }} · 限时 {{ paper.duration_minutes }} 分钟 · 画布编排
        <span v-if="paper.total_score"> · 总分 {{ paper.total_score }}</span>
      </p>
      <div style="text-align:center; margin-top:10px">
        <img :src="imgUrl(canvasImg)" alt="整卷" style="max-width:100%; border:1px solid var(--line); border-radius:8px" />
      </div>
    </div>
  </template>

  <template v-else-if="paper">
    <div class="btnrow" style="margin-bottom:12px">
      <a href="#/compose"><button>← 再组一卷</button></a>
      <a :href="`/api/papers/${paper.id}/export`"><button class="primary">下载 PDF（题目卷+答案卷）</button></a>
      <a :href="`/api/papers/${paper.id}/view`" target="_blank"><button>浏览器打印视图</button></a>
      <a href="#/papers"><button>← 试卷列表</button></a>
    </div>
    <div class="card">
      <h2 style="text-align:center">{{ paper.title }}</h2>
      <p class="muted" style="text-align:center">
        {{ paper.subject }} · 满分 {{ scoreSum }} 分 · 限时 {{ paper.duration_minutes }} 分钟
        <template v-if="paper.config && paper.config.source === 'manual'"> · 自选题目</template>
        <template v-else> · 范围：第 {{ paper.unit_from }}~{{ paper.unit_to }} 单元</template>
        <span v-if="paper.include_extension && !(paper.config && paper.config.source === 'manual')"> · 含提优拓展</span>
      </p>
      <div v-if="paper.config && paper.config.shortage && paper.config.shortage.length" class="warnbox">
        配额不足提示：{{ paper.config.shortage.join('；') }}。可去题库补题，或在下面逐题替换。
      </div>
      <div class="btnrow" style="justify-content:center">
        <span>姓名：＿＿＿＿＿＿</span><span>班级：＿＿＿＿＿＿</span><span>评分：＿＿＿＿＿＿</span>
      </div>
    </div>

    <div v-for="sec in sections" :key="sec.type" class="card">
      <h3>{{ sec.type }}（共 {{ sec.items.length }} 题，{{ sec.items.reduce((s, i) => s + i.score, 0) }} 分）</h3>
      <div v-for="(it, idx) in sec.items" :key="it.id" style="margin-bottom:14px">
        <div class="q-stem">{{ idx + 1 }}. {{ it.snapshot.stem }}</div>
        <div v-if="it.snapshot.images && it.snapshot.images.length" class="qfigs">
          <img v-for="(im, i) in it.snapshot.images" :key="i" :src="imgUrl(im)" alt="题图" loading="lazy" />
        </div>
        <div v-if="it.snapshot.options && it.snapshot.options.length" class="q-meta">
          <span v-for="(o, i) in it.snapshot.options" :key="i" class="chip">{{ o }}</span>
        </div>
        <div class="q-meta">
          <span class="chip">{{ it.score }} 分</span>
          <span v-if="it.snapshot.unit_name" class="chip">{{ it.snapshot.unit_name }}</span>
          <span v-for="kp in it.snapshot.knowledge_points" :key="kp" class="chip">{{ kp }}</span>
        </div>
        <div class="btnrow" style="margin-top:6px">
          <button @click="toggleAns(it)">看答案</button>
          <button @click="replace(it)">↻ 换一题</button>
        </div>
        <div v-if="showAns[it.id]" class="q-answer">
          <span class="cap">答案</span>{{ it.snapshot.answer }}
          <template v-if="it.snapshot.analysis"><br /><span class="cap">解析</span>{{ it.snapshot.analysis }}</template>
        </div>
      </div>
    </div>
  </template>
</template>

<style scoped>
.qfigs { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 6px; }
.qfigs img { max-width: 320px; max-height: 190px; border: 1px solid #e6e9ef; border-radius: 6px; background: #fff; }
</style>
