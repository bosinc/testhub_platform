<template>
  <div class="interface-management">
    <div class="interface-layout">
      <ResizableSplitPane v-model="sidebarWidth" :min-size="240" :max-size="520" :second-min-size="480" storage-key="api-testing.interfaces.sidebar-width">
        <template #first>
      <!-- 左侧集合树 -->
          <div class="sidebar">
        <div class="sidebar-header">
          <el-select v-model="selectedProject" :placeholder="$t('apiTesting.common.selectProject')" @change="onProjectChange" style="width: 100%;">
            <el-option
              v-for="project in projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
          <div class="header-actions">
            <el-input
              v-model="searchKeyword"
              :placeholder="$t('apiTesting.interface.searchInterface')"
              size="small"
              clearable
              style="flex: 1; min-width: 0;"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <div class="interface-header-actions">
              <el-button type="primary" size="small" @click="openCreateCollectionDialog" :title="$t('apiTesting.interface.createCollection')">
                <el-icon><Folder /></el-icon>
              </el-button>
              <el-button type="success" size="small" @click="createEmptyRequest" :title="$t('apiTesting.interface.addInterface')">
                <el-icon><Plus /></el-icon>
              </el-button>
              <el-button size="small" @click="openBatchImport" :title="$t('apiTesting.interface.importBatch.importTitle')">
                <el-icon><Upload /></el-icon>
                {{ $t('apiTesting.common.import') }}
              </el-button>
            </div>
          </div>
        </div>

        <div class="collection-tree">
          <el-tree
            ref="treeRef"
            :data="visibleCollections"
            :empty-text="searchKeyword.trim() ? $t('apiTesting.interface.noSearchResults') : $t('apiTesting.common.noData')"
            :props="treeProps"
            node-key="treeKey"
            :default-expanded-keys="visibleExpandedKeys"
            :expand-on-click-node="false"
            :draggable="!searchKeyword.trim()"
            :allow-drag="allowDrag"
            :allow-drop="allowDrop"
            @node-click="onNodeClick"
            @node-contextmenu="onNodeRightClick"
            @node-expand="onNodeExpand"
            @node-collapse="onNodeCollapse"
            @node-drop="handleDrop"
          >
            <template #default="{ node, data }">
              <div class="tree-node">
                <el-icon v-if="data.type === 'collection'">
                  <Folder />
                </el-icon>
                <el-icon v-else>
                  <Document />
                </el-icon>

                <!-- 集合名称编辑 -->
                <div v-if="data.type === 'collection' && editingNodeId === data.id" class="node-edit">
                  <el-input
                    v-model="editingNodeName"
                    size="small"
                    @blur="saveCollectionName"
                    @keyup.enter="saveCollectionName"
                    @keyup.esc="cancelEdit"
                    ref="editInputRef"
                  />
                </div>

                <!-- 普通显示模式 -->
                <span v-else class="node-label" :title="node.label">{{ node.label }}</span>

                <span v-if="data.type === 'request' && data.request_type !== 'WEBSOCKET'" class="method-tag" :class="(data.method || 'GET').toLowerCase()">
                  {{ data.method || 'GET' }}
                </span>

                <!-- 悬浮操作按钮：复制（仅接口）、删除 -->
                <div class="node-actions" @click.stop>
                  <el-tooltip v-if="data.type === 'request'" :content="$t('apiTesting.interface.contextMenu.copy')" placement="top">
                    <el-button link size="small" :icon="CopyDocument" @click.stop="copyRequest(data)" />
                  </el-tooltip>
                  <el-tooltip :content="$t('apiTesting.interface.contextMenu.delete')" placement="top">
                    <el-button link size="small" :icon="Delete" @click.stop="deleteNode(data)" />
                  </el-tooltip>
                </div>
              </div>
            </template>
          </el-tree>
        </div>
          </div>
        </template>

        <template #second>
          <!-- 右侧请求详情 -->
          <div class="main-content">
        <div v-if="!selectedRequest" class="empty-state">
          <el-empty :description="$t('apiTesting.interface.selectInterface')">
            <el-button type="primary" @click="createEmptyRequest">{{ $t('apiTesting.interface.createNewInterface') }}</el-button>
          </el-empty>
        </div>

        <div v-else class="request-detail request-editor-card">
          <!-- 请求基本信息 -->
          <RequestEditor
            ref="requestEditorRef"
            :key="requestEditorKey"
            v-model="requestDraft"
            :environment="currentEnvironment"
            :extraction-results="response?.extraction_results || []"
            :busy="sending || saving"
            :ws-status="websocketConnectionStatus"
            @send="sendRequest"
            @preview="confirmUrlPreview"
            @ws-connect="toggleWebSocketConnection"
            @ws-disconnect="disconnectWebSocket"
            @ws-send="sendWebSocketMessage"
            @data-factory="openEditorDataFactory"
            @variable-helper="openEditorVariableHelper"
          >
            <template #environment>
              <el-select v-model="selectedEnvironment" :placeholder="$t('apiTesting.interface.environment')" class="env-select">
                <el-option :label="$t('apiTesting.common.noEnvironment')" value="" />
                <el-option
                  v-for="env in environments"
                  :key="env.id"
                  :label="env.name"
                  :value="env.id"
                />
              </el-select>
            </template>
            <template #meta>
            <div class="request-meta">
              <el-input
                v-model="selectedRequest.name"
                :placeholder="$t('apiTesting.interface.requestName')"
                size="small"
                class="name-input"
              />
              <div class="action-buttons">
                <el-button size="small" @click="saveRequest" :loading="saving" ref="saveButtonRef">
                  {{ $t('apiTesting.common.save') }}
                </el-button>
                <el-dropdown split-button type="default" size="small" @click="importCurl">
                  {{ $t('apiTesting.common.import') }}
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item @click="importCurl">{{ $t('apiTesting.interface.importCurl') }}</el-dropdown-item>
                      <el-dropdown-item @click="exportRequest">{{ $t('apiTesting.interface.exportCurl') }}</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
                <el-dropdown split-button type="default" size="small" @click="generateCode('javascript')">
                  {{ $t('apiTesting.interface.generateCode') }}
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item @click="generateCode('javascript')">JavaScript</el-dropdown-item>
                      <el-dropdown-item @click="generateCode('python')">Python</el-dropdown-item>
                      <el-dropdown-item @click="generateCode('java')">Java</el-dropdown-item>
                      <el-dropdown-item @click="generateCode('curl')">cURL</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </div>
            </template>
            <template #ws-message="{ send }">
                <div class="message-container">
                  <div class="message-input-section">
                    <el-select
                      v-model="websocketMessageType"
                      :placeholder="$t('apiTesting.interface.messageType')"
                      style="width: 150px; margin-bottom: 15px;"
                    >
                      <el-option label="Text" value="text" />
                      <el-option label="JSON" value="json" />
                      <el-option label="Binary" value="binary" />
                    </el-select>

                    <div v-if="websocketMessageType === 'text' || websocketMessageType === 'json'">
                      <el-input
                        v-model="websocketMessageContent"
                        type="textarea"
                        :rows="6"
                        :placeholder="$t('apiTesting.interface.inputWebSocketMessage')"
                      />
                    </div>

                    <div v-else-if="websocketMessageType === 'binary'">
                      <el-upload
                        drag
                        action="#"
                        :auto-upload="false"
                        :show-file-list="false"
                        :on-change="handleWebSocketFileUpload"
                      >
                        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
                        <div class="el-upload__text">
                          {{ $t('apiTesting.interface.dragBinaryFile') }}<em>{{ $t('apiTesting.interface.clickUpload') }}</em>
                        </div>
                      </el-upload>
                      <div v-if="websocketBinaryFile" class="uploaded-file">
                        <span>{{ websocketBinaryFile.name }}</span>
                        <el-button size="small" type="danger" @click="clearWebSocketBinaryFile">{{ $t('apiTesting.interface.clear') }}</el-button>
                      </div>
                    </div>

                    <div class="message-actions" style="margin-top: 15px;">
                      <el-button type="primary" @click="send">
                        {{ $t('apiTesting.interface.sendMessage') }}
                      </el-button>
                      <el-button @click="clearWebSocketMessage">
                        {{ $t('apiTesting.interface.clearMessage') }}
                      </el-button>
                    </div>
                  </div>

                  <!-- WebSocket消息历史记录 -->
                  <div class="websocket-response-section" v-if="websocketMessages.length > 0">
                    <h3>{{ $t('apiTesting.interface.messageHistory') }}</h3>
                    <div class="websocket-messages">
                      <div
                        v-for="(msg, index) in websocketMessages.slice().reverse()"
                        :key="index"
                        class="websocket-message-item"
                        :class="msg.type"
                      >
                        <div class="message-header">
                          <span class="message-type" :class="msg.type">
                            {{ msg.type === 'sent' ? $t('apiTesting.interface.messageSent') :
                               msg.type === 'connected' ? $t('apiTesting.interface.messageConnected') :
                               msg.type === 'info' ? $t('apiTesting.interface.messageInfo') :
                               msg.type === 'error' ? $t('apiTesting.interface.messageError') : $t('apiTesting.interface.messageReceived') }}
                          </span>
                          <span class="message-time">{{ msg.timestamp }}</span>
                        </div>
                        <div class="message-content">
                          <pre v-if="msg.type === 'received' && isJsonString(msg.content)">{{ formatJson(msg.content) }}</pre>
                          <pre v-else>{{ msg.content }}</pre>
                        </div>
                      </div>
                    </div>
                    <div class="message-actions">
                      <el-button size="small" @click="clearWebSocketMessages">{{ $t('apiTesting.interface.clearHistory') }}</el-button>
                    </div>
                  </div>
                </div>
            </template>
          </RequestEditor>

          <!-- 响应区域 -->
          <ResponseViewer :response="response" />
        </div>
          </div>
        </template>
      </ResizableSplitPane>
    </div>

    <!-- 创建集合对话框 -->
    <el-dialog v-model="showCreateCollectionDialog" :title="$t('apiTesting.interface.createCollection')" :close-on-click-modal="false" :close-on-press-escape="false" :modal="true" :destroy-on-close="false" width="500px">
      <el-form ref="collectionFormRef" :model="collectionForm" :rules="collectionRules" label-width="100px">
        <el-form-item :label="$t('apiTesting.interface.collectionName')" prop="name">
          <el-input v-model="collectionForm.name" :placeholder="$t('apiTesting.interface.inputCollectionName')" />
        </el-form-item>
        <el-form-item :label="$t('apiTesting.common.description')" prop="description">
          <el-input v-model="collectionForm.description" type="textarea" :rows="3" :placeholder="`${$t('apiTesting.common.pleaseInput')}${$t('apiTesting.common.description')}`" />
        </el-form-item>
        <el-form-item :label="$t('apiTesting.interface.parentCollection')" prop="parent">
          <el-select v-model="collectionForm.parent" :placeholder="$t('apiTesting.interface.selectParentCollection')" clearable>
            <el-option
              v-for="collection in flatCollections"
              :key="collection.id"
              :label="collection.name"
              :value="collection.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="closeCreateCollectionDialog">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" @click="createCollection">{{ $t('apiTesting.common.create') }}</el-button>
      </template>
    </el-dialog>

    <!-- 编辑集合对话框 -->
    <el-dialog v-model="showEditCollectionDialog" :title="$t('apiTesting.interface.editCollection')" :close-on-click-modal="false" width="500px">
      <el-form ref="editCollectionFormRef" :model="editCollectionForm" :rules="collectionRules" label-width="100px">
        <el-form-item :label="$t('apiTesting.interface.collectionName')" prop="name">
          <el-input v-model="editCollectionForm.name" :placeholder="$t('apiTesting.interface.inputCollectionName')" />
        </el-form-item>
        <el-form-item :label="$t('apiTesting.common.description')" prop="description">
          <el-input v-model="editCollectionForm.description" type="textarea" :rows="3" :placeholder="`${$t('apiTesting.common.pleaseInput')}${$t('apiTesting.common.description')}`" />
        </el-form-item>
        <el-form-item :label="$t('apiTesting.interface.parentCollection')" prop="parent">
          <el-select v-model="editCollectionForm.parent" :placeholder="$t('apiTesting.interface.selectParentCollection')" clearable>
            <el-option
              v-for="collection in flatCollections.filter(c => c.id !== editCollectionForm.id)"
              :key="collection.id"
              :label="collection.name"
              :value="collection.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="closeEditCollectionDialog">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" @click="updateCollection">{{ $t('apiTesting.common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 右键菜单 -->
    <ul
      ref="contextMenuRef"
      v-show="showContextMenu"
      class="context-menu"
      :style="{
        left: contextMenuX + 'px',
        top: contextMenuY + 'px',
        visibility: contextMenuReady ? 'visible' : 'hidden'
      }"
    >
      <li @click="addRequest">{{ $t('apiTesting.interface.contextMenu.addRequest') }}</li>
      <li @click="addCollection">{{ $t('apiTesting.interface.contextMenu.addSubCollection') }}</li>
      <li v-if="rightClickedNode?.type === 'collection'" @click="openCollectionImport">{{ $t('apiTesting.interface.contextMenu.import') }}</li>
      <li v-if="rightClickedNode?.type === 'collection'" @click="exportCollection">{{ $t('apiTesting.interface.contextMenu.export') }}</li>
      <li @click="editNode">{{ $t('apiTesting.interface.contextMenu.edit') }}</li>
      <li @click="deleteNode">{{ $t('apiTesting.interface.contextMenu.delete') }}</li>
    </ul>

    <!-- 数据工厂选择器 -->
    <DataFactorySelector
      v-model="showDataFactorySelector"
      @select="handleDataFactorySelect"
    />

    <!-- 变量助手对话框 -->
    <el-dialog
      :close-on-press-escape="false"
      :modal="true"
      :destroy-on-close="false"
      v-model="showVariableHelper"
      :title="$t('apiTesting.interface.variableHelper') + ' ' + $t('apiTesting.interface.clickToInsert')"
      :close-on-click-modal="false"
      width="900px"
    >
      <div v-if="variableCategories.length === 0" style="padding: 20px; text-align: center; color: #999;">
        <p>{{ $t('apiTesting.interface.variableCategoriesLoading') }}</p>
        <p>{{ $t('apiTesting.interface.variableCategoriesCount', { count: variableCategories.length }) }}</p>
      </div>
      <el-tabs v-else tab-position="left" style="height: 450px">
        <el-tab-pane
          v-for="(category, index) in variableCategories"
          :key="index"
          :label="category.label"
        >
          <div style="height: 450px; overflow-y: auto; padding: 10px;">
            <el-table :data="category.variables" style="width: 100%" @row-click="insertVariable" highlight-current-row>
              <el-table-column prop="name" :label="$t('apiTesting.interface.functionName')" width="150">
                <template #default="{ row }">
                  <el-tag size="small">{{ row.name }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="desc" :label="$t('apiTesting.interface.description')" min-width="150" />
              <el-table-column prop="syntax" :label="$t('apiTesting.interface.syntax')" min-width="200" show-overflow-tooltip />
              <el-table-column prop="example" :label="$t('apiTesting.interface.example')" min-width="200" show-overflow-tooltip />
              <el-table-column :label="$t('apiTesting.interface.operation')" width="80" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" size="small">{{ $t('apiTesting.interface.insert') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>

    <!-- CURL导入对话框 -->
    <el-dialog
      v-model="showCurlImportDialog"
      :title="$t('apiTesting.interface.importCurlCommand')"
      width="800px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="curlCommand"
        type="textarea"
        :rows="15"
        :placeholder="$t('apiTesting.interface.pasteCurlCommand')"
      />
      <div v-if="curlPreview" class="curl-preview">
        <p>{{ $t('apiTesting.requestEditor.hostSuggestion') }} <code>{{ curlPreview.model.baseURL }}</code></p>
        <p>{{ $t('apiTesting.requestEditor.importPath') }} <code>{{ curlPreview.draft.url }}</code></p>
        <p>{{ $t('apiTesting.requestEditor.curlPreviewHint') }}</p>
        <pre>{{ JSON.stringify(curlPreview.draft, null, 2) }}</pre>
      </div>
      <template #footer>
        <el-button @click="closeCurlImportDialog">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button @click="parseAndImportCurl">{{ $t('apiTesting.requestEditor.preview') }}</el-button>
        <el-button v-if="curlPreview" type="primary" @click="applyCurlPreview">{{ $t('apiTesting.interface.parseAndImport') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showBatchImportDialog"
      :title="$t('apiTesting.interface.importBatch.title')"
      width="680px"
      :close-on-click-modal="false"
      destroy-on-close
      @closed="resetBatchImport"
    >
      <template v-if="!importPreview">
        <el-form label-width="76px">
          <el-form-item :label="$t('apiTesting.interface.importBatch.format')">
            <el-radio-group v-model="importFormat" size="small">
              <el-radio-button value="openapi">{{ $t('apiTesting.interface.importBatch.openapi') }}</el-radio-button>
              <el-radio-button value="postman">{{ $t('apiTesting.interface.importBatch.postman') }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="$t('apiTesting.interface.importBatch.file')">
            <el-upload
              ref="batchImportUploadRef"
              drag
              :auto-upload="false"
              :limit="1"
              accept=".json,.yaml,.yml"
              :on-change="onBatchImportFileChange"
              :on-remove="onBatchImportFileRemove"
            >
              <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
              <div class="el-upload__text">{{ $t('apiTesting.interface.importBatch.dropFile') }}<em>{{ $t('apiTesting.interface.importBatch.clickUpload') }}</em></div>
              <template #tip>
                <div class="el-upload__tip">{{ $t('apiTesting.interface.importBatch.fileTip') }}</div>
              </template>
            </el-upload>
          </el-form-item>
          <el-form-item :label="$t('apiTesting.interface.importBatch.targetProject')">
            <el-select v-model="importProjectId" :placeholder="$t('apiTesting.interface.importBatch.selectProject')" :disabled="Boolean(importCollectionId)" style="width: 100%">
              <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="importCollectionId" :label="$t('apiTesting.interface.importBatch.targetCollection')">
            <el-input :model-value="importTargetCollectionName" readonly />
          </el-form-item>
        </el-form>
      </template>
      <template v-else>
        <div class="import-preview-summary">
          <el-icon><InfoFilled /></el-icon>
          <span>{{ $t('apiTesting.interface.importBatch.parsed', { count: importPreview.total }) }}</span>
          <el-button link type="primary" @click="returnToImportFile">{{ $t('apiTesting.interface.importBatch.reselect') }}</el-button>
        </div>
        <div class="import-preview-list">
          <section v-for="group in importPreview.groups" :key="group.name" class="import-preview-group">
            <header>
              <el-checkbox
                :model-value="isImportGroupSelected(group)"
                :indeterminate="isImportGroupIndeterminate(group)"
                @change="toggleImportGroup(group, $event)"
              >
                {{ group.name }}
              </el-checkbox>
              <span class="import-group-count">{{ group.items.length }}</span>
            </header>
            <el-checkbox-group v-model="selectedImportIds">
              <div v-for="item in group.items" :key="item.id" class="import-preview-item">
                <el-checkbox :value="item.id" />
                <el-tag size="small" type="warning" effect="plain">{{ item.method }}</el-tag>
                <span class="import-item-name" :title="importItemName(item)">{{ importItemName(item) }}</span>
                <span class="import-item-url" :title="importItemPath(item) || $t('apiTesting.interface.importBatch.urlMissing')">
                  {{ importItemPath(item) || $t('apiTesting.interface.importBatch.urlMissing') }}
                </span>
              </div>
            </el-checkbox-group>
          </section>
        </div>
        <div class="import-selection-footer">
          <el-checkbox :model-value="allImportSelected" @change="toggleAllImportItems">{{ $t('apiTesting.interface.importBatch.selectAll') }}</el-checkbox>
          <span>{{ $t('apiTesting.interface.importBatch.selected', { selected: selectedImportIds.length, total: importPreview.total }) }}</span>
        </div>
      </template>
      <template #footer>
        <el-button @click="showBatchImportDialog = false">{{ $t('apiTesting.interface.cancel') }}</el-button>
        <el-button v-if="!importPreview" type="primary" :loading="importLoading" :disabled="!importFile || !importProjectId" @click="parseBatchImport">{{ $t('apiTesting.interface.importBatch.preview') }}</el-button>
        <el-button v-else type="primary" :loading="importLoading" :disabled="!selectedImportIds.length" @click="submitBatchImport">{{ $t('apiTesting.interface.importBatch.importSelected') }}</el-button>
      </template>
    </el-dialog>

    <!-- 代码生成对话框 -->
    <el-dialog
      v-model="showCodeGenerateDialog"
      :title="$t('apiTesting.interface.generateCode')"
      width="900px"
      :close-on-click-modal="false"
    >
      <el-select v-model="codeLanguage" :placeholder="$t('apiTesting.interface.selectLanguage')" style="width: 150px; margin-bottom: 10px" @change="generateCode(codeLanguage)">
        <el-option label="JavaScript" value="javascript" />
        <el-option label="Python" value="python" />
        <el-option label="Java" value="java" />
        <el-option label="Node.js" value="node" />
        <el-option label="cURL" value="curl" />
        <el-option label="PHP" value="php" />
        <el-option label="Go" value="go" />
        <el-option label="C#" value="csharp" />
        <el-option label="Ruby" value="ruby" />
        <el-option label="Swift" value="swift" />
        <el-option label="Kotlin" value="kotlin" />
        <el-option label="Rust" value="rust" />
        <el-option label="Dart" value="dart" />
        <el-option label="Objective-C" value="objc" />
        <el-option label="PowerShell" value="powershell" />
        <el-option label="MATLAB" value="matlab" />
        <el-option label="R" value="r" />
        <el-option label="Ansible" value="ansible" />
        <el-option label="C" value="c" />
        <el-option label="CFML" value="cfml" />
        <el-option label="Clojure" value="clojure" />
        <el-option label="Elixir" value="elixir" />
        <el-option label="HTTP" value="http" />
        <el-option label="HTTPie" value="httpie" />
        <el-option label="Julia" value="julia" />
        <el-option label="Lua" value="lua" />
        <el-option label="OCaml" value="ocaml" />
        <el-option label="Perl" value="perl" />
        <el-option label="Wget" value="wget" />
      </el-select>
      <el-input
        v-model="generatedCode"
        type="textarea"
        :rows="20"
        readonly
        class="code-generate"
      />
      <template #footer>
        <el-button @click="closeCodeGenerateDialog">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" @click="copyGeneratedCode">{{ $t('apiTesting.common.copy') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Folder, Document, Search, CopyDocument, Delete, Upload, UploadFilled, InfoFilled } from '@element-plus/icons-vue'
import api from '@/utils/api'
import RequestEditor from '@/components/api-testing/RequestEditor.vue'
import ResponseViewer from '@/components/api-testing/ResponseViewer.vue'
import { cloneDraft, createRequestDraft, serializeRequestDraft, previewRequestUrl, toCodegenModel, curlModelToDraft } from '@/utils/requestDraft'
import DataFactorySelector from '@/components/DataFactorySelector.vue'
import { RequestModelParser } from '@/utils/requestModel'
import { getVariableFunctions } from '@/api/data-factory'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'
import { CodeGenerator } from '@/utils/codeGenerator'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const requestErrorMessage = error => error.message?.startsWith('apiTesting.requestEditor.') ? t(error.message) : error.message
const sidebarWidth = ref(300)

const treeRef = ref(null)
const expandedKeys = ref([])
const projects = ref([])
const selectedProject = ref(null)
useRememberedProject(selectedProject, projects)
const collections = ref([])
const flatCollections = ref([])
const environments = ref([])
const selectedEnvironment = ref('')
const selectedRequest = ref(null)
const response = ref(null)
const sending = ref(false)
const saving = ref(false)
const requestEditorRef = ref(null)
const requestEditorKey = ref(0)
let requestSelectionVersion = 0
const currentEnvironment = computed(() => environments.value.find(env => String(env.id) === String(selectedEnvironment.value)) || null)
const requestDraft = computed({
  get: () => createRequestDraft(selectedRequest.value || {}),
  set: draft => { selectedRequest.value = { ...selectedRequest.value, ...draft } }
})
const showCreateCollectionDialog = ref(false)
const showEditCollectionDialog = ref(false)
const showContextMenu = ref(false)
const contextMenuX = ref(0)
const contextMenuY = ref(0)
const contextMenuRef = ref(null)
const contextMenuReady = ref(false)
const rightClickedNode = ref(null)
const editingNodeId = ref(null)
const editingNodeName = ref('')
const editInputRef = ref(null)

const searchKeyword = ref('')
const treeProps = {
  children: 'children',
  label: 'name'
}

const visibleCollections = computed(() => {
  const keyword = searchKeyword.value.trim().toLocaleLowerCase()
  if (!keyword) return collections.value

  const filterNodes = (nodes) => {
    const matches = []
    nodes.forEach(node => {
      if (node.type === 'request') {
        const isMatch = ['name', 'method', 'url'].some(field =>
          String(node[field] || '').toLocaleLowerCase().includes(keyword)
        )
        if (isMatch) matches.push(node)
        return
      }

      const children = filterNodes(node.children || [])
      if (children.length) matches.push({ ...node, children })
    })
    return matches
  }

  return filterNodes(collections.value)
})

const visibleExpandedKeys = computed(() => {
  if (!searchKeyword.value.trim()) return expandedKeys.value

  const keys = []
  const collectExpandedKeys = (nodes) => {
    nodes.forEach(node => {
      if (node.type !== 'collection') return
      if (node.children?.length) keys.push(node.treeKey)
      collectExpandedKeys(node.children || [])
    })
  }
  collectExpandedKeys(visibleCollections.value)
  return keys
})

// 数据工厂选择器相关
const showDataFactorySelector = ref(false)
const showVariableHelper = ref(false)
const helperTarget = ref(null)
const variableCategories = ref([])
const loading = ref(false)

// CURL导入相关
const showCurlImportDialog = ref(false)
const curlCommand = ref('')
const curlPreview = ref(null)
const applyCurlPreview = () => {
  if (!curlPreview.value) return
  if (!selectedRequest.value) createEmptyRequest()
  if (!selectedRequest.value) return
  requestDraft.value = curlPreview.value.draft
  showCurlImportDialog.value = false
  ElMessage.success(t('apiTesting.interface.imported'))
}

// Swagger / Postman 批量导入
const showBatchImportDialog = ref(false)
const batchImportUploadRef = ref(null)
const importFormat = ref('openapi')
const importFile = ref(null)
const importProjectId = ref(null)
const importCollectionId = ref(null)
const importPreview = ref(null)
const selectedImportIds = ref([])
const importLoading = ref(false)
const allImportIds = computed(() => importPreview.value?.groups.flatMap(group => group.items.map(item => item.id)) || [])
const allImportSelected = computed(() => allImportIds.value.length > 0 && selectedImportIds.value.length === allImportIds.value.length)
const importItemPath = (item) => {
  const path = item.path || item.url || ''
  if (!path) return ''
  try {
    return new URL(path, window.location.origin).pathname.replace(/%7B/gi, '{').replace(/%7D/gi, '}')
  } catch {
    return path.split(/[?#]/, 1)[0]
  }
}
const importItemName = (item) => item.name || `${item.method || ''} ${importItemPath(item)}`.trim()
const importTargetCollectionName = computed(() => {
  const collection = flatCollections.value.find(item => String(item.id) === String(importCollectionId.value))
  return collection?.name || ''
})

const openBatchImport = (targetCollection = null) => {
  importProjectId.value = selectedProject.value
  importCollectionId.value = targetCollection?.id || null
  showBatchImportDialog.value = true
}

const openCollectionImport = () => {
  if (rightClickedNode.value?.type !== 'collection') return
  openBatchImport(rightClickedNode.value)
  showContextMenu.value = false
}

const onBatchImportFileChange = (file) => {
  importFile.value = file.raw
}

const onBatchImportFileRemove = () => {
  importFile.value = null
}

const resetBatchImport = () => {
  importFile.value = null
  importCollectionId.value = null
  importPreview.value = null
  selectedImportIds.value = []
  importLoading.value = false
  batchImportUploadRef.value?.clearFiles()
}

const returnToImportFile = () => {
  importPreview.value = null
  selectedImportIds.value = []
}

const isImportGroupSelected = (group) => group.items.length > 0 && group.items.every(item => selectedImportIds.value.includes(item.id))
const isImportGroupIndeterminate = (group) => {
  const selected = group.items.filter(item => selectedImportIds.value.includes(item.id)).length
  return selected > 0 && selected < group.items.length
}

const toggleImportGroup = (group, checked) => {
  const ids = new Set(selectedImportIds.value)
  group.items.forEach(item => checked ? ids.add(item.id) : ids.delete(item.id))
  selectedImportIds.value = [...ids]
}

const toggleAllImportItems = (checked) => {
  selectedImportIds.value = checked ? [...allImportIds.value] : []
}

const createImportFormData = (includeSelection = false) => {
  const formData = new FormData()
  formData.append('file', importFile.value)
  formData.append('format', importFormat.value)
  if (includeSelection) {
    formData.append('project', importProjectId.value)
    if (importCollectionId.value) formData.append('collection', importCollectionId.value)
    selectedImportIds.value.forEach(id => formData.append('selected_ids', id))
  }
  return formData
}

const postImportFile = (url, includeSelection = false) => api.request({
  method: 'POST',
  url,
  data: createImportFormData(includeSelection),
  headers: { 'Content-Type': 'multipart/form-data' },
})

const parseBatchImport = async () => {
  if (!importFile.value || !importProjectId.value) return
  importLoading.value = true
  try {
    const response = await postImportFile('/api-testing/requests/import-preview/')
    importPreview.value = response.data
    selectedImportIds.value = response.data.groups.flatMap(group => group.items.map(item => item.id))
  } catch (error) {
    const message = error.response?.status === 405
      ? t('apiTesting.interface.importBatch.methodFailed')
      : error.response?.data?.error || t('apiTesting.interface.importBatch.parseFailed')
    ElMessage.error(message)
  } finally {
    importLoading.value = false
  }
}

const submitBatchImport = async () => {
  importLoading.value = true
  try {
    const response = await postImportFile('/api-testing/requests/import-batch/', true)
    ElMessage.success(t('apiTesting.interface.importBatch.imported', { count: response.data.imported }))
    showBatchImportDialog.value = false
    if (Number(importProjectId.value) === Number(selectedProject.value)) {
      await loadCollections(selectedProject.value)
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.error || t('apiTesting.interface.importBatch.importFailed'))
  } finally {
    importLoading.value = false
  }
}

const exportCollection = async () => {
  const collection = rightClickedNode.value
  if (!collection || collection.type !== 'collection') return

  showContextMenu.value = false
  try {
    const response = await api.get(`/api-testing/collections/${collection.id}/export/`, {
      responseType: 'blob'
    })
    const blobUrl = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    const contentDisposition = response.headers['content-disposition'] || ''
    const encodedName = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    link.href = blobUrl
    link.download = encodedName ? decodeURIComponent(encodedName) : `${collection.name}.postman_collection.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(blobUrl)
    ElMessage.success(t('apiTesting.interface.exportCollectionSuccess'))
  } catch (error) {
    ElMessage.error(error.response?.data?.error || t('apiTesting.interface.exportCollectionFailed'))
    console.error('导出集合接口失败:', error)
  }
}

// 代码生成相关
const showCodeGenerateDialog = ref(false)
const codeLanguage = ref('javascript')
const generatedCode = ref('')

// WebSocket相关
const websocketConnectionStatus = ref('disconnected') // disconnected, connecting, connected
const websocketConnection = ref(null)
const websocketMessages = ref([]) // WebSocket消息历史记录
const websocketMessageType = ref('text')
const websocketMessageContent = ref('')
const websocketBinaryFile = ref(null)

const onProjectChange = async (projectId) => {
  if (!projectId) return

  try {
    clearSearch()
    requestSelectionVersion += 1
    disconnectWebSocket()
    requestEditorKey.value += 1
    selectedRequest.value = null
    selectedEnvironment.value = ''
    response.value = null
    await Promise.all([
      loadCollections(projectId),
      loadEnvironments(projectId)
    ])
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.loadProjectsFailed'))
    console.error('切换项目失败:', error)
  }
}

const loadProjects = async () => {
  try {
    const response = await api.get('/api-testing/projects/')
    // 后端可能返回分页格式 { results: [...] } 或直接返回数组
    projects.value = response.data.results || response.data || []
    if (projects.value.length > 0) {
      selectedProject.value = getPreferredProjectId(projects.value, projects.value[0].id)
    }
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.loadProjectsFailed'))
    console.error('加载项目失败:', error)
  }
}

const loadCollections = async (projectId) => {
  try {
    const [collectionsData, requests] = await Promise.all([
      fetchAllPages('/api-testing/collections/', { project: projectId, tree: 1 }),
      fetchAllPages('/api-testing/requests/', { project: projectId, tree: 1 })
    ])

    if (selectedProject.value !== projectId) return

    // 构建树形结构
    collections.value = buildTree(collectionsData)
    flatCollections.value = collectionsData
    attachRequestsToTree(requests)
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.loadCollectionsFailed'))
    console.error('加载集合失败:', error)
  }
}

const loadEnvironments = async (projectId) => {
  try {
    // 不传递 project 参数，让后端返回所有可访问的环境（全局 + 当前用户项目），
    // 否则 DjangoFilterBackend 的 project 过滤会把 project 为 null 的全局环境过滤掉。
    const response = await api.get('/api-testing/environments/', {})
    // 后端可能返回分页格式 { results: [...] } 或直接返回数组
    const allEnvironments = response.data.results || response.data || []
    // 过滤出全局环境 + 当前项目的局部环境
    environments.value = allEnvironments.filter(env =>
      env.scope === 'GLOBAL' ||
      (env.scope === 'LOCAL' && (!projectId || env.project === projectId))
    )
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.loadEnvironmentsFailed'))
    console.error('加载环境失败:', error)
  }
}

const buildTree = (items) => {
  const map = new Map()
  const roots = []

  items.forEach(item => {
    map.set(String(item.id), {
      ...item,
      type: 'collection',
      treeKey: `collection:${item.id}`,
      children: []
    })
  })

  items.forEach(item => {
    if (item.parent) {
      const parent = map.get(String(item.parent))
      const collection = map.get(String(item.id))
      if (parent && collection) {
        parent.children.push(collection)
      }
    } else {
      const collection = map.get(String(item.id))
      if (collection) roots.push(collection)
    }
  })

  return roots
}

// 读取 DRF 分页接口的全部结果，避免大规模导入后树只显示第一页数据。
const fetchAllPages = async (url, params = {}) => {
  const results = []
  let page = 1
  while (true) {
    const response = await api.get(url, { params: { ...params, page, page_size: 1500 } })
    const data = response.data
    if (Array.isArray(data)) return data
    results.push(...(data.results || []))
    if (!data.next) return results
    page += 1
  }
}

const findCollectionById = (collections, id) => {
  for (const item of collections) {
    // 请求节点也有 id，只允许集合节点参与父集合匹配。
    if (item.type !== 'collection') continue
    if (String(item.id) === String(id)) return item
    const found = findCollectionById(item.children || [], id)
    if (found) return found
  }
  return null
}

// 保存接口后只同步当前树节点，避免重新请求并重建整棵接口树。
const updateRequestTreeNode = (requestData) => {
  if (!requestData?.id) return
  const treeRequest = {
    ...requestData,
    type: 'request',
    treeKey: `request:${requestData.id}`,
    name: requestData.name
  }
  delete treeRequest.children

  const replaceIn = (nodes) => {
    for (const node of nodes) {
      if (node.type === 'request' && String(node.id) === String(requestData.id)) {
        Object.assign(node, treeRequest)
        return true
      }
      if (node.children && replaceIn(node.children)) return true
    }
    return false
  }

  if (!replaceIn(collections.value)) {
    const collection = requestData.collection
      ? findCollectionById(collections.value, requestData.collection)
      : null
    if (collection) {
      collection.children = collection.children || []
      collection.children.push(treeRequest)
    } else if (!requestData.collection) {
      collections.value.push(treeRequest)
    }
  }
}

const clearCollectionChildren = (collection) => {
  collection.children = (collection.children || []).filter(child => child.type === 'collection')
  collection.children.forEach(clearCollectionChildren)
}

const attachRequestsToTree = (requests) => {
  collections.value = collections.value.filter(item => item.type === 'collection')

  const collectionMap = new Map()
  const indexCollections = (items) => {
    items.forEach(collection => {
      if (collection.type !== 'collection') return
      clearCollectionChildren(collection)
      collectionMap.set(String(collection.id), collection)
      indexCollections(collection.children)
    })
  }
  indexCollections(collections.value)

  requests.forEach(request => {
    const treeRequest = {
      ...request,
      type: 'request',
      treeKey: `request:${request.id}`,
      name: request.name
    }
    delete treeRequest.children

    const collection = request.collection
      ? collectionMap.get(String(request.collection))
      : null
    if (collection) {
      collection.children.push(treeRequest)
    } else if (!request.collection) {
      collections.value.push(treeRequest)
    }
  })
}

const selectRequest = (requestData) => {
  requestSelectionVersion += 1
  disconnectWebSocket()
  websocketMessages.value = []
  clearWebSocketMessage()
  showVariableHelper.value = false
  showDataFactorySelector.value = false
  helperTarget.value = null
  response.value = null
  requestEditorKey.value += 1
  selectedRequest.value = { ...cloneDraft(requestData), ...createRequestDraft(requestData) }
  delete selectedRequest.value.type
  delete selectedRequest.value.treeKey
  delete selectedRequest.value.children
}

const onNodeClick = async (data) => {
  if (data.type !== 'request') return
  const version = ++requestSelectionVersion
  try {
    const apiResponse = await api.get(`/api-testing/requests/${data.id}/`)
    if (version === requestSelectionVersion) selectRequest(apiResponse.data)
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.loadRequestsFailed'))
    console.error('加载请求失败:', error)
  }
}

const onNodeRightClick = async (event, node) => {
  event.preventDefault()
  if (node.type !== 'collection') {
    showContextMenu.value = false
    return
  }

  contextMenuReady.value = false
  showContextMenu.value = true
  contextMenuX.value = event.clientX
  contextMenuY.value = event.clientY
  rightClickedNode.value = node

  await nextTick()
  const menu = contextMenuRef.value
  if (!menu) return

  // 根据菜单实际尺寸限制坐标，避免靠近窗口边缘时菜单被裁切。
  const { width, height } = menu.getBoundingClientRect()
  const margin = 8
  const maxX = Math.max(margin, document.documentElement.clientWidth - width - margin)
  const maxY = Math.max(margin, document.documentElement.clientHeight - height - margin)
  contextMenuX.value = Math.min(Math.max(event.clientX, margin), maxX)
  contextMenuY.value = Math.min(Math.max(event.clientY, margin), maxY)
  contextMenuReady.value = true
}

const onNodeExpand = (node) => {
  if (searchKeyword.value.trim()) return
  if (!expandedKeys.value.includes(node.treeKey)) expandedKeys.value.push(node.treeKey)
}

const onNodeCollapse = (node) => {
  if (searchKeyword.value.trim()) return
  expandedKeys.value = expandedKeys.value.filter(key => key !== node.treeKey)
}

const createEmptyRequest = () => {
  if (!selectedProject.value) {
    ElMessage.warning(t('apiTesting.interface.selectProjectFirst'))
    return
  }

  const newRequest = {
    id: null,
    collection: null,
    name: t('apiTesting.interface.unnamedRequest'),
    url: '',
    method: 'GET',
    params: [],
    headers: [],
    pre_request_script: '',
    post_request_script: '',
    assertions: [],
    extractors: [],
    request_type: 'HTTP'
  }

  selectRequest(newRequest)
}

const openCreateCollectionDialog = () => {
  showCreateCollectionDialog.value = true
}

const clearSearch = () => {
  searchKeyword.value = ''
}

const closeCreateCollectionDialog = () => {
  showCreateCollectionDialog.value = false
}

const closeEditCollectionDialog = () => {
  showEditCollectionDialog.value = false
}

const closeCurlImportDialog = () => {
  showCurlImportDialog.value = false
}

const closeCodeGenerateDialog = () => {
  showCodeGenerateDialog.value = false
}

const addRequest = () => {
  if (!rightClickedNode.value) return

  const parentNode = rightClickedNode.value
  if (!parentNode || parentNode.type !== 'collection') {
    ElMessage.warning(t('apiTesting.interface.collectionOnlyAddRequest'))
    return
  }

  const newRequest = {
    id: null,
    collection: parentNode.id,
    name: t('apiTesting.interface.unnamedRequest'),
    url: '',
    method: 'GET',
    params: [],
    headers: [],
    pre_request_script: '',
    post_request_script: '',
    assertions: [],
    extractors: [],
    request_type: 'HTTP'
  }

  selectRequest(newRequest)
  showContextMenu.value = false
}

const addCollection = () => {
  if (!rightClickedNode.value) return

  const parentNode = rightClickedNode.value
  if (!parentNode || parentNode.type !== 'collection') {
    ElMessage.warning(t('apiTesting.interface.collectionOnlyAddSubcollection'))
    return
  }

  collectionForm.parent = parentNode.id
  showCreateCollectionDialog.value = true
  showContextMenu.value = false
}

const editNode = () => {
  if (!rightClickedNode.value) return

  const node = rightClickedNode.value
  if (!node) {
    ElMessage.warning(t('apiTesting.interface.cannotEditNode'))
    return
  }

  if (node.type === 'collection') {
    editingNodeId.value = node.id
    editingNodeName.value = node.name
    showContextMenu.value = false

    nextTick(() => {
      editInputRef.value?.focus()
    })
  } else {
    ElMessage.warning(t('apiTesting.interface.collectionNameOnly'))
  }
}

const deleteNode = (node = rightClickedNode.value) => {
  if (!node) {
    ElMessage.warning(t('apiTesting.interface.cannotDeleteNode'))
    return
  }

  const nodeName = node.name

  ElMessageBox.confirm(
    t('apiTesting.interface.confirmDeleteNode', {
      type: t(node.type === 'collection' ? 'apiTesting.interface.collection' : 'apiTesting.interface.request'),
      name: nodeName,
      extra: node.type === 'collection' ? t('apiTesting.interface.deleteCollectionExtra') : ''
    }),
    t('apiTesting.common.confirmDelete'),
    {
      confirmButtonText: t('apiTesting.common.confirm'),
      cancelButtonText: t('apiTesting.common.cancel'),
      type: 'warning'
    }
  ).then(async () => {
    try {
      if (node.type === 'collection') {
        await api.delete(`/api-testing/collections/${node.id}/`)
      } else {
        await api.delete(`/api-testing/requests/${node.id}/`)
      }
      ElMessage.success(t('apiTesting.interface.deleted'))
      await loadCollections(selectedProject.value)
      showContextMenu.value = false
    } catch (error) {
      ElMessage.error(t('apiTesting.interface.deleteFailed'))
      console.error('删除失败:', error)
    }
  }).catch(() => {
    // 取消删除
    showContextMenu.value = false
  })
}

const copyRequest = async (node) => {
  if (!node || node.type !== 'request') return
  try {
    const { data } = await api.get(`/api-testing/requests/${node.id}/`)
    const payload = { ...data }
    delete payload.id
    delete payload.created_at
    delete payload.updated_at
    delete payload.created_by
    payload.name = `${data.name} (${t('apiTesting.common.copy')})`
    // collection 保持原值 → 复制到同一集合下
    await api.post('/api-testing/requests/', payload)
    ElMessage.success(t('apiTesting.interface.copied'))
    await loadCollections(selectedProject.value)
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.copyFailed'))
    console.error('复制失败:', error)
  }
}

// 仅接口节点可拖拽（集合不可拖，避免改动集合树结构）
const allowDrag = (node) => node.data.type === 'request'

// 仅允许把接口“放进”某个集合节点，不实现同集合内 prev/next 重排持久化
const allowDrop = (draggingNode, dropNode, type) =>
  type === 'inner' && dropNode?.data?.type === 'collection'

const handleDrop = async (draggingNode, dropNode) => {
  const requestId = draggingNode.data.id
  const targetCollectionId = dropNode.data.id
  // 同集合：el-tree 已乐观移动，统一 reload 回到服务端真实状态
  if (draggingNode.data.collection === targetCollectionId) {
    await loadCollections(selectedProject.value)
    return
  }
  try {
    await api.patch(`/api-testing/requests/${requestId}/`, { collection: targetCollectionId })
    ElMessage.success(t('apiTesting.interface.moved'))
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.moveFailed'))
    console.error('移动失败:', error)
  } finally {
    // el-tree 已乐观改了本地 collections，无论成败都用服务端状态覆盖，避免脏数据
    await loadCollections(selectedProject.value)
  }
}

const saveCollectionName = async () => {
  if (!editingNodeId.value || !editingNodeName.value.trim()) {
    cancelEdit()
    return
  }

  try {
    await api.patch(`/api-testing/collections/${editingNodeId.value}/`, {
      name: editingNodeName.value.trim()
    })
    ElMessage.success(t('apiTesting.interface.saved'))
    await loadCollections(selectedProject.value)
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.saveFailed'))
    console.error('保存失败:', error)
  } finally {
    cancelEdit()
  }
}

const cancelEdit = () => {
  editingNodeId.value = null
  editingNodeName.value = ''
}

const collectionForm = reactive({
  name: '',
  description: '',
  parent: null
})

const editCollectionForm = reactive({
  id: null,
  name: '',
  description: '',
  parent: null
})

const collectionRules = {
  name: [{ required: true, message: t('apiTesting.interface.inputCollectionName'), trigger: 'blur' }]
}

const requireEnvironment = () => {
  if (currentEnvironment.value) return true
  ElMessage.warning(t('apiTesting.requestEditor.environmentRequired'))
  return false
}

const getBackendPreview = async () => {
  if (!requireEnvironment()) return null
  if (!selectedRequest.value?.id) {
    ElMessage.warning(t('apiTesting.requestEditor.saveBeforePreview'))
    return null
  }
  const key = requestEditorKey.value
  const id = selectedRequest.value.id
  const snapshot = JSON.stringify(requestDraft.value)
  const environmentId = selectedEnvironment.value
  const draft = await serializeRequestDraft(requestDraft.value)
  if (key !== requestEditorKey.value) return null
  const { data } = await api.post(`/api-testing/requests/${id}/preview-url/`, { ...draft, environment_id: environmentId })
  if (key !== requestEditorKey.value || String(environmentId) !== String(selectedEnvironment.value) || snapshot !== JSON.stringify(requestDraft.value)) return null
  return data.url
}

const confirmUrlPreview = async () => {
  try {
    const url = await getBackendPreview()
    if (url) await ElMessageBox.alert(url, t('apiTesting.requestEditor.confirmPreview'))
  } catch (error) {
    ElMessage.error(error.response?.data?.error || requestErrorMessage(error) || t('apiTesting.requestEditor.previewFailed'))
  }
}

const sendRequest = async () => {
  if (sending.value || saving.value || !requireEnvironment()) return
  if (!selectedRequest.value?.url) { ElMessage.warning(t('apiTesting.interface.requestUrlRequired')); return }
  const key = requestEditorKey.value
  const environmentId = selectedEnvironment.value
  try {
    sending.value = true
    if (!selectedRequest.value.id) {
      if (!await saveRequest({ silent: true }) || key !== requestEditorKey.value) return
    }
    const id = selectedRequest.value.id
    const requestData = { ...await serializeRequestDraft(requestDraft.value), environment_id: environmentId }
    const { data } = await api.post(`/api-testing/requests/${id}/execute/`, requestData)
    if (key === requestEditorKey.value) response.value = data
    ElMessage.success(t('apiTesting.interface.requestSucceeded'))
  } catch (error) {
    ElMessage.error(error.response?.data?.error || requestErrorMessage(error) || t('apiTesting.interface.requestFailed'))
  } finally { sending.value = false }
}

const saveRequest = async (options = {}) => {
  if (saving.value || !selectedRequest.value?.url) {
    ElMessage.warning(t('apiTesting.interface.requestUrlRequired'))
    return false
  }
  const key = requestEditorKey.value
  const source = cloneDraft(selectedRequest.value)
  try {
    saving.value = true
    const requestData = {
      ...await serializeRequestDraft(source), name: source.name,
      collection: source.collection || null, project: selectedProject.value
    }
    const result = source.id
      ? await api.put(`/api-testing/requests/${source.id}/`, requestData)
      : await api.post('/api-testing/requests/', requestData)
    updateRequestTreeNode(result.data)
    // A delayed save must never replace another request or discard newer local edits.
    if (key === requestEditorKey.value) selectedRequest.value = { ...selectedRequest.value, id: result.data.id }
    if (!options.silent) ElMessage.success(t('apiTesting.interface.saved'))
    return true
  } catch (error) {
    ElMessage.error(error.response?.data?.error || requestErrorMessage(error) || t('apiTesting.interface.saveFailed'))
    return false
  } finally { saving.value = false }
}

const createCollection = async () => {
  if (!collectionForm.name.trim()) {
  ElMessage.warning(t('apiTesting.interface.inputCollectionName'))
    return
  }

  try {
    await api.post('/api-testing/collections/', {
      ...collectionForm,
      project: selectedProject.value
    })
    ElMessage.success(t('apiTesting.interface.created'))
    await loadCollections(selectedProject.value)
    showCreateCollectionDialog.value = false
    collectionForm.name = ''
    collectionForm.description = ''
    collectionForm.parent = null
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.createFailed'))
    console.error('创建失败:', error)
  }
}

const updateCollection = async () => {
  if (!editCollectionForm.name.trim()) {
  ElMessage.warning(t('apiTesting.interface.inputCollectionName'))
    return
  }

  try {
    await api.patch(`/api-testing/collections/${editCollectionForm.id}/`, editCollectionForm)
    ElMessage.success(t('apiTesting.interface.updated'))
    await loadCollections(selectedProject.value)
    showEditCollectionDialog.value = false
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.updateFailed'))
    console.error('更新失败:', error)
  }
}

const importCurl = () => {
  // 清空上次的 curl 命令
  curlCommand.value = ''
  curlPreview.value = null
  showCurlImportDialog.value = true
}

const parseAndImportCurl = async () => {
  if (!curlCommand.value.trim()) {
  ElMessage.warning(t('apiTesting.interface.curlRequired'))
    return
  }

  try {
    const requestModel = await RequestModelParser.parseCurl(curlCommand.value)

    if (!selectedRequest.value) {
      createEmptyRequest()
    }

    // Import is explicit; the host is only a suggestion and never updates an environment.
    curlPreview.value = { model: requestModel, draft: curlModelToDraft(requestModel) }
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.curlParseFailed'))
    console.error('解析CURL命令失败:', error)
  }
}

// 辅助函数：将对象格式转换为数组格式
const codegenRequestModel = async () => {
  if (!requireEnvironment()) return null
  const draft = await serializeRequestDraft(requestDraft.value)
  const url = selectedRequest.value.id ? await getBackendPreview() : previewRequestUrl(draft, currentEnvironment.value)
  if (!url) throw new Error(t('apiTesting.requestEditor.previewFailed'))
  return toCodegenModel(draft, url)
}
const exportRequest = async () => {
  if (!selectedRequest.value) return
  try {
    const model = await codegenRequestModel()
    if (!model) return
    await navigator.clipboard.writeText(RequestModelParser.toCurl(model))
    ElMessage.success(t('apiTesting.interface.clipboardCopied'))
  } catch (error) { ElMessage.error(requestErrorMessage(error) || t('apiTesting.interface.exportFailed')) }
}

const generateCode = async (language) => {
  if (!selectedRequest.value) return

  if (!selectedRequest.value.url) {
  ElMessage.warning(t('apiTesting.interface.requestUrlEmpty'))
    return
  }

  try {
    // 更新语言选择器的值
    codeLanguage.value = language

    const requestModel = await codegenRequestModel()
    if (!requestModel) return
    const code = await CodeGenerator.generateCode(requestModel, language)
    generatedCode.value = code
    showCodeGenerateDialog.value = true
  } catch (error) {
  ElMessage.error(t('apiTesting.interface.codeGenerationFailed'))
    console.error('生成代码失败:', error)
  }
}

const copyGeneratedCode = () => {
  if (generatedCode.value) {
    navigator.clipboard.writeText(generatedCode.value)
  ElMessage.success(t('apiTesting.interface.clipboardCopied'))
  }
}

let wsGeneration = 0
const disconnectWebSocket = () => {
  wsGeneration += 1
  const socket = websocketConnection.value
  websocketConnection.value = null
  websocketConnectionStatus.value = 'disconnected'
  if (socket) {
    socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null
    socket.close()
  }
}
const addWsMessage = (type, content) => websocketMessages.value.push({ type, content, timestamp: new Date().toLocaleString() })
const toggleWebSocketConnection = async () => {
  if (websocketConnectionStatus.value !== 'disconnected') { disconnectWebSocket(); return }
  if (!requireEnvironment()) return
  if (!selectedRequest.value?.id) { ElMessage.warning(t('apiTesting.requestEditor.saveBeforePreview')); return }
  const generation = ++wsGeneration
  websocketConnectionStatus.value = 'connecting'
  try {
    const wsUrl = await getBackendPreview()
    if (generation !== wsGeneration) return
    if (!wsUrl) { websocketConnectionStatus.value = 'disconnected'; return }
    if (!/^wss?:\/\//i.test(wsUrl)) throw new Error(t('apiTesting.requestEditor.previewFailed'))
    const socket = new WebSocket(wsUrl)
    websocketConnection.value = socket
    socket.onopen = () => {
      if (generation !== wsGeneration) return
      websocketConnectionStatus.value = 'connected'
      addWsMessage('connected', t('apiTesting.interface.websocketConnected'))
    }
    socket.onmessage = async event => {
      const content = event.data instanceof Blob ? `[Binary ${event.data.size} bytes]` : event.data
      if (generation === wsGeneration) addWsMessage('received', content)
    }
    socket.onclose = () => {
      if (generation !== wsGeneration) return
      websocketConnectionStatus.value = 'disconnected'
      websocketConnection.value = null
      addWsMessage('info', t('apiTesting.interface.websocketClosed'))
    }
    socket.onerror = () => {
      if (generation !== wsGeneration) return
      disconnectWebSocket()
      addWsMessage('error', t('apiTesting.interface.websocketFailed'))
      ElMessage.error(t('apiTesting.interface.websocketFailed'))
    }
  } catch (error) {
    if (generation !== wsGeneration) return
    disconnectWebSocket()
    ElMessage.error(error.response?.data?.error || requestErrorMessage(error) || t('apiTesting.interface.websocketFailed'))
  }
}
watch(selectedEnvironment, disconnectWebSocket)
watch(() => selectedRequest.value?.request_type, disconnectWebSocket)

const sendWebSocketMessage = () => {
  if (!websocketConnection.value || websocketConnectionStatus.value !== 'connected') {
  ElMessage.warning(t('apiTesting.interface.websocketConnectFirst'))
    return
  }

  if (websocketMessageType.value === 'binary' && !websocketBinaryFile.value) {
  ElMessage.warning(t('apiTesting.interface.binaryFileRequired'))
    return
  }

  try {
    const message = websocketMessageType.value === 'binary'
      ? websocketBinaryFile.value
      : websocketMessageType.value === 'json'
        ? JSON.stringify(JSON.parse(websocketMessageContent.value))
        : websocketMessageContent.value
    websocketConnection.value.send(message)
    addWsMessage('sent', websocketMessageType.value === 'binary' ? `[Binary ${websocketBinaryFile.value.name}]` : message)

    if (websocketMessageType.value !== 'binary') {
      websocketMessageContent.value = ''
    }
  } catch (error) {
    ElMessage.error(t('apiTesting.interface.sendMessageFailed'))
    console.error('发送消息失败:', error)
  }
}

const clearWebSocketMessage = () => {
  websocketMessageContent.value = ''
  websocketBinaryFile.value = null
}

const clearWebSocketMessages = () => {
  websocketMessages.value = []
}

const handleWebSocketFileUpload = (file) => {
  websocketBinaryFile.value = file.raw
}

const clearWebSocketBinaryFile = () => {
  websocketBinaryFile.value = null
}

const isJsonString = (str) => {
  try {
    JSON.parse(str)
    return true
  } catch (e) {
    return false
  }
}

const formatJson = (str) => {
  try {
    const obj = JSON.parse(str)
    return JSON.stringify(obj, null, 2)
  } catch (e) {
    return str
  }
}

const openEditorDataFactory = target => {
  helperTarget.value = { ...target, key: requestEditorKey.value }
  showDataFactorySelector.value = true
}
const openEditorVariableHelper = target => {
  helperTarget.value = { ...target, key: requestEditorKey.value }
  showVariableHelper.value = true
}
const insertHelperText = (text, replace = false) => {
  const target = helperTarget.value
  if (!target || target.key !== requestEditorKey.value) return
  if (target.target === 'assertion') {
    const draft = createRequestDraft(requestDraft.value)
    const assertion = draft.assertions[target.index]
    if (!assertion || Array.isArray(assertion[target.field])) return
    assertion[target.field] = replace ? text : `${assertion[target.field] || ''}${text}`
    requestDraft.value = draft
  } else {
    requestEditorRef.value?.insertText(target.field, text)
  }
}
const insertVariable = variable => {
  insertHelperText(variable.example)
  showVariableHelper.value = false
  ElMessage.success(t('apiTesting.interface.variableInserted', { name: variable.name }))
}

const handleDataFactorySelect = (record) => {
  if (!record || !record.output_data) return

  let valueToSet = ''

  if (typeof record.output_data === 'string') {
    valueToSet = record.output_data
  } else if (record.output_data.result) {
    valueToSet = record.output_data.result
  } else if (record.output_data.output_data) {
    valueToSet = record.output_data.output_data
  } else if (typeof record.output_data === 'object') {

    const possibleResultFields = ['result', 'value', 'data', 'output', 'content']
    let foundResult = false
    for (const field of possibleResultFields) {
      if (record.output_data[field] !== undefined) {
        valueToSet = record.output_data[field]
        foundResult = true
        break
      }
    }
    // 如果没有找到可能的结果字段，将整个对象转为JSON字符串
    if (!foundResult) {
      valueToSet = JSON.stringify(record.output_data)
    }
  } else {
    valueToSet = JSON.stringify(record.output_data)
  }

  // 确保valueToSet是字符串类型
  if (typeof valueToSet !== 'string') {
    valueToSet = JSON.stringify(valueToSet)
  }

  const target = helperTarget.value
  const text = target?.field?.endsWith('_script')
    ? `\n// Data factory output\nconst factoryOutput = ${JSON.stringify(valueToSet)}\n`
    : valueToSet
  insertHelperText(text, target?.target === 'assertion')
  ElMessage.success(t('apiTesting.interface.dataFactoryBodyReferenced', { name: record.tool_name }))
  showDataFactorySelector.value = false
}

// 加载变量函数
const loadVariableFunctions = async () => {
  try {
    loading.value = true
    console.log('开始加载变量函数...')
    const apiResponse = await getVariableFunctions()
    console.log('变量函数响应:', apiResponse)
    console.log('变量函数响应.data:', apiResponse.data)
    
    // 更灵活地处理响应数据结构
    let functionsData = null
    
    // 检查不同可能的数据结构
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
    
    if (functionsData) {
      const groupedFunctions = functionsData
      console.log('处理后的 groupedFunctions:', groupedFunctions)

      // 后端已经按分类分组了，直接转换为标签页所需的格式
      if (typeof groupedFunctions === 'object' && !Array.isArray(groupedFunctions)) {
        variableCategories.value = Object.entries(groupedFunctions).map(([label, variables]) => ({
          label,
          variables
        }))
      } else if (Array.isArray(groupedFunctions)) {
        // 如果是数组，按分类分组
        const grouped = {}
        
        // 创建分类名称映射表
        const categoryMapping = {
          '随机数': t('apiTesting.component.keyValueEditor.categories.randomNumber'),
          '测试数据': t('apiTesting.component.keyValueEditor.categories.testData'),
          '字符串': t('apiTesting.component.keyValueEditor.categories.string'),
          '编码转换': t('apiTesting.component.keyValueEditor.categories.encodingConversion'),
          '加密': t('apiTesting.component.keyValueEditor.categories.encryption'),
          '时间日期': t('apiTesting.component.keyValueEditor.categories.datetime'),
          'Crontab': t('apiTesting.component.keyValueEditor.categories.crontab'),
          '未分类': t('apiTesting.component.keyValueEditor.categories.uncategorized')
        }
        
        groupedFunctions.forEach(func => {
          const rawCategory = func.category || '未分类'
          // 使用映射表获取正确的分类名称
          const category = categoryMapping[rawCategory] || t('apiTesting.component.keyValueEditor.categories.uncategorized')
          if (!grouped[category]) {
            grouped[category] = []
          }
          grouped[category].push(func)
        })
        
        // 定义固定的分类顺序 ['随机数', '测试数据', '字符串', '编码转换', '加密', '时间日期', 'Crontab', '未分类']
        const categoryOrder = [
          t('apiTesting.component.keyValueEditor.categories.randomNumber'),
          t('apiTesting.component.keyValueEditor.categories.testData'),
          t('apiTesting.component.keyValueEditor.categories.string'),
          t('apiTesting.component.keyValueEditor.categories.encodingConversion'),
          t('apiTesting.component.keyValueEditor.categories.encryption'),
          t('apiTesting.component.keyValueEditor.categories.datetime'),
          t('apiTesting.component.keyValueEditor.categories.crontab'),
          t('apiTesting.component.keyValueEditor.categories.uncategorized')
        ]
        
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
        
        // 添加剩余的分类（如果有）
        Object.entries(grouped).forEach(([label, variables]) => {
          orderedCategories.push({
            label,
            variables
          })
        })
        
        variableCategories.value = orderedCategories
      }

      console.log('最终变量分类:', variableCategories.value)
    } else {
      console.error('响应数据格式错误，无法找到函数数据')
      console.error('完整响应:', apiResponse)
    }
  } catch (error) {
    console.error('加载变量函数失败:', error)
    ElMessage.error(t('apiTesting.interface.loadFunctionsFailed'))
    // 加载失败时使用本地变量分类数据
    useLocalVariableCategories()
  } finally {
    loading.value = false
  }
}

// 生命周期钩子
onMounted(async () => {
  await loadProjects()
  const projectId = selectedProject.value
  await Promise.all([
    projectId ? loadCollections(projectId) : Promise.resolve(),
    projectId ? loadEnvironments(projectId) : Promise.resolve(),
    loadVariableFunctions()
  ])

  // 添加全局点击事件监听器，用于隐藏右键菜单
  document.addEventListener('click', handleGlobalClick)
})

onBeforeUnmount(() => {
  disconnectWebSocket()
  // 移除全局点击事件监听器
  document.removeEventListener('click', handleGlobalClick)
})

// 处理全局点击事件，隐藏右键菜单
const handleGlobalClick = (event) => {
  // 当显示右键菜单时，点击任何地方（除了菜单本身）都应该隐藏菜单
  if (showContextMenu.value) {
    const contextMenu = document.querySelector('.context-menu')
    if (!contextMenu || !contextMenu.contains(event.target)) {
      showContextMenu.value = false
    }
  }
}

// 使用本地变量分类数据
const useLocalVariableCategories = () => {
  variableCategories.value = [
    {
      label: t('apiTesting.component.keyValueEditor.categories.randomNumber'),
      variables: [
        { name: 'random_int', syntax: '${random_int(min, max, count)}', desc: t('apiTesting.component.keyValueEditor.variables.randomInt'), example: '${random_int(100, 999, 1)}' },
        { name: 'random_float', syntax: '${random_float(min, max, precision, count)}', desc: t('apiTesting.component.keyValueEditor.variables.randomFloat'), example: '${random_float(0, 1, 2, 1)}' },
        { name: 'random_boolean', syntax: '${random_boolean(count)}', desc: t('apiTesting.component.keyValueEditor.variables.randomBoolean'), example: '${random_boolean(1)}' },
        { name: 'random_date', syntax: '${random_date(start_date, end_date, count, date_format)}', desc: t('apiTesting.component.keyValueEditor.variables.randomDate'), example: '${random_date(2024-01-01, 2024-12-31, 1, %Y-%m-%d)}' }
      ]
    },
    {
      label: t('apiTesting.component.keyValueEditor.categories.randomString'),
      variables: [
        { name: 'random_string', syntax: '${random_string(length, char_type, count)}', desc: t('apiTesting.component.keyValueEditor.variables.randomString'), example: '${random_string(8, all, 1)}' },
        { name: 'random_uuid', syntax: '${random_uuid(version, count)}', desc: t('apiTesting.component.keyValueEditor.variables.randomUuid'), example: '${random_uuid(4, 1)}' },
        { name: 'random_mac_address', syntax: '${random_mac_address(separator, count)}', desc: t('apiTesting.component.keyValueEditor.variables.randomMacAddress'), example: '${random_mac_address(:, 1)}' },
        { name: 'random_ip_address', syntax: '${random_ip_address(ip_version, count)}', desc: t('apiTesting.component.keyValueEditor.variables.randomIpAddress'), example: '${random_ip_address(4, 1)}' },
        { name: 'random_sequence', syntax: '${random_sequence(sequence, count, unique)}', desc: t('apiTesting.component.keyValueEditor.variables.randomSequence'), example: '${random_sequence([a,b,c], 1, false)}' }
      ]
    }
  ]
  console.log('使用本地变量分类数据:', variableCategories.value)
}
</script>

<style scoped>
.interface-management {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.interface-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* 左侧边栏 */
.sidebar {
  width: 100%;
  border-right: 1px solid #e4e7ed;
  background: #ffffff;
  overflow: visible;
  display: flex;
  flex-direction: column;
  box-shadow: 2px 0 6px rgba(0, 0, 0, 0.05);
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: stretch;
  position: relative;
  background: #f8f9fa;
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

.interface-header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  margin-left: auto;
}

.interface-header-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.header-actions .el-button {
  border-radius: 6px;
  transition: all 0.2s ease;
}

.header-actions .el-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.import-preview-summary,
.import-selection-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  color: #606266;
  background: #f5f7fa;
}

.import-preview-summary .el-button {
  margin-left: auto;
}

.import-preview-list {
  max-height: min(55vh, 520px);
  overflow-y: auto;
  margin-top: 12px;
}

.import-preview-group {
  margin-bottom: 10px;
  border: 1px solid #ebeef5;
}

.import-preview-group > header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #f5f7fa;
}

.import-group-count,
.import-selection-footer > span {
  color: #909399;
  font-size: 12px;
}

.import-preview-group :deep(.el-checkbox-group) {
  display: block;
}

.import-preview-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 7px 10px 7px 30px;
  border-top: 1px solid #f2f3f5;
  font-size: 14px;
  line-height: normal;
}

.import-item-name,
.import-item-url {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-item-name {
  flex: 0 1 180px;
  color: #409eff;
}

.import-item-url {
  flex: 1;
  color: #909399;
  font-size: 12px;
}

.import-selection-footer {
  justify-content: space-between;
  margin-top: 10px;
}

@media (max-width: 640px) {
  .import-preview-item {
    flex-wrap: wrap;
  }

  .import-item-name {
    flex: 1;
    min-width: 120px;
  }

  .import-item-url {
    flex-basis: calc(100% - 26px);
    margin-left: 26px;
  }
}

.collection-tree {
  flex: 1;
  overflow: auto;
  padding: 10px;
  min-width: 0;
}

/* el-tree 节点内容行：打断 min-content 宽度向上传播，
   否则长接口名会让整行撑宽、把复制/删除按钮挤出可视区 */
.collection-tree :deep(.el-tree-node__content) {
  min-width: 0;
  overflow: hidden;
}

.collection-tree :deep(.el-tree-node__content > .el-tree-node__label) {
  display: none;
}

/* 树节点样式 */
.tree-node {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  padding: 4px 0;
}

.tree-node .el-icon {
  font-size: 16px;
  color: #606266;
  flex-shrink: 0;
}

.node-label {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  color: #303133;
  transition: color 0.2s;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-node:hover .node-label {
  color: #409eff;
}

/* 悬浮操作按钮：默认隐藏，悬浮节点时显示 */
.node-actions {
  margin-left: auto;
  flex-shrink: 0;
  display: none;
  align-items: center;
  gap: 2px;
}

.tree-node:hover .node-actions {
  display: flex;
}

.node-actions .el-button {
  padding: 2px;
  color: #909399;
}

.node-actions .el-button:hover {
  color: #409eff;
}

.node-edit {
  flex: 1;
}

.node-edit .el-input {
  font-size: 14px;
}

/* 方法标签样式 */
.method-tag {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 10px;
  color: white;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  display: inline-block;
  min-width: 40px;
  text-align: center;
  flex-shrink: 0;
}

.method-tag.get {
  background-color: #61affe;
}

.method-tag.post {
  background-color: #49cc90;
}

.method-tag.put {
  background-color: #fca130;
}

.method-tag.delete {
  background-color: #f93e3e;
}

.method-tag.patch {
  background-color: #50e3c2;
}

.method-tag.head {
  background-color: #9013fe;
}

.method-tag.options {
  background-color: #0ebeff;
}

.method-tag.connect {
  background-color: #7f8c8d;
}

.method-tag.trace {
  background-color: #e67e22;
}

/* 搜索结果 */
/* 右侧主内容 */
.main-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: auto;
  background: #f8f9fa;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafafa;
}

.request-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 20px;
}

.env-select {
  width: 128px;
  flex: 0 0 128px;
}

.env-select .el-select {
  border-radius: 6px;
}

.curl-preview pre { max-height: 280px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }

/* 请求元数据 */
.request-meta {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.name-input {
  flex: 1;
  min-width: 200px;
}

.name-input .el-input {
  border-radius: 6px;
}

.action-buttons {
  display: flex;
  gap: 8px;
  align-items: center;
}

.action-buttons .el-button {
  border-radius: 6px;
  transition: all 0.2s ease;
}

.action-buttons .el-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.action-buttons .el-dropdown {
  border-radius: 6px;
}

.code-generate {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 14px;
  line-height: 1.6;
  background: #f8f9fa;
  border: 1px solid #e9ecef;
  border-radius: 8px;
  padding: 16px;
  min-height: 300px;
  resize: vertical;
}

/* 上下文菜单 */
.context-menu {
  position: fixed;
  z-index: 1000;
  background: white;
  border: 1px solid #e9ecef;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  padding: 8px 0;
  min-width: 140px;
  list-style: none;
  margin: 0;
}

.context-menu li {
  padding: 10px 16px;
  cursor: pointer;
  transition: all 0.2s ease;
  font-size: 14px;
  color: #303133;
}

.context-menu li:hover {
  background: #f5f3ff;
  color: #5046e5;
  font-weight: 500;
}

/* WebSocket 相关 */
.message-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.message-input-section {
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 20px;
}

.message-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-top: 16px;
}

.message-actions .el-button {
  border-radius: 6px;
  transition: all 0.2s ease;
}

.message-actions .el-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.websocket-response-section {
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 20px;
}

.websocket-response-section h3 {
  margin-top: 0;
  margin-bottom: 16px;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.websocket-messages {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 400px;
  overflow: auto;
  padding: 10px;
  background: white;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.websocket-message-item {
  padding: 12px;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
  background: white;
}

.websocket-message-item.sent {
  border-left: 4px solid #409eff;
  background: #ecf5ff;
}

.websocket-message-item.received {
  border-left: 4px solid #67c23a;
  background: #f0f9eb;
}

.websocket-message-item.connected {
  border-left: 4px solid #67c23a;
  background: #f0f9eb;
}

.websocket-message-item.info {
  border-left: 4px solid #909399;
  background: #f5f7fa;
}

.websocket-message-item.error {
  border-left: 4px solid #f56c6c;
  background: #fef0f0;
}

.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
}

.message-type {
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 10px;
}

.message-type.sent {
  background: #d9ecff;
  color: #409eff;
}

.message-type.received {
  background: #e8f5e8;
  color: #67c23a;
}

.message-type.connected {
  background: #e8f5e8;
  color: #67c23a;
}

.message-type.info {
  background: #f0f2f5;
  color: #909399;
}

.message-type.error {
  background: #fde2e2;
  color: #f56c6c;
}

.message-time {
  color: #909399;
}

.message-content {
  font-family: 'Courier New', Courier, monospace;
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.uploaded-file {
  margin-top: 12px;
  padding: 8px 12px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .interface-layout {
    flex-direction: column;
  }

  .sidebar {
    width: 100%;
    max-height: 300px;
    border-right: none;
    border-bottom: 1px solid #e4e7ed;
  }

  .env-select {
    width: 100%;
  }

  .request-meta {
    flex-direction: column;
    align-items: stretch;
  }

  .name-input {
    width: 100%;
  }

  .action-buttons {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

}

/* 滚动条样式 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 4px;
}

::-webkit-scrollbar-thumb {
  background: #c1c1c1;
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: #a8a8a8;
}

/* 覆盖方法标签样式 */
.method-tag {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 10px;
  color: white;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  display: inline-block;
  min-width: 40px;
  text-align: center;
  flex-shrink: 0;
}

.method-tag.get {
  background-color: #61affe !important;
}

.method-tag.post {
  background-color: #49cc90 !important;
}

.method-tag.put {
  background-color: #fca130 !important;
}

.method-tag.delete {
  background-color: #f93e3e !important;
}

.method-tag.patch {
  background-color: #50e3c2 !important;
}

.method-tag.head {
  background-color: #9013fe !important;
}

.method-tag.options {
  background-color: #0ebeff !important;
}

.method-tag.connect {
  background-color: #7f8c8d !important;
}

.method-tag.trace {
  background-color: #e67e22 !important;
}

</style>
