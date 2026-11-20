<template>
  <div
    class="step-node"
    :class="{
      'is-drop-before': dropTargetId === dropKey('before'),
      'is-drop-after': dropTargetId === dropKey('after')
    }"
    :style="{ '--step-indent': `${depth * 18 + 8}px` }"
  >
    <div
      class="step-row"
      :class="{
        'is-disabled': !node.enabled,
        'is-selected': selectedId === node.id,
        'is-drop-inside': dropTargetId === dropKey('inside')
      }"
      :style="{ paddingLeft: 'var(--step-indent)' }"
      :draggable="node.type !== 'else'"
      @click="emit('select', node.id)"
      @dragstart.stop="onDragStart"
      @dragend="emit('drag-end')"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop.prevent="onDrop"
    >
      <el-icon
        v-if="hasChildren"
        class="caret"
        :class="{ collapsed: collapsed.has(node.id) }"
        @click.stop="emit('toggle-collapse', node.id)"
      ><ArrowDown /></el-icon>
      <span v-else class="caret-placeholder" />

      <span class="step-icon" :class="node.type">
        <el-icon><component :is="iconFor(node)" /></el-icon>
      </span>

      <span class="step-name">{{ displayName }}</span>
      <span v-if="summary" class="step-summary">{{ summary }}</span>

      <span class="step-actions" @click.stop>
        <span v-if="node.reference_id" class="step-reference">#{{ node.reference_id }}</span>
        <el-switch
          :model-value="node.enabled"
          size="small"
          @update:model-value="value => emit('toggle-enabled', { id: node.id, enabled: value })"
        />
        <el-button link size="small" :icon="CopyDocument" @click="emit('copy', node.id)" />
        <el-button link size="small" type="danger" :icon="Delete" @click="emit('delete', node.id)" />
        <el-popover
          v-model:visible="addVisible"
          placement="bottom-end"
          :width="canHaveChildren ? 180 : 340"
          trigger="click"
          popper-class="step-add-popper"
        >
          <template #reference>
            <el-button
              link
              size="small"
              :icon="Plus"
              :title="$t('apiTesting.scenarioEditor.addSiblingStep')"
            />
          </template>
          <StepAddPopover
            v-if="addVisible"
            :can-have-children="canHaveChildren"
            @add="payload => emit('add-step', { ...payload, targetId: node.id })"
            @close="addVisible = false"
          />
        </el-popover>
      </span>
    </div>

    <div
      v-if="onErrorSummary && !collapsed.has(node.id)"
      class="step-row virtual-config-row"
      :style="{ '--step-indent': `${(depth + 1) * 18 + 8}px` }"
      @click="emit('select', node.id)"
    >
      <span class="caret-placeholder" />
      <span class="step-icon on-error"><el-icon><CircleCloseFilled /></el-icon></span>
      <span class="step-name">{{ $t('apiTesting.scenarioEditor.onErrorRow') }}</span>
      <span class="step-summary">{{ onErrorSummary }}</span>
    </div>

    <div v-if="hasChildren && !collapsed.has(node.id)">
      <ScenarioStepNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :selected-id="selectedId"
        :collapsed="collapsed"
        :drop-target-id="dropTargetId"
        :depth="depth + 1"
        @select="id => emit('select', id)"
        @toggle-collapse="id => emit('toggle-collapse', id)"
        @toggle-enabled="payload => emit('toggle-enabled', payload)"
        @copy="id => emit('copy', id)"
        @delete="id => emit('delete', id)"
        @drag-start="id => emit('drag-start', id)"
        @drag-end="emit('drag-end')"
        @drag-over="payload => emit('drag-over', payload)"
        @drop="payload => emit('drop', payload)"
        @add-step="payload => emit('add-step', payload)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ArrowDown, CopyDocument, Delete, Link, Document,
  Folder, BottomLeft, BottomRight, Refresh, RefreshRight, Timer, Files, Plus,
  CircleCloseFilled, WarningFilled
} from '@element-plus/icons-vue'
import StepAddPopover from './StepAddPopover.vue'
import { isContainer } from '@/utils/scenarioDefinition'

const props = defineProps({
  node: { type: Object, required: true },
  selectedId: { type: String, default: null },
  collapsed: { type: Set, default: () => new Set() },
  dropTargetId: { type: String, default: null },
  depth: { type: Number, default: 0 },
})

const emit = defineEmits([
  'select', 'toggle-collapse', 'toggle-enabled', 'copy', 'delete',
  'drag-start', 'drag-end', 'drag-over', 'drop', 'add-step',
  
])
const { t } = useI18n()
const addVisible = ref(false)
const canHaveChildren = computed(() => isContainer(props.node.type))

const ICONS = {
  http: Link, group: Folder, condition: BottomLeft, else: BottomRight,
  foreach: Refresh, for: RefreshRight, script: Document, wait: Timer,
  scenario_ref: Files, break_if: WarningFilled
}

const TYPE_LABEL_KEYS = {
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

const displayName = computed(() => {
  if (props.node.type === 'http') return props.node.name || t('apiTesting.scenarioEditor.typeHttp')
  // 系统命名节点（name 仍等于类型键）展示本地化名称，例如 condition 显示为 If。
  const labelKey = TYPE_LABEL_KEYS[props.node.type]
  if (!props.node.name || props.node.name === props.node.type) {
    return labelKey ? t(labelKey) : props.node.name || props.node.type
  }
  return props.node.name
})

const summary = computed(() => {
  const config = props.node.config || {}
  if (props.node.type === 'http') {
    const request = config.request || {}
    return `${request.method || 'GET'} ${request.url || ''}`.trim()
  }
  if (props.node.type === 'condition') return conditionSummary(config)
  if (props.node.type === 'foreach') return config.array_expression || ''
  if (props.node.type === 'for') return String(config.count ?? '')
  if (props.node.type === 'wait') return `${config.duration_ms ?? 0} ms`
  if (props.node.type === 'script') return config.language || ''
  if (props.node.type === 'scenario_ref') return config.source_meta?.name || props.node.source_id || ''
  if (props.node.type === 'group') return t('apiTesting.scenarioEditor.childCount', { n: props.node.children.length })
  if (props.node.type === 'break_if') return conditionSummary(config)
  return ''
})

const onErrorSummary = computed(() => {
  if (!['for', 'foreach'].includes(props.node.type)) return ''
  const labels = {
    continue: 'apiTesting.scenarioEditor.onErrorContinue',
    next_iteration: 'apiTesting.scenarioEditor.onErrorNextIteration',
    break_loop: 'apiTesting.scenarioEditor.onErrorBreakLoop',
    abort_scenario: 'apiTesting.scenarioEditor.onErrorAbort'
  }
  // 虚拟节点名称固定为 On Error，摘要只展示具体失败策略，避免文案重复。
  return t(labels[props.node.config?.on_error || 'abort_scenario'])
})

const hasChildren = computed(() => props.node.children.length > 0)

function iconFor(node) {
  return ICONS[node.type] || Document
}

function conditionSummary(config) {
  return `${config.left ?? ''} ${operatorLabel(config.operator)} ${formatRight(config)}`.trim()
}

function operatorLabel(operator) {
  const map = {
    eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤',
    contains: t('apiTesting.scenarioEditor.opContains'),
    not_contains: t('apiTesting.scenarioEditor.opNotContains'),
    exists: t('apiTesting.scenarioEditor.opExists'),
    not_exists: t('apiTesting.scenarioEditor.opNotExists'),
    is_empty: t('apiTesting.scenarioEditor.opIsEmpty'),
    not_empty: t('apiTesting.scenarioEditor.opNotEmpty')
  }
  return map[operator] || operator
}

function formatRight(config) {
  if (['exists', 'not_exists', 'is_empty', 'not_empty'].includes(config.operator)) return ''
  if (config.right_type === 'null') return 'null'
  return typeof config.right === 'string' ? `"${config.right}"` : String(config.right)
}

function onDragStart(event) {
  if (props.node.type === 'else') {
    event.preventDefault()
    return
  }
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('application/x-testhub-step', props.node.id)
  event.dataTransfer.setData('text/plain', props.node.id)
  emit('drag-start', props.node.id)
}

function dropMode(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  const ratio = (event.clientY - rect.top) / rect.height
  // 容器的上下边缘落到外层，只有中部表示移入容器。
  if (!canHaveChildren.value) return ratio < 0.5 ? 'before' : 'after'
  if (ratio < 0.28) return 'before'
  return ratio < 0.72 ? 'inside' : 'after'
}

function onDragOver(event) {
  emit('drag-over', { targetId: props.node.id, mode: dropMode(event), event })
}

function onDragLeave(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) emit('drag-over', null)
}

function onDrop(event) {
  emit('drop', { targetId: props.node.id, mode: dropMode(event), event })
}

function dropKey(mode) {
  return `${props.node.id}:${mode}`
}

</script>

<style scoped>
.step-node { position: relative; user-select: none; }
.step-row {
  display: flex; align-items: center; gap: 8px; min-height: 38px;
  padding-right: 8px; border-radius: 8px; cursor: pointer;
  color: #303133; transition: background .15s, box-shadow .15s;
}
.step-row:hover { background: #f7f8fc; }
.step-row.is-selected { background: var(--el-color-primary-light-9); box-shadow: inset 0 0 0 1px var(--el-color-primary); }
.step-row.is-disabled .step-name, .step-row.is-disabled .step-summary { opacity: .55; }
/* 同级插入线标在整个子树的边缘，而不是容器标题和子步骤之间。 */
.step-node.is-drop-before::before, .step-node.is-drop-after::after {
  content: ''; position: absolute; z-index: 1; height: 2px;
  left: var(--step-indent); right: 8px; pointer-events: none;
  background: var(--el-color-primary);
}
.step-node.is-drop-before::before { top: 0; }
.step-node.is-drop-after::after { bottom: 0; }
.step-row.is-drop-inside {
  background: var(--el-color-primary-light-9); box-shadow: inset 0 0 0 2px var(--el-color-primary);
}
.virtual-config-row { cursor: pointer; color: #606266; }
.virtual-config-row:hover { background: #f7f8fc; }
.virtual-config-row { padding-left: var(--step-indent); }
/* 展开时向下，收起时旋转为向右，保持与树形层级方向一致。 */
.caret { font-size: 12px; color: #909399; transition: transform .15s; }
.caret.collapsed { transform: rotate(-90deg); }
.caret-placeholder { width: 12px; }
.step-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; font-size: 14px;
  color: #6366f1; background: transparent; flex: 0 0 18px;
}
.step-icon :deep(.el-icon) { width: 18px; height: 18px; }
.step-icon.http { color: #409eff; }
.step-icon.group { color: #67c23a; }
.step-icon.condition, .step-icon.else { color: #f56c6c; }
.step-icon.for, .step-icon.foreach { color: #e6a23c; }
.step-icon.wait { color: #409eff; }
.step-icon.scenario_ref { color: #909399; }
.step-icon.on-error { color: #f56c6c; }
.step-icon.break_if { color: #e6a23c; }
.step-name { font-size: 13px; font-weight: 500; max-width: 42%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-summary { flex: 1; min-width: 0; font-size: 12px; color: #909399; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-reference { display: none; color: var(--el-text-color-secondary); font-size: 12px; white-space: nowrap; }
.step-actions { display: none; align-items: center; gap: 2px; flex: 0 0 auto; margin-left: auto; }
.step-row:hover .step-actions, .step-row.is-selected .step-actions { display: inline-flex; }
.step-row:hover .step-reference { display: inline-flex; margin-right: 6px; }
.step-actions :deep(.el-button) { margin-left: 0; padding: 2px; }
</style>
