async function req(method, url, body, isForm = false) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    if (isForm) {
      opts.body = body
    } else {
      opts.headers['Content-Type'] = 'application/json'
      opts.body = JSON.stringify(body)
    }
  }
  const resp = await fetch(url, opts)
  const text = await resp.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!resp.ok) {
    const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : resp.statusText
    throw new Error(detail)
  }
  return data
}

export const api = {
  get: (u) => req('GET', u),
  post: (u, b) => req('POST', u, b),
  postForm: (u, fd) => req('POST', u, fd, true),
  patch: (u, b) => req('PATCH', u, b),
  put: (u, b) => req('PUT', u, b),
  del: (u) => req('DELETE', u),
}

// 本地题库（D:\化学竞赛题库）：本项目的题库唯一来源，只走 /api/chemy/*，不直连 8080
export const chemy = {
  status: () => api.get('/api/chemy/status'),
  start: () => api.post('/api/chemy/start'),
  topics: () => api.get('/api/chemy/topics'),
  taxonomy: () => api.get('/api/chemy/taxonomy'),
  tree: (topic) => api.get(`/api/chemy/tree?topic=${encodeURIComponent(topic)}`),
  search: (p = {}) => {
    const qs = new URLSearchParams()
    Object.entries(p).forEach(([k, v]) => { if (v !== '' && v != null) qs.set(k, v) })
    return api.get(`/api/chemy/search?${qs}`)
  },
  question: (qid) => api.get(`/api/chemy/questions/${qid}`),
  drafts: () => api.get('/api/chemy/drafts'),
  // 把一道题送进题库：{title, topic, year, stem, parts, answer, author, images, publish}
  publish: (body) => api.post('/api/chemy/publish', body),
  exportUrl: (ids, mode = 'print', extra = '') =>
    `/api/chemy/export?ids=${encodeURIComponent(ids.join(','))}&mode=${mode}${extra}`,
}

export const DIFF_LABEL = { 1: '基础', 2: '中等', 3: '提优' }
export const SCOPE_LABEL = {
  ok: '范围内', extension: '提优拓展', suspect: '疑似超纲', pending: '待对纲',
}
export const STATUS_LABEL = { confirmed: '已确认', draft: '草稿' }
export const ROLE_LABEL = { textbook: '课本·纲', workbook: '教辅', paper_set: '试卷集', self_made: '自编卷' }
