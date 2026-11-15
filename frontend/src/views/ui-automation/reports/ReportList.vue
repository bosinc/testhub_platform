<template>
  <div class="report-view">
    <div class="header">
      <div class="title-group">
        <h3>{{ $t('uiAutomation.report.title') }}</h3>
        <el-tag v-if="scheduledTaskId" closable class="task-filter-tag" @close="clearTaskFilter">
          {{ $t('uiAutomation.scheduledTask.relatedTask') }}: {{ route.query.task_name || `#${scheduledTaskId}` }}
        </el-tag>
      </div>
      <div class="actions">
        <el-select v-model="selectedProject" :placeholder="$t('uiAutomation.common.selectProject')" style="width: 200px; margin-right: 15px" @change="onProjectChange">
          <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
        </el-select>
        <el-button type="primary" @click="refreshReports">
          <el-icon><Refresh /></el-icon>
          {{ $t('uiAutomation.report.refreshReport') }}
        </el-button>
      </div>
    </div>

    <div class="content">
      <el-table :data="reports" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="test_suite_name" :label="$t('uiAutomation.report.testSuite')" min-width="200" />
        <el-table-column :label="$t('uiAutomation.scheduledTask.relatedTask')" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.scheduled_task_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" :label="$t('uiAutomation.common.status')" width="120">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('uiAutomation.report.resultDistribution')" min-width="150">
          <template #default="{ row }">
            <div v-if="getResultSegments(row).length" class="result-distribution">
              <div class="result-dist-bar">
                <el-tooltip
                  v-for="segment in getResultSegments(row)"
                  :key="segment.key"
                  placement="top"
                  effect="dark"
                  popper-class="result-dist-tooltip"
                >
                  <template #content>
                    <div class="result-dist-tip">
                      <span class="result-dist-tip__dot" :style="{ background: segment.color }"></span>
                      <span>{{ segment.label }}: {{ segment.count }} ({{ segment.percent }}%)</span>
                    </div>
                  </template>
                  <div
                    class="result-dist-segment"
                    :style="{ flexGrow: segment.count, background: segment.color }"
                  ></div>
                </el-tooltip>
              </div>
              <span class="result-dist-total">{{ getResultTotal(row) }}</span>
            </div>
            <span v-else class="result-dist-empty">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="executed_by_name" :label="$t('uiAutomation.report.executor')" width="120" />
        <el-table-column :label="$t('uiAutomation.report.duration')" width="120">
          <template #default="{ row }">
            {{ formatDuration(row.duration) }}
          </template>
        </el-table-column>
        <el-table-column prop="created_at" :label="$t('uiAutomation.report.executionTime')" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column :label="$t('uiAutomation.common.operation')" width="120" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" text type="primary" @click="viewReportDetail(row)">
                <el-icon><Document /></el-icon>
                {{ $t('uiAutomation.report.viewDetail') }}
              </el-button>
              <el-tooltip :content="$t('uiAutomation.common.delete')">
                <el-button
                  size="small"
                  text
                  type="danger"
                  :aria-label="$t('uiAutomation.common.delete')"
                  @click="deleteReport(row)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-container">
        <el-pagination
          v-model:current-page="pagination.currentPage"
          v-model:page-size="pagination.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          :total="total"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { getPreferredProjectId, useRememberedProject } from '@/utils/projectSelection'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Document, Delete } from '@element-plus/icons-vue'
import {
  getUiProjects,
  getTestExecutions,
  deleteTestExecution
} from '@/api/ui_automation'
import {
  getReportStatusType,
  getReportStatusText,
  formatReportDate,
  formatReportDuration
} from './reportHelpers'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const scheduledTaskId = computed(() => route.query.scheduled_task || undefined)
let initialized = false

// 任务筛选保存在 URL 中，刷新、前进后退和同页面切换任务都能恢复。
const clearTaskFilter = () => {
  const query = { ...route.query }
  delete query.scheduled_task
  delete query.task_name
  router.replace({ query })
}

const reports = ref([])
const projects = ref([])
const selectedProject = ref('')
useRememberedProject(selectedProject, projects)
const loading = ref(true)
const total = ref(0)
const pagination = reactive({
  currentPage: 1,
  pageSize: 20
})

// 加载项目列表
const loadProjects = async () => {
  try {
    const response = await getUiProjects({ page_size: 100 })
    projects.value = response.data.results || response.data
  } catch (error) {
    console.error('Failed to load projects:', error)
    ElMessage.error(t('uiAutomation.report.messages.loadProjectsFailed'))
  }
}

// 加载报告列表
let listRequestId = 0
const loadReports = async () => {
  // 切换任务或项目时，忽略上一个筛选条件的慢响应。
  const requestId = ++listRequestId
  loading.value = true
  try {
    const params = {
      page: pagination.currentPage,
      page_size: pagination.pageSize
    }

    if (selectedProject.value) {
      params.project = selectedProject.value
    }

    if (scheduledTaskId.value) params.scheduled_task = scheduledTaskId.value
    const response = await getTestExecutions(params)
    if (requestId !== listRequestId) return

    if (response.data.results) {
      reports.value = response.data.results
      total.value = response.data.count || 0
    } else {
      reports.value = response.data
      total.value = response.data.length
    }
  } catch (error) {
    if (requestId !== listRequestId) return
    console.error('Failed to load test reports:', error)
    ElMessage.error(t('uiAutomation.report.messages.loadFailed'))
  } finally {
    if (requestId === listRequestId) loading.value = false
  }
}

// 项目切换
const onProjectChange = async () => {
  if (scheduledTaskId.value) {
    const query = { ...route.query, project: selectedProject.value }
    delete query.scheduled_task
    delete query.task_name
    router.replace({ query })
    return
  }
  pagination.currentPage = 1
  await loadReports()
}

// 刷新报告
const refreshReports = async () => {
  await loadReports()
  ElMessage.success(t('uiAutomation.report.messages.refreshed'))
}

// 分页处理
const handleSizeChange = async () => {
  pagination.currentPage = 1
  await loadReports()
}

const handleCurrentChange = async () => {
  await loadReports()
}

// 查看报告详情：跳转到独立详情页，并把当前筛选条件带过去，便于返回时恢复列表。
const viewReportDetail = (report) => {
  const query = {}
  ;['scheduled_task', 'task_name', 'project'].forEach((key) => {
    if (route.query[key]) query[key] = route.query[key]
  })
  router.push({ path: `/ui-automation/reports/${report.id}`, query })
}

// 删除报告
const deleteReport = async (report) => {
  try {
    await ElMessageBox.confirm(
      t('uiAutomation.report.messages.deleteConfirm', { name: report.test_suite_name }),
      t('uiAutomation.report.messages.confirmDelete'),
      {
        confirmButtonText: t('uiAutomation.common.confirm'),
        cancelButtonText: t('uiAutomation.common.cancel'),
        type: 'warning'
      }
    )

    await deleteTestExecution(report.id)
    ElMessage.success(t('uiAutomation.report.messages.deleteSuccess'))
    await loadReports()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('Failed to delete report:', error)
      ElMessage.error(t('uiAutomation.report.messages.deleteFailed'))
    }
  }
}

// 辅助方法（模板直接使用，统一走 reportHelpers，详情页复用同一套逻辑）
const getStatusType = getReportStatusType
const getStatusText = (status) => getReportStatusText(t, status)
const formatDate = formatReportDate
const formatDuration = (seconds) => formatReportDuration(t, seconds)

// 结果分布：通过 / 失败 / 跳过 的配色
const RESULT_COLORS = {
  passed: '#009e5a',
  failed: '#f56c6c',
  skipped: '#87888a',
  other: '#c0c4cc'
}

// 总用例数（后端未返回时用各状态之和兜底）
const getResultTotal = (row) => {
  if (!row) return 0
  const passed = Number(row.passed_cases) || 0
  const failed = Number(row.failed_cases) || 0
  const skipped = Number(row.skipped_cases) || 0
  return Number(row.total_cases) || passed + failed + skipped
}

// 构造结果分布分段数据（数量为 0 的分段不展示）
const getResultSegments = (row) => {
  const total = getResultTotal(row)
  if (!total) return []

  const passed = Number(row?.passed_cases) || 0
  const failed = Number(row?.failed_cases) || 0
  const skipped = Number(row?.skipped_cases) || 0

  const segments = [
    { key: 'passed', count: passed, label: t('uiAutomation.report.passedCases') },
    { key: 'failed', count: failed, label: t('uiAutomation.report.failedCases') },
    { key: 'skipped', count: skipped, label: t('uiAutomation.report.skippedCases') }
  ]

  // 总数与各状态之和不一致时，剩余部分归入“其他”
  const accounted = passed + failed + skipped
  if (total > accounted) {
    segments.push({
      key: 'other',
      count: total - accounted,
      label: t('uiAutomation.report.otherCases')
    })
  }

  return segments
    .filter((segment) => segment.count > 0)
    .map((segment) => ({
      ...segment,
      color: RESULT_COLORS[segment.key],
      percent: Math.round((segment.count / total) * 100)
    }))
}

// 路由参数变化时重新加载；首次加载等待项目候选项就绪。
watch(() => [route.query.scheduled_task, route.query.project], () => {
  if (!initialized) return
  if (route.query.project) {
    selectedProject.value = projects.value.find(item => String(item.id) === String(route.query.project))?.id || ''
  }
  pagination.currentPage = 1
  loadReports()
})

onMounted(async () => {
  await loadProjects()
  if (route.query.project) {
    selectedProject.value = projects.value.find(item => String(item.id) === String(route.query.project))?.id || ''
  } else if (projects.value.length > 0) {
    selectedProject.value = getPreferredProjectId(projects.value, projects.value[0].id)
  }
  initialized = true
  await loadReports()
})
</script>

<style scoped lang="scss">
.report-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f5f5f5;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  background: white;
  padding: 10px 20px;
  border-radius: 4px;

  h3 {
    margin: 0;
    color: #303133;
    font-size: 24px;
    font-weight: 700;

    @media screen and (max-width: 1920px) {
      font-size: 22px;
    }
    @media screen and (max-width: 1600px) {
      font-size: 20px;
    }
    @media screen and (max-width: 1440px) {
      font-size: 18px;
    }
    @media screen and (max-width: 1280px) {
      font-size: 16px;
    }
    @media screen and (max-width: 480px) {
      font-size: 14px;
    }
  }

  .actions {
    display: flex;
    align-items: center;
  }
}

// 标题与「关联定时任务」筛选标签同行，标签紧贴标题右侧。
.title-group {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.task-filter-tag {
  flex-shrink: 0;
}

.content {
  flex: 1;
  overflow: auto;
  background: white;
  padding: 20px;
  border-radius: 4px;
}

// 与脚本/套件/执行记录列表一致：文字按钮 + 紧凑间距，禁止换行。
.row-actions {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 2px;
  white-space: nowrap;
}

.row-actions :deep(.el-button) {
  margin: 0;
  padding: 0 4px;
}

// 结果分布：分段堆叠条形 + 尾部总数
.result-distribution {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 200px;
}

.result-dist-bar {
  display: flex;
  align-items: center;
  gap: 1px;
  flex: 1;
  min-width: 0;
  height: 8px;
}

.result-dist-segment {
  flex-basis: 0;
  min-width: 3px;
  height: 100%;
  border-radius: 2px;
  cursor: pointer;
  transition: opacity 0.15s;

  &:first-child {
    border-top-left-radius: 4px;
    border-bottom-left-radius: 4px;
  }

  &:last-child {
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
  }

  &:hover {
    opacity: 0.85;
  }
}

.result-dist-total {
  flex: none;
  font-size: 13px;
  color: #606266;
}

.result-dist-empty {
  color: #c0c4cc;
}

.pagination-container {
  margin-top: 10px;
  display: flex;
  justify-content: flex-end;
}
</style>

<!-- 结果分布提示框挂载在 body 上，需使用非 scoped 样式 -->
<style lang="scss">
.result-dist-tooltip.el-popper {
  background: #535354 !important;
  border-color: #535354 !important;
  padding: 8px 12px;
  font-size: 13px;

  .el-popper__arrow::before {
    background: #535354 !important;
    border-color: #535354 !important;
  }
}

.result-dist-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.5;
  color: #ffffff;

  &__dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: none;
  }
}
</style>
