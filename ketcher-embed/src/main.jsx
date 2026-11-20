import React from 'react'
import { createRoot } from 'react-dom/client'
import { Editor } from 'ketcher-react'
import { fromAtomsAttrs } from 'ketcher-core/dist/application/editor/actions/atom'
import { StandaloneStructServiceProvider } from 'ketcher-standalone'
import 'ketcher-react/dist/index.css'

// ketcher-standalone 的 chunk 在运行期会引用 CommonJS 的 require，这里补个兜底
if (typeof window !== 'undefined' && typeof window.require === 'undefined') {
  window.require = function require(name) { return window[name] || {} }
}

const provider = new StandaloneStructServiceProvider()

/* ---------------------------------------------------------------- 中文化（#12）
   Ketcher 3.x 没有内置语言包接口，这里按「界面标签字典 + MutationObserver」做一层
   轻量翻译：只替换精确命中的工具提示 / 菜单项 / 按钮文字，绝不碰画布里的分子式。
   缺词条时把英文原样留下，后续往 EN2CN 里补一行即可。 */
const EN2CN = {
  'Open…': '打开…', 'Open': '打开', 'Open project': '打开项目', 'Save as…': '另存为…',
  'Save': '保存', 'Export': '导出', 'Print': '打印',
  Undo: '撤销', Redo: '重做', Cut: '剪切', Copy: '复制', Paste: '粘贴',
  Clear: '清空', 'Clear canvas': '清空画布', Delete: '删除',
  'Aromatize': '芳香化', 'Dearomatize': '去芳香化', Layout: '自动排版', 'Clean up': '整理结构',
  'Check structure': '检查结构', 'Calculate CIP': '计算 CIP',
  'Zoom in': '放大', 'Zoom out': '缩小', 'Zoom reset': '重置缩放', Zoom: '缩放',
  Erase: '橡皮擦', Select: '选择', 'Rectangle selection': '框选',
  'Lasso selection': '自由框选', 'Fragment selection': '整体选择',
  Chain: '链工具', Template: '模板', Templates: '模板库', Text: '文本',
  'Functional Group': '官能团', 'Salts and Solvents': '盐与溶剂',
  Atom: '原子', Bond: '化学键', 'General': '常用', Shape: '图形',
  'S-group': '结构基团（SGroup）', 'S-Group': '结构基团（SGroup）', 'Sgroup': '结构基团（SGroup）',
  'R-group label': '取代基标签（R-group）', 'R-group fragment': '取代基片段（R-group）',
  'Charge': '电荷', 'Isotope': '同位素', 'Explicit hydrogens': '显式氢',
  'Reaction plus': '反应加号', 'Reaction arrow': '反应箭头',
  'Reaction mapping': '反应原子映射', 'Reaction automap': '反应自动映射',
  'Enhanced stereo': '增强立体标记', 'Attach Edit': '连接点编辑',
  'Recognize': '图片识别结构', 'Molecule': '分子',
  'New': '新建', 'Open from…': '打开…',
  'Single bond': '单键', 'Double bond': '双键', 'Triple bond': '三键',
  'Custom query': '自定义查询', 'Any bond': '任意键', 'Aromatic': '芳香键',
  'Single up': '单键（楔形向上）', 'Single down': '单键（楔形向下）',
  'Single cis-trans': '单键（顺反）', 'Double cis/trans': '双键（顺反）',
  'Double either': '双键（交叠）', 'Dative bond': '配位键',
}
function cnText(t) {
  const k = String(t || '').trim()
  return Object.prototype.hasOwnProperty.call(EN2CN, k) ? EN2CN[k] : null
}
function translateOnce(root) {
  if (!root) return
  root.querySelectorAll('[title], [aria-label], [placeholder]').forEach((el) => {
    for (const attr of ['title', 'aria-label', 'placeholder']) {
      const v = el.getAttribute(attr)
      const t = cnText(v)
      if (v && t !== null) el.setAttribute(attr, t)
    }
  })
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const todo = []
  let n
  while ((n = walk.nextNode())) {
    const t = cnText(n.nodeValue)
    if (t !== null) todo.push([n, t])
  }
  todo.forEach(([node, val]) => { node.nodeValue = val })
}
let cnObserver = null
let cnTimer = 0
function installCn(root) {
  if (!root || cnObserver) return
  const run = () => {
    clearTimeout(cnTimer)
    cnTimer = setTimeout(() => translateOnce(root), 80)
  }
  run()
  cnObserver = new MutationObserver(run)
  cnObserver.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['title', 'aria-label'] })
}

/* ---------------------------------------------------------------- 基团标签（#11）
   用 Ketcher 原生的 atom alias：选中原子后写入 alias，它就是那个端点上的原子标签，
   可以继续成键、可以再点开编辑、导出 molfile / 图片都不会丢。 */
function setAtomAlias(k, alias, atomId) {
  const sel = k.editor.selection() || {}
  let ids = Array.isArray(sel.atoms) ? sel.atoms.filter((v) => Number.isFinite(v)) : []
  if (Number.isFinite(atomId)) ids = [Number(atomId)]
  if (!ids.length) {
    return { ok: false, error: '先在画布里点选要命名的原子（通常是化学键末端的那个）' }
  }
  const restruct = k.editor.render && k.editor.render.ctab
  if (!restruct) return { ok: false, error: '画板还没就绪，稍等一秒再点' }
  const action = fromAtomsAttrs(restruct, ids, { alias }, false)
  k.editor.update(action)
  return { ok: true, ids }
}

function App() {
  const ref = React.useRef(null)
  React.useEffect(() => {
    async function onMsg(ev) {
      const data = ev.data || {}
      const reply = (payload) => ev.source && ev.source.postMessage({ type: 'ketcher:result', id: data.id, ...payload }, '*')
      if (data.type === 'ketcher:ping') { reply({ ok: true, pong: true }); return }
      const k = ref.current
      if (!k) { reply({ ok: false, error: 'not ready' }); return }
      try {
        if (data.type === 'ketcher:set') {
          await k.setMolecule(String(data.molfile || ''))
          reply({ ok: true })
          return
        }
        if (data.type === 'ketcher:get') {
          const mol = await k.getMolfile()
          let svg = ''
          try {
            let out = await k.generateImage(mol, { outputFormat: 'svg' })
            if (out && typeof out.text === 'function') out = await out.text()   // Blob
            svg = String(out || '')
          } catch (e) { svg = '' }
          reply({ ok: true, molfile: mol, svg, empty: !mol || mol.trim().length < 12 })
          return
        }
        if (data.type === 'ketcher:clean') {
          try { await k.layout() } catch (e) {}
          reply({ ok: true })
          return
        }
        if (data.type === 'ketcher:alias') {
          reply(setAtomAlias(k, String(data.alias || ''), data.atomId))
          return
        }
        reply({ ok: false, error: 'unknown op' })
      } catch (e) {
        reply({ ok: false, error: String(e) })
      }
    }
    window.addEventListener('message', onMsg)
    window.parent && window.parent.postMessage({ type: 'ketcher:ready' }, '*')
    return () => window.removeEventListener('message', onMsg)
  }, [])
  React.useEffect(() => {
    const t = setInterval(() => {
      const root = document.querySelector('.Ketcher-root') || document.getElementById('root')
      if (root) { installCn(root); clearInterval(t) }
    }, 400)
    return () => clearInterval(t)
  }, [])
  return <Editor staticResourcesUrl="" structServiceProvider={provider} onInit={(k) => { ref.current = k }} />
}

createRoot(document.getElementById('root')).render(<App />)
