/**
 * 从 Ketcher 内嵌画板里抽"整块画布"的干净 SVG。
 *
 * 为什么不能只靠 molfile：molfile / SMILES 只描述分子（原子 + 键），
 * 反应箭头、加号、文字标注、括号都不是化学图元，转 molfile 时直接被丢掉，
 * 于是"含箭头的结构式"贴进画布就只剩分子骨架 —— 这就是用户报的那个 bug。
 *
 * 做法：Ketcher 把整块画布渲染成一个同源 iframe 里的 <svg data-testid="canvas">，
 * 里面是扁平的 <path>/<text>（箭头和文字也在）。直接读 DOM → 去隐藏层 → 按内容包围盒裁切
 * → 重新组装成独立 SVG，箭头与文字原地保留，字体字号颜色都不变。
 */

const NS = 'http://www.w3.org/2000/svg'
const SKIP_TAGS = new Set(['desc', 'defs', 'title'])
const DROP_ATTRS = ['data-testid', 'data-bondid', 'data-atomid', 'data-bondtype', 'data-bondstereo',
  'data-fromatomid', 'data-toatomid', 'data-topology', 'data-reacting-center']

function isHidden(el, win) {
  let st = null
  try { st = win.getComputedStyle(el) } catch (e) { st = null }
  if (st && (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0')) return true
  const op = el.getAttribute && el.getAttribute('opacity')
  return op === '0' || op === '0.0'
}

/** 元素包围盒（把自身 transform 算进去，得到它在画布坐标系里的真实外框）。 */
function outerBox(el) {
  let bb = null
  try { bb = el.getBBox() } catch (e) { return null }
  if (!bb) return null
  let { x, y, width: w, height: h } = bb
  let m = null
  try { m = el.transform && el.transform.baseVal && el.transform.baseVal.consolidate() } catch (e) { m = null }
  if (m && m.matrix) {
    const M = m.matrix
    const pts = [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]
      .map(([px, py]) => [M.a * px + M.c * py + M.e, M.b * px + M.d * py + M.f])
    x = Math.min(...pts.map((p) => p[0]))
    y = Math.min(...pts.map((p) => p[1]))
    w = Math.max(...pts.map((p) => p[0])) - x
    h = Math.max(...pts.map((p) => p[1])) - y
  }
  if (!isFinite(w) || !isFinite(h)) return null
  return { x, y, w, h }
}

/**
 * @param {Document} doc  Ketcher iframe 的 contentDocument
 * @param {{pad?: number}} opts
 * @returns {{svg: string, w: number, h: number, count: number} | null}
 */
export function extractKetcherSvg(doc, opts = {}) {
  if (!doc) return null
  const canvas = doc.querySelector('svg[data-testid="canvas"]')
  if (!canvas) return null
  const win = doc.defaultView || window
  const keep = []
  let x1 = Infinity
  let y1 = Infinity
  let x2 = -Infinity
  let y2 = -Infinity
  for (const el of Array.from(canvas.children)) {
    const tag = el.tagName.toLowerCase()
    if (SKIP_TAGS.has(tag)) continue
    if (tag === 'rect') continue                    // Raphaël 的图层占位 rect（10×10、opacity 0）
    if (isHidden(el, win)) continue                // 隐藏的文字输入框、选中态
    if (tag === 'text' && !(el.textContent || '').trim()) continue
    const box = outerBox(el)
    if (!box) continue
    keep.push(el)
    x1 = Math.min(x1, box.x)
    y1 = Math.min(y1, box.y)
    x2 = Math.max(x2, box.x + box.w)
    y2 = Math.max(y2, box.y + box.h)
  }
  if (!keep.length || !isFinite(x1)) return null
  const pad = opts.pad == null ? 10 : opts.pad
  const bx = x1 - pad
  const by = y1 - pad
  const w = Math.max(1, x2 - x1 + pad * 2)
  const h = Math.max(1, y2 - y1 + pad * 2)

  const out = doc.createElementNS(NS, 'svg')
  out.setAttribute('xmlns', NS)
  out.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  out.setAttribute('width', String(Math.round(w)))
  out.setAttribute('height', String(Math.round(h)))
  out.setAttribute('viewBox', `0 0 ${Math.round(w)} ${Math.round(h)}`)
  const g = doc.createElementNS(NS, 'g')
  g.setAttribute('transform', `translate(${-Math.round(bx * 100) / 100} ${-Math.round(by * 100) / 100})`)
  for (const el of keep) g.appendChild(el.cloneNode(true))
  out.appendChild(g)

  for (const n of Array.from(out.querySelectorAll('*'))) {
    const cls = n.getAttribute('class') || ''
    if (/select|hover|highlight|tooltip|warn/i.test(cls)) { n.remove(); continue }
    for (const a of DROP_ATTRS) n.removeAttribute(a)
    const stl = n.getAttribute('style') || ''
    if (stl) n.setAttribute('style', stl.replace(/-webkit-tap-highlight-color[^;]*;?/g, '').trim())
  }
  for (const n of out.querySelectorAll('text')) {
    const ff = (n.getAttribute('font-family') || '').replace(/"/g, '').split(',')[0].trim()
    n.setAttribute('font-family', ff || 'Arial')
  }
  return { svg: new XMLSerializer().serializeToString(out),
           w: Math.round(w), h: Math.round(h), count: keep.length }
}

/** 从 iframe 元素直接抽（跨源或还没加载好时返回 null，调用方自己回退）。 */
export function extractFromFrame(frameEl, opts = {}) {
  try {
    const doc = frameEl && frameEl.contentDocument
    return doc ? extractKetcherSvg(doc, opts) : null
  } catch (e) {
    return null
  }
}

/** 宽度自适应：像 <img> 一样按给定宽度出图（画布按 300px 左右插入最合适）。 */
export function sizedSvg(svg, width) {
  if (!svg) return ''
  return svg.replace(/<svg([^>]*?)\swidth="[^"]*"/i, '<svg$1').replace(/<svg([^>]*?)\sheight="[^"]*"/i, '<svg$1')
    .replace(/^<svg/i, `<svg width="${Math.round(width)}"`)
}
