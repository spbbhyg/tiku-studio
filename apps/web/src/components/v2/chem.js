import { Node, mergeAttributes } from '@tiptap/core'

/** 行内结构式节点：只存 chemId 与内联 SVG（data URI），避免富文本里混 HTML */
export const ChemInline = Node.create({
  name: 'chemInline',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      chemId: { default: 0 },
      svg: { default: '' },
      molfile: { default: '' },
    }
  },

  parseHTML() {
    return [{ tag: 'img[data-chem]' }]
  },

  renderHTML({ node, HTMLAttributes }) {
    const svg = node.attrs.svg || ''
    const src = svg ? 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg))) : ''
    const el = document.createElement('img')
    el.className = 'chem-inline'
    el.setAttribute('data-chem', String(node.attrs.chemId || 0))
    el.setAttribute('src', src)
    el.setAttribute('alt', '结构式')
    el.setAttribute('contenteditable', 'false')
    if (HTMLAttributes) Object.entries(HTMLAttributes).forEach(([k, v]) => el.setAttribute(k, v))
    return el
  },

  addNodeView() {
    return ({ node }) => {
      const svg = node.attrs.svg || ''
      const dom = document.createElement('img')
      dom.className = 'chem-inline'
      dom.setAttribute('contenteditable', 'false')
      dom.alt = '结构式'
      dom.title = '结构式（点击右侧面板可重编）'
      if (svg) dom.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)))
      dom.style.maxHeight = '120px'
      dom.style.verticalAlign = 'middle'
      return { dom }
    }
  },
})

/** 把 SVG 文本转成 data URI（预览/导出用） */
export function svgDataUri(svg) {
  if (!svg) return ''
  return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)))
}
