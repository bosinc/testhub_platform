<template>
  <div class="script-editor-enhanced">
    <div class="page-header">
      <h1 class="page-title">{{ $t('uiAutomation.scriptEditor.title') }}</h1>
      <div class="header-actions">
        <el-select v-model="projectId" :placeholder="$t('uiAutomation.common.selectProject')" style="width: 200px; margin-right: 15px" @change="onProjectChange">
          <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
        </el-select>
      </div>
    </div>

    <div class="main-content">
      <ResizableSplitPane v-model="elementLibraryWidth" min-size="240" max-size="520" second-min-size="560" :storage-key="'ui-automation.script-editor.element-library-width'">
        <template #first>
          <!-- 左侧:元素库(页面树形式) -->
          <div class="left-panel">
        <div class="panel-header">
          <h3>{{ $t('uiAutomation.scriptEditor.elementLibrary') }}</h3>
          <el-input
            v-model="elementFilter"
            :placeholder="$t('uiAutomation.scriptEditor.searchElement')"
            clearable
            size="small"
            style="margin-top: 10px"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>

        <div class="panel-content">
          <el-tree
            class="element-library-tree"
            :data="elementTree"
            :filter-node-method="filterElementNode"
            :props="{ children: 'children', label: 'name' }"
            node-key="treeKey"
            default-expand-all
            @node-click="handleElementClick"
          >
            <template #default="{ data }">
              <div class="tree-node">
                <el-icon class="node-icon">
                  <component :is="getTreeNodeIcon(data.type)" />
                </el-icon>
                <span class="node-label" :title="data.name">{{ data.name }}</span>
                <div class="node-actions" v-if="data.type === 'element'">
                  <el-button size="small" text @click.stop="insertElementCode(data)" :title="$t('uiAutomation.scriptEditor.clickToInsert')">
                    <el-icon><Plus /></el-icon>
                  </el-button>
                  <el-button size="small" text @click.stop="showElementDetail(data)">
                    <el-icon><View /></el-icon>
                  </el-button>
                </div>
              </div>
            </template>
          </el-tree>
        </div>
          </div>
        </template>

        <template #second>
          <ResizableSplitPane v-model="detailsPanelWidth" resize-target="second" min-size="280" max-size="520" first-min-size="480" second-min-size="480" :storage-key="'ui-automation.script-editor.details-width'">
            <template #first>
              <!-- 中间:代码编辑器 -->
              <div class="center-panel">
        <div class="editor-toolbar">
          <div class="toolbar-left">
            <el-button size="small" type="primary" plain @click="startRecording">
              <el-icon><VideoPlay /></el-icon>
              {{ $t('uiAutomation.recording.start') }}
            </el-button>
            <el-select v-model="scriptLanguage" size="small" style="width: 120px">
              <el-option label="JavaScript" value="javascript" />
              <el-option label="Python" value="python" />
            </el-select>
            <el-select v-model="scriptFramework" size="small" style="width: 120px; margin-left: 10px">
              <el-option label="Playwright" value="playwright" />
              <el-option label="Selenium" value="selenium" />
            </el-select>
          </div>
          <div class="toolbar-right">
            <el-button size="small" @click="formatCode">
              <el-icon><Operation /></el-icon>
              {{ $t('uiAutomation.scriptEditor.format') }}
            </el-button>
            <el-button size="small" @click="clearCode">
              <el-icon><Delete /></el-icon>
              {{ $t('uiAutomation.scriptEditor.clear') }}
            </el-button>
            <el-button size="small" type="primary" plain @click="openReplay">
              <el-icon><VideoPlay /></el-icon>
              {{ $t('uiAutomation.replay.button') }}
            </el-button>
            <el-button size="small" type="primary" @click="saveScript" :loading="saving">
              <el-icon><Check /></el-icon>
              {{ $t('uiAutomation.scriptEditor.saveScript') }}
            </el-button>
          </div>
        </div>

        <div class="code-editor-container">
          <div ref="codeEditor" class="code-editor" />
        </div>

        <div class="editor-status">
          <span>{{ $t('uiAutomation.scriptEditor.line') }}: {{ cursorPosition.line }}, {{ $t('uiAutomation.scriptEditor.column') }}: {{ cursorPosition.column }}</span>
          <span>{{ $t('uiAutomation.scriptEditor.characters') }}: {{ scriptContent.length }}</span>
          <span>{{ $t('uiAutomation.scriptEditor.language') }}: {{ scriptLanguage }}</span>
        </div>
              </div>
            </template>

            <template #second>
              <!-- 右侧:执行日志和元素详情 -->
              <div class="right-panel">
        <el-tabs v-model="rightActiveTab" type="border-card">
          <!-- 执行日志 -->
          <el-tab-pane :label="$t('uiAutomation.scriptEditor.executionLogs')" name="logs">
            <div class="panel-content">
              <div class="log-controls">
                <el-button size="small" @click="clearLogs">
                  <el-icon><Delete /></el-icon>
                  {{ $t('uiAutomation.scriptEditor.clearLogs') }}
                </el-button>
              </div>
              <div class="log-output">
                <div
                  v-for="(log, index) in executionLogs"
                  :key="index"
                  class="log-entry"
                  :class="log.level"
                >
                  <span class="log-time">{{ formatTime(log.timestamp) }}</span>
                  <span class="log-level">{{ log.level.toUpperCase() }}</span>
                  <span class="log-message">{{ log.message }}</span>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- 元素详情 -->
          <el-tab-pane :label="$t('uiAutomation.scriptEditor.elementDetail')" name="elementDetail" v-if="selectedElementDetail">
            <div class="panel-content">
              <div class="element-detail">
                <h4>{{ selectedElementDetail.name }}</h4>
                <el-descriptions :column="1" border size="small">
                  <el-descriptions-item :label="$t('uiAutomation.scriptEditor.type')">
                    {{ getElementTypeText(selectedElementDetail.element_type) }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="$t('uiAutomation.scriptEditor.page')">
                    {{ selectedElementDetail.page || $t('uiAutomation.element.notSpecified') }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="$t('uiAutomation.scriptEditor.locatorStrategy')">
                    {{ selectedElementDetail.locator_strategy?.name || selectedElementDetail.locator_strategy }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="$t('uiAutomation.scriptEditor.locatorExpression')">
                    <code>{{ selectedElementDetail.locator_value }}</code>
                  </el-descriptions-item>
                  <el-descriptions-item :label="$t('uiAutomation.element.locatorOptions')">
                    <code>{{ JSON.stringify(selectedElementDetail.locator_options || {}) }}</code>
                  </el-descriptions-item>
                  <el-descriptions-item :label="$t('uiAutomation.common.description')">
                    {{ selectedElementDetail.description || '-' }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="$t('uiAutomation.scriptEditor.usageCount')">
                    {{ selectedElementDetail.usage_count || 0 }}
                  </el-descriptions-item>
                </el-descriptions>

                <div class="element-actions" style="margin-top: 15px">
                  <el-button size="small" type="primary" @click="insertElementCode(selectedElementDetail)">
                    {{ $t('uiAutomation.scriptEditor.insertCode') }}
                  </el-button>
                  <el-button size="small" @click="validateElement(selectedElementDetail)">
                    {{ $t('uiAutomation.scriptEditor.validateElement') }}
                  </el-button>
                </div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
              </div>
            </template>
          </ResizableSplitPane>
        </template>
      </ResizableSplitPane>
    </div>
    <RecordingWizard v-model:visible="recordingVisible" :project-id="projectId" @imported="handleImportedScript" />
    <ScriptReplayDialog v-model:visible="replayVisible" :script="currentScript" />
  </div>
</template>

<script setup>
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution'
import 'monaco-editor/esm/vs/editor/browser/widget/codeEditor/editor.css'
import {
  Search, Plus, View, Document, Check, Delete, Operation, Folder, VideoPlay
} from '@element-plus/icons-vue'
import RecordingWizard from './RecordingWizard.vue'
import ScriptReplayDialog from './ScriptReplayDialog.vue'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'
import { createElementLibraryLoader } from './elementLibrary.mjs'

import {
  getUiProjects,
  createTestScript,
  getTestScriptDetail,
  updateTestScript,
  getElementTree,
  getElementGroupTree,
  validateElementLocator
} from '@/api/ui_automation'

// i18n
const { t } = useI18n()
const route = useRoute()

// 响应式数据
const projects = ref([])
const projectId = ref('')
useRememberedProject(projectId, projects)
const elementLibraryWidth = ref(300)
const detailsPanelWidth = ref(350)
const scriptContent = ref('')
const scriptLanguage = ref('python')
const scriptFramework = ref('playwright')
const scriptId = ref(null)
const scriptName = ref('')
const recordingVisible = ref(false)
const replayVisible = ref(false)

const elementTree = ref([])
const loadLibrary = createElementLibraryLoader(getElementGroupTree, getElementTree)
let elementTreeRequestId = 0
const elementFilter = ref('')
const selectedElementDetail = ref(null)
const executionLogs = ref([])

const cursorPosition = reactive({ line: 1, column: 1 })
const saving = ref(false)

// 标签页控制
const rightActiveTab = ref('logs')

// Monaco编辑器实例
const codeEditor = ref(null)
let editorInstance = null
let syncingEditor = false

const currentScript = computed(() => projectId.value && scriptContent.value.trim() ? {
  id: scriptId.value || undefined,
  name: scriptName.value,
  content: scriptContent.value,
  language: scriptLanguage.value,
  framework: scriptFramework.value,
  project: projects.value.find(project => project.id === projectId.value),
  start_url: ''
} : null)

const updateEditorLanguage = () => {
  if (!editorInstance?.getModel()) return
  monaco.editor.setModelLanguage(editorInstance.getModel(), scriptLanguage.value === 'javascript' ? 'javascript' : 'python')
}

const syncEditorValue = value => {
  if (!editorInstance || editorInstance.getValue() === value) return
  syncingEditor = true
  editorInstance.setValue(value || '')
  syncingEditor = false
  updateCursorPosition()
}

const createCodeEditor = () => {
  if (!codeEditor.value) return
  editorInstance = monaco.editor.create(codeEditor.value, {
    value: scriptContent.value,
    language: scriptLanguage.value === 'javascript' ? 'javascript' : 'python',
    theme: 'vs-dark',
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 14,
    lineHeight: 22,
    padding: { top: 14, bottom: 14 },
    tabSize: 4,
    wordWrap: 'on',
    scrollBeyondLastLine: false,
    renderLineHighlight: 'line',
  })
  editorInstance.onDidChangeModelContent(() => {
    if (syncingEditor) return
    scriptContent.value = editorInstance.getValue()
    updateCursorPosition()
  })
  editorInstance.onDidChangeCursorPosition(updateCursorPosition)
  updateCursorPosition()
}

// 方法定义
const loadProjects = async () => {
  try {
    const response = await getUiProjects({ page_size: 100 })
    projects.value = response.data.results || response.data
  } catch (error) {
    ElMessage.error(t('uiAutomation.scriptEditor.messages.loadProjectsFailed'))
    console.error('Failed to load projects:', error)
  }
}

const loadElementTree = async () => {
  const requestedProject = projectId.value
  const requestId = ++elementTreeRequestId
  if (!requestedProject) {
    elementTree.value = []
    return
  }

  try {
    const tree = await loadLibrary(requestedProject)
    // 快速切换项目或离开页面时，旧请求不得覆盖当前树和日志。
    if (requestId !== elementTreeRequestId || String(projectId.value) !== String(requestedProject)) return
    elementTree.value = tree

    addLog('info', t('uiAutomation.scriptEditor.messages.elementsLoaded', { count: countElements(elementTree.value) }))
  } catch (error) {
    if (requestId !== elementTreeRequestId || String(projectId.value) !== String(requestedProject)) return
    console.error('Failed to load element tree:', error)
    addLog('error', t('uiAutomation.scriptEditor.messages.loadElementTreeFailed'))
  }
}

// 统计元素数量
const countElements = (tree) => {
  let count = 0
  const traverse = (nodes) => {
    nodes.forEach(node => {
      if (node.type === 'element') count++
      if (node.children) traverse(node.children)
    })
  }
  traverse(tree)
  return count
}

const handleContentChange = () => {
  if (editorInstance && editorInstance.getValue() !== scriptContent.value) {
    syncEditorValue(scriptContent.value)
  }
  updateCursorPosition()
}

const updateCursorPosition = () => {
  if (editorInstance) {
    const position = editorInstance.getPosition()
    if (position) {
      cursorPosition.line = position.lineNumber
      cursorPosition.column = position.column
    }
  }
}

const onProjectChange = async () => {
  elementTree.value = []
  selectedElementDetail.value = null
  executionLogs.value = []
  scriptContent.value = ''
  scriptId.value = null
  scriptName.value = ''
  syncEditorValue('')

  await loadElementTree()

}

const startRecording = () => {
  if (!projectId.value) {
    ElMessage.warning(t('uiAutomation.scriptEditor.messages.selectProject'))
    return
  }
  recordingVisible.value = true
}

const handleImportedScript = async script => {
  scriptId.value = script.id
  scriptName.value = script.name || ''
  projectId.value = script.project?.id || projectId.value
  scriptContent.value = script.content || ''
  scriptLanguage.value = 'python'
  scriptFramework.value = 'playwright'
  syncEditorValue(scriptContent.value)
  updateEditorLanguage()
  await loadElementTree()
  addLog('success', `${t('uiAutomation.recording.importSuccess')}: ${script.name}`)
}

const filterElementNode = (value, data) => {
  if (!value) return true
  return data.name.indexOf(value) !== -1
}

const handleElementClick = (data) => {
  if (data.type === 'element') {
    selectedElementDetail.value = data
    rightActiveTab.value = 'elementDetail'
  }
}

const showElementDetail = (element) => {
  selectedElementDetail.value = element
  rightActiveTab.value = 'elementDetail'
}

const insertElementCode = (element) => {
  if (element.type !== 'element') return

  const code = generateElementCode(element)
  insertCodeAtCursor(code + '\n')  // 自动换行

  addLog('info', `${t('uiAutomation.scriptEditor.messages.insertCode')}: ${element.name}`)
}

const insertCodeAtCursor = (code) => {
  if (!editorInstance) return
  const selection = editorInstance.getSelection() || editorInstance.getModel().getFullModelRange()
  editorInstance.executeEdits('element-library', [{ range: selection, text: code, forceMoveMarkers: true }])
  editorInstance.focus()
  updateCursorPosition()
}

const generateElementCode = (element) => {
  const locatorValue = element.locator_value || ''
  const locatorStrategy = element.locator_strategy?.name || element.locator_strategy || 'css'

  if (scriptLanguage.value === 'javascript') {
    switch (scriptFramework.value) {
      case 'playwright':
        return `await page.locator('${locatorValue}').click();`
      case 'selenium':
        return `await driver.findElement(By.${locatorStrategy.toUpperCase()}('${locatorValue}')).click();`
      default:
        return `await page.locator('${locatorValue}').click();`
    }
  } else {
    switch (scriptFramework.value) {
      case 'playwright':
        return `page.locator('${locatorValue}').click()`
      case 'selenium':
        return `driver.find_element(By.${locatorStrategy.toUpperCase()}, '${locatorValue}').click()`
      default:
        return `page.locator('${locatorValue}').click()`
    }
  }
}

// 生成脚本文件名
const generateScriptName = () => {
  const currentProject = projects.value.find(p => p.id === projectId.value)
  const projectName = currentProject?.name || 'Script'
  const language = scriptLanguage.value === 'javascript' ? 'JS' : 'Python'
  const framework = scriptFramework.value === 'playwright' ? 'Playwright' : 'Selenium'
  const date = new Date()
  const dateStr = `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, '0')}${String(date.getDate()).padStart(2, '0')}`

  // 获取自增数字 - 简单实现，实际应从后端获取
  const timestamp = Date.now() % 1000

  const extension = scriptLanguage.value === 'javascript' ? 'js' : 'py'

  return `${projectName}_${language}_${framework}_${dateStr}_${timestamp}.${extension}`
}

const saveScript = async () => {
  if (!projectId.value) {
    ElMessage.warning(t('uiAutomation.scriptEditor.messages.selectProject'))
    return false
  }

  if (!scriptContent.value.trim()) {
    ElMessage.warning(t('uiAutomation.scriptEditor.messages.emptyScript'))
    return false
  }

  try {
    saving.value = true

    const generatedName = scriptName.value || generateScriptName()
    const payload = {
      name: generatedName,
      project: projectId.value,
      script_type: 'CODE',
      content: scriptContent.value,
      language: scriptLanguage.value,
      framework: scriptFramework.value
    }
    if (scriptId.value) {
      delete payload.project
      delete payload.name
      await updateTestScript(scriptId.value, payload)
    } else {
      const response = await createTestScript(payload)
      scriptId.value = response.data?.id || null
      scriptName.value = generatedName
    }

    ElMessage.success(`${t('uiAutomation.scriptEditor.messages.saveSuccess')}: ${generatedName}`)
    addLog('success', `${t('uiAutomation.scriptEditor.messages.saveSuccess')}: ${generatedName}`)
    return true
  } catch (error) {
    console.error('Failed to save script:', error)
    ElMessage.error(t('uiAutomation.scriptEditor.messages.saveFailed'))
    addLog('error', t('uiAutomation.scriptEditor.messages.saveFailed'))
    return false
  } finally {
    saving.value = false
  }
}

const validateElement = async (element) => {
  try {
    const response = await validateElementLocator(element.id)
    const result = response.data

    if (result.is_valid) {
      ElMessage.success(t('uiAutomation.scriptEditor.messages.validatePassed'))
      addLog('info', `${t('uiAutomation.scriptEditor.messages.validatePassed')}: ${element.name}`)
    } else {
      ElMessage.error(`${t('uiAutomation.scriptEditor.messages.validateFailed')}: ${result.validation_message}`)
      addLog('error', `${t('uiAutomation.scriptEditor.messages.validateFailed')}: ${element.name} - ${result.validation_message}`)
    }
  } catch (error) {
    console.error('Failed to validate element:', error)
    ElMessage.error(t('uiAutomation.scriptEditor.messages.validateFailed'))
    addLog('error', `${t('uiAutomation.scriptEditor.messages.validateFailed')}: ${element.name}`)
  }
}

const formatCode = () => {
  // 简单的代码格式化
  try {
    if (scriptLanguage.value === 'javascript') {
      let formatted = scriptContent.value
        .replace(/;/g, ';\n')
        .replace(/\{/g, ' {\n')
        .replace(/\}/g, '\n}')
        .replace(/\n\s*\n/g, '\n')

      scriptContent.value = formatted
      syncEditorValue(formatted)
      addLog('info', t('uiAutomation.scriptEditor.messages.codeFormatted'))
    } else {
      addLog('info', t('uiAutomation.scriptEditor.messages.formatInProgress'))
    }
  } catch (error) {
    addLog('error', t('uiAutomation.scriptEditor.messages.formatFailed'))
  }
}

const clearCode = () => {
  scriptContent.value = ''
  syncEditorValue('')
  addLog('info', t('uiAutomation.scriptEditor.messages.codeCleared'))
}

const openReplay = async () => {
  if (scriptLanguage.value !== 'python' || scriptFramework.value !== 'playwright') {
    ElMessage.warning(t('uiAutomation.replay.pythonPlaywrightOnly'))
    return
  }
  if (!projectId.value) {
    ElMessage.warning(t('uiAutomation.common.selectProject'))
    return
  }
  if (!scriptContent.value.trim()) {
    ElMessage.warning(t('uiAutomation.scriptEditor.messages.emptyScript'))
    return
  }
  replayVisible.value = true
}

const clearLogs = () => {
  executionLogs.value = []
}

const addLog = (level, message) => {
  executionLogs.value.push({
    level,
    message,
    timestamp: new Date()
  })

  // 保持最多100条日志
  if (executionLogs.value.length > 100) {
    executionLogs.value = executionLogs.value.slice(-100)
  }
}

// 辅助方法
const getTreeNodeIcon = (type) => {
  return type === 'page' ? Folder : Document
}

const getElementTypeText = (type) => {
  const typeMap = {
    'BUTTON': t('uiAutomation.element.elementTypes.button'),
    'INPUT': t('uiAutomation.element.elementTypes.input'),
    'LINK': t('uiAutomation.element.elementTypes.link'),
    'DROPDOWN': t('uiAutomation.element.elementTypes.dropdown'),
    'CHECKBOX': t('uiAutomation.element.elementTypes.checkbox'),
    'RADIO': t('uiAutomation.element.elementTypes.radio'),
    'TEXT': t('uiAutomation.element.elementTypes.text'),
    'IMAGE': t('uiAutomation.element.elementTypes.image'),
    'CONTAINER': t('uiAutomation.element.elementTypes.container'),
    'TABLE': t('uiAutomation.element.elementTypes.table'),
    'FORM': t('uiAutomation.element.elementTypes.form'),
    'MODAL': t('uiAutomation.element.elementTypes.modal')
  }
  return typeMap[type] || type
}

const formatTime = (timestamp) => {
  return timestamp.toLocaleTimeString()
}

// 监听器
watch(elementFilter, (val) => {
  // 树形筛选会自动处理
})

watch(scriptLanguage, (newLang) => {
  addLog('info', t('uiAutomation.scriptEditor.messages.switchLanguage', { lang: newLang }))
})

// 组件挂载
onMounted(async () => {
  createCodeEditor()
  await loadProjects()

  if (route.query.scriptId) {
    try {
      const response = await getTestScriptDetail(route.query.scriptId)
      await handleImportedScript(response.data)
    } catch (error) {
      ElMessage.error(t('uiAutomation.scriptEditor.messages.loadScriptFailed'))
    }
  } else if (projects.value.length > 0) {
    projectId.value = getPreferredProjectId(projects.value, projects.value[0].id)
    await onProjectChange()
  }

})

watch(scriptLanguage, updateEditorLanguage)
watch(scriptContent, handleContentChange)

onBeforeUnmount(() => {
  elementTreeRequestId++
  editorInstance?.dispose()
  editorInstance = null
})
</script>

<style scoped>
.script-editor-enhanced {
  height: 100%;
  min-height: 0;
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

.main-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.left-panel {
  width: 100%;
  border-right: 1px solid #e6e6e6;
  display: flex;
  flex-direction: column;
  background: white;
}

.panel-header {
  padding: 15px;
  border-bottom: 1px solid #e6e6e6;
}

.panel-header h3 {
  margin: 0 0 10px 0;
}

.center-panel {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e6e6e6;
}

.right-panel {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  min-height: 0;
}

.right-panel :deep(.el-tabs) {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}

.right-panel :deep(.el-tabs__content) {
  flex: 1 1 auto;
  min-height: 0;
}

.right-panel :deep(.el-tab-pane),
.right-panel .panel-content {
  height: 100%;
}

.panel-content {
  padding: 15px;
  flex: 1;
  overflow-y: auto;
}

.editor-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 15px;
  border-bottom: 1px solid #e6e6e6;
  background-color: #fafafa;
}

.toolbar-left,
.toolbar-right {
  min-width: 0;
  flex-wrap: wrap;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.code-editor-container {
  flex: 1;
  position: relative;
}

.code-editor {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.editor-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 15px;
  border-top: 1px solid #e6e6e6;
  background-color: #fafafa;
  font-size: 12px;
  color: #666;
}

.tree-node {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  justify-content: space-between;
  font-size: 14px;
  padding-right: 8px;
}

.node-icon {
  flex-shrink: 0;
  margin-right: 5px;
  font-size: 16px;
}

.node-label {
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  margin-left: auto;
  gap: 2px;
}

/* 覆盖 Element Plus 相邻按钮的默认间距，给名称留出更多空间。 */
.node-actions :deep(.el-button) {
  width: 24px;
  height: 24px;
  margin-left: 0;
  padding: 0;
}

.element-library-tree :deep(.el-tree-node__content) {
  min-width: 0;
}

.log-controls {
  margin-bottom: 10px;
}

.log-output {
  height: 400px;
  overflow-y: auto;
  border: 1px solid #e6e6e6;
  border-radius: 4px;
  padding: 10px;
  background-color: #1e1e1e;
  color: #fff;
  font-family: monospace;
  font-size: 12px;
}

.log-entry {
  margin-bottom: 5px;
  display: flex;
  gap: 10px;
}

.log-time {
  color: #888;
}

.log-level {
  font-weight: bold;
  min-width: 60px;
}

.log-entry.info .log-level {
  color: #67c23a;
}

.log-entry.error .log-level {
  color: #f56c6c;
}

.log-entry.success .log-level {
  color: #67c23a;
}

.element-detail {
  padding: 10px;
}

.element-detail h4 {
  margin: 0 0 15px 0;
  color: #333;
}

.element-actions {
  display: flex;
  gap: 10px;
}
</style>
