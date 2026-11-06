<template>
  <div class="page-container testcase-form-page">
    <div class="page-header">
      <h1 class="page-title">{{ $t('testcase.edit') }}</h1>
      <div>
        <el-button @click="$router.back()">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="submitting" :disabled="loading" @click="handleSubmit">
          {{ $t('testcase.saveChanges') }}
        </el-button>
      </div>
    </div>

    <div class="card-container" v-if="!loading">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item :label="$t('testcase.caseTitle')" prop="title">
          <el-input v-model="form.title" :placeholder="$t('testcase.caseTitlePlaceholder')" />
        </el-form-item>

        <el-form-item :label="$t('testcase.caseDescription')" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            :placeholder="$t('testcase.caseDescriptionPlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="$t('testcase.maintainer')">
          <el-select v-model="form.maintainer_id" filterable :placeholder="$t('testcase.maintainer')">
            <el-option v-for="member in projectMembers" :key="member.id" :label="member.email ? `${member.username} (${member.email})` : member.username" :value="member.id" />
          </el-select>
        </el-form-item>

        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item :label="$t('testcase.testType')" prop="test_type">
              <el-select v-model="form.test_type" :placeholder="$t('testcase.selectTestType')">
                <el-option :label="$t('testcase.functional')" value="functional" />
                <el-option :label="$t('testcase.integration')" value="integration" />
                <el-option :label="$t('testcase.api')" value="api" />
                <el-option :label="$t('testcase.ui')" value="ui" />
                <el-option :label="$t('testcase.performance')" value="performance" />
                <el-option :label="$t('testcase.security')" value="security" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item :label="$t('testcase.modules')">
              <el-tree-select v-model="form.module_id" :data="moduleOptions" node-key="id" check-strictly default-expand-all clearable :render-after-expand="false" :disabled="!form.project_id" :placeholder="$t('testcase.selectModule')" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item :label="$t('testcase.priority')" prop="priority">
              <el-select v-model="form.priority" :placeholder="$t('testcase.selectPriority')">
                <el-option :label="$t('testcase.low')" value="low" />
                <el-option :label="$t('testcase.medium')" value="medium" />
                <el-option :label="$t('testcase.high')" value="high" />
                <el-option :label="$t('testcase.critical')" value="critical" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item :label="$t('testcase.automation')">
              <AutomationCaseLinks
                :test-case-id="route.params.id"
                :project-id="form.project_id"
                :links="automationLinks"
                allow-unlink
                load-links-on-mount
                @update:links="automationLinks = $event"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="20">
          <el-col :span="24">
            <el-form-item :label="$t('testcase.relatedVersions')">
              <el-select
                v-model="form.version_ids"
                :placeholder="$t('testcase.selectVersions')"
                multiple
                clearable
                filterable
                @change="onVersionChange"
              >
                <el-option
                  v-for="version in projectVersions"
                  :key="version.id"
                  :label="version.name + (version.is_baseline ? ' (' + $t('testcase.baseline') + ')' : '')"
                  :value="version.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item :label="$t('testcase.preconditions')" prop="preconditions">
          <el-input
            v-model="form.preconditions"
            type="textarea"
            :rows="3"
            :placeholder="$t('testcase.preconditionsPlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="$t('testcase.steps')" prop="steps">
          <el-input
            v-model="form.steps"
            type="textarea"
            :rows="6"
            maxlength="1000"
            show-word-limit
            :placeholder="$t('testcase.stepsPlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="$t('testcase.expectedResult')" prop="expected_result">
          <el-input
            v-model="form.expected_result"
            type="textarea"
            :rows="6"
            :placeholder="$t('testcase.expectedResultPlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="$t('testcase.notes')">
          <RichTextEditor
            ref="notesEditor"
            v-model="form.notes"
            :upload-url="`/testcases/${route.params.id}/notes-image/`"
          />
        </el-form-item>
      </el-form>
    </div>

    <div class="card-container" v-else>
      <el-skeleton :rows="10" animated />
    </div>

  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '@/utils/api'
import RichTextEditor from '@/components/RichTextEditor.vue'
import AutomationCaseLinks from '@/components/AutomationCaseLinks.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const formRef = ref()
const loading = ref(true)
const submitting = ref(false)
const projectVersions = ref([])
const projectMembers = ref([])
const moduleOptions = ref([])
const automationLinks = ref([])
const notesEditor = ref()

const form = reactive({
  title: '',
  description: '',
  project_id: null,
  priority: 'medium',
  test_type: 'functional',
  preconditions: '',
  steps: '',
  expected_result: '',
  version_ids: [],
  notes: '',
  maintainer_id: null,
  module_id: null
})

const rules = {
  title: [
    { required: true, message: computed(() => t('testcase.titleRequired')), trigger: 'blur' },
    { min: 5, max: 500, message: computed(() => t('testcase.titleLength')), trigger: 'blur' }
  ],
  expected_result: [
    { required: true, message: computed(() => t('testcase.expectedResultRequired')), trigger: 'blur' }
  ],
  steps: [
    { max: 1000, message: computed(() => t('testcase.stepsMaxLength')), trigger: 'blur' }
  ]
}

// 将HTML的<br>标签转换为换行符（用于编辑时显示）
const convertBrToNewline = (text) => {
  if (!text) return ''
  return text.replace(/<br\s*\/?>/gi, '\n')
}

// 将换行符转换为HTML的<br>标签（用于保存）
const convertNewlineToBr = (text) => {
  if (!text) return ''
  return text.replace(/\n/g, '<br>')
}

const fetchProjectVersions = async (projectId) => {
  if (!projectId) {
    projectVersions.value = []
    return
  }

  try {
    const response = await api.get(`/versions/projects/${projectId}/versions/`)
    projectVersions.value = response.data || []
  } catch (error) {
    console.error(t('testcase.fetchVersionsFailed'), error)
    ElMessage.error(t('testcase.fetchVersionsFailed'))
    projectVersions.value = []
  }
}

const fetchModules = async (projectId) => {
  if (!projectId) { moduleOptions.value = []; return }
  try {
    const { data } = await api.get('/testcases/modules/', { params: { project: projectId } })
    const addLabels = (nodes) => nodes.map(node => ({ ...node, label: node.name, children: addLabels(node.children || []) }))
    moduleOptions.value = addLabels(data)
  } catch (error) { moduleOptions.value = []; ElMessage.error(t('testcase.fetchModulesFailed')) }
}

const fetchProjectMembers = async (projectId) => {
  projectMembers.value = []
  if (!projectId) return
  try {
    const response = await api.get(`/projects/${projectId}/members/`)
    projectMembers.value = response.data || []
  } catch (error) {
    console.error('Fetch project members failed:', error)
  }
}

const onVersionChange = () => {
  // Version change handling logic if needed
}

const fetchTestCase = async () => {
  try {
    const response = await api.get(`/testcases/${route.params.id}/`)
    const testcase = response.data

    // Fill form data
    form.title = testcase.title
    form.description = testcase.description
    form.notes = testcase.notes || ''
    form.maintainer_id = testcase.author?.id || null
    form.project_id = testcase.project?.id || null
    form.module_id = testcase.module?.id || null
    form.priority = testcase.priority
    form.test_type = testcase.test_type
    form.preconditions = convertBrToNewline(testcase.preconditions || '')
    form.expected_result = convertBrToNewline(testcase.expected_result || '')

    // Fill steps data (convert <br> to newlines)
    form.steps = convertBrToNewline(testcase.steps || '')

    // Fill version associations
    form.version_ids = testcase.versions ? testcase.versions.map(v => v.id) : []

    // If project exists, fetch versions for that project
    if (form.project_id) {
      await fetchProjectVersions(form.project_id)
      await fetchProjectMembers(form.project_id)
      await fetchModules(form.project_id)
    }

    loading.value = false
  } catch (error) {
    ElMessage.error(t('testcase.fetchDetailFailed'))
    router.back()
  }
}

const handleSubmit = async () => {
  if (!formRef.value) return

  await formRef.value.validate(async (valid) => {
    if (valid) {
      submitting.value = true
      try {
        // Convert newlines back to <br> tags before submitting
        const submitData = {
          ...form,
          notes: await notesEditor.value.uploadEmbeddedImages(form.notes),
          preconditions: convertNewlineToBr(form.preconditions || ''),
          steps: convertNewlineToBr(form.steps || ''),
          expected_result: convertNewlineToBr(form.expected_result || '')
        }

        await api.put(`/testcases/${route.params.id}/`, submitData)
        ElMessage.success(t('testcase.updateSuccess'))
        router.push(`/ai-generation/testcases/${route.params.id}`)
      } catch (error) {
        ElMessage.error(t('testcase.updateFailed'))
        console.error('Submit error:', error)
      } finally {
        submitting.value = false
      }
    }
  })
}

onMounted(async () => {
  await fetchTestCase()  // fetchTestCase中会根据项目获取版本列表
})
</script>

<style lang="scss" scoped>
.testcase-form-page {
  padding-top: 8px;
}

.testcase-form-page .page-header .page-title {
  margin: 0;
}
</style>
