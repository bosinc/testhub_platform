<template>
  <div class="extractors-editor" :class="{ compact }">
    <div class="extractors-header">
      <span v-if="showTitle">{{ $t('apiTesting.interface.variableExtraction') }}</span>
      <el-button type="primary" @click="addExtractor">
        <el-icon><Plus /></el-icon>
        {{ $t('apiTesting.interface.addExtractor') }}
      </el-button>
    </div>

    <div v-if="extractors.length" class="extractors-list">
      <div v-for="(extractor, index) in extractors" :key="index" class="extractor-row">
        <el-input v-model="extractor.name" :placeholder="$t('apiTesting.interface.variableName')" />
        <el-select v-model="extractor.source" class="extractor-source">
          <el-option :label="$t('apiTesting.interface.responseBody')" value="body" />
        </el-select>
        <el-select v-model="extractor.type" class="extractor-type">
          <el-option label="JSONPath" value="json_path" />
        </el-select>
        <el-input
          v-model="extractor.expression"
          class="extractor-expression"
          :placeholder="$t('apiTesting.interface.jsonPathExtractorPlaceholder')"
        />
        <el-input
          v-model="extractor.default_value"
          class="extractor-default"
          :placeholder="$t('apiTesting.interface.defaultValue')"
        />
        <el-button
          link
          type="danger"
          :icon="Delete"
          @click.stop="removeExtractor(index)"
        />
      </div>
    </div>
    <el-empty
      v-else
      :description="$t('apiTesting.interface.noExtractors')"
      :image-size="64"
    />

    <div v-if="results?.length" class="extraction-results">
      <strong>{{ $t('apiTesting.interface.extractionResults') }}</strong>
      <div v-for="(result, index) in results" :key="`${result.name}-${index}`" class="extraction-result-row">
        <span>{{ result.name }}</span>
        <el-tag :type="result.success ? 'success' : 'danger'">
          {{ result.success ? formatValue(result.value) : result.error }}
        </el-tag>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Delete, Plus } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => []
  },
  results: {
    type: Array,
    default: () => []
  },
  compact: {
    type: Boolean,
    default: false
  },
  showTitle: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['update:modelValue'])
const extractors = computed({
  get: () => props.modelValue || [],
  set: value => emit('update:modelValue', value)
})

const addExtractor = () => {
  emit('update:modelValue', [
    ...extractors.value,
    { name: '', source: 'body', type: 'json_path', expression: '', default_value: '', enabled: true }
  ])
}

const removeExtractor = index => {
  emit('update:modelValue', extractors.value.filter((_, itemIndex) => itemIndex !== index))
}

const formatValue = value => (
  typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '')
)
</script>

<style scoped>
/* 与 KeyValueEditor 保持一致的紧凑分区：8px 内边距与间距。 */
.extractors-editor {
  background: white;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.extractors-editor.compact {
  border: 0;
  background: transparent;
}

.extractors-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid #e4e7ed;
  background: #fafbfc;
  font-weight: 600;
}

.extractors-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
}

.extractor-row {
  display: grid;
  grid-template-columns: minmax(90px, 1fr) 90px 96px minmax(150px, 1.7fr) minmax(96px, 1fr) 24px;
  gap: 8px;
  align-items: center;
}

.extractors-editor :deep(.el-empty) {
  padding: 8px 0;
}

.extractor-source,
.extractor-type,
.extractor-expression,
.extractor-default {
  width: 100%;
  min-width: 0;
}

.extractor-row :deep(.el-input),
.extractor-row :deep(.el-select) {
  min-width: 0;
}

.extraction-results {
  margin: 0 8px 8px;
  padding-top: 8px;
  border-top: 1px solid #e4e7ed;
}

.extraction-result-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  column-gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #ebeef5;
}

.extraction-result-row > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extraction-result-row :deep(.el-tag) {
  justify-self: end;
  max-width: min(70%, 420px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 900px) {
  .extractor-row {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
