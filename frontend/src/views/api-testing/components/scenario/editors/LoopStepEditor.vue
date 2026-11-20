<template>
  <div class="loop-step-editor">
    <div class="loop-section">
      <div class="section-label">
        {{ node.type === 'foreach' ? $t('apiTesting.scenarioEditor.arrayExpression') : $t('apiTesting.scenarioEditor.loopCount') }}
      </div>
      <el-input
        v-if="node.type === 'foreach'"
        :model-value="node.config.array_expression"
        :placeholder="$t('apiTesting.scenarioEditor.arrayExpressionPlaceholder')"
        @update:model-value="patchConfig({ array_expression: $event })"
      />
      <el-input
        v-else
        :model-value="node.config.count"
        :placeholder="$t('apiTesting.scenarioEditor.countPlaceholder')"
        @update:model-value="changeCount"
      />
      <div v-if="node.type !== 'foreach'" class="section-hint">
        {{ $t('apiTesting.scenarioEditor.forHint') }}
      </div>
    </div>

    <div v-for="conditionNode in breakIfNodes" :key="conditionNode.id" class="loop-card">
      <div class="loop-card-header">
          <span class="loop-card-title">
            <el-icon><WarningFilled /></el-icon>{{ $t('apiTesting.scenarioEditor.breakIf') }}
          </span>
          <span class="loop-card-actions">
            <el-button link :icon="Close" @click="removeBreakIf(conditionNode.id)" />
          </span>
      </div>
      <ConditionFields
        :model-value="conditionNode.config"
        @update:model-value="updateBreakIf(conditionNode.id, $event)"
      />
    </div>

    <div class="loop-card">
      <div class="loop-card-header">
        <span class="loop-card-title"><el-icon><CircleCloseFilled /></el-icon>{{ $t('apiTesting.scenarioEditor.onErrorTitle') }}</span>
      </div>
      <el-select
        :model-value="node.config.on_error || 'abort_scenario'"
        class="on-error-select"
        @update:model-value="patchConfig({ on_error: $event })"
      >
        <el-option :label="$t('apiTesting.scenarioEditor.onErrorContinue')" value="continue" />
        <el-option :label="$t('apiTesting.scenarioEditor.onErrorNextIteration')" value="next_iteration" />
        <el-option :label="$t('apiTesting.scenarioEditor.onErrorBreakLoop')" value="break_loop" />
        <el-option :label="$t('apiTesting.scenarioEditor.onErrorAbort')" value="abort_scenario" />
      </el-select>
    </div>

    <div class="loop-footer">
      <el-button @click="addBreakIf">
        <el-icon><Plus /></el-icon>{{ $t('apiTesting.scenarioEditor.breakIf') }}
      </el-button>
    </div>

    <div class="loop-limit">
      <span>{{ $t('apiTesting.scenarioEditor.maxIterations') }}</span>
      <el-input-number
        :model-value="node.config.max_iterations ?? 1000"
        :min="0"
        :max="1000"
        @update:model-value="patchConfig({ max_iterations: $event })"
      />
    </div>
    <div class="editor-hint">{{ $t('apiTesting.scenarioEditor.breakIfHint') }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Close, Plus, CircleCloseFilled, WarningFilled } from '@element-plus/icons-vue'
import ConditionFields from './ConditionFields.vue'

const props = defineProps({
  node: { type: Object, required: true },
  children: { type: Array, default: () => [] }
})
const emit = defineEmits(['patch', 'add-break-if', 'update-break-if', 'remove-break-if'])
const breakIfNodes = computed(() => props.children.filter(child => child.type === 'break_if'))

function patch(fields) {
  emit('patch', fields)
}

// 循环配置都在 node.config 内，统一合并后提交，避免写到错误的层级。
function patchConfig(fields) {
  patch({ config: { ...props.node.config, ...fields } })
}

function changeCount(value) {
  const text = String(value ?? '').trim()
  const numeric = Number(text)
  // Whole-number input becomes a fixed count; anything else is an expression.
  const isFixed = text !== '' && Number.isFinite(numeric) && String(numeric) === text
  patchConfig({ count: isFixed ? numeric : text })
}

function addBreakIf() {
  emit('add-break-if')
}

function updateBreakIf(id, config) {
  emit('update-break-if', { id, config })
}

function removeBreakIf(id) {
  emit('remove-break-if', id)
}
</script>

<style scoped>
.loop-step-editor { max-width: 720px; }
.loop-section { margin-bottom: 16px; }
.section-label { font-size: 13px; color: #606266; margin-bottom: 8px; }
.section-hint { margin-top: 6px; font-size: 12px; color: #909399; }
.loop-card {
  border: 1px solid #ebeef5; border-radius: 10px;
  padding: 10px 12px; margin-bottom: 12px; background: #fafbfd;
}
.loop-card-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px;
}
.loop-card-actions { display: inline-flex; align-items: center; }
.loop-card-title {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 13px; font-weight: 600; color: #e6a23c;
}
.on-error-select { width: 100%; }
.loop-footer { display: flex; gap: 8px; margin: 4px 0 16px; }
.loop-limit { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #606266; }
.editor-hint { margin-top: 10px; font-size: 12px; color: #909399; }
</style>
