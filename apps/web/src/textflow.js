// 把 PDF/OCR 抽出来的“一行一个换行”整理成自然段：
// - 空行保留为段落分隔；
// - 题号/小问/项目符号开头另起一段；
// - 其余软换行合并，中文之间不加空格，英文之间加一个空格；
// - 英文行尾连字符（exam- ple）拼回单词。
const MARKER = /^(?:[0-9]+(?:\s*[-－—.．、)]|[.．][0-9])|[（(]\s*[0-9一二三四五六七八九十]+\s*[)）]|[①-⑳]|[•·▪◦]\s|[-*]\s|【)/

function isCjk(ch) {
  return /[\u3400-\u9fff\uf900-\ufaff]/.test(ch || '')
}

export function flowText(raw) {
  const src = String(raw || '').replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ')
  const lines = src.split('\n').map((line) => line.replace(/[ \t]+$/g, '').trim())
  const out = []
  let para = ''
  const flush = () => {
    if (para) out.push(para)
    para = ''
  }
  for (const line of lines) {
    if (!line) {
      flush()
      if (out.length && out[out.length - 1] !== '') out.push('')
      continue
    }
    if (MARKER.test(line)) {
      flush()
      para = line
      continue
    }
    if (!para) {
      para = line
      continue
    }
    const prev = para.slice(-1)
    if (/-$/.test(prev) && /^[a-z]/.test(line)) {
      para = para.slice(0, -1) + line
    } else {
      para += (isCjk(prev) && isCjk(line[0]) ? '' : ' ') + line
    }
  }
  flush()
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}
