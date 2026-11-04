<script setup>
/** 批量拆题：多份 PDF 一次拖进来，逐份拆题入库，自动带上答案 */
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ embedded: { type: Boolean, default: false } })
const emit = defineEmits(['done'])

const books = ref([])
const bookId = ref(null)
const label = ref('')
const files = ref([])
const busy = ref(false)
const err = ref('')
const result = ref(null)
const dropHot = ref(false)

async function loadBooks() {
  try {
    books.value = await api.get('/api/books')
    if (!bookId.value && books.value.length) bookId.value = books.value[0].id
  } catch (e) { err.value = e.message }
}
onMounted(loadBooks)

function pick(e) {
  files.value = Array.from(e.target.files || [])
}
function onDrop(e) {
  e.preventDefault()
  dropHot.value = false
  const list = Array.from(e.dataTransfer.files || []).filter(f => /pdf$/i.test(f.name))
  if (list.length) files.value = list
}
async function run() {
  if (!files.value.length) { err.value = '先选 PDF'; return }
  busy.value = true
  err.value = ''
  result.value = null
  try {
    const fd = new FormData()
    files.value.forEach(f => fd.append('files', f))
    if (bookId.value) fd.append('book_id', String(bookId.value))
    fd.append('label', label.value || '')
    result.value = await api.postForm('/api/wb/extract', fd)
    emit('done', result.value)
  } catch (e) {
    err.value = e.message
  } finally { busy.value = false }
}
</script>

<template>
  <template v-if="!props.embedded">
    <h2>批量拆题</h2>
  </template>
  <p class="muted" :class="{ tight: props.embedded }">
    一次放多份 PDF（试卷 / 习题册），系统逐份拆成题目入库：题干、选项、答案、解析一起带走；
    没写答案的题会让 AI 补，找不到就留空不瞎编。
  </p>

  <div class="card">
    <div class="row">
      <label class="muted">入库到</label>
      <select v-model.number="bookId" style="min-width:220px">
        <option v-for="b in books" :key="b.id" :value="b.id">{{ b.title }}</option>
      </select>
      <label class="muted">来源标签</label>
      <input v-model="label" placeholder="如：2024 真题 / 某校月考" style="min-width:220px" />
    </div>

    <div class="drop" :class="{ hot: dropHot }"
         @dragover.prevent="dropHot = true"
         @dragleave.prevent="dropHot = false"
         @drop="onDrop">
      <input type="file" accept="application/pdf" multiple @change="pick" />
      <p class="muted">把多份 PDF 拖到这里，或点上面选择（可多选）</p>
      <ul v-if="files.length" class="flist">
        <li v-for="f in files" :key="f.name">{{ f.name }} <span class="muted">· {{ (f.size/1024).toFixed(0) }} KB</span></li>
      </ul>
    </div>

    <div class="row">
      <button class="primary" :disabled="busy || !files.length" @click="run">
        {{ busy ? '拆解中（一份约 1-2 分钟）…' : `开始拆题（${files.length} 份）` }}
      </button>
      <span v-if="busy" class="muted">AI 正在逐份阅读，别关页面…</span>
    </div>
  </div>

  <div v-if="err" class="errbox">{{ err }}</div>

  <div v-if="result" class="card">
    <h3>结果：新增 {{ result.total }} 题<template v-if="!result.total">（都与题库里已有的题目重复，已跳过）</template></h3>
    <table class="tbl">
      <thead><tr><th>文件</th><th>识别标题</th><th>新增题数</th><th>说明</th></tr></thead>
      <tbody>
        <tr v-for="f in result.files" :key="f.file">
          <td>{{ f.file }}</td>
          <td class="muted">{{ f.title || '—' }}</td>
          <td>{{ f.questions }}</td>
          <td :class="{ bad: f.error }">{{ f.error || (f.questions ? '已入库' : '重复，已跳过') }}</td>
        </tr>
      </tbody>
    </table>
    <p class="muted">拆出来的题是「草稿」状态，到题库里逐题核对后点「确认」即可。</p>
  </div>
</template>

<style scoped>
.tight { margin-top: 0; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.drop { border: 1px dashed #c7d2e4; border-radius: 10px; padding: 14px; background: #f8fbff; margin: 8px 0; }
.drop.hot { border-color: #2f6fed; background: #eef4ff; }
.flist { margin: 8px 0 0; padding-left: 18px; font-size: 12.5px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #f1f2f4; }
.tbl th { color: #6b7280; font-weight: 500; }
.bad { color: #b91c1c; }
</style>
