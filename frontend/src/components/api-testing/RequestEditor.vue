<template>
  <div class="request-editor">
    <div class="request-line">
      <slot name="environment" />
      <el-select v-if="allowedProtocols.length > 1" v-model="draft.request_type" class="protocol-select">
        <el-option v-for="protocol in allowedProtocols" :key="protocol" :label="protocol" :value="protocol" />
      </el-select>
      <div class="request-target">
        <el-select v-if="draft.request_type !== 'WEBSOCKET'" v-model="draft.method" class="method-select" :class="draft.method.toLowerCase()">
          <el-option v-for="method in methods" :key="method" :label="method" :value="method" />
        </el-select>
        <el-input v-model="draft.url" :placeholder="$t('apiTesting.requestEditor.pathPlaceholder', { id: '{id}' })" class="url-input" />
      </div>
      <el-button v-if="draft.request_type !== 'WEBSOCKET'" type="primary" :loading="busy" @click="emit('send')">
        {{ $t('apiTesting.interface.send') }}
      </el-button>
      <el-button v-else :loading="wsStatus === 'connecting'" :disabled="busy" @click="emit(wsStatus === 'disconnected' ? 'wsConnect' : 'wsDisconnect')">
        {{ $t(wsStatus === 'disconnected' ? 'apiTesting.interface.connect' : wsStatus === 'connecting' ? 'apiTesting.interface.connecting' : 'apiTesting.interface.disconnect') }}
      </el-button>
    </div>
    <div class="url-preview">
      <strong>{{ $t('apiTesting.requestEditor.preview') }}</strong>
      <code>{{ estimatedUrl || $t('apiTesting.requestEditor.environmentRequired') }}</code>
      <el-button size="small" :disabled="busy" @click="emit('preview')">{{ $t('apiTesting.requestEditor.confirmPreview') }}</el-button>
      <div class="preview-note">{{ $t('apiTesting.requestEditor.previewHint') }}</div>
    </div>
    <slot name="meta" />
    <el-tabs v-model="activeTab" class="request-tabs">
      <el-tab-pane label="Params" name="params">
        <KeyValueEditor :show-type="true" v-model="draft.params" :placeholder-key="$t('apiTesting.interface.paramName')" :placeholder-value="$t('apiTesting.interface.paramValue')" />
        <div class="path-parameters-panel">
          <div class="path-parameters-title">Path Params</div>
          <KeyValueEditor :show-type="true" v-model="draft.path_params" :placeholder-key="$t('apiTesting.interface.paramName')" :placeholder-value="$t('apiTesting.interface.paramValue')" />
        </div>
      </el-tab-pane>
      <el-tab-pane label="Headers" name="headers">
        <KeyValueEditor v-model="draft.headers" :placeholder-key="$t('apiTesting.interface.headerName')" :placeholder-value="$t('apiTesting.interface.headerValue')" />
      </el-tab-pane>
      <el-tab-pane label="Body" name="body" v-if="draft.request_type !== 'WEBSOCKET'">
        <div class="body-container">
          <el-radio-group :model-value="draft.body.type" @update:model-value="changeBodyType">
            <el-radio v-for="type in BODY_TYPES" :key="type" :value="type">{{ type }}</el-radio>
          </el-radio-group>
          <div class="body-content" v-if="['form-data', 'x-www-form-urlencoded'].includes(draft.body.type)">
            <KeyValueEditor v-model="draft.body.data" :show-file="draft.body.type === 'form-data'" />
          </div>
          <div class="body-content" v-else-if="draft.body.type === 'binary'">
            <el-upload :auto-upload="false" :show-file-list="false" :on-change="uploadBinary"><el-button>{{ $t('apiTesting.component.keyValueEditor.selectFile') }}</el-button></el-upload>
            <span>{{ draft.body.data?.filename }}</span>
            <el-button size="small" @click="draft.body = { type: 'binary', data: null }">{{ $t('apiTesting.interface.clear') }}</el-button>
          </div>
          <div class="body-content" v-else>
            <div class="raw-options">
              <el-button size="small" :icon="MagicStick" @click="emit('data-factory', { field: 'rawBody' })">{{ $t('apiTesting.interface.referDataFactory') }}</el-button>
              <el-button size="small" :icon="MagicStick" @click="emit('variable-helper', { field: 'rawBody' })">{{ $t('apiTesting.interface.variableHelper') }}</el-button>
            </div>
            <el-input ref="rawBodyInputRef" :model-value="bodyText" @update:model-value="draft.body.data = $event" type="textarea" :rows="10" :placeholder="$t('apiTesting.interface.inputRequestBody')" class="raw-body" />
          </div>
        </div>
      </el-tab-pane>
      <el-tab-pane label="Auth" name="auth">
        <el-form label-width="120px" class="auth-settings">
          <el-form-item label="Auth"><el-select v-model="draft.auth.type"><el-option v-for="type in ['none', 'basic', 'bearer', 'api_key']" :key="type" :label="type" :value="type" /></el-select></el-form-item>
          <template v-if="draft.auth.type === 'basic'">
            <el-form-item :label="$t('apiTesting.requestEditor.username')"><el-input v-model="draft.auth.username" /></el-form-item>
            <el-form-item :label="$t('apiTesting.requestEditor.password')"><el-input v-model="draft.auth.password" show-password /></el-form-item>
          </template>
          <el-form-item v-if="draft.auth.type === 'bearer'" label="Token"><el-input v-model="draft.auth.token" show-password /></el-form-item>
          <template v-if="draft.auth.type === 'api_key'">
            <el-form-item label="Key"><el-input v-model="draft.auth.key" /></el-form-item>
            <el-form-item :label="$t('apiTesting.interface.value')"><el-input v-model="draft.auth.value" show-password /></el-form-item>
            <el-form-item :label="$t('apiTesting.requestEditor.addTo')"><el-select v-model="draft.auth.addTo"><el-option label="Header" value="header" /><el-option label="Query" value="query" /></el-select></el-form-item>
          </template>
        </el-form>
      </el-tab-pane>
      <template v-if="draft.request_type !== 'WEBSOCKET'">
        <el-tab-pane v-for="script in scripts" :key="script.field" :label="script.label" :name="script.field">
          <div class="script-editor-container">
            <el-input v-model="draft[script.field]" type="textarea" :rows="10" :placeholder="$t(script.placeholder)" />
            <div class="script-buttons">
              <el-button size="small" :icon="MagicStick" @click="emit('data-factory', { field: script.field })">{{ $t('apiTesting.interface.referDataFactory') }}</el-button>
              <el-button size="small" :icon="MagicStick" @click="emit('variable-helper', { field: script.field })">{{ $t('apiTesting.interface.variableHelper') }}</el-button>
            </div>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="$t('apiTesting.interface.assertions')" name="assertions">
          <AssertionEditor v-model="draft.assertions" :show-helpers="true" @data-factory="helper('data-factory', $event)" @variable-helper="helper('variable-helper', $event)" />
        </el-tab-pane>
        <el-tab-pane :label="$t('apiTesting.interface.variableExtraction')" name="extractors"><VariableExtractorEditor v-model="draft.extractors" :results="extractionResults" /></el-tab-pane>
        <el-tab-pane label="Settings" name="settings">
          <el-form label-width="180px" class="auth-settings">
            <el-form-item :label="$t('apiTesting.requestEditor.timeout')"><el-input-number :model-value="draft.settings.timeout ?? 30" @update:model-value="draft.settings.timeout = $event" :min="0.1" :max="300" /></el-form-item>
            <el-form-item :label="$t('apiTesting.requestEditor.redirects')"><el-switch :model-value="draft.settings.follow_redirects ?? true" @update:model-value="draft.settings.follow_redirects = $event" /></el-form-item>
            <el-form-item :label="$t('apiTesting.requestEditor.verifySsl')"><el-switch :model-value="draft.settings.verify_ssl ?? true" @update:model-value="draft.settings.verify_ssl = $event" /></el-form-item>
          </el-form>
        </el-tab-pane>
      </template>
      <el-tab-pane v-else label="Message" name="message"><slot name="ws-message" :send="() => emit('wsSend')" /></el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { MagicStick } from '@element-plus/icons-vue'
import KeyValueEditor from '@/views/api-testing/components/KeyValueEditor.vue'
import AssertionEditor from './AssertionEditor.vue'
import VariableExtractorEditor from './VariableExtractorEditor.vue'
import { BODY_TYPES, cloneDraft, createRequestDraft, previewRequestUrl, serializeFile } from '@/utils/requestDraft'

const props = defineProps({
  modelValue: { type: Object, required: true }, environment: { type: Object, default: null },
  extractionResults: { type: Array, default: () => [] }, busy: { type: Boolean, default: false },
  allowedProtocols: { type: Array, default: () => ['HTTP', 'WEBSOCKET'] },
  wsStatus: { type: String, default: 'disconnected' }
})
const emit = defineEmits(['update:modelValue', 'send', 'wsConnect', 'wsDisconnect', 'wsSend', 'preview', 'data-factory', 'variable-helper'])
const { t } = useI18n()
const draft = ref(createRequestDraft(props.modelValue))
const activeTab = ref('params')
const rawBodyInputRef = ref(null)
// Keep the editor list identical to the backend DTO allow-list.
const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const scripts = [
  { field: 'pre_request_script', label: 'Pre-request Script', placeholder: 'apiTesting.interface.scriptBeforePlaceholder' },
  { field: 'post_request_script', label: 'Tests', placeholder: 'apiTesting.interface.scriptAfterPlaceholder' }
]
let syncing = false
let unmounted = false
onBeforeUnmount(() => { unmounted = true })
watch(() => props.modelValue, value => {
  const next = createRequestDraft(value)
  if (JSON.stringify(next) === JSON.stringify(draft.value)) return
  syncing = true
  draft.value = next
  syncing = false
}, { deep: true, flush: 'sync' })
// Children only receive this detached draft; even legacy nested v-model never mutates props.
watch(draft, value => {
  if (!syncing) emit('update:modelValue', cloneDraft(value))
}, { deep: true, flush: 'sync' })
watch(() => draft.value.url, url => {
  if (syncing) return
  const names = [...new Set(Array.from(String(url).matchAll(/(?<!\{)\{([^{}]+)\}(?!\})/g), match => match[1]))]
  const missing = names.filter(key => !draft.value.path_params.some(row => row.key === key))
  if (missing.length) draft.value.path_params = [...draft.value.path_params, ...missing.map(key => ({ key, value: '', enabled: true, type: 'text', description: '' }))]
}, { flush: 'sync' })
const estimatedUrl = computed(() => previewRequestUrl(draft.value, props.environment))
const bodyText = computed(() => typeof draft.value.body.data === 'string' ? draft.value.body.data : JSON.stringify(draft.value.body.data, null, 2))
function changeBodyType(type) {
  draft.value.body = { type, data: ['form-data', 'x-www-form-urlencoded'].includes(type) ? [] : type === 'binary' ? null : '' }
}
function helper(event, { field, index }) { emit(event, { field, index, target: 'assertion' }) }
async function uploadBinary(upload) {
  const current = draft.value
  const body = current.body
  try {
    const value = await serializeFile(upload.raw)
    if (!unmounted && draft.value === current && current.body === body) body.data = value
  } catch {
    ElMessage.error(t('apiTesting.requestEditor.fileReadFailed'))
  }
}
function insertText(field, text) {
  if (field !== 'rawBody') { draft.value[field] = `${draft.value[field] || ''}${text}`; return }
  const textarea = rawBodyInputRef.value?.textarea || rawBodyInputRef.value?.$el?.querySelector('textarea')
  const value = bodyText.value || ''
  const start = textarea?.selectionStart ?? value.length
  const end = textarea?.selectionEnd ?? value.length
  draft.value.body.data = value.slice(0, start) + text + value.slice(end)
  nextTick(() => { textarea?.setSelectionRange(start + text.length, start + text.length); textarea?.focus() })
}
defineExpose({ insertText })
</script>

<style scoped>
.request-editor { padding: 20px; background: white; border: 1px solid #e9ecef; border-radius: 12px; min-width: 0; }
.request-line, .request-target, .raw-options, .script-buttons { display: flex; align-items: center; gap: 8px; }
.request-line { flex-wrap: wrap; }
.request-target { flex: 1; min-width: 240px; gap: 0; }
.protocol-select { width: 140px; }
.method-select { width: 110px; flex: 0 0 110px; }
.method-select.get { --method-color: #61affe; }
.method-select.post { --method-color: #49cc90; }
.method-select.put { --method-color: #fca130; }
.method-select.delete { --method-color: #f93e3e; }
.method-select.patch { --method-color: #50e3c2; }
.method-select.head { --method-color: #9013fe; }
.method-select.options { --method-color: #0ebeff; }
.method-select.connect { --method-color: #7f8c8d; }
.method-select.trace { --method-color: #e67e22; }
.method-select :deep(.el-select__selected-item), .method-select :deep(.el-input__inner) { color: var(--method-color); }
.request-target .method-select :deep(.el-input__wrapper) { border-radius: 6px 0 0 6px; }
.request-target .url-input :deep(.el-input__wrapper) { border-radius: 0 6px 6px 0; }
.request-tabs :deep(.el-tabs__item) { height: 36px; font-size: 13px; }
.request-tabs :deep(.el-tabs__item.is-active) { color: #6366f1; }
@media (max-width: 768px) { .request-target { min-width: 0; width: 100%; flex-basis: 100%; } .raw-options, .script-buttons { flex-wrap: wrap; } }
.url-input { flex: 1; min-width: 0; }
.url-preview { margin: 12px 0; overflow-wrap: anywhere; }
.url-preview code { margin: 0 8px; }
.preview-note { color: #909399; font-size: 12px; margin-top: 6px; }
.path-parameters-panel { margin-top: 16px; border-top: 1px solid #ebeef5; padding-top: 12px; }
.path-parameters-title { margin-bottom: 8px; font-weight: 600; }
.body-content, .script-buttons { margin-top: 12px; }
.raw-options { margin-bottom: 12px; }
.auth-settings { max-width: 640px; padding-top: 12px; }
</style>
