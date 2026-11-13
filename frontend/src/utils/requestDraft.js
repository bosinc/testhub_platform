import { normalizeAssertions, stripLegacyAssertionValue } from './assertions.js'

export const BODY_TYPES = ['json', 'raw', 'form-data', 'x-www-form-urlencoded', 'binary']
export const REQUEST_FIELDS = [
  'method', 'url', 'request_type', 'headers', 'params', 'path_params', 'body', 'auth',
  'pre_request_script', 'post_request_script', 'assertions', 'extractors', 'settings'
]

// Clone JSON configuration without sharing nested objects with the owning page.
export function cloneDraft(value) {
  if (Array.isArray(value)) return value.map(cloneDraft)
  if (value && typeof value === 'object') {
    if (typeof Blob !== 'undefined' && value instanceof Blob) return value
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneDraft(item)]))
  }
  return value
}

export function canonicalRows(rows = []) {
  if (!Array.isArray(rows)) throw new Error('apiTesting.requestEditor.rowsInvalid')
  return rows.map(row => ({
    key: row.key ?? '', value: row.value === undefined ? '' : cloneDraft(row.value), enabled: row.enabled !== false,
    type: row.type || 'text', description: row.description ?? ''
  }))
}

// The editor keeps the Postman-style `addTo` label; the API contract uses `in`.
function toAuthDraft(auth) {
  if (!auth || typeof auth !== 'object') return { type: 'none' }
  const draft = { ...auth }
  if (draft.type === 'api_key') {
    draft.addTo = draft.in || draft.addTo || 'header'
    delete draft.in
  }
  return draft
}

function toAuthPayload(auth) {
  if (!auth || typeof auth !== 'object') return { type: 'none' }
  const payload = { ...auth }
  if (payload.type === 'api_key') {
    payload.in = payload.addTo || payload.in || 'header'
    delete payload.addTo
  }
  return payload
}

export function createRequestDraft(source = {}) {
  return {
    method: source.method || 'GET', url: source.url || '', request_type: source.request_type || 'HTTP',
    headers: canonicalRows(source.headers ?? []), params: canonicalRows(source.params ?? []),
    path_params: canonicalRows(source.path_params ?? []),
    body: cloneDraft(source.body?.type ? source.body : { type: 'raw', data: '' }),
    auth: toAuthDraft(source.auth),
    pre_request_script: source.pre_request_script || '', post_request_script: source.post_request_script || '',
    assertions: cloneDraft(normalizeAssertions(source.assertions)), extractors: cloneDraft(source.extractors || []),
    settings: cloneDraft(source.settings || {})
  }
}

export async function serializeFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192))
  }
  return { filename: file.name || 'binary', content_base64: btoa(binary), content_type: file.type || 'application/octet-stream' }
}

function validateFile(value) {
  if (!value || typeof value !== 'object' || typeof value.filename !== 'string' ||
      typeof value.content_base64 !== 'string' || typeof value.content_type !== 'string') {
    throw new Error('apiTesting.requestEditor.fileRequired')
  }
}

export async function serializeRequestDraft(source) {
  const draft = createRequestDraft(source)
  if (!BODY_TYPES.includes(draft.body.type)) throw new Error('apiTesting.requestEditor.bodyUnsupported')
  if (draft.body.type === 'json' && typeof draft.body.data === 'string') {
    try { draft.body.data = JSON.parse(draft.body.data) }
    catch { throw new Error('apiTesting.requestEditor.jsonInvalid') }
  }
  if (['form-data', 'x-www-form-urlencoded'].includes(draft.body.type)) {
    draft.body.data = canonicalRows(draft.body.data)
    for (const row of draft.body.data) {
      if (row.type === 'file') {
        if (draft.body.type !== 'form-data') throw new Error('apiTesting.requestEditor.urlencodedFile')
        if (typeof Blob !== 'undefined' && row.value instanceof Blob) row.value = await serializeFile(row.value)
        // Disabled file rows remain serializable too; paths must never reach the API.
        validateFile(row.value)
      }
    }
  }
  if (draft.body.type === 'binary' && draft.body.data != null && draft.body.data !== '') {
    if (typeof Blob !== 'undefined' && draft.body.data instanceof Blob) draft.body.data = await serializeFile(draft.body.data)
    validateFile(draft.body.data)
  }
  draft.assertions = draft.assertions.map(stripLegacyAssertionValue)
  draft.auth = toAuthPayload(draft.auth)
  return draft
}

export function validateBaseUrl(value, allowEmpty = false) {
  if (!value) return allowEmpty
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !!url.hostname && !url.username && !url.password &&
      !url.search && !url.hash && !/[?#]/.test(value) && !/\{\{|\s/.test(value)
  } catch {
    return false
  }
}

// This is a display-only estimate. The backend owns variable resolution and final addressing.
export function previewRequestUrl(draft, environment) {
  if (!validateBaseUrl(environment?.base_url)) return ''
  let path = String(draft.url || '')
  if (/^[a-z][\w+.-]*:/i.test(path) || path.startsWith('//') || /[?#]/.test(path)) return ''
  for (const row of draft.path_params || []) {
    if (row.enabled !== false && row.key) path = path.split(`{${row.key}}`).join(encodeURIComponent(String(row.value)))
  }
  try {
    if (path.split('/').some(part => ['.', '..'].includes(decodeURIComponent(part)))) return ''
  } catch {
    return ''
  }
  let base = environment.base_url.replace(/\/+$/, '')
  if (draft.request_type === 'WEBSOCKET') base = base.replace(/^http/, 'ws')
  const query = new URLSearchParams()
  for (const row of draft.params || []) {
    if (row.enabled !== false && row.key) query.append(row.key, String(row.value))
  }
  return `${base}/${path.replace(/^\/+/, '')}${query.size ? `?${query}` : ''}`
}

export function curlModelToDraft(model) {
  const source = model.body
  const types = { formdata: 'form-data', urlencoded: 'x-www-form-urlencoded', none: 'raw' }
  const type = types[source.mode] || source.mode
  const data = source.mode === 'json' ? source.json : source.mode === 'formdata' ? source.formdata :
    source.mode === 'urlencoded' ? source.urlencoded : source.raw || ''
  const path = model.path.replace(/%7B/gi, '{').replace(/%7D/gi, '}')
  const names = [...new Set(Array.from(path.matchAll(/(?<!\{)\{([^{}]+)\}(?!\})/g), match => match[1]))]
  return createRequestDraft({
    method: model.method, url: path, request_type: 'HTTP', params: canonicalRows(model.query),
    path_params: names.map(key => ({ key, value: '', enabled: true, type: 'text', description: '' })),
    headers: canonicalRows(model.headers), body: { type, data }, auth: model.auth || { type: 'none' },
    settings: { timeout: (model.timeout || 30000) / 1000 }
  })
}

export function toCodegenModel(draft, fullUrl) {
  const url = new URL(fullUrl)
  const type = draft.body.type
  const modes = { 'form-data': 'formdata', 'x-www-form-urlencoded': 'urlencoded' }
  return {
    method: draft.method, baseURL: url.origin, path: url.pathname,
    query: Array.from(url.searchParams, ([key, value]) => ({ key, value, enabled: true })),
    headers: canonicalRows(draft.headers), auth: cloneDraft(draft.auth),
    body: { mode: modes[type] || type, raw: type === 'raw' ? draft.body.data : '',
      json: typeof draft.body.data === 'string' ? draft.body.data : JSON.stringify(draft.body.data),
      formdata: type === 'form-data' ? canonicalRows(draft.body.data) : [],
      urlencoded: type === 'x-www-form-urlencoded' ? canonicalRows(draft.body.data) : [],
      binary: type === 'binary' ? draft.body.data : undefined },
    timeout: (draft.settings.timeout || 30) * 1000
  }
}
