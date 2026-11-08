<template>
  <svg
    class="ic"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, i) in paths" :key="i" :d="d" />
    <circle v-for="(c, i) in circles" :key="'c' + i" :cx="c[0]" :cy="c[1]" :r="c[2]" />
    <rect
      v-for="(r, i) in rects"
      :key="'r' + i"
      :x="r[0]"
      :y="r[1]"
      :width="r[2]"
      :height="r[3]"
      :rx="r[4] || 0"
    />
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 17 },
  stroke: { type: [Number, String], default: 1.7 },
})

// 只用路径/圆/矩形三种图元，够画这一套工具栏图标
const ICONS = {
  // 文档与素材
  pdf: { p: ['M7 3h7l4 4v14H7z', 'M14 3v4h4', 'M10 12h5', 'M10 16h5'] },
  crop: { p: ['M7 3v13a1 1 0 0 0 1 1h13', 'M3 7h13a1 1 0 0 1 1 1v13'] },
  formula: { p: ['M18 5H7l6 7-6 7h11'] },
  structure: { p: ['M12 3.5 19 7.5v9L12 20.5 5 16.5v-9z', 'M9 10.5h6v4H9z'] },
  subs: { p: ['M4 6h9', 'M4 12h9', 'M4 18h6', 'M17 9v6', 'M14 12h6'] },
  robot: { p: ['M5 8h14v10H5z', 'M12 4v4', 'M9 13h1', 'M14 13h1', 'M9.5 16h5'] },
  sparkle: { p: ['M12 3.5 13.7 9 19 10.7 13.7 12.4 12 18 10.3 12.4 5 10.7 10.3 9z'] },
  plus: { p: ['M12 5v14', 'M5 12h14'] },
  save: { p: ['M5 4h11l3 3v13H5z', 'M8 4v6h7V4', 'M8 14h8v6H8z'] },
  newdoc: { p: ['M6 3h8l4 4v14H6z', 'M14 3v4h4', 'M12 11v6', 'M9 14h6'] },

  // 文字格式
  minus: { p: ['M5 12h14'] },
  bold: { p: ['M8 5h6a3.2 3.2 0 0 1 0 6.4H8z', 'M8 11.4h6.6a3.3 3.3 0 0 1 0 6.6H8z'] },
  alignLeft: { p: ['M4 6h16', 'M4 12h10', 'M4 18h14'] },
  alignCenter: { p: ['M4 6h16', 'M7 12h10', 'M5 18h14'] },
  alignRight: { p: ['M4 6h16', 'M10 12h10', 'M6 18h14'] },
  translate: { p: ['M4 6h9', 'M8.5 4v2c0 4-2.6 6.4-4.5 7', 'M6 12c1.8 2.8 4.4 4.6 7 5.4', 'M13.5 20l4-9 4 9', 'M15 17h5'] },
  align: { p: ['M4 4v16', 'M9 7h11', 'M9 12h7', 'M9 17h11'] },
  unify: { p: ['M4 4h5', 'M4 4v5', 'M20 20h-5', 'M20 20v-5', 'M9.5 9.5h5v5h-5z'] },
  join: { p: ['M4 7h9', 'M4 12h9', 'M4 17h9', 'M15 10.5 19 12l-4 1.5'] },
  text: { p: ['M6 5h12', 'M12 5v14', 'M9 19h6'] },
  header: { p: ['M5 3h14v18H5z', 'M5 8h14', 'M8 12h8', 'M8 16h5'] },

  // 图片与对象
  lock: { p: ['M6 11h12v9H6z', 'M9 11V8a3 3 0 0 1 6 0v3'] },
  unlock: { p: ['M6 11h12v9H6z', 'M9 11V8a3 3 0 0 1 5.4-1.8'] },
  eraser: { p: ['M4 16.5 12 8.5l6 6-4.5 4.5H8z', 'M9 21h11'] },
  ocr: { p: ['M4 8V4h4', 'M16 4h4v4', 'M20 16v4h-4', 'M8 20H4v-4', 'M8 9h8', 'M8 13h5'] },
  rotate: { p: ['M12 6V2.5L8 6l4 3.5V6a6 6 0 1 1-6 6'] },
  undo: { p: ['M9.5 14.5 4 9l5.5-5.5', 'M4 9h9.5a5.5 5.5 0 0 1 0 11H8'] },
  redo: { p: ['M14.5 14.5 20 9l-5.5-5.5', 'M20 9h-9.5a5.5 5.5 0 0 0 0 11H16'] },
  copy: { p: ['M9 9h11v11H9z', 'M5 15V4h11'] },
  paste: { p: ['M9 4h6v3H9z', 'M7 6h10v15H7z', 'M10 12h4', 'M10 16h4'] },
  group: { p: ['M4 4h7v7H4z', 'M13 13h7v7h-7z'] },
  stitch: { p: ['M6 3h12v6H6z', 'M6 15h12v6H6z', 'M12 9v6', 'M9.5 12.5 12 15l2.5-2.5'] },
  ungroup: { p: ['M4 4h7v7H4z', 'M13 4h7v7h-7z', 'M4 13h7v7H4z', 'M13 13h7v7h-7z'] },
  trash: { p: ['M4 7h16', 'M9.5 7V4h5v3', 'M6.5 7l1 14h9l1-14', 'M10 11v6', 'M14 11v6'] },
  frame: { p: ['M3 8h18', 'M3 16h18', 'M8 3v18', 'M16 3v18'] },
}

const def = computed(() => ICONS[props.name] || { p: ['M4 4h16v16H4z'] })
const paths = computed(() => def.value.p || [])
const circles = computed(() => def.value.c || [])
const rects = computed(() => def.value.r || [])
</script>

<style scoped>
.ic { display: block; flex: 0 0 auto; }
</style>
