export const global = globalThis
export const process = globalThis.process || {
  env: { NODE_ENV: 'production' }, browser: true, version: '', versions: {},
  nextTick: (fn, ...a) => setTimeout(() => fn(...a), 0),
  cwd: () => '/', platform: 'browser', argv: [],
  emit() {}, on() {}, once() {}, off() {}, removeListener() {},
}
