/// 一张插图的行内编辑条：缩略图 + 图注 + 宽度 + 对齐 + 排序
/// 直接改传进来的 fig 对象（父组件拿的是同一个引用），不需要回传。
<template>
  <div class="fe">
    <img :src="'/api/images/' + fig.path" class="fe-thumb" />
    <input v-model="fig.caption" class="fe-cap" placeholder="图注（留空自动编「图 N」）" @input="$emit('change')" />
    <label class="fe-w">
      <span>宽</span>
      <input v-model.number="fig.w" type="number" min="20" max="415" step="10" class="fe-num" @input="$emit('change')" />
      <span>pt</span>
    </label>
    <select v-model="fig.align" class="fe-al" @change="$emit('change')">
      <option value="center">居中</option>
      <option value="left">居左</option>
      <option value="right">居右</option>
    </select>
    <button class="fe-b" :disabled="!canUp" title="上移" @click="$emit('up')">↑</button>
    <button class="fe-b" :disabled="!canDown" title="下移" @click="$emit('down')">↓</button>
    <button class="fe-b del" title="删除" @click="$emit('del')">×</button>
  </div>
</template>

<script setup>
defineProps({
  fig: { type: Object, required: true },
  canUp: { type: Boolean, default: false },
  canDown: { type: Boolean, default: false },
})
defineEmits(['del', 'up', 'down', 'change'])
</script>

<style scoped>
.fe { display: flex; align-items: center; gap: 6px; padding: 4px 6px; border: 1px solid #e5e7eb; border-radius: 6px; background: #fff; }
.fe-thumb { max-height: 40px; max-width: 90px; border-radius: 4px; background: #fff; }
.fe-cap { flex: 1; min-width: 80px; height: 26px; border: 1px solid #e5e7eb; border-radius: 5px; padding: 0 6px; font-size: 12.5px; }
.fe-w { display: flex; align-items: center; gap: 3px; font-size: 12px; color: #6b7280; }
.fe-num { width: 58px; height: 26px; border: 1px solid #e5e7eb; border-radius: 5px; padding: 0 4px; font-size: 12.5px; text-align: right; }
.fe-al { height: 26px; border: 1px solid #e5e7eb; border-radius: 5px; font-size: 12.5px; background: #fff; }
.fe-b { width: 24px; height: 26px; border: 1px solid #e5e7eb; border-radius: 5px; background: #fff; cursor: pointer; font-size: 12px; }
.fe-b:disabled { opacity: .4; cursor: default; }
.fe-b.del { color: #b91c1c; border-color: #fecaca; }
</style>
