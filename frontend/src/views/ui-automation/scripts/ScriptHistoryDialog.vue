<template>
  <el-dialog v-model="dialogVisible" :title="$t('uiAutomation.history.title')" width="900px" destroy-on-close>
    <div class="history-layout">
      <el-table :data="records" v-loading="recordsLoading" size="small" stripe height="360" @row-click="selectRecord">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="status" :label="$t('uiAutomation.common.status')" width="100">
          <template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="browser" :label="$t('uiAutomation.replay.browser')" width="110" />
        <el-table-column prop="duration" :label="$t('uiAutomation.history.duration')" width="100" />
        <el-table-column prop="created_at" :label="$t('uiAutomation.history.time')" min-width="170"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
      </el-table>
      <div class="history-detail" v-loading="logsLoading">
        <div class="detail-title">{{ $t('uiAutomation.replay.executionLogs') }}</div>
        <pre class="log-panel">{{ detailLogs || $t('uiAutomation.history.selectRecord') }}</pre>
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" />
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getScriptExecutions, getTestExecutionLogs } from '@/api/ui_automation'

const props = defineProps({ visible: Boolean, script: { type: Object, default: null } })
const emit = defineEmits(['update:visible'])
const { t } = useI18n()
const dialogVisible = computed({ get: () => props.visible, set: value => emit('update:visible', value) })
const records = ref([])
const recordsLoading = ref(false)
const logsLoading = ref(false)
const detailLogs = ref('')
const detailError = ref('')
let historyRequestId = 0
let logsRequestId = 0
// 关闭弹窗或切换脚本时，让上次列表及日志请求失效。
watch(() => [props.visible, props.script?.id], ([visible]) => {
  if (visible) load()
  else {
    historyRequestId += 1
    logsRequestId += 1
    recordsLoading.value = false
    logsLoading.value = false
  }
})

const load = async () => {
  const requestId = ++historyRequestId
  logsRequestId += 1
  logsLoading.value = false
  if (!props.script) {
    records.value = []
    recordsLoading.value = false
    return
  }
  recordsLoading.value = true
  records.value = []
  detailLogs.value = ''
  detailError.value = ''
  try {
    const response = await getScriptExecutions(props.script.id, { page_size: 100 })
    if (requestId === historyRequestId) records.value = response.data.results || response.data || []
  } catch (error) {
    if (requestId === historyRequestId) detailError.value = error.response?.data?.detail || t('uiAutomation.history.loadFailed')
  } finally {
    if (requestId === historyRequestId) recordsLoading.value = false
  }
}
const selectRecord = async row => {
  const requestId = ++logsRequestId
  logsLoading.value = true
  detailLogs.value = ''
  detailError.value = ''
  try {
    const response = await getTestExecutionLogs(row.id)
    if (requestId === logsRequestId) detailLogs.value = response.data.logs || response.data.error_message || ''
  } catch (error) {
    if (requestId === logsRequestId) detailError.value = t('uiAutomation.history.loadFailed')
  } finally {
    if (requestId === logsRequestId) logsLoading.value = false
  }
}
const statusType = value => ({ SUCCESS: 'success', FAILED: 'danger', ABORTED: 'info', RUNNING: 'warning' }[value] || 'info')
const statusText = value => ({ PENDING: t('uiAutomation.status.pending'), RUNNING: t('uiAutomation.status.running'), SUCCESS: t('uiAutomation.status.success'), FAILED: t('uiAutomation.status.failed'), ABORTED: t('uiAutomation.status.aborted') }[value] || value)
const formatTime = value => value ? new Date(value).toLocaleString() : '-'
</script>

<style scoped>
.history-layout { display: grid; grid-template-columns: 1.35fr 1fr; gap: 16px; }
.history-detail { min-width: 0; }
.detail-title { padding: 9px 12px; color: #5a626e; border: 1px solid #e8eaee; border-bottom: 0; background: #f8fafb; }
.log-panel { height: 322px; margin: 0; padding: 15px; overflow: auto; border-radius: 0 0 6px 6px; background: var(--el-bg-color-page); color: var(--el-text-color-primary); border: 1px solid var(--el-border-color); font: 12px/1.6 Monaco, Consolas, monospace; white-space: pre-wrap; }
@media (max-width: 760px) { .history-layout { grid-template-columns: 1fr; } }
</style>
