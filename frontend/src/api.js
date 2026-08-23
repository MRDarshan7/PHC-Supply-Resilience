// Backend access. The URL comes from VITE_BACKEND_URL only — there is no
// fallback, so a missing value shows up as a configuration error in the UI
// rather than silently pointing at localhost.

const BACKEND_URL = String(import.meta.env.VITE_BACKEND_URL || '').trim().replace(/\/+$/, '')

export const backendUrl = BACKEND_URL
export const backendConfigured = BACKEND_URL.length > 0

// Render's free tier sleeps after inactivity; a cold start can take ~50 s.
// Nothing here gives up before that.
const TIMEOUT_MS = 120_000

function describeNetworkError(err) {
  if (err.name === 'AbortError') return `No response from the backend after ${TIMEOUT_MS / 1000} s`
  if (err instanceof TypeError) return `Could not reach the backend at ${BACKEND_URL} (${err.message})`
  return err.message
}

async function request(path, options = {}) {
  if (!backendConfigured) throw new Error('VITE_BACKEND_URL is not set')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  let res
  try {
    res = await fetch(BACKEND_URL + path, {
      ...options,
      signal: controller.signal,
      headers: { 'content-type': 'application/json', ...(options.headers || {}) },
    })
  } catch (err) {
    throw new Error(describeNetworkError(err))
  } finally {
    clearTimeout(timer)
  }
  const text = await res.text()
  let body = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!res.ok) {
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : body
    const message =
      typeof detail === 'string'
        ? detail
        : (detail && (detail.error || detail.message)) || `${res.status} ${res.statusText}`
    const err = new Error(message)
    err.status = res.status
    err.detail = detail
    throw err
  }
  return body
}

const enc = encodeURIComponent

export const api = {
  health: () => request('/health'),
  facilities: () => request('/facilities'),
  facility: (id) => request(`/facilities/${enc(id)}`),
  outbreaks: () => request('/outbreaks'),
  ingest: (filename) => request('/ingest-idsp', { method: 'POST', body: JSON.stringify({ filename }) }),
  recommend: (facilityId, medicineId) => request(`/recommend/${enc(facilityId)}/${enc(medicineId)}`),
  approve: (transferId, approvedBy) =>
    request(`/transfers/${enc(transferId)}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approved_by: approvedBy || 'District Medical Officer' }),
    }),
}
