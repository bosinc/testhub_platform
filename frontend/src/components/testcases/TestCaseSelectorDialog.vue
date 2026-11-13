<template>
  <el-dialog
    :model-value="modelValue"
    :title="title || $t('execution.testcaseSelectorTitle')"
    width="min(1300px, 96vw)"
    class="testcase-selector-dialog"
    align-center
    :close-on-click-modal="false"
    @update:model-value="value => emit('update:modelValue', value)">
    <ResizableSplitPane
      v-model="sidebarWidth"
      :min-size="200"
      :max-size="380"
      :second-min-size="520"
      storage-key="execution-testcase-selector-sidebar-width"
      class="testcase-selector-container">
      <template #first>
        <aside class="testcase-library-panel">
          <div class="testcase-library-header">
            <strong>{{ $t('execution.testcaseLibrary') }}</strong>
            <el-input v-model="librarySearch" size="small" clearable :placeholder="$t('testcase.searchModules')">
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </div>
          <div class="testcase-library-tree">
            <div
              class="testcase-library-node tree-root"
              :class="{ active: librarySelection.id === '__all__' }"
              @click="selectLibraryNode({ id: '__all__', type: 'all' })">
              <el-icon><FolderOpened /></el-icon>
              <span class="testcase-library-node-name">{{ $t('testcase.allCases') }}</span>
              <span class="testcase-library-count">{{ cases.length }}</span>
            </div>
            <el-tree
              :data="filteredLibraryTree"
              :props="{ label: 'label', children: 'children' }"
              node-key="id"
              default-expand-all
              :expand-on-click-node="false"
              @node-click="selectLibraryNode">
              <template #default="{ node, data }">
                <div class="testcase-library-node" :class="{ active: librarySelection.id === data.id }">
                  <el-icon>
                    <component :is="data.type === 'project' ? FolderOpened : Folder" />
                  </el-icon>
                  <span class="testcase-library-node-name" :title="node.label">{{ node.label }}</span>
                  <span class="testcase-library-count">{{ data.count }}</span>
                </div>
              </template>
            </el-tree>
            <el-empty
              v-if="librarySearch.trim() && !filteredLibraryTree.length"
              :description="$t('testcase.noModulesFound')"
              :image-size="60" />
          </div>
        </aside>
      </template>
      <template #second>
        <section class="testcase-library-list">
          <div class="testcase-search-bar">
            <el-form :inline="true">
              <el-form-item :label="$t('execution.testcaseKeyword')">
                <el-input
                  v-model="filters.keyword"
                  :placeholder="$t('execution.testcaseKeywordPlaceholder')"
                  clearable
                  style="width: 180px" />
              </el-form-item>
              <el-form-item :label="$t('execution.testcasePriority')">
                <el-select v-model="filters.priority" clearable style="width: 130px">
                  <el-option :label="$t('execution.allPriorities')" value="" />
                  <el-option :label="$t('testcase.low')" value="low" />
                  <el-option :label="$t('testcase.medium')" value="medium" />
                  <el-option :label="$t('testcase.high')" value="high" />
                  <el-option :label="$t('testcase.critical')" value="critical" />
                </el-select>
              </el-form-item>
              <el-form-item :label="$t('execution.testcaseCreatedDate')">
                <el-date-picker
                  v-model="filters.dateRange"
                  type="daterange"
                  value-format="YYYY-MM-DD"
                  :start-placeholder="$t('execution.dateStart')"
                  :end-placeholder="$t('execution.dateEnd')"
                  unlink-panels
                  clearable
                  style="width: 250px" />
              </el-form-item>
              <el-form-item>
                <el-button @click="resetFilters">{{ $t('execution.resetSearch') }}</el-button>
              </el-form-item>
            </el-form>
          </div>
          <div class="testcase-selector-table">
            <el-table
              ref="tableRef"
              v-loading="loading"
              :data="pageRows"
              row-key="id"
              height="100%"
              @selection-change="handleSelectionChange">
              <el-table-column type="selection" width="50" />
              <el-table-column :label="$t('execution.testcaseCode')" width="60">
                <template #default="{ row }">{{ row.id }}</template>
              </el-table-column>
              <el-table-column prop="title" :label="$t('execution.testcaseTitle')" min-width="260" show-overflow-tooltip />
              <el-table-column prop="priority" :label="$t('execution.testcasePriority')" width="120">
                <template #default="{ row }">
                  <el-tag :class="`priority-tag ${row.priority}`">{{ getPriorityText(row.priority) }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="PAGE_SIZE"
            :total="selectorList.length"
            layout="total, prev, pager, next"
            class="testcase-selector-pagination" />
        </section>
      </template>
    </ResizableSplitPane>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ $t('common.cancel') }}</el-button>
      <el-button type="primary" @click="confirm">
        {{ $t('execution.confirmSelectTestcases', { count: tempSelectedIds.length }) }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Search, Folder, FolderOpened } from '@element-plus/icons-vue'
import ResizableSplitPane from '@/components/ResizableSplitPane.vue'
import api from '@/utils/api'
import { buildTestCaseLibraryTree, updatePageSelection } from '@/utils/testCaseSelector.mjs'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 关联项目：[{ id, name }]
  projects: { type: Array, default: () => [] },
  // 已选用例 ID
  selectedIds: { type: Array, default: () => [] },
  // 已选用例对象，用于补全不在候选列表中的用例（如已废弃用例）
  selectedCases: { type: Array, default: () => [] },
  title: { type: String, default: '' }
})

const emit = defineEmits(['update:modelValue', 'confirm'])

const { t } = useI18n()

const loading = ref(false)
const cases = ref([])
const moduleTrees = ref([])
const librarySearch = ref('')
const librarySelection = ref({ id: '__all__', type: 'all' })
const tempSelectedIds = ref([])
const tableRef = ref(null)
const sidebarWidth = ref(250)
const filters = reactive({ keyword: '', priority: '', dateRange: [] })
let syncingSelection = false
let selectionSyncVersion = 0
// 候选集保留在内存中用于树计数和筛选，表格只渲染当前页，避免数千行阻塞弹窗。
const PAGE_SIZE = 50
const currentPage = ref(1)

// 按用例创建日期过滤（含首尾两天）
const matchCreatedRange = (createdAt) => {
  const [start, end] = filters.dateRange || []
  if (!start || !end) return true
  if (!createdAt) return false
  const created = new Date(createdAt).getTime()
  if (Number.isNaN(created)) return false
  const from = new Date(`${start}T00:00:00`).getTime()
  const to = new Date(`${end}T23:59:59.999`).getTime()
  return created >= from && created <= to
}

const selectorList = computed(() => {
  let scoped = cases.value
  const selection = librarySelection.value
  if (selection.type === 'project') {
    scoped = scoped.filter(item => String(item.project_id) === String(selection.projectId))
  } else if (selection.type === 'module') {
    const moduleIds = new Set((selection.moduleIds || []).map(String))
    scoped = scoped.filter(item => moduleIds.has(String(item.module_id)))
  } else if (selection.type === 'unassigned') {
    scoped = scoped.filter(item => String(item.project_id) === String(selection.projectId) && !item.module_id)
  }

  return scoped.filter(item => {
    const keyword = filters.keyword.trim().toLowerCase()
    const matchKeyword = !keyword || item.title?.toLowerCase().includes(keyword)
    const matchPriority = !filters.priority || item.priority === filters.priority
    return matchKeyword && matchPriority && matchCreatedRange(item.created_at)
  })
})

const pageRows = computed(() => selectorList.value.slice(
  (currentPage.value - 1) * PAGE_SIZE, currentPage.value * PAGE_SIZE
))

const filteredLibraryTree = computed(() => {
  const keyword = librarySearch.value.trim().toLowerCase()
  if (!keyword) return moduleTrees.value

  const filterModules = (nodes) => nodes.reduce((matches, item) => {
    const children = filterModules(item.children || [])
    if (item.label.toLowerCase().includes(keyword) || children.length) {
      matches.push({ ...item, children })
    }
    return matches
  }, [])

  return moduleTrees.value
    .map(project => ({ ...project, children: filterModules(project.children) }))
    .filter(project => project.children.length)
})

const findModuleNode = (nodes, id) => {
  for (const node of nodes) {
    if (node.id === id) return node
    const found = findModuleNode(node.children || [], id)
    if (found) return found
  }
  return null
}

const selectLibraryNode = (data) => {
  const selectedNode = data.type === 'module' ? findModuleNode(moduleTrees.value, data.id) : null
  librarySelection.value = {
    id: data.type === 'all' ? '__all__' : data.id,
    type: data.type,
    projectId: data.projectId,
    moduleIds: selectedNode?.moduleIds || []
  }
}

const syncTableSelection = async () => {
  const version = ++selectionSyncVersion
  // 数据切页时 el-table 会先清空旧选中项，提前拦截这次 selection-change。
  syncingSelection = true
  await nextTick()
  try {
    if (!tableRef.value || version !== selectionSyncVersion) return
    tableRef.value.clearSelection()
    const selectedSet = new Set(tempSelectedIds.value)
    pageRows.value.forEach(item => {
      if (selectedSet.has(item.id)) tableRef.value.toggleRowSelection(item, true)
    })
    await nextTick()
  } finally {
    if (version === selectionSyncVersion) syncingSelection = false
  }
}

const handleSelectionChange = (selection) => {
  if (syncingSelection) return
  tempSelectedIds.value = updatePageSelection(tempSelectedIds.value, pageRows.value, selection)
}

// 同一会话内按项目缓存候选用例与模块树：再次打开秒开，避免每次闪一次 loading
const CACHE_TTL = 60 * 1000
const dataCache = new Map()
let loadedKey = ''

const projectKey = () => props.projects.map(project => project.id).sort((a, b) => a - b).join(',')

const applyData = async (rows, modulesByProject) => {
  const rowMap = new Map(rows.map(row => [row.id, row]))
  // 已关联但不在候选中的用例保留下来，避免保存时被误删
  props.selectedCases.forEach(item => {
    if (!rowMap.has(item.id)) rowMap.set(item.id, item)
  })
  cases.value = [...rowMap.values()]
  moduleTrees.value = buildTestCaseLibraryTree(props.projects, modulesByProject, cases.value, t('testcase.noModule'))
  currentPage.value = 1
  librarySelection.value = { id: '__all__', type: 'all' }
  librarySearch.value = ''
  filters.keyword = ''
  filters.priority = ''
  filters.dateRange = []
  tempSelectedIds.value = [...props.selectedIds]
  await syncTableSelection()
}

const loadData = async () => {
  if (!props.projects.length) {
    ElMessage.warning(t('execution.linkNoProject'))
    emit('update:modelValue', false)
    return
  }

  const key = projectKey()
  const cached = dataCache.get(key)
  if (cached && Date.now() - cached.at < CACHE_TTL) {
    await applyData(cached.rows, cached.modulesByProject)
    loadedKey = key
    return
  }

  // 换了一批项目才清空旧列表并显示 loading；同项目刷新静默进行，列表不闪
  if (loadedKey !== key) {
    cases.value = []
    moduleTrees.value = []
    tempSelectedIds.value = []
    loading.value = true
  }
  try {
    const params = new URLSearchParams()
    props.projects.forEach(project => params.append('project_ids', project.id))
    const [testcaseRes, moduleResponses] = await Promise.all([
      api.get(`/testcases/by-projects/?${params.toString()}`),
      Promise.all(props.projects.map(project =>
        api.get('/testcases/modules/', { params: { project: project.id } }).catch(() => ({ data: [] }))
      ))
    ])
    const rows = testcaseRes.data.results || []
    const modulesByProject = Object.fromEntries(
      props.projects.map((project, index) => [project.id, moduleResponses[index].data || []])
    )
    dataCache.set(key, { rows, modulesByProject, at: Date.now() })
    await applyData(rows, modulesByProject)
    loadedKey = key
  } catch (error) {
    ElMessage.error(t('execution.fetchTestcasesFailed'))
  } finally {
    loading.value = false
  }
}

const resetFilters = async () => {
  filters.keyword = ''
  filters.priority = ''
  filters.dateRange = []
  await syncTableSelection()
}

const confirm = () => {
  emit('confirm', [...new Set(tempSelectedIds.value)])
  emit('update:modelValue', false)
}

const getPriorityText = (priority) => {
  const map = {
    low: t('testcase.low'),
    medium: t('testcase.medium'),
    high: t('testcase.high'),
    critical: t('testcase.critical')
  }
  return map[priority] || '-'
}

watch(() => props.modelValue, (visible) => {
  if (visible) loadData()
})

// 切换目录或筛选条件回到第一页；跨页选择以 tempSelectedIds 为准进行回显。
watch(selectorList, () => { currentPage.value = 1 })
watch(pageRows, syncTableSelection)
</script>

<style scoped>
.testcase-selector-container {
  height: min(68vh, 620px);
  min-height: 420px;
  border: 1px solid #e4e7ed;
  overflow: hidden;
}

.testcase-library-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}

.testcase-library-header {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px;
  border-bottom: 1px solid #e4e7ed;
}

.testcase-library-tree {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px;
}

.testcase-library-node {
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
  height: 32px;
  gap: 8px;
  border-radius: 4px;
  color: #606266;
  cursor: pointer;
}

.testcase-library-node.active {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.testcase-library-node-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.testcase-library-count {
  flex: 0 0 auto;
  min-width: 24px;
  text-align: right;
  color: #909399;
  font-size: 12px;
}

.testcase-library-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  padding: 12px;
}

.testcase-search-bar {
  flex: 0 0 auto;
  min-width: 0;
  padding: 0 10px;
  background: #f8f9fa;
  border-radius: 4px;
}

.testcase-search-bar :deep(.el-form--inline) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.testcase-search-bar :deep(.el-form-item) {
  flex: 0 0 auto;
  margin-right: 0;
  margin-bottom: 10px;
}

.testcase-search-bar :deep(.el-form-item__content) {
  flex-wrap: nowrap;
}

.testcase-selector-table {
  flex: 1;
  min-height: 0;
  padding-top: 10px;
}

.testcase-selector-table :deep(.el-table) {
  height: 100%;
}

.testcase-selector-pagination {
  flex: 0 0 auto;
  justify-content: flex-end;
  padding-top: 10px;
}

:global(.testcase-selector-dialog .el-dialog__body) {
  padding-top: 10px;
  padding-bottom: 10px;
}

.priority-tag.low { color: #67c23a; }
.priority-tag.medium { color: #e6a23c; }
.priority-tag.high { color: #f56c6c; }
.priority-tag.critical { color: #c45656; }

@media (max-width: 760px) {
  .testcase-selector-container {
    height: 70vh;
    min-height: 0;
  }

  .testcase-library-list {
    overflow-x: auto;
  }
}
</style>
