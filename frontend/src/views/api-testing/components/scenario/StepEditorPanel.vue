<template>
  <div class="step-editor-panel">
    <div v-if="node.type !== 'http'" class="panel-header">
      <span class="panel-reference">#{{ node.reference_id }}</span>
      <span class="panel-type" :class="node.type">{{ typeLabel }}</span>
      <span v-if="node.type === 'condition'" class="panel-title">{{ $t('apiTesting.scenarioEditor.conditionTitle') }}</span>
    </div>

    <HttpStepEditor
      v-if="node.type === 'http'"
      :node="node"
      :environment="environment"
      :scenario-id="scenarioId"
      :environment-id="environmentId"
      :saving="saving"
      :runtime-state="runtimeState"
      @patch="emit('patch', $event)"
      @save="emit('save')"
      @runtime-update="emit('runtime-update', $event)"
    />
    <ConditionStepEditor
      v-else-if="node.type === 'condition'"
      :node="node"
      :has-else="hasElse"
      @patch="emit('patch', $event)"
      @add-else="id => emit('add-else', id)"
    />
    <LoopStepEditor
      v-else-if="node.type === 'for' || node.type === 'foreach'"
      :node="node"
      :children="children"
      @patch="emit('patch', $event)"
      @add-break-if="emit('add-break-if')"
      @update-break-if="emit('update-break-if', $event)"
      @remove-break-if="emit('remove-break-if', $event)"
    />
    <div v-else-if="node.type === 'break_if'" class="condition-editor">
      <ConditionFields
        :model-value="node.config"
        @update:model-value="emit('patch', { config: $event })"
      />
    </div>
    <WaitStepEditor
      v-else-if="node.type === 'wait'"
      :node="node"
      @patch="emit('patch', $event)"
    />
    <ScriptStepEditor
      v-else-if="node.type === 'script'"
      :node="node"
      @patch="emit('patch', $event)"
    />
    <ScenarioRefStepEditor
      v-else-if="node.type === 'scenario_ref'"
      :node="node"
      :scenarios="scenarios"
      @patch="emit('patch', $event)"
    />
    <div v-else-if="node.type === 'group'" class="container-info">
      <el-empty :image-size="80" :description="$t('apiTesting.scenarioEditor.groupHint')" />
    </div>

    <div v-else-if="node.type === 'else'" class="container-info">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        :title="$t('apiTesting.scenarioEditor.elseInfo')"
      />
    </div>

    <el-alert
      v-else
      type="warning"
      :closable="false"
      show-icon
      :title="$t('apiTesting.scenarioEditor.unknownType', { type: node.type })"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import HttpStepEditor from './editors/HttpStepEditor.vue'
import ConditionStepEditor from './editors/ConditionStepEditor.vue'
import LoopStepEditor from './editors/LoopStepEditor.vue'
import ConditionFields from './editors/ConditionFields.vue'
import WaitStepEditor from './editors/WaitStepEditor.vue'
import ScriptStepEditor from './editors/ScriptStepEditor.vue'
import ScenarioRefStepEditor from './editors/ScenarioRefStepEditor.vue'

const props = defineProps({
  node: { type: Object, required: true },
  environment: { type: Object, default: null },
  environmentId: { type: [Number, String], default: null },
  scenarioId: { type: [Number, String], default: null },
  scenarios: { type: Array, default: () => [] },
  children: { type: Array, default: () => [] },
  saving: { type: Boolean, default: false },
  hasElse: { type: Boolean, default: false },
  runtimeState: { type: Object, default: null }
})
const emit = defineEmits([
  'patch', 'add-else', 'add-break-if', 'update-break-if', 'remove-break-if',
  'save', 'runtime-update'
])
const { t } = useI18n()

const TYPE_LABELS = {
  http: 'apiTesting.scenarioEditor.typeHttp',
  group: 'apiTesting.scenarioEditor.typeGroup',
  condition: 'apiTesting.scenarioEditor.typeCondition',
  else: 'apiTesting.scenarioEditor.typeElse',
  foreach: 'apiTesting.scenarioEditor.typeForeach',
  for: 'apiTesting.scenarioEditor.typeFor',
  wait: 'apiTesting.scenarioEditor.typeWait',
  script: 'apiTesting.scenarioEditor.typeScript',
  scenario_ref: 'apiTesting.scenarioEditor.typeScenarioRef',
  break_if: 'apiTesting.scenarioEditor.breakIfRow'
}

const typeLabel = computed(() => t(TYPE_LABELS[props.node.type] || props.node.type))
</script>

<style scoped>
.step-editor-panel { padding: 10px 0px; }
.panel-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.panel-reference {
  color: var(--el-text-color-secondary); font-size: 13px;
  text-align: left; white-space: nowrap;
}
.panel-type {
  padding: 2px 10px; border-radius: 6px; font-size: 12px;
  color: #6366f1; background: #eef0ff;
}
.panel-type.http { color: #409eff; background: #ecf5ff; }
.panel-type.wait { color: #409eff; background: #ecf5ff; }
.panel-type.condition, .panel-type.else { color: #f56c6c; background: #fef0f0; }
.panel-type.for, .panel-type.foreach, .panel-type.break_if { color: #e6a23c; background: #fdf6ec; }
.panel-title { font-size: 15px; font-weight: 600; color: #303133; }
.container-info { padding: 24px 0; }
.condition-editor { max-width: 720px; }
</style>
