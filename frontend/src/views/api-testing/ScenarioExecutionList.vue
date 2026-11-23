<template>
  <div class="page-container">
    <div class="page-header">
      <div class="title-group">
        <h1 class="page-title">{{ $t('apiTesting.scenarioExecutions.title') }}</h1>
        <el-tag v-if="scheduledTaskId" closable @close="clearTaskFilter">
          {{ $t('apiTesting.scenarioExecutions.associatedScheduledTask') }}: {{ route.query.task_name || `#${scheduledTaskId}` }}
        </el-tag>
      </div>
    </div>

    <div class="card-container">
      <div class="filter-bar">
        <el-form :inline="true">
          <el-form-item :label="$t('apiTesting.common.status')">
            <el-select v-model="status" class="status-select" clearable @change="loadExecutions">
              <el-option label="PENDING" value="PENDING" />
              <el-option label="RUNNING" value="RUNNING" />
              <el-option label="COMPLETED" value="COMPLETED" />
              <el-option label="FAILED" value="FAILED" />
              <el-option label="CANCELLED" value="CANCELLED" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button @click="resetFilters">{{ $t('apiTesting.common.reset') }}</el-button>
            <el-button type="primary" @click="loadExecutions">{{ $t('apiTesting.common.search') }}</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table :data="executions" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="scenario_case_name" :label="$t('apiTesting.scenarioExecutions.scenarioCase')" min-width="200" />
        <el-table-column :label="$t('apiTesting.scheduledTask.relatedTask')" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.scheduled_task_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" :label="$t('apiTesting.common.status')" width="110">
          <template #default="scope">
            <el-tag :type="getStatusType(scope.row)">{{ getStatusText(scope.row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="total_requests" :label="$t('apiTesting.scenarioExecutions.totalRequests')" width="100" />
        <el-table-column prop="passed_requests" :label="$t('apiTesting.scenarioExecutions.passedRequests')" width="100" />
        <el-table-column prop="failed_requests" :label="$t('apiTesting.scenarioExecutions.failedRequests')" width="100" />
        <el-table-column prop="created_at" :label="$t('apiTesting.scenarioExecutions.executionTime')" width="180">
          <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="$t('apiTesting.common.operation')" width="110" fixed="right">
          <template #default="scope">
            <el-button text type="primary" @click="viewDetail(scope.row)">
              {{ $t('apiTesting.common.view') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadExecutions"
          @current-change="loadExecutions"
        />
      </div>
    </div>

    <el-dialog v-model="detailVisible" :title="$t('apiTesting.scenarioExecutions.detail')" width="900px">
      <div v-if="detailExecution" v-loading="detailLoading">
        <el-descriptions :column="3" border>
          <el-descriptions-item :label="$t('apiTesting.scenarioExecutions.scenarioCase')">{{ detailExecution.scenario_case_name || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('apiTesting.common.status')">{{ getStatusText(detailExecution) }}</el-descriptions-item>
        </el-descriptions>
        <StepResultPanel class="detail-panel" :execution="stepSummary" :steps="steps" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { getExecutionSteps, getScenarioCaseExecutions } from '@/api/api-testing.js'
import StepResultPanel from './components/scenario/StepResultPanel.vue'

const route = useRoute()
const router = useRouter()
const scheduledTaskId = computed(() => route.query.scheduled_task || '')
const executions = ref([])
const steps = ref([])
const detailExecution = ref(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const loading = ref(false)
const status = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const stepSummary = computed(() => {
  if (!detailExecution.value) return null
  return {
    passed: detailExecution.value.passed_requests,
    failed: detailExecution.value.failed_requests,
    skipped: detailExecution.value.skipped_steps,
    total: detailExecution.value.total_requests
  }
})

const loadExecutions = async () => {
  loading.value = true
  try {
    const response = await getScenarioCaseExecutions({
      page: page.value,
      page_size: pageSize.value,
      scheduled_task: scheduledTaskId.value,
      ...(status.value ? { status: status.value } : {})
    })
    executions.value = response.data.results || response.data
    total.value = response.data.count ?? executions.value.length
  } finally {
    loading.value = false
  }
}

const viewDetail = async (execution) => {
  detailExecution.value = execution
  steps.value = []
  detailVisible.value = true
  detailLoading.value = true
  try {
    const response = await getExecutionSteps(execution.id)
    steps.value = response.data.steps || []
    detailExecution.value = { ...execution, ...response.data }
  } finally {
    detailLoading.value = false
  }
}

const clearTaskFilter = () => router.push({ name: 'ApiScenarioExecutions' })
const resetFilters = () => {
  status.value = ''
  page.value = 1
  loadExecutions()
}
const getStatusType = (execution) => execution.conclusion === 'PASSED' ? 'success' : execution.status === 'RUNNING' ? 'warning' : execution.status === 'FAILED' || execution.conclusion === 'FAILED' ? 'danger' : 'info'
const getStatusText = (execution) => execution.conclusion || execution.status || '-'
const formatDate = value => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'

onMounted(loadExecutions)
</script>

<style scoped>
.page-container { height: 100%; overflow-y: auto; }
.title-group { display: flex; align-items: center; gap: 12px; }

.status-select { width: 180px; }

@media screen and (max-width: 480px) {
  .status-select { width: 100%; }
}

.detail-panel { margin-top: 20px; }
</style>
