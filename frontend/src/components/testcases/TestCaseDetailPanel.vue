<template>
  <div class="test-case-detail-panel">
    <div class="case-title-row">
      <div class="case-heading">
        <div class="case-id">{{ $t('execution.testcaseId') }} {{ testCase.id ?? '-' }}</div>
        <h2>{{ testCase.title || '-' }}</h2>
      </div>
      <!-- 编辑、历史等业务操作由使用方提供，详情组件只负责展示。 -->
      <div v-if="$slots.actions" class="case-title-actions">
        <slot name="actions" />
      </div>
    </div>
    <div class="case-meta-grid">
      <div><span>{{ $t('execution.testcasePriority') }}</span>{{ priorityText }}</div>
      <div><span>{{ $t('execution.testcaseType') }}</span>{{ testTypeText }}</div>
      <div><span>{{ $t('execution.maintainer') }}</span>{{ testCase.author?.username || '-' }}</div>
      <div><span>{{ $t('execution.project') }}</span>{{ testCase.project_name || testCase.project?.name || '-' }}</div>
    </div>
    <div class="case-section">
      <h3>{{ $t('execution.preconditions') }}</h3>
      <p>{{ displayText(testCase.preconditions) }}</p>
    </div>
    <div class="case-section">
      <h3>{{ $t('execution.steps') }}</h3>
      <TestCaseStepEditor v-if="testCase.steps?.length" :model-value="testCase.steps" readonly />
      <p v-else>{{ $t('execution.noData') }}</p>
    </div>
    <div class="case-section">
      <h3>{{ $t('execution.testcaseNotes') }}</h3>
      <div v-if="safeNotes" class="notes-content" v-html="safeNotes"></div>
      <p v-else>{{ $t('execution.noData') }}</p>
    </div>
    <div v-if="testCase.description" class="case-section">
      <h3>{{ $t('execution.testcaseDescription') }}</h3>
      <p>{{ displayText(testCase.description) }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import TestCaseStepEditor from '@/components/testcases/TestCaseStepEditor.vue'

const props = defineProps({
  testCase: { type: Object, required: true }
})
const { t } = useI18n()

const priorityKeys = {
  low: 'priorityLow', medium: 'priorityMedium', high: 'priorityHigh', critical: 'priorityCritical'
}
const testTypeKeys = {
  functional: 'typeFunctional', integration: 'typeIntegration', api: 'typeApi',
  ui: 'typeUi', performance: 'typePerformance', security: 'typeSecurity'
}
const priorityText = computed(() => priorityKeys[props.testCase.priority]
  ? t(`execution.${priorityKeys[props.testCase.priority]}`) : '-')
const testTypeText = computed(() => testTypeKeys[props.testCase.test_type]
  ? t(`execution.${testTypeKeys[props.testCase.test_type]}`) : '-')

// 备注支持富文本图片，先限制可渲染标签和图片来源，避免直接渲染任意 HTML。
const safeNotes = computed(() => {
  const parsed = new DOMParser().parseFromString(props.testCase.notes || '', 'text/html')
  const allowedTags = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'UL', 'OL', 'LI', 'P', 'H2', 'H3', 'BR', 'FONT', 'IMG'])
  const cleanNode = node => {
    for (const child of [...node.childNodes]) {
      if (child.nodeType !== Node.ELEMENT_NODE) {
        if (child.nodeType !== Node.TEXT_NODE) child.remove()
        continue
      }
      if (!allowedTags.has(child.tagName)) {
        cleanNode(child)
        child.replaceWith(...child.childNodes)
        continue
      }
      [...child.attributes].forEach(attribute => {
        const isImageSource = child.tagName === 'IMG' && attribute.name === 'src' && (
          /^data:image\/(png|jpeg|gif|webp);base64,/i.test(attribute.value) || /^\/media\//.test(attribute.value)
        )
        const isFontSize = child.tagName === 'FONT' && attribute.name === 'size' && /^[1-7]$/.test(attribute.value)
        if (!isImageSource && !isFontSize) child.removeAttribute(attribute.name)
      })
      if (child.tagName === 'IMG' && !child.hasAttribute('src')) child.remove()
      else cleanNode(child)
    }
  }
  cleanNode(parsed.body)
  return parsed.body.innerHTML
})

// 历史用例用 <br> 保存换行；转换为纯文本展示，避免直接渲染用例中的 HTML。
const displayText = value => value ? String(value).replace(/<br\s*\/?>/gi, '\n') : t('execution.noData')
</script>

<style scoped>
.test-case-detail-panel,
.case-heading {
  min-width: 0;
}

.case-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--el-border-color-light);
}

.case-id {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.case-title-row h2 {
  margin: 5px 0 0;
  color: var(--el-text-color-primary);
  font-size: 19px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.case-title-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
  white-space: nowrap;
}

.case-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
  padding: 16px 0 20px;
  border-bottom: 1px solid var(--el-border-color-light);
}

.case-meta-grid div {
  display: flex;
  gap: 8px;
  min-width: 0;
  color: var(--el-text-color-primary);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.case-meta-grid span {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
}

.case-section {
  padding-top: 18px;
}

.case-section h3 {
  margin: 0 0 9px;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
}

.case-section p {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.notes-content {
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.notes-content :deep(img) {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 8px 0;
}

@media (max-width: 768px) {
  .case-title-row {
    flex-direction: column;
  }

  .case-meta-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
