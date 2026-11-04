<script setup>
/**
 * QuizForge 题库：把 quizforge.db 里的题库列出来，逐个/批量导入本系统题库。
 * 侧边栏常驻入口，导完可直接跳题库按标签筛选。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api.js'
import WrongBooks from '../components/WrongBooks.vue'

const info = reactive({ found: false, db_path: '', hint: '', banks: [] })
const route = useRoute()
const focusBank = ref(String(route.query.bank || ''))
const busy = ref('')
const err = ref('')
const msg = ref('')

const pending = computed(() => info.banks.filter((b) => !b.imported))
const totalQ = computed(() => info.banks.reduce((n, b) => n + (b.question_count || 0), 0))

async function load() {
  err.value = ''
  try {
    const d = await api.get('/api/import/quizforge')
    info.found = d.found
    info.db_path = d.db_path || ''
    info.hint = d.hint || ''
    info.banks = d.banks || []
  } catch (e) { err.value = e.message }
}

async function importBanks(ids, label) {
  if (!ids.length) return
  busy.value = label
  err.value = ''
  try {
    const r = await api.post('/api/import/quizforge', { bank_ids: ids })
    msg.value = `${label} 完成：新增 ${r.questions_created} 题，补全 ${r.questions_updated} 题，新建书册 ${r.books_created} 个`
    await load()
  } catch (e) { err.value = e.message }
  busy.value = ''
}

function importOne(b) { importBanks([b.id], `导入「${b.name}」`) }
function importAll() { importBanks(pending.value.map((b) => b.id), '批量导入') }

async function uploadDb(event) {
  const file = event.target.files && event.target.files[0]
  if (!file) return
  busy.value = 'upload'
  err.value = ''
  const fd = new FormData()
  fd.append('file', file)
  try {
    const r = await api.postForm('/api/import/quizforge/upload', fd)
    msg.value = `上传导入完成：新增 ${r.questions_created} 题，补全 ${r.questions_updated} 题`
    await load()
  } catch (e) { err.value = e.message }
  busy.value = ''
  event.target.value = ''
}

onMounted(load)
</script>

<template>
  <h2>QuizForge 题库</h2>
  <p class="muted">
    这里直接读 QuizForge 的数据库，把它的题库搬进本系统：
    <code>题库 → 书册</code>、<code>题目 → 题目</code>，题型、选项、答案、解析、页码都带过来，重复导入只补空字段。
  </p>

  <WrongBooks />

  <div class="card qf-head">
    <div class="qf-path">
      <b>库文件</b>
      <code v-if="info.found">{{ info.db_path }}</code>
      <span v-else class="muted">{{ info.hint || '没找到 quizforge.db' }}</span>
    </div>
    <div class="qf-stat">
      <span class="chip">{{ info.banks.length }} 个题库</span>
      <span class="chip">{{ totalQ }} 题</span>
      <span class="chip ok" v-if="info.banks.length - pending.length">{{ info.banks.length - pending.length }} 个已导入</span>
    </div>
    <div class="qf-actions">
      <button class="ghost" @click="load">刷新</button>
      <label class="upload-inline">
        <input type="file" accept=".db,.sqlite,.sqlite3" @change="uploadDb" />
        <span>{{ busy === 'upload' ? '上传中…' : '上传 .db 文件' }}</span>
      </label>
      <button class="primary" :disabled="!pending.length || !!busy" @click="importAll">
        {{ busy === '批量导入' ? '导入中…' : `全部导入（${pending.length}）` }}
      </button>
    </div>
  </div>

  <div v-if="err" class="errbox">{{ err }}</div>
  <div v-if="msg" class="okbox">{{ msg }}</div>

  <div class="bgrid">
    <article v-for="b in info.banks" :key="b.id" class="bcard"
             :class="{ done: b.imported, focus: focusBank === b.id }" :id="'bank-' + b.id">
      <div class="b-top">
        <span class="bname">{{ b.name }}</span>
        <span class="chip" :class="b.imported ? 'ok' : ''">{{ b.imported ? `已导入 ${b.imported_count} 题` : '未导入' }}</span>
      </div>
      <div class="b-meta">
        <span>{{ b.question_count }} 题</span>
        <span v-if="b.created_at" class="muted">{{ String(b.created_at).slice(0, 10) }}</span>
        <span v-if="b.source_file" class="muted ellip" :title="b.source_file">{{ b.source_file }}</span>
      </div>
      <div class="b-actions">
        <button class="primary sm" :disabled="!!busy" @click="importOne(b)">
          {{ busy === `导入「${b.name}」` ? '导入中…' : (b.imported ? '重新同步' : '导入到题库') }}
        </button>
        <router-link v-if="b.imported" class="ghost sm link" :to="{ path: '/questions', query: { tag: 'QuizForge' } }">
          去题库查看 →
        </router-link>
      </div>
    </article>
  </div>

  <p v-if="!info.banks.length && !err" class="muted" style="padding:16px 0">
    没有可导入的题库。先在 QuizForge 里导入一份 PDF，或直接上传它的 .db 文件。
  </p>
</template>

<style scoped>
.qf-head { display: flex; flex-direction: column; gap: 10px; }
.qf-path { display: flex; gap: 8px; align-items: baseline; font-size: 13px; word-break: break-all; }
.qf-path code { background: #f4f6fb; padding: 2px 6px; border-radius: 6px; font-size: 12.5px; }
.qf-stat { display: flex; gap: 8px; flex-wrap: wrap; }
.qf-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.upload-inline { cursor: pointer; }
.upload-inline input { display: none; }
.upload-inline span {
  display: inline-block; padding: 7px 12px; border: 1px dashed var(--line);
  border-radius: 8px; font-size: 13px; color: #475467;
}
.bgrid { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
.bcard { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.bcard.done { border-color: #b7ebc6; background: #fbfefc; }
.bcard.focus { box-shadow: 0 0 0 2px rgba(47, 111, 237, .35); }
.b-top { display: flex; gap: 10px; align-items: center; justify-content: space-between; }
.bname { font-size: 14.5px; font-weight: 600; color: #101828; }
.b-meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12.5px; color: #667085; margin: 6px 0 10px; }
.ellip { max-width: 460px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.b-actions { display: flex; gap: 8px; align-items: center; }
.sm { padding: 5px 11px; font-size: 12.5px; }
.link { text-decoration: none; }
</style>
