<template>
  <div class="scenario-editor" v-loading="loading">
    <template v-if="executionViewVisible">
      <div class="execution-toolbar">
        <el-button
          link
          :icon="ArrowLeft"
          :title="$t('apiTesting.scenarioEditor.backToEditor')"
          @click="exitExecutionView"
        />
        <span class="execution-title">{{ $t('apiTesting.scenarioEditor.executionRecords') }}</span>
        <span v-if="execution" class="execution-id">#{{ execution.id }}</span>
        <span class="step-edit-spacer" />
        <el-button link :icon="Refresh" :loading="historyLoading" @click="refreshExecutionView" />
      </div>

      <div class="execution-view-body">
        <div class="execution-history-pane">
          <div class="history-header">
            <span>{{ $t('apiTesting.scenarioEditor.history') }}</span>
            <span class="history-count">{{ executionHistory.length }}</span>
          </div>
          <el-scrollbar>
            <div
              v-for="item in executionHistory"
              :key="item.id"
              class="history-item"
              :class="{ active: String(item.id) === String(viewedExecutionId) }"
              @click="loadExecutionDetail(item.id)"
            >
              <div class="history-main-line">
                <el-tag size="small" :type="historyStatusType(item)">
                  {{ item.conclusion || item.status }}
                </el-tag>
                <span class="history-stat">
                  {{ $t('apiTesting.scenarioEditor.historyStat', {
                    passed: item.passed_requests || 0,
                    failed: item.failed_requests || 0,
                    total: item.total_requests || 0
                  }) }}
                </span>
                <el-button
                  link
                  size="small"
                  type="danger"
                  :icon="Delete"
                  :title="$t('apiTesting.scenarioEditor.deleteExecution')"
                  @click.stop="deleteExecution(item)"
                />
              </div>
              <div class="history-meta">
                <span>{{ formatDate(item.created_at) }}</span>
              </div>
            </div>
            <el-empty v-if="!executionHistory.length" :image-size="60" :description="$t('apiTesting.scenarioEditor.noExecution')" />
          </el-scrollbar>
        </div>
        <div class="execution-detail-pane">
          <StepResultPanel
            :key="viewedExecutionId || 'empty-execution'"
            :execution="execution"
            :steps="resultSteps"
          />
        </div>
      </div>
    </template>

    <template v-else>
      <!-- 标题与描述直接在此编辑，不再单独弹窗 -->
      <div v-if="!stepEditMode" class="scenario-header">
      <div class="scenario-title-row">
        <span v-if="scenarioCase?.id" class="scenario-id">ID {{ scenarioCase.id }}</span>
        <el-input
          v-model="localName"
          class="title-input"
          maxlength="200"
          :placeholder="$t('apiTesting.scenarioEditor.inputName')"
          @blur="saveMeta"
          @keyup.enter="saveMeta"
        />
        <el-tag v-if="hasUnsavedChanges" size="small" type="warning">{{ $t('apiTesting.scenarioEditor.unsaved') }}</el-tag>
      </div>
      <el-input
        v-model="localDescription"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 3 }"
        class="description-input"
        :placeholder="$t('apiTesting.scenarioEditor.addDescription')"
        @blur="saveMeta"
      />
      <div class="scenario-meta">
        <span v-if="scenarioCase?.created_by?.username">{{ scenarioCase.created_by.username }}</span>
        <span v-if="scenarioCase?.updated_at">{{ $t('apiTesting.scenarioEditor.updatedAt', { time: formatDate(scenarioCase.updated_at) }) }}</span>
        <span v-if="scenarioCase?.created_at">{{ $t('apiTesting.scenarioEditor.createdAt', { time: formatDate(scenarioCase.created_at) }) }}</span>
      </div>
      </div>

      <div v-if="!stepEditMode" class="editor-toolbar">
      <div class="toolbar-right">
        <el-button @click="openExecutionView">
          <el-icon><Clock /></el-icon>
          {{ $t('apiTesting.scenarioEditor.executionRecords') }}
        </el-button>
        <el-button :loading="saving" @click="save()">
          <el-icon><DocumentChecked /></el-icon>
          {{ $t('apiTesting.scenarioEditor.saveAll') }}
        </el-button>
        <el-button v-if="!running" type="primary" @click="run">
          <el-icon><VideoPlay /></el-icon>
          {{ $t('apiTesting.scenarioEditor.run') }}
        </el-button>
        <el-button v-else type="danger" plain @click="stop">
          <el-icon><VideoPause /></el-icon>
          {{ $t('apiTesting.scenarioEditor.stop') }}
        </el-button>
      </div>
      </div>

    <!-- 步骤编辑模式：只显示标题栏与编排区 -->
      <div v-else class="step-edit-toolbar">
      <el-button
        link
        :icon="ArrowLeft"
        :title="$t('apiTesting.scenarioEditor.backToList')"
        @click="exitStepEdit"
      />
      <span class="step-edit-title" :title="localName">{{ localName }}</span>
      <el-tag v-if="hasUnsavedChanges" size="small" type="warning">{{ $t('apiTesting.scenarioEditor.unsaved') }}</el-tag>
      <span class="step-edit-spacer" />
      <el-button @click="openExecutionView">
        <el-icon><Clock /></el-icon>
        {{ $t('apiTesting.scenarioEditor.executionRecords') }}
      </el-button>
      <el-button :loading="saving" @click="save()">
        <el-icon><DocumentChecked /></el-icon>
        {{ $t('apiTesting.scenarioEditor.saveAll') }}
      </el-button>
      <el-button v-if="!running" type="primary" @click="run">
        <el-icon><VideoPlay /></el-icon>
        {{ $t('apiTesting.scenarioEditor.run') }}
      </el-button>
      <el-button v-else type="danger" plain @click="stop">
        <el-icon><VideoPause /></el-icon>
        {{ $t('apiTesting.scenarioEditor.stop') }}
      </el-button>
      </div>

      <div ref="editorMainRef" class="editor-main" :class="{ 'overview-mode': !stepEditMode }">
      <ResizableSplitPane
        v-model="treeWidth"
        :min-size="300"
        :max-size="maxTreeWidth"
        :second-min-size="secondPaneMinWidth"
      >
        <template #first>
          <div class="tree-pane">
            <div class="tree-header">
              <span>{{ $t('apiTesting.scenarioEditor.steps') }}</span>
              <span class="tree-count">{{ steps.length }}</span>
            </div>
            <el-scrollbar class="tree-scroll">
              <div v-if="tree.length" class="tree-list">
                <ScenarioStepNode
                  v-for="node in tree"
                  :key="node.id"
                  :node="node"
                  :selected-id="selectedId"
                  :collapsed="collapsed"
                  :drop-target-id="dropTargetId"
                  :depth="0"
                  @select="selectStep"
                  @toggle-collapse="toggleCollapse"
                  @toggle-enabled="toggleEnabled"
                  @copy="copyStep"
                  @delete="removeStep"
                  @drag-start="draggingId = $event"
                  @drag-end="clearDrag"
                  @drag-over="previewStepDrop"
                  @drop="handleDrop"
                  @add-step="onAddStepAt"
                />
              </div>
              <el-empty v-else :image-size="70" :description="$t('apiTesting.scenarioEditor.emptyTree')" />
            </el-scrollbar>
            <div class="tree-footer">
              <el-popover placement="top-start" :width="340" trigger="click" v-model:visible="addStepVisible">
                <template #reference>
                  <el-button class="add-step-button" text>
                    <el-icon><Plus /></el-icon>
                    {{ $t('apiTesting.scenarioEditor.addStep') }}
                  </el-button>
                </template>
                <AddStepMenu @select="onAddStep" />
              </el-popover>
            </div>
          </div>
        </template>

        <template #second>
          <div class="editor-pane">
            <StepEditorPanel
              v-if="selectedNode"
              :node="selectedNode"
              :environment="selectedEnvironment"
              :environment-id="environmentId"
              :scenario-id="scenarioCase?.id"
              :scenarios="scenarios"
              :saving="savingStep"
              :has-else="selectedHasElse"
              :children="selectedNode ? childrenOf(steps, selectedNode.id) : []"
              :runtime-state="selectedRuntimeState"
              @patch="patchStep"
              @add-else="addElse"
              @add-break-if="addBreakIf"
              @update-break-if="updateBreakIf"
              @remove-break-if="removeBreakIf"
              @runtime-update="updateStepRuntimeState"
              @save="saveStep"
            />
            <el-empty v-else :image-size="90" :description="$t('apiTesting.scenarioEditor.selectStepHint')" />
          </div>
        </template>
      </ResizableSplitPane>
      </div>
    </template>

    <!-- 从单接口用例导入 -->
    <el-dialog v-model="importRequestVisible" :title="$t('apiTesting.scenarioEditor.importRequestTitle')" width="640px" destroy-on-close>
      <el-input v-model="requestKeyword" class="import-search" clearable :placeholder="$t('apiTesting.scenarioEditor.searchRequest')" />
      <el-scrollbar max-height="360px">
        <el-tree
          ref="importRequestTreeRef"
          class="import-request-tree"
          :data="apiRequestTree"
          :props="{ label: 'name', children: 'children' }"
          node-key="treeKey"
          default-expand-all
          :filter-node-method="filterRequestNode"
          :empty-text="requestKeyword.trim() ? $t('apiTesting.interface.noSearchResults') : $t('apiTesting.common.noData')"
          @node-click="onImportRequestNodeClick"
        >
          <template #default="{ data }">
            <div
              class="import-tree-node"
              :class="{ 'is-selected': data.type === 'request' && importRequestId === data.id }"
            >
              <template v-if="data.type === 'collection'">
                <el-icon class="import-folder-icon"><Folder /></el-icon>
                <span class="import-name import-folder-name">{{ data.name }}</span>
              </template>
              <template v-else>
                <el-tag size="small" :type="methodType(data.method)">{{ data.method }}</el-tag>
                <span class="import-name">{{ data.name }}</span>
                <span class="import-url" :title="data.url">{{ data.url }}</span>
              </template>
            </div>
          </template>
        </el-tree>
      </el-scrollbar>
      <template #footer>
        <el-button @click="importRequestVisible = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!importRequestId" :loading="importing" @click="confirmImportRequest">
          {{ $t('apiTesting.scenarioEditor.import') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 从其他场景用例导入 -->
    <el-dialog v-model="importScenarioVisible" :title="$t('apiTesting.scenarioEditor.importScenarioTitle')" width="520px">
      <el-select v-model="importScenarioId" filterable class="import-search" :placeholder="$t('apiTesting.scenarioEditor.selectScenario')">
        <el-option v-for="item in scenarios" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
      <template #footer>
        <el-button @click="importScenarioVisible = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!importScenarioId" :loading="importing" @click="confirmImportScenario">
          {{ $t('apiTesting.scenarioEditor.import') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- cURL 导入 -->
    <el-dialog v-model="curlVisible" :title="$t('apiTesting.scenarioEditor.curlTitle')" width="800px" destroy-on-close>
      <CurlImportPanel
        ref="curlImportPanelRef"
        v-model="curlCommand"
        :rows="12"
        :placeholder="$t('apiTesting.scenarioEditor.curlPlaceholder')"
        @parsed="curlPreview = $event"
      />
      <template #footer>
        <el-button @click="curlVisible = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button :disabled="!curlCommand.trim()" @click="parseCurl">{{ $t('apiTesting.scenarioEditor.parse') }}</el-button>
        <el-button type="primary" :disabled="!curlPreview" @click="confirmCurlImport">
          {{ $t('apiTesting.scenarioEditor.import') }}
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, VideoPlay, VideoPause, DocumentChecked, Clock, Refresh, ArrowLeft, Folder, Delete } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import { serializeStepRequest, cloneDraft, createRequestDraft } from '@/utils/requestDraft'
import {
  buildTree, childrenOf, cloneNode, createId, createNode, deleteNode, ensureElse,
  flattenTree, isContainer, moveNode, normalizePositions,
  pairedElse, resolveNodeDrop, rewriteStepReferences
} from '@/utils/scenarioDefinition'
import {
  cancelExecution, deleteScenarioExecution, executeScenarioCase, getAllApiCollections, getAllApiRequests, getApiRequest,
  getEnvironments, getExecutionSteps, getScenarioCaseExecutions,
  getScenarioDefinition, patchScenarioCase, saveScenarioDefinition, saveScenarioStep
} from '@/api/api-testing'
import AddStepMenu from './AddStepMenu.vue'
import CurlImportPanel from '@/components/api-testing/CurlImportPanel.vue'
import ScenarioStepNode from './ScenarioStepNode.vue'
import StepEditorPanel from './StepEditorPanel.vue'
import StepResultPanel from './StepResultPanel.vue'

const props = defineProps({
  scenarioCase: { type: Object, required: true },
  // 当前项目的场景列表（场景树的轻量数据），由页面传入，编辑器不再重复请求
  projectScenarios: { type: Array, default: () => [] },
  // 环境由页面顶部统一选择，编辑器不再单独维护
  environmentId: { type: [Number, String], default: null }
})
const emit = defineEmits(['updated'])
const { t, te } = useI18n()

const loading = ref(false)
const saving = ref(false)
const importing = ref(false)
const localName = ref('')
const localDescription = ref('')
const steps = ref(/** @type {Array<any>} */ ([]))
// 已在服务端落库的步骤 id；新增/复制的步骤在整体保存前只存在于本地。
const persistedStepIds = ref(new Set())
const variables = ref({})
const executionConfig = ref({ total_timeout_ms: 600000, on_error: 'stop' })
const selectedId = ref(null)
const stepEditMode = ref(false)
const collapsed = ref(new Set())
const dirty = ref(false)
// 仅步骤内容被编辑时记录；单步骤保存后只清除对应步骤的未保存标记。
const dirtyStepIds = ref(new Set())
const hasUnsavedChanges = computed(() => dirty.value || dirtyStepIds.value.size > 0)
const savingStep = ref(false)
const treeWidth = ref(380)
const editorMainRef = ref(null)
const editorMainWidth = ref(0)
// 场景树最大可拖到编辑区宽度的 70%
const maxTreeWidth = computed(() => (editorMainWidth.value
  ? Math.round(editorMainWidth.value * 0.7)
  : 560))
// 右侧编辑区允许缩到约 30%，这样场景树才能真正拖到 70%
const secondPaneMinWidth = computed(() => (editorMainWidth.value
  ? Math.max(240, Math.round(editorMainWidth.value * 0.3) - 8)
  : 420))
let editorResizeObserver = null
const environments = ref([])
// 可引用/导入的其他场景：排除当前场景自身。
const scenarios = computed(() => props.projectScenarios.filter(
  item => String(item.id) !== String(props.scenarioCase?.id)))
const draggingId = ref(null)
const dropTargetId = ref(null)
const addStepVisible = ref(false)

const execution = ref(null)
const resultSteps = ref([])
const running = ref(false)
const executionViewVisible = ref(false)
const executionViewPreviousStepEditMode = ref(false)
const executionHistory = ref([])
const historyLoading = ref(false)
const viewedExecutionId = ref(null)
let pollTimer = null
let afterSequence = 0
let executionDetailRequestToken = 0

const importRequestVisible = ref(false)
const importRequestId = ref(null)
const requestKeyword = ref('')
const apiRequestTree = ref(/** @type {Array<Record<string, any>>} */ ([]))
const importRequestTreeRef = ref()
const importScenarioVisible = ref(false)
const importScenarioId = ref(null)
const curlVisible = ref(false)
const curlCommand = ref('')
const curlPreview = ref(/** @type {{ model: any, draft: any } | null} */ (null))
const curlImportPanelRef = ref()

const tree = computed(() => buildTree(steps.value))
const selectedNode = computed(() => steps.value.find(step => step.id === selectedId.value) || null)
// HTTP 步骤的调试请求/响应只在当前编辑页暂存，不写入场景定义或后端。
const stepRuntimeStates = ref(new Map())
const selectedRuntimeState = computed(() => (
  selectedNode.value ? stepRuntimeStates.value.get(selectedNode.value.id) || null : null
))
const selectedHasElse = computed(() => {
  const node = selectedNode.value
  return node?.type === 'condition' && Boolean(pairedElse(steps.value, node.id))
})
const selectedEnvironment = computed(() => {
  const env = environments.value.find(item => String(item.id) === String(props.environmentId))
  return env ? { base_url: env.base_url, variables: env.variables || {} } : null
})
// 把集合与接口组装成与接口树一致的层级结构，供单选导入使用。
function buildApiRequestTree(collections, requests) {
  const nodes = collections.map(item => ({
    ...item, type: 'collection', treeKey: `collection:${item.id}`, children: []
  }))
  const nodeMap = new Map(nodes.map(item => [String(item.id), item]))
  const pathNames = (node) => {
    const names = []
    const seen = new Set()
    let current = node
    while (current && !seen.has(current.id)) {
      seen.add(current.id)
      names.unshift(current.name)
      current = current.parent ? nodeMap.get(String(current.parent)) : null
    }
    return names
  }
  const roots = []
  nodes.forEach(node => {
    node.searchText = pathNames(node).join(' ')
    const parent = node.parent ? nodeMap.get(String(node.parent)) : null
    if (parent) parent.children.push(node)
    else roots.push(node)
  })
  requests.forEach(item => {
    const parent = item.collection != null ? nodeMap.get(String(item.collection)) : null
    const node = {
      ...item, type: 'request', treeKey: `request:${item.id}`,
      searchText: [...(parent ? pathNames(parent) : []), item.name, item.url].filter(Boolean).join(' ')
    }
    if (parent) parent.children.push(node)
    else roots.push(node)
  })
  return roots
}

// 只让接口节点命中；集合节点由 el-tree 在子节点命中时自动显示。
function filterRequestNode(value, data) {
  const keyword = String(value || '').trim().toLowerCase()
  if (!keyword) return true
  return data.type === 'request' && String(data.searchText || '').toLowerCase().includes(keyword)
}

function onImportRequestNodeClick(data) {
  if (data.type !== 'request') return
  importRequestId.value = data.id
}

watch(requestKeyword, value => {
  importRequestTreeRef.value?.filter(value)
})

watch(() => props.scenarioCase?.id, () => {
  // 场景切换后不复用上一个场景的调试数据，避免相同步骤 ID 造成串结果。
  stepRuntimeStates.value = new Map()
  reload()
}, { immediate: true })

async function reload() {
  if (!props.scenarioCase?.id) return
  loading.value = true
  stopPolling()
  running.value = false
  // 切换场景用例时回到场景编辑页，避免复用组件状态仍停留在旧执行记录页。
  executionViewVisible.value = false
  executionViewPreviousStepEditMode.value = false
  execution.value = null
  resultSteps.value = []
  executionHistory.value = []
  viewedExecutionId.value = null
  try {
    const { data } = await getScenarioDefinition(props.scenarioCase.id)
    // 循环的中断条件现在是独立的 break_if 子步骤，不再写入循环 config。
    steps.value = (data.steps || []).map(step => ({
      ...step,
      config: step.config ? cloneStepConfig(step.config) : step.config
    }))
    persistedStepIds.value = new Set(steps.value.map(step => step.id))
    stepEditMode.value = false
    variables.value = data.variables || {}
    executionConfig.value = { total_timeout_ms: 600000, on_error: 'stop', ...(data.execution_config || {}) }
    localName.value = props.scenarioCase.name || ''
    localDescription.value = props.scenarioCase.description || ''
    dirty.value = false
    dirtyStepIds.value = new Set()
    collapsed.value = new Set()
    if (!steps.value.some(step => step.id === selectedId.value)) {
      selectedId.value = steps.value[0]?.id || null
    }
    await loadSupport()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadSupport() {
  const projectId = props.scenarioCase?.project
  try {
    // 不能按项目过滤：后端的 project 过滤会把 project 为空的全局环境也过滤掉，
    // 导致已选环境匹配不到选项、下拉框只显示 ID。这里取全量后在前端筛选。
    const envResponse = await getEnvironments({})
    const allEnvironments = envResponse.data?.results || envResponse.data || []
    environments.value = allEnvironments.filter(env =>
      env.scope === 'GLOBAL' || String(env.project) === String(projectId))
  } catch {
    // Support data is optional; the editor still works without it.
  }
}

// -- scenario title/description -----------------------------------------
async function saveMeta() {
  const name = localName.value.trim()
  if (!name) {
    localName.value = props.scenarioCase.name || ''
    return
  }
  const description = localDescription.value
  if (name === props.scenarioCase.name && description === (props.scenarioCase.description || '')) return
  try {
    await patchScenarioCase(props.scenarioCase.id, { name, description })
    emit('updated')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

// -- tree interactions ---------------------------------------------------
function selectStep(id) {
  selectedId.value = id
  // 点击步骤进入编辑模式；返回按钮回到全宽列表
  stepEditMode.value = true
}

function exitStepEdit() {
  stepEditMode.value = false
}

function toggleCollapse(id) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

function toggleEnabled({ id, enabled }) {
  const node = steps.value.find(step => step.id === id)
  if (!node) return
  node.enabled = enabled
  // If/Else are one control unit and must share the enabled flag.
  if (node.type === 'condition') {
    const elseNode = pairedElse(steps.value, id)
    if (elseNode) elseNode.enabled = enabled
  } else if (node.type === 'else') {
    const conditionId = node.config?.if_step_id
    const condition = steps.value.find(step => step.id === conditionId)
    if (condition) condition.enabled = enabled
  }
  dirty.value = true
}

function copyStep(id) {
  const existingIds = new Set(steps.value.map(step => step.id))
  const result = cloneNode(steps.value, id)
  steps.value = result.steps
  const nextRuntimeStates = new Map(stepRuntimeStates.value)
  result.steps
    .filter(step => !existingIds.has(step.id))
    .forEach(step => nextRuntimeStates.delete(step.id))
  stepRuntimeStates.value = nextRuntimeStates
  if (result.rootId) selectedId.value = result.rootId
  dirty.value = true
}

async function removeStep(id) {
  const node = steps.value.find(step => step.id === id)
  if (!node) return
  try {
    await ElMessageBox.confirm(
      node.type === 'condition'
        ? t('apiTesting.scenarioEditor.deleteIfConfirm')
        : t('apiTesting.scenarioEditor.deleteStepConfirm', { name: node.name || node.type }),
      t('apiTesting.common.tip'),
      { type: 'warning' }
    )
  } catch {
    return
  }
  const removedIds = new Set([id])
  let changed = true
  while (changed) {
    changed = false
    steps.value.forEach(step => {
      if (removedIds.has(step.parent) && !removedIds.has(step.id)) {
        removedIds.add(step.id)
        changed = true
      }
    })
  }
  steps.value = deleteNode(steps.value, id)
  const nextRuntimeStates = new Map(stepRuntimeStates.value)
  removedIds.forEach(stepId => nextRuntimeStates.delete(stepId))
  stepRuntimeStates.value = nextRuntimeStates
  if (!steps.value.some(step => step.id === selectedId.value)) {
    selectedId.value = steps.value[0]?.id || null
    stepEditMode.value = false
  }
  dirty.value = true
}

function clearDrag() {
  draggingId.value = null
  dropTargetId.value = null
}


function previewStepDrop(payload) {
  if (!payload) {
    dropTargetId.value = null
    return
  }
  const { targetId, mode, event } = payload
  const dragId = draggingId.value
    || event.dataTransfer.getData('application/x-testhub-step')
    || event.dataTransfer.getData('text/plain')
  if (dragId && !draggingId.value) draggingId.value = dragId
  const drop = resolveNodeDrop(steps.value, dragId, targetId, mode)
  event.dataTransfer.dropEffect = drop ? 'move' : 'none'
  if (drop) event.preventDefault()
  dropTargetId.value = drop ? `${drop.anchor.id}:${mode}` : null
}

function applyStepMove(id, targetId, mode) {
  const moved = moveNode(steps.value, id, targetId, mode)
  if (moved === steps.value) return false
  steps.value = moved
  dirty.value = true
  return true
}

function handleDrop({ targetId, mode, event }) {
  const dragId = draggingId.value
    || event?.dataTransfer?.getData('application/x-testhub-step')
    || event?.dataTransfer?.getData('text/plain')
  clearDrag()
  if (!dragId || !applyStepMove(dragId, targetId, mode)) return
  selectedId.value = dragId
  if (mode === 'inside' && collapsed.value.has(targetId)) toggleCollapse(targetId)
}

// -- step editing --------------------------------------------------------
function patchStep(fields) {
  const node = selectedNode.value
  if (!node) return
  for (const [key, value] of Object.entries(fields)) {
    if (key === 'config') node.config = value
    else if (key === 'source_id' || key === 'source_type') node[key] = value
    else node[key] = value
  }
  dirtyStepIds.value = new Set(dirtyStepIds.value).add(node.id)
}

function addBreakIf() {
  const loop = selectedNode.value
  if (!loop || !['for', 'foreach'].includes(loop.type)) return
  const condition = createNode('break_if', {
    parent: loop.id,
    config: { left: '', operator: 'eq', right: '', right_type: 'string' }
  })
  condition.position = childrenOf(steps.value, loop.id).length
  steps.value = normalizePositions([...steps.value, condition])
  selectedId.value = condition.id
  collapsed.value = new Set([...collapsed.value].filter(id => id !== loop.id))
  dirty.value = true
}

function updateBreakIf({ id, config }) {
  const node = steps.value.find(step => step.id === id)
  if (!node || node.type !== 'break_if') return
  node.config = config
  dirtyStepIds.value = new Set(dirtyStepIds.value).add(id)
}

function removeBreakIf(id) {
  const node = steps.value.find(step => step.id === id)
  if (!node || node.type !== 'break_if') return
  steps.value = deleteNode(steps.value, id)
  if (selectedId.value === id) selectedId.value = node.parent
  dirty.value = true
}

function updateStepRuntimeState(state) {
  const node = selectedNode.value
  if (!node || node.type !== 'http') return
  const nextRuntimeStates = new Map(stepRuntimeStates.value)
  nextRuntimeStates.set(node.id, state)
  stepRuntimeStates.value = nextRuntimeStates
}

function addElse(conditionId) {
  const result = ensureElse(steps.value, conditionId)
  steps.value = result.steps
  if (result.elseId) selectedId.value = result.elseId
  dirty.value = true
}

function onAddStep(key) {
  addStepVisible.value = false
  addStep(key, null)
}

// 步骤行内“+”新增：after 插到该步骤后面，inside 追加为容器子步骤。
function onAddStepAt({ key, mode, targetId }) {
  addStep(key, targetId, mode)
}

function addStep(key, targetId, mode = 'after') {
  if (key === 'import-request') {
    openImportRequest(targetId, mode)
    return
  }
  if (key === 'import-scenario') {
    openImportScenario(targetId, mode)
    return
  }
  if (key === 'curl') {
    openCurlImport(targetId, mode)
    return
  }
  const node = buildNode(key)
  if (targetId) placeNodeAt(node, targetId, mode)
  else placeNode(node)
}

function buildNode(type) {
  if (type === 'http') {
    return createNode('http', { name: t('apiTesting.scenarioEditor.typeHttp'), config: { request: createRequestDraft() } })
  }
  if (type === 'condition') {
    return createNode('condition', { config: { left: '', operator: 'eq', right: '', right_type: 'string' } })
  }
  if (type === 'for') {
    return createNode('for', {
      config: { count: 1, on_error: 'abort_scenario', max_iterations: 1000 }
    })
  }
  if (type === 'foreach') {
    return createNode('foreach', {
      config: { array_expression: '', on_error: 'abort_scenario', max_iterations: 1000 }
    })
  }
  if (type === 'wait') {
    return createNode('wait', { config: { duration_ms: 1000 } })
  }
  if (type === 'script') {
    return createNode('script', { config: { language: 'javascript', code: '' } })
  }
  if (type === 'scenario_ref') {
    return createNode('scenario_ref', {
      config: { input_mapping: {}, output_mapping: {} },
      sourceType: 'scenario_case'
    })
  }
  return createNode(type, { config: {} })
}

// 根级末尾位置：取当前根步骤最大 position 之后的序号，normalize 后即最后一项。
function nextRootPosition() {
  const roots = childrenOf(steps.value, null)
  return roots.length ? Math.max(...roots.map(step => step.position)) + 1 : 0
}

function placeNode(node) {
  // 底部“添加步骤”是全局入口：始终追加到根级末尾，不跟随当前选中步骤插入。
  node.parent = null
  node.position = nextRootPosition()
  steps.value = normalizePositions([...steps.value, node])
  selectedId.value = node.id
  dirty.value = true
}

// 导入类入口（单接口导入、cURL、场景导入）打开对话框时记录插入位置。
const pendingPlacement = ref(/** @type {{ targetId: string, mode: string } | null} */ (null))

function placeImported(node) {
  if (pendingPlacement.value) {
    const { targetId, mode } = pendingPlacement.value
    pendingPlacement.value = null
    placeNodeAt(node, targetId, mode)
    return
  }
  placeNode(node)
}

// 从步骤行“+”新增：after 插到目标步骤之后，inside 追加为容器的最后一个子步骤。
function placeNodeAt(node, targetId, mode = 'after') {
  const target = steps.value.find(step => step.id === targetId)
  if (!target) {
    placeNode(node)
    return
  }
  if (mode === 'inside' && isContainer(target.type)) {
    const children = childrenOf(steps.value, target.id)
    node.parent = target.id
    node.position = children.length
      ? Math.max(...children.map(step => step.position)) + 1
      : 0
    steps.value = normalizePositions([...steps.value, node])
  } else {
    // If 后面紧跟配对 Else，必须插到 Else 之后，否则会破坏 If/Else 相邻规则。
    const anchor = target.type === 'condition'
      ? (pairedElse(steps.value, target.id) || target)
      : target
    node.parent = anchor.parent ?? null
    node.position = (anchor.position ?? 0) + 1
    childrenOf(steps.value, node.parent).forEach(step => {
      if (step.position >= node.position) step.position += 1
    })
    steps.value = normalizePositions([...steps.value, node])
  }
  selectedId.value = node.id
  // 展开目标节点，避免新增的子步骤被折叠隐藏。
  const next = new Set(collapsed.value)
  next.delete(target.id)
  collapsed.value = next
  dirty.value = true
}

// -- imports -------------------------------------------------------------
async function openImportRequest(targetId = null, mode = 'after') {
  addStepVisible.value = false
  pendingPlacement.value = targetId ? { targetId, mode } : null
  importRequestVisible.value = true
  importRequestId.value = null
  requestKeyword.value = ''
  apiRequestTree.value = []
  try {
    const params = { project: props.scenarioCase?.project, tree: 1 }
    const [collections, requests] = await Promise.all([
      getAllApiCollections(params),
      getAllApiRequests(params)
    ])
    apiRequestTree.value = buildApiRequestTree(collections, requests)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function confirmImportRequest() {
  if (!importRequestId.value) return
  importing.value = true
  try {
    const { data } = await getApiRequest(importRequestId.value)
    const request = createRequestDraft(data)
    const node = createNode('http', {
      name: data.name || t('apiTesting.scenarioEditor.typeHttp'),
      sourceType: 'api_request',
      sourceId: String(data.id),
      config: {
        request,
        // Keep the source name for traceability; the request itself is a local copy.
        source_meta: { name: data.name || '', imported_at: new Date().toISOString() }
      }
    })
    placeImported(node)
    importRequestVisible.value = false
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    importing.value = false
  }
}

function openImportScenario(targetId = null, mode = 'after') {
  addStepVisible.value = false
  pendingPlacement.value = targetId ? { targetId, mode } : null
  importScenarioVisible.value = true
  importScenarioId.value = null
}

async function confirmImportScenario() {
  if (!importScenarioId.value) return
  importing.value = true
  try {
    const { data } = await getScenarioDefinition(importScenarioId.value)
    const external = data.steps || []
    if (!external.length) {
      ElMessage.warning(t('apiTesting.scenarioEditor.importEmpty'))
      return
    }
    const placement = pendingPlacement.value
    let cloned = []
    if (placement && placement.mode === 'inside') {
      cloned = cloneExternalSteps(external, placement.targetId, 0)
    } else {
      const target = placement
        ? steps.value.find(step => step.id === placement.targetId)
        : null
      const parentId = target?.parent ?? null
      const siblings = childrenOf(steps.value, parentId)
      const start = target
        ? Math.max(...siblings.map(step => step.position)) + 1
        : nextRootPosition()
      cloned = cloneExternalSteps(external, parentId, start)
    }
    steps.value = normalizePositions([...steps.value, ...cloned])
    selectedId.value = cloned[0]?.id || selectedId.value
    pendingPlacement.value = null
    importScenarioVisible.value = false
    dirty.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    importing.value = false
  }
}

function cloneExternalSteps(external, parentId, rootPosition) {
  const idMap = new Map(external.map(step => [step.id, createId()]))
  return external.map(step => {
    const config = rewriteStepReferences(cloneDraft(step.config || {}), idMap)
    // Else keeps its pairing through the old UUID, so map it explicitly.
    if (step.type === 'else' && config.if_step_id) {
      config.if_step_id = idMap.get(config.if_step_id) || config.if_step_id
    }
    return {
      id: idMap.get(step.id),
      parent: step.parent ? idMap.get(step.parent) : parentId,
      position: step.parent ? step.position : rootPosition + (step.position || 0),
      type: step.type,
      name: step.name,
      enabled: step.enabled !== false,
      config,
      source_type: step.source_type || null,
      source_id: step.source_id || null
    }
  })
}

function openCurlImport(targetId = null, mode = 'after') {
  addStepVisible.value = false
  pendingPlacement.value = targetId ? { targetId, mode } : null
  curlVisible.value = true
  curlCommand.value = ''
  curlPreview.value = null
}

function parseCurl() {
  curlImportPanelRef.value?.parse()
}

function confirmCurlImport() {
  if (!curlPreview.value) return
  const host = curlPreview.value.model?.baseURL || ''
  const node = createNode('http', {
    name: t('apiTesting.scenarioEditor.typeHttp'),
    config: {
      request: createRequestDraft(curlPreview.value.draft),
      source_meta: host
        ? { name: host, imported_at: new Date().toISOString() }
        : undefined
    }
  })
  placeImported(node)
  curlVisible.value = false
}

// -- save / run ----------------------------------------------------------
function payloadSteps() {
  return flattenTree(steps.value).map(step => {
    const node = {
      id: step.id,
      parent: step.parent,
      position: step.position,
      type: step.type,
      name: step.name,
      enabled: step.enabled,
      config: cloneStepConfig(step.config || {}),
      source_type: step.source_type || null,
      source_id: step.source_id || null
    }
    return node
  })
}

function cloneStepConfig(config) {
  return cloneDraft(config || {})
}

async function buildPayload() {
  const payload = payloadSteps()
  for (const node of payload) {
    if (node.type === 'http') {
      node.config.request = await serializeStepRequest(node.config.request || {})
    }
  }
  return {
    steps: payload,
    variables: cloneDraft(variables.value),
    execution_config: cloneDraft(executionConfig.value)
  }
}

async function save(showSuccess = true) {
  if (saving.value) return false
  const keepStepEditMode = stepEditMode.value
  saving.value = true
  try {
    const payload = await buildPayload()
    await saveScenarioDefinition(props.scenarioCase.id, payload)
    dirty.value = false
    dirtyStepIds.value = new Set()
    await reload()
    // 保存全部步骤后仍停留在当前步骤编辑页，只有返回按钮才退出编辑模式。
    if (keepStepEditMode) stepEditMode.value = true
    if (showSuccess) ElMessage.success(t('apiTesting.scenarioEditor.saved'))
    emit('updated')
    return true
  } catch (error) {
    ElMessage.error(errorMessage(error))
    return false
  } finally {
    saving.value = false
  }
}

// 仅保存当前步骤：不提交场景名称、变量、执行配置或其他步骤的未保存修改。
async function saveStep() {
  const node = selectedNode.value
  if (!node || savingStep.value) return false
  // 新增的步骤（添加 HTTP 请求、cURL 导入、复制/添加 Else 等）尚未落库，
  // 单步骤接口找不到记录会返回“步骤不存在”，此时改用整体保存。
  if (!persistedStepIds.value.has(node.id)) {
    const saved = await save(false)
    if (saved) {
      // 整体保存后的 reload 会退出步骤编辑模式，这里保持用户当前所在的步骤。
      stepEditMode.value = true
      ElMessage.success(t('apiTesting.scenarioEditor.stepSaved'))
    }
    return saved
  }
  savingStep.value = true
  try {
    const config = cloneStepConfig(node.config)
    if (node.type === 'http') {
      config.request = await serializeStepRequest(config.request || {})
    }
    const { data } = await saveScenarioStep(props.scenarioCase.id, node.id, {
      name: node.name,
      enabled: node.enabled,
      config
    })
    // 用服务端校验后的配置回填当前节点，其余节点保持本地状态不动。
    Object.assign(node, {
      name: data.name,
      enabled: data.enabled,
      config: data.config
    })
    const next = new Set(dirtyStepIds.value)
    next.delete(node.id)
    dirtyStepIds.value = next
    ElMessage.success(t('apiTesting.scenarioEditor.stepSaved'))
    return true
  } catch (error) {
    ElMessage.error(errorMessage(error))
    return false
  } finally {
    savingStep.value = false
  }
}

async function run() {
  if (running.value) return
  if (hasUnsavedChanges.value && !(await save(false))) return
  if (!steps.value.length) {
    ElMessage.warning(t('apiTesting.scenarioEditor.emptyTree'))
    return
  }
  running.value = true
  execution.value = null
  resultSteps.value = []
  afterSequence = 0
  viewedExecutionId.value = null
  executionViewPreviousStepEditMode.value = stepEditMode.value
  executionViewVisible.value = true
  try {
    const { data } = await executeScenarioCase(props.scenarioCase.id, {
      environment_id: props.environmentId,
      async: true
    })
    const executionId = data.execution_id
    if (!executionId) {
      // Legacy synchronous response (no step tree).
      running.value = false
      execution.value = data
      ElMessage.success(t('apiTesting.scenarioEditor.runFinished'))
      emit('updated')
      return
    }
    execution.value = { id: executionId, status: data.status || 'PENDING', passed: 0, failed: 0, skipped: 0, total: 0 }
    viewedExecutionId.value = executionId
    loadExecutionHistory()
    pollExecution(executionId)
  } catch (error) {
    running.value = false
    ElMessage.error(errorMessage(error))
  }
}

async function pollExecution(executionId) {
  try {
    const { data } = await getExecutionSteps(executionId, { after_sequence: afterSequence })
    // 用户查看历史记录时，后台轮询只能更新当前执行状态，不能覆盖右侧历史详情。
    if (String(viewedExecutionId.value) === String(executionId)) {
      execution.value = {
        id: executionId,
        status: data.status,
        conclusion: data.conclusion,
        passed: data.passed,
        failed: data.failed,
        skipped: data.skipped,
        total: data.total
      }
      if (data.steps?.length) {
        resultSteps.value = [...resultSteps.value, ...data.steps]
        afterSequence = data.steps[data.steps.length - 1].sequence
      }
    }
    if (['PENDING', 'RUNNING'].includes(data.status)) {
      pollTimer = setTimeout(() => pollExecution(executionId), 900)
    } else {
      running.value = false
      loadExecutionHistory()
      emit('updated')
    }
  } catch (error) {
    running.value = false
    ElMessage.error(errorMessage(error))
  }
}

async function stop() {
  if (!execution.value?.id) return
  try {
    await cancelExecution(execution.value.id)
    ElMessage.info(t('apiTesting.scenarioEditor.cancelRequested'))
    // Keep polling: the worker only reaches a terminal state at the next
    // step/loop/wait boundary.
    clearTimeout(pollTimer)
    pollExecution(execution.value.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
    running.value = false
  }
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}

// -- execution records ---------------------------------------------------
async function loadExecutionHistory() {
  historyLoading.value = true
  try {
    const { data } = await getScenarioCaseExecutions({
      scenario_case: props.scenarioCase.id, page: 1, page_size: 50
    })
    executionHistory.value = data?.results || data || []
  } catch {
    // History is optional; the editor keeps working.
  } finally {
    historyLoading.value = false
  }
}

async function openExecutionView() {
  // 执行记录沿用步骤编辑页的全屏切换方式，返回时恢复进入前的视图。
  executionViewPreviousStepEditMode.value = stepEditMode.value
  executionViewVisible.value = true
  await loadExecutionHistory()
  const live = running.value ? execution.value?.id : null
  const target = live || viewedExecutionId.value || executionHistory.value[0]?.id
  if (target) await loadExecutionDetail(target)
}

function exitExecutionView() {
  executionViewVisible.value = false
  stepEditMode.value = executionViewPreviousStepEditMode.value
}

async function refreshExecutionView() {
  await loadExecutionHistory()
  if (viewedExecutionId.value) await loadExecutionDetail(viewedExecutionId.value)
}

async function deleteExecution(item) {
  try {
    await ElMessageBox.confirm(
      t('apiTesting.scenarioEditor.confirmDeleteExecution'),
      t('apiTesting.common.confirmDelete'),
      { type: 'warning' }
    )
    await deleteScenarioExecution(item.id)
    ElMessage.success(t('apiTesting.scenarioEditor.executionDeleted'))

    const wasViewed = String(item.id) === String(viewedExecutionId.value)
    await loadExecutionHistory()
    if (wasViewed) {
      viewedExecutionId.value = null
      execution.value = null
      resultSteps.value = []
      const nextExecution = executionHistory.value[0]
      if (nextExecution) await loadExecutionDetail(nextExecution.id)
    }
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  }
}

async function loadExecutionDetail(id) {
  const requestToken = ++executionDetailRequestToken
  if (running.value && String(id) === String(execution.value?.id)) {
    // Live execution keeps streaming through pollExecution.
    viewedExecutionId.value = id
    return
  }
  viewedExecutionId.value = id
  // 切换记录时先清空旧结果，避免请求期间继续展示上一条执行记录。
  resultSteps.value = []
  execution.value = null
  try {
    const { data } = await getExecutionSteps(id, {})
    if (requestToken !== executionDetailRequestToken) return
    execution.value = {
      id,
      status: data.status,
      conclusion: data.conclusion,
      passed: data.passed,
      failed: data.failed,
      skipped: data.skipped,
      total: data.total
    }
    resultSteps.value = data.steps || []
    afterSequence = resultSteps.value.length
      ? resultSteps.value[resultSteps.value.length - 1].sequence
      : 0
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function historyStatusType(item) {
  const status = item.conclusion || item.status
  if (status === 'PASSED' || status === 'COMPLETED') return 'success'
  if (status === 'FAILED') return 'danger'
  if (status === 'CANCELLED' || status === 'PENDING') return 'info'
  return 'warning'
}

function formatDate(value) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : ''
}

function methodType(method) {
  return { GET: 'success', POST: 'warning', PUT: 'primary', DELETE: 'danger', PATCH: 'info' }[method] || 'info'
}

function errorMessage(error) {
  const data = error?.response?.data
  const message = data?.errors
    ? flattenErrors(data.errors)
    : data?.error || data?.detail || error?.message || ''
  if (!message) return t('apiTesting.common.failed')
  return te(message) ? t(message) : String(message)
}

function flattenErrors(errors) {
  if (typeof errors === 'string') return errors
  if (Array.isArray(errors)) return errors.join('；')
  if (errors && typeof errors === 'object') {
    return Object.entries(errors)
      .map(([key, value]) => `${key}: ${flattenErrors(value)}`)
      .join('；')
  }
  return String(errors)
}

// 步骤树与编辑区默认五五分，用户仍可拖拽调整（最大 70%）
onMounted(() => {
  nextTick(() => {
    const width = editorMainRef.value?.clientWidth || 0
    editorMainWidth.value = width
    if (width > 0) treeWidth.value = Math.round(width / 2)
    if (window.ResizeObserver && editorMainRef.value) {
      editorResizeObserver = new ResizeObserver(entries => {
        const nextWidth = Math.round(entries[0]?.contentRect?.width || 0)
        editorMainWidth.value = nextWidth
        if (nextWidth && treeWidth.value > Math.round(nextWidth * 0.7)) {
          treeWidth.value = Math.round(nextWidth * 0.7)
        }
      })
      editorResizeObserver.observe(editorMainRef.value)
    }
  })
})

onBeforeUnmount(() => {
  editorResizeObserver?.disconnect()
  stopPolling()
})
</script>

<style scoped>
.scenario-editor {
  display: flex; flex-direction: column; height: 100%; min-height: 560px;
  background: #fff; overflow: hidden;
}
.scenario-header { padding: 0 10px 10px; border-bottom: 1px solid #f0f2f5; }
.scenario-title-row { display: flex; align-items: center; gap: 10px; }
.scenario-id { flex: none; color: #909399; font-size: 14px; }
.title-input :deep(.el-input__wrapper) { box-shadow: none; padding: 0 4px; }
.title-input :deep(.el-input__inner) { font-size: 18px; font-weight: 600; color: #303133; }
.description-input { margin-top: 4px; }
.description-input :deep(.el-textarea__inner) {
  box-shadow: none; padding: 2px 4px; font-size: 13px; color: #606266; resize: none;
}
.scenario-meta { display: flex; gap: 16px; margin-top: 4px; font-size: 12px; color: #909399; }
.editor-toolbar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 5px; border-bottom: 1px solid #ebeef5; background: #fafbfd;
}
.toolbar-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.toolbar-right { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.environment-select { width: 180px; }
.editor-main { flex: 1; min-height: 0; display: flex; }
.tree-pane { display: flex; flex-direction: column; height: 100%; border-right: 1px solid #f0f2f5; }

/* 列表模式：隐藏步骤编辑区，场景树占满整宽 */
.editor-main.overview-mode :deep(.split-pane-second),
.editor-main.overview-mode :deep(.split-pane-divider) { display: none; }
.editor-main.overview-mode :deep(.split-pane-first) { width: 100% !important; flex: 1 1 auto; }
.editor-main.overview-mode .tree-pane { border-right: none; }

/* 步骤编辑模式：只保留标题栏 */
.step-edit-toolbar {
  display: flex; align-items: center; gap: 10px;
  padding: 5px; border-bottom: 1px solid #ebeef5; background: #fafbfd;
}
.step-edit-title {
  font-size: 15px; font-weight: 600; color: #303133;
  max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.step-edit-spacer { flex: 1; }
.tree-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; font-size: 13px; font-weight: 600; color: #303133;
  border-bottom: 1px solid #f0f2f5;
}
.tree-count { font-size: 12px; color: #909399; font-weight: 400; }
.tree-scroll { flex: 1; min-height: 0; }
.tree-list { padding: 6px 6px 12px; }
.tree-footer { border-top: 1px solid #f0f2f5; padding: 6px 8px; }
.add-step-button { color: var(--el-color-primary); }
.editor-pane { flex: 1; padding: 0 10px; min-width: 0; overflow: auto; }
.import-search { margin-bottom: 12px; }
.import-request-tree :deep(.el-tree-node__content) { height: auto; padding: 2px 4px; border-radius: 6px; }
.import-tree-node {
  display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0;
  padding: 5px 4px; border-radius: 6px; font-size: 13px;
}
.import-tree-node.is-selected { background: var(--el-color-primary-light-9); box-shadow: inset 0 0 0 1px var(--el-color-primary); }
/* 文件夹样式与接口管理页左侧接口树保持一致 */
.import-folder-icon { font-size: 16px; color: #606266; flex: none; }
.import-folder-name { font-size: 14px; font-weight: 400; }
.import-tree-node:hover .import-name { color: var(--el-color-primary); }
.import-name { font-weight: 500; color: #303133; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.import-url { margin-left: auto; color: #909399; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 55%; flex: none; }
.execution-toolbar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; border-bottom: 1px solid #ebeef5; background: #fafbfd;
}
.execution-title { font-size: 15px; font-weight: 600; color: #303133; }
.execution-id { color: #909399; font-size: 13px; }
.execution-view-body { display: flex; flex: 1; min-height: 0; gap: 16px; padding: 16px; }
.execution-history-pane {
  display: flex; flex: 0 0 280px; flex-direction: column;
  border-right: 1px solid #f0f2f5; padding-right: 12px;
}
.execution-history-pane :deep(.el-scrollbar) { flex: 1; min-height: 0; }
.history-header {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 8px;
}
.history-item {
  padding: 8px 10px; border-radius: 8px; cursor: pointer;
  border: 1px solid transparent; margin-bottom: 6px;
}
.history-item:hover { background: #f7f8fc; }
.history-item.active { background: var(--el-fill-color-light); border-color: var(--el-border-color); }
.history-main-line { display: flex; align-items: center; gap: 8px; min-width: 0; }
.history-main-line :deep(.el-button) { display: none; margin-left: auto; padding: 2px; flex: none; }
.history-item:hover .history-main-line :deep(.el-button) { display: inline-flex; }
.history-stat { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #909399; font-size: 12px; }
.history-meta {
  display: flex; margin-top: 4px; font-size: 12px; color: #909399;
}
.history-count { font-size: 12px; color: #909399; font-weight: 400; }
.execution-detail-pane { flex: 1; min-width: 0; overflow: auto; }
.execution-detail-pane :deep(.step-result-panel) { border-top: none; padding: 0; }
</style>
