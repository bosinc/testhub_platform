<template>
  <div class="condition-step-editor">
    <ConditionFields
      :model-value="node.config"
      @update:model-value="patch({ config: $event })"
    />
    <div class="else-action">
      <el-button
        class="else-button"
        size="small"
        :disabled="hasElse"
        @click="emit('add-else', node.id)"
      >
        {{ $t('apiTesting.scenarioEditor.addElse') }}
      </el-button>
    </div>
  </div>
</template>

<script setup>
import ConditionFields from './ConditionFields.vue'

defineProps({
  node: { type: Object, required: true },
  hasElse: { type: Boolean, default: false }
})
const emit = defineEmits(['patch', 'add-else'])

function patch(fields) {
  emit('patch', fields)
}
</script>

<style scoped>
.else-action { margin-top: 14px; }
.else-button { border-radius: 8px; }
</style>
