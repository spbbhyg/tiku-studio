import { reactive } from 'vue'

/**
 * 跨页面存活的任务提示。
 *
 * 这个模块是模块级的（不是组件 state），所以路由切走、组件卸载都不会丢：
 * 长任务开始后随便切页面，回来还能看到「进行中 / 已完成」。
 */
export const tasks = reactive({
  running: [],          // [{ id, title, since }]
  toasts: [],           // [{ id, text, kind, at }]
})

let seq = 0
const nextId = () => `t${Date.now().toString(36)}${(seq++).toString(36)}`

export function taskBegin(title) {
  const id = nextId()
  tasks.running.push({ id, title, since: Date.now() })
  return id
}

export function taskEnd(id) {
  const i = tasks.running.findIndex((t) => t.id === id)
  if (i >= 0) tasks.running.splice(i, 1)
}

export function toast(text, kind = 'ok', ms = 6000) {
  const id = nextId()
  tasks.toasts.push({ id, text, kind, at: Date.now() })
  setTimeout(() => dropToast(id), ms)
  return id
}

export function dropToast(id) {
  const i = tasks.toasts.findIndex((t) => t.id === id)
  if (i >= 0) tasks.toasts.splice(i, 1)
}
