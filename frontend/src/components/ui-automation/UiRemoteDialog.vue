<template>
  <el-dialog v-model="visible" :title="t('uiAutomation.remote.title')" width="92%" append-to-body :close-on-click-modal="false">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-if="!desktopUrl && active" v-loading="true" class="desktop-wait">{{ t('uiAutomation.remote.preparing') }}</div>
    <iframe v-if="desktopUrl && visible" :src="desktopUrl" :title="t('uiAutomation.remote.browserTitle')" class="desktop-frame" referrerpolicy="no-referrer" />
    <el-empty v-if="!active && !desktopUrl" :description="t('uiAutomation.remote.ended')" />
    <template #footer>
      <span class="desktop-tip">{{ t('uiAutomation.remote.closeHint') }}</span>
      <el-button v-if="active" @click="reconnect">{{ t('uiAutomation.remote.reconnect') }}</el-button>
      <el-button v-if="active" type="danger" @click="stop">{{ t('uiAutomation.remote.stopExecution') }}</el-button>
      <el-button @click="visible = false">{{ t('uiAutomation.remote.closeView') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { connectUiRemoteSession, getUiRemoteSession, stopUiRemoteSession } from '@/api/ui_automation'

const emit = defineEmits(['finished'])
const { t } = useI18n()
const visible = ref(false)
const sessionId = ref(null)
const status = ref('starting')
const desktopUrl = ref('')
const error = ref('')
const active = computed(() => ['starting', 'ready', 'running'].includes(status.value))
let timer = null
let disposed = false
let generation = 0

const clearTimer = () => { clearTimeout(timer); timer = null }
const reconnect = () => { desktopUrl.value = ''; error.value = '' }
const poll = async (id, currentGeneration) => {
  try {
    const response = await getUiRemoteSession(id)
    if (disposed || generation !== currentGeneration) return
    status.value = response.data.status
    error.value = response.data.error || ''
    if (visible.value && ['ready', 'running'].includes(status.value) && !desktopUrl.value) {
      const connection = await connectUiRemoteSession(id)
      if (disposed || generation !== currentGeneration) return
      // 覆盖 noVNC 可能记住的服务器设置，始终连接当前同源鉴权入口。
      const url = new URL(connection.data.url, window.location.origin)
      url.searchParams.set('host', window.location.hostname)
      url.searchParams.set('port', window.location.port || (window.location.protocol === 'https:' ? '443' : '80'))
      url.searchParams.set('encrypt', String(window.location.protocol === 'https:'))
      desktopUrl.value = url.href
    }
    if (!active.value) {
      desktopUrl.value = ''
      emit('finished', response.data)
      return
    }
  } catch (err) {
    if (disposed || generation !== currentGeneration) return
    error.value = err.response?.data?.error || err.message || t('uiAutomation.remote.loadFailed')
    // 无权限或会话已删除时停止轮询，避免持续请求失效的地址。
    if ([403, 404].includes(err.response?.status)) {
      status.value = 'failed'
      desktopUrl.value = ''
      emit('finished', { status: 'failed', error: error.value })
      return
    }
  }
  if (!disposed && generation === currentGeneration) timer = setTimeout(() => poll(id, currentGeneration), 1500)
}
const open = id => {
  visible.value = true
  if (sessionId.value === id) {
    if (active.value) reconnect()
    return
  }
  clearTimer()
  sessionId.value = id
  status.value = 'starting'
  reconnect()
  // 关闭弹窗后仍轮询，保持执行状态；卸载组件只断开观看，不停止测试。
  poll(id, ++generation)
}
const stop = async () => {
  try { await stopUiRemoteSession(sessionId.value) }
  catch (err) { error.value = err.response?.data?.error || t('uiAutomation.remote.stopFailed') }
}
onBeforeUnmount(() => { disposed = true; generation++; clearTimer() })
defineExpose({ open })
</script>

<style scoped>
.desktop-frame { width: 100%; height: 72vh; border: 0; background: #171717; }
.desktop-wait { min-height: 240px; display: grid; place-items: center; }
.desktop-tip { margin-right: 16px; color: var(--el-text-color-secondary); }
</style>
