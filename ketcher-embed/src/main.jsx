import React from 'react'
import { createRoot } from 'react-dom/client'
import { Editor } from 'ketcher-react'
import { StandaloneStructServiceProvider } from 'ketcher-standalone'
import 'ketcher-react/dist/index.css'

// ketcher-standalone 的 chunk 在运行期会引用 CommonJS 的 require，这里补个兜底
if (typeof window !== 'undefined' && typeof window.require === 'undefined') {
  window.require = function require(name) { return window[name] || {} }
}

const provider = new StandaloneStructServiceProvider()

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
        reply({ ok: false, error: 'unknown op' })
      } catch (e) {
        reply({ ok: false, error: String(e) })
      }
    }
    window.addEventListener('message', onMsg)
    window.parent && window.parent.postMessage({ type: 'ketcher:ready' }, '*')
    return () => window.removeEventListener('message', onMsg)
  }, [])
  return <Editor staticResourcesUrl="" structServiceProvider={provider} onInit={(k) => { ref.current = k }} />
}

createRoot(document.getElementById('root')).render(<App />)
