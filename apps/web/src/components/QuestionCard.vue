<script setup>
import { ref } from 'vue'
import { api, DIFF_LABEL, SCOPE_LABEL, STATUS_LABEL } from '../api.js'

const props = defineProps({ q: { type: Object, required: true }, qtypes: { type: Array, default: () => [] } })
const emit = defineEmits(['changed'])

const showAns = ref(false)
const zoom = ref('')

function imgUrl(im) {
  const raw = typeof im === 'string' ? im : (im && im.path) || ''
  if (!raw) return ''
  if (raw.startsWith('/') || raw.startsWith('http')) return raw
  return '/api/images/' + raw.split('/').map(encodeURIComponent).join('/')
}
const editing = ref(false)
const err = ref('')
const form = ref({})

function startEdit() {
  form.value = {
    stem: props.q.stem, answer: props.q.answer, analysis: props.q.analysis || '',
    q_type: props.q.q_type, difficulty: props.q.difficulty,
    knowledge_points: (props.q.knowledge_points || []).join('、'),
  }
  editing.value = true
}

async function save() {
  err.value = ''
  try {
    const body = { ...form.value, knowledge_points: form.value.knowledge_points.split(/[、,，]/).map(s => s.trim()).filter(Boolean) }
    await api.patch(`/api/questions/${props.q.id}`, body)
    editing.value = false
    emit('changed')
  } catch (e) { err.value = e.message }
}

async function patch(body) {
  err.value = ''
  try { await api.patch(`/api/questions/${props.q.id}`, body); emit('changed') }
  catch (e) { err.value = e.message }
}

async function remove() {
  if (!confirm('确定删除这道题？')) return
  err.value = ''
  try { await api.del(`/api/questions/${props.q.id}`); emit('changed') }
  catch (e) { err.value = e.message }
}
</script>

<template>
  <div class="card">
    <div v-if="!editing">
      <div class="q-stem">{{ q.stem }}</div>
      <div v-if="q.images && q.images.length" class="qfigs">
        <img v-for="(im, i) in q.images" :key="i" :src="imgUrl(im)"
             :alt="(im && im.caption) || '题图'" loading="lazy" @click="zoom = imgUrl(im)" />
      </div>
      <div v-if="q.options && q.options.length" class="q-meta">
        <span v-for="(o, i) in q.options" :key="i" class="chip">{{ o }}</span>
      </div>
      <div class="q-meta">
        <span class="chip brand">{{ q.q_type }}</span>
        <span class="chip">{{ DIFF_LABEL[q.difficulty] }}</span>
        <span class="chip" :class="{ ok: q.scope_status === 'ok', warn: q.scope_status === 'extension', bad: q.scope_status === 'suspect' }">{{ SCOPE_LABEL[q.scope_status] }}</span>
        <span class="chip" :class="{ warn: q.status === 'draft' }">{{ STATUS_LABEL[q.status] || q.status }}</span>
        <span v-for="kp in q.knowledge_points" :key="kp" class="chip">{{ kp }}</span>
        <span v-if="q.unit_name" class="chip">{{ q.unit_name }}</span>
        <span class="muted">来源：{{ q.book_title }}</span>
      </div>
      <div v-if="q.scope_reason" class="warnbox" style="margin:8px 0 0">对纲说明：{{ q.scope_reason }}</div>
      <details style="margin-top:8px">
        <summary>答案{{ q.analysis ? ' / 解析' : '' }}</summary>
        <div class="q-answer"><span class="cap">答案</span>{{ q.answer }}</div>
        <div v-if="q.analysis" class="q-answer"><span class="cap">解析</span>{{ q.analysis }}</div>
      </details>
      <div class="btnrow" style="margin-top:10px">
        <button @click="startEdit">编辑</button>
        <button v-if="q.status !== 'confirmed'" @click="patch({ status: 'confirmed' })">确认入池</button>
        <button v-if="q.scope_status === 'suspect'" @click="patch({ scope_status: 'extension', scope_reason: q.scope_reason || '家长确认保留为提优题' })">标为提优</button>
        <button v-if="q.status !== 'rejected'" class="danger" @click="patch({ status: 'rejected' })">驳回</button>
        <button class="danger" @click="remove">删除</button>
      </div>
    </div>
    <div v-else>
      <label class="f"><span>题干（图形可用 ● ▲ ★ ■ ○ 等符号）</span><textarea v-model="form.stem"></textarea></label>
      <label class="f"><span>答案</span><textarea v-model="form.answer"></textarea></label>
      <label class="f"><span>解析（可空）</span><textarea v-model="form.analysis"></textarea></label>
      <div class="grid cols2">
        <label class="f"><span>题型</span>
          <select v-model="form.q_type">
            <option v-for="t in qtypes" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label class="f"><span>难度（1基础 2中等 3提优）</span>
          <select v-model="form.difficulty"><option :value="1">1</option><option :value="2">2</option><option :value="3">3</option></select>
        </label>
      </div>
      <label class="f"><span>知识点（顿号分隔）</span><input v-model="form.knowledge_points" /></label>
      <div v-if="err" class="errbox">{{ err }}</div>
      <div class="btnrow">
        <button class="primary" @click="save">保存</button>
        <button @click="editing = false">取消</button>
      </div>
    </div>
  </div>
  <div v-if="zoom" class="qzoom" @click="zoom = ''">
    <img :src="zoom" alt="题图" />
  </div>
</template>

<style scoped>
.qfigs { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 4px; }
.qfigs img {
  max-width: 300px; max-height: 180px; border: 1px solid #e6e9ef; border-radius: 6px;
  background: #fff; cursor: zoom-in; display: block;
}
.qzoom {
  position: fixed; inset: 0; z-index: 60; background: rgba(17, 24, 39, .72);
  display: flex; align-items: center; justify-content: center; padding: 24px; cursor: zoom-out;
}
.qzoom img { max-width: 94vw; max-height: 92vh; background: #fff; border-radius: 6px; box-shadow: 0 18px 50px rgba(0,0,0,.4); }
</style>
