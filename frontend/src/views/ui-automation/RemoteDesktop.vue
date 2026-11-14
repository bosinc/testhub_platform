<template>
  <main class="remote-page">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-if="!desktopUrl && active" v-loading="true" class="desktop-wait">
      {{ t('uiAutomation.remote.preparing') }}
    </div>
    <iframe
      v-if="desktopUrl"
      :src="desktopUrl"
      :title="t('uiAutomation.remote.browserTitle')"
      class="desktop-frame"
      referrerpolicy="no-referrer"
    />
    <footer class="remote-footer">
      <span class="desktop-tip">{{ t('uiAutomation.remote.closeHint') }}</span>
      <el-button v-if="active" @click="reconnect">{{ t('uiAutomation.remote.reconnect') }}</el-button>
      <el-button v-if="active" type="danger" @click="stop">{{ t('uiAutomation.remote.stopExecution') }}</el-button>
      <el-button @click="leave()">{{ t('uiAutomation.remote.closeView') }}</el-button>
    </footer>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { connectUiRemoteSession, getUiRemoteSession, stopUiRemoteSession } from '@/api/ui_automation'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const sessionId = String(route.params.sessionId || '')
const status = ref('starting')
const desktopUrl = ref('')
const error = ref('')
const active = computed(() => ['starting', 'ready', 'running'].includes(status.value))
let timer = null
let disposed = false
let generation = 0

const clearTimer = () => { clearTimeout(timer); timer = null }
const reconnect = () => { desktopUrl.value = ''; error.value = '' }

const leave = session => {
  clearTimer()
  if (session && window.opener && !window.opener.closed) {
    window.opener.postMessage({ type: 'testhub-ui-remote-finished', sessionId, session }, window.location.origin)
  }
  if (window.opener) window.setTimeout(() => window.close(), session ? 50 : 0)
  else if (route.query.fallback === '1' && window.history.state?.back?.startsWith('/')) router.back()
  else router.replace('/ui-automation')
}

const poll = async currentGeneration => {
  try {
    const response = await getUiRemoteSession(sessionId)
    if (disposed || generation !== currentGeneration) return
    status.value = response.data.status
    error.value = response.data.error || ''
    if (['ready', 'running'].includes(status.value) && !desktopUrl.value) {
      const connection = await connectUiRemoteSession(sessionId)
      if (disposed || generation !== currentGeneration) return
      const url = new URL(connection.data.url, window.location.origin)
      url.searchParams.set('host', window.location.hostname)
      url.searchParams.set('port', window.location.port || (window.location.protocol === 'https:' ? '443' : '80'))
      url.searchParams.set('encrypt', String(window.location.protocol === 'https:'))
      desktopUrl.value = url.href
    }
    if (!active.value) {
      leave(response.data)
      return
    }
  } catch (err) {
    if (disposed || generation !== currentGeneration) return
    error.value = err.response?.data?.error || err.message || t('uiAutomation.remote.loadFailed')
    if ([403, 404].includes(err.response?.status)) {
      status.value = 'failed'
      leave({ sessionId, status: 'failed', error: error.value })
      return
    }
  }
  if (!disposed && generation === currentGeneration) timer = setTimeout(() => poll(currentGeneration), 1500)
}

const stop = async () => {
  try { await stopUiRemoteSession(sessionId) }
  catch (err) { error.value = err.response?.data?.error || t('uiAutomation.remote.stopFailed') }
}

onMounted(() => {
  if (!sessionId) {
    leave()
    return
  }
  poll(++generation)
})
onBeforeUnmount(() => { disposed = true; generation++; clearTimer() })
</script>

<style scoped>
.remote-page { display: flex; flex-direction: column; width: 100%; height: 100vh; height: 100dvh; overflow: hidden; background: #171717; }
.remote-page :deep(.el-alert) { flex: 0 0 auto; }
.desktop-frame { display: block; flex: 1 1 auto; width: 100%; min-height: 0; border: 0; background: #171717; }
.desktop-wait { display: grid; flex: 1 1 auto; min-height: 0; place-items: center; color: #fff; }
.remote-footer { display: flex; flex: 0 0 auto; align-items: center; justify-content: flex-end; gap: 10px; padding: 10px 20px 12px; background: var(--el-bg-color); }
.desktop-tip { margin-right: auto; color: var(--el-text-color-secondary); }
@media (max-width: 640px) {
  .remote-footer { padding-right: 12px; padding-left: 12px; }
  .remote-footer { flex-wrap: wrap; }
  .desktop-tip { width: 100%; margin-right: 0; }
}
</style>
