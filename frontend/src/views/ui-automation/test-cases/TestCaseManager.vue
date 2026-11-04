<template>
  <div class="test-case-manager" @click="showFolderContextMenu = false">
    <div class="page-header">
      <h1 class="page-title">{{ t('uiAutomation.testCase.title') }}</h1>
      <div class="header-actions">
        <el-select v-model="projectId" :placeholder="t('uiAutomation.project.selectProject')" class="project-selector" @change="onProjectChange">
          <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
        </el-select>
        <el-button :disabled="!projectId" @click="openStorageStateManager">登录态管理</el-button>
        <el-tooltip :content="t('uiAutomation.variables.selectProject')" :disabled="!!projectId">
          <span>
            <el-button :disabled="!projectId" @click="openVariablesDialog">
              <el-icon><Setting /></el-icon>
              {{ t('uiAutomation.variables.title') }}
              <span v-if="currentVariables.length" class="variable-count">{{ currentVariables.length }}</span>
            </el-button>
          </span>
        </el-tooltip>
        <el-button type="primary" @click="showCreateDialog = true">
          <el-icon><Plus /></el-icon>
          {{ t('uiAutomation.testCase.newTestCase') }}
        </el-button>
      </div>
    </div>

    <div class="main-content">
      <ResizableSplitPane v-model="leftPanelWidth" min-size="240" max-size="520" second-min-size="480" :storage-key="'ui-automation.test-cases.sidebar-width'">
        <template #first>
      <!-- 左侧：测试用例列表 -->
          <div class="left-panel">
        <div class="panel-header">
          <!-- 搜索框随侧栏伸缩，新建目录按钮固定在右侧。 -->
            <el-input
              class="case-search"
              v-model="searchKeyword"
              :placeholder="t('uiAutomation.testCase.searchPlaceholder')"
              clearable
              size="small"
              @click.stop
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <el-button
              text
              size="small"
              class="tree-toggle-button"
              :disabled="!testCaseFolders.length"
              :aria-label="t(allFoldersExpanded ? 'uiAutomation.testCase.foldAll' : 'uiAutomation.testCase.expandAll')"
              @click.stop="toggleAllFolders"
            >
              <el-icon><Fold v-if="allFoldersExpanded" /><Expand v-else /></el-icon>
            </el-button>
            <el-button
              text
              size="small"
              class="folder-create-button"
              :disabled="!projectId"
              :title="t('uiAutomation.testCase.folder.create')"
              @click.stop="createFolder()"
            >
              <el-icon><FolderAdd /></el-icon>
            </el-button>
        </div>

        <div class="test-case-list">
          <el-tree
            ref="testCaseTreeRef"
            :data="testCaseTree"
            :props="{ label: 'name', children: 'children' }"
            :default-expanded-keys="treeDefaultExpandedKeys"
            node-key="id"
            @node-expand="onFolderNodeExpand"
            @node-collapse="onFolderNodeCollapse"
            @node-contextmenu="onFolderNodeContextMenu"
          >
            <template #default="{ data }">
              <div
                class="test-case-tree-node"
                :class="{ active: data.type === 'testCase' && selectedTestCase?.id === data.id }"
                @click.stop="data.type === 'testCase' && selectTestCase(data)"
              >
                <el-icon v-if="data.type === 'folder'"><Folder /></el-icon>
                <el-icon v-else><Document /></el-icon>
                <div class="test-case-tree-content">
                  <div class="test-case-tree-title">
                    <span class="test-case-tree-name">{{ data.name }}</span>
                    <div v-if="data.type === 'testCase'" class="case-actions">
                      <el-button size="small" text :title="t('uiAutomation.testCase.copy.title')" @click.stop="copyTestCase(data)"><el-icon><CopyDocument /></el-icon></el-button>
                      <el-button size="small" text type="danger" :title="t('uiAutomation.testCase.delete.title')" @click.stop="deleteTestCase(data)"><el-icon><Delete /></el-icon></el-button>
                    </div>
                    <span v-if="data.type === 'testCase'" class="case-step-count">{{ data.steps?.length || 0 }}</span>
                  </div>
                </div>
              </div>
            </template>
          </el-tree>
          <el-empty v-if="!testCaseTree.length" :description="t('uiAutomation.common.noData')" />
        </div>
        <ul
          ref="folderContextMenuRef"
          v-show="showFolderContextMenu"
          class="folder-context-menu"
          :style="{ left: folderContextMenuX + 'px', top: folderContextMenuY + 'px' }"
          @click.stop
        >
          <template v-if="contextTreeNode?.type === 'folder'">
            <li @click="createFolder(contextTreeNode)">{{ t('uiAutomation.testCase.folder.createSubfolder') }}</li>
            <li v-if="contextTreeNode.folderId" @click="renameFolder(contextTreeNode)">{{ t('uiAutomation.testCase.folder.rename') }}</li>
            <li v-if="contextTreeNode.folderId" class="danger" @click="deleteFolder(contextTreeNode)">{{ t('uiAutomation.common.delete') }}</li>
          </template>
          <li v-else-if="contextTreeNode?.type === 'testCase'" @click="openMoveTestCaseDialog(contextTreeNode)">{{ t('uiAutomation.testCase.folder.move') }}</li>
        </ul>
          </div>
        </template>

        <template #second>
          <!-- 右侧：测试用例详情和步骤编辑 -->
          <div class="right-panel">
        <div v-if="selectedTestCase" class="test-case-detail">
          <div class="detail-header">
            <div class="detail-title-row">
              <el-input
                v-model="selectedTestCase.name"
                class="detail-title-input"
                :placeholder="t('uiAutomation.testCase.caseNamePlaceholder')"
                :aria-label="t('uiAutomation.testCase.caseName')"
              />
              <el-select
                v-model="selectedTestCase.priority"
                class="detail-priority-select"
                :placeholder="t('uiAutomation.testCase.priority')"
                :aria-label="t('uiAutomation.testCase.priority')"
              >
                <el-option :label="t('uiAutomation.testCase.priorityHigh')" value="high" />
                <el-option :label="t('uiAutomation.testCase.priorityMedium')" value="medium" />
                <el-option :label="t('uiAutomation.testCase.priorityLow')" value="low" />
              </el-select>
            </div>
            <el-input
              v-model="selectedTestCase.description"
              class="detail-description-input"
              type="textarea"
              :rows="2"
              resize="vertical"
              :placeholder="t('uiAutomation.testCase.caseDescPlaceholder')"
              :aria-label="t('uiAutomation.testCase.caseDescription')"
            />
            <div class="detail-actions">
              <el-button size="small" @click="addStep">
                <el-icon><Plus /></el-icon>
                {{ t('uiAutomation.testCase.addStep') }}
              </el-button>
              <el-select v-model="selectedEngine" :placeholder="t('uiAutomation.testCase.selectEngine')" size="small" style="width: 130px; margin-right: 10px">
                <el-option label="Playwright" value="playwright" />
                <el-option label="Selenium" value="selenium" />
              </el-select>
              <el-select v-model="selectedBrowser" :placeholder="t('uiAutomation.testCase.selectBrowser')" size="small" style="width: 120px; margin-right: 10px">
                <el-option label="Chrome" value="chrome" />
                <el-option label="Firefox" value="firefox" />
                <el-option label="Safari" value="safari" />
                <el-option label="Edge" value="edge" />
              </el-select>
              <!-- 运行前选择可选的初始登录态，清空即使用干净上下文。 -->
              <el-select v-model="selectedStorageStateId" clearable placeholder="选择登录态" size="small" style="width: 150px; margin-right: 10px" :disabled="selectedEngine !== 'playwright'">
                <el-option v-for="state in storageStates" :key="state.id" :label="state.name" :value="state.id" />
              </el-select>
              <el-select v-model="headlessMode" :placeholder="t('uiAutomation.testCase.runModeLabel')" size="small" style="width: 110px; margin-right: 10px">
                <el-option :label="t('uiAutomation.testCase.headedMode')" :value="false" />
                <el-option :label="t('uiAutomation.testCase.headlessMode')" :value="true" />
              </el-select>
              <el-button size="small" type="success" @click="runTestCase(selectedTestCase)" :loading="isRunning">
                <el-icon v-if="!isRunning"><CaretRight /></el-icon>
                {{ isRunning ? t('uiAutomation.testCase.running') : t('uiAutomation.testCase.runLabel') }}
              </el-button>
              <el-button size="small" type="primary" @click="saveTestCase">
                <el-icon><Check /></el-icon>
                {{ t('uiAutomation.testCase.saveTestCase') }}
              </el-button>
              <el-button size="small" v-if="executionResult" @click="toggleView">
                <el-icon><component :is="showSteps ? 'View' : 'Edit'" /></el-icon>
                {{ showSteps ? t('uiAutomation.testCase.viewResult') : t('uiAutomation.testCase.editSteps') }}
              </el-button>
              <el-button
                size="small"
                v-if="executionResult && !showSteps"
                type="success"
                @click="runTestCase(selectedTestCase)"
                :loading="isRunning"
              >
                <el-icon v-if="!isRunning"><Refresh /></el-icon>
                {{ t('uiAutomation.testCase.rerun') }}
              </el-button>
            </div>
          </div>

          <!-- 测试步骤编辑 -->
          <div class="steps-container" v-show="showSteps">
            <div class="steps-header">
              <h4>{{ t('uiAutomation.testCase.testSteps') }}</h4>
              <el-button size="small" text @click="expandAllSteps">
                {{ allStepsExpanded ? t('uiAutomation.testCase.foldAll') : t('uiAutomation.testCase.expandAll') }}
              </el-button>
            </div>

            <div class="steps-scroll-container">
              <StructuredStepEditor
                v-model="currentSteps"
                :elements="availableElements"
                :pages="pages"
                :storage-states="storageStates"
                :elements-loading="elementsLoading"
                :elements-ready="elementLibraryLoaded"
                :helper-actions="true"
                :show-toolbar="false"
                @helper="({ step, field }) => openDataFactorySelector(step, field)"
                @variable="({ step, field }) => openVariableHelper(step, field)"
                @open-element-selector="loadElementsForStep"
                @search-elements="searchElementsForStep"
                @change="onStepsReorder"
              />
            </div>
          </div>

          <!-- 执行结果 -->
          <div v-if="executionResult" class="execution-result" v-show="!showSteps">
            <div class="result-header">
              <h4>{{ t('uiAutomation.testCase.executionResult') }}</h4>
              <el-tag :type="executionResult.success ? 'success' : 'danger'">
                {{ executionResult.success ? t('uiAutomation.testCase.executionSuccess') : t('uiAutomation.testCase.executionFailed') }}
              </el-tag>
            </div>
            <div class="result-content">
              <el-tabs v-model="resultActiveTab">
                <el-tab-pane :label="t('uiAutomation.testCase.executionLogs')" name="logs">
                  <div class="logs-container">
                    <div v-if="parsedExecutionLogs.length > 0">
                      <div v-for="(step, index) in parsedExecutionLogs" :key="index" class="log-item">
                        <div class="log-header">
                          <el-tag :type="step.success ? 'success' : 'danger'" size="small">
                            {{ t('uiAutomation.testCase.step') }} {{ step.step_number }}
                          </el-tag>
                          <span class="log-action">{{ getActionText(step.action_type) }}</span>
                          <span class="log-desc">{{ step.description }}</span>
                        </div>
                        <div v-if="step.error" class="log-error">
                          <el-icon><WarningFilled /></el-icon>
                          <pre class="error-message">{{ step.error }}</pre>
                        </div>
                      </div>
                    </div>
                    <el-empty :description="t('uiAutomation.testCase.noLogs')" />
                  </div>
                </el-tab-pane>
                <el-tab-pane :label="t('uiAutomation.testCase.failedScreenshots')" name="screenshots" v-if="executionResult.screenshots && executionResult.screenshots.length > 0">
                  <div class="screenshots-container">
                    <div
                      v-for="(screenshot, index) in executionResult.screenshots"
                      :key="index"
                      class="screenshot-item"
                      @click="previewScreenshot(screenshot)"
                    >
                      <div class="screenshot-wrapper">
                        <img
                          :src="screenshot.url"
                          :alt="`${t('uiAutomation.testCase.screenshot')} ${index + 1}`"
                          :data-index="index"
                          @error="handleImageError"
                          @load="handleImageLoad"
                        />
                        <div class="screenshot-placeholder" v-if="!screenshot.loaded">
                          <el-icon><Picture /></el-icon>
                          <span>{{ t('uiAutomation.testCase.loadingImage') }}</span>
                        </div>
                        <div class="screenshot-error" v-if="screenshot.error">
                          <el-icon><Warning /></el-icon>
                          <span>{{ t('uiAutomation.testCase.imageLoadFailed') }}</span>
                        </div>
                        <div class="screenshot-overlay">
                          <el-icon class="zoom-icon"><ZoomIn /></el-icon>
                        </div>
                      </div>
                      <div class="screenshot-info">
                        <p class="screenshot-description">{{ screenshot.description || t('uiAutomation.testCase.screenshot') + ' ' + (index + 1) }}</p>
                        <p class="screenshot-meta" v-if="screenshot.step_number">{{ t('uiAutomation.testCase.step') }} {{ screenshot.step_number }}</p>
                        <p class="screenshot-time" v-if="screenshot.timestamp">{{ formatTime(screenshot.timestamp) }}</p>
                      </div>
                    </div>
                  </div>
                </el-tab-pane>
                <el-tab-pane :label="t('uiAutomation.testCase.errorInfo')" name="errors" v-if="executionResult.errors && executionResult.errors.length > 0">
                  <div class="errors-container">
                    <div
                      v-for="(error, index) in executionResult.errors"
                      :key="index"
                      class="error-item"
                    >
                      <div class="error-header">
                        <el-tag type="danger" size="large">
                          <el-icon><WarningFilled /></el-icon>
                          {{ error.message || error }}
                        </el-tag>
                        <span v-if="error.step_number" class="error-step">
                          {{ t('uiAutomation.testCase.step') }} {{ error.step_number }}
                        </span>
                      </div>

                      <div v-if="error.action_type || error.element || error.description" class="error-meta">
                        <div v-if="error.action_type" class="meta-item">
                          <span class="meta-label">{{ t('uiAutomation.testCase.operationType') }}</span>
                          <span class="meta-value">{{ error.action_type }}</span>
                        </div>
                        <div v-if="error.element" class="meta-item">
                          <span class="meta-label">{{ t('uiAutomation.testCase.targetElement') }}</span>
                          <span class="meta-value">{{ error.element }}</span>
                        </div>
                        <div v-if="error.description" class="meta-item">
                          <span class="meta-label">{{ t('uiAutomation.testCase.stepDesc') }}</span>
                          <span class="meta-value">{{ error.description }}</span>
                        </div>
                      </div>

                      <div v-if="error.details || error.stack" class="error-details">
                        <div class="details-header">{{ t('uiAutomation.testCase.detailErrorInfo') }}</div>
                        <pre class="details-content">{{ error.details || error.stack }}</pre>
                      </div>
                    </div>
                  </div>
                </el-tab-pane>
              </el-tabs>
            </div>
          </div>
        </div>

        <div v-else class="no-selection">
          <el-empty :description="t('uiAutomation.testCase.selectTestCase')" />
        </div>
          </div>
        </template>
      </ResizableSplitPane>
    </div>

    <el-dialog
      v-model="showVariablesDialog"
      :title="t('uiAutomation.variables.title')"
      width="min(1040px, calc(100vw - 40px))"
      top="6vh"
      :close-on-click-modal="false"
      :close-on-press-escape="!savingVariables"
      :show-close="!savingVariables"
      destroy-on-close
      class="project-variables-dialog"
    >
      <div class="variables-case-heading">
        <span>{{ t('uiAutomation.variables.currentProject') }}</span>
        <strong>{{ projects.find(project => String(project.id) === String(projectId))?.name }}</strong>
        <el-tag size="small" type="info">{{ variableDraft.length }}</el-tag>
      </div>
      <div class="variables-dialog-body">
        <VariableEditor v-model="variableDraft" />
      </div>
      <template #footer>
        <el-button :disabled="savingVariables" @click="showVariablesDialog = false">{{ t('uiAutomation.common.cancel') }}</el-button>
        <el-button type="primary" :loading="savingVariables" @click="saveVariables">{{ t('uiAutomation.variables.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 登录态管理支持上传文件、手工编辑和项目内资源操作。 -->
    <el-dialog
      v-model="showStorageStateDialog"
      title="登录态管理"
      width="min(1040px, calc(100vw - 40px))"
      top="6vh"
      :close-on-click-modal="false"
      :close-on-press-escape="true"
      :show-close="true"
      destroy-on-close
      class="project-variables-dialog"
    >
      <div class="storage-state-toolbar">
        <el-button :icon="Plus" @click="openStorageStateEditor()">新建</el-button>
        <el-upload :show-file-list="false" accept=".json,application/json" :http-request="uploadStorageState">
          <el-button type="primary" :icon="Upload" :disabled="!projectId">上传</el-button>
        </el-upload>
      </div>
      <div class="variables-dialog-body storage-state-dialog-body">
        <el-table :data="storageStates" size="small" v-loading="loadingStorageStates">
          <el-table-column prop="name" label="名称" width="160" show-overflow-tooltip />
          <el-table-column prop="source_url" label="来源页面" min-width="180" show-overflow-tooltip />
          <el-table-column label="大小" width="100"><template #default="scope">{{ formatStorageSize(scope.row.file_size) }}</template></el-table-column>
          <el-table-column label="创建时间" width="150"><template #default="scope">{{ formatStorageStateTime(scope.row.created_at) }}</template></el-table-column>
          <el-table-column label="操作" width="180"><template #default="scope">
            <el-button link @click="downloadStorageStateFile(scope.row)">下载</el-button>
            <el-button link @click="openStorageStateEditor(scope.row)">编辑</el-button>
            <el-button link type="danger" @click="removeStorageState(scope.row)">删除</el-button>
          </template></el-table-column>
        </el-table>
      </div>
    </el-dialog>
    <el-dialog v-model="showStorageStateEditor" :title="editingStorageState ? '编辑登录态' : '新增登录态'" width="720px">
      <el-form label-width="100px">
        <el-form-item label="名称"><el-input v-model="storageStateDraft.name" /></el-form-item>
        <el-form-item label="来源页面"><el-input v-model="storageStateDraft.source_url" /></el-form-item>
        <el-form-item label="JSON"><el-input v-model="storageStateDraft.content" type="textarea" :rows="16" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="showStorageStateEditor = false">取消</el-button><el-button type="primary" @click="saveStorageStateEditor">保存</el-button></template>
    </el-dialog>

    <!-- 新建/编辑测试用例对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      :title="editingTestCase ? t('uiAutomation.testCase.editTestCase') : t('uiAutomation.testCase.createTestCase')"
      :close-on-click-modal="false"
      width="500px"
    >
      <el-form :model="testCaseForm" label-width="100px">
        <el-form-item :label="t('uiAutomation.testCase.caseName')" required>
          <el-input v-model="testCaseForm.name" :placeholder="t('uiAutomation.testCase.caseNamePlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('uiAutomation.testCase.caseDescription')">
          <el-input
            v-model="testCaseForm.description"
            type="textarea"
            :rows="3"
            :placeholder="t('uiAutomation.testCase.caseDescPlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="t('uiAutomation.testCase.priority')">
          <el-select v-model="testCaseForm.priority" style="width: 100%">
            <el-option :label="t('uiAutomation.testCase.priorityHigh')" value="high" />
            <el-option :label="t('uiAutomation.testCase.priorityMedium')" value="medium" />
            <el-option :label="t('uiAutomation.testCase.priorityLow')" value="low" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('uiAutomation.testCase.folder.label')">
          <el-select v-model="testCaseForm.folder" filterable allow-create clearable style="width: 100%" :placeholder="t('uiAutomation.testCase.folder.selectOrInputPath')">
          <el-option v-for="folder in folderOptions" :key="folder.id" :label="folder.label" :value="folder.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showCreateDialog = false">{{ t('uiAutomation.common.cancel') }}</el-button>
          <el-button type="primary" @click="saveTestCaseForm">{{ t('uiAutomation.common.confirm') }}</el-button>
        </span>
      </template>
    </el-dialog>

    <el-dialog v-model="showMoveTestCaseDialog" :title="t('uiAutomation.testCase.folder.moveTitle')" width="420px">
      <el-form label-width="90px">
        <el-form-item :label="t('uiAutomation.testCase.folder.target')">
          <el-select v-model="moveTargetFolder" clearable style="width: 100%" :placeholder="t('uiAutomation.testCase.folder.selectTarget')">
            <el-option v-for="folder in moveFolderOptions" :key="folder.id" :label="folder.label" :value="folder.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMoveTestCaseDialog = false">{{ t('uiAutomation.common.cancel') }}</el-button>
        <el-button type="primary" :loading="movingTestCase" @click="moveTestCaseToFolder">{{ t('uiAutomation.testCase.folder.move') }}</el-button>
      </template>
    </el-dialog>

    <!-- 截图预览对话框 -->
    <el-dialog
      v-model="showScreenshotPreview"
      :title="t('uiAutomation.testCase.screenshotPreview')"
      width="80%"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :modal="true"
      :destroy-on-close="false"
    >
      <div v-if="currentScreenshot" class="screenshot-preview">
        <div class="preview-info">
          <h4>{{ currentScreenshot.description }}</h4>
          <p v-if="currentScreenshot.step_number">{{ t('uiAutomation.testCase.failedStep') }}: {{ t('uiAutomation.testCase.step') }} {{ currentScreenshot.step_number }}</p>
          <p v-if="currentScreenshot.timestamp">{{ t('uiAutomation.testCase.screenshotTime') }}: {{ formatTime(currentScreenshot.timestamp) }}</p>
        </div>
        <div class="preview-image">
          <img :src="currentScreenshot.url" :alt="currentScreenshot.description" />
        </div>
      </div>
    </el-dialog>

    <!-- 变量助手对话框 -->
    <el-dialog
      :close-on-press-escape="false"
      :modal="true"
      :destroy-on-close="false"
      v-model="showVariableHelper"
      :title="t('uiAutomation.testCase.variableHelper')"
      :close-on-click-modal="false"
      width="900px"
    >
      <el-tabs tab-position="left" style="height: 450px">
        <el-tab-pane
          v-for="(category, index) in variableCategoriesComputed"
          :key="index"
          :label="category.label"
        >
          <div style="height: 450px; overflow-y: auto; padding: 10px;">
            <el-table :data="category.variables" style="width: 100%" @row-click="insertVariable" highlight-current-row>
              <el-table-column prop="name" :label="t('uiAutomation.testCase.functionName')" width="150" show-overflow-tooltip>
                <template #default="{ row }">
                  <el-tag size="small">{{ row.name }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="desc" :label="t('uiAutomation.testCase.description')" min-width="150" />
              <el-table-column prop="syntax" :label="t('uiAutomation.testCase.syntax')" min-width="200" show-overflow-tooltip />
              <el-table-column prop="example" :label="t('uiAutomation.testCase.example')" min-width="200" show-overflow-tooltip />
              <el-table-column :label="t('uiAutomation.testCase.operation')" width="80" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" size="small">{{ t('uiAutomation.testCase.insert') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
    
    <DataFactorySelector
      v-model="showDataFactorySelector"
      @select="handleDataFactorySelect"
    />
  </div>
</template>

<script setup>
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import StructuredStepEditor from '@/components/ui-automation/StructuredStepEditor.vue'
import VariableEditor from '@/components/ui-automation/VariableEditor.vue'
import { loadUiVariables, formatUiVariables } from '@/utils/uiVariables'
import { ref, reactive, computed, onMounted, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, Plus, Edit, Delete, Check, CaretRight, Picture, Warning, View, ZoomIn, Refresh, WarningFilled, MagicStick, Setting, Upload, Folder, FolderAdd, Document, CopyDocument, Expand, Fold
} from '@element-plus/icons-vue'
import DataFactorySelector from '@/components/DataFactorySelector.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const leftPanelWidth = ref(350)

import {
  getUiProjects,
  updateUiProject,
  getElements,
  createTestCase,
  updateTestCase,
  deleteTestCase as deleteTestCaseApi,
  getTestCases,
  getTestCaseFolders,
  createTestCaseFolder,
  updateTestCaseFolder,
  deleteTestCaseFolder,
  getTestCaseDetail,
  runTestCase as runTestCaseApi,
  copyTestCase as copyTestCaseApi,
  getLocatorStrategies,
  getElementGroupTree,
  getStorageStates, createStorageState, createStorageStateFromContent, updateStorageState, deleteStorageState, downloadStorageState, updateStorageStateContent
} from '@/api/ui_automation'
import { getVariableFunctions } from '@/api/data-factory'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'

// 响应式数据
const projects = ref([])
const projectId = ref('')
useRememberedProject(projectId, projects)
const testCases = ref([])
const testCaseFolders = ref([])
const testCaseTreeRef = ref(null)
const expandedFolderKeys = ref(new Set())
const selectedTestCase = ref(null)
const currentSteps = ref([])
const currentVariables = ref([])
const storageStates = ref([])
const selectedStorageStateId = ref(null)
const loadingStorageStates = ref(false)
const showStorageStateDialog = ref(false)
const showStorageStateEditor = ref(false)
const editingStorageState = ref(null)
const storageStateDraft = reactive({ name: '', source_url: '', content: '' })
const showVariablesDialog = ref(false)
const variableDraft = ref([])
const savingVariables = ref(false)
const availableElements = ref([])
const elementsLoading = ref(false)
const elementLibraryLoaded = ref(false)
const elementsReady = ref(Promise.resolve())
const elementSearchCache = new Set()
const elementSearchInFlight = new Set()
const elementSearchTimers = new Map()
const pages = ref([])
const searchKeyword = ref('')
const showFolderContextMenu = ref(false)
const folderContextMenuX = ref(0)
const folderContextMenuY = ref(0)
const folderContextMenuRef = ref(null)
const contextTreeNode = ref(null)
const showMoveTestCaseDialog = ref(false)
const moveTargetFolder = ref('')
const movingTestCase = ref(false)
const showCreateDialog = ref(false)
const editingTestCase = ref(null)
const executionResult = ref(null)
const resultActiveTab = ref('logs')
// 从当前步骤推导按钮状态，切换用例、保存回填和单步折叠时均自动更新。
// 空步骤列表按未展开处理，避免 every() 对空数组返回 true。
const allStepsExpanded = computed(() => currentSteps.value.length > 0 && currentSteps.value.every(step => step.expanded))
const showSteps = ref(true)
const showScreenshotPreview = ref(false)
const currentScreenshot = ref(null)
const isRunning = ref(false)
const selectedEngine = ref('playwright')  // 默认使用Playwright
const selectedBrowser = ref('chrome')  // 默认使用Chrome
const headlessMode = ref(true)  // 默认使用无头模式
const showVariableHelper = ref(false)
const currentEditingStep = ref(null)
const currentEditingField = ref('')
const showDataFactorySelector = ref(false)
const currentStepForDataFactory = ref(null)
const currentFieldForDataFactory = ref('')
const variableCategories = ref([])
const loading = ref(false)



// 表单数据
const testCaseForm = reactive({
  name: '',
  description: '',
  priority: 'medium',
  folder: null
})

// 计算属性
const filteredTestCases = computed(() => {
  if (!searchKeyword.value) return testCases.value
  return testCases.value.filter(tc =>
    tc.name.includes(searchKeyword.value) ||
    tc.description?.includes(searchKeyword.value)
  )
})

const folderOptions = computed(() => {
  const byId = new Map(testCaseFolders.value.map(folder => [String(folder.id), folder]))
  const getLabel = (folder, seen = new Set()) => {
    if (!folder || seen.has(String(folder.id))) return folder?.name || ''
    const nextSeen = new Set(seen).add(String(folder.id))
    const parent = byId.get(String(folder.parent))
    return parent ? `${getLabel(parent, nextSeen)} / ${folder.name}` : folder.name
  }
  return testCaseFolders.value
    .map(folder => ({ id: folder.id, label: getLabel(folder) }))
    .sort((a, b) => a.label.localeCompare(b.label, 'zh', { numeric: true }))
})

const moveFolderOptions = computed(() => folderOptions.value.filter(
  folder => String(folder.id) !== String(contextTreeNode.value?.folder)
))

// 使用文件夹的父子关系构建树，目录名称变化时不需要同步修改用例数据。
const testCaseTree = computed(() => {
  const root = []
  const map = new Map()
  testCaseFolders.value.forEach(folder => {
    map.set(String(folder.id), { id: `f-${folder.id}`, folderId: folder.id, name: folder.name, type: 'folder', children: [] })
  })
  testCaseFolders.value.forEach(folder => {
    const node = map.get(String(folder.id))
    const parent = map.get(String(folder.parent))
    ;(parent ? parent.children : root).push(node)
  })

  filteredTestCases.value.forEach(testCase => {
    const parent = map.get(String(testCase.folder))
    ;(parent ? parent.children : root).push({ ...testCase, type: 'testCase' })
  })

  const sortTree = nodes => {
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'folder' ? -1 : 1
      return a.name.localeCompare(b.name, 'zh', { numeric: true })
    })
    nodes.forEach(node => node.children && sortTree(node.children))
  }
  sortTree(root)
  return root
})

const treeDefaultExpandedKeys = computed(() => testCaseTree.value.filter(node => node.type === 'folder').map(node => node.id))

watch(testCaseFolders, (folders, previousFolders = []) => {
  const folderKeys = new Set(folders.map(folder => `f-${folder.id}`))
  const previousKeys = new Set(previousFolders.map(folder => `f-${folder.id}`))
  const nextExpandedKeys = new Set([...expandedFolderKeys.value].filter(key => folderKeys.has(key)))

  folders.forEach(folder => {
    const key = `f-${folder.id}`
    if (!folder.parent && !previousKeys.has(key)) nextExpandedKeys.add(key)
  })
  expandedFolderKeys.value = nextExpandedKeys
})

const allFoldersExpanded = computed(() => (
  testCaseFolders.value.length > 0 &&
  testCaseFolders.value.every(folder => expandedFolderKeys.value.has(`f-${folder.id}`))
))

const expandAllFolders = () => {
  const expandedKeys = new Set()
  testCaseFolders.value.forEach(folder => {
    const node = testCaseTreeRef.value?.getNode(`f-${folder.id}`)
    if (!node) return
    node.expand()
    expandedKeys.add(node.data.id)
  })
  expandedFolderKeys.value = expandedKeys
}

const collapseAllFolders = () => {
  testCaseFolders.value.forEach(folder => {
    testCaseTreeRef.value?.getNode(`f-${folder.id}`)?.collapse()
  })
  expandedFolderKeys.value = new Set()
}

const onFolderNodeExpand = data => {
  if (data.type !== 'folder') return
  expandedFolderKeys.value = new Set([...expandedFolderKeys.value, data.id])
}

const onFolderNodeCollapse = data => {
  if (data.type !== 'folder') return
  const keys = new Set(expandedFolderKeys.value)
  keys.delete(data.id)
  expandedFolderKeys.value = keys
}

const toggleAllFolders = () => {
  if (allFoldersExpanded.value) collapseAllFolders()
  else expandAllFolders()
}

// 解析执行日志
const parsedExecutionLogs = computed(() => {
  if (!executionResult.value || !executionResult.value.logs) return []
  try {
    return typeof executionResult.value.logs === 'string'
      ? JSON.parse(executionResult.value.logs)
      : executionResult.value.logs
  } catch (e) {
    console.error('解析执行日志失败:', e)
    return []
  }
})

// 方法定义
const loadProjects = async () => {
  try {
    const response = await getUiProjects({ page_size: 100 })
    projects.value = response.data.results || response.data
  } catch (error) {
    ElMessage.error('获取项目列表失败')
    console.error('获取项目列表失败:', error)
  }
}

const loadTestCases = async () => {
  if (!projectId.value) {
    testCases.value = []
    return
  }

  try {
    // 列表接口按页返回，逐页读取避免项目用例超过单页数量时被截断。
    const cases = []
    let page = 1
    let hasNext = true
    while (hasNext) {
      const response = await getTestCases({ project: projectId.value, page, page_size: 1000 })
      const pageCases = response.data.results || response.data
      cases.push(...pageCases)
      hasNext = Boolean(response.data.next)
      page += 1
    }
    testCases.value = cases
  } catch (error) {
    console.error('获取测试用例失败:', error)
  }
}

const loadTestCaseFolders = async () => {
  if (!projectId.value) {
    testCaseFolders.value = []
    return
  }
  try {
    const folders = []
    let page = 1
    let hasNext = true
    while (hasNext) {
      const response = await getTestCaseFolders({ project: projectId.value, page, page_size: 1000 })
      folders.push(...(response.data.results || response.data))
      hasNext = Boolean(response.data.next)
      page += 1
    }
    testCaseFolders.value = folders
  } catch (error) {
    console.error('获取测试用例文件夹失败:', error)
    testCaseFolders.value = []
  }
}

const onFolderNodeContextMenu = async (event, data) => {
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

const createFolder = async (parentFolder = null) => {
  if (!projectId.value) return
  showFolderContextMenu.value = false
  try {
    const { value: name } = await ElMessageBox.prompt(
      t('uiAutomation.testCase.folder.inputName'),
      t('uiAutomation.testCase.folder.create'),
      {
        inputPattern: /^[^/\\]+$/,
        inputErrorMessage: t('uiAutomation.testCase.folder.invalidName'),
        confirmButtonText: t('uiAutomation.common.confirm'),
        cancelButtonText: t('uiAutomation.common.cancel')
      }
    )
    const response = await createTestCaseFolder({ project: projectId.value, name: name.trim(), parent: parentFolder?.folderId || null })
    testCaseFolders.value = [...testCaseFolders.value, response.data]
    ElMessage.success(t('uiAutomation.testCase.folder.created'))
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.name?.[0] || t('uiAutomation.testCase.folder.createFailed'))
  }
}

const renameFolder = async folder => {
  if (!folder?.folderId) return
  showFolderContextMenu.value = false
  try {
    const { value: name } = await ElMessageBox.prompt(
      t('uiAutomation.testCase.folder.inputNewName'),
      t('uiAutomation.testCase.folder.rename'),
      {
        inputValue: folder.name,
        inputPattern: /^[^/\\]+$/,
        inputErrorMessage: t('uiAutomation.testCase.folder.invalidName'),
        confirmButtonText: t('uiAutomation.common.save'),
        cancelButtonText: t('uiAutomation.common.cancel')
      }
    )
    const response = await updateTestCaseFolder(folder.folderId, { name: name.trim() })
    const updatedFolder = response.data
    testCaseFolders.value = testCaseFolders.value.map(item => (
      String(item.id) === String(updatedFolder.id) ? updatedFolder : item
    ))
    ElMessage.success(t('uiAutomation.testCase.folder.renamed'))
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.name?.[0] || t('uiAutomation.testCase.folder.renameFailed'))
  }
}

const deleteFolder = async folder => {
  if (!folder?.folderId) return
  showFolderContextMenu.value = false
  try {
    await ElMessageBox.confirm(
      t('uiAutomation.testCase.folder.confirmDelete', { name: folder.name }),
      t('uiAutomation.common.confirmDelete'),
      {
        confirmButtonText: t('uiAutomation.common.delete'),
        cancelButtonText: t('uiAutomation.common.cancel'),
        type: 'warning'
      }
    )
    await deleteTestCaseFolder(folder.folderId)
    const folderIds = new Set([String(folder.folderId)])
    let foundDescendant = true
    while (foundDescendant) {
      foundDescendant = false
      testCaseFolders.value.forEach(item => {
        if (folderIds.has(String(item.parent)) && !folderIds.has(String(item.id))) {
          folderIds.add(String(item.id))
          foundDescendant = true
        }
      })
    }
    testCaseFolders.value = testCaseFolders.value.filter(item => !folderIds.has(String(item.id)))
    testCases.value = testCases.value.map(item => (
      folderIds.has(String(item.folder)) ? { ...item, folder: null } : item
    ))
    if (selectedTestCase.value && folderIds.has(String(selectedTestCase.value.folder))) {
      selectedTestCase.value = { ...selectedTestCase.value, folder: null }
    }
    ElMessage.success(t('uiAutomation.testCase.folder.deleted'))
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(t('uiAutomation.testCase.folder.deleteFailed'))
  }
}

const openMoveTestCaseDialog = testCase => {
  showFolderContextMenu.value = false
  contextTreeNode.value = testCase
  moveTargetFolder.value = testCase.folder || null
  showMoveTestCaseDialog.value = true
}

const moveTestCaseToFolder = async () => {
  const testCase = contextTreeNode.value
  if (!testCase) return
  movingTestCase.value = true
  try {
    const response = await updateTestCase(testCase.id, { folder: moveTargetFolder.value || null, project: projectId.value })
    const updatedTestCase = response.data
    testCases.value = testCases.value.map(item => (
      String(item.id) === String(updatedTestCase.id) ? { ...item, ...updatedTestCase } : item
    ))
    ElMessage.success(t('uiAutomation.testCase.folder.moved'))
    showMoveTestCaseDialog.value = false
    if (selectedTestCase.value?.id === testCase.id) {
      selectedTestCase.value = testCases.value.find(item => String(item.id) === String(testCase.id)) || null
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.folder?.[0] || t('uiAutomation.testCase.folder.moveFailed'))
  } finally {
    movingTestCase.value = false
  }
}

const loadElements = async () => {
  // 项目切换时后台预加载一次全量元素；步骤编排复用此缓存，避免重复请求。
  availableElements.value = []
  elementsLoading.value = true
  elementLibraryLoaded.value = false
  elementSearchCache.clear()
  elementSearchInFlight.clear()
  elementSearchTimers.forEach(timer => clearTimeout(timer))
  elementSearchTimers.clear()

  if (!projectId.value) {
    elementsLoading.value = false
    elementsReady.value = Promise.resolve()
    return
  }

  const project = projectId.value
  const request = getElements({ project, page_size: 1000, compact: 1 })
    .then(response => {
      if (String(projectId.value) !== String(project)) return
      availableElements.value = response.data.results || response.data
      elementLibraryLoaded.value = true
    })
    .catch(error => {
      console.error('获取元素列表失败:', error)
    })
    .finally(() => {
      if (String(projectId.value) === String(project)) elementsLoading.value = false
    })
  elementsReady.value = request
  return request
}

const mergeAvailableElements = (elements = []) => {
  const byId = new Map(availableElements.value.map(element => [String(element.id), element]))
  elements.forEach(element => byId.set(String(element.id), element))
  availableElements.value = [...byId.values()]
}

const elementSearchKey = (step, keyword = '') => `${step.element_page || ''}\u0000${String(keyword).trim().toLowerCase()}`

const fetchElementsForStep = async (step, keyword = '') => {
  if (!projectId.value) return
  // 全量缓存完成后由 Element Plus 在本地过滤，避免打开选择器再次请求后端。
  if (elementLibraryLoaded.value) return
  const normalizedKeyword = String(keyword || '').trim()
  const cacheKey = elementSearchKey(step, normalizedKeyword)
  if (elementSearchCache.has(cacheKey) || elementSearchInFlight.has(cacheKey)) return

  elementSearchInFlight.add(cacheKey)
  elementsLoading.value = true
  try {
    const response = await getElements({
      project: projectId.value,
      page_name: step.element_page || undefined,
      search: normalizedKeyword || undefined,
      compact: 1,
      page_size: 50
    })
    mergeAvailableElements(response.data.results || response.data)
    elementSearchCache.add(cacheKey)
  } catch (error) {
    console.error('按需获取元素失败:', error)
  } finally {
    elementSearchInFlight.delete(cacheKey)
    elementsLoading.value = false
  }
}

const getStepElementId = step => {
  const value = step?.element ?? step?.element_id
  return value && typeof value === 'object' ? value.id : value
}

const loadElementsForStep = step => fetchElementsForStep(step)

const searchElementsForStep = ({ step, keyword }) => {
  const timerKey = String(step.id ?? step.element_id ?? 'new-step')
  const oldTimer = elementSearchTimers.get(timerKey)
  if (oldTimer) clearTimeout(oldTimer)
  const timer = setTimeout(() => {
    elementSearchTimers.delete(timerKey)
    fetchElementsForStep(step, keyword)
  }, 180)
  elementSearchTimers.set(timerKey, timer)
}

const loadPages = async () => {
  if (!projectId.value) {
    pages.value = []
    return
  }

  try {
    const response = await getElementGroupTree({ project: projectId.value })
    const flattenedPages = []
    const flatten = (groups) => {
      groups.forEach(group => {
        flattenedPages.push({ id: group.id, name: group.name })
        if (group.children) flatten(group.children)
      })
    }
    flatten(response.data || [])
    pages.value = flattenedPages
  } catch (error) {
    pages.value = []
    console.error('获取页面列表失败:', error)
  }
}

const onProjectChange = async () => {
  selectedTestCase.value = null
  currentSteps.value = []
  currentVariables.value = loadUiVariables(projects.value.find(project => String(project.id) === String(projectId.value))?.variables || [])
  executionResult.value = null

  // 元素库在后台加载；用例列表和页面树无需等待全量元素响应。
  loadElements()
  await Promise.all([
    loadTestCases(),
    loadTestCaseFolders(),
    loadPages(),
    loadStorageStates()
  ])
}

const loadStorageStates = async () => {
  // 项目切换后刷新可用登录态，保证选择器和管理列表使用同一数据源。
  if (!projectId.value) { storageStates.value = []; return }
  loadingStorageStates.value = true
  try {
    const response = await getStorageStates({ project: projectId.value, page_size: 200 })
    storageStates.value = response.data.results || response.data
  } catch { storageStates.value = [] } finally { loadingStorageStates.value = false }
}
const openStorageStateManager = async () => { await loadStorageStates(); showStorageStateDialog.value = true }
const formatStorageSize = size => size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`
// 列表时间只保留到分钟，避免后端微秒和时区信息撑开表格。
const formatStorageStateTime = timestamp => {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return timestamp
  const pad = value => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
// 自定义上传请求直接使用 Element Plus 提供的原始 File，确保请求包含文件字节。
const uploadStorageState = async ({ file, onSuccess, onError }) => {
  if (!(file instanceof File)) {
    const error = new Error('请选择有效的 JSON 文件')
    onError(error)
    ElMessage.error(error.message)
    return
  }
  const form = new FormData(); form.append('file', file); form.append('project', projectId.value)
  form.append('name', file.name.replace(/\.json$/i, '')); form.append('browser', 'chromium')
  try {
    const response = await createStorageState(form)
    await loadStorageStates()
    ElMessage.success('登录态已添加')
    onSuccess(response.data)
  } catch (error) {
    onError(error)
    ElMessage.error(error.response?.data?.file?.[0] || error.response?.data?.error || '上传失败')
  }
}
// 打开新增或编辑表单；编辑已有资源时先读取其当前 JSON 内容。
const openStorageStateEditor = async state => {
  editingStorageState.value = state || null
  Object.assign(storageStateDraft, { name: state?.name || '', source_url: state?.source_url || '', content: '{\n  "cookies": [],\n  "origins": []\n}' })
  if (state) { const response = await downloadStorageState(state.id); storageStateDraft.content = await response.data.text() }
  showStorageStateEditor.value = true
}
const saveStorageStateEditor = async () => {
  // 手工编辑先在前端校验标准 JSON，再分别调用元数据和内容接口。
  let content
  try { content = JSON.parse(storageStateDraft.content) } catch { ElMessage.error('JSON 格式无效'); return }
  if (!Array.isArray(content.cookies || []) || !Array.isArray(content.origins || [])) { ElMessage.error('cookies 和 origins 必须为数组'); return }
  try {
    if (editingStorageState.value) {
      await updateStorageState(editingStorageState.value.id, { name: storageStateDraft.name, source_url: storageStateDraft.source_url })
      await updateStorageStateContent(editingStorageState.value.id, content)
    } else {
      await createStorageStateFromContent({
        project: projectId.value,
        name: storageStateDraft.name,
        source_url: storageStateDraft.source_url,
        content
      })
    }
    showStorageStateEditor.value = false; await loadStorageStates()
  } catch (error) { ElMessage.error(error.response?.data?.content?.[0] || error.message || '保存失败') }
}
const downloadStorageStateFile = async state => {
  const response = await downloadStorageState(state.id); const url = URL.createObjectURL(response.data)
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${state.name}.json`; anchor.click(); URL.revokeObjectURL(url)
}
const removeStorageState = async state => {
  try { await ElMessageBox.confirm(`确定删除登录态“${state.name}”吗？`, '确认删除', { type: 'warning' }); await deleteStorageState(state.id); await loadStorageStates() } catch {}
}

// 将接口返回的测试步骤转换为结构化步骤组件所需的编辑字段。
const hydrateTestCaseSteps = (steps = []) => steps.map(step => {
  const elementId = getStepElementId(step)
  const element = availableElements.value.find(item => String(item.id) === String(elementId)) || (
    elementId
      ? {
          id: elementId,
          name: step.element_name || `元素 #${elementId}`,
          page: step.element_page || '',
          locator_value: step.element_locator || '',
          locator_strategy: step.element_locator_strategy || '',
          locator_options: step.element_locator_options || {}
        }
      : null
  )
  if (element) mergeAvailableElements([element])
  const strategy = element?.locator_strategy?.name || element?.locator_strategy || step.element_locator_strategy || ''
  const locatorOptions = element?.locator_options || step.element_locator_options || {}
  return {
    ...step,
    element_id: elementId || '',
    // 优先使用元素库数据，详情接口字段作为元素列表尚未加载时的回退。
    element_page: element?.page || step.element_page || '',
    locator_strategy: strategy.toLowerCase(),
    locator_value: element?.locator_value || step.element_locator || '',
    locator_options: { ...locatorOptions },
    locator_options_json: JSON.stringify(locatorOptions),
    locator_name: locatorOptions.name || step.element_locator_options?.name || '',
    expanded: false
  }
})

const selectTestCase = async (testCase) => {
  // 如果点击的是同一个用例，不做任何处理
  if (selectedTestCase.value && selectedTestCase.value.id === testCase.id) {
    return
  }

  // 列表数据可能没有最新步骤，切换时读取详情确保元素关联信息完整。
  let selected = testCase
  try {
    const response = await getTestCaseDetail(testCase.id)
    selected = response.data
  } catch (error) {
    console.warn('获取测试用例详情失败，使用列表数据:', error)
  }
  // 等待项目级元素库缓存完成，再渲染步骤，确保下拉框显示完整元素信息。
  currentSteps.value = []
  await elementsReady.value
  selectedTestCase.value = selected
  // 确保步骤数据格式正确，添加前端需要的字段
  if (selected.steps && selected.steps.length > 0) {
    currentSteps.value = hydrateTestCaseSteps(selected.steps)
  } else {
    currentSteps.value = []
  }
  // 只有在切换到不同用例时才清空执行结果
  executionResult.value = null
  showSteps.value = true
}

const addStep = () => {
  const newStep = {
    id: Date.now(),
    action_type: 'click',
    element_id: '',
    element_page: '',
    input_value: '',
    wait_time: 1000,
    assert_type: 'textContains',
    assert_value: '',
    action_params: {},
    description: '',
    locator_strategy: 'role',
    locator_value: '',
    locator_name: '',
    locator_options: {},
    locator_options_json: '{}',
    expanded: true
  }
  currentSteps.value.push(newStep)
}

const onStepsReorder = () => {
  // 步骤重新排序后的处理
  console.log('步骤已重新排序')
}

const expandAllSteps = () => {
  // 先固定本次操作的目标状态，避免遍历时计算属性随单个步骤变化。
  const expanded = !allStepsExpanded.value
  currentSteps.value.forEach(step => {
    step.expanded = expanded
  })
}

const openVariablesDialog = () => {
  if (!projectId.value) return
  variableDraft.value = structuredClone(formatUiVariables(currentVariables.value))
  variableDraft.value = loadUiVariables(variableDraft.value)
  showVariablesDialog.value = true
}

const saveVariables = async () => {
  if (!projectId.value || savingVariables.value) return
  savingVariables.value = true
  try {
    const variables = formatUiVariables(variableDraft.value)
    // 单独 PATCH 项目变量，避免覆盖当前尚未保存的步骤编辑。
    const response = await updateUiProject(projectId.value, { variables })
    currentVariables.value = loadUiVariables(response.data.variables || variables)
    const index = projects.value.findIndex(item => String(item.id) === String(projectId.value))
    if (index !== -1) projects.value[index] = { ...projects.value[index], variables: response.data.variables || variables }
    showVariablesDialog.value = false
    ElMessage.success(t('uiAutomation.variables.saved'))
  } catch (error) {
    ElMessage.error(error.response?.data?.variables?.join?.(' / ') || error.message || t('uiAutomation.testCase.save.failed'))
  } finally {
    savingVariables.value = false
  }
}

const saveTestCase = async () => {
  if (!selectedTestCase.value) return

  try {
    const updateData = {
      ...selectedTestCase.value,
      // 提交项目主键，避免把序列化后的项目对象传回步骤更新接口。
      project: projectId.value,
      steps: currentSteps.value.map(({ expanded, element_page, ...step }) => step)
    }

    await updateTestCase(selectedTestCase.value.id, updateData)
    // 保存后重新读取服务端步骤，确保元素、定位策略和定位器来自真实关联数据。
    const detailResponse = await getTestCaseDetail(selectedTestCase.value.id)
    const savedTestCase = detailResponse.data
    await elementsReady.value
    currentSteps.value = hydrateTestCaseSteps(savedTestCase.steps || [])
    ElMessage.success(t('uiAutomation.testCase.save.success'))

    // 更新本地数据
    const index = testCases.value.findIndex(tc => tc.id === selectedTestCase.value.id)
    if (index !== -1) {
      testCases.value[index] = savedTestCase
    }
    selectedTestCase.value = savedTestCase
  } catch (error) {
      console.error('保存测试用例失败:', error)
      ElMessage.error(error.response?.data?.variables?.join?.(' / ') || error.message || t('uiAutomation.testCase.save.failed'))
    }
}

const runTestCase = async (testCase) => {
  isRunning.value = true
  try {
    const modeText = headlessMode.value ? t('uiAutomation.testCase.runMode.headless') : t('uiAutomation.testCase.runMode.headed')
    ElMessage.info(t('uiAutomation.testCase.run.start', { engine: selectedEngine.value.toUpperCase(), browser: selectedBrowser.value.toUpperCase(), mode: modeText }))

    const response = await runTestCaseApi(testCase.id, {
      project_id: projectId.value,
      engine: selectedEngine.value,
      browser: selectedBrowser.value,
      headless: headlessMode.value,
      storage_state_id: selectedEngine.value === 'playwright' ? selectedStorageStateId.value : null
    })

    executionResult.value = response.data
    resultActiveTab.value = 'logs'
    showSteps.value = false  // 自动切换到结果视图

    if (response.data.success) {
      ElMessage.success(t('uiAutomation.testCase.run.success'))
    } else {
      ElMessage.error(t('uiAutomation.testCase.run.failed'))
      // 如果有截图，自动切换到截图标签页
      if (response.data.screenshots && response.data.screenshots.length > 0) {
        resultActiveTab.value = 'screenshots'
      }
    }
  } catch (error) {
    console.error('执行测试用例失败:', error)

    // 即使出错也要设置执行结果,显示错误信息
    const errorMessage = error.response?.data?.message || error.message || '执行失败'
    const errorLogs = error.response?.data?.logs || `测试用例执行出错\n\n错误信息: ${errorMessage}`

    // 格式化错误信息为统一的对象格式
    const errors = error.response?.data?.errors || [{
      message: errorMessage,
      details: error.stack || '',
      step_number: null,
      action_type: '',
      element: '',
      description: ''
    }]

    executionResult.value = {
      success: false,
      logs: errorLogs,
      screenshots: error.response?.data?.screenshots || [],
      execution_time: 0,
      errors: errors
    }
    resultActiveTab.value = 'logs'
    showSteps.value = false  // 切换到结果视图显示错误

    ElMessage.error(t('uiAutomation.testCase.run.failedWithMessage', { message: errorMessage }))
  } finally {
    isRunning.value = false
  }
}

const toggleView = () => {
  showSteps.value = !showSteps.value
}

const editTestCase = (testCase) => {
  editingTestCase.value = testCase
  testCaseForm.name = testCase.name
  testCaseForm.description = testCase.description || ''
  testCaseForm.priority = testCase.priority || 'medium'
  testCaseForm.folder = testCase.folder || null
  showCreateDialog.value = true
}

const deleteTestCase = async (testCase) => {
  try {
    await ElMessageBox.confirm(
      t('uiAutomation.testCase.delete.confirm', { name: testCase.name }),
      t('uiAutomation.testCase.delete.title'),
      {
        confirmButtonText: t('uiAutomation.common.confirm'),
        cancelButtonText: t('uiAutomation.common.cancel'),
        type: 'warning'
      }
    )

    await deleteTestCaseApi(testCase.id)
    ElMessage.success(t('uiAutomation.testCase.delete.success'))

    // 从列表中移除
    const index = testCases.value.findIndex(tc => tc.id === testCase.id)
    if (index !== -1) {
      testCases.value.splice(index, 1)
    }

    // 如果删除的是当前选中的用例，清空选择
    if (selectedTestCase.value?.id === testCase.id) {
      selectedTestCase.value = null
      currentSteps.value = []
      currentVariables.value = loadUiVariables(projects.value.find(project => String(project.id) === String(projectId.value))?.variables || [])
      executionResult.value = null
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除测试用例失败:', error)
      ElMessage.error('删除失败')
    }
  }
}

const copyTestCase = async (testCase) => {
  try {
    await ElMessageBox.confirm(
      t('uiAutomation.testCase.copy.confirm', { name: testCase.name }),
      t('uiAutomation.testCase.copy.title'),
      {
        confirmButtonText: t('uiAutomation.common.confirm'),
        cancelButtonText: t('uiAutomation.common.cancel'),
        type: 'info'
      }
    )

    const response = await copyTestCaseApi(testCase.id)
    ElMessage.success(t('uiAutomation.testCase.copy.success'))

    // 找到原用例的位置
    const index = testCases.value.findIndex(tc => tc.id === testCase.id)
    if (index !== -1) {
      // 在原用例下方插入新用例
      testCases.value.splice(index + 1, 0, response.data)
    } else {
      // 如果找不到，就添加到末尾
      testCases.value.push(response.data)
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('复制测试用例失败:', error)
      ElMessage.error('复制失败')
    }
  }
}

// 加载变量函数
const loadVariableFunctions = async () => {
  try {
    loading.value = true
    console.log('开始加载变量函数...')
    const apiResponse = await getVariableFunctions()
    console.log('变量函数响应:', apiResponse)
    console.log('变量函数响应.data:', apiResponse.data)
    
    // 检查不同可能的数据结构
    let functionsData = []
    if (apiResponse && apiResponse.data) {
      if (Array.isArray(apiResponse.data)) {
        // 后端返回的是数组，直接使用
        functionsData = apiResponse.data
      } else if (apiResponse.data.functions) {
        // 如果data中有functions字段，使用它
        functionsData = apiResponse.data.functions
      } else if (typeof apiResponse.data === 'object') {
        // 如果data是对象但没有functions字段，假设整个对象就是按分类组织的函数
        functionsData = apiResponse.data
      }
    }
    
    console.log('处理后的函数数据:', functionsData)
    
    // 处理函数数据，按分类组织
    const grouped = {}
    
    if (Array.isArray(functionsData)) {
      // 如果是数组格式
      functionsData.forEach(func => {
        const category = func.category || '未分类'
        if (!grouped[category]) {
          grouped[category] = []
        }
        grouped[category].push({
          name: func.name,
          syntax: func.syntax,
          desc: func.description || func.desc || '',
          example: func.example
        })
      })
    } else if (typeof functionsData === 'object') {
      // 如果是按分类组织的对象格式
      for (const [category, funcs] of Object.entries(functionsData)) {
        if (Array.isArray(funcs)) {
          grouped[category] = funcs.map(func => ({
            name: func.name,
            syntax: func.syntax,
            desc: func.description || func.desc || '',
            example: func.example
          }))
        }
      }
    }
    
    console.log('按分类组织后的函数:', grouped)
    
    // 定义固定的分类顺序
    const categoryOrder = ['随机数', '测试数据', '字符串', '编码转换', '加密', '时间日期', 'Crontab', '未分类']
    
    // 按固定顺序构建分类列表
    const orderedCategories = []
    categoryOrder.forEach(category => {
      if (grouped[category]) {
        orderedCategories.push({
          label: category,
          variables: grouped[category]
        })
        delete grouped[category]
      }
    })
    
    // 添加剩余的分类
    for (const [category, funcs] of Object.entries(grouped)) {
      orderedCategories.push({
        label: category,
        variables: funcs
      })
    }
    
    console.log('最终的分类列表:', orderedCategories)
    variableCategories.value = orderedCategories
  } catch (error) {
    console.error('加载变量函数失败:', error)
    ElMessage.error('加载变量函数失败，使用本地数据')
    useLocalVariableCategories()
  } finally {
    loading.value = false
  }
}

// 使用本地变量分类数据作为 fallback
const useLocalVariableCategories = () => {
  variableCategories.value = [
    {
      label: t('uiAutomation.testCase.variableCategory.randomNumber'),
      variables: [
        { name: 'random_int', syntax: '${random_int(min, max, count)}', desc: t('uiAutomation.testCase.variable.randomInt.desc'), example: '${random_int(100, 999, 1)}' },
        { name: 'random_float', syntax: '${random_float(min, max, precision, count)}', desc: t('uiAutomation.testCase.variable.randomFloat.desc'), example: '${random_float(0, 1, 2, 1)}' }
      ]
    },
    {
      label: t('uiAutomation.testCase.variableCategory.randomString'),
      variables: [
        { name: 'random_string', syntax: '${random_string(length, char_type, count)}', desc: t('uiAutomation.testCase.variable.randomString.desc'), example: '${random_string(8, "all", 1)}' }
      ]
    }
  ]
}

// 计算属性提供变量分类数据
const variableCategoriesComputed = computed(() => {
  return variableCategories.value.length > 0 ? variableCategories.value : [
    {
      label: t('uiAutomation.testCase.variableCategory.randomNumber'),
      variables: []
    }
  ]
})

const openVariableHelper = (step, field) => {
  console.log('TestCaseManager openVariableHelper 被调用, step:', step, 'field:', field)
  console.log('variableCategories.value:', variableCategories.value)
  console.log('variableCategories.value.length:', variableCategories.value.length)
  currentEditingStep.value = step
  currentEditingField.value = field
  showVariableHelper.value = true
  console.log('showVariableHelper.value:', showVariableHelper.value)
}

const openDataFactorySelector = (step, field) => {
  currentStepForDataFactory.value = step
  currentFieldForDataFactory.value = field
  showDataFactorySelector.value = true
}

const handleDataFactorySelect = (record) => {
  const step = currentStepForDataFactory.value
  const field = currentFieldForDataFactory.value
  
  if (record && record.output_data && step && field) {
    let valueToSet = ''
    
    if (typeof record.output_data === 'string') {
      valueToSet = record.output_data
    } else if (record.output_data.result) {
      valueToSet = record.output_data.result
    } else if (record.output_data.output_data) {
      valueToSet = record.output_data.output_data
    } else {
      valueToSet = JSON.stringify(record.output_data)
    }
    
    step[field] = valueToSet
    ElMessage.success(t('uiAutomation.testCase.messages.dataFactorySelected', { toolName: record.tool_name }))
  }
  
  showDataFactorySelector.value = false
}

const insertVariable = (variable) => {
  if (currentEditingStep.value && currentEditingField.value) {
    const example = variable.example
    const currentValue = currentEditingStep.value[currentEditingField.value] || ''
    
    // 简单起见，这里直接追加到末尾，或者如果为空则替换
    if (!currentValue) {
      currentEditingStep.value[currentEditingField.value] = example
    } else {
      currentEditingStep.value[currentEditingField.value] = currentValue + example
    }
    
    ElMessage.success(t('uiAutomation.testCase.messages.variableInserted', { name: variable.name }))
    showVariableHelper.value = false
  }
}

const saveTestCaseForm = async () => {
  if (!testCaseForm.name.trim()) {
    ElMessage.warning(t('uiAutomation.testCase.form.nameRequired'))
    return
  }

  try {
    const data = {
      name: testCaseForm.name,
      description: testCaseForm.description,
      priority: testCaseForm.priority,
      folder: testCaseForm.folder || null,
      project: projectId.value
    }

    if (editingTestCase.value) {
      // 编辑现有用例
      await updateTestCase(editingTestCase.value.id, data)
      ElMessage.success(t('uiAutomation.testCase.update.success'))

      // 更新本地数据
      const index = testCases.value.findIndex(tc => tc.id === editingTestCase.value.id)
      if (index !== -1) {
        testCases.value[index] = { ...testCases.value[index], ...data }
      }
      if (selectedTestCase.value?.id === editingTestCase.value.id) {
        selectedTestCase.value = { ...selectedTestCase.value, ...data }
      }
    } else {
      // 创建新用例
      const response = await createTestCase(data)
      ElMessage.success(t('uiAutomation.testCase.create.success'))
      testCases.value.push(response.data)
    }

    showCreateDialog.value = false
    editingTestCase.value = null
    resetForm()
  } catch (error) {
    console.error('保存测试用例失败:', error)
    ElMessage.error(t('uiAutomation.testCase.save.failed'))
  }
}

const resetForm = () => {
  testCaseForm.name = ''
  testCaseForm.description = ''
  testCaseForm.priority = 'medium'
  testCaseForm.folder = null
}

// 辅助方法
const getStatusTag = (status) => {
  const tagMap = {
    'draft': 'info',
    'ready': 'success',
    'running': 'warning',
    'passed': 'success',
    'failed': 'danger'
  }
  return tagMap[status] || 'info'
}

const getStatusText = (status) => {
  const textMap = {
    'draft': t('uiAutomation.testCase.status.draft'),
    'ready': t('uiAutomation.testCase.status.ready'),
    'running': t('uiAutomation.testCase.status.running'),
    'passed': t('uiAutomation.testCase.status.passed'),
    'failed': t('uiAutomation.testCase.status.failed')
  }
  return textMap[status] || t('uiAutomation.testCase.status.unknown')
}

const getActionTypeText = (actionType) => {
  const textMap = {
    'click': t('uiAutomation.testCase.actionType.click'),
    'fill': t('uiAutomation.testCase.actionType.fill'),
    'getText': t('uiAutomation.testCase.actionType.getText'),
    'waitFor': t('uiAutomation.testCase.actionType.waitFor'),
    'hover': t('uiAutomation.testCase.actionType.hover'),
    'scroll': t('uiAutomation.testCase.actionType.scroll'),
    'screenshot': t('uiAutomation.testCase.actionType.screenshot'),
    'assert': t('uiAutomation.testCase.actionType.assert'),
    'wait': t('uiAutomation.testCase.actionType.wait'),
    'navigate': t('uiAutomation.testCase.actionType.navigate')
  }
  return textMap[actionType] || actionType
}

const formatTime = (timestamp) => {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  return date.toLocaleString()
}

// 获取操作类型文本
const getActionText = (actionType) => {
  const actionMap = {
    'click': t('uiAutomation.testCase.actionText.click'),
    'fill': t('uiAutomation.testCase.actionText.fill'),
    'getText': t('uiAutomation.testCase.actionText.getText'),
    'waitFor': t('uiAutomation.testCase.actionText.waitFor'),
    'hover': t('uiAutomation.testCase.actionText.hover'),
    'scroll': t('uiAutomation.testCase.actionText.scroll'),
    'screenshot': t('uiAutomation.testCase.actionText.screenshot'),
    'assert': t('uiAutomation.testCase.actionText.assert'),
    'wait': t('uiAutomation.testCase.actionText.wait'),
    'navigate': t('uiAutomation.testCase.actionText.navigate')
  }
  return actionMap[actionType] || actionType
}

// 图片处理方法
const handleImageError = (event) => {
  const img = event.target
  const screenshotIndex = parseInt(img.dataset.index)
  if (executionResult.value && executionResult.value.screenshots) {
    executionResult.value.screenshots[screenshotIndex].error = true
    executionResult.value.screenshots[screenshotIndex].loaded = true
  }
}

const handleImageLoad = (event) => {
  const img = event.target
  const screenshotIndex = parseInt(img.dataset.index)
  if (executionResult.value && executionResult.value.screenshots) {
    executionResult.value.screenshots[screenshotIndex].loaded = true
    executionResult.value.screenshots[screenshotIndex].error = false
  }
}

const previewScreenshot = (screenshot) => {
  currentScreenshot.value = screenshot
  showScreenshotPreview.value = true
}

// 组件挂载
onMounted(async () => {
  console.log('TestCaseManager onMounted 开始执行...')
  await loadProjects()
  console.log('loadProjects 完成，准备加载变量函数...')
  await loadVariableFunctions()
  console.log('loadVariableFunctions 完成')

  if (projects.value.length > 0) {
    projectId.value = getPreferredProjectId(projects.value, projects.value[0].id)
    await onProjectChange()
  }
})
</script>

<style scoped>
.test-case-manager {
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
  gap: 16px;
  flex-wrap: wrap;
}

.page-title {
  margin: 0;
  font-size: 24px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.project-selector { width: 200px; }
.header-actions :deep(.el-button + .el-button) { margin-left: 0; }
.variable-count { margin-left: 6px; color: var(--el-color-primary); }
.variables-case-heading { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
.variables-case-heading > span { color: var(--el-text-color-secondary); flex-shrink: 0; }
.variables-case-heading strong { overflow-wrap: anywhere; }
.variables-dialog-body { max-height: 65vh; overflow-y: auto; padding-right: 4px; }
.storage-state-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.storage-state-dialog-body { padding-right: 0; }

.main-content {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.left-panel {
  width: 100%;
  border-right: 1px solid #e6e6e6;
  background: white;
  display: flex;
  flex-direction: column;
}

.panel-header {
  padding: 15px;
  border-bottom: 1px solid #e6e6e6;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.panel-header .case-search {
  flex: 1;
  min-width: 0;
  margin-right: 8px;
}

.panel-header .folder-create-button {
  width: 32px;
  height: 24px;
  padding: 0;
}

.panel-header .tree-toggle-button {
  width: 24px;
  height: 24px;
  padding: 0;
}

.panel-header .tree-toggle-button :deep(.el-icon) {
  font-size: 16px;
}

.panel-header .folder-create-button :deep(.el-icon) {
  font-size: 18px;
}

.test-case-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 6px;
}

.test-case-tree-node {
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
  min-height: 32px;
  padding: 3px 4px;
  border-radius: 4px;
  color: #303133;
}

.test-case-tree-node:hover,
.test-case-tree-node.active {
  background: #f0f8ff;
  color: #409eff;
}

.test-case-tree-content {
  flex: 1;
  min-width: 0;
  margin-left: 5px;
}

.test-case-tree-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 4px;
}

.test-case-tree-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-step-count {
  flex: 0 0 auto;
  color: #409eff;
  font-size: 11px;
}

.test-case-tree-node .case-actions {
  display: none;
  align-items: center;
  gap: 1px;
}

.test-case-tree-node:hover .case-actions {
  display: inline-flex;
}

.test-case-tree-node .case-actions :deep(.el-button) {
  width: 19px;
  height: 19px;
  padding: 0;
}

.folder-context-menu {
  position: fixed;
  z-index: 1000;
  min-width: 150px;
  margin: 0;
  padding: 8px 0;
  list-style: none;
  background: #fff;
  border: 1px solid #e9ecef;
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}

.folder-context-menu li {
  padding: 9px 14px;
  color: #303133;
  font-size: 13px;
  cursor: pointer;
}

.folder-context-menu li:hover { background: #f5f7fa; }
.folder-context-menu li.danger { color: #f56c6c; }

.test-case-item {
  border: 1px solid #e6e6e6;
  border-radius: 6px;
  margin-bottom: 10px;
  padding: 15px;
  cursor: pointer;
  transition: all 0.3s;
}

.test-case-item:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.1);
}

.test-case-item.active {
  border-color: #409eff;
  background-color: #f0f8ff;
}

.case-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
}

.case-info {
  flex: 1;
  min-width: 0;
}

.case-name {
  margin: 0 0 5px 0;
  font-size: 16px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.case-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
}

.case-actions :deep(.el-button) {
  width: 24px;
  height: 24px;
  padding: 0;
}

.case-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #888;
}

.step-count {
  color: #409eff;
  font-weight: 500;
}

.right-panel {
  flex: 1;
  min-width: 0;
  background: white;
  display: flex;
  flex-direction: column;
}

.test-case-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 20px;
  overflow: hidden;
  height: 100%;
}

.detail-header {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  margin-bottom: 20px;
  padding-bottom: 15px;
  border-bottom: 1px solid #e6e6e6;
  gap: 8px;
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detail-title-input {
  flex: 1;
  min-width: 0;
}

.detail-priority-select {
  width: 100px;
  flex: 0 0 auto;
}

.detail-description-input {
  width: 100%;
}

.detail-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}

.detail-actions :deep(.el-button + .el-button) { margin-left: 0; }

.steps-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  margin-bottom: 20px;
  border: 1px solid #e6e6e6;
  border-radius: 6px;
  background: #fafafa;
  overflow: hidden;
}

.steps-container.has-steps {
  max-height: 50%;
}

.steps-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.steps-header h4 {
  margin: 0;
}

.steps-list {
  padding: 10px;
  padding-bottom: 20px;
}

.steps-scroll-container {
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  padding: 10px;
  padding-right: 5px;
}

.steps-scroll-container::-webkit-scrollbar {
  width: 6px;
}

.steps-scroll-container::-webkit-scrollbar-track {
  background: #f5f5f5;
  border-radius: 3px;
}

.steps-scroll-container::-webkit-scrollbar-thumb {
  background: #ccc;
  border-radius: 3px;
}

.steps-scroll-container::-webkit-scrollbar-thumb:hover {
  background: #999;
}

.step-item {
  border: 1px solid #e6e6e6;
  border-radius: 6px;
  margin-bottom: 10px;
  background: white;
  transition: all 0.3s;
}

.step-item:hover {
  border-color: #409eff;
}

.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 15px;
  background: #fafafa;
  border-radius: 6px 6px 0 0;
}

.step-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.drag-handle {
  cursor: move;
  color: #999;
}

.step-number {
  background: #409eff;
  color: white;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: bold;
}

.step-right {
  display: flex;
  gap: 5px;
}

.step-content {
  padding: 15px;
  border-top: 1px solid #e6e6e6;
}

.step-param {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
  gap: 10px;
}

.step-param label {
  width: 120px;
  font-weight: 500;
  color: #333;
}

.execution-result {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid #e6e6e6;
  border-radius: 6px;
  background: white;
  overflow: hidden;
}

.execution-result.with-steps {
  margin-top: 0;
}

.execution-result .result-header {
  padding: 15px;
  border-bottom: 1px solid #e6e6e6;
  background: #fafafa;
  border-radius: 6px 6px 0 0;
}

.execution-result .result-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 15px;
}

.result-content {
  flex: 1;
  overflow: hidden;
}

/* 为el-tabs和el-tab-pane添加flex布局支持 */
.result-content :deep(.el-tabs) {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.result-content :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.result-content :deep(.el-tab-pane) {
  height: 100%;
  overflow: auto;
}

/* .result-header 已在 .execution-result 中定义 */

.result-header h4 {
  margin: 0;
}

.logs-container {
  max-height: 500px;
  overflow-y: auto;
  background: #f5f7fa;
  padding: 15px;
  border-radius: 4px;
}

.log-item {
  margin-bottom: 15px;
  padding: 12px;
  background: white;
  border-radius: 4px;
  border-left: 3px solid #409eff;
}

.log-item:last-child {
  margin-bottom: 0;
}

.log-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.log-action {
  font-weight: 500;
  color: #606266;
}

.log-desc {
  color: #909399;
  font-size: 14px;
}

.log-error {
  display: flex;
  align-items: flex-start;  /* 改为 flex-start，适配多行文本 */
  gap: 8px;
  color: #f56c6c;
  background: #fef0f0;
  padding: 8px 12px;
  border-radius: 4px;
  margin-top: 8px;
  font-size: 14px;

  .error-message {
    margin: 0;
    padding: 0;
    font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;  /* 保留换行符和空格 */
    word-break: break-word;  /* 长单词换行 */
    flex: 1;
  }

  .el-icon {
    margin-top: 2px;  /* 图标与文本顶部对齐 */
    flex-shrink: 0;  /* 图标不缩小 */
  }
}

.screenshots-container {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
  padding: 10px;
}

.screenshot-item {
  display: flex;
  flex-direction: column;
  cursor: pointer;
  transition: transform 0.2s ease;
}

.screenshot-item:hover {
  transform: translateY(-4px);
}

.screenshot-wrapper {
  position: relative;
  width: 100%;
  min-height: 200px;
  background: #f5f5f5;
  border-radius: 8px;
  border: 2px solid #e6e6e6;
  overflow: hidden;
  transition: border-color 0.3s ease;
}

.screenshot-item:hover .screenshot-wrapper {
  border-color: #409eff;
}

.screenshot-wrapper img {
  width: 100%;
  height: auto;
  display: block;
  transition: opacity 0.3s ease;
}

.screenshot-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.3s ease;
}

.screenshot-item:hover .screenshot-overlay {
  opacity: 1;
}

.zoom-icon {
  font-size: 48px;
  color: white;
}

.screenshot-placeholder,
.screenshot-error {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  color: #999;
  font-size: 14px;
}

.screenshot-placeholder .el-icon,
.screenshot-error .el-icon {
  font-size: 32px;
  margin-bottom: 8px;
}

.screenshot-error {
  color: #f56c6c;
}

.screenshot-info {
  margin-top: 10px;
}

.screenshot-description {
  margin: 0 0 5px 0;
  font-size: 14px;
  font-weight: 500;
  color: #333;
  text-align: left;
}

.screenshot-meta {
  margin: 0 0 3px 0;
  font-size: 12px;
  color: #666;
  text-align: left;
}

.screenshot-time {
  margin: 0;
  font-size: 11px;
  color: #999;
  text-align: left;
}

/* 截图预览对话框样式 */
.screenshot-preview {
  display: flex;
  flex-direction: column;
}

.preview-info {
  margin-bottom: 20px;
  padding: 15px;
  background: #f5f7fa;
  border-radius: 6px;
}

.preview-info h4 {
  margin: 0 0 10px 0;
  font-size: 16px;
  color: #333;
}

.preview-info p {
  margin: 5px 0;
  font-size: 14px;
  color: #666;
}

.preview-image {
  display: flex;
  justify-content: center;
  align-items: center;
  background: #f5f5f5;
  border-radius: 8px;
  padding: 20px;
  max-height: 70vh;
  overflow: auto;
}

.preview-image img {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.errors-container {
  padding: 10px;
  height: 100%;
  overflow-y: auto;
}

.error-item {
  background: #fff;
  border: 2px solid #f56c6c;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 15px;
}

.error-item:last-child {
  margin-bottom: 0;
}

.error-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 15px;
  padding-bottom: 15px;
  border-bottom: 1px solid #f5f5f5;
}

.error-header .el-tag {
  font-size: 16px;
  padding: 10px 15px;
  font-weight: 600;
}

.error-header .el-icon {
  margin-right: 5px;
}

.error-step {
  background: #fef0f0;
  color: #f56c6c;
  padding: 5px 12px;
  border-radius: 4px;
  font-weight: 600;
  font-size: 14px;
}

.error-meta {
  background: #f9f9f9;
  padding: 15px;
  border-radius: 6px;
  margin-bottom: 15px;
}

.meta-item {
  display: flex;
  align-items: flex-start;
  margin-bottom: 8px;
}

.meta-item:last-child {
  margin-bottom: 0;
}

.meta-label {
  font-weight: 600;
  color: #606266;
  min-width: 80px;
  margin-right: 10px;
}

.meta-value {
  color: #303133;
  flex: 1;
}

.error-details {
  background: #2d2d2d;
  border-radius: 6px;
  overflow: hidden;
}

.details-header {
  background: #1e1e1e;
  color: #fff;
  padding: 10px 15px;
  font-weight: 600;
  font-size: 14px;
  border-bottom: 1px solid #3d3d3d;
}

.details-content {
  color: #ff6b6b;
  padding: 15px;
  margin: 0;
  font-family: 'Courier New', Courier, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-wrap: break-word;
  max-height: 400px;
  overflow-y: auto;
}

.details-content::-webkit-scrollbar {
  width: 6px;
}

.details-content::-webkit-scrollbar-track {
  background: #1e1e1e;
}

.details-content::-webkit-scrollbar-thumb {
  background: #555;
  border-radius: 3px;
}

.details-content::-webkit-scrollbar-thumb:hover {
  background: #777;
}

.no-selection {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.data-factory-btn {
  background-color: #409eff !important;
  border-color: #409eff !important;
  color: white !important;
}

.data-factory-btn:hover {
  background-color: #66b1ff !important;
  border-color: #66b1ff !important;
}

.variable-helper-btn {
  background-color: #67c23a;
  border-color: #67c23a;
  color: white;
}

.variable-helper-btn:hover {
  background-color: #5daf34;
  border-color: #5daf34;
}
</style>
