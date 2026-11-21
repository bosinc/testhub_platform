<template>
  <div class="page-container">
    <div class="page-header">
      <div class="title-group">
        <h1 class="page-title">{{ $t('apiTesting.report.title') }}</h1>
        <el-tag v-if="scheduledTaskId" closable class="task-filter-tag" @close="clearTaskFilter">
          {{ $t('apiTesting.scheduledTask.relatedTask') }}: {{ route.query.task_name || `#${scheduledTaskId}` }}
        </el-tag>
      </div>
      <div class="actions">
        <el-button
          type="danger"
          :disabled="selectedIds.length === 0"
          @click="handleBatchDelete"
        >
          {{ $t('apiTesting.report.batchDelete') }}
          <span v-if="selectedIds.length">({{ selectedIds.length }})</span>
        </el-button>
        <el-button type="primary" @click="refreshReports">{{ $t('apiTesting.report.refreshReport') }}</el-button>
      </div>
    </div>

    <div class="card-container">
      <el-table
        :data="reports"
        v-loading="loading"
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="40" align="center" />
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="test_suite_name" :label="$t('apiTesting.report.testSuite')" min-width="200" />
        <el-table-column :label="$t('apiTesting.scheduledTask.relatedTask')" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.scheduled_task_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" :label="$t('apiTesting.common.status')" width="100">
          <template #default="scope">
            <el-tag :type="getStatusType(scope.row.status)">
              {{ getStatusText(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('apiTesting.report.resultDistribution')" min-width="180">
          <template #default="scope">
            <div v-if="getResultSegments(scope.row).length" class="result-distribution">
              <div class="result-dist-bar">
                <el-tooltip
                  v-for="segment in getResultSegments(scope.row)"
                  :key="segment.key"
                  placement="top"
                  effect="dark"
                  popper-class="api-result-dist-tooltip"
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
              <span class="result-dist-total">{{ getResultTotal(scope.row) }}</span>
            </div>
            <span v-else class="result-dist-empty">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="executed_by_name" :label="$t('apiTesting.report.executor')" width="120" />
        <el-table-column :label="$t('apiTesting.report.duration')" width="90">
          <template #default="scope">
            {{ formatDuration(scope.row.duration) }}
          </template>
        </el-table-column>
        <el-table-column prop="created_at" :label="$t('apiTesting.report.executionTime')" width="180">
          <template #default="scope">
            {{ formatDate(scope.row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column :label="$t('apiTesting.common.operation')" width="130" fixed="right" align="center">
          <template #default="scope">
            <div class="row-actions">
              <el-button size="small" text type="primary" @click="viewReportDetail(scope.row)">
                {{ $t('apiTesting.report.generateAndViewReport') }}
              </el-button>
              <el-button size="small" text type="danger" @click="handleDelete(scope.row)">
                {{ $t('apiTesting.common.delete') }}
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-container">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  batchDeleteApiSuiteExecutions,
  deleteApiSuiteExecution,
  getApiSuiteExecutions
} from '@/api/api-testing'
import dayjs from 'dayjs'
import { useRouter } from 'vue-router'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const scheduledTaskId = computed(() => route.query.scheduled_task || '')
const reports = ref([])
const loading = ref(false)
const selectedIds = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

const clearTaskFilter = () => {
  const query = { ...route.query }
  delete query.scheduled_task
  delete query.task_name
  router.replace({ query }).then(loadReports)
}

const loadReports = async () => {
  loading.value = true
  selectedIds.value = []
  try {
    const response = await getApiSuiteExecutions({
      page: currentPage.value,
      page_size: pageSize.value,
      ...(route.query.scheduled_task ? { scheduled_task: route.query.scheduled_task } : {})
    })
    reports.value = response.data.results || response.data
    total.value = response.data.count ?? reports.value.length
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadReports'))
  } finally {
    loading.value = false
  }
}

const refreshReports = async () => {
  await loadReports()
}

const handleSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1
  loadReports()
}

const handleCurrentChange = (page) => {
  currentPage.value = page
  loadReports()
}

const viewReportDetail = (report) => {
  router.push(`/api-testing/reports/${report.id}`)
}

const handleSelectionChange = (selection) => {
  selectedIds.value = selection.map((report) => report.id)
}

const handleDelete = async (report) => {
  try {
    await ElMessageBox.confirm(
      t('apiTesting.report.confirmDelete'),
      t('apiTesting.common.confirm'),
      { type: 'warning' }
    )
    await deleteApiSuiteExecution(report.id)
    ElMessage.success(t('apiTesting.messages.success.delete'))
    if (reports.value.length === 1 && currentPage.value > 1) {
      currentPage.value -= 1
    }
    await loadReports()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(t('apiTesting.messages.error.deleteFailed'))
    }
  }
}

const handleBatchDelete = async () => {
  if (!selectedIds.value.length) return

  try {
    await ElMessageBox.confirm(
      t('apiTesting.report.confirmBatchDelete', { n: selectedIds.value.length }),
      t('apiTesting.common.confirm'),
      { type: 'warning' }
    )
    await batchDeleteApiSuiteExecutions(selectedIds.value)
    ElMessage.success(t('apiTesting.messages.success.batchDeleteSuccess'))
    if (selectedIds.value.length >= reports.value.length && currentPage.value > 1) {
      currentPage.value -= 1
    }
    selectedIds.value = []
    await loadReports()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(t('apiTesting.messages.error.batchDeleteFailed'))
    }
  }
}

const getStatusType = (status) => {
  const typeMap = {
    'PENDING': 'info',
    'RUNNING': 'warning',
    'COMPLETED': 'success',
    'FAILED': 'danger',
    'CANCELLED': 'info'
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status) => {
  const statusKey = {
    'PENDING': 'pending',
    'RUNNING': 'running',
    'COMPLETED': 'completed',
    'FAILED': 'failed',
    'CANCELLED': 'cancelled'
  }[status]
  return statusKey ? t(`apiTesting.report.status.${statusKey}`) : status
}

// 结果分布：通过 / 失败的分段堆叠条，与 UI 自动化报告列表保持一致。
const RESULT_COLORS = {
  passed: '#009e5a',
  failed: '#f56c6c'
}

const getResultTotal = (report) => {
  const passed = Number(report?.passed_scenarios) || 0
  const failed = Number(report?.failed_scenarios) || 0
  return Number(report?.total_scenarios) || passed + failed
}

const getResultSegments = (report) => {
  const total = getResultTotal(report)
  if (!total) return []

  return [
    { key: 'passed', count: Number(report?.passed_scenarios) || 0, label: t('apiTesting.report.passedScenarios') },
    { key: 'failed', count: Number(report?.failed_scenarios) || 0, label: t('apiTesting.report.failedScenarios') }
  ]
    .filter((segment) => segment.count > 0)
    .map((segment) => ({
      ...segment,
      color: RESULT_COLORS[segment.key],
      percent: Math.round((segment.count / total) * 100)
    }))
}

const formatDuration = (seconds) => {
  const value = Number(seconds) || 0
  if (value <= 0) return `0${t('apiTesting.report.seconds')}`
  if (value < 60) return `${value.toFixed(1)}${t('apiTesting.report.seconds')}`
  const minutes = Math.floor(value / 60)
  const remainingSeconds = Math.round(value % 60)
  return `${minutes}${t('apiTesting.report.minutes')}${remainingSeconds}${t('apiTesting.report.seconds')}`
}

const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm:ss')
}

onMounted(() => {
  loadReports()
})
</script>

<style scoped>
.page-container {
  height: 100%;
  overflow-y: auto;
  background: #f5f5f5;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  background: #fff;
  padding: 10px 20px;
  border-radius: 4px;
}

.title-group {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-container .page-header .page-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: #303133;
}

/* 与 UI 自动化对应页面保持一致的响应式标题字号。 */
@media screen and (max-width: 1920px) {
  .page-container .page-header .page-title { font-size: 22px; }
}

@media screen and (max-width: 1600px) {
  .page-container .page-header .page-title { font-size: 20px; }
}

@media screen and (max-width: 1440px) {
  .page-container .page-header .page-title { font-size: 18px; }
}

@media screen and (max-width: 1280px) {
  .page-container .page-header .page-title { font-size: 16px; }
}

@media screen and (max-width: 480px) {
  .page-container .page-header .page-title { font-size: 14px; }
}

.card-container {
  min-height: calc(100% - 70px);
  box-sizing: border-box;
  background: #fff;
  padding: 20px;
  border-radius: 4px;
  overflow: auto;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: nowrap;
  white-space: nowrap;
}

.row-actions :deep(.el-button) {
  margin: 0;
  padding: 0 4px;
}

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
}

.result-dist-segment:first-child {
  border-top-left-radius: 4px;
  border-bottom-left-radius: 4px;
}

.result-dist-segment:last-child {
  border-top-right-radius: 4px;
  border-bottom-right-radius: 4px;
}

.result-dist-segment:hover {
  opacity: 0.85;
}

.result-dist-total {
  flex: none;
  font-size: 13px;
  color: #606266;
}

.result-dist-empty {
  color: #c0c4cc;
}
</style>

<style>
.api-result-dist-tooltip.el-popper {
  background: #535354 !important;
  border-color: #535354 !important;
  padding: 8px 12px;
  font-size: 13px;
}

.api-result-dist-tooltip.el-popper .el-popper__arrow::before {
  background: #535354 !important;
  border-color: #535354 !important;
}

.api-result-dist-tooltip .result-dist-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.5;
  color: #ffffff;
}

.api-result-dist-tooltip .result-dist-tip__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
</style>
