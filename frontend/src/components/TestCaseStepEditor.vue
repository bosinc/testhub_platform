<template>
  <div class="step-editor" :class="{ 'is-readonly': readonly }">
    <div v-if="showTitle" class="step-editor__title">{{ $t('testcase.steps') }}</div>

    <div class="step-table">
      <div class="step-table__head">
        <span class="cell cell--index">{{ $t('testcase.stepNumber') }}</span>
        <span class="cell">{{ $t('testcase.stepDescription') }}</span>
        <span class="cell">{{ $t('testcase.expectedResult') }}</span>
        <span v-if="!readonly" class="cell cell--ops">{{ $t('common.operation') }}</span>
      </div>

      <draggable
        v-if="!readonly"
        v-model="rows"
        item-key="__key"
        handle=".drag-handle"
        :animation="160"
        class="step-table__body"
      >
        <template #item="{ element, index }">
          <div class="step-table__row">
            <span class="cell cell--index">{{ index + 1 }}</span>
            <div class="cell">
              <el-input
                v-model="element.action"
                type="textarea"
                :autosize="{ minRows: 1, maxRows: 10 }"
                resize="none"
                :maxlength="maxLength"
                :placeholder="$t('testcase.stepPlaceholder')"
              />
            </div>
            <div class="cell">
              <el-input
                v-model="element.expected"
                type="textarea"
                :autosize="{ minRows: 1, maxRows: 10 }"
                resize="none"
                :maxlength="maxLength"
                :placeholder="$t('testcase.stepPlaceholder')"
              />
            </div>
            <div class="cell cell--ops">
              <el-icon class="drag-handle" :title="$t('testcase.dragToSort')"><Rank /></el-icon>
              <el-button link type="danger" :title="$t('common.delete')" @click="removeRow(index)">
                <el-icon><Close /></el-icon>
              </el-button>
            </div>
          </div>
        </template>
      </draggable>

      <div v-else class="step-table__body">
        <div v-for="(row, index) in rows" :key="row.id || index" class="step-table__row">
          <span class="cell cell--index">{{ index + 1 }}</span>
          <div class="cell cell--text">{{ row.action || $t('testcase.stepEmpty') }}</div>
          <div class="cell cell--text">{{ row.expected || $t('testcase.stepEmpty') }}</div>
        </div>
      </div>

      <div v-if="!readonly" class="step-table__add" @click="addRow">
        <el-icon><Plus /></el-icon>
        <span>{{ $t('testcase.addStep') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue'
import draggable from 'vuedraggable'
import { Rank, Close, Plus } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
  showTitle: { type: Boolean, default: false },
  maxLength: { type: Number, default: 2000 }
})
const emit = defineEmits(['update:modelValue'])

let keySeed = 0
const createRow = (row = {}) => ({
  id: row.id ?? null,
  sort_order: row.sort_order ?? null,
  action: row.action || '',
  expected: row.expected || '',
  __key: row.__key || `step-${++keySeed}`
})

// 直接代理父级数组：单元格编辑与拖拽排序都写回同一份数据，避免多一层同步。
// 排序变化时同步刷新 sort_order，保证提交给后端的就是当前展示顺序。
const rows = computed({
  get: () => props.modelValue || [],
  set: value => {
    value.forEach((row, index) => { row.sort_order = index })
    emit('update:modelValue', value)
  }
})

// 拖拽需要稳定的 item-key，首次渲染时补齐（提交前会被 normalizeSteps 过滤掉）。
// 只读模式不渲染 draggable，也不需要 key，避免往父级数据里塞字段。
const ensureKeys = () => {
  if (props.readonly) return
  ;(props.modelValue || []).forEach(row => {
    if (!row.__key) row.__key = `step-${++keySeed}`
  })
}
watch(() => props.modelValue, ensureKeys, { immediate: true, deep: false })

const addRow = () => {
  rows.value = [...rows.value, createRow()]
}

const removeRow = index => {
  const next = rows.value.filter((_, i) => i !== index)
  rows.value = next.length ? next : [createRow()]
}

// 可编辑状态且没有任何步骤时，默认给出一行空白步骤，与设计稿一致。
onMounted(() => {
  if (!props.readonly && !(props.modelValue || []).length) {
    rows.value = [createRow()]
  }
})

defineExpose({ addRow })
</script>

<style scoped>
.step-editor {
  width: 100%;
}

.step-editor__title {
  margin-bottom: 8px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.step-table {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  overflow: hidden;
}

.step-table__head,
.step-table__row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) minmax(0, 1fr) 88px;
}

.is-readonly .step-table__head,
.is-readonly .step-table__row {
  grid-template-columns: 72px minmax(0, 1fr) minmax(0, 1fr);
}

.step-table__head {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 500;
}

.cell {
  padding: 8px 12px;
  min-width: 0;
}

.step-table__head .cell {
  border-right: 1px solid var(--el-border-color-lighter);
}

.step-table__head .cell:last-child {
  border-right: none;
}

.cell--index {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.step-table__row {
  border-top: 1px solid var(--el-border-color-lighter);
}

.step-table__row .cell {
  border-right: 1px solid var(--el-border-color-lighter);
}

.step-table__row .cell:last-child {
  border-right: none;
}

.cell--text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  color: var(--el-text-color-primary);
  min-height: 40px;
}

.cell--ops {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.drag-handle {
  cursor: grab;
  color: var(--el-text-color-secondary);
  font-size: 16px;
}

.drag-handle:active {
  cursor: grabbing;
}

.step-table__row :deep(.el-textarea__inner) {
  box-shadow: none;
  padding: 0;
  background: transparent;
  border: none;
  font-family: inherit;
  line-height: 1.6;
}

.step-table__row :deep(.el-textarea__inner:focus) {
  box-shadow: none;
}

.step-table__add {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
  color: var(--el-color-primary);
  font-size: 13px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.step-table__add:hover {
  background: var(--el-color-primary-light-9);
}
</style>
