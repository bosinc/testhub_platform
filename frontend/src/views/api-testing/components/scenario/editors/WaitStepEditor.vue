<template>
  <div class="wait-step-editor">
    <div class="wait-row">
      <div class="editor-title">{{ $t('apiTesting.scenarioEditor.waitTitle') }}</div>
      <div class="duration-control">
        <span class="duration-label">{{ $t('apiTesting.scenarioEditor.durationMs') }}</span>
        <el-input-number
          :model-value="node.config.duration_ms ?? 1000"
          :min="0"
          :max="300000"
          :step="100"
          @update:model-value="patch({ config: { ...node.config, duration_ms: $event } })"
        />
      </div>
    </div>
    <div class="editor-hint">{{ $t('apiTesting.scenarioEditor.waitHint') }}</div>
  </div>
</template>

<script setup>
defineProps({ node: { type: Object, required: true } })
const emit = defineEmits(['patch'])

function patch(fields) {
  emit('patch', fields)
}
</script>

<style scoped>
.wait-row { display: flex; align-items: center; gap: 18px; }
.editor-title { flex: 0 0 auto; font-size: 14px; font-weight: 600; color: #303133; }
.duration-control { display: flex; align-items: center; gap: 12px; }
.duration-label { color: #606266; font-size: 14px; white-space: nowrap; }
.editor-hint { font-size: 12px; color: #909399; }
</style>
