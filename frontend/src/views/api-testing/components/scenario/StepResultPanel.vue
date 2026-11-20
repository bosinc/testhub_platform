<template>
  <div class="step-result-panel">
    <div class="result-header">
      <span class="result-title">{{ $t('apiTesting.scenarioEditor.resultTitle') }}</span>
      <template v-if="execution">
        <span class="result-stat">
          {{ $t('apiTesting.scenarioEditor.resultStat', {
            passed: execution.passed ?? 0,
            failed: execution.failed ?? 0,
            skipped: execution.skipped ?? 0,
            total: execution.total ?? 0
          }) }}
        </span>
      </template>
      <span v-else class="result-empty">{{ $t('apiTesting.scenarioEditor.noExecution') }}</span>
    </div>

    <div v-if="resultTree.length" class="result-filter-bar">
      <button
        v-for="filter in resultFilters"
        :key="filter.key"
        type="button"
        class="result-filter"
        :class="{ active: activeFilter === filter.key }"
        @click="activeFilter = filter.key"
      >
        {{ filter.label }} ({{ filter.count }})
      </button>
    </div>

    <div class="result-workspace">
      <el-scrollbar class="result-tree-pane">
        <div v-if="resultTree.length" class="result-list">
          <div
            v-for="item in visibleRows"
            v-show="activeFilter === 'all' || item.row.status === activeFilter"
            :key="item.key"
            class="result-item"
            :class="{
              'is-selected': selectedSequence === item.row.sequence,
              'is-instance': item.kind === 'instance'
            }"
            :style="{ paddingLeft: `${item.depth * 18 + 8}px` }"
            @click="selectResult(item.row)"
          >
            <el-icon
              v-if="canExpand(item)"
              class="result-caret"
              :class="{ collapsed: !expanded.has(expansionKey(item)) }"
              @click.stop="toggle(expansionKey(item))"
            ><ArrowDown /></el-icon>
            <span v-else class="result-caret-placeholder" />
            <span class="status-dot" :class="statusDotClass(item.row.status)" />
            <el-icon class="result-kind-icon"><component :is="iconFor(item.row)" /></el-icon>
            <span class="result-name" :title="item.kind === 'iteration' ? iterationLabelFromPath(item.iterationPath) : displayName(item.row)">
              {{ item.kind === 'iteration' ? iterationLabelFromPath(item.iterationPath) : displayName(item.row) }}
            </span>
            <span v-if="item.kind !== 'iteration' && item.row.type_snapshot === 'http'" class="result-type">
              {{ item.row.type_snapshot }}
            </span>
            <span v-if="item.kind === 'instance'" class="result-iteration">
              {{ iterationLabel(item.row) }}
            </span>
            <span v-if="item.kind !== 'iteration'" class="result-summary">{{ rowSummary(item.row) }}</span>
            <span class="result-duration">{{ formatDuration(item.row) }}</span>
          </div>
        </div>
        <el-empty v-else :image-size="60" :description="$t('apiTesting.scenarioEditor.noExecution')" />
      </el-scrollbar>

      <el-drawer
        v-model="detailVisible"
        class="result-detail-drawer"
        direction="rtl"
        size="560px"
        destroy-on-close
      >
        <template #header>
          <div class="result-drawer-header">
            <div class="result-drawer-title">
              <span class="result-drawer-name">
                {{ selectedStep ? displayName(selectedStep) : $t('apiTesting.scenarioEditor.resultTitle') }}
              </span>
              <template v-if="selectedStep">
                <span class="status-dot" :class="statusDotClass(selectedStep.status)" />
                <span class="result-drawer-status">{{ selectedStep.status }}</span>
              </template>
            </div>
            <div v-if="selectedStep" class="result-drawer-navigation">
              <el-button
                class="result-drawer-nav-button"
                :icon="ArrowUp"
                :disabled="!canGoPrevious"
                aria-label="上一条执行结果"
                @click="navigateResult(-1)"
              />
              <span class="result-drawer-position">{{ detailPosition }} / {{ detailRows.length }}</span>
              <el-button
                class="result-drawer-nav-button"
                :icon="ArrowDown"
                :disabled="!canGoNext"
                aria-label="下一条执行结果"
                @click="navigateResult(1)"
              />
            </div>
          </div>
        </template>
        <template v-if="selectedStep">
          <div class="result-detail-content">
            <div
              v-if="selectedStep.type_snapshot === 'http' && assertionResults(selectedStep).length"
              class="result-assertions"
            >
              <div class="result-assertions-title">
                {{ $t('apiTesting.interface.assertionResults') }}
              </div>
              <div
                v-for="(assertion, index) in assertionResults(selectedStep)"
                :key="`${assertionExpression(assertion)}-${index}`"
                class="result-assertion"
                :class="{ failed: !assertion.passed }"
              >
                <span class="result-assertion-index">{{ index + 1 }}.</span>
                <span class="status-dot" :class="assertion.passed ? 'is-passed' : 'is-failed'" />
                <span class="result-assertion-content">
                  <span class="result-assertion-expression">{{ assertionExpression(assertion) }}</span>
                  <span v-if="assertion.error" class="result-assertion-error"> | {{ assertion.error }}</span>
                  <span v-else class="result-assertion-values">
                    · {{ $t('apiTesting.interface.expected') }} {{ formatValue(assertion.expected) }}
                    · {{ $t('apiTesting.interface.actual') }} {{ formatValue(assertion.actual) }}
                  </span>
                </span>
              </div>
            </div>
            <ResponseViewer
              v-if="selectedStep.type_snapshot === 'http'"
              class="execution-response-viewer"
              :response="responseFor(selectedStep)"
              :hide-assertions="true"
              :fill-height="true"
            />
            <div v-else class="control-detail">
              <div class="control-detail-title">{{ controlTitle(selectedStep) }}</div>
              <div v-if="selectedStep.error" class="result-error">{{ selectedStep.error }}</div>
              <div
                v-for="item in controlDetails(selectedStep)"
                :key="item.label"
                class="control-detail-row"
                :class="{ 'control-detail-row-code': item.code }"
              >
                <span class="control-detail-label">{{ item.label }}</span>
                <pre v-if="item.code" class="control-detail-value control-detail-code">{{ item.value }}</pre>
                <span v-else class="control-detail-value">{{ item.value }}</span>
              </div>
            </div>
          </div>
        </template>
        <el-empty v-else :image-size="70" :description="$t('apiTesting.scenarioEditor.selectResultHint')" />
      </el-drawer>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDown, ArrowUp, BottomLeft, BottomRight, Connection, Document, Folder, Link, Refresh, WarningFilled } from '@element-plus/icons-vue'
import ResponseViewer from '@/components/api-testing/ResponseViewer.vue'

const props = defineProps({
  execution: { type: Object, default: null },
  steps: { type: Array, default: () => [] }
})
const { t } = useI18n()
const expanded = ref(new Set())
const knownExpansionKeys = ref(new Set())
const expansionInitialized = ref(false)
const selectedSequence = ref(null)
const detailVisible = ref(false)
const activeFilter = ref('all')

const resultFilters = computed(() => {
  const rows = props.steps
  return [
    { key: 'all', label: t('apiTesting.scenarioEditor.resultFilterAll'), count: rows.length },
    { key: 'PASSED', label: t('apiTesting.scenarioEditor.resultFilterPassed'), count: rows.filter(row => row.status === 'PASSED').length },
    { key: 'FAILED', label: t('apiTesting.scenarioEditor.resultFilterFailed'), count: rows.filter(row => row.status === 'FAILED').length }
  ]
})

const resultTree = computed(() => {
  const nodesByInvocation = new Map()
  const roots = []
  for (const row of props.steps) {
    const invocationId = row.invocation_id || `sequence:${row.sequence}`
    let node = nodesByInvocation.get(invocationId)
    if (!node) {
      // 循环父节点可能在子节点之后写入，先创建占位节点再补全 row。
      node = { key: invocationId, row: null, instances: [], children: [] }
      nodesByInvocation.set(invocationId, node)
    }
    node.row = row
    node.instances.push(row)

    const parentId = row.parent_invocation_id
    if (parentId && !node.parent) {
      let parent = nodesByInvocation.get(parentId)
      if (!parent) {
        parent = { key: parentId, row: null, instances: [], children: [] }
        nodesByInvocation.set(parentId, parent)
      }
      node.parent = parent
      parent.children.push(node)
    }
  }

  for (const node of nodesByInvocation.values()) {
    if (!node.parent) roots.push(node)
  }
  const updateSequence = node => {
    node.children.forEach(updateSequence)
    const ownSequences = node.instances
      .map(row => row.sequence)
      .filter(sequence => Number.isFinite(sequence))
    const childSequences = node.children
      .map(child => child.minSequence)
      .filter(sequence => Number.isFinite(sequence))
    node.minSequence = Math.min(...ownSequences, ...childSequences)
    node.children.sort((left, right) => left.minSequence - right.minSequence)
  }
  roots.forEach(updateSequence)
  roots.sort((left, right) => left.minSequence - right.minSequence)
  return roots
})

const visibleRows = computed(() => {
  const rows = []
  const append = (node, depth, iterationPath = '') => {
    // 增量轮询时父节点可能尚未返回，先展示已到达的子节点。
    if (!node.row) {
      node.children.forEach(child => append(child, depth, iterationPath))
      return
    }
    const row = instanceFor(node, iterationPath)
    // 当前轮次未实际执行该步骤时不渲染，避免把上一轮实例错误复用到本轮。
    if (!row) return
    const rowKey = iterationPath ? `${node.key}:${iterationPath}` : node.key
    rows.push({ key: `group:${rowKey}`, kind: 'group', node, row, depth })
    if (!expanded.value.has(node.key)) return

    if (node.row.type_snapshot === 'for' || node.row.type_snapshot === 'foreach') {
      const paths = childIterationPaths(node, iterationPath)
      paths.forEach(path => {
        // 每个轮次使用独立 key，避免折叠一轮时影响同一循环的其他轮次。
        const iterationKey = `iteration:${node.key}:${path}`
        rows.push({
          key: iterationKey,
          expansionKey: iterationKey,
          kind: 'iteration',
          node,
          row,
          iterationPath: path,
          depth: depth + 1
        })
        if (expanded.value.has(iterationKey)) {
          node.children.forEach(child => append(child, depth + 2, path))
        }
      })
      return
    }

    node.children.forEach(child => append(child, depth + 1, iterationPath))
  }
  resultTree.value.forEach(node => append(node, 0))
  return rows
})

function instanceFor(node, iterationPath) {
  if (!iterationPath) return node.row
  const instance = node.instances.find(row => row.iteration_path === iterationPath)
  if (instance) return instance
  // 循环节点只有一条聚合记录，轮次标题仍使用该记录；循环体步骤不允许回退。
  if (node.row?.type_snapshot === 'for' || node.row?.type_snapshot === 'foreach') {
    return node.row
  }
  return null
}

function childIterationPaths(node, parentPath) {
  const prefix = parentPath ? `${parentPath}/` : ''
  const paths = new Set()
  const collect = current => {
    current.instances.forEach(row => {
      const path = row.iteration_path || ''
      if (!path.startsWith(prefix) || path === parentPath) return
      const next = path.slice(prefix.length).split('/')[0]
      if (next) paths.add(prefix + next)
    })
    current.children.forEach(collect)
  }
  node.children.forEach(collect)
  return [...paths].sort((left, right) => {
    const leftParts = left.split('/').map(Number)
    const rightParts = right.split('/').map(Number)
    for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
      const difference = (leftParts[index] ?? -1) - (rightParts[index] ?? -1)
      if (difference) return difference
    }
    return 0
  })
}

const selectedStep = computed(() => props.steps.find(step => step.sequence === selectedSequence.value) || null)

const detailRows = computed(() => visibleRows.value.filter(item => (
  item.kind !== 'iteration'
    && (activeFilter.value === 'all' || item.row.status === activeFilter.value)
)))
const detailIndex = computed(() => detailRows.value.findIndex(
  item => item.row.sequence === selectedSequence.value
))
const detailPosition = computed(() => detailIndex.value >= 0 ? detailIndex.value + 1 : 0)
const canGoPrevious = computed(() => detailIndex.value > 0)
const canGoNext = computed(() => detailIndex.value >= 0 && detailIndex.value < detailRows.value.length - 1)

watch(() => props.steps, steps => {
  const expandableKeys = new Set()
  const defaultExpandedKeys = new Set()
  const visit = (nodes, iterationPath = '') => nodes.forEach(node => {
    if (node.children.length || node.instances.length > 1) {
      expandableKeys.add(node.key)
      if (hasFailedDescendant(node, iterationPath)) defaultExpandedKeys.add(node.key)
    }
    if (node.row?.type_snapshot === 'for' || node.row?.type_snapshot === 'foreach') {
      childIterationPaths(node, iterationPath).forEach(path => {
        const iterationKey = `iteration:${node.key}:${path}`
        expandableKeys.add(iterationKey)
        if (hasFailedDescendant(node, path)) defaultExpandedKeys.add(iterationKey)
        visit(node.children, path)
      })
      return
    }
    visit(node.children, iterationPath)
  })
  visit(resultTree.value)

  if (!expansionInitialized.value) {
    expanded.value = defaultExpandedKeys
    expansionInitialized.value = true
  } else {
    const next = new Set([...expanded.value].filter(key => expandableKeys.has(key)))
    expandableKeys.forEach(key => {
      if (!knownExpansionKeys.value.has(key) && defaultExpandedKeys.has(key)) next.add(key)
    })
    expanded.value = next
  }
  knownExpansionKeys.value = expandableKeys
  if (!steps.some(step => step.sequence === selectedSequence.value)) {
    selectedSequence.value = steps.find(step => step.type_snapshot === 'http')?.sequence || steps[0]?.sequence || null
  }
}, { immediate: true })

function hasFailedDescendant(node, iterationPath = '') {
  const row = instanceFor(node, iterationPath)
  if (row?.status === 'FAILED') return true

  // 循环节点在未指定轮次时需要检查所有轮次，确保失败轮次默认展开。
  if (!iterationPath && (node.row?.type_snapshot === 'for' || node.row?.type_snapshot === 'foreach')) {
    return childIterationPaths(node, '').some(path => hasFailedDescendant(node, path))
  }
  return node.children.some(child => hasFailedDescendant(child, iterationPath))
}

function toggle(key) {
  const next = new Set(expanded.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expanded.value = next
}

function expansionKey(item) {
  return item.kind === 'iteration' ? item.expansionKey : item.node.key
}

function canExpand(item) {
  // 只有真正承载子步骤或循环实例的控制节点显示展开按钮，普通步骤（包括等待）不显示。
  const containerTypes = ['group', 'condition', 'else', 'for', 'foreach']
  if (item.kind === 'iteration') return item.node.children.length > 0
  return item.kind === 'group'
    && containerTypes.includes(item.row.type_snapshot)
    && (item.node.children.length || item.node.instances.length > 1)
}

function selectResult(row) {
  selectedSequence.value = row.sequence
  detailVisible.value = true
}

function navigateResult(offset) {
  const target = detailRows.value[detailIndex.value + offset]
  if (target) selectedSequence.value = target.row.sequence
}

function iconFor(row) {
  return {
    http: Link,
    group: Folder,
    condition: BottomLeft,
    else: BottomRight,
    for: Refresh,
    foreach: Refresh,
    scenario_ref: Connection,
    wait: Document,
    break_if: WarningFilled
  }[row.type_snapshot] || Document
}

function displayName(row) {
  const name = row.name_snapshot
  const typeLabels = {
    condition: 'apiTesting.scenarioEditor.typeCondition',
    else: 'apiTesting.scenarioEditor.typeElse',
    foreach: 'apiTesting.scenarioEditor.typeForeach',
    for: 'apiTesting.scenarioEditor.typeFor',
    group: 'apiTesting.scenarioEditor.typeGroup',
    wait: 'apiTesting.scenarioEditor.typeWait',
    script: 'apiTesting.scenarioEditor.typeScript',
    scenario_ref: 'apiTesting.scenarioEditor.typeScenarioRef',
    http: 'apiTesting.scenarioEditor.typeHttp',
    break_if: 'apiTesting.scenarioEditor.breakIfRow'
  }
  // 系统默认名称直接显示类型文案，自定义名称不再重复拼接类型前缀。
  if (!name || name === row.type_snapshot) {
    const labelKey = typeLabels[row.type_snapshot]
    return labelKey ? t(labelKey) : name || row.type_snapshot
  }
  return name
}

function iterationLabel(row) {
  if (!row.iteration_path) return ''
  return iterationLabelFromPath(row.iteration_path)
}

function iterationLabelFromPath(path) {
  if (!path) return ''
  return `轮次 ${path.split('/').at(-1) - -1}`
}

function rowSummary(row) {
  const output = row.output || {}
  if (row.type_snapshot === 'http') {
    const method = row.request_data?.method || ''
    const url = row.request_data?.url || ''
    const statusCode = row.response_data?.status_code
    return [method, url, statusCode ? `${statusCode}` : ''].filter(Boolean).join(' ')
  }
  if (row.type_snapshot === 'condition') return conditionSummary(output)
  if (row.type_snapshot === 'break_if') return conditionSummary(output)
  if (row.type_snapshot === 'foreach') {
    return output.array_expression || ''
  }
  if (row.type_snapshot === 'for') return String(output.count ?? '')
  if (row.type_snapshot === 'wait') return `${output.duration_ms ?? 0} ms`
  if (row.type_snapshot === 'group') return ''
  return output.passed === false ? '未通过' : ''
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
  return map[operator] || operator || ''
}

function formatRight(config) {
  if (['exists', 'not_exists', 'is_empty', 'not_empty'].includes(config.operator)) return ''
  if (config.right_type === 'null') return 'null'
  return typeof config.right === 'string' ? `"${config.right}"` : String(config.right ?? '')
}

function controlTitle(row) {
  if (row.type_snapshot === 'foreach') return 'ForEach 执行信息'
  if (row.type_snapshot === 'for') return '循环执行信息'
  if (row.type_snapshot === 'condition') return '条件判断信息'
  return '步骤执行信息'
}

function controlDetails(row) {
  const output = row.output || {}
  const details = []
  if (row.type_snapshot === 'condition') {
    details.push({ label: '条件结果', value: output.matched ? '成立' : '不成立' })
    details.push({ label: '表达式', value: conditionSummary(output) })
  }
  if (row.type_snapshot === 'break_if') {
    details.push({ label: '条件结果', value: output.matched ? '命中，中断循环' : '未命中，继续循环' })
    details.push({ label: '表达式', value: conditionSummary(output) })
  }
  if (row.type_snapshot === 'foreach') {
    details.push({ label: '数组表达式', value: output.array_expression || '—' })
    details.push({ label: '数组长度', value: output.item_count ?? output.items?.length ?? 0 })
    details.push({ label: '完整执行次数', value: output.completed_iterations ?? output.iterations ?? 0 })
    if (output.break_iteration !== null && output.break_iteration !== undefined) {
      details.push({ label: '中断轮次', value: Number(output.break_iteration) + 1 })
    }
    // ForEach 数组内容使用格式化 JSON 代码块，便于查看嵌套对象和长文本。
    details.push({ label: '数组内容', value: formatValue(output.items), code: true })
  }
  if (row.type_snapshot === 'for') {
    details.push({ label: '配置次数', value: output.count ?? '—' })
    details.push({ label: '完整执行次数', value: output.completed_iterations ?? output.iterations ?? 0 })
    if (output.break_iteration !== null && output.break_iteration !== undefined) {
      details.push({ label: '中断轮次', value: Number(output.break_iteration) + 1 })
    }
  }
  if (row.iteration_path) details.push({ label: '循环路径', value: row.iteration_path })
  return details
}

function responseFor(row) {
  return {
    ...row,
    assertions_results: row.assertion_results || [],
    extraction_results: row.extraction_results || []
  }
}

function assertionResults(row) {
  return row.assertion_results || []
}

function assertionExpression(assertion) {
  const hasValue = value => value !== null && value !== undefined && value !== ''
  const expected = hasValue(assertion.expected) ? formatValue(assertion.expected) : ''
  const jsonPath = assertion.json_path || '$'
  const operatorKeys = {
    equals: 'equals',
    not_equals: 'notEquals',
    exists: 'exists',
    not_exists: 'notExists',
    is_not_empty: 'isNotEmpty',
    is_empty: 'isEmpty',
    is_string: 'isString',
    is_number: 'isNumber',
    is_boolean: 'isBoolean',
    is_array: 'isArray',
    is_object: 'isObject',
    includes: 'includes',
    not_includes: 'notIncludes',
    contains: 'contains',
    not_contains: 'notContains',
    at_least: 'atLeast',
    greater_than: 'greaterThan',
    less_than: 'lessThan',
    close_to: 'closeTo',
    length: 'length',
    matches: 'matches'
  }
  const operator = assertion.operator || 'equals'
  const operatorLabel = operatorKeys[operator]
    ? t(`apiTesting.interface.assertionOperators.${operatorKeys[operator]}`)
    : operator
  const withExpected = value => value == null || value === '' ? '' : ` ${value}`

  if (assertion.type === 'status_code') return `HTTP 状态码 ${operatorLabel}${withExpected(expected)}`
  if (assertion.type === 'response_time') return `响应时间 ≤ ${expected} ms`
  if (assertion.type === 'contains') return `响应内容 ${operatorLabel}${withExpected(expected)}`
  if (assertion.type === 'json_path') {
    return `${jsonPath} ${operatorLabel}${withExpected(expected)}`.trim()
  }
  if (assertion.type === 'header') {
    const headerExpected = hasValue(assertion.expected_value) ? formatValue(assertion.expected_value) : ''
    return `Header ${assertion.header_name || '—'} ${operatorLabel}${withExpected(headerExpected)}`
  }
  if (assertion.type === 'equals') return `响应内容 ${operatorLabel}${withExpected(expected)}`
  return assertion.type || '—'
}

function formatValue(value) {
  if (value == null) return '—'
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}

function statusDotClass(status) {
  if (status === 'PASSED') return 'is-passed'
  if (status === 'FAILED') return 'is-failed'
  return 'is-neutral'
}

function formatDuration(step) {
  if (!step.started_at || !step.finished_at) return ''
  const ms = new Date(step.finished_at) - new Date(step.started_at)
  return ms >= 0 ? `${ms} ms` : ''
}
</script>

<style scoped>
.step-result-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; background: #fff; }
.result-header { display: flex; align-items: center; gap: 10px; padding: 0 0 10px; }
.result-title { font-size: 13px; font-weight: 600; color: #303133; }
.result-stat { font-size: 12px; color: #909399; }
.result-empty { font-size: 12px; color: #c0c4cc; }
.result-filter-bar { display: flex; align-items: center; gap: 6px; min-height: 42px; border-bottom: 1px solid #ebeef5; }
.result-filter { padding: 7px 14px; border: 0; border-radius: 8px; background: transparent; color: #606266; cursor: pointer; font-size: 13px; }
.result-filter:hover { color: var(--el-color-primary); }
.result-filter.active { background: #f5f7fa; color: #303133; box-shadow: 0 1px 4px rgb(0 0 0 / 8%); }
.result-workspace { display: flex; flex: 1; min-height: 0; border: 1px solid #ebeef5; border-radius: 8px; overflow: hidden; }
.result-tree-pane { flex: 1 1 auto; width: 100%; min-width: 0; }
.result-list { min-width: 680px; }
.result-item { display: flex; align-items: center; gap: 7px; min-height: 42px; padding-top: 7px; padding-right: 10px; padding-bottom: 7px; border-bottom: 1px dashed #f0f2f5; cursor: pointer; color: #303133; }
.result-item:hover { background: var(--el-fill-color-light); }
.result-item.is-selected { background: var(--el-bg-color); }
.result-item.is-instance { background: #fafbfd; }
.result-caret { flex: 0 0 14px; color: #909399; transition: transform .15s; cursor: pointer; }
.result-caret.collapsed { transform: rotate(-90deg); }
.result-caret-placeholder { flex: 0 0 14px; }
.result-kind-icon { flex: 0 0 16px; color: #909399; }
.status-dot { display: inline-block; width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; }
.status-dot.is-passed { background: #67c23a; }
.status-dot.is-failed { background: #f56c6c; }
.status-dot.is-neutral { background: #c0c4cc; }
.result-name { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.result-type, .result-iteration { flex: 0 0 auto; color: #909399; font-size: 12px; }
.result-iteration { color: var(--el-color-primary); }
.result-summary { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #909399; font-size: 12px; }
.result-duration { margin-left: auto; flex: 0 0 auto; color: #909399; font-size: 12px; }
.result-drawer-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-width: 0; min-height: 32px; }
.result-drawer-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.result-drawer-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 15px; font-weight: 600; color: #303133; }
.result-drawer-status { color: #606266; font-size: 13px; }
.result-detail-content { display: flex; flex: 1 1 auto; flex-direction: column; height: 100%; min-height: 0; overflow: hidden; }
.result-assertions { margin: 0 0 12px; padding: 12px 14px; border-radius: 8px; background: #f8f9fb; }
.result-assertions-title { margin-bottom: 9px; font-size: 13px; font-weight: 600; color: #303133; }
.result-assertion { display: flex; align-items: flex-start; gap: 7px; min-width: 0; padding: 7px 0; color: #606266; font-size: 13px; line-height: 1.5; }
.result-assertion + .result-assertion { border-top: 1px solid #ebeef5; }
.result-assertion-index { flex: 0 0 16px; color: #909399; text-align: right; }
.result-assertion > .status-dot { margin-top: 6px; }
.result-assertion-content { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.result-assertion-expression, .result-assertion-values, .result-assertion-error { overflow-wrap: anywhere; }
.result-assertion-expression { color: #606266; }
.result-assertion-values, .result-assertion-error { color: #909399; }
.result-assertion-error { color: var(--el-color-danger); }
.result-drawer-navigation { display: flex; align-items: center; flex: 0 0 auto; gap: 8px; }
.result-drawer-nav-button { box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 32px; width: 32px; height: 32px; margin: 0; padding: 0; border-radius: 4px; line-height: 1; }
.result-drawer-position { min-width: 42px; color: #909399; font-size: 13px; text-align: center; }
/* 抽屉内容被 teleport 到 body，使用全局选择器确保 Element Plus 默认间距被覆盖。 */
:global(.result-detail-drawer .el-drawer__header) { margin-bottom: 0 !important; overflow: visible; }
:global(.result-detail-drawer .el-drawer__body) { display: flex; flex: 1 1 auto; flex-direction: column; height: 100%; min-height: 0; padding: 16px; overflow: hidden; }
.result-detail-content > .execution-response-viewer { flex: 1 1 auto; min-height: 0; }
.control-detail {
  display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0;
  padding: 12px; overflow: hidden; border: 1px solid #ebeef5; border-radius: 8px; background: #fafbfd;
}
.control-detail-title { margin-bottom: 12px; font-weight: 600; color: #303133; }
.control-detail-row { display: flex; gap: 16px; padding: 8px 0; border-bottom: 1px solid #ebeef5; font-size: 13px; }
.control-detail-row:last-child { border-bottom: none; }
.control-detail-label { flex: 0 0 100px; color: #909399; }
.control-detail-value { min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: #303133; }
.control-detail-row-code { display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0; gap: 8px; }
.control-detail-row-code .control-detail-label { flex: 0 0 auto; }
.control-detail-code {
  width: 100%; box-sizing: border-box; flex: 1 1 auto; min-height: 0; margin: 0; padding: 10px 12px; overflow: auto;
  border-radius: 6px; background: #f5f7fa; color: #303133;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere;
}
.result-error { margin-bottom: 10px; color: var(--el-color-danger); white-space: pre-wrap; }
</style>
