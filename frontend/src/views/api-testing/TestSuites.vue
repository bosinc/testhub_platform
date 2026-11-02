<template>
  <div class="test-suites-page">
    <div class="page-header">
      <h2>{{ $t('apiTesting.testSuiteManagement.title') }}</h2>
      <el-select
        v-model="selectedProject"
        class="project-select"
        filterable
        :placeholder="$t('apiTesting.testSuiteManagement.projectPlaceholder')"
        @change="loadTestSuites"
      >
        <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
      </el-select>
      <el-button type="primary" :icon="Plus" @click="openCreateSuite">{{ $t('apiTesting.testSuiteManagement.create') }}</el-button>
    </div>

    <el-card class="table-card" shadow="never">
      <el-input
        v-model="searchText"
        class="search-input"
        clearable
        :placeholder="$t('apiTesting.testSuiteManagement.searchPlaceholder')"
        :prefix-icon="Search"
      />

      <el-table v-loading="loading" :data="pagedSuites" style="width: 100%" :empty-text="$t('apiTesting.testSuiteManagement.empty')">
        <el-table-column prop="name" :label="$t('apiTesting.testSuiteManagement.suiteName')" min-width="100">
          <template #default="{ row }"><el-link type="primary" @click="editSuite(row)">{{ row.name }}</el-link></template>
        </el-table-column>
        <el-table-column :label="$t('apiTesting.testSuiteManagement.includedCases')" width="130" align="center">
          <template #default="{ row }">{{ row.scenario_cases?.length || 0 }}</template>
        </el-table-column>
        <el-table-column prop="created_at" :label="$t('apiTesting.testSuiteManagement.createdAt')" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" :label="$t('apiTesting.testSuiteManagement.updatedAt')" width="160">
          <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column :label="$t('apiTesting.testSuiteManagement.operation')" width="230" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" :icon="Edit" @click="editSuite(row)">{{ $t('apiTesting.common.edit') }}</el-button>
              <el-button link type="success" :icon="VideoPlay" :loading="runningSuiteId === row.id" @click="runSuite(row)">{{ $t('apiTesting.testSuiteManagement.run') }}</el-button>
              <el-button link type="danger" :icon="Delete" @click="deleteSuite(row)">{{ $t('apiTesting.common.delete') }}</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        class="pagination"
        :page-sizes="[10, 20, 50, 100]"
        :total="filteredSuites.length"
        layout="total, sizes, prev, pager, next, jumper"
      />
    </el-card>

    <el-dialog
      v-model="dialogVisible"
      :title="editingSuite ? $t('apiTesting.testSuiteManagement.edit') : $t('apiTesting.testSuiteManagement.createTitle')"
      width="820px"
      :close-on-click-modal="false"
      @closed="resetDialog"
    >
      <el-form :model="form" label-width="80px">
        <el-form-item :label="$t('apiTesting.testSuiteManagement.nameLabel')" required>
          <el-input v-model="form.name" maxlength="200" :placeholder="$t('apiTesting.testSuiteManagement.namePlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('apiTesting.testSuiteManagement.descriptionLabel')">
          <el-input v-model="form.description" type="textarea" :rows="2" :placeholder="$t('apiTesting.testSuiteManagement.descriptionPlaceholder')" />
        </el-form-item>
      </el-form>

      <div class="scenario-picker">
        <div class="picker-header">
          <span>{{ $t('apiTesting.testSuiteManagement.selectScenarios', { count: checkedScenarioCount }) }}</span>
          <el-input v-model="scenarioSearch" size="small" clearable :placeholder="$t('apiTesting.testSuiteManagement.scenarioSearchPlaceholder')" :prefix-icon="Search" />
        </div>
        <el-scrollbar height="320px">
          <el-tree
            ref="scenarioTreeRef"
            v-loading="scenariosLoading"
            :data="scenarioTree"
            :props="{ label: 'name', children: 'children' }"
            :filter-node-method="filterScenarioNode"
            node-key="id"
            show-checkbox
            default-expand-all
            check-on-click-node
          >
            <template #default="{ data }">
              <span class="tree-node-label">
                <el-icon><Folder v-if="data.type === 'folder'" /><Document v-else /></el-icon>
                <span>{{ data.name }}</span>
              </span>
            </template>
          </el-tree>
        </el-scrollbar>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">{{ $t('apiTesting.common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="saveSuite">{{ $t('apiTesting.common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Document, Edit, Folder, Plus, Search, VideoPlay } from '@element-plus/icons-vue'
import api from '@/utils/api'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'

const testSuites = ref([])
const scenarios = ref([])
const projects = ref([])
const selectedProject = ref(null)
useRememberedProject(selectedProject, projects)
const loading = ref(false)
const scenariosLoading = ref(false)
const saving = ref(false)
const runningSuiteId = ref(null)
const dialogVisible = ref(false)
const editingSuite = ref(null)
const searchText = ref('')
const scenarioSearch = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const scenarioTreeRef = ref(null)
const form = ref(emptyForm())
const { t } = useI18n()

function emptyForm() {
  return { name: '', description: '', project: null, scenario_case_ids: [] }
}

const filteredSuites = computed(() => {
  const query = searchText.value.trim().toLowerCase()
  return testSuites.value.filter((item) => !query ||
    item.name.toLowerCase().includes(query) || (item.description || '').toLowerCase().includes(query))
})

const pagedSuites = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredSuites.value.slice(start, start + pageSize.value)
})

const scenarioTree = computed(() => {
  const root = []
  const folders = new Map()

  for (const scenario of scenarios.value) {
    const parts = (scenario.folder || t('apiTesting.testSuiteManagement.ungrouped')).split('/').filter(Boolean)
    let branch = root
    let path = ''
    for (const part of parts) {
      path = path ? `${path}/${part}` : part
      let folder = folders.get(path)
      if (!folder) {
        folder = { id: `folder:${path}`, name: part, type: 'folder', children: [] }
        folders.set(path, folder)
        branch.push(folder)
      }
      branch = folder.children
    }
    branch.push({ id: scenario.id, name: scenario.name, type: 'scenario', scenario })
  }
  return root
})

const checkedScenarioCount = computed(() => scenarioTreeRef.value?.getCheckedNodes(true, false).length || 0)

watch(scenarioSearch, (value) => scenarioTreeRef.value?.filter(value))
watch(searchText, () => { currentPage.value = 1 })

function filterScenarioNode(query, data) {
  if (!query) return true
  if (data.type === 'folder') return true
  return data.name.toLowerCase().includes(query.toLowerCase())
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '-'
}

async function fetchAllPages(url, params = {}) {
  const results = []
  let page = 1
  while (true) {
    const { data } = await api.get(url, { params: { ...params, page, page_size: 1500 } })
    if (Array.isArray(data)) return data
    results.push(...(data.results || []))
    if (!data.next) return results
    page += 1
  }
}

async function loadProjects() {
  try {
    projects.value = (await fetchAllPages('/api-testing/projects/'))
      .filter((project) => project.project_type !== 'WEBSOCKET')
    if (projects.value.length) {
      selectedProject.value = getPreferredProjectId(projects.value, projects.value[0].id)
      await loadTestSuites()
    }
  } catch {
    ElMessage.error(t('apiTesting.testSuiteManagement.loadProjectsFailed'))
  }
}

async function loadTestSuites() {
  if (!selectedProject.value) {
    testSuites.value = []
    return
  }
  loading.value = true
  try {
    testSuites.value = await fetchAllPages('/api-testing/test-suites/', { project: selectedProject.value })
    currentPage.value = 1
  } catch {
    ElMessage.error(t('apiTesting.testSuiteManagement.loadSuitesFailed'))
  } finally {
    loading.value = false
  }
}

async function loadScenarios(projectId = selectedProject.value) {
  if (!projectId) {
    scenarios.value = []
    return
  }
  scenariosLoading.value = true
  try {
    scenarios.value = await fetchAllPages('/api-testing/scenario-cases/', { project: projectId })
  } catch {
    ElMessage.error(t('apiTesting.testSuiteManagement.loadScenariosFailed'))
  } finally {
    scenariosLoading.value = false
  }
}

async function openCreateSuite() {
  editingSuite.value = null
  form.value = { ...emptyForm(), project: selectedProject.value }
  scenarioSearch.value = ''
  await loadScenarios()
  dialogVisible.value = true
  await nextTick()
  scenarioTreeRef.value?.setCheckedKeys([])
}

async function editSuite(item) {
  editingSuite.value = item
  const projectId = typeof item.project === 'object' ? item.project.id : item.project
  form.value = {
    name: item.name,
    description: item.description || '',
    project: projectId,
    scenario_case_ids: (item.scenario_cases || []).map((scenario) => scenario.id)
  }
  scenarioSearch.value = ''
  await loadScenarios(projectId)
  dialogVisible.value = true
  await nextTick()
  scenarioTreeRef.value?.setCheckedKeys(form.value.scenario_case_ids)
}

async function saveSuite() {
  if (!form.value.name.trim()) return ElMessage.warning(t('apiTesting.testSuiteManagement.enterName'))
  const projectId = editingSuite.value
    ? (typeof editingSuite.value.project === 'object' ? editingSuite.value.project.id : editingSuite.value.project)
    : selectedProject.value
  if (!projectId) return ElMessage.warning(t('apiTesting.testSuiteManagement.selectProject'))
  const checkedNodes = scenarioTreeRef.value?.getCheckedNodes(true, false) || []
  const scenarioCaseIds = checkedNodes.filter((node) => node.type === 'scenario').map((node) => node.id)
  saving.value = true
  const body = {
    name: form.value.name.trim(),
    description: form.value.description,
    project: projectId,
    scenario_case_ids: scenarioCaseIds
  }
  try {
    if (editingSuite.value) await api.put(`/api-testing/test-suites/${editingSuite.value.id}/`, body)
    else await api.post('/api-testing/test-suites/', body)
    ElMessage.success(t('apiTesting.testSuiteManagement.saved'))
    dialogVisible.value = false
    await loadTestSuites()
  } catch {
    ElMessage.error(t('apiTesting.testSuiteManagement.saveFailed'))
  } finally {
    saving.value = false
  }
}

function resetDialog() {
  form.value = emptyForm()
  editingSuite.value = null
  scenarioSearch.value = ''
}

async function deleteSuite(item) {
  try {
    await ElMessageBox.confirm(t('apiTesting.testSuiteManagement.confirmDelete', { name: item.name }), t('apiTesting.common.confirm'), { type: 'warning' })
    await api.delete(`/api-testing/test-suites/${item.id}/`)
    ElMessage.success(t('apiTesting.testSuiteManagement.deleted'))
    await loadTestSuites()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(t('apiTesting.testSuiteManagement.deleteFailed'))
  }
}

async function runSuite(item) {
  runningSuiteId.value = item.id
  try {
    const { data } = await api.post(`/api-testing/test-suites/${item.id}/execute/`)
    const results = data.results || []
    const failed = results.filter((result) => result.status !== 'COMPLETED').length
    if (!results.length) ElMessage.warning(t('apiTesting.testSuiteManagement.emptyScenarios'))
    else if (failed) ElMessage.error(t('apiTesting.testSuiteManagement.executionComplete', { passed: results.length - failed, total: results.length, failed }))
    else ElMessage.success(t('apiTesting.testSuiteManagement.executionSuccess', { passed: results.length, total: results.length }))
  } catch {
    ElMessage.error(t('apiTesting.testSuiteManagement.runFailed'))
  } finally {
    runningSuiteId.value = null
  }
}

onMounted(loadProjects)
</script>

<style scoped>
.page-header { display: flex; align-items: center; gap: 16px; padding: 20px; margin-bottom: 20px; background: #fff; border-radius: 4px; }
.page-header h2 { margin: 0 auto 0 0; font-size: 22px; }
.project-select { flex: 0 0 200px; width: 200px; }
.row-actions { display: flex; flex-wrap: nowrap; align-items: center; gap: 4px; white-space: nowrap; }
.row-actions :deep(.el-button) { flex-shrink: 0; }
.table-card { border: 0; }
.search-input { width: 380px; margin-bottom: 16px; }
.pagination { justify-content: flex-end; margin-top: 18px; }
.scenario-picker { border: 1px solid var(--el-border-color); border-radius: 4px; }
.picker-header { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; border-bottom: 1px solid var(--el-border-color); font-weight: 600; }
.picker-header :deep(.el-input) { width: 240px; font-weight: 400; }
.tree-node-label { display: inline-flex; align-items: center; gap: 8px; }
@media (max-width: 760px) { .page-header { flex-wrap: wrap; } .page-header h2 { flex-basis: 100%; } .search-input { width: 100%; } }
</style>
