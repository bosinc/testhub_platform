<template>
  <div class="http-step-editor">
    <div class="step-title-row">
      <span class="step-reference">#{{ node.reference_id }}</span>
      <el-input
        :model-value="node.name"
        class="name-input"
        maxlength="255"
        :placeholder="$t('apiTesting.scenarioEditor.httpNamePlaceholder')"
        @update:model-value="patch({ name: $event })"
      />
      <el-tag v-if="node.source_type === 'api_request'" size="small" type="warning">
        {{ $t('apiTesting.scenarioEditor.fromApiRequest', { id: node.source_id }) }}
      </el-tag>
      <el-button class="save-step-button" :loading="saving" @click="emit('save')">
        {{ $t('apiTesting.common.save') }}
      </el-button>
    </div>
    <!-- 与接口管理共用同一请求/响应组件：编辑之外还能直接调试该步骤 -->
    <RequestResponsePanel
      :model-value="request"
      :environment="environment"
      :response="runtimeState?.response"
      :busy="debugging"
      @update:model-value="patch({ config: { ...node.config, request: $event } })"
      @send="debugRequest"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import RequestResponsePanel from '@/components/api-testing/RequestResponsePanel.vue'
import { createRequestDraft, serializeStepRequest } from '@/utils/requestDraft'
import { debugScenarioStep } from '@/api/api-testing'

const props = defineProps({
  node: { type: Object, required: true },
  environment: { type: Object, default: null },
  scenarioId: { type: [Number, String], default: null },
  environmentId: { type: [Number, String], default: null },
  saving: { type: Boolean, default: false },
  runtimeState: { type: Object, default: null }
})
const emit = defineEmits(['patch', 'save', 'runtime-update'])
const { t, te } = useI18n()

const debugging = ref(false)

const request = computed(() => createRequestDraft(props.node.config?.request || {}))

function patch(fields) {
  emit('patch', fields)
}

async function debugRequest() {
  if (debugging.value || !props.scenarioId) return
  debugging.value = true
  try {
    const payload = await serializeStepRequest(request.value)
    const { data } = await debugScenarioStep(props.scenarioId, {
      request: payload,
      environment_id: props.environmentId || null
    })
    emit('runtime-update', {
      response: {
        ...data,
        // 某些调试响应不带请求快照，使用发送时的请求补齐响应页请求信息。
        request_data: data.request_data || payload
      },
      requestSnapshot: payload,
      sentAt: new Date().toISOString()
    })
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    debugging.value = false
  }
}

function errorMessage(error) {
  const data = error?.response?.data
  const raw = data?.errors ? flatten(data.errors) : data?.error || data?.detail || error?.message || ''
  if (!raw) return t('apiTesting.common.failed')
  return te(raw) ? t(raw) : String(raw)
}

function flatten(value) {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.join('；')
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => `${key}: ${flatten(item)}`).join('；')
  }
  return String(value)
}
</script>

<style scoped>
.step-title-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.step-reference {
  color: var(--el-text-color-secondary); font-size: 14px;
  text-align: left; white-space: nowrap;
}
.name-input { max-width: 360px; }
.save-step-button { margin-left: auto; flex: none; }
</style>
