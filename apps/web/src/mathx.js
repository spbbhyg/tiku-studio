// 题面文字 → HTML：只处理三种情况，够用且不会把中文弄丢
//   1) $...$ 行内公式 → KaTeX（渲染失败就退化成纯文本）
//   2) \ce{...} 化学式 → 自己转上下标（不依赖 mhchem，导出/卡片/PDF 表现一致）
//   3) _{...} ^{...} 与 H2O 这类裸写 → <sub>/<sup>
import katex from 'katex'

export function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// 化学式转 HTML：P4S3 → P<sub>4</sub>S<sub>3</sub>；SO4^2- → SO<sub>4</sub><sup>2-</sup>
export function ceHtml(inner) {
  let s = esc(inner)
  s = s.replace(/&lt;=&gt;/g, '⇌').replace(/&lt;-&gt;/g, '↔')
  s = s.replace(/&lt;=/g, '≤').replace(/&gt;=/g, '≥')
  s = s.replace(/-&gt;/g, '→').replace(/&lt;-/g, '←')
  s = s.replace(/\^\{([^}]*)\}/g, '<sup>$1</sup>').replace(/_\{([^}]*)\}/g, '<sub>$1</sub>')
  s = s.replace(/\^([A-Za-z0-9+\-]+)/g, '<sup>$1</sup>')
  s = s.replace(/_([A-Za-z0-9+\-]+)/g, '<sub>$1</sub>')
  // 紧跟字母或右括号的数字串当小写下标（系数 8 前面没有字母，不会误伤）
  s = s.replace(/([A-Za-z)\]])(\d+(?:\.\d+)?)/g, '$1<sub>$2</sub>')
  return s
}

function plainPiece(text) {
  let s = esc(text)
  // 段落里裸写的化学式与上下标
  s = s.replace(/\\ce\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/g, (m, inner) => ceHtml(inner))
  s = s.replace(/\^\{([^}]*)\}/g, '<sup>$1</sup>').replace(/_\{([^}]*)\}/g, '<sub>$1</sub>')
  s = s.replace(/([A-Za-z)\]])\^([A-Za-z0-9+\-]+)/g, '$1<sup>$2</sup>')
  s = s.replace(/([A-Za-z)\]])_([A-Za-z0-9+\-]+)/g, '$1<sub>$2</sub>')
  return s
}

function mathPiece(src) {
  const t = src.trim()
  const m = /^\\ce\{([\s\S]*)\}$/.exec(t)
  if (m) return ceHtml(m[1])
  try {
    return katex.renderToString(t, { throwOnError: false, displayMode: false, strict: false })
  } catch (e) {
    return plainPiece(t)
  }
}

// 行内渲染：$...$ 之间交给公式，其余走纯文本规则
export function renderInline(text) {
  const src = String(text == null ? '' : text)
  let out = ''
  let i = 0
  while (i < src.length) {
    const a = src.indexOf('$', i)
    if (a < 0) { out += plainPiece(src.slice(i)); break }
    const b = src.indexOf('$', a + 1)
    if (b < 0) { out += plainPiece(src.slice(i)); break }
    out += plainPiece(src.slice(i, a))
    out += mathPiece(src.slice(a + 1, b))
    i = b + 1
  }
  return out
}

// 段落：空行分段
export function renderParas(text) {
  return String(text == null ? '' : text)
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => renderInline(p.replace(/\n/g, ' ')))
}
