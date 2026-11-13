<template>
  <div v-loading="loading" class="tc-editor">
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      class="tc-editor__form"
      @submit.prevent
    >
      <div class="tc-editor__grid">
        <!-- 左栏：标题 / 维护人 / 优先级 / 前置条件 / 操作步骤 / 备注 -->
        <section class="tc-editor__main">
          <el-form-item prop="title" class="tc-title-field">
            <el-input
              v-model="form.title"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 4 }"
              resize="none"
              maxlength="500"
              :placeholder="$t('testcase.caseTitlePlaceholder')"
              class="tc-title-input"
            />
          </el-form-item>

          <!-- 原生带字段标签的选择器；不设固定标签宽度，整行与标题左边缘对齐 -->
          <el-row :gutter="20" class="tc-identity">
            <el-col :span="12">
              <el-form-item :label="$t('testcase.maintainer')" prop="maintainer_id">
                <el-select
                  v-model="form.maintainer_id"
                  filterable
                  clearable
                  :placeholder="$t('testcase.maintainer')"
                >
                  <el-option
                    v-for="member in projectMembers"
                    :key="member.id"
                    :label="member.email ? `${member.username} (${member.email})` : member.username"
                    :value="member.id"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item :label="$t('testcase.priority')" prop="priority">
                <el-select v-model="form.priority" :placeholder="$t('testcase.selectPriority')">
                  <el-option :label="$t('testcase.low')" value="low" />
                  <el-option :label="$t('testcase.medium')" value="medium" />
                  <el-option :label="$t('testcase.high')" value="high" />
                  <el-option :label="$t('testcase.critical')" value="critical" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>

          <div class="tc-editor__divider" />

          <div class="tc-field">
            <div class="tc-field__label">{{ $t('testcase.preconditions') }}</div>
            <el-input
              v-model="form.preconditions"
              type="textarea"
              :rows="3"
              :placeholder="$t('testcase.preconditionsPlaceholder')"
            />
          </div>

          <div class="tc-field">
            <div class="tc-field__label">{{ $t('testcase.steps') }}</div>
            <el-form-item prop="steps" class="tc-steps-field">
              <TestCaseStepEditor v-model="form.steps" />
            </el-form-item>
          </div>

          <div class="tc-field">
            <div class="tc-field__label">{{ $t('testcase.notes') }}</div>
            <RichTextEditor ref="notesEditor" v-model="form.notes" :upload-url="notesUploadUrl" />
          </div>

          <!-- 用例描述暂时不展示；form.description 仍会回填并提交，改 SHOW_DESCRIPTION 为 true 即可恢复 -->
          <div v-if="SHOW_DESCRIPTION" class="tc-field">
            <div class="tc-field__label">{{ $t('testcase.caseDescription') }}</div>
            <el-input
              v-model="form.description"
              type="textarea"
              :rows="3"
              :placeholder="$t('testcase.caseDescriptionPlaceholder')"
            />
          </div>
        </section>

        <!-- 右栏：属性字段（不显示分组标题） -->
        <aside class="tc-editor__aside">
          <div class="tc-attr">
            <div class="tc-attr__label">{{ $t('testcase.project') }}</div>
            <el-select
              v-if="!isEdit"
              v-model="form.project_id"
              filterable
              clearable
              :placeholder="$t('testcase.relatedProject')"
              @change="onProjectChange"
            >
              <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
            </el-select>
            <div v-else class="tc-attr__value">{{ projectName || '-' }}</div>
          </div>

          <div class="tc-attr">
            <div class="tc-attr__label"><span class="tc-required">*</span>{{ $t('testcase.modules') }}</div>
            <el-tree-select
              :key="moduleTreeKey"
              v-model="form.module_id"
              :data="moduleOptions"
              node-key="id"
              check-strictly
              :default-expanded-keys="expandedModuleKeys"
              clearable
              filterable
              :render-after-expand="false"
              :disabled="!form.project_id"
              :placeholder="$t('testcase.selectModule')"
            />
          </div>

          <div class="tc-attr">
            <div class="tc-attr__label"><span class="tc-required">*</span>{{ $t('testcase.testType') }}</div>
            <el-select v-model="form.test_type" :placeholder="$t('testcase.selectTestType')">
              <el-option :label="$t('testcase.functional')" value="functional" />
              <el-option :label="$t('testcase.integration')" value="integration" />
              <el-option :label="$t('testcase.api')" value="api" />
              <el-option :label="$t('testcase.ui')" value="ui" />
              <el-option :label="$t('testcase.performance')" value="performance" />
              <el-option :label="$t('testcase.security')" value="security" />
            </el-select>
          </div>

          <div class="tc-attr">
            <div class="tc-attr__label">{{ $t('testcase.relatedVersions') }}</div>
            <el-select
              v-model="form.version_ids"
              :placeholder="$t('testcase.selectVersions')"
              multiple
              clearable
              filterable
              collapse-tags
              collapse-tags-tooltip
            >
              <el-option
                v-for="version in projectVersions"
                :key="version.id"
                :label="version.name + (version.is_baseline ? ' (' + $t('testcase.baseline') + ')' : '')"
                :value="version.id"
              />
            </el-select>
          </div>

          <div class="tc-attr">
            <div class="tc-attr__label">{{ $t('testcase.automation') }}</div>
            <AutomationCaseLinks
              :test-case-id="isEdit ? testCaseId : null"
              :project-id="form.project_id"
              :links="automationLinks"
              :allow-unlink="isEdit"
              :load-links-on-mount="isEdit"
              @update:links="automationLinks = $event"
            />
          </div>

          <template v-if="isEdit">
            <div class="tc-attr">
              <div class="tc-attr__label">{{ $t('testcase.createdAt') }}</div>
              <div class="tc-attr__value">{{ formatDate(detail.created_at) }}</div>
            </div>
            <div class="tc-attr">
              <div class="tc-attr__label">{{ $t('testcase.updatedAt') }}</div>
              <div class="tc-attr__value">{{ formatDate(detail.updated_at) }}</div>
            </div>
          </template>
        </aside>
      </div>
    </el-form>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import api from '@/utils/api'
import RichTextEditor from '@/components/RichTextEditor.vue'
import TestCaseStepEditor from '@/components/testcases/TestCaseStepEditor.vue'
import AutomationCaseLinks from '@/components/testcases/AutomationCaseLinks.vue'
import { getPreferredProjectId, rememberSelectedProject } from '@/utils/projectSelection'
import { normalizeSteps } from '@/utils/testcaseSteps'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  // 为空表示新建用例；有值表示编辑该用例
  testCaseId: { type: [Number, String], default: null },
  // 新建时的默认归属项目（一般由列表页当前项目传入）
  projectId: { type: [Number, String], default: null }
})
const emit = defineEmits(['saved', 'busy-change', 'load-error'])

const { t } = useI18n()
const userStore = useUserStore()

// 用例描述字段暂时隐藏（数据仍保留读写），需要恢复时改为 true 即可
const SHOW_DESCRIPTION = false

const isEdit = computed(() => props.testCaseId !== null && props.testCaseId !== undefined && props.testCaseId !== '')
const notesUploadUrl = computed(() => (isEdit.value ? `/testcases/${props.testCaseId}/notes-image/` : ''))

const formRef = ref()
const notesEditor = ref()
const loading = ref(true)
const submitting = ref(false)
const projects = ref([])
const projectVersions = ref([])
const projectMembers = ref([])
const moduleOptions = ref([])
const automationLinks = ref([])
const detail = ref({})
// 模块数据/选中值回填完成后重挂树，让 default-expanded-keys 按当前用例生效
const moduleTreeKey = ref(0)

const createForm = () => ({
  title: '',
  description: '',
  project_id: null,
  priority: 'medium',
  test_type: 'functional',
  preconditions: '',
  steps: [],
  version_ids: [],
  notes: '',
  maintainer_id: null,
  module_id: null
})
const form = reactive(createForm())

const currentUserId = computed(() => userStore.user?.id ?? null)
const projectName = computed(
  () => detail.value.project?.name || projects.value.find(p => String(p.id) === String(form.project_id))?.name || ''
)
const formatDate = value => (value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-')

// 找到选中模块在模块树中的完整路径，只展开它所在的目录链，其余目录保持折叠
const findModulePath = (nodes, targetId, path = []) => {
  if (targetId === null || targetId === undefined || targetId === '') return null
  for (const node of nodes || []) {
    const current = [...path, node.id]
    if (String(node.id) === String(targetId)) return current
    const found = findModulePath(node.children, targetId, current)
    if (found) return found
  }
  return null
}
const expandedModuleKeys = computed(() => {
  const path = findModulePath(moduleOptions.value, form.module_id)
  return path ? path.slice(0, -1) : []
})

const validateSteps = (_rule, value, callback) => {
  if (!normalizeSteps(value).length) {
    callback(new Error(t('testcase.stepsRequired')))
    return
  }
  callback()
}
const rules = {
  title: [
    { required: true, message: computed(() => t('testcase.titleRequired')), trigger: 'blur' },
    { min: 5, max: 500, message: computed(() => t('testcase.titleLength')), trigger: 'blur' }
  ],
  steps: [{ validator: validateSteps, trigger: 'blur' }]
}

// 库里的前置条件用 <br> 存换行，编辑时还原成换行符，提交时再转回去
const convertBrToNewline = text => (text ? String(text).replace(/<br\s*\/?>/gi, '\n') : '')
const convertNewlineToBr = text => (text ? String(text).replace(/\n/g, '<br>') : '')

const fetchProjects = async () => {
  try {
    const { data } = await api.get('/projects/list/')
    projects.value = data.results || []
  } catch {
    ElMessage.error(t('testcase.fetchProjectsFailed'))
    projects.value = []
  }
}

const fetchProjectVersions = async projectId => {
  if (!projectId) { projectVersions.value = []; return }
  try {
    const { data } = await api.get(`/versions/projects/${projectId}/versions/`)
    projectVersions.value = data || []
  } catch {
    ElMessage.error(t('testcase.fetchVersionsFailed'))
    projectVersions.value = []
  }
}

const fetchModules = async projectId => {
  if (!projectId) { moduleOptions.value = []; moduleTreeKey.value += 1; return }
  try {
    const { data } = await api.get('/testcases/modules/', { params: { project: projectId } })
    const addLabels = nodes => nodes.map(node => ({ ...node, label: node.name, children: addLabels(node.children || []) }))
    moduleOptions.value = addLabels(Array.isArray(data) ? data : data.results || [])
  } catch {
    moduleOptions.value = []
    ElMessage.error(t('testcase.fetchModulesFailed'))
  } finally {
    moduleTreeKey.value += 1
  }
}

const fetchProjectMembers = async projectId => {
  projectMembers.value = []
  if (!projectId) return
  try {
    const { data } = await api.get(`/projects/${projectId}/members/`)
    projectMembers.value = data || []
  } catch (error) {
    console.error('Fetch project members failed:', error)
  } finally {
    // 新建时默认维护人取当前登录用户，且必须是项目成员（后端会校验）
    const uid = currentUserId.value
    if (!isEdit.value && !form.maintainer_id && uid && projectMembers.value.some(m => String(m.id) === String(uid))) {
      form.maintainer_id = uid
    }
  }
}

const loadProjectOptions = async projectId => {
  await Promise.all([
    fetchProjectVersions(projectId),
    fetchProjectMembers(projectId),
    fetchModules(projectId)
  ])
}

const onProjectChange = projectId => {
  rememberSelectedProject(projectId, projects.value)
  form.version_ids = []
  form.module_id = null
  form.maintainer_id = null
  loadProjectOptions(projectId)
}

const resetForm = () => {
  Object.assign(form, createForm())
  detail.value = {}
  automationLinks.value = []
  projectVersions.value = []
  projectMembers.value = []
  moduleOptions.value = []
  nextTick(() => formRef.value?.clearValidate())
}

const init = async () => {
  loading.value = true
  resetForm()
  try {
    await fetchProjects()
    if (isEdit.value) {
      const { data } = await api.get(`/testcases/${props.testCaseId}/`)
      detail.value = data
      form.title = data.title || ''
      form.description = data.description || ''
      form.notes = data.notes || ''
      form.project_id = data.project?.id || null
      form.module_id = data.module?.id || null
      form.priority = data.priority || 'medium'
      form.test_type = data.test_type || 'functional'
      form.preconditions = convertBrToNewline(data.preconditions || '')
      form.steps = Array.isArray(data.steps) ? data.steps.map(step => ({ ...step })) : []
      form.version_ids = (data.versions || []).map(version => version.id)
      form.maintainer_id = data.author?.id || null
      await loadProjectOptions(form.project_id)
    } else {
      const requested = Number(props.projectId)
      const requestedExists = requested && projects.value.some(p => String(p.id) === String(requested))
      form.project_id = requestedExists
        ? requested
        : (getPreferredProjectId(projects.value) || projects.value[0]?.id || null)
      // 新建时保留一条空白步骤，便于直接输入
      form.steps = [{ action: '', expected: '' }]
      await loadProjectOptions(form.project_id)
    }
  } catch (error) {
    console.error('Load test case failed:', error)
    ElMessage.error(t('testcase.fetchDetailFailed'))
    emit('load-error', error)
  } finally {
    loading.value = false
  }
}

const submit = async () => {
  if (!formRef.value || submitting.value || loading.value) return false
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return false

  submitting.value = true
  emit('busy-change', true)
  try {
    const notes = notesEditor.value
      ? await notesEditor.value.uploadEmbeddedImages(form.notes)
      : form.notes
    const payload = {
      ...form,
      notes,
      preconditions: convertNewlineToBr(form.preconditions || ''),
      steps: normalizeSteps(form.steps)
    }
    // 后端的 maintainer_id 不接受显式 null：未选择维护人时直接不传该字段，
    // 新建会默认当前用户，编辑会保留原有维护人。
    if (payload.maintainer_id === null || payload.maintainer_id === undefined || payload.maintainer_id === '') {
      delete payload.maintainer_id
    }
    const { data } = isEdit.value
      ? await api.put(`/testcases/${props.testCaseId}/`, payload)
      : await api.post('/testcases/', payload)
    ElMessage.success(t(isEdit.value ? 'testcase.updateSuccess' : 'testcase.createSuccess'))
    emit('saved', data, isEdit.value ? 'edit' : 'create')
    return true
  } catch (error) {
    console.error('Save test case failed:', error)
    ElMessage.error(t(isEdit.value ? 'testcase.updateFailed' : 'testcase.createFailed'))
    return false
  } finally {
    submitting.value = false
    emit('busy-change', false)
  }
}

defineExpose({ submit, loading })

onMounted(init)
watch(() => [props.testCaseId, props.projectId], () => { init() })
</script>

<style lang="scss" scoped>
.tc-editor {
  min-height: 0;
  color: var(--el-text-color-primary);
}

.tc-editor__form,
.tc-editor__grid {
  min-height: 0;
}

.tc-editor__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  align-items: stretch;
  /* 尺寸与「用例执行详情弹窗」的 .case-execution-layout 保持一致 */
  height: min(80vh, 820px);
  min-height: min(76vh, 820px);
  max-height: 80vh;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  overflow: hidden;
  background: var(--el-bg-color);
}

.tc-editor__main {
  min-width: 0;
  min-height: 0;
  /* 弹窗整体不滚动，只有左侧内容区独立滚动 */
  overflow-y: auto;
  padding: 20px 24px 28px;
}

.tc-editor__aside {
  min-width: 0;
  min-height: 0;
  /* 右侧属性区不滚动 */
  overflow: hidden;
  padding: 20px 22px 28px;
  border-left: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}

.tc-title-field {
  /* 标题与「维护人 / 优先级」这一行保持 20px 间距 */
  margin-bottom: 20px;

  :deep(.el-form-item__content) {
    line-height: normal;
  }
  :deep(.el-textarea__inner) {
    padding: 0;
    border: 0;
    box-shadow: none;
    background: transparent;
    color: var(--el-text-color-primary);
    font-size: 20px;
    font-weight: 500;
    line-height: 1.45;
  }
  :deep(.el-textarea__inner::placeholder) {
    color: var(--el-text-color-placeholder);
  }
  :deep(.el-textarea__inner:focus) {
    box-shadow: none;
  }
}

/* 维护人 / 优先级直接用 Element Plus 原生表单选择器，不做额外装饰 */
.tc-identity {
  margin-bottom: 0;

  :deep(.el-form-item) {
    margin-bottom: 8px;
  }
  :deep(.el-select) {
    width: 100%;
  }
}

.tc-editor__divider {
  height: 1px;
  /* 抵消左栏内边距，让分隔线贯穿整个左侧区域 */
  margin: 0 -24px 16px;
  background: var(--el-border-color-light);
}

.tc-field {
  margin-bottom: 24px;

  &:last-child { margin-bottom: 0; }
}

.tc-field__label {
  margin-bottom: 10px;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
}

.tc-steps-field {
  margin-bottom: 0;

  :deep(.el-form-item__content) {
    display: block;
    line-height: normal;
  }
}

.tc-attr {
  margin-bottom: 22px;

  &:last-child { margin-bottom: 0; }
}

.tc-attr__label {
  margin-bottom: 8px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.tc-required {
  margin-right: 4px;
  color: var(--el-color-danger);
}

.tc-attr__value {
  color: var(--el-text-color-primary);
  font-size: 14px;
  line-height: 22px;
  min-height: 22px;
  word-break: break-word;
}

.tc-attr :deep(.el-select),
.tc-attr :deep(.el-tree-select) {
  width: 100%;
}

@media (max-width: 900px) {
  .tc-editor__grid {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
    min-height: 0;
    max-height: none;
  }
  .tc-editor__main,
  .tc-editor__aside {
    overflow: visible;
  }
  .tc-editor__aside {
    border-left: 0;
    border-top: 1px solid var(--el-border-color-lighter);
  }
}
</style>
