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


// 把任何报错翻成一句中文人话（第一行），方便直接照做
export function errText(e) {
  const raw = String((e && (e.message || e.detail)) || e || '').trim()
  const m = raw.toLowerCase()
  if (!raw) return '出错了，但没拿到原因：再点一次试试，还不行就重启软件'
  if (/failed to fetch|networkerror|load failed|fetch failed/.test(m)) {
    return '连不上本地服务：先确认软件开着（或设置 →「AI 模型」点「重启内置服务」），再试一次'
  }
  if (/413|too large/.test(m)) return '文件太大了：先拆成几份再转'
  if (/401/.test(m)) return 'API Key 不对（401）：去设置 →「AI 模型」检查 Key 是否复制完整'
  if (/402/.test(m)) return '账户余额不足（402）：充值或改用内置免费模型'
  if (/403/.test(m)) return '没权限（403）：多半是 GitHub 限流或文件被占用，等一会儿再试'
  if (/429/.test(m)) return '调用太频繁（429）：等半分钟再试，或改用本地模型'
  if (/500|internal server error/.test(m)) return '本地服务内部出错了：一般是数据文件被占用，重启软件即可'
  if (/502|503|504/.test(m)) return '上游服务暂时不可用（' + raw.slice(0, 24) + '）：等一会儿再试'
  if (/timeout|timed out/.test(m)) return '等太久了（超时）：网络不稳或文件太大，重试一次'
  if (/permission|denied|拒绝访问/.test(m)) return '这个文件被别的程序占着（比如 Word 正开着它）：关掉再试'
  if (/no such file|not found|找不到/.test(m)) return '找不到文件：可能被移动或删掉了，重新选一次'
  if (/中文|[一-鿿]/.test(raw)) return raw      // 后端本来就是中文，直接用
  return raw
}

export const api = {
  errText,
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
