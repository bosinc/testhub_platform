<template>
  <div class="scenario-ref-editor">
    <div class="editor-title">{{ $t('apiTesting.scenarioEditor.scenarioRefTitle') }}</div>

    <el-form label-width="110px" class="ref-form">
      <el-form-item :label="$t('apiTesting.scenarioEditor.targetScenario')">
        <el-select
          :model-value="node.source_id"
          filterable
          :placeholder="$t('apiTesting.scenarioEditor.selectScenario')"
          @update:model-value="changeTarget"
        >
          <el-option
            v-for="item in scenarioOptions"
            :key="item.id"
            :label="item.name"
            :value="String(item.id)"
          />
        </el-select>
      </el-form-item>
    </el-form>

    <MappingRows
      :title="$t('apiTesting.scenarioEditor.inputMapping')"
      :hint="$t('apiTesting.scenarioEditor.inputMappingHint')"
      :model-value="node.config.input_mapping || {}"
      @update:model-value="patch({ config: { ...node.config, input_mapping: $event } })"
    />
    <MappingRows
      :title="$t('apiTesting.scenarioEditor.outputMapping')"
      :hint="$t('apiTesting.scenarioEditor.outputMappingHint')"
      :model-value="node.config.output_mapping || {}"
      @update:model-value="patch({ config: { ...node.config, output_mapping: $event } })"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import MappingRows from './MappingRows.vue'

const props = defineProps({
  node: { type: Object, required: true },
  scenarios: { type: Array, default: () => [] }
})
const emit = defineEmits(['patch'])

const scenarioOptions = computed(() =>
  props.scenarios.filter(item => String(item.id) !== String(props.node.config.__self_id || '')))

function patch(fields) {
  emit('patch', fields)
}

function changeTarget(value) {
  const target = props.scenarios.find(item => String(item.id) === String(value))
  patch({
    source_id: value ? String(value) : null,
    source_type: value ? 'scenario_case' : null,
    config: {
      ...props.node.config,
      source_meta: target ? { name: target.name } : undefined
    }
  })
}
</script>

<style scoped>
.editor-title { font-size: 14px; font-weight: 600; color: #303133; margin-bottom: 14px; }
.ref-form { max-width: 620px; }
</style>
