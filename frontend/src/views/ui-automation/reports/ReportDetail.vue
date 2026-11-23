<template>
  <div class="report-detail-page page-container">
    <!-- 顶栏：返回图标 + 标题 + 状态 + 刷新（返回只保留图标，不显示文案） -->
    <div class="page-header">
      <div class="header-left">
        <el-button
          link
          class="back-btn"
          :icon="ArrowLeft"
          :aria-label="$t('uiAutomation.common.back')"
          @click="goBack"
        />
        <h3 class="page-title">{{ $t('uiAutomation.report.reportDetail') }}</h3>
        <span class="report-no">#{{ reportId }}</span>
        <el-tag v-if="report" :type="getReportStatusType(report.status)">
          {{ getReportStatusText(t, report.status) }}
        </el-tag>
      </div>
      <div class="header-right">
        <el-button :icon="Refresh" :loading="loading" @click="loadReport">
          {{ $t('uiAutomation.common.refresh') }}
        </el-button>
      </div>
    </div>

    <div class="page-body" v-loading="loading">
      <el-empty
        v-if="!loading && !report"
        :description="$t('uiAutomation.report.messages.notFound')"
      />

      <template v-else-if="report">
        <!-- 基础信息 -->
        <el-card shadow="never" class="detail-card">
          <template #header>
            <span class="card-title">{{ $t('uiAutomation.report.basicInfo') }}</span>
          </template>
          <el-descriptions :column="3" border>
            <el-descriptions-item :label="$t('uiAutomation.report.reportId')">{{ report.id }}</el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.scheduledTask.relatedTask')">
              {{ report.scheduled_task_name || '-' }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.testSuite')">
              {{ report.test_suite_name || '-' }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.executionStatus')">
              <el-tag :type="getReportStatusType(report.status)">
                {{ getReportStatusText(t, report.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.executor')">
              {{ report.executed_by_name || '-' }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.testEngine')">
              {{ getReportEngineText(report.engine) }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.browser')">
              {{ getReportBrowserText(report.browser) }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.executionMode')">
              {{ report.headless ? $t('uiAutomation.report.headlessMode') : $t('uiAutomation.report.headedMode') }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.startTime')">
              {{ formatReportDate(report.started_at) }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.duration')">
              {{ formatReportDuration(t, report.duration) }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.executionTime')">
              {{ formatReportDate(report.created_at) }}
            </el-descriptions-item>
            <el-descriptions-item :label="$t('uiAutomation.report.endTime')">
              {{ formatReportDate(report.finished_at) }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <!-- 测试统计：单行指标条（通过率 / 已测 / 分布条 / 图例），与执行详情页一致 -->
        <div class="report-metrics" :title="progressTooltip">
          <div class="report-metrics__stat">
            <span class="report-metrics__label">{{ $t('uiAutomation.report.passRate') }}</span>
            <span class="report-metrics__value">{{ stats.passRate }}%</span>
          </div>
          <div class="report-metrics__stat">
            <span class="report-metrics__label">{{ $t('uiAutomation.report.totalCases') }}</span>
            <span class="report-metrics__value">{{ stats.total }}</span>
          </div>
          <div class="report-metrics__track">
            <div
              v-for="segment in progressSegments"
              :key="segment.key"
              class="report-metrics__segment"
              :class="segment.class"
              :style="{ flexGrow: segment.value }"
            ></div>
          </div>
          <div class="report-metrics__legend">
            <span
              v-for="segment in legendSegments"
              :key="segment.key"
              class="legend"
              :class="segment.legendClass"
            >{{ $t(segment.labelKey) }} {{ segment.value }}</span>
          </div>
        </div>

        <!-- 执行结果详情 -->
        <el-card shadow="never" class="detail-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">{{ $t('uiAutomation.report.executionResultDetail') }}</span>
              <span class="card-subtitle">
                {{ $t('uiAutomation.report.totalCases') }}: {{ caseList.length }}
              </span>
            </div>
          </template>
          <el-table :data="caseList" border empty-text="-">
            <el-table-column type="index" :label="$t('uiAutomation.report.sequence')" width="60" />
            <el-table-column prop="test_case_name" :label="$t('uiAutomation.report.testCase')" min-width="220" show-overflow-tooltip />
            <el-table-column :label="$t('uiAutomation.report.executionStatus')" width="110" align="center">
              <template #default="{ row }">
                <el-tag :type="row.status === 'passed' ? 'success' : 'danger'">
                  {{ row.status === 'passed' ? $t('uiAutomation.report.casePassed') : $t('uiAutomation.report.caseFailed') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('uiAutomation.report.stepCount')" width="100" align="center">
              <template #default="{ row }">{{ row.steps ? row.steps.length : 0 }}</template>
            </el-table-column>
            <el-table-column :label="$t('uiAutomation.report.startTime')" width="180" align="center">
              <template #default="{ row }">{{ formatReportDate(row.start_time) }}</template>
            </el-table-column>
            <el-table-column :label="$t('uiAutomation.common.operation')" width="120" align="center">
              <template #default="{ row }">
                <el-button type="primary" link @click="viewCaseDetail(row)">
                  {{ $t('uiAutomation.report.viewDetail') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 报告级错误信息 -->
        <el-card v-if="report.error_message" shadow="never" class="detail-card">
          <template #header>
            <span class="card-title">{{ $t('uiAutomation.report.errorInfo') }}</span>
          </template>
          <div class="errors-container">
            <div class="error-item">
              <div class="error-content">
                <pre class="error-text">{{ report.error_message }}</pre>
              </div>
            </div>
          </div>
        </el-card>
      </template>
    </div>

    <!-- 用例详情对话框 -->
    <el-dialog
      v-model="showCaseDetailDialog"
      :title="`${$t('uiAutomation.report.caseDetail')} - ${currentCase?.test_case_name || ''}`"
      width="900px"
      :close-on-click-modal="false"
    >
      <div v-if="currentCase" class="case-detail">
        <!-- 用例执行成功 - 只显示执行日志 -->
        <div v-if="currentCase.status === 'passed'">
          <h4>{{ $t('uiAutomation.report.executionLogs') }}</h4>
          <div class="log-container">
            <div v-for="(step, index) in currentCase.steps || []" :key="index" class="log-item">
              <div class="log-header">
                <el-tag :type="step.success ? 'success' : 'danger'" size="small">
                  {{ $t('uiAutomation.report.step') }} {{ step.step_number }}
                </el-tag>
                <span class="log-action">{{ getActionText(step.action_type) }}</span>
                <span class="log-desc">{{ step.description }}</span>
              </div>
              <div v-if="step.error" class="log-error">
                <el-icon><WarningFilled /></el-icon>
                {{ step.error }}
              </div>
            </div>
          </div>
        </div>

        <!-- 用例执行失败 - 显示执行日志、失败截图、错误信息三个tab -->
        <div v-else>
          <el-tabs v-model="activeTab" type="border-card">
            <el-tab-pane :label="$t('uiAutomation.report.executionLogs')" name="logs">
              <div class="log-container">
                <div v-for="(step, index) in currentCase.steps || []" :key="index" class="log-item">
                  <div class="log-header">
                    <el-tag :type="step.success ? 'success' : 'danger'" size="small">
                      {{ $t('uiAutomation.report.step') }} {{ step.step_number }}
                    </el-tag>
                    <span class="log-action">{{ getActionText(step.action_type) }}</span>
                    <span class="log-desc">{{ step.description }}</span>
                  </div>
                  <div v-if="step.error" class="log-error">
                    <el-icon><WarningFilled /></el-icon>
                    {{ step.error }}
                  </div>
                </div>
              </div>
            </el-tab-pane>

            <el-tab-pane :label="$t('uiAutomation.report.failedScreenshots')" name="screenshots">
              <div v-if="currentCase.screenshots && currentCase.screenshots.length > 0" class="screenshot-container">
                <div v-for="(screenshot, index) in currentCase.screenshots" :key="index" class="screenshot-item">
                  <h5>{{ screenshot.description || `${$t('uiAutomation.report.screenshot')} ${index + 1}` }}</h5>
                  <img
                    :src="screenshot.url"
                    :alt="screenshot.description"
                    class="screenshot-img"
                    @click="openScreenshotPreview(screenshot)"
                  />
                  <p class="screenshot-time">{{ screenshot.timestamp }}</p>
                </div>
              </div>
              <el-empty v-else :description="$t('uiAutomation.report.noScreenshots')" />
            </el-tab-pane>

            <el-tab-pane :label="$t('uiAutomation.report.errorInfo')" name="error">
              <div class="errors-container">
                <div v-if="currentCase.error" class="error-item">
                  <div class="error-content">
                    <pre class="error-text">{{ currentCase.error }}</pre>
                  </div>
                </div>
                <el-empty v-else :description="$t('uiAutomation.report.noError')" />
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
      </div>
      <template #footer>
        <el-button @click="showCaseDetailDialog = false">{{ $t('uiAutomation.common.close') }}</el-button>
      </template>
    </el-dialog>

    <!-- 失败截图放大预览 -->
    <ScreenshotPreviewDialog
      v-model="showScreenshotPreview"
      :screenshot="currentScreenshot"
      :fallback-title="$t('uiAutomation.report.screenshot')"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh, WarningFilled } from '@element-plus/icons-vue'
import { getUiActionLabel } from '@/utils/ui-automation'
import ScreenshotPreviewDialog from '@/components/ui-automation/ScreenshotPreviewDialog.vue'
import { getTestExecutionDetail } from '@/api/ui_automation'
import {
  getReportStatusType,
  getReportStatusText,
  getReportEngineText,
  getReportBrowserText,
  formatReportDate,
  formatReportDuration
} from './reportHelpers'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const reportId = computed(() => route.params.id)
const report = ref(null)
const loading = ref(false)

// 用例详情对话框
const showCaseDetailDialog = ref(false)
const currentCase = ref(null)
const activeTab = ref('logs')

// 失败截图放大预览
const showScreenshotPreview = ref(false)
const currentScreenshot = ref(null)
const openScreenshotPreview = (screenshot) => {
  if (!screenshot?.url) return
  currentScreenshot.value = screenshot
  showScreenshotPreview.value = true
}

const caseList = computed(() => report.value?.result_data?.test_cases || [])

// 统计沿用报告自身的字段：总用例数 / 通过数 / 失败数 / 跳过数 / 通过率
const stats = computed(() => {
  const data = report.value || {}
  return {
    total: Number(data.total_cases) || 0,
    passed: Number(data.passed_cases) || 0,
    failed: Number(data.failed_cases) || 0,
    skipped: Number(data.skipped_cases) || 0,
    passRate: Number(data.pass_rate) || 0
  }
})

// 分布条分段：通过 / 失败 / 跳过，颜色与执行详情页同源（EP 语义色）
const METRIC_SEGMENTS = [
  { key: 'passed', class: 'seg--passed', legendClass: 'legend--passed', labelKey: 'uiAutomation.report.passedCases' },
  { key: 'failed', class: 'seg--failed', legendClass: 'legend--failed', labelKey: 'uiAutomation.report.failedCases' },
  { key: 'skipped', class: 'seg--skipped', legendClass: 'legend--skipped', labelKey: 'uiAutomation.report.skippedCases' }
]

const withValues = () => METRIC_SEGMENTS.map((segment) => ({ ...segment, value: stats.value[segment.key] || 0 }))

// 条形只画有数据的段，图例始终展示全部状态，避免数量为 0 时布局跳动
const progressSegments = computed(() => withValues().filter((segment) => segment.value > 0))
const legendSegments = computed(() => withValues())

const progressTooltip = computed(() =>
  progressSegments.value.map((segment) => `${t(segment.labelKey)} ${segment.value}`).join(' · ')
)

// 列表不包含执行结果和日志，详情页按报告 ID 单独读取完整数据。
let detailRequestId = 0
const loadReport = async () => {
  const id = reportId.value
  if (!id) return
  const requestId = ++detailRequestId
  loading.value = true
  try {
    const response = await getTestExecutionDetail(id)
    if (requestId !== detailRequestId) return
    report.value = response.data
  } catch (error) {
    if (requestId !== detailRequestId) return
    report.value = null
    console.error('加载测试报告详情失败:', error)
    ElMessage.error(t('uiAutomation.report.messages.loadDetailFailed'))
  } finally {
    if (requestId === detailRequestId) loading.value = false
  }
}

// 返回列表：保留从列表带过来的筛选条件（关联任务 / 项目）。
const goBack = () => {
  const query = {}
  ;['scheduled_task', 'task_name', 'project'].forEach((key) => {
    if (route.query[key]) query[key] = route.query[key]
  })
  router.push({ path: '/ui-automation/reports', query })
}

const viewCaseDetail = (caseData) => {
  currentCase.value = caseData
  activeTab.value = 'logs'
  showCaseDetailDialog.value = true
}

// 获取操作类型文本（执行日志用），与步骤编辑器共用同一套文案键
const getActionText = (actionType) => getUiActionLabel(t, actionType)

watch(reportId, () => {
  report.value = null
  loadReport()
}, { immediate: true })
</script>

<style scoped lang="scss">
.report-detail-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.page-header {
  flex: none;

  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .header-right {
    flex: none;
  }

  /* 返回按钮只留图标，尺寸与标题行对齐 */
  .back-btn {
    flex: none;
    padding: 0;
    font-size: 18px;
  }

  .page-title {
    white-space: nowrap;
  }

  .report-no {
    color: #909399;
    font-size: 14px;
  }
}

.page-body {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 10px;
  min-height: 200px;
}

.detail-card {
  border: none;
  border-radius: 4px;
  margin-bottom: 10px;

  &:last-child {
    margin-bottom: 0;
  }

  :deep(.el-card__header) {
    padding: 12px 20px;
    background: #fafafa;
  }

  :deep(.el-card__body) {
    padding: 20px;
  }
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-title {
  color: #303133;
  font-size: 16px;
  font-weight: 600;
}

.card-subtitle {
  color: #909399;
  font-size: 13px;
}

// 测试统计指标条：与执行详情页（ExecutionDetailView）同一套视觉
.report-metrics {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding: 10px 14px;
  margin-bottom: 10px;
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.report-metrics__stat {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.report-metrics__label {
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
}

.report-metrics__value {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.report-metrics__track {
  display: flex;
  align-items: stretch;
  gap: 1px;
  flex: 1 1 160px;
  min-width: 110px;
  height: 8px;
}

.report-metrics__segment {
  flex: 1 1 0;
  min-width: 2px;
  border-radius: 4px;
  transition: flex-grow 300ms cubic-bezier(0.4, 0, 0.2, 1);
}

.report-metrics__segment.seg--passed { background: var(--el-color-success); }
.report-metrics__segment.seg--failed { background: var(--el-color-danger); }
.report-metrics__segment.seg--skipped { background: var(--el-color-warning); }

.report-metrics__legend {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.legend {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.legend::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-border-color-lighter);
}

.legend--passed::before { background: var(--el-color-success); }
.legend--failed::before { background: var(--el-color-danger); }
.legend--skipped::before { background: var(--el-color-warning); }

// 统一的错误信息样式
.errors-container {
  padding: 0;
  overflow-y: auto;
}

.error-item {
  background: #fff;
  border: 2px solid #f56c6c;
  border-radius: 8px;
  padding: 16px;

  &:last-child {
    margin-bottom: 0;
  }
}

.error-content {
  display: flex;
  flex-direction: column;
}

.error-text {
  margin: 0;
  padding: 15px;
  background: #2d2d2d;
  color: #ff6b6b;
  border-radius: 4px;
  font-family: 'Courier New', Courier, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-x: auto;
}

// 用例详情（对话框内）
.case-detail {
  h4 {
    margin: 0 0 16px 0;
    color: #303133;
    font-size: 16px;
  }

  .log-container {
    max-height: 500px;
    overflow-y: auto;
    background: #f5f7fa;
    padding: 15px;
    border-radius: 4px;

    .log-item {
      margin-bottom: 15px;
      padding: 12px;
      background: #fff;
      border-radius: 4px;
      border-left: 3px solid #409eff;

      &:last-child {
        margin-bottom: 0;
      }

      .log-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 8px;

        .log-action {
          font-weight: 500;
          color: #606266;
        }

        .log-desc {
          color: #909399;
          font-size: 14px;
        }
      }

      .log-error {
        display: flex;
        align-items: center;
        gap: 8px;
        color: #f56c6c;
        background: #fef0f0;
        padding: 8px 12px;
        border-radius: 4px;
        margin-top: 8px;
        font-size: 14px;
      }
    }
  }

  .screenshot-container {
    max-height: 600px;
    overflow-y: auto;
    padding: 10px;

    .screenshot-item {
      margin-bottom: 30px;
      text-align: center;

      h5 {
        margin: 0 0 15px 0;
        color: #303133;
        font-size: 14px;
      }

      .screenshot-img {
        max-width: 100%;
        border: 1px solid #dcdfe6;
        border-radius: 4px;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
        cursor: zoom-in;
      }

      .screenshot-time {
        margin: 10px 0 0 0;
        color: #909399;
        font-size: 12px;
      }
    }
  }
}
</style>
