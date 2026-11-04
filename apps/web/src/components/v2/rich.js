import { InputRule, Node, mergeAttributes } from '@tiptap/core'
import katex from 'katex'

/** 行内公式节点：atom，内容不可编辑，保存 latex 原文 */
export const MathInline = Node.create({
  name: 'mathInline',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      latex: { default: '' },
      display: { default: false },
    }
  },

  parseHTML() {
    return [{ tag: 'span[data-math]' }]
  },

  renderHTML({ node, HTMLAttributes }) {
    const latex = node.attrs.latex || ''
    const el = document.createElement('span')
    el.className = 'math-inline'
    el.setAttribute('data-math', latex)
    el.setAttribute('contenteditable', 'false')
    try {
      el.innerHTML = katex.renderToString(latex, {
        throwOnError: false,
        displayMode: !!node.attrs.display,
      })
    } catch (e) {
      el.textContent = latex
    }
    return el
  },

  addInputRules() {
    // 输入 $...$ 后自动转成公式节点
    return [
      new InputRule({
        find: /\$([^$\n]+)\$$/,
        handler: ({ state, range, match }) => {
          const type = state.schema.nodes.mathInline
          if (!type) return
          const node = type.create({ latex: (match[1] || '').trim() })
          state.tr.replaceWith(range.from, range.to, node)
        },
      }),
    ]
  },

  addNodeView() {
    return ({ node, HTMLAttributes }) => {
      const dom = document.createElement('span')
      dom.className = 'math-inline'
      dom.setAttribute('contenteditable', 'false')
      dom.title = node.attrs.latex || ''
      try {
        dom.innerHTML = katex.renderToString(node.attrs.latex || '', { throwOnError: false })
      } catch (e) {
        dom.textContent = node.attrs.latex || ''
      }
      if (HTMLAttributes) Object.entries(HTMLAttributes).forEach(([k, v]) => dom.setAttribute(k, v))
      return { dom }
    }
  },
})

export const EMPTY_DOC = { type: 'doc', content: [{ type: 'paragraph' }] }

export function emptyDoc() {
  return JSON.parse(JSON.stringify(EMPTY_DOC))
}

export function isDocEmpty(json) {
  const c = (json && json.content) || []
  if (!c.length) return true
  if (c.length === 1 && c[0].type === 'paragraph' && !(c[0].content || []).length) return true
  return false
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** 把 Tiptap JSON 渲染成 HTML（带 KaTeX），用于分屏预览与导出 */
const INLINE_MATH = /\$([^$\n]+)\$/g

/** 文本节点里的 $...$ 也渲染成行内公式（兼容旧数据） */
export function mathifyText(html) {
  return String(html).replace(INLINE_MATH, (_, latex) => {
    try {
      return katex.renderToString(latex, { throwOnError: false })
    } catch (e) {
      return _ + ''
    }
  })
}

export function renderDoc(json, opts = {}) {
  const node = (n) => {
    if (!n) return ''
    if (n.type === 'text') {
      let t = esc(n.text)
      for (const m of n.marks || []) {
        if (m.type === 'bold') t = `<strong>${t}</strong>`
        else if (m.type === 'italic') t = `<em>${t}</em>`
        else if (m.type === 'code') t = `<code>${t}</code>`
        else if (m.type === 'strike') t = `<s>${t}</s>`
      }
      return t
    }
    if (n.type === 'mathInline') {
      try {
        return katex.renderToString(n.attrs?.latex || '', { throwOnError: false })
      } catch (e) {
        return esc(n.attrs?.latex || '')
      }
    }
    if (n.type === 'chemInline') {
      const svg = n.attrs?.svg || ''
      if (!svg) return ''
      const b64 = btoa(unescape(encodeURIComponent(svg)))
      return `<img class="chem-inline" src="data:image/svg+xml;base64,${b64}" alt="结构式" style="max-height:120px;vertical-align:middle">`
    }
    if (n.type === 'hardBreak') return '<br>'
    if (n.type === 'image') {
      const src = n.attrs?.src ? esc(n.attrs.src) : ''
      return src ? `<img src="${src}" alt="${esc(n.attrs?.alt || '')}" style="max-width:100%">` : ''
    }
    const inner = (n.content || []).map(node).join('')
    switch (n.type) {
      case 'doc': return inner
      case 'paragraph': return `<p>${inner || '<br>'}</p>`
      case 'heading': return `<h${n.attrs?.level || 3}>${inner}</h${n.attrs?.level || 3}>`
      case 'bulletList': return `<ul>${inner}</ul>`
      case 'orderedList': return `<ol>${inner}</ol>`
      case 'listItem': return `<li>${inner}</li>`
      case 'blockquote': return `<blockquote>${inner}</blockquote>`
      case 'codeBlock': return `<pre><code>${inner}</code></pre>`
      case 'horizontalRule': return '<hr>'
      default: return inner
    }
  }
  const html = node(json || EMPTY_DOC)
  if (opts.plain) return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
  return mathifyText(html)
}

/** 从 JSON 里抽纯文本（列标题用） */
export function docText(json) {
  const out = []
  const walk = (n) => {
    if (!n) return
    if (n.type === 'text') out.push(n.text || '')
    else if (n.type === 'mathInline') out.push('')
    else (n.content || []).forEach(walk)
  }
  walk(json)
  return out.join(' ').replace(/\s+/g, ' ').trim()
}

export const MATH_SYMBOLS = [
  ['\\alpha', 'α'], ['\\beta', 'β'], ['\\gamma', 'γ'], ['\\Delta', 'Δ'], ['\\pi', 'π'],
  ['\\lambda', 'λ'], ['\\mu', 'μ'], ['\\sigma', 'σ'], ['\\omega', 'ω'], ['\\infty', '∞'],
  ['\\int', '∫'], ['\\sum', '∑'], ['\\prod', '∏'], ['\\sqrt{}', '√'], ['\\frac{}{}', 'a/b'],
  ['\\times', '×'], ['\\cdot', '·'], ['\\div', '÷'], ['\\pm', '±'], ['\\le', '≤'],
  ['\\ge', '≥'], ['\\ne', '≠'], ['\\approx', '≈'], ['\\rightarrow', '→'], ['\\rightleftharpoons', '⇌'],
  ['\\uparrow', '↑'], ['\\downarrow', '↓'], ['^', '上标'], ['_', '下标'], ['\\xrightarrow{}', '箭头上'],
]
