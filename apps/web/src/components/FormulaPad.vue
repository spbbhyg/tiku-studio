<script setup>
/**
 * 公式面板：像科学计算器一样点按输入 LaTeX，实时预览，再插入到题干/答案/解析。
 * 依赖 KaTeX 本地渲染，离线可用。
 */
import { computed, nextTick, ref } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const emit = defineEmits(['send', 'close'])

const latex = ref('')
const ta = ref(null)
const err = ref('')
const activeGroup = ref('上下标')

const groups = [
  {
    name: '上下标',
    items: [
      { t: 'x²', i: '^{2}' }, { t: 'xₙ', i: '_{n}' }, { t: 'xⁿ', i: '^{n}' },
      { t: 'xⁿ⁺¹', i: '^{n+1}' }, { t: 'xᵢⱼ', i: '_{i,j}' }, { t: 'a^(b^c)', i: '^{b^{c}}' },
      { t: 'x′', i: "'" }, { t: 'x″', i: "''" }, { t: 'x̄', i: '\\bar{x}' },
      { t: 'x⃗', i: '\\vec{x}' }, { t: 'x̂', i: '\\hat{x}' }, { t: 'ẋ', i: '\\dot{x}' },
    ],
  },
  {
    name: '结构与分式',
    items: [
      { t: 'a/b', i: '\\frac{a}{b}', b: 6 }, { t: '√x', i: '\\sqrt{x}', b: 1 },
      { t: 'ⁿ√x', i: '\\sqrt[n]{x}', b: 4 }, { t: '( )', i: '\\left( x \\right)', b: 8 },
      { t: '[ ]', i: '\\left[ x \\right]', b: 8 }, { t: '{ }', i: '\\left\\{ x \\right\\}', b: 10 },
      { t: '|x|', i: '\\left| x \\right|', b: 8 }, { t: 'x̅', i: '\\overline{AB}', b: 1 },
      { t: '求和', i: '\\sum_{i=1}^{n} x_i', b: 0 }, { t: '连乘', i: '\\prod_{i=1}^{n} x_i', b: 0 },
      { t: '积分', i: '\\int_{a}^{b} f(x)\\,dx', b: 0 }, { t: '极限', i: '\\lim_{x \\to 0} f(x)', b: 0 },
      { t: '矩阵', i: '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}', b: 0 },
      { t: '分段', i: '\\begin{cases} a & x>0 \\\\ b & x\\le 0 \\end{cases}', b: 0 },
    ],
  },
  {
    name: '希腊字母',
    items: [
      { t: 'α', i: '\\alpha ' }, { t: 'β', i: '\\beta ' }, { t: 'γ', i: '\\gamma ' },
      { t: 'δ', i: '\\delta ' }, { t: 'ε', i: '\\varepsilon ' }, { t: 'ζ', i: '\\zeta ' },
      { t: 'η', i: '\\eta ' }, { t: 'θ', i: '\\theta ' }, { t: 'ι', i: '\\iota ' },
      { t: 'κ', i: '\\kappa ' }, { t: 'λ', i: '\\lambda ' }, { t: 'μ', i: '\\mu ' },
      { t: 'ν', i: '\\nu ' }, { t: 'ξ', i: '\\xi ' }, { t: 'π', i: '\\pi ' },
      { t: 'ρ', i: '\\rho ' }, { t: 'σ', i: '\\sigma ' }, { t: 'τ', i: '\\tau ' },
      { t: 'υ', i: '\\upsilon ' }, { t: 'φ', i: '\\varphi ' }, { t: 'χ', i: '\\chi ' },
      { t: 'ψ', i: '\\psi ' }, { t: 'ω', i: '\\omega ' },
      { t: 'Γ', i: '\\Gamma ' }, { t: 'Δ', i: '\\Delta ' }, { t: 'Θ', i: '\\Theta ' },
      { t: 'Λ', i: '\\Lambda ' }, { t: 'Ξ', i: '\\Xi ' }, { t: 'Π', i: '\\Pi ' },
      { t: 'Σ', i: '\\Sigma ' }, { t: 'Φ', i: '\\Phi ' }, { t: 'Ψ', i: '\\Psi ' },
      { t: 'Ω', i: '\\Omega ' },
    ],
  },
  {
    name: '运算与关系',
    items: [
      { t: '×', i: '\\times ' }, { t: '÷', i: '\\div ' }, { t: '±', i: '\\pm ' },
      { t: '∓', i: '\\mp ' }, { t: '·', i: '\\cdot ' }, { t: '∗', i: '\\ast ' },
      { t: '∘', i: '\\circ ' }, { t: '≠', i: '\\neq ' }, { t: '≈', i: '\\approx ' },
      { t: '≡', i: '\\equiv ' }, { t: '≤', i: '\\leq ' }, { t: '≥', i: '\\geq ' },
      { t: '≪', i: '\\ll ' }, { t: '≫', i: '\\gg ' }, { t: '∝', i: '\\propto ' },
      { t: '∼', i: '\\sim ' }, { t: '∞', i: '\\infty ' }, { t: '∂', i: '\\partial ' },
      { t: '∇', i: '\\nabla ' }, { t: '°', i: '^{\\circ} ' },
    ],
  },
  {
    name: '集合与逻辑',
    items: [
      { t: '∈', i: '\\in ' }, { t: '∉', i: '\\notin ' }, { t: '⊂', i: '\\subset ' },
      { t: '⊃', i: '\\supset ' }, { t: '⊆', i: '\\subseteq ' }, { t: '⊇', i: '\\supseteq ' },
      { t: '∪', i: '\\cup ' }, { t: '∩', i: '\\cap ' }, { t: '∅', i: '\\emptyset ' },
      { t: '∀', i: '\\forall ' }, { t: '∃', i: '\\exists ' }, { t: '¬', i: '\\neg ' },
      { t: '∧', i: '\\land ' }, { t: '∨', i: '\\lor ' }, { t: '⇒', i: '\\Rightarrow ' },
      { t: '⇔', i: '\\Leftrightarrow ' }, { t: '→', i: '\\to ' }, { t: '↦', i: '\\mapsto ' },
    ],
  },
  {
    name: '函数与统计',
    items: [
      { t: 'sin', i: '\\sin ' }, { t: 'cos', i: '\\cos ' }, { t: 'tan', i: '\\tan ' },
      { t: 'log', i: '\\log ' }, { t: 'ln', i: '\\ln ' }, { t: 'exp', i: '\\exp ' },
      { t: 'max', i: '\\max ' }, { t: 'min', i: '\\min ' }, { t: 'arg', i: '\\arg ' },
      { t: 'x̄', i: '\\bar{x}' }, { t: 'σ²', i: '\\sigma^{2}' }, { t: 'μ', i: '\\mu ' },
      { t: 'P(A)', i: 'P(A)' }, { t: 'E(X)', i: 'E(X)' }, { t: 'Var', i: '\\operatorname{Var}(X)' },
      { t: '∑', i: '\\sum_{i=1}^{n} ' }, { t: '∏', i: '\\prod_{i=1}^{n} ' },
      { t: 'nCr', i: '\\binom{n}{k}', b: 4 },
    ],
  },
]

const current = computed(() => groups.find((g) => g.name === activeGroup.value) || groups[0])

const preview = computed(() => {
  const src = latex.value.trim()
  if (!src) return ''
  try {
    return katex.renderToString(src, { throwOnError: false, displayMode: true })
  } catch (e) {
    return ''
  }
})

function put(text, back = 0) {
  const el = ta.value
  const pos = el ? (el.selectionStart ?? latex.value.length) : latex.value.length
  const before = latex.value.slice(0, pos)
  const after = latex.value.slice(pos)
  latex.value = before + text + after
  const caret = pos + text.length - (back || 0)
  nextTick(() => {
    if (!el) return
    el.focus()
    el.setSelectionRange(caret, caret)
  })
}

function onKey(e) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    send('stem')
  }
}

function send(target) {
  const src = latex.value.trim()
  if (!src) { err.value = '先输入公式'; return }
  err.value = ''
  emit('send', { target, text: `$${src}$` })
}

function clearAll() {
  latex.value = ''
  err.value = ''
}
</script>

<template>
  <div class="fpad">
    <div class="fpad-head">
      <b>公式面板</b>
      <span class="muted">点按钮拼公式 → 选中插入位置</span>
      <button class="ghost" style="margin-left:auto" @click="emit('close')">收起</button>
    </div>

    <div class="fpad-tabs">
      <button v-for="g in groups" :key="g.name" class="tab" :class="{ on: g.name === activeGroup }"
              @click="activeGroup = g.name">{{ g.name }}</button>
    </div>

    <div class="fpad-keys">
      <button v-for="(k, idx) in current.items" :key="idx" class="key" :title="k.i"
              @click="put(k.i, k.b || 0)">{{ k.t }}</button>
    </div>

    <textarea ref="ta" v-model="latex" class="fpad-input" rows="3" spellcheck="false"
              placeholder="在这里拼公式，例如 \frac{\partial u}{\partial t} = \alpha \nabla^2 u"
              @keydown="onKey"></textarea>

    <div class="fpad-preview">
      <div v-if="preview" v-html="preview"></div>
      <div v-else class="muted">预览区</div>
    </div>

    <div v-if="err" class="errbox">{{ err }}</div>

    <div class="fpad-actions">
      <span class="muted">插入到：</span>
      <button class="primary" @click="send('stem')">题干</button>
      <button @click="send('answer')">答案</button>
      <button @click="send('analysis')">解析</button>
      <button class="ghost" @click="clearAll">清空</button>
      <span class="muted" style="margin-left:auto">Ctrl+Enter = 插入题干</span>
    </div>
  </div>
</template>

<style scoped>
.fpad { border: 1px solid var(--line, #e3e7ef); border-radius: 10px; padding: 10px; background: #fbfcff; }
.fpad-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.fpad-tabs { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
.fpad-tabs .tab { padding: 3px 9px; font-size: 12px; border-radius: 999px; border: 1px solid #d8dee9; background: #fff; cursor: pointer; }
.fpad-tabs .tab.on { background: var(--brand, #2f6fed); border-color: var(--brand, #2f6fed); color: #fff; }
.fpad-keys { display: grid; grid-template-columns: repeat(auto-fill, minmax(52px, 1fr)); gap: 5px; margin-bottom: 8px; }
.fpad-keys .key { padding: 6px 2px; font-size: 13px; border: 1px solid #dfe4ee; border-radius: 6px; background: #fff; cursor: pointer; }
.fpad-keys .key:hover { border-color: var(--brand, #2f6fed); color: var(--brand, #2f6fed); }
.fpad-input { width: 100%; font-family: Consolas, Menlo, monospace; font-size: 12.5px; padding: 8px; border: 1px solid #dfe4ee; border-radius: 8px; resize: vertical; }
.fpad-preview { min-height: 44px; margin: 8px 0; padding: 8px; background: #fff; border: 1px dashed #dfe4ee; border-radius: 8px; overflow-x: auto; }
.fpad-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
</style>
