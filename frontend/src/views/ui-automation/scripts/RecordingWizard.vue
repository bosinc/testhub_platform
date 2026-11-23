<template>
  <el-dialog
    v-model="dialogVisible"
    :title="$t('uiAutomation.recording.title')"
    width="820px"
    class="recording-dialog"
    :close-on-click-modal="false"
    destroy-on-close
    @closed="reset"
  >
    <el-steps :active="step" align-center class="recording-steps">
      <el-step :title="$t('uiAutomation.recording.steps.configure')" />
      <el-step :title="$t('uiAutomation.recording.steps.record')" />
      <el-step :title="$t('uiAutomation.recording.steps.import')" />
    </el-steps>

    <div v-if="step === 0" class="wizard-panel page-container">
      <el-form ref="configFormRef" :model="form" :rules="rules" label-width="112px">
        <el-form-item :label="$t('uiAutomation.recording.startUrl')" prop="start_url">
          <el-input v-model="form.start_url" placeholder="https://example.com/login" />
        </el-form-item>
        <el-form-item :label="$t('uiAutomation.recording.scriptName')">
          <el-input v-model="form.name" :placeholder="$t('uiAutomation.recording.scriptNamePlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('uiAutomation.recording.language')">
          <el-radio-group v-model="form.language">
            <el-radio value="python">Python <span class="recommended">({{ $t('uiAutomation.recording.recommended') }})</span></el-radio>
            <el-radio value="javascript" disabled>JavaScript <span class="unsupported">({{ $t('uiAutomation.recording.javascriptUnsupported') }})</span></el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="$t('uiAutomation.recording.browser')">
          <el-select v-model="form.browser" style="width: 220px">
            <el-option label="Chromium / Chrome" value="chromium" />
          </el-select>
          <span class="field-hint">{{ $t('uiAutomation.recording.browserHint') }}</span>
        </el-form-item>
        <el-form-item :label="$t('uiAutomation.recording.device')">
          <el-input v-model="form.device" :placeholder="$t('uiAutomation.recording.devicePlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('uiAutomation.recording.storageState')">
          <el-switch v-model="form.save_storage" />
          <span class="field-hint">{{ $t('uiAutomation.recording.storageStateHint') }}</span>
        </el-form-item>
      </el-form>
    </div>

    <div v-else-if="step === 1" class="wizard-panel record-panel">
      <el-alert :title="$t('uiAutomation.recording.localTip')" type="info" :closable="false" show-icon />
      <div class="command-label">{{ $t('uiAutomation.recording.installCommand') }}</div>
      <div class="command-box">
        <code>{{ command.install_command }}</code>
        <el-button text type="primary" class="copy-button" @click="copy(command.install_command)">
          <el-icon><DocumentCopy /></el-icon>{{ $t('uiAutomation.recording.copy') }}
        </el-button>
      </div>
      <div class="command-label">{{ $t('uiAutomation.recording.recordCommand') }}</div>
      <div class="command-box">
        <code>{{ command.record_command }}</code>
        <el-button text type="primary" class="copy-button" @click="copy(command.record_command)">
          <el-icon><DocumentCopy /></el-icon>{{ $t('uiAutomation.recording.copy') }}
        </el-button>
      </div>
      <el-alert :title="$t('uiAutomation.recording.securityTip')" type="warning" :closable="false" show-icon />
    </div>

    <div v-else class="wizard-panel import-panel">
      <el-tabs v-model="importMode">
        <el-tab-pane :label="$t('uiAutomation.recording.uploadFile')" name="file">
          <el-upload
            class="upload-area"
            drag
            :auto-upload="false"
            :show-file-list="false"
            accept=".py"
            @change="handleFileChange"
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="upload-title">{{ $t('uiAutomation.recording.uploadTitle') }}</div>
            <div class="upload-tip">{{ $t('uiAutomation.recording.uploadHint') }}</div>
          </el-upload>
          <div v-if="fileName" class="selected-file"><el-icon><Document /></el-icon>{{ fileName }}</div>
        </el-tab-pane>
        <el-tab-pane :label="$t('uiAutomation.recording.pasteCode')" name="paste">
          <el-input
            v-model="content"
            type="textarea"
            :rows="12"
            class="code-input"
            placeholder="from playwright.sync_api import Playwright, sync_playwright"
          />
        </el-tab-pane>
      </el-tabs>
      <el-alert :title="$t('uiAutomation.recording.reimportHint')" type="info" :closable="false" show-icon />
      <el-alert v-if="analysisError" :title="analysisError" type="error" :closable="false" show-icon />
    </div>

    <template #footer>
      <el-button @click="close">{{ $t('uiAutomation.common.cancel') }}</el-button>
      <el-button v-if="step > 0" @click="step -= 1">{{ $t('uiAutomation.recording.previous') }}</el-button>
      <el-button v-if="step < 2" type="primary" :loading="loading" @click="next">
        {{ step === 0 ? $t('uiAutomation.recording.generateCommand') : $t('uiAutomation.recording.nextImport') }}
      </el-button>
      <el-button v-else type="primary" :loading="loading" @click="importScript">
        {{ $t('uiAutomation.recording.importToEditor') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Document, DocumentCopy, UploadFilled } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { getRecordingCommand, importRecordedScript } from '@/api/ui_automation'

const props = defineProps({
  visible: { type: Boolean, default: false },
  projectId: { type: [String, Number], default: '' }
})
const emit = defineEmits(['update:visible', 'imported'])
const { t } = useI18n()

const step = ref(0)
const loading = ref(false)
const importMode = ref('file')
const fileName = ref('')
const content = ref('')
const analysisError = ref('')
const configFormRef = ref(null)
const command = reactive({ install_command: '', record_command: '' })
const form = reactive({ start_url: '', name: '', language: 'python', browser: 'chromium', device: '', save_storage: false })
const dialogVisible = computed({
  get: () => props.visible,
  set: value => emit('update:visible', value)
})
const rules = {
  start_url: [{ required: true, message: t('uiAutomation.recording.urlRequired'), trigger: 'blur' }, { type: 'url', message: t('uiAutomation.recording.urlInvalid'), trigger: 'blur' }]
}

watch(() => props.visible, value => { if (value) reset() })

const next = async () => {
  if (step.value === 0) {
    const valid = await configFormRef.value?.validate().catch(() => false)
    if (!valid) return
    loading.value = true
    try {
      const response = await getRecordingCommand({ ...form, output_name: 'testhub_recorded.py' })
      Object.assign(command, response.data)
      step.value = 1
    } catch (error) {
      ElMessage.error(error.response?.data?.error || t('uiAutomation.recording.commandFailed'))
    } finally { loading.value = false }
  } else {
    step.value = 2
  }
}

const handleFileChange = uploadFile => {
  const file = uploadFile.raw
  if (!file) return
  if (file.size > 2 * 1024 * 1024) {
    analysisError.value = t('uiAutomation.recording.fileTooLarge')
    return
  }
  fileName.value = file.name
  const reader = new FileReader()
  reader.onload = event => { content.value = String(event.target?.result || ''); analysisError.value = '' }
  reader.onerror = () => { analysisError.value = t('uiAutomation.recording.fileReadFailed') }
  reader.readAsText(file, 'utf-8')
}

const importScript = async () => {
  if (!content.value.trim()) {
    analysisError.value = t('uiAutomation.recording.contentRequired')
    return
  }
  loading.value = true
  analysisError.value = ''
  try {
    const response = await importRecordedScript({
      project: props.projectId,
      content: content.value,
      name: form.name || fileName.value || 'testhub_recorded.py',
      start_url: form.start_url,
      browser: form.browser,
      device: form.device,
      save_storage: form.save_storage
    })
    emit('imported', response.data)
    close()
    ElMessage.success(t('uiAutomation.recording.importSuccess'))
  } catch (error) {
    analysisError.value = error.response?.data?.error || t('uiAutomation.recording.importFailed')
  } finally { loading.value = false }
}

const copy = async value => {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
    } else {
      const input = document.createElement('textarea')
      input.value = value
      input.style.position = 'fixed'
      input.style.opacity = '0'
      document.body.appendChild(input)
      input.select()
      document.execCommand('copy')
      input.remove()
    }
    ElMessage.success(t('uiAutomation.recording.copied'))
  } catch {
    ElMessage.error(t('uiAutomation.recording.copyFailed'))
  }
}

const close = () => { dialogVisible.value = false }
const reset = () => {
  step.value = 0
  loading.value = false
  importMode.value = 'file'
  fileName.value = ''
  content.value = ''
  analysisError.value = ''
  Object.assign(command, { install_command: '', record_command: '' })
  Object.assign(form, { start_url: '', name: '', language: 'python', browser: 'chromium', device: '', save_storage: false })
}
</script>

<style scoped>
.recording-steps { margin: 2px 18px 28px; }
.wizard-panel { padding: 4px 18px 10px; min-height: 300px; }
.field-hint { margin-left: 12px; color: #9aa0aa; font-size: 12px; }
.recommended { color: var(--el-color-primary); }
.unsupported { color: #9aa0aa; font-size: 12px; }
.record-panel { padding-top: 0; }
.command-label { margin: 20px 0 8px; color: #58606d; font-size: 13px; }
.command-box { position: relative; display: flex; align-items: center; min-height: 54px; padding: 13px 112px 13px 16px; border-radius: 6px; background: #1e1e1e; color: #d4d4d4; border: 1px solid #333; overflow-wrap: anywhere; }
.command-box code { white-space: pre-wrap; font-family: Monaco, Consolas, monospace; font-size: 13px; line-height: 1.6; }
.copy-button { position: absolute; right: 10px; }
.upload-area :deep(.el-upload-dragger) { width: 100%; min-height: 220px; border: 1px dashed var(--el-border-color); background: var(--el-bg-color); }
.upload-icon { margin-top: 42px; font-size: 46px; color: var(--el-text-color-secondary); }
.upload-title { margin-top: 12px; color: var(--el-color-primary); font-size: 20px; font-weight: 600; }
.upload-tip { margin-top: 8px; color: var(--el-text-color-secondary); }
.selected-file { display: inline-flex; align-items: center; gap: 8px; margin-top: 10px; color: var(--el-text-color-primary); }
.code-input :deep(textarea) { font-family: Monaco, Consolas, monospace; font-size: 13px; }
</style>
