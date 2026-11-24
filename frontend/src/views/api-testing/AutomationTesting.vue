<template>
  <div class="automation-testing page-container" @click="showFolderContextMenu = false">
    <div class="page-header">
      <h1 class="page-title">{{ $t('apiTesting.automation.title') }}</h1>
      <div class="page-header-actions">
        <el-select
          v-model="selectedProject"
          class="project-selector"
          :placeholder="$t('apiTesting.common.selectProject')"
          @change="onProjectChange"
        >
          <el-option
            v-for="project in httpProjects"
            :key="project.id"
            :label="project.name"
            :value="project.id"
          />
        </el-select>
        <el-select
          v-model="pageEnvironment"
          class="environment-selector"
          clearable
          :placeholder="$t('apiTesting.scenarioEditor.selectEnvironment')"
          @change="onPageEnvironmentChange"
        >
          <el-option v-for="env in environments" :key="env.id" :label="env.name" :value="env.id" />
        </el-select>
        <el-button type="primary" @click="openCreateScenarioCaseDialog">
          <el-icon><Plus /></el-icon>
          {{ $t('apiTesting.automation.createScenarioCase') }}
        </el-button>
      </div>
    </div>

    <div class="content-layout">
      <ResizableSplitPane v-model="sidebarWidth" :min-size="240" :max-size="520" :second-min-size="480" storage-key="api-testing.automation.sidebar-width">
        <template #first>
      <!-- 左侧场景树：布局与接口管理的接口树对齐 -->
          <div class="sidebar">
        <div class="sidebar-header">
          <div class="header-actions">
            <el-input
              v-model="scenarioKeyword"
              :placeholder="$t('apiTesting.automation.searchScenario')"
              size="small"
              clearable
              style="flex: 1; min-width: 0;"
              @input="filterScenarioTree"
              @clear="filterScenarioTree"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <div class="sidebar-actions">
              <el-button
                size="small"
                type="primary"
                :icon="FolderAdd"
                :disabled="!selectedProject"
                :title="$t('apiTesting.automation.folder.create')"
                @click="createFolder()"
              />
              <el-button
                size="small"
                type="success"
                :icon="VideoPlay"
                :disabled="selectedScenarioCaseIds.length === 0"
                :title="$t('apiTesting.automation.batchRun', { count: selectedScenarioCaseIds.length })"
                @click="runSelectedScenarioCases"
              >{{ selectedScenarioCaseIds.length }}</el-button>
            </div>
          </div>
        </div>

        <div class="scenario-tree">
          <el-tree
            ref="scenarioCaseTreeRef"
            :data="scenarioCaseTree"
            :props="{ label: 'name', children: 'children' }"
            :default-expanded-keys="expandedFolderKeys"
            :default-checked-keys="selectedScenarioCaseIds"
            :auto-expand-parent="false"
            node-key="id"
            show-checkbox
            :check-on-click-node="false"
            :check-on-click-leaf="false"
            :filter-node-method="filterScenarioNode"
            @node-contextmenu="onFolderNodeContextMenu"
            @node-expand="onTreeNodeExpand"
            @node-collapse="onTreeNodeCollapse"
            @check="onScenarioCaseCheck"
          >
            <template #default="{ data }">
              <div
                class="scenario-case-tree-node"
                :class="{ active: data.type === 'scenarioCase' && (loadingScenarioCaseId ?? selectedScenarioCase?.id) === data.id }"
                @click="data.type === 'scenarioCase' && selectScenarioCase(data)"
              >
                <el-icon class="node-icon">
                  <Folder v-if="data.type === 'folder'" />
                  <Document v-else />
                </el-icon>
                <span class="scenario-case-name" :title="data.name">{{ data.name }}</span>
                <span v-if="data.type === 'scenarioCase'" class="node-actions" @click.stop>
                  <el-button
                    link
                    size="small"
                    :icon="CopyDocument"
                    @click="handleScenarioCaseAction({ action: 'duplicate', scenarioCase: data })"
                  />
                  <el-button
                    link
                    size="small"
                    type="danger"
                    :icon="Delete"
                    @click="handleScenarioCaseAction({ action: 'delete', scenarioCase: data })"
                  />
                </span>
              </div>
            </template>
          </el-tree>
        </div>
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
        </template>

        <template #second>
          <!-- 右侧场景用例详情 -->
          <div class="main-content">
        <div v-if="!selectedScenarioCase" class="empty-state">
          <el-empty :description="$t('apiTesting.automation.selectScenarioCaseHint')" />
        </div>

        <div v-else class="scenario-case-detail">
          <!-- 场景编排编辑器：步骤树 + 按类型切换的编辑器 + 执行结果 -->
          <ScenarioEditor
            :scenario-case="selectedScenarioCase"
            :project-scenarios="scenarioCases"
            :environment-id="pageEnvironment"
            @updated="onScenarioEdited"
          />
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
          <el-table :data="formatExecutionResults(currentExecutionSteps)">
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
  Plus, VideoPlay, Search, Folder, FolderAdd, Document, Delete, CopyDocument
} from '@element-plus/icons-vue'
import ScenarioEditor from './components/scenario/ScenarioEditor.vue'
import {
  createScenarioCase,
  createScenarioCaseFolder,
  deleteScenarioCase as deleteScenarioCaseApi,
  deleteScenarioCaseFolder,
  duplicateScenarioCase as duplicateScenarioCaseApi,
  executeScenarioCase,
  getExecutionSteps,
  getApiProjects,
  getEnvironments,
  getScenarioCaseDetail,
  getScenarioCaseTree,
  patchScenarioCase,
  updateScenarioCase,
  updateScenarioCaseFolder
} from '@/api/api-testing'
import { createTestCaseAutomationLink } from '@/api/testcases'
import dayjs from 'dayjs'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'
import { findRememberedEnvironment, rememberEnvironment } from '@/utils/environmentPreference'

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
// 场景树只保存轻量字段；这里是点击用例后单独加载的详情。
const selectedScenarioCase = ref(null)
const loadingScenarioCaseId = ref(null)
let scenarioCaseDetailRequestToken = 0
const environments = ref([])
const pageEnvironment = ref(null)
const running = ref(false)
const scenarioCaseTreeRef = ref(null)
const scenarioKeyword = ref('')

// 与接口树一致的筛选交互：输入即过滤，清空即恢复。
const filterScenarioNode = (value, data) => {
  if (!value) return true
  return String(data?.name || '').toLowerCase().includes(String(value).toLowerCase())
}

const filterScenarioTree = () => {
  scenarioCaseTreeRef.value?.filter(scenarioKeyword.value)
}
const showCreateScenarioCaseDialog = ref(false)
const showExecutionDialog = ref(false)
const editingScenarioCase = ref(null)
const submittingScenarioCase = ref(false)
const currentExecution = ref(null)
const currentExecutionSteps = ref([])
const scenarioCaseFormRef = ref()

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
  if (typeof execution.results?.average_response_time === 'number') {
    const average = execution.results.average_response_time
    return average < 1000 ? `${Math.round(average)}ms` : `${(average / 1000).toFixed(1)}s`
  }
  return '-'
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
      if (listedCase) selectScenarioCase(listedCase, linkedCase)
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

// 编辑器保存/执行后只刷新当前用例，并同步树节点上的名称等字段，不重拉整棵树。
const onScenarioEdited = () => {
  reloadCurrentScenarioCase()
}

// 用详情或写接口返回的数据就地更新/追加树节点，只保留场景树需要的轻量字段。
const upsertScenarioCaseRow = (data) => {
  const row = {
    id: data.id,
    name: data.name,
    folder: data.folder,
    project: data.project,
    environment: data.environment
  }
  const index = scenarioCases.value.findIndex(item => item.id === row.id)
  if (index === -1) {
    scenarioCases.value = [...scenarioCases.value, row]
    return
  }
  // 字段没变时不写回，避免每次点击用例都触发整棵树重建（会丢失展开/勾选状态）。
  const current = scenarioCases.value[index]
  if (Object.keys(row).some(key => current[key] !== row[key])) scenarioCases.value[index] = row
}

const resetScenarioCaseSelection = () => {
  // 作废仍在途中的详情请求，避免旧请求返回后重新选中用例。
  scenarioCaseDetailRequestToken += 1
  loadingScenarioCaseId.value = null
  selectedScenarioCase.value = null
}

const loadScenarioCases = async () => {
  const projectId = selectedProject.value
  if (!projectId) {
    scenarioCases.value = []
    scenarioCaseFolders.value = []
    return
  }

  try {
    const response = await getScenarioCaseTree({ project: projectId })
    // 快速切换项目时先发出的请求可能后返回，不能用它覆盖当前项目的场景树。
    if (projectId !== selectedProject.value) return
    scenarioCases.value = response.data.scenario_cases || []
    scenarioCaseFolders.value = response.data.folders || []
    // 进入项目时默认展开根文件夹，之后的展开/折叠以用户操作为准。
    expandedFolderKeys.value = scenarioCaseFolders.value
      .filter(folder => !folder.parent && !folder.name.startsWith('22'))
      .map(folder => 'f-' + folder.id)
  } catch (error) {
    if (projectId !== selectedProject.value) return
    ElMessage.error(t('apiTesting.messages.error.loadScenarioCases'))
  }
}

const refreshScenarioCaseTree = async () => {
  await loadScenarioCases()
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
      selectedScenarioCase.value = { ...selectedScenarioCase.value, folder: updatedScenarioCase.folder }
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
    // 展开父文件夹，新建的子文件夹才看得到。
    if (parentId) onTreeNodeExpand({ id: 'f-' + parentId })
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
    if (selectedScenarioCase.value && folderIds.has(selectedScenarioCase.value.folder)) {
      selectedScenarioCase.value = { ...selectedScenarioCase.value, folder: null }
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
// 树数据每次变化 el-tree 都会重建全部节点，不保留用户的展开/折叠，
// 所以由页面记录已展开的文件夹，重建时通过 default-expanded-keys 还原。
const expandedFolderKeys = ref([])
const onTreeNodeExpand = (data) => {
  if (!expandedFolderKeys.value.includes(data.id)) {
    expandedFolderKeys.value = [...expandedFolderKeys.value, data.id]
  }
}
const onTreeNodeCollapse = (data) => {
  expandedFolderKeys.value = expandedFolderKeys.value.filter(key => key !== data.id)
}

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
    // 优先恢复记忆的环境；不在可用范围内时回退为空
    const remembered = findRememberedEnvironment(environments.value)
    if (remembered) {
      pageEnvironment.value = remembered.id
    } else if (pageEnvironment.value && !environments.value.some(env => String(env.id) === String(pageEnvironment.value))) {
      pageEnvironment.value = null
    }
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadEnvironments'))
  }
}

const onProjectChange = async () => {
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

  resetScenarioCaseSelection()
  await Promise.all([
    refreshScenarioCaseTree(),
    loadEnvironments()
  ])
}

// 点击树节点后才加载该用例详情（描述、创建人、时间等）；已有详情时直接复用。
const selectScenarioCase = async (scenarioCase, loadedDetail = null) => {
  const token = ++scenarioCaseDetailRequestToken
  loadingScenarioCaseId.value = scenarioCase.id
  try {
    const detail = loadedDetail || (await getScenarioCaseDetail(scenarioCase.id)).data
    // 连续点击多个用例时，只采用最后一次点击的结果。
    if (token !== scenarioCaseDetailRequestToken) return
    selectedScenarioCase.value = detail
    upsertScenarioCaseRow(detail)
    pageEnvironment.value = detail.environment ?? null
  } catch (error) {
    if (token === scenarioCaseDetailRequestToken) {
      ElMessage.error(t('apiTesting.messages.error.loadScenarioCases'))
    }
  } finally {
    if (token === scenarioCaseDetailRequestToken) loadingScenarioCaseId.value = null
  }
}

// 顶部环境选择器统一控制当前场景的执行环境
const onPageEnvironmentChange = async (value) => {
  rememberEnvironment(value)
  const target = selectedScenarioCase.value
  if (!target) return
  try {
    await patchScenarioCase(target.id, { environment: value || null })
    target.environment = value || null
    const item = scenarioCases.value.find(sc => String(sc.id) === String(target.id))
    if (item) item.environment = value || null
  } catch (error) {
    ElMessage.error(t('apiTesting.common.updateFailed'))
  }
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
    currentExecutionSteps.value = []
    showExecutionDialog.value = true
    await loadExecutionSteps(response.data)
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
// 树数据变化后节点已重建：勾选由 default-checked-keys 还原，这里再同步一次勾选结果
// （去掉已删除或不属于当前项目的用例），并重新应用搜索关键字。
watch(scenarioCaseTree, async () => {
  await nextTick()
  onScenarioCaseCheck()
  if (scenarioKeyword.value) filterScenarioTree()
})
const runSelectedScenarioCases = async () => {
  running.value = true
  try {
    for (const id of [...selectedScenarioCaseIds.value]) {
      await executeScenarioCase(id)
    }
    ElMessage.success(t('apiTesting.automation.batchRunSuccess', { count: selectedScenarioCaseIds.value.length }))
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
    environment: pageEnvironment.value || null
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
    const response = await duplicateScenarioCaseApi(scenarioCase.id, {
      name: `${scenarioCase.name} - ${t('apiTesting.common.copyText')}`
    })
    ElMessage.success(t('apiTesting.messages.success.copy'))
    upsertScenarioCaseRow(response.data)
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

    if (selectedScenarioCase.value?.id === scenarioCase.id || loadingScenarioCaseId.value === scenarioCase.id) {
      resetScenarioCaseSelection()
    }
    scenarioCases.value = scenarioCases.value.filter(item => item.id !== scenarioCase.id)
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
      const response = await updateScenarioCase(editingScenarioCase.value.id, formData)
      upsertScenarioCaseRow(response.data)
      ElMessage.success(t('apiTesting.messages.success.scenarioCaseUpdated'))
    } else {
      const response = await createScenarioCase(formData)
      savedScenarioCaseId = response.data.id
      upsertScenarioCaseRow(response.data)
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
      return
    }

    showCreateScenarioCaseDialog.value = false
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
    environment: pageEnvironment.value || null
  })
  scenarioCaseFormRef.value?.resetFields()
}

const reloadCurrentScenarioCase = async () => {
  const scenarioCaseId = selectedScenarioCase.value?.id
  if (!scenarioCaseId) return

  try {
    // 重新加载当前场景用例的详细信息
    const response = await getScenarioCaseDetail(scenarioCaseId)
    const updatedScenarioCase = response.data

    // 等待期间该用例可能已被删除或已切换项目，不能把它重新加回当前场景树。
    if (!scenarioCases.value.some(item => item.id === scenarioCaseId)) return

    // 同时更新场景树中对应的节点
    upsertScenarioCaseRow(updatedScenarioCase)
    // 等待期间可能已切换到其他用例，此时不能覆盖当前选中的详情。
    if (selectedScenarioCase.value?.id === scenarioCaseId) {
      selectedScenarioCase.value = { ...updatedScenarioCase }
    }
  } catch (error) {
    // 用例已被删除或已切走时，这次刷新失败与当前界面无关，不提示。
    if (selectedScenarioCase.value?.id === scenarioCaseId) {
      ElMessage.error(t('apiTesting.messages.error.loadScenarioCases'))
    }
  }
}

const viewExecutionDetail = async (execution) => {
  currentExecution.value = execution
  currentExecutionSteps.value = []
  showExecutionDialog.value = true
  await loadExecutionSteps(execution)
}

const loadExecutionSteps = async (execution) => {
  try {
    const response = await getExecutionSteps(execution.id)
    currentExecutionSteps.value = response.data.steps || []
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadExecutionHistory'))
  }
}

const formatExecutionResults = (results) => {
  if (!results || !Array.isArray(results)) return []
  return results.map(step => ({
    name: step.name_snapshot,
    method: step.request_data?.method,
    passed: step.status === 'PASSED',
    status: step.status,
    status_code: step.response_data?.status_code,
    response_time: step.response_data?.response_time,
    error: step.error,
  }))
}

onMounted(() => {
  loadProjects()
})
</script>

<style scoped>
.automation-testing {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.page-header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.project-selector {
  width: 200px;
}

.environment-selector {
  width: 180px;
}

.page-header-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.content-layout {
  display: flex;
  flex: 1;
  gap: 20px;
  overflow: hidden;
}

.sidebar {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-right: 1px solid #e4e7ed;
  box-shadow: 2px 0 6px rgba(0, 0, 0, 0.05);
  overflow: hidden;
}

/* 与接口管理的接口树保持一致：项目选择 + 搜索 + 操作按钮 */
.sidebar-header {
  padding: 10px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.header-actions .el-input {
  flex: 1 1 150px;
  min-width: 120px;
}

.sidebar-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  margin-left: auto;
}

.sidebar-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.scenario-tree {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 10px;
}

.scenario-tree :deep(.el-tree-node__content) {
  min-width: 0;
  overflow: hidden;
}

.folder-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 140px;
  margin: 0;
  padding: 8px 0;
  list-style: none;
  background: var(--el-bg-color-overlay, #fff);
  border: 1px solid #e9ecef;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}

.folder-context-menu li {
  padding: 10px 16px;
  font-size: 14px;
  color: #303133;
  cursor: pointer;
  transition: all 0.2s ease;
}

.folder-context-menu li:hover {
  background: var(--el-fill-color-light);
  color: var(--el-color-primary);
  font-weight: 500;
}

.folder-context-menu li.danger { color: var(--el-color-danger); }
.folder-context-menu li.danger:hover { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }

.scenario-case-tree-node {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  padding: 4px 0;
}

.scenario-case-tree-node .node-icon {
  font-size: 16px;
  color: #606266;
  flex-shrink: 0;
}

.scenario-case-name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scenario-case-tree-node:hover .scenario-case-name {
  color: #409eff;
}

.scenario-case-tree-node.active .scenario-case-name {
  color: var(--el-color-primary);
}

.scenario-case-tree-node .node-actions {
  display: none;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.scenario-case-tree-node:hover .node-actions,
.scenario-case-tree-node.active .node-actions {
  display: inline-flex;
}

.scenario-case-tree-node .node-actions :deep(.el-button) {
  margin-left: 0;
  padding: 2px;
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
  padding: 10px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* 编排编辑器占满详情区，内部自行滚动 */
.scenario-case-detail :deep(.scenario-editor) {
  flex: 1 1 auto;
  min-height: 0;
  height: auto;
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
