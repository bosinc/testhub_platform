<template>
  <div class="report-detail-page page-container">
    <div class="page-header">
      <div class="header-left">
        <el-button
          link
          class="back-btn"
          :icon="ArrowLeft"
          :aria-label="$t('apiTesting.common.back')"
          @click="goBack"
        />
        <h1 class="page-title">{{ $t('apiTesting.report.reportDetail') }}</h1>
        <span class="report-no">#{{ reportId }}</span>
        <el-tag v-if="report" :type="getStatusType(report.status)">
          {{ getStatusText(report.status) }}
        </el-tag>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadReport">
        {{ $t('apiTesting.common.refresh') }}
      </el-button>
    </div>

    <div class="page-body" v-loading="loading">
      <el-empty v-if="!loading && !report" :description="$t('apiTesting.report.notFound')" />

      <template v-else-if="report">
        <el-card shadow="never" class="detail-card">
          <template #header>
            <span class="card-title">{{ $t('apiTesting.report.basicInfo') }}</span>
          </template>
          <el-descriptions :column="3" border>
            <el-descriptions-item :label="$t('apiTesting.report.reportId')">{{ report.id }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.testSuite')">{{ report.test_suite_name || '-' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.project')">{{ report.project_name || '-' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.description')">{{ report.test_suite_description || '-' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.environment')">{{ report.environment_name || '-' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.executionStatus')">
              <el-tag :type="getStatusType(report.status)">{{ getStatusText(report.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.executor')">{{ report.executed_by_name || '-' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.executionTime')">{{ formatDate(report.created_at) }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.startTime')">{{ formatDate(report.start_time) }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.endTime')">{{ formatDate(report.end_time) }}</el-descriptions-item>
            <el-descriptions-item :label="$t('apiTesting.report.duration')">{{ formatDuration(report.duration) }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <div class="report-metrics" :title="progressTooltip">
          <div class="report-metrics__stat">
            <span class="report-metrics__label">{{ $t('apiTesting.report.passRate') }}</span>
            <span class="report-metrics__value">{{ stats.passRate }}%</span>
          </div>
          <div class="report-metrics__stat">
            <span class="report-metrics__label">{{ $t('apiTesting.report.totalScenarios') }}</span>
            <span class="report-metrics__value">{{ stats.total }}</span>
          </div>
          <div class="report-metrics__track">
            <div
              v-for="segment in progressSegments"
              :key="segment.key"
              class="report-metrics__segment"
              :class="segment.class"
              :style="{ flexGrow: segment.value }"
            />
          </div>
          <div class="report-metrics__legend">
            <span v-for="segment in legendSegments" :key="segment.key" class="legend" :class="segment.legendClass">
              {{ $t(segment.labelKey) }} {{ segment.value }}
            </span>
          </div>
        </div>

        <el-card shadow="never" class="detail-card result-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">{{ $t('apiTesting.report.executionResultDetail') }}</span>
              <span class="card-subtitle">{{ $t('apiTesting.report.totalScenarios') }}: {{ scenarioResults.length }}</span>
            </div>
          </template>

          <el-collapse v-if="scenarioResults.length" v-model="expandedScenarioIds">
            <el-collapse-item v-for="scenario in scenarioResults" :key="scenario.id || scenario.execution_id" :name="scenario.id || scenario.execution_id">
              <template #title>
                <div class="scenario-title">
                  <span class="status-dot" :class="statusDotClass(scenario)" />
                  <span class="scenario-name">{{ scenario.scenario_case_name || scenario.scenario_case?.name || scenario.name || '-' }}</span>
                  <el-tag size="small" :type="scenarioStatusType(scenario)">{{ scenarioStatusText(scenario) }}</el-tag>
                  <span class="scenario-stat">{{ scenario.passed_requests || 0 }}/{{ scenario.total_requests || 0 }}</span>
                </div>
              </template>
              <StepResultPanel
                :execution="toPanelExecution(scenario)"
                :steps="scenario.steps || []"
              />
            </el-collapse-item>
          </el-collapse>
          <el-empty v-else :description="$t('apiTesting.report.noScenarioResults')" />
        </el-card>

        <el-card v-if="report.error_message" shadow="never" class="detail-card">
          <template #header><span class="card-title">{{ $t('apiTesting.report.errorInfo') }}</span></template>
          <pre class="error-text">{{ report.error_message }}</pre>
        </el-card>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import { getApiSuiteExecution } from '@/api/api-testing'
import StepResultPanel from './components/scenario/StepResultPanel.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const reportId = computed(() => route.params.id)
const report = ref(null)
const loading = ref(false)
const expandedScenarioIds = ref([])

const scenarioResults = computed(() => report.value?.scenario_results || [])
const stats = computed(() => {
  const total = Number(report.value?.total_scenarios) || scenarioResults.value.length
  const passed = Number(report.value?.passed_scenarios) || 0
  const failed = Number(report.value?.failed_scenarios) || 0
  return {
    total,
    passed,
    failed,
    passRate: total ? ((passed / total) * 100).toFixed(1) : '0.0'
  }
})

const metricSegments = [
  { key: 'passed', class: 'seg--passed', legendClass: 'legend--passed', labelKey: 'apiTesting.report.passedScenarios' },
  { key: 'failed', class: 'seg--failed', legendClass: 'legend--failed', labelKey: 'apiTesting.report.failedScenarios' }
]
const withValues = () => metricSegments.map((segment) => ({ ...segment, value: stats.value[segment.key] || 0 }))
const progressSegments = computed(() => withValues().filter((segment) => segment.value > 0))
const legendSegments = computed(withValues)
const progressTooltip = computed(() => progressSegments.value.map((segment) => `${t(segment.labelKey)} ${segment.value}`).join(' · '))

const loadReport = async () => {
  if (!reportId.value) return
  loading.value = true
  try {
    const response = await getApiSuiteExecution(reportId.value)
    report.value = response.data
    // 通过场景默认折叠，失败场景展开后由步骤结果面板继续只展开失败路径。
    expandedScenarioIds.value = scenarioResults.value
      .filter((scenario) => isScenarioFailed(scenario))
      .map((scenario) => scenario.id || scenario.execution_id)
  } catch (error) {
    report.value = null
    console.error('加载接口测试报告详情失败:', error)
  } finally {
    loading.value = false
  }
}

const goBack = () => router.push('/api-testing/reports')

const getStatusType = (status) => ({
  PENDING: 'info', RUNNING: 'warning', COMPLETED: 'success', FAILED: 'danger', CANCELLED: 'info'
}[status] || 'info')

const getStatusText = (status) => {
  const key = { PENDING: 'pending', RUNNING: 'running', COMPLETED: 'completed', FAILED: 'failed', CANCELLED: 'cancelled' }[status]
  return key ? t(`apiTesting.report.status.${key}`) : status || '-'
}

const isScenarioPassed = (scenario) => {
  // 套件结果是场景在本次套件执行中的最终结论，优先于单次执行记录状态。
  if (scenario.suite_status) return scenario.suite_status === 'COMPLETED'
  // 已有明确结论时必须尊重结论，控制流步骤失败但请求失败数为 0 时也应显示失败。
  if (scenario.conclusion) return scenario.conclusion === 'PASSED'
  return scenario.status === 'COMPLETED' && !scenario.failed_requests
}
const isScenarioFailed = (scenario) => !isScenarioPassed(scenario)
const scenarioStatusType = (scenario) => isScenarioPassed(scenario) ? 'success' : 'danger'
const scenarioStatusText = (scenario) => scenarioStatusType(scenario) === 'success' ? t('apiTesting.report.passed') : t('apiTesting.report.failed')
const statusDotClass = (scenario) => scenarioStatusType(scenario) === 'success' ? 'is-passed' : 'is-failed'
const toPanelExecution = (scenario) => ({
  passed: scenario.passed_requests || 0,
  failed: scenario.failed_requests || 0,
  skipped: scenario.skipped_steps || 0,
  total: scenario.total_requests || 0
})
const formatDate = (value) => value ? new Date(value).toLocaleString() : '-'
const formatDuration = (seconds) => {
  const value = Number(seconds)
  if (!Number.isFinite(value)) return '-'
  if (value < 60) return `${value.toFixed(1)}s`
  return `${Math.floor(value / 60)}m${Math.round(value % 60)}s`
}

watch(reportId, () => {
  report.value = null
  loadReport()
}, { immediate: true })
</script>

<style scoped lang="scss">
.report-detail-page { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.page-header { flex: none; }
.header-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
.back-btn { flex: none; padding: 0; font-size: 18px; }
.page-title { white-space: nowrap; }
.report-no { color: #909399; font-size: 14px; }
.page-body { flex: 1; min-height: 200px; overflow-y: auto; padding-bottom: 10px; }
.detail-card { margin-bottom: 10px; border: none; border-radius: 4px; }
.detail-card :deep(.el-card__header) { padding: 12px 20px; background: #fafafa; }
.detail-card :deep(.el-card__body) { padding: 20px; }
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-title { color: #303133; font-size: 16px; font-weight: 600; }
.card-subtitle { color: #909399; font-size: 13px; }
.report-metrics { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; margin-bottom: 10px; padding: 10px 14px; background: #fff; border: 1px solid var(--el-border-color-lighter); border-radius: 4px; }
.report-metrics__stat { display: inline-flex; align-items: baseline; gap: 6px; }
.report-metrics__label { color: var(--el-text-color-secondary); font-size: 14px; font-weight: 500; }
.report-metrics__value { color: var(--el-text-color-primary); font-size: 14px; font-weight: 600; }
.report-metrics__track { display: flex; flex: 1 1 160px; gap: 1px; min-width: 110px; height: 8px; }
.report-metrics__segment { min-width: 2px; border-radius: 4px; transition: flex-grow 300ms ease; }
.seg--passed { background: var(--el-color-success); }
.seg--failed { background: var(--el-color-danger); }
.report-metrics__legend { display: flex; gap: 12px; flex-wrap: wrap; color: var(--el-text-color-secondary); font-size: 14px; }
.legend { display: inline-flex; align-items: center; gap: 5px; }
.legend::before { width: 7px; height: 7px; content: ''; border-radius: 50%; background: var(--el-border-color-lighter); }
.legend--passed::before { background: var(--el-color-success); }
.legend--failed::before { background: var(--el-color-danger); }
.result-card :deep(.el-card__body) { padding: 0 20px 20px; }
.result-card :deep(.el-collapse-item__header) { height: 54px; }
.scenario-title { display: flex; align-items: center; gap: 10px; width: 100%; min-width: 0; padding-right: 12px; }
.status-dot { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 50%; }
.status-dot.is-passed { background: var(--el-color-success); }
.status-dot.is-failed { background: var(--el-color-danger); }
.scenario-name { min-width: 0; overflow: hidden; color: #303133; font-size: 15px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.scenario-stat { margin-left: auto; color: #909399; font-size: 13px; }
.error-text { margin: 0; padding: 14px; overflow: auto; color: var(--el-color-danger); background: #fff5f5; border-radius: 4px; white-space: pre-wrap; }
@media screen and (max-width: 720px) { .page-title { font-size: 18px; } .report-metrics__track { flex-basis: 100%; order: 3; } }
</style>
