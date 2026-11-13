<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="page-title">{{ isEdit ? $t('reviewForm.editTitle') : $t('reviewForm.createTitle') }}</h1>
      <div>
        <el-button @click="$router.back()">{{ $t('reviewForm.back') }}</el-button>
        <el-button type="primary" @click="saveReview" :loading="saving">{{ $t('reviewForm.save') }}</el-button>
      </div>
    </div>

    <div class="form-container">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="120px">
        <el-row :gutter="24">
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.reviewTitle')" prop="title">
              <el-input v-model="form.title" :placeholder="$t('reviewForm.reviewTitlePlaceholder')" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.associatedProject')" prop="projects">
              <el-select
                v-model="form.projects"
                multiple
                :placeholder="$t('reviewForm.selectProject')"
                @change="onProjectChange"
              >
                <el-option
                  v-for="project in projects"
                  :key="project.id"
                  :label="project.name"
                  :value="project.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="24">
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.priority')" prop="priority">
              <el-select v-model="form.priority" :placeholder="$t('reviewForm.selectPriority')">
                <el-option :label="$t('reviewForm.priorityLow')" value="low" />
                <el-option :label="$t('reviewForm.priorityMedium')" value="medium" />
                <el-option :label="$t('reviewForm.priorityHigh')" value="high" />
                <el-option :label="$t('reviewForm.priorityUrgent')" value="urgent" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.deadline')" prop="deadline">
              <el-date-picker
                v-model="form.deadline"
                type="datetime"
                :placeholder="$t('reviewForm.deadlinePlaceholder')"
                format="YYYY-MM-DD HH:mm"
                value-format="YYYY-MM-DD HH:mm:ss"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item :label="$t('reviewForm.description')">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            :placeholder="$t('reviewForm.descriptionPlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="$t('reviewForm.selectTestcases')" prop="testcases">
          <div class="testcase-selector">
            <div class="search-bar">
              <el-button
                type="primary"
                plain
                :disabled="!form.projects || form.projects.length === 0"
                @click="showTestcaseSelector"
              >
                {{ $t('reviewForm.selectTestcasesBtn') }}
              </el-button>
              <span class="testcase-selection-tip">
                {{
                  form.testcases.length > 0
                    ? $t('reviewForm.selectedTestcasesCount', { count: form.testcases.length })
                    : $t('reviewForm.noTestcasesSelected')
                }}
              </span>
              <el-button
                v-if="form.testcases.length > 0"
                link
                type="primary"
                @click="showTestcaseSelector"
              >
                {{ $t('reviewForm.modifyTestcasesSelection') }}
              </el-button>
            </div>
          </div>
        </el-form-item>

        <el-form-item :label="$t('reviewForm.reviewers')" prop="reviewers">
          <el-select
            v-model="form.reviewers"
            multiple
            :placeholder="$t('reviewForm.selectReviewers')"
            @change="onReviewersChange"
          >
            <el-option
              v-for="user in projectUsers"
              :key="user.id"
              :label="user.username"
              :value="user.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="$t('reviewForm.reviewTemplate')">
          <el-select v-model="form.template" :placeholder="$t('reviewForm.selectTemplate')" @change="applyTemplate">
            <el-option
              v-for="template in templates"
              :key="template.id"
              :label="template.name"
              :value="template.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
    </div>

    <!-- 用例选择对话框（公共组件） -->
    <TestCaseSelectorDialog
      v-model="testcaseSelectorVisible"
      :projects="selectorProjects"
      :selected-ids="form.testcases"
      :selected-cases="selectedTestcases"
      @confirm="onTestcaseSelected" />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import TestCaseSelectorDialog from '@/components/testcases/TestCaseSelectorDialog.vue'
import api from '@/utils/api'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const isEdit = computed(() => !!route.params.id)

const formRef = ref()
const saving = ref(false)
const testcaseSelectorVisible = ref(false)

const projects = ref([])
const projectUsers = ref([])
const templates = ref([])
// 已关联但可能不在候选用例列表中的用例（如已归档），透传给选择弹窗保留勾选
const selectedTestcases = ref([])

const form = reactive({
  title: '',
  description: '',
  projects: [],
  priority: 'medium',
  deadline: '',
  testcases: [],
  reviewers: [],
  template: ''
})

// 用例选择弹窗的项目范围：[{ id, name }]
const selectorProjects = computed(() => {
  return (form.projects || [])
    .map(id => projects.value.find(project => String(project.id) === String(id)))
    .filter(Boolean)
    .map(project => ({ id: project.id, name: project.name }))
})

const rules = computed(() => ({
  title: [{ required: true, message: t('reviewForm.titleRequired'), trigger: 'blur' }],
  projects: [{ required: true, message: t('reviewForm.projectRequired'), trigger: 'change' }],
  deadline: [{ required: true, message: t('reviewForm.deadlineRequired'), trigger: 'change' }],
  testcases: [{ required: true, message: t('reviewForm.testcasesRequired'), trigger: 'change' }],
  reviewers: [{ required: true, message: t('reviewForm.reviewersRequired'), trigger: 'change' }]
}))

const fetchProjects = async () => {
  try {
    const response = await api.get('/projects/')
    projects.value = response.data.results || response.data
  } catch (error) {
    ElMessage.error(t('reviewForm.fetchProjectsFailed'))
  }
}

const fetchProjectUsers = async () => {
  try {
    const response = await api.get('/auth/users/')
    projectUsers.value = response.data.results || response.data || []
    console.log('All users:', projectUsers.value)
  } catch (error) {
    console.error('Fetch users failed:', error)
    ElMessage.error(t('reviewForm.fetchUsersFailed'))
    projectUsers.value = []
  }
}

const fetchTemplates = async (projectIds) => {
  try {
    // 如果没有选择项目，清空模板列表
    if (!projectIds || projectIds.length === 0) {
      templates.value = []
      return
    }

    // 获取所有选中项目的模板
    const promises = projectIds.map(projectId =>
      api.get('/reviews/review-templates/', { params: { project: projectId } })
    )

    const responses = await Promise.all(promises)
    const allTemplates = []

    responses.forEach(response => {
      const temps = response.data.results || response.data || []
      allTemplates.push(...temps)
    })

    // 去重（基于模板ID）
    const uniqueTemplates = allTemplates.filter((template, index, self) =>
      index === self.findIndex(t => t.id === template.id)
    )

    templates.value = uniqueTemplates
  } catch (error) {
    console.error('Fetch templates failed:', error)
  }
}

const onProjectChange = (projectIds) => {
  // 项目变化时清空已选用例（用例选择弹窗按项目范围重新加载）
  selectedTestcases.value = []

  if (projectIds && projectIds.length > 0) {
    fetchTemplates(projectIds)
  } else {
    templates.value = []
  }

  // 清空相关选择
  form.reviewers = []
  form.testcases = []
}

const showTestcaseSelector = () => {
  if (!form.projects || form.projects.length === 0) {
    ElMessage.warning(t('reviewForm.selectProjectFirst'))
    return
  }
  testcaseSelectorVisible.value = true
}

// 公共选择弹窗回传已选用例 ID
const onTestcaseSelected = (ids) => {
  form.testcases = [...new Set(ids)]
  // 已通过弹窗确认过一次，后续以 form.testcases 为准，避免回填的旧用例对象残留
  selectedTestcases.value = []
  testcaseSelectorVisible.value = false
  formRef.value?.clearValidate('testcases')
}

const onReviewersChange = () => {
  // 可以在这里添加评审人员变更的逻辑
}

const applyTemplate = async (templateId) => {
  if (!templateId) return

  try {
    const template = templates.value.find(t => t.id === templateId)
    if (template) {
      // 应用模板的默认评审人员
      form.reviewers = template.default_reviewers.map(u => u.id)
    }
  } catch (error) {
    console.error('Apply template failed:', error)
  }
}

const saveReview = async () => {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
    saving.value = true

    // 截止日期预处理：未选则传 null（字段可空）；有值则转 ISO-8601 格式，
    // 避免 "YYYY-MM-DD HH:mm:ss"（空格分隔）被后端 DateTimeField 判为格式错误
    let deadline = null
    if (form.deadline) {
      deadline = form.deadline.replace(' ', 'T')
    }

    const data = {
      ...form,
      testcases: form.testcases,
      reviewers: form.reviewers,
      deadline,
      // 确保包含 template 字段
      template: form.template || null
    }

    if (isEdit.value) {
      await api.put(`/reviews/reviews/${route.params.id}/`, data)
      ElMessage.success(t('reviewForm.updateSuccess'))
    } else {
      await api.post('/reviews/reviews/', data)
      ElMessage.success(t('reviewForm.createSuccess'))
    }

    router.push('/ai-generation/reviews')

  } catch (error) {
    if (error.response?.data) {
      const errors = Object.values(error.response.data).flat()
      ElMessage.error(errors[0] || t('reviewForm.saveFailed'))
    } else {
      ElMessage.error(t('reviewForm.saveFailed'))
    }
  } finally {
    saving.value = false
  }
}

const findMatchingTemplate = (review, templateList) => {
  if (!templateList || templateList.length === 0) return null

  // 获取评审的项目ID列表和评审人ID列表
  const reviewProjectIds = review.projects.map(p => p.id).sort()
  const reviewReviewerIds = review.assignments.map(a => a.reviewer.id).sort()

  let bestMatch = null
  let bestScore = 0

  for (const template of templateList) {
    let score = 0

    // 检查项目匹配度
    const templateProjectIds = (template.project || []).map(p => p.id).sort()
    const projectIntersection = reviewProjectIds.filter(id => templateProjectIds.includes(id))
    if (projectIntersection.length > 0) {
      score += projectIntersection.length * 2 // 项目匹配权重更高
    }

    // 检查默认评审人匹配度
    const templateReviewerIds = (template.default_reviewers || []).map(r => r.id).sort()
    const reviewerIntersection = reviewReviewerIds.filter(id => templateReviewerIds.includes(id))
    if (reviewerIntersection.length > 0) {
      score += reviewerIntersection.length // 评审人匹配
    }

    // 如果有更高的匹配分数，更新最佳匹配
    if (score > bestScore) {
      bestScore = score
      bestMatch = template
    }
  }

  // 只有当匹配分数大于0时才返回匹配的模板
  return bestScore > 0 ? bestMatch : null
}

const fetchReviewData = async (reviewId) => {
  try {
    const response = await api.get(`/reviews/reviews/${reviewId}/`)
    const review = response.data

    // 填充表单数据
    form.title = review.title
    form.description = review.description
    form.projects = review.projects.map(p => p.id)
    form.priority = review.priority
    form.deadline = review.deadline
    form.reviewers = review.assignments.map(a => a.reviewer.id)

    // 处理测试用例
    selectedTestcases.value = review.testcases
    form.testcases = review.testcases.map(tc => tc.id)

    // 加载项目相关数据
    if (form.projects.length > 0) {
      await fetchTemplates(form.projects)

      // 在模板加载完成后，尝试找到匹配的模板
      const matchingTemplate = findMatchingTemplate(review, templates.value)
      if (matchingTemplate) {
        form.template = matchingTemplate.id
      }
    }

  } catch (error) {
    console.error('Fetch review data failed:', error)
    ElMessage.error(t('reviewForm.fetchReviewFailed'))
    router.push('/ai-generation/reviews')
  }
}

onMounted(async () => {
  await fetchProjects()
  fetchProjectUsers() // 页面加载时就获取所有用户

  if (isEdit.value) {
    // 如果是编辑模式，加载现有数据
    fetchReviewData(route.params.id)
  } else {
    // 创建模式，检查是否有模板参数
    const templateId = route.query.template
    if (templateId) {
      try {
        // 获取模板详情
        const response = await api.get(`/reviews/review-templates/${templateId}/`)
        const template = response.data

        // 设置模板ID到表单
        form.template = parseInt(templateId)

        // 自动填充项目
        if (template.project && template.project.length > 0) {
          form.projects = template.project.map(p => p.id)

          // 触发项目变更，加载对应的用例和模板
          await onProjectChange(form.projects)

          // 应用模板的默认评审人
          if (template.default_reviewers && template.default_reviewers.length > 0) {
            form.reviewers = template.default_reviewers.map(u => u.id)
          }
        }
      } catch (error) {
        console.error('加载模板失败:', error)
      }
    }
  }
})
</script>

<style lang="scss" scoped>
.testcase-selector {
  .search-bar {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .testcase-selection-tip {
    color: #606266;
    font-size: 14px;
  }
}
</style>
