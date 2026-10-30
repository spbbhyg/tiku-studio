import katex from 'katex'

export function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 先转义 + 换行，再做公式替换：避免 <br> 落进 KaTeX 生成的 SVG path 里 */
export function renderMath(text) {
  const raw = escapeHtml(text).replace(/\n/g, '<br>')
  return raw.replace(/\$([^$]+)\$/g, (m, body) => {
    try { return katex.renderToString(body, { throwOnError: false }) } catch (e) { return m }
  })
}

/** 纯文本（去掉 $ 但保留公式原文），用于标题、列表摘要 */
export function stripMath(text) {
  return String(text == null ? '' : text).replace(/\$/g, '')
}
