<template>
  <div class="script-list">
    <div class="page-header">
      <h1 class="page-title">{{ $t('uiAutomation.script.title') }}</h1>
      <div class="header-actions">
        <el-select v-model="selectedProject" :placeholder="$t('uiAutomation.common.selectProject')" style="width: 200px; margin-right: 15px" @change="onProjectChange">
          <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
        </el-select>
        <el-button type="primary" @click="openRecording">
          <el-icon><Plus /></el-icon>
          {{ $t('uiAutomation.recording.recordScript') }}
        </el-button>
        <el-button type="primary" plain :loading="uploadingScript" @click="openUpload">
          <el-icon><Upload /></el-icon>
          {{ $t('uiAutomation.script.uploadScript') }}
        </el-button>
        <input
          ref="scriptFileInput"
          class="script-file-input"
          type="file"
          accept=".py"
          @change="handleScriptFileChange"
        >
      </div>
    </div>

    <div class="main-content">
      <el-table :data="scripts" stripe style="width: 100%">
        <el-table-column type="index" :label="$t('uiAutomation.script.index')" width="60" />
        <el-table-column :label="$t('uiAutomation.script.projectColumn')" min-width="180">
          <template #default="{ row }">
            {{ row.project?.name || $t('uiAutomation.script.unknownProject') }}
          </template>
        </el-table-column>
        <el-table-column prop="name" :label="$t('uiAutomation.script.nameColumn')" min-width="340" show-overflow-tooltip />
        <el-table-column :label="$t('uiAutomation.script.languageColumn')" min-width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.language === 'python' ? 'success' : 'primary'">
              {{ getLanguageText(row.language) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('uiAutomation.script.frameworkColumn')" min-width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="row.framework === 'playwright' ? 'warning' : 'info'">
              {{ getFrameworkText(row.framework) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" :label="$t('uiAutomation.script.createTimeColumn')" min-width="160">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column :label="$t('uiAutomation.script.operationColumn')" :width="operationColumnWidth">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" text type="primary" @click="replayScript(row)">
                <el-icon><VideoPlay /></el-icon>
                {{ $t('uiAutomation.replay.button') }}
              </el-button>
              <el-button size="small" text type="primary" @click="historyScript(row)">
                <el-icon><Clock /></el-icon>
                {{ $t('uiAutomation.history.button') }}
              </el-button>
              <el-button size="small" text @click="viewScript(row)">
                <el-icon><View /></el-icon>
                {{ $t('uiAutomation.script.viewDetail') }}
              </el-button>
              <el-button size="small" text @click="editScript(row)">
                <el-icon><Edit /></el-icon>
                {{ $t('uiAutomation.script.edit') }}
              </el-button>
              <el-button size="small" text type="success" @click="openConvertDialog(row)">
                {{ $t('uiAutomation.script.convertToTestCase') }}
              </el-button>
              <el-tooltip :content="$t('uiAutomation.script.delete')">
                <el-button size="small" text type="danger" :aria-label="$t('uiAutomation.script.delete')" @click="deleteScript(row)">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </div>

    <!-- 查看详情对话框 -->
    <el-dialog v-model="showDetailDialog" :title="$t('uiAutomation.script.scriptDetail')" width="70%">
      <div v-if="currentScript" class="script-detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item :label="$t('uiAutomation.script.scriptName')" :span="2">{{ currentScript.name }}</el-descriptions-item>
          <el-descriptions-item :label="$t('uiAutomation.script.project')">{{ currentScript.project?.name || $t('uiAutomation.script.unknownProject') }}</el-descriptions-item>
          <el-descriptions-item :label="$t('uiAutomation.script.language')">{{ getLanguageText(currentScript.language) }}</el-descriptions-item>
          <el-descriptions-item :label="$t('uiAutomation.script.framework')">{{ getFrameworkText(currentScript.framework) }}</el-descriptions-item>
          <el-descriptions-item :label="$t('uiAutomation.script.scriptType')">{{ getScriptTypeText(currentScript.script_type) }}</el-descriptions-item>
          <el-descriptions-item :label="$t('uiAutomation.script.createTime')" :span="2">{{ formatTime(currentScript.created_at) }}</el-descriptions-item>
          <el-descriptions-item :label="$t('uiAutomation.script.updateTime')" :span="2">{{ formatTime(currentScript.updated_at) }}</el-descriptions-item>
        </el-descriptions>

        <div class="script-content">
          <h4>{{ $t('uiAutomation.script.scriptContent') }}</h4>
          <pre class="code-view">{{ currentScript.content || $t('uiAutomation.script.noContent') }}</pre>
        </div>
      </div>
      <template #footer>
        <el-button @click="showDetailDialog = false">{{ $t('uiAutomation.script.close') }}</el-button>
        <el-button type="primary" @click="editScript(currentScript)">{{ $t('uiAutomation.script.editScript') }}</el-button>
      </template>
    </el-dialog>

    <!-- 编辑对话框 -->
    <el-dialog v-model="showEditDialog" title="步骤与代码同步编辑" width="96%" top="3vh" :close-on-click-modal="false" class="sync-editor-dialog" @opened="createMonaco" @closed="disposeMonaco">
      <div v-if="editingScript" class="script-editor">
        <div class="sync-editor-topbar">
          <el-input v-model="editingScript.name" maxlength="200" class="script-title-input" />
          <el-tag :type="syncState === 'synced' ? 'success' : 'warning'">{{ syncStateText }}</el-tag>
          <span class="topbar-actions">
            <el-button size="small" @click="parseCode" :loading="syncing">代码反解析回步骤</el-button>
            <el-button size="small" @click="generateFromSteps" :loading="syncing">重新生成代码</el-button>
          </span>
        </div>
        <div class="sync-editor-body">
          <div class="steps-pane">
            <StructuredStepEditor v-model="editingSteps" :elements="editorElements" :storage-states="storageStates" title="录制步骤" @change="onStepsChanged" />
          </div>
          <div class="code-pane">
            <div class="pane-heading">生成的 Playwright 代码 <el-tag size="small" type="success">步骤 ↔ 代码同步</el-tag></div>
            <div ref="monacoHost" class="monaco-host" />
          </div>
        </div>
        <el-alert v-if="editorError" :title="editorError" type="error" :closable="true" @close="editorError = ''" />
      </div>
      <template #footer>
        <el-button @click="showEditDialog = false">{{ $t('uiAutomation.common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEditedScript" :loading="saving">保存脚本和步骤</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showConvertDialog" :title="$t('uiAutomation.script.convertToTestCase')" width="78%" :close-on-click-modal="false">
      <el-form label-width="90px" class="convert-form">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="用例名称" required><el-input v-model="convertForm.name" maxlength="200" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="起始 URL"><el-input v-model="convertForm.startUrl" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="用例描述"><el-input v-model="convertForm.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <el-table :data="convertSteps" stripe max-height="280" size="small">
        <el-table-column type="index" label="#" width="55" />
        <el-table-column label="动作类型" width="120"><template #default="{ row }">{{ actionLabel(row.action_type) }}</template></el-table-column>
        <el-table-column label="元素 / 定位器" min-width="260"><template #default="{ row }">{{ getStepElementText(row) }}</template></el-table-column>
        <el-table-column prop="description" label="步骤描述" min-width="220" show-overflow-tooltip />
        <el-table-column prop="input_value" label="输入值" min-width="150" show-overflow-tooltip />
        <el-table-column prop="assert_value" label="断言" min-width="130" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button @click="showConvertDialog = false">取消</el-button>
        <el-button type="primary" @click="convertToTestCase" :loading="converting">确认入库</el-button>
      </template>
    </el-dialog>

    <RecordingWizard v-model:visible="recordingVisible" :project-id="selectedProject" @imported="openImportedScript" />
    <ScriptReplayDialog v-model:visible="replayVisible" :script="currentScript" />
    <ScriptHistoryDialog v-model:visible="historyVisible" :script="currentScript" />
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, View, Edit, Delete, VideoPlay, Clock, Upload } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'

import {
  getUiProjects,
  getTestScripts,
  deleteTestScript,
  importRecordedScript,
  getElements,
  getStorageStates,
  getScriptSteps,
  parseScriptSteps,
  saveScriptEditor,
  generateScriptCode,
  convertScriptToTestCase
} from '@/api/ui_automation'
import StructuredStepEditor from '@/components/ui-automation/StructuredStepEditor.vue'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import 'monaco-editor/esm/vs/editor/browser/widget/codeEditor/editor.css'
import RecordingWizard from './RecordingWizard.vue'
import ScriptReplayDialog from './ScriptReplayDialog.vue'
import ScriptHistoryDialog from './ScriptHistoryDialog.vue'

const router = useRouter()
const { t, locale } = useI18n()

// 操作列宽度按语言取：中文文案更短，用 340px 更紧凑；英文最长，取上限 420px。
const operationColumnWidth = computed(() => (String(locale.value).startsWith('zh') ? 340 : 380))

// 响应式数据
const projects = ref([])
const selectedProject = ref('')
useRememberedProject(selectedProject, projects)
const scripts = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 对话框控制
const showDetailDialog = ref(false)
const showEditDialog = ref(false)
const recordingVisible = ref(false)
const replayVisible = ref(false)
const historyVisible = ref(false)
const scriptFileInput = ref(null)
const uploadingScript = ref(false)

// 当前操作的脚本
const currentScript = ref(null)
const editingScript = ref(null)
const saving = ref(false)
const syncing = ref(false)
const converting = ref(false)
const editingSteps = ref([])
const editorElements = ref([])
const storageStates = ref([])
const monacoHost = ref(null)
const editorError = ref('')
const syncState = ref('synced')
const showConvertDialog = ref(false)
const convertScript = ref(null)
const convertSteps = ref([])
const convertForm = ref({ name: '', startUrl: '', description: '' })
let codeEditor = null
let suppressCodeChange = false
let generateTimer = null
const syncStateText = computed(() => ({ synced: '已同步', steps: '步骤待生成代码', code: '代码待反解析' }[syncState.value]))
// 兼容后端大写动作和编辑器驼峰动作，保证两侧显示一致。
const normalizeAction = value => ({
  CLICK: 'click', INPUT: 'fill', FILL: 'fill', PRESS: 'press', SELECT: 'select',
  CHECK: 'check', UNCHECK: 'uncheck', HOVER: 'hover', GETTEXT: 'getText',
  GET_TEXT: 'getText', WAITFOR: 'waitFor', WAIT_FOR: 'waitFor', SCROLL: 'scroll', SCREENSHOT: 'screenshot',
  ASSERT: 'assert', VERIFY: 'assert', WAIT: 'wait', SWITCHTAB: 'switchTab',
  SWITCH_TAB: 'switchTab', NAVIGATE: 'navigate', STORAGE_STATE: 'storageState', STORAGESTATE: 'storageState'
}[String(value || '').replace(/([a-z])([A-Z])/g, '$1_$2').toUpperCase()] || value)
const actionLabel = value => ({
  click: '点击', fill: '输入文本', press: '按键', select: '选择选项', check: '勾选', uncheck: '取消勾选',
  getText: '获取文本', waitFor: '等待元素', hover: '悬停', scroll: '滚动', screenshot: '截图',
  assert: '断言', wait: '等待', switchTab: '切换标签页', navigate: '跳转URL', storageState: '登录态'
}[normalizeAction(value)] || value || '-')
const getStepElementText = step => {
  const element = step.target_element
  if (element) {
    const strategy = element.locator_strategy?.name || element.locator_strategy || ''
    const value = element.locator_value || ''
    return `${element.name || '元素'}${strategy || value ? ` (${strategy}${strategy && value ? '=' : ''}${value})` : ''}`
  }
  const locator = step.action_params?.locator
  return locator ? `${locator.strategy || '定位器'}=${locator.value || '-'}` : '-'
}

// 加载项目列表
const loadProjects = async () => {
  try {
    const response = await getUiProjects({ page_size: 100 })
    projects.value = response.data.results || response.data
  } catch (error) {
    ElMessage.error(t('uiAutomation.script.messages.loadProjectsFailed'))
    console.error('获取项目列表失败:', error)
  }
}

// 加载脚本列表
const loadScripts = async () => {
  if (!selectedProject.value) {
    scripts.value = []
    total.value = 0
    return
  }

  try {
    const response = await getTestScripts({
      project: selectedProject.value,
      page: currentPage.value,
      page_size: pageSize.value
    })

    // 处理分页响应
    if (response.data.results) {
      scripts.value = response.data.results
      total.value = response.data.count || 0
    } else {
      scripts.value = response.data
      total.value = response.data.length
    }
  } catch (error) {
    ElMessage.error(t('uiAutomation.script.messages.loadScriptsFailed'))
    console.error('获取脚本列表失败:', error)
  }
}

// 项目切换
const onProjectChange = async () => {
  currentPage.value = 1
  await loadScripts()
}

// 页面大小改变
const handleSizeChange = async () => {
  currentPage.value = 1
  await loadScripts()
}

// 当前页改变
const handleCurrentChange = async () => {
  await loadScripts()
}

// 打开录制向导
const openRecording = () => {
  if (!selectedProject.value) {
    ElMessage.warning(t('uiAutomation.scriptEditor.messages.selectProject'))
    return
  }
  recordingVisible.value = true
}

const openUpload = () => {
  if (!selectedProject.value) {
    ElMessage.warning(t('uiAutomation.scriptEditor.messages.selectProject'))
    return
  }
  scriptFileInput.value?.click()
}

const handleScriptFileChange = async event => {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.py')) {
    ElMessage.warning(t('uiAutomation.recording.pythonFileOnly'))
    return
  }
  if (file.size > 2 * 1024 * 1024) {
    ElMessage.warning(t('uiAutomation.recording.fileTooLarge'))
    return
  }

  uploadingScript.value = true
  try {
    const content = await file.text()
    await importRecordedScript({ project: selectedProject.value, name: file.name, content })
    ElMessage.success(t('uiAutomation.recording.importSuccess'))
    await loadScripts()
  } catch (error) {
    ElMessage.error(error.response?.data?.error || t('uiAutomation.recording.importFailed'))
  } finally {
    uploadingScript.value = false
  }
}

const openImportedScript = async script => {
  await loadScripts()
  router.push({ path: '/ui-automation/scripts/editor', query: { scriptId: script.id } })
}

const replayScript = script => {
  currentScript.value = script
  replayVisible.value = true
}

const historyScript = script => {
  currentScript.value = script
  historyVisible.value = true
}

const handleRowCommand = (command, script) => {
  if (command === 'detail') viewScript(script)
  if (command === 'edit') editScript(script)
  if (command === 'delete') deleteScript(script)
}

// 查看脚本详情
const viewScript = (script) => {
  currentScript.value = script
  showDetailDialog.value = true
}

// 编辑脚本
// 打开同步编辑器时同时加载已保存步骤和项目元素，保证左右两侧能立即回显。
const editScript = async (script) => {
  editingScript.value = { ...script, name: (script.name || '').replace(/\.py$/i, '') }
  editingSteps.value = []
  editorError.value = ''
  syncState.value = 'synced'
  showDetailDialog.value = false
  showEditDialog.value = true
  try {
    const [stepResponse, elementResponse] = await Promise.all([
      getScriptSteps({ script: script.id, page_size: 1000 }),
      getElements({ project: script.project?.id || selectedProject.value, page_size: 1000 })
    ])
    let rawSteps = stepResponse.data.results || stepResponse.data
    editorElements.value = elementResponse.data.results || elementResponse.data
    const storageResponse = await getStorageStates({ project: script.project?.id || selectedProject.value, page_size: 200 })
    storageStates.value = storageResponse.data.results || storageResponse.data
    if (!rawSteps.length) {
      const parsed = await parseScriptSteps(script.id, script.content || '')
      rawSteps = parsed.data.steps.map((step, index) => ({
        ...step, id: `parsed-${index}-${Date.now()}`
      }))
    }
    editingSteps.value = rawSteps.map(step => {
      const element = step.target_element
      const actionType = normalizeAction(step.action_type)
      const actionParams = { ...(step.action_params || {}) }
      // 登录态动作复用手动用例的固定参数结构，兼容历史步骤中的空 action_params。
      if (actionType === 'storageState') {
        Object.assign(actionParams, {
          operation: actionParams.operation || 'save',
          mode: actionParams.mode || 'save_as',
          name: actionParams.name || '',
          storage_state_id: actionParams.storage_state_id || null
        })
      }
      const locator = element
        ? { strategy: element.locator_strategy?.name?.toLowerCase(), value: element.locator_value, options: element.locator_options || {} }
        : (actionParams.locator || {})
      const strategyName = locator.strategy || 'role'
      return {
        ...step,
        action_type: actionType,
        action_params: actionParams,
        id: step.id,
        element_id: element?.id || step.element_id || '',
        element_page: element?.page || '',
        locator_strategy: strategyName,
        locator_value: locator.value || element?.locator_value || '',
        locator_options: { ...(locator.options || element?.locator_options || {}) },
        locator_options_json: JSON.stringify(locator.options || element?.locator_options || {}),
        locator_name: locator.options?.name || element?.locator_options?.name || '',
        input_value: step.input_value || '',
        wait_time: step.wait_time || 1000,
        assert_type: step.assert_type || 'textContains',
        assert_value: step.assert_value || '',
        expanded: true
      }
    })
    // 历史版本可能已经保存了与代码不一致的步骤；打开时将代码标记为待反解析，保存前会以代码为准同步。
    try {
      const generated = await generateScriptCode(script.id, {
        steps: editorStepPayload(), start_url: editingScript.value.start_url || ''
      })
      if (comparableCode(generated.data.content) !== comparableCode(editingScript.value.content)) {
        const existingStorageStep = editingSteps.value.length === 1 && editingSteps.value[0].action_type === 'storageState'
          ? editingSteps.value[0]
          : null
        const existingStorageName = existingStorageStep?.action_params?.operation === 'save'
          ? String(existingStorageStep.action_params.name || '').trim()
          : ''
        // 已保存步骤与脚本内容来自不同版本时，以当前代码重新反解析，立即恢复两侧一致。
        const parsed = await parseCode()
        if (!parsed) {
          syncState.value = 'code'
        } else if (existingStorageName && existingStorageName !== 'storage_state') {
          // 录制导出的默认文件名不应覆盖用户在手动步骤中填写的登录态名称。
          const parsedStorageStep = editingSteps.value.find(step => step.action_type === 'storageState')
          if (parsedStorageStep?.action_params?.operation === 'save' && parsedStorageStep.action_params.name === 'storage_state') {
            parsedStorageStep.action_params.name = existingStorageName
            await generateFromSteps()
          }
        }
      }
    } catch {
      // 一致性检查失败不阻断编辑器打开，仍保留原有同步状态。
    }
  } catch (error) {
    editorError.value = error.response?.data?.detail || '加载步骤或元素库失败'
  }
}

// 创建代码编辑器实例；代码变更只标记为待反解析，不自动覆盖左侧步骤。
const createMonaco = () => {
  if (!monacoHost.value || codeEditor) return
  codeEditor = monaco.editor.create(monacoHost.value, {
    value: editingScript.value?.content || '', language: 'python', theme: 'vs-dark', automaticLayout: true,
    minimap: { enabled: false }, fontSize: 13, lineHeight: 20, tabSize: 4, wordWrap: 'on', scrollBeyondLastLine: false
  })
  codeEditor.onDidChangeModelContent(() => {
    if (!suppressCodeChange) syncState.value = 'code'
  })
}

const disposeMonaco = () => {
  if (generateTimer) clearTimeout(generateTimer)
  codeEditor?.dispose()
  codeEditor = null
}

const applyCode = content => {
  if (codeEditor && codeEditor.getValue() !== content) {
    suppressCodeChange = true
    codeEditor.setValue(content)
    suppressCodeChange = false
  }
  if (editingScript.value) editingScript.value.content = content
}

// 步骤发生变化后延迟生成代码，减少连续编辑造成的重复请求。
const onStepsChanged = () => {
  if (syncState.value === 'code') return
  syncState.value = 'steps'
  if (generateTimer) clearTimeout(generateTimer)
  generateTimer = setTimeout(generateFromSteps, 400)
}

// 忽略换行和缩进差异，仅用于判断代码与结构化步骤是否来自同一份内容。
const comparableCode = value => String(value || '').replace(/\s+/g, '').trim()

// 将编辑器字段重新组装成后端 action_params 结构并保留定位选项。
const editorStepPayload = () => editingSteps.value.map(step => ({
  ...step,
  action_params: {
    ...(step.action_params || {}),
    locator: step.locator_value ? {
      strategy: step.locator_strategy,
      value: step.locator_value,
      options: {
        ...(step.locator_options || step.action_params?.locator?.options || {})
      }
    } : step.action_params?.locator
  }
}))

// 把左侧步骤提交给后端统一生成 Playwright 代码。
const generateFromSteps = async () => {
  if (!editingScript.value) return
  syncing.value = true
  try {
    const response = await generateScriptCode(editingScript.value.id, {
      steps: editorStepPayload(), start_url: editingScript.value.start_url || ''
    })
    applyCode(response.data.content)
    syncState.value = 'synced'
    return true
  } catch (error) {
    editorError.value = error.response?.data?.error || '代码生成失败'
    return false
  } finally {
    syncing.value = false
  }
}

// 将右侧手工修改的代码反解析，并映射回结构化步骤字段。
const parseCode = async () => {
  if (!editingScript.value) return
  if (generateTimer) {
    clearTimeout(generateTimer)
    generateTimer = null
  }
  syncing.value = true
  editorError.value = ''
  try {
    const response = await parseScriptSteps(editingScript.value.id, codeEditor?.getValue() || editingScript.value.content)
    editingSteps.value = response.data.steps.map((step, index) => ({
      ...step, id: `parsed-${index}-${Date.now()}`, action_type: normalizeAction(step.action_type),
      element_id: step.element_id || step.target_element?.id || '',
      element_page: step.target_element?.page || '', target_element: step.target_element || null,
      locator_strategy: step.action_params?.locator?.strategy || step.target_element?.locator_strategy?.name?.toLowerCase() || 'role',
      locator_value: step.action_params?.locator?.value || '',
      locator_options: { ...(step.action_params?.locator?.options || step.target_element?.locator_options || {}) },
      locator_options_json: JSON.stringify(step.action_params?.locator?.options || step.target_element?.locator_options || {}),
      locator_name: step.action_params?.locator?.options?.name || step.target_element?.locator_options?.name || '', expanded: true
    }))
    if (response.data.start_url) editingScript.value.start_url = response.data.start_url
    syncState.value = 'synced'
    return true
  } catch (error) {
    editorError.value = error.response?.data?.error || '代码反解析失败'
    return false
  } finally {
    syncing.value = false
  }
}

// 转换前读取已保存步骤，预览内容与最终入库数据保持一致。
const openConvertDialog = async script => {
  convertScript.value = script
  convertForm.value = { name: (script.name || '').replace(/\.py$/i, ''), startUrl: script.start_url || '', description: script.description || '' }
  try {
    const response = await getScriptSteps({ script: script.id, page_size: 1000 })
    convertSteps.value = response.data.results || response.data
    if (!convertSteps.value.length) {
      ElMessage.warning('请先在编辑器中完成步骤同步并保存')
      return
    }
    showConvertDialog.value = true
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '加载已同步步骤失败')
  }
}

// 调用后端事务接口，把同步步骤复制为 UI 测试用例。
const convertToTestCase = async () => {
  if (!convertForm.value.name.trim()) return ElMessage.warning('请输入用例名称')
  converting.value = true
  try {
    await convertScriptToTestCase(convertScript.value.id, {
      name: convertForm.value.name.trim(), description: convertForm.value.description,
      start_url: convertForm.value.startUrl
    })
    ElMessage.success('测试用例创建成功')
    showConvertDialog.value = false
  } catch (error) {
    ElMessage.error(error.response?.data?.error || '转换测试用例失败')
  } finally {
    converting.value = false
  }
}

// 保存编辑的脚本
const saveEditedScript = async () => {
  const name = editingScript.value?.name.trim().replace(/\.py$/i, '')
  if (!editingScript.value || !name) {
    ElMessage.warning(t('uiAutomation.script.messages.enterScriptName'))
    return
  }

  try {
    saving.value = true

    // 保存前确保左右两侧使用同一份内容，避免旧步骤覆盖用户刚修改的代码。
    if (syncState.value === 'code') {
      const parsed = await parseCode()
      if (!parsed) return
    } else if (syncState.value === 'steps') {
      const generated = await generateFromSteps()
      if (!generated) return
    }

    const content = codeEditor?.getValue() || editingScript.value.content
    await saveScriptEditor(editingScript.value.id, {
      name,
      content,
      start_url: editingScript.value.start_url || '',
      steps: editorStepPayload()
    })

    ElMessage.success(t('uiAutomation.script.messages.saveSuccess'))
    showEditDialog.value = false

    // 重新加载脚本列表
    await loadScripts()
  } catch (error) {
    ElMessage.error(t('uiAutomation.script.messages.saveFailed'))
    console.error('脚本保存失败:', error)
  } finally {
    saving.value = false
  }
}

// 删除脚本
const deleteScript = async (script) => {
  try {
    await ElMessageBox.confirm(
      t('uiAutomation.script.messages.deleteConfirm', { name: script.name }),
      t('uiAutomation.script.messages.confirmDelete'),
      {
        confirmButtonText: t('uiAutomation.common.confirm'),
        cancelButtonText: t('uiAutomation.common.cancel'),
        type: 'warning'
      }
    )

    await deleteTestScript(script.id)
    ElMessage.success(t('uiAutomation.script.messages.deleteSuccess'))

    // 重新加载脚本列表
    await loadScripts()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(t('uiAutomation.script.messages.deleteFailed'))
      console.error('删除失败:', error)
    }
  }
}

// 辅助方法
const getScriptTypeText = (type) => {
  const typeMap = {
    'CODE': t('uiAutomation.script.scriptTypes.CODE'),
    'VISUAL': t('uiAutomation.script.scriptTypes.VISUAL'),
    'KEYWORD': t('uiAutomation.script.scriptTypes.KEYWORD')
  }
  return typeMap[type] || type
}

const getLanguageText = (language) => {
  const languageMap = {
    'python': 'Python',
    'javascript': 'JavaScript'
  }
  return languageMap[language] || language || t('uiAutomation.status.unknown')
}

const getFrameworkText = (framework) => {
  const frameworkMap = {
    'playwright': 'Playwright',
    'selenium': 'Selenium'
  }
  return frameworkMap[framework] || framework || t('uiAutomation.status.unknown')
}

const formatTime = (timestamp) => {
  if (!timestamp) return ''
  return new Date(timestamp).toLocaleString()
}

// 组件挂载
onMounted(async () => {
  await loadProjects()

  if (projects.value.length > 0) {
    selectedProject.value = getPreferredProjectId(projects.value, projects.value[0].id)
    await loadScripts()
  }
})
</script>

<style scoped>
.script-list {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px;
  border-bottom: 1px solid #e6e6e6;
  background: white;
}

.page-title {
  margin: 0;
  font-size: 24px;
}

.header-actions {
  display: flex;
  align-items: center;
}

.script-file-input {
  display: none;
}

.main-content {
  flex: 1;
  padding: 20px;
  overflow: auto;
  background: #f5f5f5;
}

.pagination {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

.row-actions {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 2px;
  white-space: nowrap;
}

.row-actions :deep(.el-button) {
  margin: 0;
  padding: 0 4px;
}

.script-detail {
  padding: 10px;
}

.script-content {
  margin-top: 20px;
}

.script-content h4 {
  margin: 0 0 10px 0;
  color: #333;
}

.code-view {
  background-color: #1e1e1e;
  color: #d4d4d4;
  padding: 15px;
  border-radius: 4px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
  max-height: 400px;
  overflow: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.script-editor {
  display: flex;
  flex-direction: column;
  height: 76vh;
}

.edit-script-name { padding: 14px 15px 0; }

.editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 15px;
  background-color: #fafafa;
  border-bottom: 1px solid #e6e6e6;
}

.script-name {
  font-weight: bold;
  font-size: 16px;
}

.editor-info {
  display: flex;
  align-items: center;
}

.editor-container {
  flex: 1;
  position: relative;
}

.sync-editor-topbar { display: flex; align-items: center; gap: 12px; padding: 0 4px 12px; }
.script-title-input { max-width: 300px; }
.topbar-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.sync-editor-body { display: grid; grid-template-columns: minmax(420px, 48%) minmax(480px, 52%); gap: 12px; flex: 1; min-height: 0; }
.steps-pane, .code-pane { min-height: 0; border: 1px solid #dcdfe6; border-radius: 4px; background: #fff; overflow: hidden; }
.steps-pane { background: #f7f8fa; overflow: auto; }
.code-pane { display: flex; flex-direction: column; background: #1e1e1e; }
.pane-heading { color: #d4d4d4; padding: 9px 12px; border-bottom: 1px solid #3c3c3c; font-size: 13px; display: flex; justify-content: space-between; }
.monaco-host { flex: 1; min-height: 0; }
.convert-form { padding: 2px 8px 10px; }

@media (max-width: 1100px) {
  .sync-editor-body { grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; }
}

.code-editor {
  width: 100%;
  height: 100%;
  border: none;
  outline: none;
  resize: none;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 14px;
  line-height: 1.5;
  padding: 15px;
  background-color: #1e1e1e;
  color: #d4d4d4;
  tab-size: 2;
}
</style>
