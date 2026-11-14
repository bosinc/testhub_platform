<template>
  <el-dialog v-model="dialogVisible" :title="$t('uiAutomation.replay.title')" width="760px" :close-on-click-modal="false" destroy-on-close>
    <div v-if="script" class="replay-header">
      <span>{{ $t('uiAutomation.replay.currentScript') }}</span>
      <el-tag :type="statusType">{{ statusText }}</el-tag>
      <div class="replay-actions">
        <el-select v-model="form.browser" size="small" style="width: 150px">
          <el-option label="Chromium" value="chromium" />
        </el-select>
        <el-select v-model="form.storage_state_id" size="small" clearable placeholder="无登录态" style="width: 170px">
          <el-option v-for="state in storageStates" :key="state.id" :label="state.name" :value="state.id" />
        </el-select>
        <el-checkbox v-model="form.headless">{{ $t('uiAutomation.replay.headless') }}</el-checkbox>
        <el-button type="primary" size="small" :loading="running" :disabled="running" @click="run">
          <el-icon><VideoPlay /></el-icon>{{ $t('uiAutomation.common.run') }}
        </el-button>
      </div>
    </div>
    <el-input v-model="form.base_url" :placeholder="$t('uiAutomation.replay.baseUrlPlaceholder')" class="base-url" />
    <div class="log-title"><span>{{ $t('uiAutomation.replay.executionLogs') }}</span><el-button text size="small" @click="logs = ''">{{ $t('uiAutomation.replay.clear') }}</el-button></div>
    <pre class="log-panel">{{ logs || $t('uiAutomation.replay.emptyLogs') }}</pre>
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" />
    <template #footer>
      <el-button v-if="remoteSessionId" @click="remoteDesktop.open(remoteSessionId)">{{ $t('uiAutomation.remote.liveView') }}</el-button>
      <el-button v-if="running" type="danger" plain @click="abort">{{ $t('uiAutomation.common.stop') }}</el-button>
      <el-button @click="close">{{ $t('uiAutomation.common.close') }}</el-button>
    </template>
  </el-dialog>
  <UiRemoteDialog ref="remoteDesktop" />
</template>

<script setup>
import UiRemoteDialog from '@/components/ui-automation/UiRemoteDialog.vue'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { VideoPlay } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { abortTestScriptExecution, getStorageStates, getTestExecutionLogs, runRecordedScript, runUnsavedRecordedScript } from '@/api/ui_automation'

const props = defineProps({ visible: Boolean, script: { type: Object, default: null } })
const emit = defineEmits(['update:visible', 'started'])
const { t } = useI18n()
const dialogVisible = computed({ get: () => props.visible, set: value => emit('update:visible', value) })
const form = reactive({ browser: 'chromium', headless: true, base_url: '', storage_state_id: null })
const storageStates = ref([])
const executionId = ref(null)
const remoteDesktop = ref(null)
const remoteSessionId = ref(null)
const status = ref('IDLE')
const logs = ref('')
const errorMessage = ref('')
const submitting = ref(false)
const running = computed(() => submitting.value || Boolean(executionId.value && ['PENDING', 'RUNNING'].includes(status.value)))
let timer = null

const statusType = computed(() => ({ SUCCESS: 'success', FAILED: 'danger', ABORTED: 'info', RUNNING: 'warning' }[status.value] || 'info'))
const statusText = computed(() => ({ IDLE: t('uiAutomation.status.ready'), PENDING: t('uiAutomation.status.pending'), RUNNING: t('uiAutomation.status.running'), SUCCESS: t('uiAutomation.status.success'), FAILED: t('uiAutomation.status.failed'), ABORTED: t('uiAutomation.status.aborted') }[status.value] || status.value))
watch(() => props.visible, value => { if (value) { reset(); loadStorageStates() } })
watch(executionId, value => { if (value) poll() })

const reset = () => {
  stopPolling()
  executionId.value = null
  remoteSessionId.value = null
  status.value = 'IDLE'
  submitting.value = false
  logs.value = ''
  errorMessage.value = ''
  form.browser = 'chromium'
  form.headless = true
  form.base_url = props.script?.start_url || ''
  form.storage_state_id = null
}

const loadStorageStates = async () => {
  // 弹窗打开时按脚本所属项目加载，避免复用其他项目的登录态。
  const projectId = props.script?.project_id || props.script?.project?.id
  if (!projectId) { storageStates.value = []; return }
  try {
    const response = await getStorageStates({ project: projectId, page_size: 200 })
    storageStates.value = response.data.results || response.data
  } catch { storageStates.value = [] }
}

const run = async () => {
  if (!props.script || running.value) return
  errorMessage.value = ''
  submitting.value = true
  try {
    const response = props.script.id
      ? await runRecordedScript(props.script.id, form)
      : await runUnsavedRecordedScript({
          ...form,
          project: props.script.project_id || props.script.project?.id,
          content: props.script.content,
          start_url: props.script.start_url || ''
        })
    remoteSessionId.value = response.data.remote_session_id || null
    if (remoteSessionId.value) remoteDesktop.value.open(remoteSessionId.value)
    executionId.value = response.data.id
    status.value = response.data.status || 'PENDING'
    emit('started', response.data)
  } catch (error) {
    errorMessage.value = error.response?.data?.error || t('uiAutomation.replay.runFailed')
  } finally {
    submitting.value = false
  }
}

const poll = () => {
  stopPolling()
  const fetchLogs = async () => {
    if (!executionId.value) return
    try {
      const response = await getTestExecutionLogs(executionId.value)
      status.value = response.data.status
      logs.value = response.data.logs || ''
      errorMessage.value = response.data.error_message || ''
      if (!['PENDING', 'RUNNING'].includes(status.value)) stopPolling()
    } catch (error) { errorMessage.value = error.response?.data?.error || t('uiAutomation.replay.logsFailed') }
  }
  fetchLogs()
  timer = window.setInterval(fetchLogs, 1500)
}

const abort = async () => {
  if (!executionId.value) return
  try { await abortTestScriptExecution(executionId.value); status.value = 'ABORTED'; stopPolling(); ElMessage.success(t('uiAutomation.replay.abortSuccess')) } catch (error) { errorMessage.value = error.response?.data?.error || t('uiAutomation.replay.abortFailed') }
}
const stopPolling = () => { if (timer) { window.clearInterval(timer); timer = null } }
const close = () => { stopPolling(); dialogVisible.value = false }
onBeforeUnmount(stopPolling)
</script>

<style scoped>
.replay-header { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; color: #4d5562; }
.replay-actions { display: flex; align-items: center; gap: 12px; margin-left: auto; }
.base-url { margin-bottom: 16px; }
.log-title { display: flex; justify-content: space-between; align-items: center; padding: 9px 12px; color: #5a626e; border: 1px solid #e8eaee; border-bottom: 0; background: #f8fafb; }
.log-panel { height: 300px; margin: 0 0 14px; padding: 15px; overflow: auto; border-radius: 0 0 6px 6px; background: var(--el-bg-color-page); color: var(--el-text-color-primary); border: 1px solid var(--el-border-color); font: 13px/1.65 Monaco, Consolas, monospace; white-space: pre-wrap; }
</style>
