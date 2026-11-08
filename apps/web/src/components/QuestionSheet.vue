<script setup>
// 题面渲染器：一份 doc，两种外壳（card 题库卡片 / a4 出题页预览）。
// 段落 / 行内图 / 表 / 公式的渲染只写这一份，两种外壳只包不同 CSS。
import { computed } from 'vue'
import { renderInline, renderParas } from '../mathx.js'

const props = defineProps({
  doc: { type: Object, required: true },
  variant: { type: String, default: 'card' },   // card | a4
  showAnswers: { type: Boolean, default: false },
  answersOpen: { type: Boolean, default: false },
})

const meta = computed(() => props.doc.meta || {})
const suffix = computed(() => {
  const m = meta.value
  const bits = []
  if (m.score) bits.push(`${m.score} 分`)
  if (m.percent) bits.push(`占 ${m.percent}%`)
  return bits.length ? `（${bits.join('，')}）` : ''
})

function imgUrl(p) {
  const s = String(p || '').replace(/\\/g, '/').replace(/^\/+/, '')
  return s ? `/api/images/${s}` : ''
}

// 卡片里不显示自动编号的「图 2」这类占位图注（自己写的说明照常显示）
function showCap(c) {
  const t = String(c || '').trim()
  if (!t) return false
  if (props.variant === 'card' && /^图\s*\d+$/.test(t)) return false
  return true
}

// 小问拍平：卡片按缩进显示，比递归组件简单得多，层级最多四级
const subs = computed(() => {
  const out = []
  const paren = (meta.value.numbering || 'dash') === 'paren'
  const base = meta.value.number || 1
  const walk = (list, prefix, level) => {
    (list || []).forEach((s, i) => {
      const no = prefix ? `${prefix}-${i + 1}` : (paren ? `(${i + 1})` : `${base}-${i + 1}`)
      out.push({
        no: s.no || no, level, score: s.score || 0,
        paras: renderParas(s.text || s.stem || ''),
        imgs: s.imgs || s.images || [],
        answer: s.answer || '', points: s.points || [],
        answerImgs: s.answer_images || [],
      })
      if (s.subs && s.subs.length) walk(s.subs, no, level + 1)
    })
  }
  walk(props.doc.subs, '', 0)
  return out
})

const stemBlocks = computed(() => props.doc.stem || [])
function imgStyle(o) {
  const w = Number(o && o.w) || 300
  const al = (o && o.align) || 'center'
  return {
    width: w + 'pt',
    marginLeft: al === 'right' || al === 'center' ? 'auto' : '0',
    marginRight: al === 'left' || al === 'center' ? 'auto' : '0',
  }
}
const allAnswers = computed(() => subs.value.some((s) => s.answer || s.points.length || s.answerImgs.length))
</script>

<template>
  <div class="qs" :class="[`v-${variant}`]">
    <div v-if="meta.title" class="qshead">
      <span class="no">第&nbsp;{{ meta.number || 1 }}&nbsp;题</span>
      <span class="tt">{{ meta.title }}</span>
      <span class="sc">{{ suffix }}</span>
    </div>

    <template v-for="(b, i) in stemBlocks" :key="'b' + i">
      <p v-if="b.t === 'p'" class="bp" v-html="renderInline(b.text || '')" />
      <figure v-else-if="b.t === 'img'" class="bimg">
        <img :src="imgUrl(b.path)" :style="imgStyle(b)" loading="lazy" />
        <figcaption v-if="showCap(b.caption)">{{ b.caption }}</figcaption>
      </figure>
      <table v-else-if="b.t === 'table'" class="btable">
        <thead v-if="b.head && b.head.length">
          <tr><th v-for="(h, j) in b.head" :key="j" v-html="renderInline(h || '')" /></tr>
        </thead>
        <tbody>
          <tr v-for="(row, j) in b.rows || []" :key="j">
            <td v-for="(c, k) in row" :key="k" v-html="renderInline(c || '')" />
          </tr>
        </tbody>
      </table>
      <p v-else-if="b.t === 'formula'" class="bfml" v-html="renderInline('$' + (b.text || '') + '$')" />
    </template>

    <div class="subs">
      <div v-for="(s, i) in subs" :key="'s' + i" class="sub" :style="{ paddingLeft: s.level * 12 + 'px' }">
        <div class="subhead">
          <b class="sno">{{ s.no }}</b>
          <span class="sc2" v-if="s.score && variant === 'a4'">{{ s.score }} 分</span>
        </div>
        <p v-for="(p, j) in s.paras" :key="j" class="bp" v-html="p" />
        <figure v-for="(im, j) in s.imgs" :key="'i' + j" class="bimg">
          <img :src="imgUrl(im.path || im)" :style="imgStyle(im)" loading="lazy" />
          <figcaption v-if="showCap(im.caption)">{{ im.caption }}</figcaption>
        </figure>
        <div v-if="showAnswers && (s.answer || s.points.length || s.answerImgs.length)" class="ans">
          <p v-if="s.answer" class="atext" v-html="renderInline(s.answer)" />
          <p v-for="(pt, j) in s.points" :key="'p' + j" class="ptext" v-html="renderInline(pt)" />
          <figure v-for="(im, j) in s.answerImgs" :key="'ai' + j" class="bimg">
            <img :src="imgUrl(im.path || im)" :style="imgStyle(im)" loading="lazy" />
          </figure>
        </div>
      </div>
    </div>

    <details v-if="variant === 'card' && allAnswers && !showAnswers" class="ansbox" :open="answersOpen">
      <summary>查看参考答案</summary>
      <div v-for="(s, i) in subs" :key="'a' + i" class="ansrow">
        <b class="sno">{{ s.no }}</b>
        <span v-if="s.answer" class="atext" v-html="renderInline(s.answer)" />
        <span v-for="(pt, j) in s.points" :key="j" class="ptext" v-html="renderInline(pt)" />
        <figure v-for="(im, j) in s.answerImgs" :key="'dai' + j" class="bimg">
          <img :src="imgUrl(im.path || im)" :style="imgStyle(im)" loading="lazy" />
        </figure>
      </div>
    </details>
  </div>
</template>

<style scoped>
.qs { color: #111827; }
.qs.v-card { font-size: 14px; line-height: 1.7; }
.qs.v-a4 {
  width: 595.3pt; min-height: 841.9pt; padding: 72.5pt 80pt 82pt 90pt;
  background: #fff; color: #000; box-sizing: border-box;
  font-family: "Times New Roman", SimSun, "宋体", serif;
  font-size: 10.45pt; line-height: 15.6pt;
}
.qshead { margin-bottom: 4pt; }
.qs.v-card .qshead { font-weight: 700; font-size: 14.5px; }
.qs.v-a4 .qshead { font-weight: 700; font-size: 10.45pt; }
.qs.v-a4 .qshead .no { font-family: "Times New Roman", serif; }
.qshead .sc { font-weight: 400; }
.bp { margin: 0; }
.qs.v-a4 .bp { text-align: justify; text-justify: inter-ideograph; }
.subs { margin-top: 2pt; }
.sub { break-inside: avoid; }
.subhead { display: flex; align-items: baseline; gap: 6px; }
.qs.v-a4 .sno { font-family: "Times New Roman", serif; font-weight: 700; }
.qs.v-card .sno { color: #1f2937; }
.sc2 { font-size: 11px; color: #9ca3af; }
.qs.v-a4 .sc2 { display: none; }
.bimg { margin: 4pt 0; text-align: center; break-inside: avoid; }
.bimg img, .qs img { display: block; max-width: 100%; height: auto; }
.qs.v-card img { max-height: 300px; width: auto; max-width: 100%; }
.qs.v-card .bimg img { max-height: 260px; }
figcaption { font-size: 6.8pt; color: #6b7280; text-align: center; margin-top: 2px; }
.qs.v-a4 figcaption { color: #7030A0; }
.btable { width: 100%; table-layout: fixed; border-collapse: collapse; margin: 4pt 0; }
.btable th, .btable td { border: 0.5pt solid #000; padding: 2pt 4pt; font-size: inherit; text-align: center; }
.qs.v-a4 .btable th, .qs.v-a4 .btable td { font-size: 10.45pt; }
.bfml { text-align: center; margin: 4pt 0; }
.ans { margin-top: 2pt; }
.qs.v-a4 .atext, .qs.v-a4 .ansrow .atext { color: #FF0000; }
.qs.v-card .atext { color: #b91c1c; }
.qs.v-a4 .ptext { color: #0000FF; }
.qs.v-card .ptext { color: #1d4ed8; font-size: 12.5px; }
.ansbox { margin-top: 6px; border-top: 1px dashed #e5e7eb; padding-top: 4px; }
.ansbox summary { cursor: pointer; color: #2563eb; font-size: 12.5px; }
.ansrow { display: flex; gap: 6px; align-items: baseline; font-size: 12.5px; padding: 2px 0; }
</style>
