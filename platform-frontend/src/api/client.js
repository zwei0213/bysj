/**
 * 生产级统一 HTTP 客户端：
 * 1. 自动注入 Bearer Access Token 与 X-Request-ID 全链路追踪头；
 * 2. Access Token 过期 (401) 时自动使用 Refresh Token 无感续期重试；
 * 3. 提供支持 POST/PUT 与 FormData/二进制分片的带进度上传器，以及带鉴权的二进制文件下载器。
 */
const API_BASE = import.meta.env.VITE_API_BASE || ''

export function makeRequestId() {
  return 'web_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4)
}

let refreshPromise = null

async function tryRefreshAccessToken() {
  const refreshToken = localStorage.getItem('slp_refresh_token')
  if (!refreshToken) return false
  if (refreshPromise) return refreshPromise

  refreshPromise = (async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Request-ID': makeRequestId(),
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      })
      if (!resp.ok) return false
      const payload = await resp.json()
      const data = payload?.data
      if (!data?.access_token) return false
      localStorage.setItem('slp_access_token', data.access_token)
      if (data.refresh_token) localStorage.setItem('slp_refresh_token', data.refresh_token)
      if (data.user) localStorage.setItem('slp_user', JSON.stringify(data.user))
      return true
    } catch {
      return false
    } finally {
      refreshPromise = null
    }
  })()

  return refreshPromise
}

function clearExpiredSessionAndRedirect() {
  localStorage.removeItem('slp_access_token')
  localStorage.removeItem('slp_refresh_token')
  localStorage.removeItem('slp_user')
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

export async function apiRequest(path, { method = 'GET', body = undefined, headers = {}, _retried = false } = {}) {
  const token = localStorage.getItem('slp_access_token')
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData
  const isBinary =
    (typeof Blob !== 'undefined' && body instanceof Blob) ||
    (typeof ArrayBuffer !== 'undefined' && (body instanceof ArrayBuffer || ArrayBuffer.isView(body)))

  const reqHeaders = {
    'X-Request-ID': makeRequestId(),
    ...headers,
  }
  if (token) {
    reqHeaders['Authorization'] = `Bearer ${token}`
  }
  if (body !== undefined && !isFormData && !isBinary && !reqHeaders['Content-Type']) {
    reqHeaders['Content-Type'] = 'application/json'
  } else if (isBinary && !reqHeaders['Content-Type']) {
    reqHeaders['Content-Type'] = 'application/octet-stream'
  }

  let serializedBody = undefined
  if (body !== undefined) {
    serializedBody = isFormData || isBinary ? body : JSON.stringify(body)
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers: reqHeaders,
    body: serializedBody,
  })

  if (resp.status === 401 && !_retried && !path.startsWith('/api/v1/auth/')) {
    const refreshed = await tryRefreshAccessToken()
    if (refreshed) {
      return apiRequest(path, { method, body, headers, _retried: true })
    }
    clearExpiredSessionAndRedirect()
  }

  if (resp.status === 204) {
    return null
  }

  const payload = await resp.json().catch(() => null)
  if (!resp.ok) {
    const msg = payload?.error?.message || `请求失败 (HTTP ${resp.status})`
    const err = new Error(msg)
    err.code = payload?.error?.code || 'HTTP_ERROR'
    err.status = resp.status
    err.details = payload?.error?.details || []
    throw err
  }
  return payload?.data ?? payload
}

/**
 * 生产级 SSE 流式请求客户端（支持 Bearer Token、401 自动无感续期与逐事件回调）。
 */
export async function apiStreamRequest(
  path,
  { method = 'POST', body = undefined, headers = {}, onEvent = () => {}, _retried = false } = {}
) {
  const token = localStorage.getItem('slp_access_token')
  const reqHeaders = {
    'X-Request-ID': makeRequestId(),
    Accept: 'text/event-stream',
    ...headers,
  }
  if (token) reqHeaders['Authorization'] = `Bearer ${token}`
  if (body !== undefined && !reqHeaders['Content-Type']) {
    reqHeaders['Content-Type'] = 'application/json'
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers: reqHeaders,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (resp.status === 401 && !_retried && !path.startsWith('/api/v1/auth/')) {
    const refreshed = await tryRefreshAccessToken()
    if (refreshed) {
      return apiStreamRequest(path, { method, body, headers, onEvent, _retried: true })
    }
    clearExpiredSessionAndRedirect()
  }

  if (!resp.ok) {
    const payload = await resp.json().catch(() => null)
    const err = new Error(payload?.error?.message || `流式请求失败 (HTTP ${resp.status})`)
    err.code = payload?.error?.code || 'HTTP_ERROR'
    err.status = resp.status
    throw err
  }

  const reader = resp.body?.getReader()
  if (!reader) throw new Error('浏览器不支持流式响应读取')

  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let currentEvent = 'message'
  let donePayload = null

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() || ''

    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (!line) {
        currentEvent = 'message'
        continue
      }
      if (line.startsWith('event:')) {
        currentEvent = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        const jsonText = line.slice(5).trim()
        try {
          const parsed = JSON.parse(jsonText)
          onEvent(currentEvent, parsed)
          if (currentEvent === 'done') donePayload = parsed
        } catch {
          // 忽略非 JSON 心跳行
        }
      }
    }
  }
  return donePayload
}

/**
 * 支持 POST / PUT 及 FormData / Blob 二进制分片上传（带实时字节百分比回调与 401 自动续期）。
 */
export async function apiUploadWithProgress(path, bodyData, onProgress, options = {}) {
  const resolvedOpts = typeof options === 'string' ? { method: options } : (options || {})
  const method = (resolvedOpts.method || 'POST').toUpperCase()
  const extraHeaders = resolvedOpts.headers || {}
  const _retried = Boolean(resolvedOpts._retried)

  try {
    return await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open(method, `${API_BASE}${path}`, true)
      xhr.setRequestHeader('X-Request-ID', makeRequestId())
      const token = localStorage.getItem('slp_access_token')
      if (token) {
        xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      }

      const isFormData = typeof FormData !== 'undefined' && bodyData instanceof FormData
      const isBinary =
        (typeof Blob !== 'undefined' && bodyData instanceof Blob) ||
        (typeof ArrayBuffer !== 'undefined' && (bodyData instanceof ArrayBuffer || ArrayBuffer.isView(bodyData)))

      if (isBinary && !isFormData && !extraHeaders['Content-Type']) {
        xhr.setRequestHeader('Content-Type', 'application/octet-stream')
      }
      for (const [k, v] of Object.entries(extraHeaders)) {
        if (v !== undefined && v !== null) {
          xhr.setRequestHeader(k, String(v))
        }
      }

      if (xhr.upload && typeof onProgress === 'function') {
        xhr.upload.onprogress = (evt) => {
          if (evt.lengthComputable && evt.total > 0) {
            onProgress(Math.round((evt.loaded / evt.total) * 100))
          }
        }
      }

      xhr.onload = () => {
        let payload = null
        try {
          payload = xhr.responseText ? JSON.parse(xhr.responseText) : null
        } catch {
          payload = null
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(payload?.data ?? payload)
        } else {
          const msg = payload?.error?.message || `上传失败 (HTTP ${xhr.status})`
          const err = new Error(msg)
          err.code = payload?.error?.code || 'UPLOAD_ERROR'
          err.status = xhr.status
          err.details = payload?.error?.details || []
          reject(err)
        }
      }

      xhr.onerror = () => reject(new Error('网络异常导致文件上传中断，请检查网络连接'))
      xhr.send(bodyData)
    })
  } catch (err) {
    if (err?.status === 401 && !_retried && !path.startsWith('/api/v1/auth/')) {
      const refreshed = await tryRefreshAccessToken()
      if (refreshed) {
        return apiUploadWithProgress(path, bodyData, onProgress, {
          ...resolvedOpts,
          method,
          _retried: true,
        })
      }
      clearExpiredSessionAndRedirect()
    }
    throw err
  }
}

export function apiPutChunkWithProgress(path, chunkBody, onProgress, headers = {}) {
  return apiUploadWithProgress(path, chunkBody, onProgress, { method: 'PUT', headers })
}

/**
 * 带 Bearer Token 鉴权与自动续期的二进制文件流下载辅助函数（支持 .xlsx / .csv 等报表）。
 */
export async function apiDownloadBlob(path, defaultFilename = 'download.bin', _retried = false) {
  const token = localStorage.getItem('slp_access_token')
  const headers = {
    'X-Request-ID': makeRequestId(),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'GET',
    headers,
  })

  if (resp.status === 401 && !_retried) {
    const refreshed = await tryRefreshAccessToken()
    if (refreshed) {
      return apiDownloadBlob(path, defaultFilename, true)
    }
    clearExpiredSessionAndRedirect()
  }

  if (!resp.ok) {
    const payload = await resp.json().catch(() => null)
    const msg = payload?.error?.message || `文件导出失败 (HTTP ${resp.status})`
    const err = new Error(msg)
    err.status = resp.status
    throw err
  }

  let filename = defaultFilename
  const disposition = resp.headers.get('Content-Disposition') || ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i)
  if (utf8Match?.[1]) {
    try {
      filename = decodeURIComponent(utf8Match[1])
    } catch {
      filename = utf8Match[1]
    }
  } else if (plainMatch?.[1]) {
    filename = plainMatch[1]
  }

  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function formatSeconds(sec) {
  if (sec === null || sec === undefined || Number.isNaN(Number(sec))) return '00:00'
  const s = Math.max(0, Math.floor(Number(sec)))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}
