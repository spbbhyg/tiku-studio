import { api } from './api.js'

/** 内嵌画布与独立画布共用的资源入口：图片与 PDF 页都进 data/docx-assets 对应的材料库。 */
export async function uploadImageToCanvas(file, pane, x, y) {
  const fd = new FormData()
  fd.append('file', file, file.name || 'paste.png')
  const r = await api.postForm('/api/v2/materials/upload', fd)
  await pane.addImage(r.path, x, y)
  return r
}

/** PDF 逐页转 PNG，再按普通图片进画布；不另存一套私有资源。 */
export async function importPdfToCanvas(file, pane, onProgress = () => {}) {
  const fd = new FormData()
  fd.append('file', file, file.name || 'document.pdf')
  const doc = await api.postForm('/api/manual/upload', fd)
  const total = Number(doc.page_count || 0)
  if (!total) throw new Error('这份 PDF 没有可读页面')
  const out = []
  for (let page = 1; page <= total; page++) {
    onProgress(`正在把 PDF 第 ${page}/${total} 页放进画布…`)
    const resp = await fetch(`/api/manual/doc/${encodeURIComponent(doc.doc_id)}/page/${page}.png?zoom=2`)
    if (!resp.ok) throw new Error(`PDF 第 ${page} 页转图失败（HTTP ${resp.status}）`)
    const blob = await resp.blob()
    const name = `${String(file.name || 'document.pdf').replace(/\.pdf$/i, '')}-第${page}页.png`
    const image = new File([blob], name, { type: 'image/png' })
    out.push(await uploadImageToCanvas(image, pane, null, null))
  }
  return { doc, images: out }
}

export function isCanvasImage(file) {
  return /^image\//i.test(file.type || '') || /\.(png|jpe?g|webp|gif|bmp)$/i.test(file.name || '')
}

export function isCanvasPdf(file) {
  return /pdf/i.test(file.type || '') || /\.pdf$/i.test(file.name || '')
}
