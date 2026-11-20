<template>
  <div class="script-step-editor">
    <div class="editor-title">{{ $t('apiTesting.scenarioEditor.scriptTitle') }}</div>
    <el-alert
      class="runtime-alert"
      type="warning"
      :closable="false"
      show-icon
      :title="$t('apiTesting.scenarioEditor.scriptRuntimeWarning')"
    />
    <el-form label-width="110px" class="script-form">
      <el-form-item :label="$t('apiTesting.scenarioEditor.language')">
        <el-input model-value="javascript" disabled />
      </el-form-item>
      <el-form-item :label="$t('apiTesting.scenarioEditor.code')">
        <el-input
          :model-value="node.config.code"
          type="textarea"
          :rows="14"
          spellcheck="false"
          :placeholder="$t('apiTesting.scenarioEditor.codePlaceholder')"
          @update:model-value="patch({ config: { ...node.config, language: 'javascript', code: $event } })"
        />
      </el-form-item>
    </el-form>
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
.editor-title { font-size: 14px; font-weight: 600; color: #303133; margin-bottom: 14px; }
.runtime-alert { margin-bottom: 14px; }
.script-form { max-width: 760px; }
</style>
