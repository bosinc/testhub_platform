<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="page-title">{{ $t('testcase.title') }}</h1>
      <div class="header-actions">
        <el-button v-if="selectedTestCases.length > 0" @click="openMoveDialog">
          <el-icon><FolderOpened /></el-icon>{{ $t('testcase.moveCases') }} ({{ selectedCount }})
        </el-button>
        <el-button
          v-if="selectedTestCases.length > 0"
          type="danger"
          @click="batchDeleteTestCases"
          :disabled="isDeleting">
          <el-icon><Delete /></el-icon>
          {{ $t('testcase.batchDelete') }} ({{ selectedCount }})
        </el-button>
        <el-button type="success" @click="exportToExcel">
          <el-icon><Download /></el-icon>
          {{ $t('testcase.exportExcel') }}
        </el-button>
        <el-button @click="downloadImportTemplate">
          <el-icon><Download /></el-icon>
          {{ $t('testcase.downloadImportTemplate') }}
        </el-button>
        <el-button type="warning" @click="openImportDialog">
          <el-icon><Upload /></el-icon>
          {{ $t('testcase.importCases') }}
        </el-button>
        <el-button @click="goToImportRecords">
          {{ $t('testcase.importRecords') }}
        </el-button>
        <el-button type="primary" @click="goToCreateTestCase">
          <el-icon><Plus /></el-icon>
          {{ $t('testcase.newCase') }}
        </el-button>
      </div>
    </div>
    
    <ResizableSplitPane v-model="sidebarWidth" :min-size="220" :max-size="420" :second-min-size="500" storage-key="testcase-module-sidebar-width">
      <template #first>
        <aside class="module-sidebar">
          <div class="sidebar-header">
            <el-select v-model="moduleProjectId" clearable filterable :placeholder="$t('testcase.relatedProject')" @change="handleModuleProjectChange">
              <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
            </el-select>
            <div class="sidebar-title-row">
              <el-input
                v-model="moduleSearch"
                size="small"
                clearable
                :placeholder="$t('testcase.searchModules')"
              >
                <template #prefix><el-icon><Search /></el-icon></template>
              </el-input>
              <el-button type="success" size="small" :disabled="!moduleProjectId" @click="openModuleDialog()"><el-icon><Plus /></el-icon></el-button>
            </div>
          </div>
          <div class="collection-tree">
            <div class="tree-entry" :class="{ active: selectedModule === '' }" @click="selectModule('')">
              <span class="tree-entry-label">{{ $t('testcase.allCases') }}</span><span class="tree-count">{{ allProjectCount }}</span>
            </div>
            <el-tree
              v-if="moduleProjectId"
              ref="moduleTreeRef"
              :data="filteredModuleTree"
              :props="{ label: 'name', children: 'children' }"
              node-key="id"
              :default-expanded-keys="expandedModuleKeys"
              :auto-expand-parent="false"
              highlight-current
              draggable
              :allow-drop="allowModuleDrop"
              :expand-on-click-node="false"
              @node-click="node => selectModule(String(node.id))"
              @node-expand="handleModuleExpand"
              @node-collapse="handleModuleCollapse"
              @node-drop="handleModuleDrop"
            >
              <template #default="{ node, data }">
                <div class="tree-node" :class="{ 'is-active': selectedModule === String(data.id) }">
                  <el-icon><Folder /></el-icon>
                  <span class="node-label" :title="node.label">{{ node.label }}</span>
                  <div class="node-actions" @click.stop>
                    <el-dropdown trigger="click" @command="command => handleModuleCommand(command, data)">
                      <el-button link size="small" :icon="MoreFilled" :title="$t('testcase.modules')" />
                      <template #dropdown><el-dropdown-menu>
                        <el-dropdown-item command="child">{{ $t('testcase.addSubmodule') }}</el-dropdown-item>
                        <el-dropdown-item command="move">{{ $t('testcase.moveModule') }}</el-dropdown-item>
                        <el-dropdown-item command="rename">{{ $t('common.edit') }}</el-dropdown-item>
                        <el-dropdown-item command="delete" divided>{{ $t('common.delete') }}</el-dropdown-item>
                      </el-dropdown-menu></template>
                    </el-dropdown>
                  </div>
                  <span class="tree-count">{{ data.count }}</span>
                </div>
              </template>
            </el-tree>
            <el-empty v-else :description="$t('testcase.selectProjectForModules')" :image-size="60" />
            <el-empty v-if="moduleProjectId && moduleSearch.trim() && !filteredModuleTree.length" :description="$t('testcase.noModulesFound')" :image-size="60" />
            <div v-if="moduleProjectId" class="tree-entry root-module-level" :class="{ active: selectedModule === 'none' }" @click="selectModule('none')">
              <el-icon><FolderOpened /></el-icon><span class="tree-entry-label">{{ $t('testcase.noModule') }}</span><span class="tree-count">{{ unassignedCount }}</span>
            </div>
          </div>
        </aside>
      </template>
      <template #second>
      <section class="card-container">
      <div class="filter-bar">
        <div class="filter-row">
          <el-input
            v-model="searchText"
            class="filter-search"
            :placeholder="$t('testcase.searchPlaceholder')"
            clearable
            @input="handleSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
          <el-select
            v-model="priorityFilter"
            class="filter-select"
            :placeholder="$t('testcase.priorityFilter')"
            clearable
            @change="handleFilter"
          >
            <el-option :label="$t('testcase.low')" value="low" />
            <el-option :label="$t('testcase.medium')" value="medium" />
            <el-option :label="$t('testcase.high')" value="high" />
            <el-option :label="$t('testcase.critical')" value="critical" />
          </el-select>
          <el-select
            v-model="maintainerFilter"
            class="filter-select"
            :placeholder="$t('testcase.maintainerFilter')"
            clearable
            filterable
            @change="handleFilter"
          >
            <el-option
              v-for="maintainer in maintainers"
              :key="maintainer.id"
              :label="maintainer.email ? `${maintainer.username} (${maintainer.email})` : maintainer.username"
              :value="maintainer.id"
            />
          </el-select>
          <el-tooltip :content="$t('testcase.dateRangeTip')" placement="top" :show-after="400">
            <el-date-picker
              v-model="dateRange"
              class="filter-date"
              type="daterange"
              value-format="YYYY-MM-DD"
              :range-separator="$t('testcase.dateRangeSeparator')"
              :start-placeholder="$t('testcase.dateRangeStart')"
              :end-placeholder="$t('testcase.dateRangeEnd')"
              unlink-panels
              clearable
              @change="handleFilter"
            />
          </el-tooltip>
        </div>
      </div>
      
      <div class="list-summary">
        <span class="summary-text">{{ $t('testcase.listSummary', { total, selected: selectedCount }) }}</span>
      </div>

      <div class="table-container">
        <el-table 
          ref="tableRef"
          :data="testcases" 
          row-key="id"
          v-loading="loading" 
          style="width: 100%"
          height="100%"
          @selection-change="handleSelectionChange"
          @select-all="handleSelectAll">
          <el-table-column type="selection" width="30" />
          <el-table-column prop="id" :label="$t('testcase.caseId')" width="60" />
          <el-table-column prop="title" :label="$t('testcase.caseTitle')" min-width="360">
            <template #default="{ row }">
              <el-tooltip :content="row.title" placement="top" :show-after="500">
                <div
                  class="case-title"
                  role="link"
                  tabindex="0"
                  @click="goToTestCase(row.id)"
                  @keydown.enter="goToTestCase(row.id)"
                  @keydown.space.prevent="goToTestCase(row.id)"
                >{{ row.title }}</div>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column :label="$t('testcase.automation')" width="110">
            <template #default="{ row }">
              <AutomationCaseLinks
                :test-case-id="row.id"
                :project-id="row.project?.id"
                :links="row.automation_links || []"
                @update:links="row.automation_links = $event"
              />
            </template>
          </el-table-column>
          <el-table-column prop="priority" :label="$t('testcase.priority')" width="80">
            <template #default="{ row }">
              <el-tag :class="`priority-tag ${row.priority}`">{{ getPriorityText(row.priority) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="test_type" :label="$t('testcase.testType')" width="100">
            <template #default="{ row }">
              {{ getTypeText(row.test_type) }}
            </template>
          </el-table-column>
          <el-table-column prop="author.username" :label="$t('testcase.maintainer')" width="100" />
          <el-table-column prop="updated_at" :label="$t('testcase.updatedAt')" width="150">
            <template #default="{ row }">
              {{ formatDate(row.updated_at) }}
            </template>
          </el-table-column>
          <el-table-column :label="$t('project.actions')" width="80" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button
                  link
                  type="primary"
                  size="default"
                  :aria-label="$t('common.edit')"
                  @click="editTestCase(row)"
                >
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button
                  link
                  type="danger"
                  size="default"
                  :aria-label="$t('common.delete')"
                  @click="deleteTestCase(row)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
            </template>
          </el-table-column>
          <!-- 滚动到底部的哨兵元素：进入可视区域时加载下一页 -->
          <template #append>
            <div ref="loadMoreSentinel" class="load-more-sentinel">
              <el-icon v-if="loadingMore" class="is-loading"><Loading /></el-icon>
              <span v-if="loadingMore">{{ $t('testcase.loadingMore') }}</span>
              <span v-else-if="hasMore">{{ $t('testcase.scrollToLoadMore') }}</span>
              <span v-else-if="testcases.length">{{ $t('testcase.allLoaded') }}</span>
            </div>
          </template>
        </el-table>
      </div>
      
      </section>
      </template>
    </ResizableSplitPane>

    <el-dialog v-model="moduleDialogVisible" :title="moduleEditing ? $t('testcase.renameModule') : $t('testcase.createModule')" width="420px">
      <el-form label-width="90px"><el-form-item :label="$t('testcase.moduleName')"><el-input v-model="moduleForm.name" maxlength="100" /></el-form-item></el-form>
      <template #footer><el-button @click="moduleDialogVisible = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" @click="saveModule">{{ $t('common.confirm') }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="moveDialogVisible" :title="movingModule ? $t('testcase.moveModule') : $t('testcase.moveCases')" width="460px">
      <el-form label-width="100px"><el-form-item :label="$t('testcase.targetModule')"><el-tree-select v-model="moveTargetModule" :data="moveModuleOptions" node-key="id" check-strictly default-expand-all :render-after-expand="false" style="width: 100%" :placeholder="$t('testcase.selectTargetModule')" /></el-form-item></el-form>
      <template #footer><el-button @click="moveDialogVisible = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="isMoving" @click="confirmMove">{{ movingModule ? $t('testcase.moveModule') : $t('testcase.moveCases') }}</el-button></template>
    </el-dialog>

    <el-dialog
      v-model="importDialogVisible"
      :title="$t('testcase.importDialogTitle')"
      width="560px"
    >
      <el-alert
        :title="$t('testcase.uploadTip')"
        type="info"
        :closable="false"
        show-icon
        class="import-alert"
      />

      <el-form label-width="100px">
        <el-form-item :label="$t('testcase.importProject')">
          <el-select
            v-model="importForm.projectId"
            style="width: 100%"
            :placeholder="$t('testcase.selectImportProject')"
            filterable
          >
            <el-option
              v-for="project in projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('testcase.selectImportFile')">
          <el-upload
            ref="importUploadRef"
            class="import-upload"
            drag
            action="#"
            :auto-upload="false"
            :limit="1"
            accept=".xlsx"
            :show-file-list="false"
            :before-upload="beforeImportUpload"
            :on-change="handleImportFileChange"
            :on-exceed="handleImportFileExceed"
          >
            <el-icon class="el-icon--upload"><Upload /></el-icon>
            <div class="el-upload__text">
              {{ $t('testcase.chooseFile') }}
            </div>
            <template #tip>
              <div class="el-upload__tip">
                {{ $t('testcase.selectedFile') }}: {{ selectedImportFile?.name || '-' }}
              </div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="importDialogVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button @click="downloadImportTemplate">
          {{ $t('testcase.downloadImportTemplate') }}
        </el-button>
        <el-button type="primary" :loading="isCreatingImport" @click="submitImport">
          {{ isCreatingImport ? $t('testcase.uploading') : $t('common.confirm') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
defineOptions({ name: 'TestCaseList' })

import { ref, onMounted, onActivated, onUnmounted, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox, ElLoading } from 'element-plus'
import { Plus, Search, Download, Delete, Edit, Upload, Folder, FolderOpened, MoreFilled, Loading } from '@element-plus/icons-vue'
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import AutomationCaseLinks from '@/components/AutomationCaseLinks.vue'
import api from '@/utils/api'
import { deleteTestCase as deleteTestCaseApi, bulkDeleteTestCases as bulkDeleteTestCasesApi, deleteTestCaseModule as deleteTestCaseModuleApi } from '@/api/testcases'
import dayjs from 'dayjs'
import * as XLSX from 'xlsx'
import { getPreferredProjectId, rememberSelectedProject } from '@/utils/projectSelection'

const { t, locale } = useI18n()
const router = useRouter()
const loading = ref(false)
const sidebarWidth = ref(280)
const moduleTree = ref([])
const moduleTreeRef = ref(null)
const expandedModuleKeys = ref([])
const moduleProjectId = ref('')
const moduleSearch = ref('')
const selectedModule = ref('')
const includeChildModules = ref(true)
const allProjectCount = ref(0)
const unassignedCount = ref(0)
const moduleDialogVisible = ref(false)
const moduleEditing = ref(false)
const moduleForm = ref({ name: '', id: null, parentId: null })
const moveDialogVisible = ref(false)
const moveTargetModule = ref(null)
const isMoving = ref(false)
const movingModule = ref(null)
const isReorderingModules = ref(false)
// 列表滚动加载的 IntersectionObserver 实例（哨兵元素进入视口时加载下一页）
let sentinelObserver = null
const filteredModuleTree = computed(() => {
  const keyword = moduleSearch.value.trim().toLocaleLowerCase()
  if (!keyword) return moduleTree.value
  const filterNodes = (nodes) => nodes.reduce((matches, node) => {
    const children = filterNodes(node.children || [])
    if (node.name.toLocaleLowerCase().includes(keyword) || children.length) {
      matches.push({ ...node, children })
    }
    return matches
  }, [])
  return filterNodes(moduleTree.value)
})
const testcases = ref([])
const projects = ref([])
const maintainers = ref([])
const currentPage = ref(1)
const total = ref(0)
const tableRef = ref(null)
const loadMoreSentinel = ref(null)
const loadingMore = ref(false)
// 表头全选后置为 true：表示"全部用例"（已加载 + 后续滚动加载的）都处于勾选状态
const selectAllMode = ref(false)
// 程序化勾选新加载行时，避免 selection-change 把 selectAllMode 误判为取消全选
const syncingSelection = ref(false)
const hasMore = computed(() => testcases.value.length < total.value)
const LIST_PAGE_SIZE = 50
const MAX_PAGE_SIZE = 100
// 批量删除接口单次最多 5000 个 id（与后端 max_batch_size 保持一致），超出则分块提交
const BULK_DELETE_CHUNK_SIZE = 5000
const searchText = ref('')
// 由左侧模块树选中的项目驱动，不再单独提供项目筛选器
const projectFilter = ref('')
const priorityFilter = ref('')
const maintainerFilter = ref('')
// 创建日期范围筛选：['YYYY-MM-DD', 'YYYY-MM-DD']
const dateRange = ref(null)
const selectedTestCases = ref([])
// 实际已选条数：表头全选模式下代表"全部匹配用例"（含尚未滚动加载出来的），否则为已勾选行数
const selectedCount = computed(() => (selectAllMode.value ? total.value : selectedTestCases.value.length))
const isDeleting = ref(false)
const importDialogVisible = ref(false)
const isCreatingImport = ref(false)
const selectedImportFile = ref(null)
const importUploadRef = ref(null)
const importForm = ref({
  projectId: ''
})
// 统一的列表查询参数：滚动加载、全选取数、导出都复用，避免筛选条件不一致
const buildListParams = (page, size) => {
  const params = {
    page,
    page_size: size,
    search: searchText.value,
    project: projectFilter.value,
    priority: priorityFilter.value,
    maintainer: maintainerFilter.value
  }
  if (selectedModule.value) {
    params.module = selectedModule.value
    if (includeChildModules.value && selectedModule.value !== 'none') params.include_children = 'true'
  }
  if (Array.isArray(dateRange.value) && dateRange.value.length === 2) {
    params.date_from = dateRange.value[0]
    params.date_to = dateRange.value[1]
  }
  return params
}

const fetchTestCases = async () => {
  loading.value = true
  try {
    const response = await api.get('/testcases/', { params: buildListParams(1, LIST_PAGE_SIZE) })
    testcases.value = response.data.results || []
    total.value = response.data.count || 0
    currentPage.value = 1
    selectAllMode.value = false
    selectedTestCases.value = []
    await nextTick()
    observeLoadMoreSentinel()
  } catch (error) {
    ElMessage.error(t('testcase.fetchListFailed'))
  } finally {
    loading.value = false
  }
}

// 滚动加载下一页，追加到现有列表（不重置已加载数据与勾选）
const loadMore = async () => {
  if (loading.value || loadingMore.value || !hasMore.value) return
  loadingMore.value = true
  const nextPage = currentPage.value + 1
  const keepSelectAll = selectAllMode.value
  try {
    const response = await api.get('/testcases/', { params: buildListParams(nextPage, LIST_PAGE_SIZE) })
    const results = response.data.results || []
    if (!results.length) {
      // 后端已无更多数据，收敛总数避免无限触发
      total.value = testcases.value.length
      return
    }
    if (keepSelectAll) syncingSelection.value = true
    // 原地 push 而非整体替换数组：el-table 在 data 引用变化时会 clearSelection()，
    // 保持同一数组实例才能让已勾选的行不被清空
    testcases.value.push(...results)
    currentPage.value = nextPage
    if (typeof response.data.count === 'number') total.value = response.data.count
    if (keepSelectAll) {
      await nextTick()
      results.forEach(row => tableRef.value?.toggleRowSelection(row, true))
      await nextTick()
      selectedTestCases.value = tableRef.value?.getSelectionRows() || selectedTestCases.value
      selectAllMode.value = true
    }
  } catch (error) {
    ElMessage.error(t('testcase.fetchListFailed'))
  } finally {
    loadingMore.value = false
    syncingSelection.value = false
    // 重新观察：若加载后哨兵仍在可视区域（视口未填满），继续加载下一页
    resetLoadMoreSentinelObserver()
  }
}

const observeLoadMoreSentinel = () => {
  if (sentinelObserver) {
    sentinelObserver.disconnect()
    sentinelObserver = null
  }
  const element = loadMoreSentinel.value
  if (!element || typeof IntersectionObserver === 'undefined') return
  sentinelObserver = new IntersectionObserver((entries) => {
    if (entries.some(entry => entry.isIntersecting)) loadMore()
  })
  sentinelObserver.observe(element)
}

const resetLoadMoreSentinelObserver = () => {
  const element = loadMoreSentinel.value
  if (!sentinelObserver || !element) return
  sentinelObserver.unobserve(element)
  sentinelObserver.observe(element)
}

// 按当前筛选条件拉取全部用例（用于"全选"后执行批量操作/导出）
const fetchAllMatchingTestCases = async () => {
  const allTestCases = []
  let page = 1
  while (true) {
    const { data } = await api.get('/testcases/', { params: buildListParams(page, MAX_PAGE_SIZE) })
    const results = data.results || []
    allTestCases.push(...results)
    if (!results.length || results.length < MAX_PAGE_SIZE || allTestCases.length >= (data.count || 0)) break
    page += 1
  }
  return allTestCases
}

// 批量操作实际作用的对象：全选模式下取全部用例，否则取已勾选的行
const resolveSelectedForAction = async () => {
  if (selectAllMode.value && hasMore.value) return fetchAllMatchingTestCases()
  return selectedTestCases.value
}

onActivated(() => {
  // 已滚动加载多页或处于全选状态时不再自动刷新，避免丢失已加载数据与勾选
  if (selectAllMode.value || selectedTestCases.value.length || testcases.value.length > LIST_PAGE_SIZE) return
  fetchTestCases()
})

const refreshTestCasesOnFocus = () => {
  if (document.visibilityState !== 'visible') return
  if (selectAllMode.value || selectedTestCases.value.length || testcases.value.length > LIST_PAGE_SIZE) return
  fetchTestCases()
}
onMounted(() => window.addEventListener('focus', refreshTestCasesOnFocus))
onUnmounted(() => {
  window.removeEventListener('focus', refreshTestCasesOnFocus)
  sentinelObserver?.disconnect()
  sentinelObserver = null
})

const handleSearch = () => {
  fetchTestCases()
}

const handleFilter = () => {
  fetchTestCases()
}

const moveModuleOptions = computed(() => {
  const addLabels = (nodes) => nodes.map(node => ({ ...node, label: node.name, children: addLabels(node.children || []) }))
  return [{ id: '__root__', label: t('testcase.noParentModule'), children: addLabels(moduleTree.value) }]
})
const loadModuleTree = async () => {
  if (!moduleProjectId.value) { moduleTree.value = []; return }
  try {
    const { data } = await api.get('/testcases/modules/', { params: { project: moduleProjectId.value } })
    moduleTree.value = data
    const [allCases, unassigned] = await Promise.all([
      api.get('/testcases/', { params: { project: moduleProjectId.value, page_size: 1 } }),
      api.get('/testcases/', { params: { project: moduleProjectId.value, module: 'none', page_size: 1 } })
    ])
    allProjectCount.value = allCases.data.count || 0
    unassignedCount.value = unassigned.data.count || 0
  } catch (error) { ElMessage.error(t('testcase.fetchModulesFailed')) }
}
const handleModuleExpand = (module) => {
  if (!expandedModuleKeys.value.includes(module.id)) {
    expandedModuleKeys.value = [...expandedModuleKeys.value, module.id]
  }
}
const handleModuleCollapse = (module) => {
  const collapsedIds = new Set()
  const collectDescendantIds = (node) => {
    collapsedIds.add(node.id)
    ;(node.children || []).forEach(collectDescendantIds)
  }
  collectDescendantIds(module)
  expandedModuleKeys.value = expandedModuleKeys.value.filter(id => !collapsedIds.has(id))
}
const allowModuleDrop = (draggingNode, dropNode, dropType) => {
  return !moduleSearch.value.trim()
    && !isReorderingModules.value
    && ['prev', 'next'].includes(dropType)
    && draggingNode.data.parent_id === dropNode.data.parent_id
}
const findModuleSiblings = (nodes, parentId) => {
  if (parentId === null || parentId === undefined) return nodes
  for (const node of nodes) {
    if (node.id === parentId) return node.children || []
    const children = findModuleSiblings(node.children || [], parentId)
    if (children.length) return children
  }
  return []
}
const handleModuleDrop = async (draggingNode) => {
  const siblings = findModuleSiblings(moduleTree.value, draggingNode.data.parent_id)
  isReorderingModules.value = true
  try {
    await api.post('/testcases/modules/reorder/', {
      project_id: moduleProjectId.value,
      parent_id: draggingNode.data.parent_id,
      module_ids: siblings.map(module => module.id)
    })
    await loadModuleTree()
    ElMessage.success(t('testcase.moduleOrderSaved'))
  } catch (error) {
    await loadModuleTree()
    ElMessage.error(t('testcase.moduleOrderSaveFailed'))
  } finally {
    isReorderingModules.value = false
  }
}
const handleModuleProjectChange = () => {
  rememberSelectedProject(moduleProjectId.value, projects.value)
  selectedModule.value = ''
  moduleTreeRef.value?.setCurrentKey(null)
  projectFilter.value = moduleProjectId.value || ''
  if (!moduleProjectId.value) { allProjectCount.value = 0; unassignedCount.value = 0 }
  loadModuleTree()
  handleFilter()
}
const selectModule = (id) => {
  selectedModule.value = id
  moduleTreeRef.value?.setCurrentKey(id && id !== 'none' ? Number(id) : null)
  projectFilter.value = moduleProjectId.value || ''
  handleFilter()
}
const openModuleDialog = (parent = null) => {
  moduleEditing.value = Boolean(parent?.id && parent?.rename)
  moduleForm.value = { name: moduleEditing.value ? parent.name : '', id: moduleEditing.value ? parent.id : null, parentId: parent?.id || null }
  moduleDialogVisible.value = true
}
const handleModuleCommand = async (command, module) => {
  if (command === 'child') return openModuleDialog(module)
  if (command === 'rename') return openModuleDialog({ ...module, rename: true })
  if (command === 'move') { movingModule.value = module; moveTargetModule.value = module.parent_id || '__root__'; moveDialogVisible.value = true; return }
  if (command === 'delete') {
    try { await ElMessageBox.confirm(t('testcase.deleteModuleConfirm'), t('common.warning')); await deleteTestCaseModuleApi(module.id); await loadModuleTree() }
    catch (error) { if (error !== 'cancel') ElMessage.error(error.response?.data?.detail || t('testcase.moduleDeleteFailed')) }
  }
}
const saveModule = async () => {
  try {
    if (moduleEditing.value) await api.patch(`/testcases/modules/${moduleForm.value.id}/`, { name: moduleForm.value.name })
    else await api.post('/testcases/modules/', { project_id: moduleProjectId.value, parent_id: moduleForm.value.parentId, name: moduleForm.value.name })
    moduleDialogVisible.value = false; await loadModuleTree(); ElMessage.success(t('testcase.moduleSaved'))
  } catch (error) { ElMessage.error(error.response?.data?.name || error.response?.data?.parent_id || t('testcase.moduleSaveFailed')) }
}
const openMoveDialog = () => {
  const projectsSelected = new Set(selectedTestCases.value.map(item => item.project?.id))
  if (projectsSelected.size !== 1) { ElMessage.warning(t('testcase.moveSameProject')); return }
  movingModule.value = null; moduleProjectId.value = [...projectsSelected][0]; loadModuleTree(); moveTargetModule.value = null; moveDialogVisible.value = true
}
const confirmMove = () => movingModule.value ? moveSelectedModule() : moveSelectedCases()
const moveSelectedModule = async () => {
  isMoving.value = true
  try { await api.put(`/testcases/modules/${movingModule.value.id}/`, { parent_id: moveTargetModule.value === '__root__' ? null : moveTargetModule.value }); movingModule.value = null; moveDialogVisible.value = false; await loadModuleTree(); ElMessage.success(t('testcase.moduleMoved')) }
  catch (error) { ElMessage.error(error.response?.data?.parent_id || error.response?.data?.name || t('testcase.moveFailed')) }
  finally { isMoving.value = false }
}
const moveSelectedCases = async () => {
  isMoving.value = true
  try {
    // 全选模式下要移动的是"全部用例"，而不仅是已滚动加载出来的那部分
    const targets = await resolveSelectedForAction()
    await api.post('/testcases/move/', { testcase_ids: targets.map(item => item.id), module_id: moveTargetModule.value === '__root__' ? null : moveTargetModule.value })
    moveDialogVisible.value = false; selectedTestCases.value = []; await loadModuleTree(); await fetchTestCases(); ElMessage.success(t('testcase.casesMoved'))
  }
  catch (error) { ElMessage.error(error.response?.data?.detail || t('testcase.moveFailed')) }
  finally { isMoving.value = false }
}

const goToTestCase = (id) => {
  router.push(`/ai-generation/testcases/${id}`)
}

const goToCreateTestCase = () => {
  const selectedProject = projectFilter.value || moduleProjectId.value || getPreferredProjectId(projects.value)
  router.push({ path: '/ai-generation/testcases/create', query: selectedProject ? { project_id: selectedProject } : {} })
}

const editTestCase = (testcase) => {
  router.push(`/ai-generation/testcases/${testcase.id}/edit`)
}

const deleteTestCase = async (testcase) => {
  try {
    await ElMessageBox.confirm(t('testcase.deleteConfirm'), t('common.warning'), {
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      type: 'warning'
    })
    
    await deleteTestCaseApi(testcase.id)
    ElMessage.success(t('testcase.deleteSuccess'))
    // 原地移除，保持已滚动加载的列表与其余勾选状态不被重置
    const rowIndex = testcases.value.findIndex(item => item.id === testcase.id)
    if (rowIndex > -1) testcases.value.splice(rowIndex, 1)
    selectedTestCases.value = selectedTestCases.value.filter(item => item.id !== testcase.id)
    total.value = Math.max(0, total.value - 1)
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(t('testcase.deleteFailed'))
    }
  }
}

// 处理选择变化
const handleSelectionChange = (selection) => {
  selectedTestCases.value = selection
  // 程序化勾选新加载的行时跳过，避免把"全选"误判成"取消全选"
  if (syncingSelection.value) return
  // 取消掉任意一行即退出全选模式
  if (selection.length < testcases.value.length) selectAllMode.value = false
}

// 表头全选：勾选当前已加载的全部用例，并标记为"全选"，后续滚动加载的行会自动勾选
const handleSelectAll = (selection) => {
  selectAllMode.value = selection.length > 0
}

// 批量删除：调用批量删除接口，一次请求删除多条（超过后端单次上限时按块提交）
const batchDeleteTestCases = async () => {
  if (selectedTestCases.value.length === 0) {
    ElMessage.warning(t('testcase.selectFirst'))
    return
  }

  let loadingInstance = null
  try {
    // 全选模式下删除的是"全部用例"，先取回完整 id 列表再确认
    const targets = await resolveSelectedForAction()
    if (targets.length === 0) {
      ElMessage.warning(t('testcase.selectFirst'))
      return
    }

    await ElMessageBox.confirm(
      t('testcase.batchDeleteConfirm', { count: targets.length }),
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

    if (targets.length > 20) {
      loadingInstance = ElLoading.service({
        lock: true,
        text: t('testcase.batchDeleting', { total: targets.length })
      })
    }

    const ids = targets.map(item => item.id)
    for (let start = 0; start < ids.length; start += BULK_DELETE_CHUNK_SIZE) {
      const chunk = ids.slice(start, start + BULK_DELETE_CHUNK_SIZE)
      try {
        const { data } = await bulkDeleteTestCasesApi(chunk)
        successCount += data?.deleted ?? 0
        failCount += data?.skipped ?? 0
      } catch (error) {
        console.error('Bulk delete chunk failed:', error)
        failCount += chunk.length
      }
    }

    // 显示删除结果
    if (successCount > 0) {
      if (failCount > 0) {
        ElMessage.success(t('testcase.batchDeletePartialSuccess', { successCount, failCount }))
      } else {
        ElMessage.success(t('testcase.batchDeleteSuccess', { successCount }))
      }
    } else {
      ElMessage.error(t('testcase.batchDeleteFailed'))
    }

    // 清空选择并重新加载列表
    selectAllMode.value = false
    selectedTestCases.value = []
    await fetchTestCases()

  } catch (error) {
    if (error !== 'cancel') {
      console.error('Batch delete failed:', error)
      ElMessage.error(t('testcase.batchDeleteError') + ': ' + (error.message || t('common.error')))
    }
  } finally {
    loadingInstance?.close()
    isDeleting.value = false
  }
}

const getPriorityText = (priority) => {
  const textMap = {
    low: t('testcase.low'),
    medium: t('testcase.medium'),
    high: t('testcase.high'),
    critical: t('testcase.critical')
  }
  return textMap[priority] || priority
}

const getTypeText = (type) => {
  const textMap = {
    functional: t('testcase.functional'),
    integration: t('testcase.integration'),
    api: t('testcase.api'),
    ui: t('testcase.ui'),
    performance: t('testcase.performance'),
    security: t('testcase.security')
  }
  return textMap[type] || '-'
}

const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm')
}

// 将HTML的<br>标签转换为换行符（用于Excel导出）
const convertBrToNewline = (text) => {
  if (!text) return ''
  return text.replace(/<br\s*\/?>/gi, '\n')
}

// 将富文本备注转换为纯文本，便于写入 Excel 单元格。
const getNotesText = (html) => {
  if (!html) return ''
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const readNode = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || ''
    if (node.nodeType !== Node.ELEMENT_NODE) return ''
    if (node.tagName === 'BR') return '\n'
    if (node.tagName === 'IMG') return '[图片]'

    const content = [...node.childNodes].map(readNode).join('')
    return ['P', 'H2', 'H3', 'LI'].includes(node.tagName) ? `${content}\n` : content
  }
  return readNode(parsed.body).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

// 根据模块树还原每条用例的完整模块路径，导出时按实际最大层级生成列。
const getExportModulePaths = async (testCases) => {
  const projectIds = [...new Set(testCases
    .filter(testcase => testcase.module)
    .map(testcase => testcase.project?.id || testcase.module.project_id)
    .filter(Boolean))]
  const modulePaths = new Map()

  await Promise.all(projectIds.map(async (projectId) => {
    const { data } = await api.get('/testcases/modules/', { params: { project: projectId } })
    const collectPaths = (modules, parentPath = []) => {
      modules.forEach((module) => {
        const path = [...parentPath, module.name]
        modulePaths.set(`${projectId}:${module.id}`, path)
        collectPaths(module.children || [], path)
      })
    }
    collectPaths(data)
  }))

  return testCases.map((testcase) => {
    const projectId = testcase.project?.id || testcase.module?.project_id
    const key = `${projectId}:${testcase.module?.id}`
    return testcase.module ? (modulePaths.get(key) || [testcase.module.name]) : []
  })
}

const exportToExcel = async () => {
  try {
    loading.value = true

    // 确定要导出的数据：全选模式或未勾选时，导出当前筛选条件下的全部用例
    const useSelection = selectedTestCases.value.length > 0 && !selectAllMode.value
    const testCasesToExport = useSelection
      ? selectedTestCases.value
      : await fetchAllMatchingTestCases()

    if (testCasesToExport.length === 0) {
      ElMessage.warning(t('testcase.noDataToExport'))
      loading.value = false
      return
    }

    // 创建工作簿
    const workbook = XLSX.utils.book_new()
    const modulePaths = await getExportModulePaths(testCasesToExport)
    const moduleLevelLabels = ['一', '二', '三', '四', '五', '六', '七', '八']
    const moduleLevelCount = Math.max(0, ...modulePaths.map(path => path.length))
    const moduleHeaders = Array.from({ length: moduleLevelCount }, (_, index) => (
      t('testcase.excelModuleLevel', {
        level: locale.value.startsWith('zh') ? moduleLevelLabels[index] : index + 1
      })
    ))

    // 准备Excel数据
    const worksheetData = [
      [t('testcase.excelNumber'), ...moduleHeaders, t('testcase.excelTitle'), t('testcase.excelPreconditions'), t('testcase.excelSteps'), t('testcase.excelExpectedResult'), t('testcase.excelPriority'), t('testcase.excelTestType'), t('testcase.excelAuthor'), t('testcase.excelCreatedAt'), t('testcase.updatedAt'), t('testcase.excelNotes')]
    ]

    testCasesToExport.forEach((testcase, index) => {
      worksheetData.push([
        testcase.id,
        ...Array.from({ length: moduleLevelCount }, (_, level) => modulePaths[index][level] || ''),
        testcase.title || '',
        convertBrToNewline(testcase.preconditions || ''),
        convertBrToNewline(testcase.steps || ''),
        convertBrToNewline(testcase.expected_result || ''),
        getPriorityText(testcase.priority),
        getTypeText(testcase.test_type),
        testcase.author?.username || '',
        formatDate(testcase.created_at),
        formatDate(testcase.updated_at),
        getNotesText(testcase.notes)
      ])
    })
    
    // 创建工作表
    const worksheet = XLSX.utils.aoa_to_sheet(worksheetData)
    
    // 设置列宽
    const colWidths = [
      { wch: 12 }, // Test case ID
      ...Array.from({ length: moduleLevelCount }, () => ({ wch: 24 })), // Module hierarchy
      { wch: 30 }, // Case title
      { wch: 30 }, // Preconditions
      { wch: 40 }, // Steps
      { wch: 30 }, // Expected result
      { wch: 10 }, // Priority
      { wch: 15 }, // Test type
      { wch: 15 }, // Author
      { wch: 20 }, // Created at
      { wch: 20 }, // Updated at
      { wch: 30 }  // Notes
    ]
    worksheet['!cols'] = colWidths
    
    // 设置表头样式
    for (let col = 0; col < worksheetData[0].length; col++) {
      const cellAddress = XLSX.utils.encode_cell({ r: 0, c: col })
      if (!worksheet[cellAddress]) continue
      worksheet[cellAddress].s = {
        font: { bold: true },
        alignment: { horizontal: 'center', vertical: 'center', wrapText: true }
      }
    }
    
    // 设置其他行的样式
    for (let row = 1; row < worksheetData.length; row++) {
      for (let col = 0; col < worksheetData[row].length; col++) {
        const cellAddress = XLSX.utils.encode_cell({ r: row, c: col })
        if (worksheet[cellAddress]) {
          worksheet[cellAddress].s = {
            alignment: { vertical: 'top', wrapText: true }
          }
        }
      }
    }

    // Add worksheet to workbook
    XLSX.utils.book_append_sheet(workbook, worksheet, t('testcase.excelSheetName'))

    // Generate filename
    const fileName = t('testcase.excelFileName', { date: new Date().toISOString().slice(0, 10) })

    // Export file
    XLSX.writeFile(workbook, fileName)

    ElMessage.success(t('testcase.exportSuccess'))
  } catch (error) {
    console.error('Export test cases failed:', error)
    ElMessage.error(t('testcase.exportFailed') + ': ' + (error.message || t('common.error')))
  } finally {
    loading.value = false
  }
}

const downloadBlob = (blob, fileName) => {
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}

const downloadImportTemplate = async () => {
  try {
    const response = await api.get('/testcases/import/template/', {
      responseType: 'blob'
    })
    downloadBlob(response.data, 'testcase_import_template.xlsx')
    ElMessage.success(t('testcase.downloadTemplateSuccess'))
  } catch (error) {
    console.error('Download import template failed:', error)
    ElMessage.error(t('testcase.downloadTemplateFailed'))
  }
}

const openImportDialog = () => {
  importForm.value.projectId = projectFilter.value || ''
  selectedImportFile.value = null
  importUploadRef.value?.clearFiles()
  importDialogVisible.value = true
}

const beforeImportUpload = (file) => {
  const isXlsx = file.name.toLowerCase().endsWith('.xlsx')
  if (!isXlsx) {
    ElMessage.error(t('testcase.invalidImportFile'))
  }
  return isXlsx
}

const handleImportFileChange = (uploadFile) => {
  if (uploadFile?.raw && beforeImportUpload(uploadFile.raw)) {
    selectedImportFile.value = uploadFile.raw
  } else if (uploadFile?.raw) {
    selectedImportFile.value = null
    importUploadRef.value?.clearFiles()
  }
}

const handleImportFileExceed = (files) => {
  const file = files[0]
  // Replace the hidden upload entry so the next file selection can be accepted.
  importUploadRef.value?.clearFiles()
  if (file && beforeImportUpload(file)) {
    selectedImportFile.value = file
  } else {
    selectedImportFile.value = null
  }
}

const submitImport = async () => {
  if (!importForm.value.projectId) {
    ElMessage.warning(t('testcase.importProjectRequired'))
    return
  }
  if (!selectedImportFile.value) {
    ElMessage.warning(t('testcase.importFileRequired'))
    return
  }

  const formData = new FormData()
  formData.append('project_id', importForm.value.projectId)
  formData.append('file', selectedImportFile.value)

  isCreatingImport.value = true
  try {
    await api.post('/testcases/import-records/', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
    ElMessage.success(t('testcase.importCreated'))
    importDialogVisible.value = false
    goToImportRecords()
  } catch (error) {
    console.error('Create import record failed:', error)
    ElMessage.error(error.response?.data?.error || t('testcase.importCreateFailed'))
  } finally {
    isCreatingImport.value = false
  }
}

const goToImportRecords = () => {
  router.push('/ai-generation/testcases/import-records')
}

const fetchProjects = async () => {
  try {
    const response = await api.get('/projects/')
    projects.value = response.data.results || response.data || []
  } catch (error) {
    ElMessage.error(t('testcase.fetchProjectsFailed'))
  }
}

const fetchMaintainers = async () => {
  const memberMap = new Map()
  // 维护人候选来自当前用户可访问项目的成员集合，去重后供筛选使用。
  const responses = await Promise.allSettled(
    projects.value.map(project => api.get(`/projects/${project.id}/members/`))
  )
  responses.forEach(result => {
    if (result.status !== 'fulfilled') return
    ;(result.value.data || []).forEach(member => memberMap.set(member.id, member))
  })
  maintainers.value = [...memberMap.values()].sort((left, right) =>
    (left.username || '').localeCompare(right.username || '')
  )
}

onMounted(async () => {
  await fetchProjects()
  const preferredProjectId = getPreferredProjectId(projects.value)
  moduleProjectId.value = preferredProjectId ?? (projects.value[0]?.id ?? '')
  if (moduleProjectId.value) rememberSelectedProject(moduleProjectId.value, projects.value)
  if (preferredProjectId) projectFilter.value = preferredProjectId
  await loadModuleTree()
  await fetchMaintainers()
  fetchTestCases()
})
</script>

<style lang="scss" scoped>
.page-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 0;
  box-sizing: border-box;
  overflow: hidden;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  flex-shrink: 0;
}

.page-title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: #303133;
}

.header-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.card-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  background: #fff;
  border-radius: 4px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.resizable-split-pane { flex: 1; min-height: 0; }
.module-sidebar { width: 100%; height: 100%; display: flex; flex-direction: column; overflow: hidden; border-right: 1px solid #e4e7ed; background: #fff; box-shadow: 2px 0 6px rgba(0, 0, 0, 0.05); }
.sidebar-header { display: flex; flex-direction: column; gap: 12px; padding: 16px; border-bottom: 1px solid #e4e7ed; background: #fff; }
.sidebar-title-row { display: flex; align-items: center; gap: 8px; }
.sidebar-title-row .el-input { flex: 1; min-width: 0; }
.sidebar-title-row .el-button { flex: 0 0 36px; width: 36px; }
.collection-tree { flex: 1; min-width: 0; overflow: auto; padding: 10px; }
.collection-tree :deep(.el-tree-node__content) { min-width: 0; overflow: hidden; }
.collection-tree :deep(.el-tree-node__content > .el-tree-node__label) { display: none; }
.tree-node { position: relative; display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; overflow: hidden; padding: 4px 0; }
.tree-node > .el-icon { flex-shrink: 0; color: #606266; font-size: 16px; }
.node-label { flex: 1; min-width: 0; overflow: hidden; padding-right: 32px; color: #303133; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; transition: color 0.2s; }
.tree-node:hover .node-label, .tree-node.is-active .node-label { color: #409eff; }
.node-actions { position: absolute; right: 26px; display: none; flex-shrink: 0; align-items: center; gap: 2px; }
.tree-node:hover .node-actions { display: flex; }
.node-actions .el-button { padding: 2px; color: #909399; }
.node-actions .el-button:hover { color: #409eff; }
.tree-entry { position: relative; display: flex; align-items: center; gap: 8px; min-width: 0; padding: 7px 8px; border-radius: 3px; cursor: pointer; color: #303133; }
.tree-entry.root-module-level { padding-left: 24px; }
.tree-entry:hover, .tree-entry.active { color: #409eff; background: #ecf5ff; }
.tree-entry-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-count { position: absolute; right: 8px; flex-shrink: 0; color: #909399; font-size: 12px; text-align: right; }
.filter-bar {
  padding: 0px 0px 10px 0px;
  border-bottom: 1px solid #ebeef5;
  flex-shrink: 0;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;

  // el-date-picker 根节点不携带 scoped 属性，需用 :deep 才能覆盖 Element 的 flex-grow
  :deep(.filter-search) {
    width: 200px;
    flex: 0 0 200px;
  }

  :deep(.filter-select) {
    width: 160px;
    flex: 0 0 160px;
  }

  :deep(.filter-date) {
    width: 230px;
    flex: 0 0 230px;
  }
}

.table-container {
  flex: 1;
  overflow: hidden;
  
  :deep(.el-table) {
    height: 100% !important;
  }
  
  :deep(.el-table__body-wrapper) {
    overflow-y: auto !important;
  }
}

.case-title {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-height: 20px;
  overflow-wrap: anywhere;
  cursor: pointer;
}

.row-actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
}

.list-summary {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  padding: 10px;
  border-bottom: 1px solid #ebeef5;
  font-size: 14px;
  color: #303133;
}

.summary-text {
  color: #303133;
}

.load-more-sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 40px;
  font-size: 13px;
  color: #909399;
}

.import-alert {
  margin-bottom: 20px;
}

.import-upload {
  width: 100%;

  :deep(.el-upload),
  :deep(.el-upload-dragger) {
    width: 100%;
  }
}

.priority-tag {
  &.low { color: #67c23a; }
  &.medium { color: #e6a23c; }
  &.high { color: #f56c6c; }
  &.critical { color: #f56c6c; font-weight: bold; }
}

@media (max-width: 1200px) {
  .card-container {
    min-height: 0;
  }
  
  .table-container {
    min-height: 0;
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
  }
  
  .header-actions {
    width: 100%;
  }
  
  .filter-bar {
    padding: 10px 15px;
  }
  
}

.step-content {
  min-height: 200px;
}

.preview-info {
  padding: 15px;
  background-color: #f5f7fa;
  border-radius: 4px;

  p {
    margin: 5px 0;
  }
}
</style>
