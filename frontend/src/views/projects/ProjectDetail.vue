<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="page-title">{{ $t('project.projectDetail') }}</h1>
      <el-button type="primary" @click="$router.back()">
        <el-icon><ArrowLeft /></el-icon>
        {{ $t('common.back') }}
      </el-button>
    </div>

    <div class="card-container">
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="$t('project.projectInfo')" name="info">
          <div v-if="project">
            <el-descriptions :column="2" border>
              <el-descriptions-item :label="$t('project.projectName')">{{ project.name }}</el-descriptions-item>
              <el-descriptions-item :label="$t('project.status')">
                <el-tag :type="getStatusType(project.status)">{{ getStatusText(project.status) }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('project.owner')">{{ project.owner?.username }}</el-descriptions-item>
              <el-descriptions-item :label="$t('project.createdAt')">{{ formatDate(project.created_at) }}</el-descriptions-item>
              <el-descriptions-item :label="$t('project.projectDescription')" :span="2">{{ project.description || $t('project.noDescription') }}</el-descriptions-item>
            </el-descriptions>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="$t('project.projectMembers')" name="members">
          <div class="members-section">
            <el-button type="primary" @click="openAddMember">{{ $t('project.addMember') }}</el-button>
            <el-table :data="project?.members || []" style="width: 100%; margin-top: 20px;">
              <el-table-column prop="user.username" :label="$t('project.username')" />
              <el-table-column prop="user.email" :label="$t('project.email')" />
              <el-table-column prop="role" :label="$t('project.role')" />
              <el-table-column prop="joined_at" :label="$t('project.joinedAt')">
                <template #default="{ row }">
                  {{ formatDate(row.joined_at) }}
                </template>
              </el-table-column>
              <el-table-column :label="$t('project.actions')" width="100">
                <template #default="{ row }">
                  <el-button v-if="row.role !== 'owner'" size="small" type="danger" @click="removeMember(row)">{{ $t('common.delete') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="$t('project.environments')" name="environments">
          <div class="environments-section">
            <el-button type="primary" @click="showAddEnvDialog = true">{{ $t('project.addEnvironment') }}</el-button>
            <el-table :data="project?.environments || []" style="width: 100%; margin-top: 20px;">
              <el-table-column prop="name" :label="$t('project.environmentName')" />
              <el-table-column prop="base_url" :label="$t('project.baseUrl')" />
              <el-table-column prop="description" :label="$t('project.description')" />
              <el-table-column prop="is_default" :label="$t('project.defaultEnvironment')">
                <template #default="{ row }">
                  <el-tag v-if="row.is_default" type="success">{{ $t('project.yes') }}</el-tag>
                  <span v-else>{{ $t('project.no') }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="showAddMemberDialog" :title="$t('project.addMember')" width="420px" destroy-on-close>
      <el-form label-width="80px">
        <el-form-item :label="$t('project.username')">
          <UserSelect v-model="selectedUserId" :options="availableUsers" clearable style="width: 100%" :placeholder="$t('project.selectMember')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddMemberDialog = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="memberSubmitting" :disabled="!selectedUserId" @click="addMember">{{ $t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/utils/api'
import dayjs from 'dayjs'
import { addProjectMember, removeProjectMember } from '@/api/projects'
import UserSelect from '@/components/UserSelect.vue'

const route = useRoute()
const { t } = useI18n()
const project = ref(null)
const activeTab = ref('info')
const showAddMemberDialog = ref(false)
const showAddEnvDialog = ref(false)
const users = ref([])
const selectedUserId = ref(null)
const memberSubmitting = ref(false)
const availableUsers = ref([])

const fetchProject = async () => {
  try {
    const response = await api.get(`/projects/${route.params.id}/`)
    project.value = response.data
  } catch (error) {
    ElMessage.error(t('project.fetchDetailFailed'))
  }
}

const loadUsers = async () => {
  try {
    const response = await api.get('/auth/users/')
    users.value = response.data.results || response.data || []
  } catch {
    users.value = []
  }
}

const openAddMember = () => {
  const existingIds = new Set([
    project.value?.owner?.id,
    ...(project.value?.members || []).map(member => member.user?.id || member.id)
  ])
  availableUsers.value = users.value.filter(user => !existingIds.has(user.id))
  selectedUserId.value = null
  showAddMemberDialog.value = true
}

const addMember = async () => {
  memberSubmitting.value = true
  try {
    await addProjectMember(route.params.id, { user_id: selectedUserId.value })
    ElMessage.success(t('project.memberAddSuccess'))
    showAddMemberDialog.value = false
    await fetchProject()
  } catch (error) {
    ElMessage.error(error.response?.data?.error || t('project.memberAddFailed'))
  } finally {
    memberSubmitting.value = false
  }
}

const getStatusType = (status) => {
  const typeMap = {
    active: 'success',
    paused: 'warning',
    completed: 'info',
    archived: 'info'
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status) => {
  const textMap = {
    active: t('project.active'),
    paused: t('project.paused'),
    completed: t('project.completed'),
    archived: t('project.archived')
  }
  return textMap[status] || status
}

const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm')
}

const removeMember = async (member) => {
  const memberId = member.member_id || member.id
  if (!memberId || member.role === 'owner') return
  try {
    await ElMessageBox.confirm(t('project.memberDeleteConfirm'), t('common.confirm'), { type: 'warning' })
    await removeProjectMember(route.params.id, memberId)
    ElMessage.success(t('project.memberDeleteSuccess'))
    fetchProject()
  } catch (error) {
    ElMessage.error(t('project.memberDeleteFailed'))
  }
}

onMounted(() => {
  fetchProject()
  loadUsers()
})
</script>

<style lang="scss" scoped>
.members-section, .environments-section {
  padding: 20px 0;
}
.user-email {
  float: right;
  margin-left: 16px;
  color: #9aa4b3;
  font-size: 12px;
}
</style>
