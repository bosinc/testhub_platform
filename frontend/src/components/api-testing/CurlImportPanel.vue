<template>
  <div class="curl-import-panel">
    <el-input
      v-model="command"
      type="textarea"
      :rows="rows"
      spellcheck="false"
      :placeholder="placeholder"
    />
    <div v-if="preview" class="curl-preview">
      <p>{{ $t('apiTesting.requestEditor.hostSuggestion') }} <code>{{ preview.model.baseURL || '' }}</code></p>
      <p>{{ $t('apiTesting.requestEditor.importPath') }} <code>{{ preview.draft.url }}</code></p>
      <template v-if="showDetails">
        <p>{{ $t('apiTesting.requestEditor.curlPreviewHint') }}</p>
        <pre>{{ JSON.stringify(preview.draft, null, 2) }}</pre>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { RequestModelParser } from '@/utils/requestModel'
import { curlModelToDraft } from '@/utils/requestDraft'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  rows: { type: Number, default: 12 },
  // 是否展示完整 DTO 预览（接口管理使用，场景步骤只展示必要信息）
  showDetails: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue', 'parsed', 'error'])
const { t } = useI18n()

const command = ref(props.modelValue)
const preview = ref(null)

watch(() => props.modelValue, value => {
  if (value !== command.value) command.value = value
})
watch(command, value => emit('update:modelValue', value))

async function parse() {
  if (!command.value.trim()) {
    ElMessage.warning(t('apiTesting.interface.curlRequired'))
    return null
  }
  try {
    const model = await RequestModelParser.parseCurl(command.value)
    preview.value = { model, draft: curlModelToDraft(model) }
    emit('parsed', preview.value)
    return preview.value
  } catch (error) {
    preview.value = null
    emit('error', error)
    ElMessage.error(t('apiTesting.interface.curlParseFailed'))
    return null
  }
}

function reset() {
  command.value = ''
  preview.value = null
}

defineExpose({ parse, reset, preview })
</script>

<style scoped>
.curl-preview { margin-top: 12px; font-size: 12px; color: #606266; }
.curl-preview code { color: var(--el-color-primary); }
.curl-preview pre { max-height: 280px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
