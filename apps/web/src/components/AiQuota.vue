<script setup>
/** 免费 AI 额度/用量面板：本机 DSH 实测数据 + 官方账户余额 */
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ compact: { type: Boolean, default: false } })
const data = ref(null)
const err = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    data.value = await api.get('/api/wb/ai-quota')
    err.value = ''
  } catch (e) {
    err.value = e.message
  } finally {
    loading.value = false
  }
}
onMounted(load)
defineExpose({ load })

function fmt(n) {
  const v = Number(n || 0)
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'k'
  return String(v)
}
function secs(ms) { return ms ? (ms / 1000).toFixed(1) + 's' : '—' }
</script>

<template>
  <div class="aiquota" :class="{ compact: props.compact }">
    <div class="q-head">
      <b>免费模型用量</b>
      <span class="muted" v-if="data">统计日 {{ data.date }}</span>
      <span class="spacer"></span>
      <button class="ghost sm" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
    </div>
    <p v-if="err" class="errline">{{ err }}</p>
    <p v-if="data && !data.models.length" class="muted">
      还没有调用记录。配好模型后，这里会按模型统计调用次数、token 与首字延迟，方便挑“又快又稳”的那个。
    </p>
    <table v-if="data && data.models.length" class="qt">
      <thead>
        <tr><th>模型</th><th>今日</th><th>累计调用</th><th>失败</th><th>输出 tokens</th><th>首字</th></tr>
      </thead>
      <tbody>
        <tr v-for="m in data.models" :key="m.model" :title="m.description || m.model">
          <td class="mname">{{ m.model }}</td>
          <td>{{ m.today_calls }}</td>
          <td>{{ m.calls }}</td>
          <td :class="{ bad: m.failed > 0 }">{{ m.failed }}</td>
          <td>{{ fmt(m.output) }}</td>
          <td>{{ secs(m.avg_ttft_ms) }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="data && data.balances.length" class="balrow">
      <span class="muted">官方账户余额：</span>
      <span v-for="b in data.balances" :key="b.provider" class="chip sm2">
        {{ b.provider }} {{ b.balance }} {{ b.currency }}
      </span>
    </div>
    <p v-if="data" class="muted tiny">{{ data.note }}</p>
  </div>
</template>

<style scoped>
.aiquota { border: 1px solid var(--line, #e5e7eb); border-radius: 10px; padding: 10px 12px; background: #fff; }
.aiquota.compact { padding: 8px 10px; }
.q-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.spacer { flex: 1; }
.qt { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.qt th, .qt td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #f1f2f4; white-space: nowrap; }
.qt th { color: #6b7280; font-weight: 500; }
.mname { max-width: 240px; overflow: hidden; text-overflow: ellipsis; }
.bad { color: #b91c1c; }
.balrow { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-top: 8px; font-size: 12.5px; }
.tiny { font-size: 11.5px; }
.errline { color: #b91c1c; font-size: 12.5px; }
</style>
