// 快捷键：统一定义、可在「设置 → 快捷键」里自由改，存 localStorage
const LS = 'tiku-shortcuts-v1'

export const ACTIONS = [
  { key: 'copy', label: '复制所选元素', def: 'Ctrl+C' },
  { key: 'paste', label: '粘贴', def: 'Ctrl+V' },
  { key: 'group', label: '组合所选', def: 'Ctrl+G' },
  { key: 'ungroup', label: '拆分组合', def: 'Ctrl+Shift+G' },
  { key: 'del', label: '删除所选', def: 'Delete' },
  { key: 'rotate', label: '旋转 90°', def: 'Ctrl+R' },
  { key: 'addText', label: '新建文本块', def: 'Ctrl+T' },
  { key: 'esc', label: '取消选择 / 退出编辑', def: 'Escape' },
]

export function defaults() {
  const out = {}
  for (const a of ACTIONS) out[a.key] = a.def
  return out
}

export function loadShortcuts() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS) || '{}')
    return { ...defaults(), ...(raw || {}) }
  } catch (e) {
    return defaults()
  }
}

export function saveShortcuts(map) {
  localStorage.setItem(LS, JSON.stringify(map))
}

/** 把键盘事件压成 'Ctrl+Shift+G' 这样的字符串；只按修饰键时返回 '' */
export function comboOf(e) {
  const k = e.key || ''
  if (!k || ['Control', 'Shift', 'Alt', 'Meta'].includes(k)) return ''
  const parts = []
  if (e.ctrlKey) parts.push('Ctrl')
  if (e.altKey) parts.push('Alt')
  if (e.shiftKey) parts.push('Shift')
  let key = k
  if (key === ' ') key = 'Space'
  else if (key.length === 1) key = key.toUpperCase()
  else if (key === 'Escape') key = 'Escape'
  parts.push(key)
  return parts.join('+')
}

export function matchCombo(e, action, map) {
  const want = (map && map[action]) || ''
  return !!want && comboOf(e) === want
}
