<template>
  <div class="execution-detail">
    <!-- 页面头部：扁平、克制，Grafana 式仪表盘标题区 -->
    <header class="plan-header">
      <div class="plan-header__title-row">
        <h1 class="plan-header__title">{{ testPlan.name }}</h1>
        <span class="status-chip" :class="headerStatusClass">
          <span class="status-chip__dot"></span>
          {{ headerStatusText }}
        </span>
        <span v-if="testPlan.version" class="version-chip">
          <el-icon class="version-chip__icon"><Stamp /></el-icon>
          {{ testPlan.version }}
        </span>
        <div class="plan-header__actions">
          <el-button size="small" :icon="Link" @click="openLinkDialog">
            {{ $t('execution.linkCases') }}
          </el-button>
        </div>
      </div>
      <div class="plan-header__meta">
        <span class="meta-item">
          <el-icon class="meta-item__icon"><FolderOpened /></el-icon>
          <template v-if="projectNames.length > 0">
            {{ projectNames.join(', ') }}
          </template>
          <template v-else>{{ $t('execution.noProject') }}</template>
        </span>
        <span v-if="testPlan.created_at" class="meta-item meta-dim">
          {{ $t('execution.createdAt') }} · {{ formatDate(testPlan.created_at) }}
        </span>
      </div>
    </header>

    <!-- 测试执行区 -->
    <template v-if="testPlan.test_runs && testPlan.test_runs.length > 0">
      <section v-for="run in testPlan.test_runs" :key="run.id" class="run-card">
        <!-- 运行头部：指标条 + 批量保存 -->
        <div class="run-card__head">
          <div class="run-metrics">
            <div class="run-metrics__stat">
              <span class="run-metrics__label">{{ $t('execution.passRate') }}</span>
              <span class="run-metrics__value">{{ passRate(run.progress) }}%</span>
            </div>
            <div class="run-metrics__stat">
              <span class="run-metrics__label">{{ $t('execution.testedCount') }}</span>
              <span class="run-metrics__value">
                {{ run.progress.tested || 0 }}/{{ run.progress.total }}
              </span>
            </div>
            <div class="run-metrics__track" :title="progressTooltip(run.progress)">
              <div
                v-for="segment in progressSegments(run.progress)"
                :key="segment.key"
                class="run-metrics__segment"
                :class="segment.class"
                :style="{ flexGrow: segment.value }">
              </div>
            </div>
            <div class="run-metrics__legend">
              <span class="legend legend--passed">{{ $t('execution.passed') }} {{ run.progress.passed }}</span>
              <span class="legend legend--failed">{{ $t('execution.failed') }} {{ run.progress.failed }}</span>
              <span class="legend legend--blocked">{{ $t('execution.blocked') }} {{ run.progress.blocked }}</span>
              <span v-if="run.progress.retest" class="legend legend--retest">
                {{ $t('execution.retest') }} {{ run.progress.retest }}
              </span>
              <span class="legend legend--untested">{{ $t('execution.untested') }} {{ run.progress.untested }}</span>
            </div>
            <el-button
              v-if="dirtyCount(run) > 0"
              class="run-metrics__action"
              size="small"
              type="primary"
              :loading="batchRunId === run.id"
              @click="saveAllInRun(run)">
              {{ $t('execution.batchSaveAll') }} ({{ dirtyCount(run) }})
            </el-button>
          </div>
        </div>

        <!-- 模块树 + 用例列表 -->
        <div class="run-body">
          <ResizableSplitPane
            v-model="sidebarWidth"
            :min-size="200"
            :max-size="420"
            :second-min-size="520"
            storage-key="execution-module-sidebar-width">
            <template #first>
              <aside class="module-sidebar">
                <div class="sidebar-header">
                  <span class="sidebar-title">{{ $t('execution.moduleTree') }}</span>
                  <el-input
                    v-model="ensureUi(run).moduleSearch"
                    size="small"
                    clearable
                    :placeholder="$t('execution.searchModules')">
                    <template #prefix><el-icon><Search /></el-icon></template>
                  </el-input>
                </div>
                <div class="collection-tree">
                  <div
                    class="tree-entry"
                    :class="{ active: ensureUi(run).module === '' }"
                    @click="selectModule(run, '')">
                    <el-icon><Files /></el-icon>
                    <span class="tree-entry-label">{{ $t('execution.allCases') }}</span>
                    <span class="tree-count">{{ run.progress.total }}</span>
                  </div>
                  <el-tree
                    v-if="filteredModuleTree(run).length"
                    :data="filteredModuleTree(run)"
                    :props="{ label: 'name', children: 'children' }"
                    node-key="id"
                    default-expand-all
                    :expand-on-click-node="false"
                    @node-click="node => selectModule(run, String(node.id))">
                    <template #default="{ data }">
                      <div class="tree-node" :class="{ 'is-active': ensureUi(run).module === String(data.id) }">
                        <el-icon><Folder /></el-icon>
                        <span class="node-label" :title="data.name">{{ data.name }}</span>
                        <span class="tree-count">{{ data.count }}</span>
                      </div>
                    </template>
                  </el-tree>
                  <div
                    v-if="unassignedCount(run) > 0"
                    class="tree-entry root-module-level"
                    :class="{ active: ensureUi(run).module === 'none' }"
                    @click="selectModule(run, 'none')">
                    <el-icon><FolderOpened /></el-icon>
                    <span class="tree-entry-label">{{ $t('execution.unassignedModule') }}</span>
                    <span class="tree-count">{{ unassignedCount(run) }}</span>
                  </div>
                </div>
              </aside>
            </template>
            <template #second>
              <div class="run-main">
                <div class="run-main__head">
                  <div class="run-main__actions">
                    <el-button
                      size="small"
                      :disabled="selectedCount(run) === 0"
                      @click="openAssignDialog(run)">
                      {{ $t('execution.batchAssign') }}
                    </el-button>
                    <el-button
                      size="small"
                      :disabled="selectedCount(run) === 0"
                      @click="openStatusDialog(run)">
                      {{ $t('execution.batchChangeStatus') }}
                    </el-button>
                    <el-button
                      size="small"
                      :disabled="selectedCount(run) === 0 || isDeleting"
                      @click="batchDeleteCases(run)">
                      {{ $t('execution.batchRemove') }}
                    </el-button>
                    <template v-if="selectedCount(run) > 0">
                      <span class="run-main__selected">
                        {{ $t('execution.selectedCount', { count: selectedCount(run) }) }}
                      </span>
                      <el-button link type="primary" size="small" @click="clearSelection(run)">
                        {{ $t('execution.clearSelection') }}
                      </el-button>
                    </template>
                  </div>
                  <div class="run-main__tools">
                    <el-input
                      v-model="ensureUi(run).search"
                      class="tool-search"
                      size="small"
                      clearable
                      :placeholder="$t('execution.searchCases')">
                      <template #prefix><el-icon><Search /></el-icon></template>
                    </el-input>
                    <el-select
                      v-model="ensureUi(run).executor"
                      class="tool-select"
                      size="small"
                      clearable
                      filterable
                      :placeholder="$t('execution.filterExecutor')">
                      <el-option
                        v-for="name in executorOptions(run)"
                        :key="name"
                        :label="name"
                        :value="name" />
                    </el-select>
                    <el-select
                      v-model="ensureUi(run).status"
                      class="tool-select"
                      size="small"
                      clearable
                      :placeholder="$t('execution.executionResult')">
                      <el-option :label="$t('execution.passed')" value="passed" />
                      <el-option :label="$t('execution.failed')" value="failed" />
                      <el-option :label="$t('execution.blocked')" value="blocked" />
                      <el-option :label="$t('execution.retest')" value="retest" />
                      <el-option :label="$t('execution.untested')" value="untested" />
                    </el-select>
                    <el-select
                      v-model="ensureUi(run).sort"
                      class="tool-select tool-select--sort"
                      size="small">
                      <el-option :label="$t('execution.sortId')" value="id" />
                      <el-option :label="$t('execution.sortPriority')" value="priority" />
                      <el-option :label="$t('execution.sortStatus')" value="status" />
                      <el-option :label="$t('execution.sortExecutedAt')" value="executed_at" />
                    </el-select>
                  </div>
                </div>

                <div class="run-main__table">
                  <el-table
                    :ref="el => setTableRef(run.id, el)"
                    :data="visibleCasesMap[run.id] || []"
                    height="100%"
                    style="width: 100%"
                    class="execution-table"
                    :row-class-name="rowClassName"
                    @selection-change="selection => handleSelectionChange(run, selection)"
                    @row-click="openCaseExecution"
                    :row-key="(row) => row.id">
                    <el-table-column type="selection" width="30" />
                    <el-table-column :label="$t('execution.testcaseId')" width="60">
                      <template #default="scope">{{ scope.row.testcase?.id ?? '-' }}</template>
                    </el-table-column>
                    <el-table-column :label="$t('execution.testcaseTitle')" min-width="180" show-overflow-tooltip>
                      <template #default="scope">
                        {{ scope.row.testcase?.title || scope.row.testcase || '-' }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="$t('execution.maintainer')" width="100">
                      <template #default="scope">{{ scope.row.testcase?.author?.username || '-' }}</template>
                    </el-table-column>
                    <el-table-column :label="$t('execution.testcasePriority')" width="85">
                      <template #default="scope">
                        <span class="priority-tag" :class="'priority-tag--' + (scope.row.testcase?.priority || 'medium')">
                          {{ getPriorityText(scope.row.testcase?.priority) }}
                        </span>
                      </template>
                    </el-table-column>
                    <el-table-column :label="$t('execution.testcaseType')" width="90">
                      <template #default="scope">{{ getTestTypeText(scope.row.testcase?.test_type) }}</template>
                    </el-table-column>
                    <el-table-column :label="$t('execution.executedBy')" width="100">
                      <template #default="scope">{{ scope.row.executed_by?.username || '-' }}</template>
                    </el-table-column>
                    <el-table-column :label="$t('execution.executionResult')" width="90">
                      <template #default="scope">
                        <span class="case-status" :class="'case-status--' + scope.row.status">
                          <span class="case-status__dot"></span>{{ getStatusText(scope.row.status) }}
                        </span>
                      </template>
                    </el-table-column>
                    <el-table-column :label="$t('execution.lastExecutedAt')" width="140">
                      <template #default="scope">{{ formatDate(scope.row.executed_at) }}</template>
                    </el-table-column>
                    <template #empty>
                      <div class="table-empty">{{ $t('execution.noCasesInModule') }}</div>
                    </template>
                  </el-table>
                </div>
              </div>
            </template>
          </ResizableSplitPane>
        </div>
      </section>
    </template>

    <!-- 空态 -->
    <div v-else-if="loaded" class="empty-state">
      {{ $t('execution.noRuns') }}
    </div>

    <!-- 关联用例：复用计划创建时的用例选择弹窗 -->
    <TestCaseSelectorDialog
      v-model="linkDialogVisible"
      :projects="planProjects"
      :selected-ids="linkedCaseIds"
      :selected-cases="testPlan.testcases || []"
      @confirm="confirmLinkCases" />

    <!-- 批量更改执行人 -->
    <el-dialog
      :title="$t('execution.batchAssign')"
      v-model="assignDialogVisible"
      width="420px"
      align-center>
      <div class="batch-dialog-body">
        <p class="batch-dialog-hint">
          {{ $t('execution.batchSelectedHint', { count: batchSelection.length }) }}
        </p>
        <el-select
          v-model="assignUserId"
          filterable
          clearable
          :loading="membersLoading"
          :placeholder="$t('execution.selectExecutor')"
          style="width: 100%">
          <el-option
            v-for="member in memberOptions"
            :key="member.id"
            :label="member.username"
            :value="member.id" />
        </el-select>
      </div>
      <template #footer>
        <el-button @click="assignDialogVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="batchOperating" :disabled="!assignUserId" @click="confirmAssign">
          {{ $t('common.confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量更改执行结果 -->
    <el-dialog
      :title="$t('execution.batchChangeStatus')"
      v-model="statusDialogVisible"
      width="420px"
      align-center>
      <div class="batch-dialog-body">
        <p class="batch-dialog-hint">
          {{ $t('execution.batchSelectedHint', { count: batchSelection.length }) }}
        </p>
        <el-select
          v-model="batchStatus"
          :placeholder="$t('execution.selectResult')"
          style="width: 100%">
          <el-option
            v-for="option in statusOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value" />
        </el-select>
      </div>
      <template #footer>
        <el-button @click="statusDialogVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="batchOperating" @click="confirmBatchStatus">
          {{ $t('common.confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 历史记录对话框 -->
    <el-dialog
      :title="$t('execution.executionHistory')"
      v-model="historyDialogVisible"
      width="80%"
      align-center>
      <el-table :data="currentCaseHistory" style="width: 100%">
        <el-table-column prop="status" :label="$t('execution.status')" width="100">
          <template #default="scope">
            <el-tag :type="getStatusType(scope.row.status)">
              {{ getStatusText(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="comments" :label="$t('execution.comments')" show-overflow-tooltip />
        <el-table-column prop="executed_by.username" :label="$t('execution.executedBy')" width="120" />
        <el-table-column prop="executed_at" :label="$t('execution.executedAt')" width="180">
          <template #default="scope">
            {{ formatDate(scope.row.executed_at) }}
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog
      v-model="caseExecutionDialogVisible"
      :title="$t('execution.caseExecutionDetail')"
      width="min(1440px, 96vw)"
      align-center
      class="case-execution-dialog"
      :close-on-click-modal="false">
      <template #header>
        <div class="case-dialog-header">
          <div class="case-dialog-heading">
            <span class="case-dialog-title">{{ $t('execution.caseExecutionDetail') }}</span>
            <span v-if="currentCase" class="case-dialog-current-title">
              {{ currentCase.testcase?.title || currentCase.testcase }}
            </span>
          </div>
          <div v-if="currentCase" class="case-dialog-toolbar">
            <el-button
              class="case-nav-button"
              :icon="ArrowUp"
              :disabled="currentCaseIndex <= 0"
              @click="navigateCase(-1)" />
            <span class="case-dialog-count">{{ currentCaseIndex + 1 }} / {{ allCases.length }}</span>
            <el-button
              class="case-nav-button"
              :icon="ArrowDown"
              :disabled="currentCaseIndex >= allCases.length - 1"
              @click="navigateCase(1)" />
            <el-divider direction="vertical" />
            <el-button
              type="primary"
              :loading="savingId === currentCase?.id"
              :disabled="!isRowDirty(currentCase)"
              @click="saveCurrentCase">
              {{ $t('common.save') }}
            </el-button>
            <el-button
              type="primary"
              :loading="savingId === currentCase?.id"
              :disabled="!isRowDirty(currentCase)"
              @click="saveCurrentAndNext">
              {{ $t('execution.saveAndNext') }}
            </el-button>
          </div>
        </div>
      </template>
      <div v-if="currentCase" class="case-execution-layout">
        <section class="case-information">
          <div class="case-title-row">
            <div>
              <div class="case-id">{{ $t('execution.testcaseId') }} {{ currentCase.testcase?.id }}</div>
              <h2>{{ currentCase.testcase?.title || currentCase.testcase }}</h2>
            </div>
            <el-button link type="primary" :icon="Clock" @click="viewCaseHistory(currentCase)">
              {{ $t('execution.viewHistory') }}
            </el-button>
          </div>
          <div class="case-meta-grid">
            <div><span>{{ $t('execution.testcasePriority') }}</span>{{ getPriorityText(currentCase.testcase?.priority) }}</div>
            <div><span>{{ $t('execution.testcaseType') }}</span>{{ getTestTypeText(currentCase.testcase?.test_type) }}</div>
            <div><span>{{ $t('execution.maintainer') }}</span>{{ currentCase.testcase?.author?.username || '-' }}</div>
            <div><span>{{ $t('execution.project') }}</span>{{ currentCase.testcase?.project_name || '-' }}</div>
          </div>
          <div class="case-section">
            <h3>{{ $t('execution.preconditions') }}</h3>
            <p>{{ currentCase.testcase?.preconditions || $t('execution.noData') }}</p>
          </div>
          <div class="case-section">
            <h3>{{ $t('execution.steps') }}</h3>
            <p>{{ currentCase.testcase?.steps || $t('execution.noData') }}</p>
          </div>
          <div class="case-section">
            <h3>{{ $t('execution.expectedResult') }}</h3>
            <p>{{ currentCase.testcase?.expected_result || $t('execution.noData') }}</p>
          </div>
          <div v-if="currentCase.testcase?.description" class="case-section">
            <h3>{{ $t('execution.testcaseDescription') }}</h3>
            <p>{{ currentCase.testcase.description }}</p>
          </div>
        </section>
        <aside class="case-result-panel">
          <h3>{{ $t('execution.executionResult') }}</h3>
          <el-radio-group v-model="currentCase.status" class="result-status-group">
            <el-radio-button value="passed" class="result-option--passed">{{ $t('execution.passed') }}</el-radio-button>
            <el-radio-button value="failed" class="result-option--failed">{{ $t('execution.failed') }}</el-radio-button>
            <el-radio-button value="blocked" class="result-option--blocked">{{ $t('execution.blocked') }}</el-radio-button>
            <el-radio-button value="retest" class="result-option--retest">{{ $t('execution.retest') }}</el-radio-button>
          </el-radio-group>
          <div class="result-field">
            <label>{{ $t('execution.actualResult') }}</label>
            <el-input v-model="currentCase.actual_result" type="textarea" :rows="6" resize="vertical" />
          </div>
          <div class="result-field">
            <label>{{ $t('execution.comments') }}</label>
            <el-input v-model="currentCase.comments" type="textarea" :rows="5" resize="vertical" :placeholder="$t('execution.commentsPlaceholder')" />
          </div>
          <div class="last-execution-meta">
            <div><span>{{ $t('execution.lastExecutedAt') }}</span>{{ formatDate(currentCase.executed_at) }}</div>
            <div><span>{{ $t('execution.executedBy') }}</span>{{ currentCase.executed_by?.username || '-' }}</div>
          </div>
        </aside>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Clock, Stamp, Folder, Files, FolderOpened, Search, Link, ArrowUp, ArrowDown
} from '@element-plus/icons-vue'
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import TestCaseSelectorDialog from '@/components/TestCaseSelectorDialog.vue'
import { useUserStore } from '@/stores/user'
import api from '@/utils/api'

const { t } = useI18n()
const userStore = useUserStore()

const route = useRoute()
const testPlan = ref({})
const historyDialogVisible = ref(false)
const currentCaseHistory = ref([])
const caseExecutionDialogVisible = ref(false)
const currentCaseIndex = ref(-1)
const isDeleting = ref(false)
const loaded = ref(false)
const sidebarWidth = ref(260)

// 每个 run 独立的模块树 UI 状态：{ module, moduleSearch, search, status, sort }
const uiState = reactive({})
// 每个 run 的表格选中项：runId -> runCase[]
const selectionByRun = reactive({})
// 项目模块树缓存：projectId -> 模块树
const moduleTreeByProject = ref({})
// 每个 run 的表格实例，用于清空选择
const tableRefs = {}

// 批量操作（更改执行人 / 更改执行结果）
const batchRun = ref(null)
const assignDialogVisible = ref(false)
const assignUserId = ref(null)
const memberOptions = ref([])
const membersLoading = ref(false)
const statusDialogVisible = ref(false)
const batchStatus = ref('passed')
const batchOperating = ref(false)
const statusOptions = computed(() => [
  { value: 'passed', label: t('execution.passed') },
  { value: 'failed', label: t('execution.failed') },
  { value: 'blocked', label: t('execution.blocked') },
  { value: 'retest', label: t('execution.retest') },
  { value: 'untested', label: t('execution.untested') }
])
const batchSelection = computed(() => (batchRun.value ? (selectionByRun[batchRun.value.id] || []) : []))

// 关联用例
const linkDialogVisible = ref(false)
const linkSaving = ref(false)
// 计划绑定项目：[{ id, name }]，详情接口直接给，无需从 runs / testcases 里拼
const planProjects = computed(() => testPlan.value.projects || [])
const projectNames = computed(() => planProjects.value.map(project => project.name))
// 已关联用例 id：直接取 run_cases 里的 testcase，接口不再单独下发 testcases 字段
const linkedCaseIds = computed(() => {
  const ids = new Set()
  for (const run of testPlan.value.test_runs || []) {
    for (const runCase of run.run_cases || []) {
      if (runCase.testcase?.id) ids.add(runCase.testcase.id)
    }
  }
  return [...ids]
})

// 已保存快照：row.id -> { status, comments }，用于判断某行是否有未提交的改动
const savedState = ref({})
// 单行保存中的用例 id；批量保存中的 run.id
const savingId = ref(null)
const batchRunId = ref(null)

const allCases = computed(() => (testPlan.value.test_runs || []).flatMap(run =>
  (run.run_cases || []).map(runCase => ({ run, runCase }))
))
const currentCase = computed(() => allCases.value[currentCaseIndex.value]?.runCase || null)
const currentRun = computed(() => allCases.value[currentCaseIndex.value]?.run || null)

// 页面头部的整体状态：聚合所有 run 的进度
const overallProgress = computed(() => {
  const agg = { total: 0, passed: 0, failed: 0, blocked: 0, retest: 0, untested: 0, tested: 0, progress: 0 }
  for (const run of testPlan.value.test_runs || []) {
    const p = run.progress || {}
    agg.total += p.total || 0
    agg.passed += p.passed || 0
    agg.failed += p.failed || 0
    agg.blocked += p.blocked || 0
    agg.retest += p.retest || 0
    agg.untested += p.untested || 0
  }
  agg.tested = agg.passed + agg.failed + agg.blocked + agg.retest
  agg.progress = agg.total > 0 ? Math.round((agg.tested / agg.total) * 1000) / 10 : 0
  return agg
})

// 通过率：已执行用例中通过的比例
const passRate = (progress) => {
  const tested = (progress?.passed || 0) + (progress?.failed || 0) +
    (progress?.blocked || 0) + (progress?.retest || 0)
  if (!tested) return 0
  return Math.round(((progress.passed || 0) / tested) * 1000) / 10
}

// 进度条分段：按执行结果占比着色（通过/失败/阻塞/重测/未测试）
const PROGRESS_SEGMENTS = [
  { key: 'passed', class: 'seg--passed' },
  { key: 'failed', class: 'seg--failed' },
  { key: 'blocked', class: 'seg--blocked' },
  { key: 'retest', class: 'seg--retest' },
  { key: 'untested', class: 'seg--untested' }
]

const progressSegments = (progress) => {
  if (!progress || !progress.total) return []
  return PROGRESS_SEGMENTS
    .map(segment => ({ ...segment, value: progress[segment.key] || 0 }))
    .filter(segment => segment.value > 0)
}

const progressTooltip = (progress) => {
  if (!progress || !progress.total) return ''
  return PROGRESS_SEGMENTS
    .filter(segment => (progress[segment.key] || 0) > 0)
    .map(segment => `${t(`execution.${segment.key}`)} ${progress[segment.key]}`)
    .join(' · ')
}

// —— 模块树：以 run 内用例的实际归属构建，只保留有数据的模块 ——
const initRunUi = () => {
  for (const run of testPlan.value.test_runs || []) {
    if (!uiState[run.id]) {
      uiState[run.id] = { module: '', moduleSearch: '', search: '', status: '', executor: '', sort: 'id' }
    }
  }
}

const ensureUi = (run) => {
  if (!uiState[run.id]) {
    uiState[run.id] = { module: '', moduleSearch: '', search: '', status: '', executor: '', sort: 'id' }
  }
  return uiState[run.id]
}

const selectModule = (run, moduleId) => {
  ensureUi(run).module = moduleId
}

const setTableRef = (runId, el) => {
  if (el) tableRefs[runId] = el
}

const countByModule = (run) => {
  const counts = new Map()
  let unassigned = 0
  for (const runCase of run.run_cases || []) {
    const moduleId = runCase.testcase?.module?.id
    if (moduleId) counts.set(moduleId, (counts.get(moduleId) || 0) + 1)
    else unassigned++
  }
  return { counts, unassigned }
}

const buildModuleTree = (nodes, counts) => {
  const result = []
  for (const node of nodes || []) {
    const children = buildModuleTree(node.children, counts)
    const own = counts.get(node.id) || 0
    if (!own && !children.length) continue
    result.push({
      id: node.id,
      name: node.name,
      own,
      count: own + children.reduce((sum, child) => sum + child.count, 0),
      children
    })
  }
  return result
}

const runModuleTree = (run) => {
  const raw = moduleTreeByProject.value[run.project?.id] || []
  if (!raw.length) return []
  const { counts } = countByModule(run)
  return buildModuleTree(raw, counts)
}

const filterModuleNodes = (nodes, keyword) => {
  const result = []
  for (const node of nodes) {
    const children = filterModuleNodes(node.children || [], keyword)
    if (node.name.toLowerCase().includes(keyword) || children.length) {
      result.push({ ...node, children })
    }
  }
  return result
}

const filteredModuleTree = (run) => {
  const tree = runModuleTree(run)
  const keyword = (ensureUi(run).moduleSearch || '').trim().toLowerCase()
  return keyword ? filterModuleNodes(tree, keyword) : tree
}

const unassignedCount = (run) => countByModule(run).unassigned

const findModuleNode = (nodes, id) => {
  for (const node of nodes) {
    if (node.id === id) return node
    const found = findModuleNode(node.children || [], id)
    if (found) return found
  }
  return null
}

const collectModuleIds = (node, bucket = new Set()) => {
  bucket.add(node.id)
  for (const child of node.children || []) collectModuleIds(child, bucket)
  return bucket
}

// 当前模块（含子模块）下的用例
const casesInModule = (run) => {
  const all = run.run_cases || []
  const { module } = ensureUi(run)
  if (!module) return all
  if (module === 'none') return all.filter(runCase => !runCase.testcase?.module?.id)
  const node = findModuleNode(runModuleTree(run), Number(module))
  if (!node) return all
  const ids = collectModuleIds(node)
  return all.filter(runCase => ids.has(runCase.testcase?.module?.id))
}

// —— 关联用例：复用用例选择弹窗，保存后同步各项目的执行记录 ——
const openLinkDialog = () => {
  if (!planProjects.value.length) {
    ElMessage.warning(t('execution.linkNoProject'))
    return
  }
  linkDialogVisible.value = true
}

const confirmLinkCases = async (testcaseIds) => {
  linkSaving.value = true
  try {
    await api.post(`/executions/plans/${route.params.id}/link_testcases/`, {
      testcases: testcaseIds
    })
    linkDialogVisible.value = false
    await refreshPlan()
    ElMessage.success(t('execution.linkCasesSuccess'))
  } catch (error) {
    ElMessage.error(error.response?.data?.error || t('execution.linkCasesFailed'))
  } finally {
    linkSaving.value = false
  }
}

const PRIORITY_WEIGHT = { critical: 0, high: 1, medium: 2, low: 3 }
const STATUS_WEIGHT = { failed: 0, blocked: 1, retest: 2, untested: 3, passed: 4 }

const sortRunCases = (list, sort) => {
  const sorted = [...list]
  if (sort === 'priority') {
    sorted.sort((left, right) =>
      (PRIORITY_WEIGHT[left.testcase?.priority] ?? 9) - (PRIORITY_WEIGHT[right.testcase?.priority] ?? 9) ||
      (left.testcase?.id || 0) - (right.testcase?.id || 0)
    )
  } else if (sort === 'status') {
    sorted.sort((left, right) =>
      (STATUS_WEIGHT[left.status] ?? 9) - (STATUS_WEIGHT[right.status] ?? 9) ||
      (left.testcase?.id || 0) - (right.testcase?.id || 0)
    )
  } else if (sort === 'executed_at') {
    sorted.sort((left, right) =>
      new Date(right.executed_at || 0) - new Date(left.executed_at || 0) ||
      (left.testcase?.id || 0) - (right.testcase?.id || 0)
    )
  } else {
    sorted.sort((left, right) => (left.testcase?.id || 0) - (right.testcase?.id || 0))
  }
  return sorted
}

// 模块 + 搜索 + 执行结果 / 执行人筛选 + 排序后的表格数据
const computeVisibleCases = (run) => {
  const ui = ensureUi(run)
  let list = casesInModule(run)
  const keyword = (ui.search || '').trim().toLowerCase()
  if (keyword) {
    list = list.filter(runCase => {
      const title = (runCase.testcase?.title || '').toLowerCase()
      return title.includes(keyword) || String(runCase.testcase?.id ?? '').includes(keyword)
    })
  }
  if (ui.status) list = list.filter(runCase => runCase.status === ui.status)
  if (ui.executor) list = list.filter(runCase => runCase.executed_by?.username === ui.executor)
  return sortRunCases(list, ui.sort)
}

// 执行人筛选选项：取当前 run 内出现过的执行人
const executorOptions = (run) => {
  const names = new Set()
  for (const runCase of run.run_cases || []) {
    if (runCase.executed_by?.username) names.add(runCase.executed_by.username)
  }
  return [...names].sort((left, right) => left.localeCompare(right))
}

// 用 computed 缓存表格数据：保证数组引用稳定，避免每次渲染重建导致复选框选择被重置
const visibleCasesMap = computed(() => {
  const map = {}
  for (const run of testPlan.value.test_runs || []) {
    map[run.id] = computeVisibleCases(run)
  }
  return map
})

const refreshPlan = async () => {
  const response = await api.get(`/executions/plans/${route.params.id}/`)
  testPlan.value = response.data
  initRunUi()
  buildSavedState()
  await loadModuleTrees()
  Object.keys(selectionByRun).forEach(runId => {
    selectionByRun[runId] = []
  })
}

const fetchTestPlan = async () => {
  try {
    await refreshPlan()
  } catch (error) {
    ElMessage.error(t('execution.fetchDetailFailed'))
  } finally {
    loaded.value = true
  }
}

// 按 run 所属项目加载模块树，供左侧功能模块树使用
const loadModuleTrees = async () => {
  const projectIds = [...new Set(
    (testPlan.value.test_runs || []).map(run => run.project?.id).filter(Boolean)
  )]
  if (!projectIds.length) return
  const entries = await Promise.all(projectIds.map(async (projectId) => {
    try {
      const { data } = await api.get('/testcases/modules/', { params: { project: projectId } })
      return [projectId, data || []]
    } catch (error) {
      return [projectId, []]
    }
  }))
  moduleTreeByProject.value = Object.fromEntries(entries)
}

// 依据当前已持久化的数据构建"已保存快照"
const buildSavedState = () => {
  const map = {}
  const runs = testPlan.value.test_runs
  if (runs) {
    for (const run of runs) {
      if (run.run_cases) {
        for (const rc of run.run_cases) {
          map[rc.id] = {
            status: rc.status,
            actual_result: rc.actual_result || '',
            comments: rc.comments || ''
          }
        }
      }
    }
  }
  savedState.value = map
}

// 行是否有未提交改动（状态或备注与快照不一致）
const isRowDirty = (runCase) => {
  if (!runCase) return false
  const saved = savedState.value[runCase.id]
  if (!saved) return false
  return runCase.status !== saved.status ||
    (runCase.actual_result || '') !== saved.actual_result ||
    (runCase.comments || '') !== saved.comments
}

// 某 run 内未提交的用例数
const dirtyCount = (run) => {
  if (!run.run_cases) return 0
  return run.run_cases.filter(rc => isRowDirty(rc)).length
}

// 表格行样式：未提交行加 row-dirty 类
const rowClassName = ({ row }) => (isRowDirty(row) ? 'row-dirty' : '')

// 本地重算 run 进度（与后端 progress_stats 公式一致），
// 避免整页 fetchTestPlan 刷新替换行对象、从而丢失其他行的未提交草稿
const recomputeProgress = (run) => {
  const cases = run.run_cases || []
  const p = run.progress || (run.progress = {})
  p.total = cases.length
  p.untested = 0
  p.passed = 0
  p.failed = 0
  p.blocked = 0
  p.retest = 0
  for (const c of cases) {
    if (c.status && p[c.status] !== undefined) p[c.status]++
  }
  p.tested = p.passed + p.failed + p.blocked + p.retest
  p.progress = p.total > 0 ? Math.round((p.tested / p.total) * 100 * 10) / 10 : 0
}

// 保存单个用例：状态 + 备注一次提交，只产生一条历史记录
const saveRunCase = async (runCase, run) => {
  if (!runCase || !isRowDirty(runCase)) return false
  savingId.value = runCase.id
  try {
    const response = await api.patch(`/executions/run_cases/${runCase.id}/update_status/`, {
      status: runCase.status,
      actual_result: runCase.actual_result || '',
      comments: runCase.comments || ''
    })
    Object.assign(runCase, response.data)
    savedState.value[runCase.id] = {
      status: runCase.status,
      actual_result: runCase.actual_result || '',
      comments: runCase.comments || ''
    }
    if (run) recomputeProgress(run)
    ElMessage.success(t('execution.statusUpdateSuccess'))
    return true
  } catch (error) {
    ElMessage.error(t('execution.statusUpdateFailed'))
    return false
  } finally {
    savingId.value = null
  }
}

// 批量保存某 run 内所有未提交用例
const saveAllInRun = async (run) => {
  const dirty = (run.run_cases || []).filter(rc => isRowDirty(rc))
  if (!dirty.length) return
  batchRunId.value = run.id
  let ok = 0
  let fail = 0
  for (const rc of dirty) {
    try {
      await api.patch(`/executions/run_cases/${rc.id}/update_status/`, {
        status: rc.status,
        actual_result: rc.actual_result || '',
        comments: rc.comments || ''
      })
      savedState.value[rc.id] = {
        status: rc.status,
        actual_result: rc.actual_result || '',
        comments: rc.comments || ''
      }
      ok++
    } catch (error) {
      fail++
    }
  }
  recomputeProgress(run)
  batchRunId.value = null
  if (fail > 0) {
    ElMessage.success(t('execution.batchSavePartialSuccess', { successCount: ok, failCount: fail }))
  } else {
    ElMessage.success(t('execution.batchSaveSuccess', { count: ok }))
  }
}

const viewCaseHistory = async (runCase) => {
  try {
    const response = await api.get(`/executions/run_cases/${runCase.id}/history/`)
    currentCaseHistory.value = response.data
    historyDialogVisible.value = true
  } catch (error) {
    ElMessage.error(t('execution.fetchHistoryFailed'))
  }
}

// 点击用例行打开执行弹窗；复选框点击只改变批量选择状态。
const openCaseExecution = (runCase, column, event) => {
  if (event.target.closest('.el-checkbox, .el-table__expand-icon')) return
  const index = allCases.value.findIndex(item => item.runCase.id === runCase.id)
  if (index < 0) return
  currentCaseIndex.value = index
  caseExecutionDialogVisible.value = true
}

// 在当前测试计划的全部执行用例中前后切换。
const navigateCase = (offset) => {
  const nextIndex = currentCaseIndex.value + offset
  if (nextIndex >= 0 && nextIndex < allCases.value.length) currentCaseIndex.value = nextIndex
}

// 弹窗保存沿用用例执行接口，保留最后操作人和执行时间。
const saveCurrentCase = async () => {
  await saveRunCase(currentCase.value, currentRun.value)
}

const saveCurrentAndNext = async () => {
  const saved = isRowDirty(currentCase.value)
    ? await saveRunCase(currentCase.value, currentRun.value)
    : true
  if (saved && currentCaseIndex.value < allCases.value.length - 1) navigateCase(1)
}

// 处理选择变化（按 run 分别记录）
const handleSelectionChange = (run, selection) => {
  selectionByRun[run.id] = selection
}

const selectedCount = (run) => (selectionByRun[run.id] || []).length

// 清空某个 run 的表格选择
const clearSelection = (run) => {
  const table = tableRefs[run.id]
  if (table?.clearSelection) table.clearSelection()
  selectionByRun[run.id] = []
}

// 批量删除：本地移除 + 重算进度（不整页刷新，保留其他行未提交草稿）
const batchDeleteCases = async (run) => {
  const selected = selectionByRun[run.id] || []
  if (selected.length === 0) {
    ElMessage.warning(t('execution.selectCasesFirst'))
    return
  }

  try {
    await ElMessageBox.confirm(
      t('execution.batchDeleteCasesConfirm', { count: selected.length }),
      t('common.warning'),
      {
        confirmButtonText: t('common.confirm'),
        cancelButtonText: t('common.cancel'),
        type: 'warning'
      }
    )

    isDeleting.value = true
    let successCount = 0
    let failCount = 0
    const deletedIds = new Set()

    for (const runCase of selected) {
      try {
        await api.delete(`/executions/run_cases/${runCase.id}/`)
        successCount++
        deletedIds.add(runCase.id)
      } catch (error) {
        console.error(`删除用例 ${runCase.id} 失败:`, error)
        failCount++
      }
    }

    // 本地移除已删除用例并重算进度
    if (deletedIds.size > 0 && testPlan.value.test_runs) {
      for (const item of testPlan.value.test_runs) {
        if (item.run_cases) {
          item.run_cases = item.run_cases.filter(rc => !deletedIds.has(rc.id))
          recomputeProgress(item)
        }
      }
      for (const id of deletedIds) delete savedState.value[id]
    }

    selectionByRun[run.id] = []
    clearSelection(run)

    if (successCount > 0) {
      if (failCount > 0) {
        ElMessage.success(t('execution.batchDeleteCasesPartialSuccess', { successCount, failCount }))
      } else {
        ElMessage.success(t('execution.batchDeleteCasesSuccess', { successCount }))
      }
    } else {
      ElMessage.error(t('execution.batchDeleteFailed'))
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除失败:', error)
      ElMessage.error(t('execution.batchDeleteFailed'))
    }
  } finally {
    isDeleting.value = false
  }
}

// 批量操作后表格数据会重新计算，这里按 id 还原选中项，方便连续批量操作
const reselectRows = (run, ids) => {
  nextTick(() => {
    const table = tableRefs[run?.id]
    if (!table || !ids.length) return
    const rows = (visibleCasesMap.value[run.id] || []).filter(runCase => ids.includes(runCase.id))
    table.clearSelection()
    rows.forEach(row => table.toggleRowSelection(row, true))
  })
}

// 打开「更改执行人」：加载该 run 所属项目的成员
const openAssignDialog = async (run) => {
  if (selectedCount(run) === 0) return
  batchRun.value = run
  assignUserId.value = null
  memberOptions.value = []
  assignDialogVisible.value = true
  const projectId = run.project?.id
  if (!projectId) return
  membersLoading.value = true
  try {
    const { data } = await api.get(`/projects/${projectId}/members/`)
    memberOptions.value = data || []
  } catch (error) {
    memberOptions.value = []
  } finally {
    membersLoading.value = false
  }
}

// 批量更改执行人：只改执行人，不改执行结果与执行时间
const confirmAssign = async () => {
  const run = batchRun.value
  const selected = selectionByRun[run?.id] || []
  if (!run || selected.length === 0 || !assignUserId.value) return
  batchOperating.value = true
  try {
    await api.post('/executions/run_cases/batch_update/', {
      ids: selected.map(runCase => runCase.id),
      executed_by: assignUserId.value
    })
    const member = memberOptions.value.find(item => item.id === assignUserId.value)
    if (member) {
      selected.forEach(runCase => {
        runCase.executed_by = { id: member.id, username: member.username }
      })
    }
    assignDialogVisible.value = false
    reselectRows(run, selected.map(runCase => runCase.id))
    ElMessage.success(t('execution.batchAssignSuccess', { count: selected.length }))
  } catch (error) {
    ElMessage.error(t('execution.batchOperationFailed'))
  } finally {
    batchOperating.value = false
  }
}

const openStatusDialog = (run) => {
  if (selectedCount(run) === 0) return
  batchRun.value = run
  batchStatus.value = 'passed'
  statusDialogVisible.value = true
}

// 批量更改执行结果：写历史记录，同时把本地快照对齐，避免误判为未保存
const confirmBatchStatus = async () => {
  const run = batchRun.value
  const selected = selectionByRun[run?.id] || []
  if (!run || selected.length === 0) return
  batchOperating.value = true
  try {
    await api.post('/executions/run_cases/batch_update/', {
      ids: selected.map(runCase => runCase.id),
      status: batchStatus.value
    })
    const now = new Date().toISOString()
    const currentUser = userStore.user
      ? { id: userStore.user.id, username: userStore.user.username }
      : null
    for (const runCase of selected) {
      runCase.status = batchStatus.value
      runCase.executed_at = now
      if (currentUser) runCase.executed_by = currentUser
      savedState.value[runCase.id] = {
        status: runCase.status,
        actual_result: runCase.actual_result || '',
        comments: runCase.comments || ''
      }
    }
    recomputeProgress(run)
    statusDialogVisible.value = false
    reselectRows(run, selected.map(runCase => runCase.id))
    ElMessage.success(t('execution.batchStatusSuccess', { count: selected.length }))
  } catch (error) {
    ElMessage.error(t('execution.batchOperationFailed'))
  } finally {
    batchOperating.value = false
  }
}

const formatDate = (dateString) => {
  if (!dateString) return '-'
  return new Date(dateString).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

// 运行状态 -> chip 样式修饰符（颜色由 CSS 变量驱动，语义化而非装饰）
const runStatusClass = (progress) => {
  if (progress.progress === 100) return 'is-completed'
  if (progress.untested === progress.total) return 'is-pending'
  if (progress.failed > 0) return 'is-failed'
  if (progress.blocked > 0) return 'is-blocked'
  return 'is-progress'
}

const getRunStatusText = (progress) => {
  if (progress.progress === 100) return t('execution.completed')
  if (progress.untested === progress.total) return t('execution.notStarted')
  return t('execution.inProgress')
}

// 计划本身被关闭时，头部状态优先展示「已关闭」，不再用执行进度推断
const isPlanClosed = computed(() => testPlan.value.is_active === false)
const headerStatusClass = computed(() => (isPlanClosed.value ? 'is-closed' : runStatusClass(overallProgress.value)))
const headerStatusText = computed(() => (isPlanClosed.value ? t('execution.closed') : getRunStatusText(overallProgress.value)))

const getStatusType = (status) => {
  const typeMap = {
    'untested': 'info',
    'passed': 'success',
    'failed': 'danger',
    'blocked': 'warning',
    'retest': 'primary'
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status) => {
  const textMap = {
    'untested': t('execution.untested'),
    'passed': t('execution.passed'),
    'failed': t('execution.failed'),
    'blocked': t('execution.blocked'),
    'retest': t('execution.retest')
  }
  return textMap[status] || status
}

const getPriorityText = (priority) => {
  const textMap = {
    low: t('execution.priorityLow'),
    medium: t('execution.priorityMedium'),
    high: t('execution.priorityHigh'),
    critical: t('execution.priorityCritical')
  }
  return textMap[priority] || '-'
}

const getTestTypeText = (type) => {
  const textMap = {
    functional: t('execution.typeFunctional'),
    integration: t('execution.typeIntegration'),
    api: t('execution.typeApi'),
    ui: t('execution.typeUi'),
    performance: t('execution.typePerformance'),
    security: t('execution.typeSecurity')
  }
  return textMap[type] || '-'
}

onMounted(() => {
  fetchTestPlan()
})
</script>

<style scoped>
.execution-detail {
  /* 配色全部走 Element 变量（页面底色由 el-main 提供），不再自定义令牌 */
  /* 整页固定为一屏高，由 run-card / run-body 自适应剩余空间，避免整页滚动与底部留白 */
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  color: var(--el-text-color-primary);
}

/* —— 页面头部：扁平卡片，克制 —— */
.plan-header {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.06);
  padding: 20px;
  margin-bottom: 10px;
}

.plan-header__title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.plan-header__title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: -0.3px;
  color: var(--el-text-color-primary);
}

.version-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 500;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 999px;
  line-height: 1.6;
}

.version-chip__icon {
  font-size: 13px;
}

.plan-header__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.plan-header__meta {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.meta-item__icon {
  font-size: 15px;
  color: var(--el-text-color-secondary);
}

.meta-dim {
  color: var(--el-text-color-placeholder);
}

/* —— 运行卡片 —— */
.run-card {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.06);
  padding: 20px;
}

.run-card:last-of-type {
  margin-bottom: 0;
}

.run-card__head {
  margin-bottom: 14px;
}

/* 状态 chip：中性底 + 语义色圆点 */
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 999px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
  line-height: 1.7;
}

.status-chip__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-text-color-secondary);
}

.status-chip.is-completed { color: var(--el-color-success); }
.status-chip.is-completed .status-chip__dot { background: var(--el-color-success); }
.status-chip.is-failed { color: var(--el-color-danger); }
.status-chip.is-failed .status-chip__dot { background: var(--el-color-danger); }
.status-chip.is-blocked { color: var(--el-color-warning); }
.status-chip.is-blocked .status-chip__dot { background: var(--el-color-warning); }
.status-chip.is-pending .status-chip__dot { background: var(--el-text-color-disabled); }
.status-chip.is-progress { color: var(--el-color-primary); }
.status-chip.is-progress .status-chip__dot { background: var(--el-color-primary); }
.status-chip.is-closed { color: var(--el-text-color-secondary); }
.status-chip.is-closed .status-chip__dot { background: var(--el-text-color-secondary); }

/* —— 运行指标条：通过率 / 已测 / 进度 / 状态图例 —— */
.run-metrics {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.run-metrics__stat {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.run-metrics__label {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
}

.run-metrics__value {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.run-metrics__track {
  display: flex;
  align-items: stretch;
  gap: 2px;
  flex: 1 1 160px;
  min-width: 110px;
  height: 8px;
}

.run-metrics__segment {
  flex: 1 1 0;
  min-width: 2px;
  border-radius: 4px;
  transition: flex-grow 300ms cubic-bezier(0.4, 0, 0.2, 1);
}

.run-metrics__segment.seg--passed { background: var(--el-color-success); }
.run-metrics__segment.seg--failed { background: var(--el-color-danger); }
.run-metrics__segment.seg--blocked { background: var(--el-color-warning); }
.run-metrics__segment.seg--retest { background: var(--el-color-primary); }
.run-metrics__segment.seg--untested { background: var(--el-text-color-disabled); }

.run-metrics__legend {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.legend {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.legend::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-border-color-lighter);
}

.legend--passed::before { background: var(--el-color-success); }
.legend--failed::before { background: var(--el-color-danger); }
.legend--blocked::before { background: var(--el-color-warning); }
.legend--retest::before { background: var(--el-color-primary); }
.legend--untested::before { background: var(--el-text-color-disabled); }

.run-metrics__action {
  margin-left: auto;
}

/* —— 模块树 + 用例列表 —— */
.run-body {
  flex: 1 1 auto;
  min-height: 300px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  overflow: hidden;
}

.module-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--el-bg-color);
}

.sidebar-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 0 0 auto;
  padding: 12px 12px 10px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
}

.sidebar-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.collection-tree {
  flex: 1;
  min-height: 0;
  padding: 8px;
  overflow: auto;
}

.collection-tree :deep(.el-tree) {
  background: transparent;
  --el-tree-node-hover-bg-color: var(--el-fill-color-light);
}

.collection-tree :deep(.el-tree-node__content) {
  height: 30px;
  min-width: 0;
  overflow: hidden;
  border-radius: 4px;
}

.collection-tree :deep(.el-tree-node__content > .el-tree-node__label) {
  display: none;
}

.tree-node {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  padding-right: 22px;
  color: var(--el-text-color-regular);
}

.tree-node > .el-icon {
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
  font-size: 15px;
}

.node-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-node:hover .node-label,
.tree-node.is-active .node-label {
  color: var(--el-color-primary);
}

.tree-count {
  position: absolute;
  right: 10px;
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-align: right;
}

.tree-entry {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-bottom: 2px;
  padding: 6px 34px 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.tree-entry > .el-icon {
  color: var(--el-text-color-secondary);
  font-size: 15px;
}

.tree-entry.root-module-level {
  margin-top: 2px;
}

.tree-entry:hover,
.tree-entry.active {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.tree-entry-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 右侧用例区 */
.run-main {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  background: var(--el-bg-color);
}

.run-main__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex: 0 0 auto;
  flex-wrap: wrap;
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.run-main__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.run-main__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.run-main__selected {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-color-primary);
}

.run-main__tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-search { width: 168px; }
.tool-select { width: 108px; }
.tool-select--sort { width: 124px; }

.run-main__table {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.table-empty {
  padding: 32px 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

/* —— 批量操作弹窗 —— */
.batch-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.batch-dialog-hint {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

/* —— 表格：与测试用例页一致（Element 默认表头 + 浅描边）—— */
.execution-table {
  --el-table-border-color: var(--el-border-color-lighter);
  --el-table-header-bg-color: var(--el-bg-color);
  --el-table-header-text-color: var(--el-text-color-secondary);
  --el-table-row-hover-bg-color: var(--el-fill-color-light);
}

:deep(.execution-table .el-table__header th) {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  background: var(--el-bg-color) !important;
}

:deep(.execution-table .el-table__body td) {
  font-size: 13px;
  color: var(--el-text-color-primary);
}

:deep(.execution-table .el-table__body tr) {
  cursor: pointer;
}

/* 未提交行：极淡暖底，提示有待保存的改动 */
:deep(.execution-table .row-dirty td) {
  background: var(--el-color-warning-light-9) !important;
}

:deep(.execution-table .row-dirty:hover td) {
  background: var(--el-color-warning-light-8) !important;
}

/* 优先级标记 */
.priority-tag {
  display: inline-block;
  min-width: 28px;
  padding: 0 6px;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
  border-radius: 3px;
  background: var(--el-color-info-light-9);
  color: var(--el-text-color-secondary);
}

.priority-tag--critical,
.priority-tag--high {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.priority-tag--medium {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.priority-tag--low {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

/* 执行结果：语义色圆点 + 文本 */
.case-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.case-status__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-text-color-disabled);
}

.case-status--passed { color: var(--el-color-success); }
.case-status--passed .case-status__dot { background: var(--el-color-success); }
.case-status--failed { color: var(--el-color-danger); }
.case-status--failed .case-status__dot { background: var(--el-color-danger); }
.case-status--blocked { color: var(--el-color-warning); }
.case-status--blocked .case-status__dot { background: var(--el-color-warning); }
.case-status--retest { color: var(--el-color-primary); }
.case-status--retest .case-status__dot { background: var(--el-color-primary); }

/* —— 空态 —— */
.empty-state {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.06);
  padding: 48px 24px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}

.case-dialog-header,
.case-dialog-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.case-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  width: 100%;
}

.case-dialog-heading,
.case-dialog-toolbar {
  min-width: 0;
}

.case-dialog-heading {
  display: flex;
  align-items: center;
  gap: 14px;
}

.case-dialog-title {
  flex: 0 0 auto;
  font-weight: 600;
}

.case-dialog-current-title {
  max-width: min(34vw, 460px);
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-dialog-toolbar {
  flex: 0 0 auto;
  justify-content: flex-end;
  gap: 8px;
}

.case-nav-button {
  width: 32px;
  height: 32px;
  padding: 0;
}

.case-dialog-count,
.case-id {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

:global(.case-execution-dialog) {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 10px);
}

:global(.case-execution-dialog .el-dialog__header) {
  flex: 0 0 auto;
}

:global(.case-execution-dialog .el-dialog__body) {
  min-height: 0;
  overflow-y: auto;
}

.case-execution-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  height: min(80vh, 820px);
  min-height: min(76vh, 820px);
  max-height: 80vh;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  overflow: hidden;
}

.case-information {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 24px;
}

.case-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--el-border-color-light);
}

.case-title-row h2 {
  margin: 5px 0 0;
  color: var(--el-text-color-primary);
  font-size: 19px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.case-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
  padding: 16px 0 20px;
  border-bottom: 1px solid var(--el-border-color-light);
}

.case-meta-grid div,
.last-execution-meta div {
  display: flex;
  gap: 8px;
  min-width: 0;
  color: var(--el-text-color-primary);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.case-meta-grid span,
.last-execution-meta span {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
}

.case-section {
  padding-top: 18px;
}

.case-section h3,
.case-result-panel h3 {
  margin: 0 0 9px;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
}

.case-section p {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.case-result-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 18px;
  background: var(--el-bg-color);
  border-left: 1px solid var(--el-border-color-lighter);
}

.result-status-group {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

:deep(.result-status-group .el-radio-button__inner) {
  border: 1px solid var(--el-border-color);
  border-radius: 4px !important;
  box-shadow: none !important;
  transition: color 120ms ease, background-color 120ms ease, border-color 120ms ease;
}

:deep(.result-option--passed .el-radio-button__inner) {
  color: var(--el-color-success);
  border-color: var(--el-color-success);
}

:deep(.result-option--passed .el-radio-button__original-radio:checked + .el-radio-button__inner) {
  color: var(--el-color-white);
  background: var(--el-color-success);
  border-color: var(--el-color-success);
}

:deep(.result-option--failed .el-radio-button__inner) {
  color: var(--el-color-danger);
  border-color: var(--el-color-danger);
}

:deep(.result-option--failed .el-radio-button__original-radio:checked + .el-radio-button__inner) {
  color: var(--el-color-white);
  background: var(--el-color-danger);
  border-color: var(--el-color-danger);
}

:deep(.result-option--blocked .el-radio-button__inner) {
  color: var(--el-color-warning);
  border-color: var(--el-color-warning);
}

:deep(.result-option--blocked .el-radio-button__original-radio:checked + .el-radio-button__inner) {
  color: var(--el-color-white);
  background: var(--el-color-warning);
  border-color: var(--el-color-warning);
}

:deep(.result-option--retest .el-radio-button__inner) {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

:deep(.result-option--retest .el-radio-button__original-radio:checked + .el-radio-button__inner) {
  color: var(--el-color-white);
  background: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.result-field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.result-field label {
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 600;
}

.last-execution-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: auto;
  padding-top: 15px;
  border-top: 1px solid var(--el-border-color-light);
}

/* —— 响应式 —— */
@media (max-width: 768px) {
  .execution-detail {
    padding: 16px;
  }
  .plan-header,
  .run-card {
    padding: 16px;
  }
  .plan-header__title {
    font-size: 19px;
  }

  .run-body {
    min-height: 320px;
  }

  .run-main__tools {
    width: 100%;
  }

  .tool-search {
    flex: 1 1 auto;
    width: auto;
  }

  .case-execution-layout {
    display: flex;
    flex-direction: column;
    height: auto;
    min-height: 0;
    max-height: none;
    overflow: visible;
  }

  .case-information,
  .case-result-panel {
    min-height: auto;
    overflow: visible;
  }

  .case-result-panel {
    border-top: 1px solid var(--el-border-color-lighter);
    border-left: 0;
  }

  .case-dialog-header,
  .case-dialog-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .case-dialog-header {
    gap: 12px;
  }

  .case-dialog-current-title {
    max-width: 80vw;
  }

  .case-dialog-toolbar {
    width: 100%;
    justify-content: flex-start;
  }

  .case-meta-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
