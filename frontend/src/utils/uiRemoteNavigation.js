import { getUiRemoteCapabilities } from '@/api/ui_automation'
import { useUserStore } from '@/stores/user'

let remoteDesktopEnabled = false
let capabilityLoaded = false
let capabilityRequest = null
let capabilityGeneration = 0

export const resetUiRemoteCapabilities = () => {
  // Invalidate pending responses as well as the cached capability on logout.
  capabilityGeneration += 1
  remoteDesktopEnabled = false
  capabilityLoaded = false
  capabilityRequest = null
}

export const loadUiRemoteCapabilities = () => {
  const userStore = useUserStore()
  if (!userStore.isAuthenticated) {
    resetUiRemoteCapabilities()
    return Promise.resolve(false)
  }
  if (capabilityLoaded) return Promise.resolve(remoteDesktopEnabled)
  if (capabilityRequest) return capabilityRequest

  // Explicitly preload after authentication; importing this module must not send requests.
  const generation = capabilityGeneration
  capabilityRequest = getUiRemoteCapabilities()
    .then(response => {
      if (generation !== capabilityGeneration || !userStore.isAuthenticated) return false
      remoteDesktopEnabled = Boolean(response.data.enabled)
      capabilityLoaded = true
      return remoteDesktopEnabled
    })
    // Optional capability failures disable the popup and can be retried on the next visit.
    .catch(() => false)
    .finally(() => {
      if (generation === capabilityGeneration) capabilityRequest = null
    })
  return capabilityRequest
}

export const reserveUiRemoteWindow = message => {
  if (!remoteDesktopEnabled || !useUserStore().isAuthenticated) return null

  const target = window.open('about:blank', '_blank')
  if (!target) return null

  try {
    const popupDocument = target.document
    popupDocument.open()
    popupDocument.write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>html,body{width:100%;height:100%;margin:0}body{display:grid;place-items:center;background:#f5f7fa;color:#303133;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.loading{display:flex;align-items:center;gap:12px}.spinner{width:18px;height:18px;border:2px solid #dcdfe6;border-top-color:#409eff;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body></body></html>')
    popupDocument.close()
    popupDocument.title = message
    const loading = popupDocument.createElement('main')
    loading.className = 'loading'
    loading.setAttribute('aria-live', 'polite')
    const spinner = popupDocument.createElement('span')
    spinner.className = 'spinner'
    spinner.setAttribute('aria-hidden', 'true')
    const label = popupDocument.createElement('span')
    label.textContent = message
    loading.append(spinner, label)
    popupDocument.body.replaceChildren(loading)
    return target
  } catch {
    target.close()
    return null
  }
}

export const openUiRemoteSession = (router, sessionId, reservedWindow = null) => {
  const route = { name: 'UiRemoteDesktop', params: { sessionId } }
  const url = router.resolve(route).href
  const target = reservedWindow && !reservedWindow.closed ? reservedWindow : window.open(url, '_blank')

  if (!target) {
    router.push({ ...route, query: { fallback: '1' } })
    return null
  }

  if (reservedWindow && !reservedWindow.closed) target.location.href = url
  target.focus()
  return target
}

export const closeReservedUiRemoteWindow = target => {
  if (target && !target.closed) target.close()
}
