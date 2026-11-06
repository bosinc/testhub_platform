<template>
  <div class="automation-testing" @click="showFolderContextMenu = false">
    <div class="header">
      <h3>{{ $t('apiTesting.automation.title') }}</h3>
      <el-button type="primary" @click="openCreateScenarioCaseDialog">
        <el-icon><Plus /></el-icon>
        {{ $t('apiTesting.automation.createScenarioCase') }}
      </el-button>
    </div>

    <div class="content-layout">
      <ResizableSplitPane v-model="sidebarWidth" :min-size="240" :max-size="520" :second-min-size="480" storage-key="api-testing.automation.sidebar-width">
        <template #first>
      <!-- 左侧项目选择和场景用例列表 -->
          <div class="sidebar">
        <div class="project-selector">
          <el-select
            v-model="selectedProject"
            :placeholder="$t('apiTesting.common.selectProject')"
            @change="onProjectChange"
            style="width: 100%;"
          >
            <el-option
              v-for="project in httpProjects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </div>

        <div class="scenario-case-list">
          <div class="list-header">
            <span>{{ $t('apiTesting.automation.scenarioCases') }}</span>
            <div class="list-actions">
              <el-button class="create-folder-button" size="small" text :disabled="!selectedProject" :title="$t('apiTesting.automation.folder.create')" @click="createFolder()">
                <el-icon><FolderAdd /></el-icon>
              </el-button>
            </div>
          </div>
          <div class="batch-bar">
            <el-button size="small" type="success" :disabled="selectedScenarioCaseIds.length === 0" @click="runSelectedScenarioCases">
              <el-icon><VideoPlay /></el-icon> {{ $t('apiTesting.automation.batchRun', { count: selectedScenarioCaseIds.length }) }}
            </el-button>
          </div>

          <el-scrollbar height="520px">
            <el-tree
              ref="scenarioCaseTreeRef"
              :data="scenarioCaseTree"
              :props="{ label: 'name', children: 'children' }"
              :default-expanded-keys="treeDefaultExpandedKeys"
              node-key="id"
              show-checkbox
              :check-on-click-node="false"
              :filter-node-method="() => true"
              @node-contextmenu="onFolderNodeContextMenu"
              @check="onScenarioCaseCheck"
            >
              <template #default="{ data }">
                <div
                  class="scenario-case-tree-node"
                  :class="{ active: data.type === 'scenarioCase' && selectedScenarioCase?.id === data.id }"
                  @click="data.type === 'scenarioCase' && selectScenarioCase(data)"
                >
                  <el-icon v-if="data.type === 'folder'"><Folder /></el-icon>
                  <el-icon v-else><Document /></el-icon>
                  <span class="scenario-case-name">{{ data.name }}</span>
                  <el-icon
                    v-if="data.type === 'scenarioCase'"
                    class="del-icon"
                    @click.stop="handleScenarioCaseAction({ action: 'delete', scenarioCase: data })"
                  ><Delete /></el-icon>
                </div>
              </template>
            </el-tree>
          </el-scrollbar>
          <ul
            ref="folderContextMenuRef"
            v-show="showFolderContextMenu"
            class="folder-context-menu"
            :style="{ left: folderContextMenuX + 'px', top: folderContextMenuY + 'px' }"
            @click.stop
          >
            <template v-if="contextTreeNode?.type === 'folder'">
              <li @click="createFolder(contextTreeNode.folderId)">{{ $t('apiTesting.automation.folder.createSubfolder') }}</li>
              <li @click="renameFolder(contextTreeNode)">{{ $t('apiTesting.common.rename') }}</li>
              <li class="danger" @click="deleteFolder(contextTreeNode)">{{ $t('apiTesting.common.delete') }}</li>
            </template>
            <li v-else-if="contextTreeNode?.type === 'scenarioCase'" @click="openMoveScenarioCaseDialog(contextTreeNode)">{{ $t('apiTesting.automation.folder.moveToGroup') }}</li>
          </ul>
        </div>
          </div>
        </template>

        <template #second>
          <!-- 右侧场景用例详情 -->
          <div class="main-content">
        <div v-if="!selectedScenarioCase" class="empty-state">
          <el-empty :description="$t('apiTesting.automation.selectScenarioCaseHint')" />
        </div>

        <div v-else class="scenario-case-detail">
          <!-- 场景用例信息 -->
          <div class="scenario-case-header">
            <div class="scenario-case-title">
              <h4>{{ selectedScenarioCase.name }}</h4>
              <div class="scenario-case-actions">
                <el-button type="success" @click="runScenarioCase(selectedScenarioCase)" :loading="running">
                  <el-icon><VideoPlay /></el-icon>
                  {{ $t('apiTesting.automation.runTest') }}
                </el-button>
                <el-button @click="editScenarioCase(selectedScenarioCase)">
                  <el-icon><Edit /></el-icon>
                  {{ $t('apiTesting.common.edit') }}
                </el-button>
              </div>
            </div>
            <div class="scenario-case-description">
              {{ selectedScenarioCase.description || $t('apiTesting.automation.noDescription') }}
            </div>
            <div class="scenario-case-meta">
              <el-tag size="small">{{ getEnvironmentName(selectedScenarioCase.environment) }}</el-tag>
              <span class="meta-text">{{ $t('apiTesting.automation.creator') }}{{ selectedScenarioCase.created_by?.username }}</span>
              <span class="meta-text">{{ $t('apiTesting.automation.createTime') }}{{ formatDate(selectedScenarioCase.created_at) }}</span>
            </div>
          </div>

          <!-- 请求列表 -->
          <div class="requests-section">
            <div class="section-header">
              <h5>{{ $t('apiTesting.automation.testRequests') }}</h5>
              <el-button size="small" @click="showAddRequest">
                <el-icon><Plus /></el-icon>
                {{ $t('apiTesting.automation.addRequest') }}
              </el-button>
            </div>

            <el-table :data="selectedScenarioCase.scenario_case_requests" style="width: 100%">
              <el-table-column type="index" width="50" />
              <el-table-column prop="request.name" :label="$t('apiTesting.automation.requestName')" min-width="200" />
              <el-table-column prop="request.method" :label="$t('apiTesting.automation.method')" width="80">
                <template #default="scope">
                  <el-tag :type="getMethodType(scope.row.request.method)" size="small">
                    {{ scope.row.request.method }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="request.url" label="URL" min-width="300" show-overflow-tooltip />
              <el-table-column prop="enabled" :label="$t('apiTesting.automation.enabled')" width="80">
                <template #default="scope">
                  <el-switch
                    v-model="scope.row.enabled"
                    @change="updateRequestEnabled(scope.row)"
                  />
                </template>
              </el-table-column>
              <el-table-column :label="$t('apiTesting.automation.assertions')" width="100">
                <template #default="scope">
                  {{ $t('apiTesting.automation.assertionCount', { n: scope.row.assertions?.length || 0 }) }}
                </template>
              </el-table-column>
              <el-table-column :label="$t('apiTesting.interface.variableExtraction')" width="100">
                <template #default="scope">
                  {{ scope.row.extractors?.length || 0 }}
                </template>
              </el-table-column>
              <el-table-column :label="$t('apiTesting.common.operation')" width="170">
                <template #default="scope">
                  <el-button
                    link
                    size="small"
                    :icon="ArrowUp"
                    :disabled="scope.$index === 0"
                    @click="moveRequest(scope.row, 'up')"
                  />
                  <el-button
                    link
                    size="small"
                    :icon="ArrowDown"
                    :disabled="scope.$index === selectedScenarioCase.scenario_case_requests.length - 1"
                    @click="moveRequest(scope.row, 'down')"
                  />
                  <el-button link type="primary" size="small" :icon="Edit" @click="editRequestConfig(scope.row)" />
                  <el-button link type="danger" size="small" :icon="Delete" @click="removeRequest(scope.row)" />
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 执行历史 -->
          <div class="executions-section">
            <div class="section-header">
              <h5>{{ $t('apiTesting.automation.executionHistory') }}</h5>
              <el-button size="small" @click="loadExecutions">
                <el-icon><Refresh /></el-icon>
                {{ $t('apiTesting.automation.refresh') }}
              </el-button>
            </div>

            <el-table
              class="execution-history-table"
              :data="executions"
              fit
              height="100%"
              v-loading="executionsLoading"
            >
              <el-table-column prop="status" :label="$t('apiTesting.common.status')" min-width="120">
                <template #default="scope">
                  <el-tag :type="getStatusType(scope.row.status)">
                    {{ getStatusText(scope.row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="total_requests" :label="$t('apiTesting.automation.totalRequests')" min-width="120" />
              <el-table-column prop="passed_requests" :label="$t('apiTesting.automation.passedCount')" min-width="120">
                <template #default="scope">
                  <span style="color: #67c23a">{{ scope.row.passed_requests }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="failed_requests" :label="$t('apiTesting.automation.failedCount')" min-width="120">
                <template #default="scope">
                  <span style="color: #f56c6c">{{ scope.row.failed_requests }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('apiTesting.automation.averageTime')" min-width="140">
                <template #default="scope">
                  {{ getAverageExecutionTime(scope.row) }}
                </template>
              </el-table-column>
              <el-table-column prop="executed_by.username" :label="$t('apiTesting.automation.executor')" min-width="140" />
              <el-table-column prop="created_at" :label="$t('apiTesting.automation.executionTime')" min-width="180">
                <template #default="scope">
                  {{ formatDate(scope.row.created_at) }}
                </template>
              </el-table-column>
              <el-table-column :label="$t('apiTesting.common.operation')" min-width="180">
                <template #default="scope">
                  <el-button link type="primary" @click="viewExecutionDetail(scope.row)" size="small">
                    {{ $t('apiTesting.automation.viewDetails') }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
          </div>
        </template>
      </ResizableSplitPane>
    </div>

    <!-- 创建/编辑场景用例对话框 -->
    <el-dialog
      v-model="showCreateScenarioCaseDialog"
      :title="editingScenarioCase ? $t('apiTesting.automation.editScenarioCase') : $t('apiTesting.automation.createScenarioCase')"
      width="600px"
      :close-on-click-modal="false"
      @close="resetScenarioCaseForm"
    >
      <el-form
        ref="scenarioCaseFormRef"
        :model="scenarioCaseForm"
        :rules="scenarioCaseRules"
        label-width="100px"
      >
        <el-form-item :label="$t('apiTesting.automation.scenarioCaseName')" prop="name">
          <el-input v-model="scenarioCaseForm.name" :placeholder="$t('apiTesting.automation.inputScenarioCaseName')" />
        </el-form-item>

        <el-form-item :label="$t('apiTesting.automation.scenarioCaseDescription')" prop="description">
          <el-input
            v-model="scenarioCaseForm.description"
            type="textarea"
            :rows="3"
            :placeholder="$t('apiTesting.automation.inputScenarioCaseDescription')"
          />
        </el-form-item>

        <el-form-item :label="$t('apiTesting.automation.folder.label')" prop="folder">
          <el-select v-model="scenarioCaseForm.folder" filterable clearable :placeholder="$t('apiTesting.automation.folder.selectOrInputPath')">
            <el-option v-for="folder in folderOptions" :key="folder.id" :label="folder.label" :value="folder.id" />
          </el-select>
        </el-form-item>

        <el-form-item :label="$t('apiTesting.automation.executionEnvironment')" prop="environment">
          <el-select v-model="scenarioCaseForm.environment" :placeholder="$t('apiTesting.automation.selectEnvironment')" clearable>
            <el-option
              v-for="env in environments"
              :key="env.id"
              :label="env.name"
              :value="env.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateScenarioCaseDialog = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" @click="submitScenarioCaseForm" :loading="submittingScenarioCase">
          {{ editingScenarioCase ? $t('apiTesting.common.update') : $t('apiTesting.common.create') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showMoveScenarioCaseDialog" :title="$t('apiTesting.automation.folder.moveScenarioCase')" width="420px">
      <el-form label-width="90px">
        <el-form-item :label="$t('apiTesting.automation.folder.targetGroup')">
          <el-select v-model="moveTargetFolder" :placeholder="$t('apiTesting.automation.folder.selectTargetGroup')" style="width: 100%">
            <el-option v-for="folder in moveFolderOptions" :key="folder.id" :label="folder.label" :value="folder.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMoveScenarioCaseDialog = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" :loading="movingScenarioCase" @click="moveScenarioCaseToFolder">{{ $t('apiTesting.automation.folder.move') }}</el-button>
      </template>
    </el-dialog>

    <!-- 添加请求对话框 -->
    <el-dialog
      v-model="showAddRequestDialog"
      :title="$t('apiTesting.automation.addRequestToScenarioCase')"
      width="800px"
      :close-on-click-modal="false"
    >
      <div class="add-request-content">
        <el-input
          v-model="requestSearchKeyword"
          :placeholder="$t('apiTesting.interface.searchInterface')"
          clearable
          class="request-search"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <div class="request-selector">
          <el-tree
            ref="requestTreeRef"
            :data="requestTree"
            :props="requestTreeProps"
            :filter-node-method="filterRequestTreeNode"
            :empty-text="requestSearchKeyword.trim() ? '' : $t('apiTesting.common.noData')"
            show-checkbox
            node-key="id"
            :check-on-click-node="false"
            @check="onRequestCheck"
          >
            <template #default="{ node, data }">
              <div class="request-tree-node">
                <el-icon v-if="data.type === 'collection'">
                  <Folder />
                </el-icon>
                <el-icon v-else>
                  <Document />
                </el-icon>
                <span>{{ data.name }}</span>
                <span v-if="data.type === 'request'" class="method-tag" :class="data.method?.toLowerCase()">
                  {{ data.method }}
                </span>
              </div>
            </template>
          </el-tree>
          <div
            v-if="requestSearchKeyword.trim() && !requestSearchHasMatches"
            class="request-search-empty"
          >
            {{ $t('apiTesting.interface.noSearchResults') }}
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="showAddRequestDialog = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" @click="addSelectedRequests" :loading="addingRequests">
          {{ $t('apiTesting.automation.addSelectedRequests') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 场景用例请求配置，复用接口管理中的断言和变量提取编辑器 -->
    <el-dialog
      v-model="showAssertionDialog"
      :title="$t('apiTesting.automation.requestConfig')"
      width="860px"
      :close-on-click-modal="false"
    >
      <el-tabs v-model="requestConfigTab">
        <el-tab-pane :label="$t('apiTesting.interface.assertions')" name="assertions">
          <AssertionEditor v-model="assertionDraft" compact />
        </el-tab-pane>
        <el-tab-pane :label="$t('apiTesting.interface.variableExtraction')" name="extractors">
          <VariableExtractorEditor v-model="extractorDraft" compact :show-title="false" />
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="showAssertionDialog = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" :loading="savingAssertions" @click="saveScenarioCaseAssertions">
          {{ $t('apiTesting.common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 执行结果对话框 -->
    <el-dialog
      v-model="showExecutionDialog"
      :title="$t('apiTesting.automation.testExecutionResult')"
      width="80%"
      :top="'5vh'"
    >
      <div v-if="currentExecution" class="execution-detail">
        <div class="execution-summary">
          <el-row :gutter="20">
            <el-col :span="6">
              <el-statistic :title="$t('apiTesting.automation.totalRequests')" :value="currentExecution.total_requests" />
            </el-col>
            <el-col :span="6">
              <el-statistic :title="$t('apiTesting.automation.passedCount')" :value="currentExecution.passed_requests" />
            </el-col>
            <el-col :span="6">
              <el-statistic :title="$t('apiTesting.automation.failedCount')" :value="currentExecution.failed_requests" />
            </el-col>
            <el-col :span="6">
              <el-statistic :title="$t('apiTesting.automation.passRate')" :value="getPassRate(currentExecution)" suffix="%" />
            </el-col>
          </el-row>
        </div>

        <div class="execution-results">
          <h4>{{ $t('apiTesting.automation.detailedResults') }}</h4>
          <el-table :data="formatExecutionResults(currentExecution.results)">
            <el-table-column prop="name" :label="$t('apiTesting.automation.requestName')" min-width="200" />
            <el-table-column prop="method" :label="$t('apiTesting.automation.method')" width="80">
              <template #default="scope">
                <el-tag :type="getMethodType(scope.row.method)" size="small">
                  {{ scope.row.method }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status" :label="$t('apiTesting.automation.result')" width="100">
              <template #default="scope">
                <el-tag :type="scope.row.passed ? 'success' : 'danger'" size="small">
                  {{ scope.row.passed ? $t('apiTesting.automation.status.passed') : $t('apiTesting.automation.status.failed') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status_code" :label="$t('apiTesting.automation.statusCode')" width="100" />
            <el-table-column prop="response_time" :label="$t('apiTesting.automation.responseTime')" width="120">
              <template #default="scope">
                {{ scope.row.response_time?.toFixed(0) }}ms
              </template>
            </el-table-column>
            <el-table-column prop="error" :label="$t('apiTesting.automation.errorMessage')" min-width="200" show-overflow-tooltip />
          </el-table>
        </div>
      </div>

      <template #footer>
        <el-button @click="showExecutionDialog = false">{{ $t('apiTesting.common.close') }}</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import { ref, reactive, onMounted, computed, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import {
  Plus, Refresh, MoreFilled, VideoPlay, Edit, Search,
  Folder, FolderAdd, Document, ArrowUp, ArrowDown, Delete
} from '@element-plus/icons-vue'
import AssertionEditor from '@/components/api-testing/AssertionEditor.vue'
import VariableExtractorEditor from '@/components/api-testing/VariableExtractorEditor.vue'
import {
  addRequestsToScenarioCase,
  createScenarioCase,
  createScenarioCaseFolder,
  deleteScenarioCase as deleteScenarioCaseApi,
  deleteScenarioCaseFolder,
  deleteScenarioCaseRequest,
  executeScenarioCase,
  getAllApiCollections,
  getAllApiRequests,
  getAllScenarioCases,
  getApiProjects,
  getEnvironments,
  getScenarioCaseExecutions,
  getScenarioCaseDetail,
  getScenarioCaseFolders,
  moveScenarioCaseRequest,
  patchScenarioCase,
  patchScenarioCaseRequest,
  updateScenarioCase,
  updateScenarioCaseRequest,
  updateScenarioCaseFolder
} from '@/api/api-testing'
import { createTestCaseAutomationLink } from '@/api/testcases'
import dayjs from 'dayjs'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'
import { normalizeAssertions, stripLegacyAssertionValue } from '@/utils/assertions'

const { t } = useI18n()
const route = useRoute()
const sidebarWidth = ref(300)

const projects = ref([])
const selectedProject = ref(null)
useRememberedProject(selectedProject, projects)
const scenarioCases = ref([])
const scenarioCaseFolders = ref([])
const showFolderContextMenu = ref(false)
const folderContextMenuX = ref(0)
const folderContextMenuY = ref(0)
const folderContextMenuRef = ref(null)
const contextTreeNode = ref(null)
const showMoveScenarioCaseDialog = ref(false)
const movingScenarioCase = ref(false)
const moveTargetFolder = ref(null)
const selectedScenarioCase = ref(null)
const executions = ref([])
const environments = ref([])
const requestTree = ref([])
const requestSearchKeyword = ref('')
const running = ref(false)
const scenarioCaseTreeRef = ref(null)
const executionsLoading = ref(false)
const showCreateScenarioCaseDialog = ref(false)
const showAddRequestDialog = ref(false)
const showAssertionDialog = ref(false)
const showExecutionDialog = ref(false)
const editingScenarioCase = ref(null)
const submittingScenarioCase = ref(false)
const addingRequests = ref(false)
const savingAssertions = ref(false)
const currentExecution = ref(null)
const editingScenarioCaseRequest = ref(null)
const assertionDraft = ref([])
const extractorDraft = ref([])
const requestConfigTab = ref('assertions')
const scenarioCaseFormRef = ref()
const requestTreeRef = ref()

const scenarioCaseForm = reactive({
  name: '',
  description: '',
  folder: null,
  project: null,
  environment: null
})

const scenarioCaseRules = computed(() => ({
  name: [{ required: true, message: t('apiTesting.automation.inputScenarioCaseName'), trigger: 'blur' }]
}))

const requestTreeProps = {
  children: 'children',
  label: 'name'
}

// 搜索范围与接口管理页保持一致，并保留包含命中接口的父集合。
const requestMatchesKeyword = (request, keyword) => {
  return ['name', 'method', 'url'].some(field =>
    String(request[field] || '').toLocaleLowerCase().includes(keyword)
  )
}

const collectionHasMatchingRequest = (collection, keyword) => {
  return (collection.children || []).some(child => {
    if (child.type === 'request') return requestMatchesKeyword(child, keyword)
    return collectionHasMatchingRequest(child, keyword)
  })
}

const requestTreeHasMatchingRequest = (nodes, keyword) => {
  return nodes.some(node => {
    if (node.type === 'request') return requestMatchesKeyword(node, keyword)
    return collectionHasMatchingRequest(node, keyword)
  })
}

const requestSearchHasMatches = computed(() => {
  const keyword = requestSearchKeyword.value.trim().toLocaleLowerCase()
  return !keyword || requestTreeHasMatchingRequest(requestTree.value, keyword)
})

const filterRequestTreeNode = (keyword, data) => {
  const normalizedKeyword = String(keyword || '').trim().toLocaleLowerCase()
  if (!normalizedKeyword) return true
  if (data.type === 'request') return requestMatchesKeyword(data, normalizedKeyword)
  return collectionHasMatchingRequest(data, normalizedKeyword)
}

const expandMatchingCollections = (collections, keyword) => {
  collections.forEach(collection => {
    if (collection.type !== 'collection') return
    if (collectionHasMatchingRequest(collection, keyword)) {
      requestTreeRef.value?.getNode(collection.id)?.expand()
    }
    expandMatchingCollections(collection.children || [], keyword)
  })
}

watch(requestSearchKeyword, async (keyword) => {
  await nextTick()
  if (!requestTreeRef.value) return

  const normalizedKeyword = keyword.trim().toLocaleLowerCase()
  requestTreeRef.value.filter(normalizedKeyword)
  if (normalizedKeyword) expandMatchingCollections(requestTree.value, normalizedKeyword)
})

const httpProjects = computed(() => {
  return projects.value.filter(project => project.project_type !== 'WEBSOCKET')
})

const getMethodType = (method) => {
  const typeMap = {
    'GET': 'success',
    'POST': 'primary',
    'PUT': 'warning',
    'DELETE': 'danger',
    'PATCH': 'info'
  }
  return typeMap[method] || 'info'
}

const getStatusType = (status) => {
  const typeMap = {
    'PENDING': 'info',
    'RUNNING': 'warning',
    'COMPLETED': 'success',
    'FAILED': 'danger',
    'CANCELLED': 'info'
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status) => {
  const statusKey = {
    'PENDING': 'pending',
    'RUNNING': 'running',
    'COMPLETED': 'completed',
    'FAILED': 'failed',
    'CANCELLED': 'cancelled'
  }[status]
  return statusKey ? t(`apiTesting.automation.status.${statusKey}`) : status
}

const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm:ss')
}

const getExecutionTime = (execution) => {
  if (!execution.start_time || !execution.end_time) return '-'
  const start = dayjs(execution.start_time)
  const end = dayjs(execution.end_time)
  return `${end.diff(start, 'second')}s`
}

const getAverageExecutionTime = (execution) => {
  if (!execution.results || !Array.isArray(execution.results) || execution.results.length === 0) {
    return '-'
  }

  // 计算所有请求的平均响应时间
  const totalResponseTime = execution.results.reduce((sum, result) => sum + (result.response_time || 0), 0)
  const averageTime = totalResponseTime / execution.results.length

  if (averageTime < 1000) {
    return `${Math.round(averageTime)}ms`
  } else {
    return `${(averageTime / 1000).toFixed(1)}s`
  }
}

const getPassRate = (execution) => {
  if (execution.total_requests === 0) return 0
  return ((execution.passed_requests / execution.total_requests) * 100).toFixed(1)
}

const getEnvironmentName = (environmentId) => {
  if (!environmentId) return t('apiTesting.automation.noEnvironment')
  const env = environments.value.find(e => e.id === environmentId)
  return env ? env.name : t('apiTesting.automation.noEnvironment')
}

const loadProjects = async () => {
  try {
    const response = await getApiProjects()
    projects.value = response.data.results || response.data

    // 过滤出HTTP项目
    const httpProjects = projects.value.filter(project => project.project_type !== 'WEBSOCKET')

    const formalCaseId = route.query.automation_for_test_case_id
    if (formalCaseId) {
      const workspaceProjectId = route.query.workspace_project_id
      const linkedProject = httpProjects.find(project =>
        String(project.workspace_project_id) === String(workspaceProjectId)
      )
      if (!linkedProject) {
        ElMessage.warning(t('apiTesting.automation.linkedScenarioUnavailable'))
        return
      }
      selectedProject.value = linkedProject.id
      await onProjectChange()
      openCreateScenarioCaseDialog()
      return
    }

    const linkedCaseId = route.query.scenario_case_id
    if (linkedCaseId) {
      const detailResponse = await getScenarioCaseDetail(linkedCaseId)
      const linkedCase = detailResponse.data
      const linkedProject = httpProjects.find(project => String(project.id) === String(linkedCase.project))
      if (!linkedProject) {
        ElMessage.warning(t('apiTesting.automation.linkedScenarioUnavailable'))
        return
      }
      selectedProject.value = linkedProject.id
      await onProjectChange()
      const listedCase = scenarioCases.value.find(item => String(item.id) === String(linkedCaseId))
      if (listedCase) selectScenarioCase(listedCase)
      else ElMessage.warning(t('apiTesting.automation.linkedScenarioUnavailable'))
      return
    }

    if (httpProjects.length > 0 && !selectedProject.value) {
      selectedProject.value = getPreferredProjectId(httpProjects, httpProjects[0].id)
      await onProjectChange()
    } else if (httpProjects.length === 0) {
      // 如果没有HTTP项目，清空选择
      selectedProject.value = null
    }
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadProjects'))
  }
}

const loadScenarioCases = async () => {
  if (!selectedProject.value) return

  try {
    scenarioCases.value = await getAllScenarioCases({ project: selectedProject.value })
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadScenarioCases'))
  }
}

const loadScenarioCaseFolders = async () => {
  if (!selectedProject.value) {
    scenarioCaseFolders.value = []
    return
  }
  try {
    scenarioCaseFolders.value = await getScenarioCaseFolders({ project: selectedProject.value })
  } catch (error) {
    ElMessage.error(t('apiTesting.automation.folder.loadFailed'))
  }
}

const refreshScenarioCaseTree = async () => {
  await Promise.all([loadScenarioCases(), loadScenarioCaseFolders()])
}

const onFolderNodeContextMenu = async (event, data) => {
  if (data.type === 'folder' && !data.folderId) return
  event.preventDefault()
  contextTreeNode.value = data
  showFolderContextMenu.value = true
  folderContextMenuX.value = event.clientX
  folderContextMenuY.value = event.clientY
  await nextTick()
  const menu = folderContextMenuRef.value
  if (!menu) return
  const { width, height } = menu.getBoundingClientRect()
  const margin = 8
  folderContextMenuX.value = Math.max(margin, Math.min(event.clientX, window.innerWidth - width - margin))
  folderContextMenuY.value = Math.max(margin, Math.min(event.clientY, window.innerHeight - height - margin))
}

const openMoveScenarioCaseDialog = (scenarioCase) => {
  contextTreeNode.value = null
  showFolderContextMenu.value = false
  moveTargetFolder.value = scenarioCase.folder || null
  contextTreeNode.value = scenarioCase
  showMoveScenarioCaseDialog.value = true
}

const moveScenarioCaseToFolder = async () => {
  const scenarioCase = contextTreeNode.value
  if (!scenarioCase) return
  movingScenarioCase.value = true
  try {
    const response = await patchScenarioCase(scenarioCase.id, { folder: moveTargetFolder.value })
    const updatedScenarioCase = response.data || { ...scenarioCase, folder: moveTargetFolder.value }
    scenarioCases.value = scenarioCases.value.map(item =>
      item.id === scenarioCase.id ? { ...item, ...updatedScenarioCase } : item
    )
    ElMessage.success(t('apiTesting.automation.folder.scenarioCaseMoved'))
    showMoveScenarioCaseDialog.value = false
    if (selectedScenarioCase.value?.id === scenarioCase.id) {
      selectedScenarioCase.value = scenarioCases.value.find(item => item.id === scenarioCase.id) || null
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.folder?.[0] || t('apiTesting.automation.folder.moveFailed'))
  } finally {
    movingScenarioCase.value = false
  }
}

const folderOptions = computed(() => {
  const byId = new Map(scenarioCaseFolders.value.map(folder => [folder.id, folder]))
  const getLabel = (folder) => {
    const names = []
    let current = folder
    const visited = new Set()
    while (current && !visited.has(current.id)) {
      visited.add(current.id)
      names.unshift(current.name)
      current = byId.get(current.parent)
    }
    return names.join(' / ')
  }
  return scenarioCaseFolders.value
    .map(folder => ({ id: folder.id, label: getLabel(folder) }))
    .sort((a, b) => a.label.localeCompare(b.label, 'zh', { numeric: true }))
})

const moveFolderOptions = computed(() => [
  { id: null, label: t('apiTesting.testSuiteManagement.ungrouped') },
  ...folderOptions.value.filter(folder => folder.id !== contextTreeNode.value?.folder)
])

const createFolder = async (parentId = null) => {
  if (!selectedProject.value) return
  showFolderContextMenu.value = false
  try {
    const { value: name } = await ElMessageBox.prompt(t('apiTesting.automation.folder.inputName'), t('apiTesting.automation.folder.create'), {
      inputPattern: /^[^/\\]+$/,
      inputErrorMessage: t('apiTesting.automation.folder.invalidName'),
      confirmButtonText: t('apiTesting.common.create'),
      cancelButtonText: t('apiTesting.common.cancel')
    })
    const response = await createScenarioCaseFolder({
      project: selectedProject.value,
      parent: parentId || null,
      name: name.trim()
    })
    if (response.data) scenarioCaseFolders.value = [...scenarioCaseFolders.value, response.data]
    ElMessage.success(t('apiTesting.automation.folder.created'))
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.name?.[0] || t('apiTesting.automation.folder.createFailed'))
  }
}

const renameFolder = async (folder) => {
  if (!folder) return
  showFolderContextMenu.value = false
  try {
    const { value: name } = await ElMessageBox.prompt(t('apiTesting.automation.folder.inputNewName'), t('apiTesting.automation.folder.rename'), {
      inputValue: folder.name,
      inputPattern: /^[^/\\]+$/,
      inputErrorMessage: t('apiTesting.automation.folder.invalidName'),
      confirmButtonText: t('apiTesting.common.save'),
      cancelButtonText: t('apiTesting.common.cancel')
    })
    const response = await updateScenarioCaseFolder(folder.folderId, { name: name.trim() })
    const updatedFolder = response.data || { ...scenarioCaseFolders.value.find(item => item.id === folder.folderId), name: name.trim() }
    scenarioCaseFolders.value = scenarioCaseFolders.value.map(item =>
      item.id === folder.folderId ? { ...item, ...updatedFolder } : item
    )
    ElMessage.success(t('apiTesting.automation.folder.renamed'))
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.name?.[0] || t('apiTesting.automation.folder.renameFailed'))
  }
}

const deleteFolder = async (folder) => {
  if (!folder) return
  showFolderContextMenu.value = false
  try {
    await ElMessageBox.confirm(
      t('apiTesting.automation.folder.confirmDelete', { path: folder.name }),
      t('apiTesting.common.confirmDelete'),
      {
        confirmButtonText: t('apiTesting.common.delete'),
        cancelButtonText: t('apiTesting.common.cancel'),
        type: 'warning'
      }
    )
    await deleteScenarioCaseFolder(folder.folderId)
    const folderIds = new Set([folder.folderId])
    let changed = true
    while (changed) {
      changed = false
      scenarioCaseFolders.value.forEach(item => {
        if (folderIds.has(item.parent) && !folderIds.has(item.id)) {
          folderIds.add(item.id)
          changed = true
        }
      })
    }
    scenarioCaseFolders.value = scenarioCaseFolders.value.filter(item => !folderIds.has(item.id))
    scenarioCases.value = scenarioCases.value.map(item =>
      folderIds.has(item.folder) ? { ...item, folder: null } : item
    )
    ElMessage.success(t('apiTesting.automation.folder.deleted'))
    if (selectedScenarioCase.value) {
      selectedScenarioCase.value = scenarioCases.value.find(scenarioCase => scenarioCase.id === selectedScenarioCase.value.id) || null
    }
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(t('apiTesting.automation.folder.deleteFailed'))
  }
}

// 按父子文件夹关系组织场景用例树，避免在前端保存或解析完整路径。
const scenarioCaseTree = computed(() => {
  const root = []
  const map = new Map()
  scenarioCaseFolders.value.forEach(folder => {
    map.set(folder.id, { id: 'f-' + folder.id, name: folder.name, type: 'folder', folderId: folder.id, children: [] })
  })
  scenarioCaseFolders.value.forEach(folder => {
    const node = map.get(folder.id)
    const parent = folder.parent ? map.get(folder.parent) : null
    ;(parent ? parent.children : root).push(node)
  })
  for (const s of scenarioCases.value) {
    const parent = s.folder ? map.get(s.folder) : null
    ;(parent ? parent.children : root).push({ ...s, type: 'scenarioCase' })
  }
  const sortTree = (nodes) => {
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'folder' ? -1 : 1
      return a.name.localeCompare(b.name, 'zh', { numeric: true })
    })
    nodes.forEach(n => n.children && sortTree(n.children))
  }
  sortTree(root)
  return root
})
const treeDefaultExpandedKeys = computed(() =>
  scenarioCaseTree.value
    .filter(g => g.type === 'folder' && !g.name.startsWith('22'))
    .map(g => g.id)
)

const loadEnvironments = async () => {
  try {
    // 获取全局环境 + 当前项目环境
    // 不传递 project 参数，让后端返回所有可访问的全局和项目环境。
    const response = await getEnvironments()
    const allEnvironments = response.data.results || response.data

    // 过滤当前项目相关或全局环境
    environments.value = allEnvironments.filter(env =>
      env.scope === 'GLOBAL' ||
      (env.scope === 'LOCAL' && (!selectedProject.value || env.project === selectedProject.value))
    )
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadEnvironments'))
  }
}

const loadRequestTree = async () => {
  const projectId = selectedProject.value
  if (!projectId) {
    requestTree.value = []
    return
  }

  requestTree.value = []
  try {
    const [collections, requests] = await Promise.all([
      getAllApiCollections({ project: projectId, tree: 1 }),
      getAllApiRequests({ project: projectId, tree: 1 })
    ])

    // 构建树形结构
    if (selectedProject.value === projectId) {
      requestTree.value = buildRequestTree(collections, requests)
    }
  } catch (error) {
    if (selectedProject.value === projectId) {
      ElMessage.error(t('apiTesting.messages.error.loadRequestTree'))
    }
  }
}

const buildRequestTree = (collections, requests) => {
  const map = {}
  const roots = []

  // 创建集合节点
  collections.forEach(collection => {
    map[collection.id] = {
      ...collection,
      type: 'collection',
      children: []
    }
  })

  // 构建集合层级关系
  collections.forEach(collection => {
    if (collection.parent && map[collection.parent]) {
      map[collection.parent].children.push(map[collection.id])
    } else {
      roots.push(map[collection.id])
    }
  })

  // 添加请求到对应集合或根节点
  requests.forEach(request => {
    if (map[request.collection]) {
      map[request.collection].children.push({
        ...request,
        type: 'request',
        id: `request_${request.id}`
      })
    } else {
      // 没有关联集合的请求，直接添加到根节点
      roots.push({
        ...request,
        type: 'request',
        id: `request_${request.id}`
      })
    }
  })

  return roots
}

const loadExecutions = async () => {
  if (!selectedScenarioCase.value) return

  executionsLoading.value = true
  try {
    const response = await getScenarioCaseExecutions({ scenario_case: selectedScenarioCase.value.id })
    executions.value = response.data.results || response.data
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadExecutionHistory'))
  } finally {
    executionsLoading.value = false
  }
}

const onProjectChange = async () => {
  requestSearchKeyword.value = ''
  // 检查选中的项目是否为HTTP项目
  const selectedProjectData = projects.value.find(p => p.id === selectedProject.value)
  if (selectedProjectData && selectedProjectData.project_type === 'WEBSOCKET') {
    ElMessage.warning(t('apiTesting.messages.warning.websocketNotSupported'))
    // 重置为第一个HTTP项目或清空选择
    const httpProjects = projects.value.filter(project => project.project_type !== 'WEBSOCKET')
    if (httpProjects.length > 0) {
      selectedProject.value = getPreferredProjectId(httpProjects, httpProjects[0].id)
    } else {
      selectedProject.value = null
    }
    return
  }

  selectedScenarioCase.value = null
  await Promise.all([
    refreshScenarioCaseTree(),
    loadEnvironments(),
    loadRequestTree()
  ])
}

const selectScenarioCase = (scenarioCase) => {
  selectedScenarioCase.value = scenarioCase
  loadExecutions()
}

const handleScenarioCaseAction = async ({ action, scenarioCase }) => {
  switch (action) {
    case 'run':
      await runScenarioCase(scenarioCase)
      break
    case 'edit':
      editScenarioCase(scenarioCase)
      break
    case 'duplicate':
      await duplicateScenarioCase(scenarioCase)
      break
    case 'delete':
      await deleteScenarioCase(scenarioCase)
      break
  }
}

const runScenarioCase = async (scenarioCase) => {
  running.value = true
  try {
    const response = await executeScenarioCase(scenarioCase.id)
    currentExecution.value = response.data
    showExecutionDialog.value = true
    await loadExecutions()
    if (response.data.status === 'FAILED') {
      ElMessage.error(t('apiTesting.messages.error.scenarioCaseExecutionFailed'))
    } else {
      ElMessage.success(t('apiTesting.messages.success.scenarioCaseExecuted'))
    }
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.executeFailed'))
  } finally {
    running.value = false
  }
}

// 多选场景用例批量运行
const selectedScenarioCaseIds = ref([])
const onScenarioCaseCheck = () => {
  const nodes = scenarioCaseTreeRef.value?.getCheckedNodes() || []
  selectedScenarioCaseIds.value = nodes.filter(n => n.type === 'scenarioCase').map(n => n.id)
}
const runSelectedScenarioCases = async () => {
  running.value = true
  try {
    for (const id of [...selectedScenarioCaseIds.value]) {
      await executeScenarioCase(id)
    }
    ElMessage.success(t('apiTesting.automation.batchRunSuccess', { count: selectedScenarioCaseIds.value.length }))
    await loadExecutions()
  } catch (e) {
    ElMessage.error(t('apiTesting.automation.batchRunFailed'))
  } finally {
    running.value = false
  }
}

const openCreateScenarioCaseDialog = () => {
  editingScenarioCase.value = null
  Object.assign(scenarioCaseForm, {
    name: '',
    description: '',
    folder: null,
    project: selectedProject.value,
    environment: null
  })
  showCreateScenarioCaseDialog.value = true
}

const editScenarioCase = (scenarioCase) => {
  editingScenarioCase.value = scenarioCase
  scenarioCaseForm.name = scenarioCase.name
  scenarioCaseForm.description = scenarioCase.description
  scenarioCaseForm.folder = scenarioCase.folder || null
  scenarioCaseForm.project = scenarioCase.project
  // 修复：environment字段直接是ID，不需要?.id
  scenarioCaseForm.environment = scenarioCase.environment || null
  showCreateScenarioCaseDialog.value = true
}

const duplicateScenarioCase = async (scenarioCase) => {
  try {
    const newScenarioCase = {
      name: `${scenarioCase.name} - ${t('apiTesting.common.copyText')}`,
      description: scenarioCase.description,
      folder: scenarioCase.folder || null,
      project: scenarioCase.project,
      environment: scenarioCase.environment || null  // 修复：直接使用environment ID
    }
    await createScenarioCase(newScenarioCase)
    ElMessage.success(t('apiTesting.messages.success.copy'))
    await loadScenarioCases()
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.copyFailed'))
  }
}

const deleteScenarioCase = async (scenarioCase) => {
  try {
    await ElMessageBox.confirm(
      t('apiTesting.automation.confirmDeleteScenarioCase', { name: scenarioCase.name }),
      t('apiTesting.messages.confirm.deleteTitle'),
      {
        confirmButtonText: t('apiTesting.common.confirm'),
        cancelButtonText: t('apiTesting.common.cancel'),
        type: 'warning'
      }
    )

    await deleteScenarioCaseApi(scenarioCase.id)
    ElMessage.success(t('apiTesting.messages.success.delete'))

    if (selectedScenarioCase.value?.id === scenarioCase.id) {
      selectedScenarioCase.value = null
    }
    await loadScenarioCases()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(t('apiTesting.messages.error.deleteFailed'))
    }
  }
}

const submitScenarioCaseForm = async () => {
  if (!scenarioCaseFormRef.value) return

  const valid = await scenarioCaseFormRef.value.validate().catch(() => false)
  if (!valid) return

  submittingScenarioCase.value = true
  try {
    const formData = {
      ...scenarioCaseForm,
      project: editingScenarioCase.value?.project ?? selectedProject.value
    }
    let savedScenarioCaseId = editingScenarioCase.value?.id
    if (editingScenarioCase.value) {
      await updateScenarioCase(editingScenarioCase.value.id, formData)
      ElMessage.success(t('apiTesting.messages.success.scenarioCaseUpdated'))
    } else {
      const response = await createScenarioCase(formData)
      savedScenarioCaseId = response.data.id
      ElMessage.success(t('apiTesting.messages.success.scenarioCaseCreated'))
    }

    const formalCaseId = route.query.automation_for_test_case_id
    if (formalCaseId && savedScenarioCaseId) {
      try {
        await createTestCaseAutomationLink(formalCaseId, {
          type: 'api_scenario_case',
          object_id: savedScenarioCaseId
        })
      } catch (error) {
        editingScenarioCase.value = { id: savedScenarioCaseId, project: selectedProject.value }
        ElMessage.error(error.response?.data?.detail || t('testcase.linkAutomationFailed'))
        return
      }

      ElMessage.success(t('testcase.automationLinked'))
      showCreateScenarioCaseDialog.value = false
      await loadScenarioCases()
      return
    }

    showCreateScenarioCaseDialog.value = false
    await loadScenarioCases()
  } catch (error) {
    ElMessage.error(editingScenarioCase.value ? t('apiTesting.messages.error.updateFailed') : t('apiTesting.messages.error.createFailed'))
  } finally {
    submittingScenarioCase.value = false
  }
}

const resetScenarioCaseForm = () => {
  editingScenarioCase.value = null
  Object.assign(scenarioCaseForm, {
    name: '',
    description: '',
    folder: null,
    project: selectedProject.value,
    environment: null
  })
  scenarioCaseFormRef.value?.resetFields()
}

const showAddRequest = async () => {
  requestSearchKeyword.value = ''
  await loadRequestTree()
  showAddRequestDialog.value = true

  // 等待对话框显示完成后再设置勾选状态
  nextTick(() => {
    setTimeout(() => {
      if (requestTreeRef.value && selectedScenarioCase.value) {
        // 获取当前已关联的请求ID
        const existingRequestIds = selectedScenarioCase.value.scenario_case_requests?.map(sr =>
          `request_${sr.request.id}`
        ) || []

        // 设置已关联接口为已勾选状态
        requestTreeRef.value.setCheckedKeys(existingRequestIds, false)
        console.log('设置已关联接口ID:', existingRequestIds)
      }
    }, 200)
  })
}

const onRequestCheck = () => {
  // 请求选择变化处理
}

const addSelectedRequests = async () => {
  const checkedNodes = requestTreeRef.value.getCheckedNodes()
  const requestIds = checkedNodes
    .filter(node => node.type === 'request')
    .map(node => node.id.replace('request_', ''))

  if (requestIds.length === 0) {
    ElMessage.warning(t('apiTesting.messages.warning.selectAtLeastOneRequest'))
    return
  }

  addingRequests.value = true
  try {
    // 这里需要调用添加请求到场景用例的API
    await addRequestsToScenarioCase(selectedScenarioCase.value.id, requestIds)

    ElMessage.success(t('apiTesting.messages.success.addSuccess'))
    showAddRequestDialog.value = false
    // 重新加载当前场景用例详情
    await reloadCurrentScenarioCase()
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.addFailed'))
  } finally {
    addingRequests.value = false
  }
}

const updateRequestEnabled = async (scenarioCaseRequest) => {
  try {
    await updateScenarioCaseRequest(scenarioCaseRequest.id, {
      enabled: scenarioCaseRequest.enabled
    })
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.updateFailed'))
    scenarioCaseRequest.enabled = !scenarioCaseRequest.enabled
  }
}

const editRequestConfig = (scenarioCaseRequest) => {
  editingScenarioCaseRequest.value = scenarioCaseRequest
  assertionDraft.value = normalizeAssertions(scenarioCaseRequest.assertions)
  extractorDraft.value = JSON.parse(JSON.stringify(scenarioCaseRequest.extractors || scenarioCaseRequest.request?.extractors || []))
  requestConfigTab.value = 'assertions'
  showAssertionDialog.value = true
}

const saveScenarioCaseAssertions = async () => {
  if (!editingScenarioCaseRequest.value) return

  savingAssertions.value = true
  try {
    const assertions = assertionDraft.value.map(stripLegacyAssertionValue)
    const extractors = extractorDraft.value
    await patchScenarioCaseRequest(editingScenarioCaseRequest.value.id, { assertions, extractors })
    editingScenarioCaseRequest.value.assertions = assertions
    editingScenarioCaseRequest.value.extractors = extractors
    showAssertionDialog.value = false
    ElMessage.success(t('apiTesting.messages.success.requestConfigSaved'))
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.saveScenarioCaseRequestConfigFailed'))
  } finally {
    savingAssertions.value = false
  }
}

const moveRequest = async (scenarioCaseRequest, direction) => {
  try {
    await moveScenarioCaseRequest(scenarioCaseRequest.id, direction)
    await reloadCurrentScenarioCase()
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.reorderFailed'))
  }
}

const removeRequest = async (scenarioCaseRequest) => {
  try {
    await ElMessageBox.confirm(t('apiTesting.automation.confirmRemoveRequest'), t('apiTesting.automation.confirmRemove'), {
      confirmButtonText: t('apiTesting.common.confirm'),
      cancelButtonText: t('apiTesting.common.cancel'),
      type: 'warning'
    })

    await deleteScenarioCaseRequest(scenarioCaseRequest.id)
    ElMessage.success(t('apiTesting.messages.success.removeSuccess'))
    // 重新加载当前场景用例详情
    await reloadCurrentScenarioCase()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(t('apiTesting.messages.error.removeFailed'))
    }
  }
}

const reloadCurrentScenarioCase = async () => {
  if (!selectedScenarioCase.value) return

  try {
    // 重新加载当前场景用例的详细信息
    const response = await getScenarioCaseDetail(selectedScenarioCase.value.id)
    const updatedScenarioCase = response.data

    // 强制重新设置响应式数据
    selectedScenarioCase.value = { ...updatedScenarioCase }

    // 同时更新场景用例列表中对应的场景用例
    const index = scenarioCases.value.findIndex(scenarioCase => scenarioCase.id === updatedScenarioCase.id)
    if (index !== -1) {
      scenarioCases.value[index] = { ...updatedScenarioCase }
    }
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadScenarioCases'))
  }
}

const viewExecutionDetail = (execution) => {
  currentExecution.value = execution
  showExecutionDialog.value = true
}

const formatExecutionResults = (results) => {
  if (!results || !Array.isArray(results)) return []
  return results
}

onMounted(() => {
  loadProjects()
})
</script>

<style scoped>
.automation-testing {
  padding: 20px;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.header h3 {
  margin: 0;
  color: #303133;
}

.content-layout {
  display: flex;
  flex: 1;
  gap: 20px;
  overflow: hidden;
}

.sidebar {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.project-selector {
  background: white;
  padding: 15px;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
}

.scenario-case-list {
  background: white;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
  overflow: hidden;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 15px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
  font-weight: 500;
}
.list-actions {
  display: flex;
  align-items: center;
}

.create-folder-button {
  font-size: 16px;
}

.scenario-case-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 15px;
  border-bottom: 1px solid #f5f7fa;
  cursor: pointer;
  transition: background-color 0.3s;
}

.scenario-case-item:hover {
  background: #f5f7fa;
}

.scenario-case-item.active {
  background: #e1f3d8;
  border-color: #67c23a;
}

.scenario-case-info {
  flex: 1;
}

.scenario-case-name {
  font-weight: 500;
  margin-bottom: 4px;
}

.scenario-case-tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  padding: 2px 4px;
}
.scenario-case-tree-node .del-icon {
  margin-left: auto;
  visibility: hidden;
  color: #f56c6c;
  cursor: pointer;
}
.folder-context-menu {
  position: fixed;
  z-index: 1000;
  min-width: 140px;
  margin: 0;
  padding: 8px 0;
  list-style: none;
  background: #fff;
  border: 1px solid #e9ecef;
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}
.folder-context-menu li {
  padding: 10px 16px;
  color: #303133;
  font-size: 14px;
  cursor: pointer;
}
.folder-context-menu li:hover {
  background: #f5f7fa;
}
.folder-context-menu li.danger {
  color: #f56c6c;
}
.scenario-case-tree-node:hover .del-icon {
  visibility: visible;
}
.batch-bar {
  padding: 6px 0;
}
.assertion-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}

.scenario-case-meta {
  font-size: 12px;
  color: #909399;
}

.main-content {
  flex: 1;
  min-width: 0;
  background: white;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.scenario-case-detail {
  flex: 1;
  min-height: 0;
  padding: 20px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.scenario-case-header {
  margin-bottom: 30px;
}

.scenario-case-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.scenario-case-title h4 {
  margin: 0;
  color: #303133;
}

.scenario-case-actions {
  display: flex;
  gap: 10px;
}

.scenario-case-description {
  color: #606266;
  margin-bottom: 10px;
}

.scenario-case-meta {
  display: flex;
  gap: 15px;
  align-items: center;
}

.meta-text {
  font-size: 12px;
  color: #909399;
}

.requests-section,
.executions-section {
  margin-bottom: 30px;
}

.requests-section {
  flex: 0 0 auto;
}

.executions-section {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.execution-history-table {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.section-header h5 {
  margin: 0;
  color: #303133;
  font-size: 16px;
}

.add-request-content {
  max-height: 400px;
  display: flex;
  flex-direction: column;
}

.request-search {
  margin-bottom: 12px;
}

.request-selector {
  min-height: 0;
  overflow-y: auto;
}

.request-search-empty {
  padding: 24px 0;
  color: #909399;
  text-align: center;
}

.request-tree-node {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
}

.method-tag {
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 2px;
  color: white;
  font-weight: bold;
  margin-left: auto;
}

.method-tag.get { background: #67c23a; }
.method-tag.post { background: #409eff; }
.method-tag.put { background: #e6a23c; }
.method-tag.delete { background: #f56c6c; }
.method-tag.patch { background: #909399; }

.execution-detail {
  max-height: 70vh;
  overflow-y: auto;
}

.execution-summary {
  margin-bottom: 30px;
  padding: 20px;
  background: #f8f9fa;
  border-radius: 6px;
}

.execution-results h4 {
  margin: 0 0 15px 0;
  color: #303133;
}
</style>
