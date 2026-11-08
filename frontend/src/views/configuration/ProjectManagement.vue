<template>
  <div class="project-center">
    <div class="page-header">
      <div>
        <h1>{{ $t('configuration.projectCenter.title') }}</h1>
        <p>{{ $t('configuration.projectCenter.subtitle') }}</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">{{ $t('configuration.projectCenter.createProject') }}</el-button>
    </div>

    <div class="summary-grid">
      <div v-for="item in summaryItems" :key="item.key" class="summary-item">
        <div class="summary-icon" :style="{ backgroundColor: item.color }"><el-icon><component :is="item.icon" /></el-icon></div>
        <div><strong>{{ item.count }}</strong><span>{{ item.label }}</span></div>
      </div>
    </div>

    <div class="table-panel">
      <div class="panel-title">{{ $t('configuration.projectCenter.panelTitle') }}</div>
      <div class="filter-row">
        <el-input v-model="search" clearable :placeholder="$t('project.searchPlaceholder')" :prefix-icon="Search" @keyup.enter="loadProjects" />
        <el-select v-model="status" clearable :placeholder="$t('project.selectStatus')" @change="loadProjects">
          <el-option :label="$t('configuration.projectCenter.status.active')" value="active" />
          <el-option :label="$t('configuration.projectCenter.status.paused')" value="paused" />
          <el-option :label="$t('configuration.projectCenter.status.completed')" value="completed" />
          <el-option :label="$t('configuration.projectCenter.status.archived')" value="archived" />
        </el-select>
        <el-button :icon="Search" @click="loadProjects">{{ $t('configuration.projectCenter.search') }}</el-button>
      </div>
      <el-table v-loading="loading" :data="projects" stripe>
        <el-table-column prop="name" :label="$t('project.projectName')" min-width="180" />
        <el-table-column prop="description" :label="$t('project.description')" min-width="180" show-overflow-tooltip />
        <el-table-column :label="$t('project.status')" width="100">
          <template #default="{ row }"><el-tag type="success">{{ statusText(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="$t('configuration.projectCenter.module')" min-width="260">
          <template #default="{ row }">
            <div class="module-tags">
              <el-tag
                v-for="moduleKey in row.modules || []"
                :key="moduleKey"
                size="small"
                class="module-tag"
                :style="{ color: moduleMeta(moduleKey).color, backgroundColor: `${moduleMeta(moduleKey).color}18` }"
              >
                <el-icon><component :is="moduleMeta(moduleKey).icon" /></el-icon>
                <span>{{ moduleMeta(moduleKey).label }}</span>
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="$t('project.owner')" width="120"><template #default="{ row }">{{ row.owner?.username || '-' }}</template></el-table-column>
        <el-table-column :label="$t('project.createdAt')" width="170"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column :label="$t('project.actions')" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openMembers(row)">{{ $t('configuration.projectCenter.members') }}</el-button>
            <el-button link type="primary" @click="openEdit(row)">{{ $t('common.edit') }}</el-button>
            <el-button link type="danger" @click="remove(row)">{{ $t('common.delete') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="loadProjects" />
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? $t('configuration.projectCenter.dialog.editTitle') : $t('configuration.projectCenter.dialog.createTitle')" width="760px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="rules" :label-width="labelWidth">
        <div class="form-section">
          <div class="section-title">{{ $t('configuration.projectCenter.dialog.basicInfo') }}</div>
          <el-form-item :label="$t('project.projectName')" prop="name"><el-input v-model="form.name" :placeholder="$t('project.projectNamePlaceholder')" /></el-form-item>
          <el-form-item :label="$t('project.projectDescription')"><el-input v-model="form.description" type="textarea" :rows="3" :placeholder="$t('project.projectDescriptionPlaceholder')" /></el-form-item>
          <el-form-item :label="$t('configuration.projectCenter.dialog.projectStatus')">
            <el-select v-model="form.status">
              <el-option :label="$t('configuration.projectCenter.status.active')" value="active" />
              <el-option :label="$t('configuration.projectCenter.status.paused')" value="paused" />
              <el-option :label="$t('configuration.projectCenter.status.completed')" value="completed" />
              <el-option :label="$t('configuration.projectCenter.status.archived')" value="archived" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('configuration.projectCenter.dialog.projectMembers')">
            <el-select v-model="form.member_ids" multiple filterable collapse-tags :placeholder="$t('configuration.projectCenter.dialog.membersPlaceholder')" style="width: 100%"><el-option v-for="user in users" :key="user.id" :label="user.username" :value="user.id" /></el-select>
          </el-form-item>
        </div>
        <div class="form-section module-section">
          <div class="section-title">{{ $t('configuration.projectCenter.dialog.projectTypes') }} <small>{{ $t('configuration.projectCenter.dialog.projectTypesHint') }}</small></div>
          <div class="module-grid">
            <button v-for="item in moduleOptions" :key="item.key" type="button" class="module-card" :class="{ selected: form.modules.includes(item.key) }" @click="toggleModule(item.key)">
              <el-icon :style="{ color: item.color }"><component :is="item.icon" /></el-icon><span>{{ item.label }}</span><em>{{ form.modules.includes(item.key) ? $t('configuration.projectCenter.dialog.selected') : $t('configuration.projectCenter.dialog.select') }}</em>
            </button>
          </div>
          <div v-if="form.modules.includes('api')" class="module-config"><b>{{ $t('configuration.projectCenter.dialog.apiConfig') }}</b><el-select v-model="form.module_configs.api.project_type"><el-option label="HTTP" value="HTTP" /><el-option label="WebSocket" value="WEBSOCKET" /></el-select></div>
          <div v-if="form.modules.includes('ui')" class="module-config"><b>{{ $t('configuration.projectCenter.dialog.uiConfig') }}</b><el-input v-model="form.module_configs.ui.base_url" :placeholder="$t('configuration.projectCenter.dialog.uiBaseUrlPlaceholder')" /></div>
          <div v-if="form.modules.includes('perf')" class="module-config"><b>{{ $t('configuration.projectCenter.dialog.perfConfig') }}</b><span>{{ $t('configuration.projectCenter.dialog.perfHint') }}</span></div>
        </div>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="saving" @click="save">{{ editing ? $t('configuration.projectCenter.dialog.saveChanges') : $t('configuration.projectCenter.createProject') }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="memberDialogVisible" :title="$t('configuration.projectCenter.memberDialog.title')" width="620px" destroy-on-close>
      <div class="member-dialog-header">
        <span>{{ memberProject?.name || '' }}</span>
        <el-button type="primary" size="small" @click="openMemberAdd">{{ $t('project.addMember') }}</el-button>
      </div>
      <el-table v-loading="memberLoading" :data="projectMembers" size="small">
        <el-table-column prop="username" :label="$t('project.username')" />
        <el-table-column prop="email" :label="$t('project.email')" />
        <el-table-column prop="role" :label="$t('project.role')" width="100" />
        <el-table-column :label="$t('project.actions')" width="90">
          <template #default="{ row }">
            <el-button v-if="row.role !== 'owner'" link type="danger" @click="removeMember(row)">{{ $t('configuration.projectCenter.memberDialog.remove') }}</el-button>
            <span v-else class="owner-label">{{ $t('project.owner') }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="memberAddVisible" :title="$t('configuration.projectCenter.memberDialog.addTitle')" width="420px" append-to-body>
      <el-select v-model="selectedMemberId" filterable clearable style="width: 100%" :placeholder="$t('project.selectMember')">
        <el-option v-for="user in availableMembers" :key="user.id" :label="user.username" :value="user.id">
          <span>{{ user.username }}</span><span class="user-email">{{ user.email }}</span>
        </el-option>
      </el-select>
      <template #footer>
        <el-button @click="memberAddVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="memberSubmitting" :disabled="!selectedMemberId" @click="submitMember">{{ $t('configuration.projectCenter.memberDialog.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Cellphone, Connection, Document, Folder, Link, MagicStick, Monitor, Plus, Search } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import api from '@/utils/api'
import { addProjectMember, createProjectCenter, deleteProjectCenter, getProjectCenter, getProjectMembers, removeProjectMember, updateProjectCenter } from '@/api/projects'

const { t, te, locale } = useI18n()

const projects = ref([])
const users = ref([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref(false)
const editingId = ref(null)
const formRef = ref()
const memberDialogVisible = ref(false)
const memberAddVisible = ref(false)
const memberProject = ref(null)
const projectMembers = ref([])
const memberLoading = ref(false)
const memberSubmitting = ref(false)
const selectedMemberId = ref(null)
const availableMembers = ref([])
const page = ref(1)
const pageSize = 10
const total = ref(0)
const search = ref('')
const status = ref('')
const form = reactive({ name: '', description: '', status: 'active', modules: ['ai_generation'], member_ids: [], module_configs: { api: { project_type: 'HTTP' }, ui: { base_url: '' }, perf: {} } })
const rules = computed(() => ({ name: [{ required: true, message: t('project.projectNameRequired'), trigger: 'blur' }] }))
// 英文标签较长，适当加宽表单标签宽度
const labelWidth = computed(() => (locale.value === 'zh-cn' ? '90px' : '150px'))
const moduleOptions = computed(() => [
  { key: 'ai_generation', label: t('configuration.projectCenter.modules.aiGeneration'), icon: Document, color: '#6575d8' },
  { key: 'ai_testing', label: t('configuration.projectCenter.modules.aiTesting'), icon: MagicStick, color: '#ed72c7' },
  { key: 'api', label: t('configuration.projectCenter.modules.api'), icon: Link, color: '#5867d8' },
  { key: 'ui', label: t('configuration.projectCenter.modules.ui'), icon: Monitor, color: '#ef69c7' },
  { key: 'app', label: t('configuration.projectCenter.modules.app'), icon: Cellphone, color: '#38b7ef' },
  { key: 'perf', label: t('configuration.projectCenter.modules.perf'), icon: Connection, color: '#37b995' },
])
const moduleMap = computed(() => Object.fromEntries(moduleOptions.value.map(item => [item.key, item])))
const summaryItems = computed(() => [
  { key: 'all', label: t('configuration.projectCenter.totalProjects'), count: total.value, icon: Folder, color: '#6873d8' },
  ...moduleOptions.value.map(item => ({
    ...item,
    count: projects.value.filter(p => p.modules?.includes(item.key) && (p.module_status?.[item.key]?.enabled !== false)).length
  }))
])

function moduleMeta(key) {
  return moduleMap.value[key] || { label: key, icon: Document, color: '#9099a8' }
}

function statusText(value) {
  if (!value) return value
  const key = `configuration.projectCenter.status.${value}`
  return te(key) ? t(key) : value
}
function formatDate(value) { return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-' }
async function loadProjects() {
  loading.value = true
  try {
    const response = await getProjectCenter({ page: page.value, search: search.value || undefined, status: status.value || undefined })
    projects.value = response.data.results || response.data || []
    total.value = response.data.count ?? projects.value.length
  } catch (error) { ElMessage.error(error.response?.data?.detail || t('configuration.projectCenter.messages.loadFailed')) } finally { loading.value = false }
}
async function loadUsers() { try { const response = await api.get('/auth/users/'); users.value = response.data.results || response.data || [] } catch { users.value = [] } }
async function openMembers(row) {
  memberProject.value = row
  memberDialogVisible.value = true
  memberLoading.value = true
  try {
    if (!users.value.length) await loadUsers()
    const response = await getProjectMembers(row.id)
    projectMembers.value = response.data || []
    const existingIds = new Set(projectMembers.value.map(member => member.id))
    availableMembers.value = users.value.filter(user => !existingIds.has(user.id))
  } catch (error) {
    projectMembers.value = []
    ElMessage.error(error.response?.data?.error || t('configuration.projectCenter.messages.membersLoadFailed'))
  } finally { memberLoading.value = false }
}
function openMemberAdd() {
  selectedMemberId.value = null
  memberAddVisible.value = true
}
async function submitMember() {
  if (!selectedMemberId.value || !memberProject.value) return
  memberSubmitting.value = true
  try {
    await addProjectMember(memberProject.value.id, { user_id: selectedMemberId.value })
    ElMessage.success(t('project.memberAddSuccess'))
    memberAddVisible.value = false
    await openMembers(memberProject.value)
    await loadProjects()
  } catch (error) { ElMessage.error(error.response?.data?.error || t('project.memberAddFailed')) } finally { memberSubmitting.value = false }
}
async function removeMember(member) {
  if (!member.member_id || !memberProject.value) return
  try {
    await ElMessageBox.confirm(t('configuration.projectCenter.messages.memberRemoveConfirm', { name: member.username }), t('configuration.projectCenter.messages.tip'), { type: 'warning' })
    await removeProjectMember(memberProject.value.id, member.member_id)
    ElMessage.success(t('configuration.projectCenter.messages.memberRemoveSuccess'))
    await openMembers(memberProject.value)
    await loadProjects()
  } catch (error) { if (error !== 'cancel') ElMessage.error(error.response?.data?.error || t('configuration.projectCenter.messages.memberRemoveFailed')) }
}
function resetForm() { Object.assign(form, { name: '', description: '', status: 'active', modules: ['ai_generation'], member_ids: [], module_configs: { api: { project_type: 'HTTP' }, ui: { base_url: '' }, perf: {} } }) }
function openCreate() { editing.value = false; editingId.value = null; resetForm(); dialogVisible.value = true }
function openEdit(row) {
  editing.value = true
  editingId.value = row.id
  const selectedModules = (row.modules || []).filter(module => {
    if (!['api', 'ui', 'app', 'perf'].includes(module)) return true
    return row.module_status?.[module]?.enabled !== false
  })
  Object.assign(form, { name: row.name, description: row.description || '', status: row.status, modules: selectedModules, member_ids: (row.members || []).map(item => item.user?.id || item.id), module_configs: { api: { project_type: 'HTTP' }, ui: { base_url: '' }, perf: {} } })
  dialogVisible.value = true
}
function toggleModule(key) { const index = form.modules.indexOf(key); index >= 0 ? form.modules.splice(index, 1) : form.modules.push(key) }
async function save() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...form, modules: [...form.modules], member_ids: [...form.member_ids] }
    editing.value ? await updateProjectCenter(editingId.value, payload) : await createProjectCenter(payload)
    ElMessage.success(editing.value ? t('project.updateSuccess') : t('project.createSuccess'))
    dialogVisible.value = false
    await loadProjects()
  } catch (error) { ElMessage.error(error.response?.data?.detail || t('configuration.projectCenter.messages.saveFailed')) } finally { saving.value = false }
}
async function remove(row) {
  try {
    await ElMessageBox.confirm(t('configuration.projectCenter.messages.deleteConfirm', { name: row.name }), t('configuration.projectCenter.messages.tip'), { type: 'warning' })
    await deleteProjectCenter(row.id)
    ElMessage.success(t('project.deleteSuccess'))
    await loadProjects()
  } catch (error) { if (error !== 'cancel') ElMessage.error(t('project.deleteFailed')) }
}
onMounted(() => { loadProjects(); loadUsers() })
</script>

<style scoped>
.project-center { padding: 8px 4px 24px; color: #1f2937; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; font-weight: 600; }
.page-header p { margin: 0; color: #8b95a7; font-size: 13px; }
.summary-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.summary-item { display: flex; align-items: center; gap: 10px; min-height: 68px; padding: 12px 14px; background: #fff; border: 1px solid #edf0f5; border-radius: 6px; box-shadow: 0 2px 8px rgba(38, 52, 77, .04); }
.summary-icon { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 8px; color: #fff; }
.summary-item strong { display: block; font-size: 20px; line-height: 1; }.summary-item span { display: block; margin-top: 6px; color: #9099a8; font-size: 11px; white-space: nowrap; }
.module-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; padding: 2px 0; }
.module-tag { flex-shrink: 0; margin: 0; border: 0; white-space: nowrap; }
.module-tag :deep(.el-tag__content) { display: inline-flex; align-items: center; gap: 3px; white-space: nowrap; }
.module-tag .el-icon { flex-shrink: 0; }
.table-panel { padding: 18px; background: #fff; border: 1px solid #edf0f5; box-shadow: 0 2px 10px rgba(38, 52, 77, .04); }.panel-title { margin-bottom: 16px; font-weight: 600; }.filter-row { display: flex; gap: 10px; margin-bottom: 14px; }.filter-row .el-input { width: 240px; }.filter-row .el-select { width: 150px; }.el-pagination { justify-content: flex-end; margin-top: 16px; }.form-section { padding: 12px 14px; border: 1px solid #edf0f5; border-radius: 6px; margin-bottom: 14px; }.module-section { background: #f8fbff; }.section-title { margin-bottom: 14px; font-weight: 600; }.section-title small { margin-left: 8px; color: #9aa4b3; font-weight: 400; }.module-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }.module-card { display: flex; flex-direction: column; align-items: center; gap: 7px; min-height: 92px; padding: 12px 6px; border: 1px solid #e8edf5; border-radius: 7px; background: #fff; cursor: pointer; }.module-card.selected { border-color: #409eff; box-shadow: 0 0 0 1px #409eff inset; background: #f5faff; }.module-card span { font-size: 12px; color: #364152; text-align: center; line-height: 1.3; }.module-card em { padding: 2px 8px; border-radius: 10px; background: #f1f3f6; color: #8993a2; font-size: 11px; font-style: normal; }.module-card.selected em { background: #409eff; color: #fff; }.module-config { display: flex; align-items: center; gap: 12px; margin-top: 12px; padding: 10px 12px; background: #fff; border-radius: 5px; color: #667085; }.module-config .el-select, .module-config .el-input { max-width: 360px; }
 .member-dialog-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; font-weight: 600; }.owner-label { color: #98a2b3; font-size: 12px; }.user-email { float: right; margin-left: 16px; color: #9aa4b3; font-size: 12px; }
@media (max-width: 1100px) { .summary-grid { grid-template-columns: repeat(3, 1fr); }.module-grid { grid-template-columns: repeat(3, 1fr); } }
</style>
