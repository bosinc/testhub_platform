<template>
  <div class="page-container">
    <div class="page-header">
      <div class="title-group">
        <h1 class="page-title">{{ $t('uiAutomation.scheduledTask.title') }}</h1>
      </div>
      <el-button type="primary" @click="handleCreateClick">
        <el-icon><Plus /></el-icon>
        {{ $t('uiAutomation.scheduledTask.newTask') }}
      </el-button>
    </div>

    <div class="card-container">
      <!-- 筛选条件 -->
      <div class="filter-bar">
        <el-form :inline="true" :model="filters" class="demo-form-inline">
          <el-form-item :label="$t('uiAutomation.scheduledTask.taskType')">
            <el-select v-model="filters.task_type" :placeholder="$t('uiAutomation.scheduledTask.taskType')" clearable style="width: 150px">
              <el-option :label="$t('uiAutomation.scheduledTask.taskTypes.testSuite')" value="TEST_SUITE" />
              <el-option :label="$t('uiAutomation.scheduledTask.taskTypes.testCase')" value="TEST_CASE" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('uiAutomation.scheduledTask.triggerType')">
            <el-select v-model="filters.trigger_type" :placeholder="$t('uiAutomation.scheduledTask.triggerType')" clearable style="width: 150px">
              <el-option :label="$t('uiAutomation.scheduledTask.triggerTypes.cron')" value="CRON" />
              <el-option :label="$t('uiAutomation.scheduledTask.triggerTypes.interval')" value="INTERVAL" />
              <el-option :label="$t('uiAutomation.scheduledTask.triggerTypes.once')" value="ONCE" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('uiAutomation.scheduledTask.status')">
            <el-select v-model="filters.status" :placeholder="$t('uiAutomation.scheduledTask.status')" clearable style="width: 150px">
              <el-option :label="$t('uiAutomation.scheduledTask.statusTypes.active')" value="ACTIVE" />
              <el-option :label="$t('uiAutomation.scheduledTask.statusTypes.paused')" value="PAUSED" />
              <el-option :label="$t('uiAutomation.scheduledTask.statusTypes.completed')" value="COMPLETED" />
              <el-option :label="$t('uiAutomation.scheduledTask.statusTypes.failed')" value="FAILED" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="loadTasks">{{ $t('uiAutomation.common.search') }}</el-button>
            <el-button @click="resetFilters">{{ $t('uiAutomation.common.reset') }}</el-button>
          </el-form-item>
        </el-form>
      </div>

      <!-- 任务列表 -->
      <el-table :data="tasks" v-loading="loading" style="width: 100%">
        <el-table-column prop="name" :label="$t('uiAutomation.scheduledTask.taskName')" min-width="150" />
        <el-table-column prop="task_type" :label="$t('uiAutomation.scheduledTask.taskType')" width="120">
          <template #default="scope">
            <el-tag :type="scope.row.task_type === 'TEST_SUITE' ? 'success' : 'primary'">
              {{ scope.row.task_type === 'TEST_SUITE' ? $t('uiAutomation.scheduledTask.taskTypes.testSuiteShort') : $t('uiAutomation.scheduledTask.taskTypes.testCaseShort') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="notification_type_display" :label="$t('uiAutomation.scheduledTask.notificationType')" width="120">
          <template #default="scope">
            <el-tag v-if="scope.row.notification_type_display && scope.row.notification_type_display !== '-'"
                    :type="getNotificationTypeTagType(scope.row.notification_type_display)"
                    size="small">
              {{ getNotificationTypeText(scope.row.notification_type) }}
            </el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="trigger_type" :label="$t('uiAutomation.scheduledTask.triggerType')" width="120">
          <template #default="scope">
            <el-tag>
              {{ getTriggerTypeText(scope.row.trigger_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" :label="$t('uiAutomation.scheduledTask.status')" width="80">
          <template #default="scope">
            <el-tag :type="scope.row.status === 'ACTIVE' ? 'success' : scope.row.status === 'PAUSED' ? 'warning' : 'info'">
              {{ getStatusText(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="next_run_time" :label="$t('uiAutomation.scheduledTask.nextRunTime')" width="180">
          <template #default="scope">
            {{ formatDateTime(scope.row.next_run_time) }}
          </template>
        </el-table-column>
        <el-table-column prop="last_run_time" :label="$t('uiAutomation.scheduledTask.lastRunTime')" width="180">
          <template #default="scope">
            {{ formatDateTime(scope.row.last_run_time) }}
          </template>
        </el-table-column>
        <el-table-column :label="$t('uiAutomation.common.operation')" width="250" fixed="right" align="center" class-name="action-col">
          <template #default="scope">
            <div class="row-actions">
              <el-button size="small" text @click="editTask(scope.row)">
                <el-icon><Edit /></el-icon>
                {{ $t('uiAutomation.scheduledTask.actions.edit') }}
              </el-button>
              <el-button size="small" text type="primary" :loading="scope.row.running" @click="runTaskNow(scope.row)">
                <el-icon><VideoPlay /></el-icon>
                {{ $t('uiAutomation.scheduledTask.runNow') }}
              </el-button>
              <el-button size="small" text type="primary" @click="viewTaskExecutions(scope.row)">
                <el-icon><Clock /></el-icon>
                {{ $t('uiAutomation.scheduledTask.executionHistory') }}
              </el-button>
              <el-button v-if="scope.row.status === 'ACTIVE'" size="small" text @click="pauseTask(scope.row)">
                <el-icon><VideoPause /></el-icon>
                {{ $t('uiAutomation.scheduledTask.actions.pause') }}
              </el-button>
              <el-button v-if="scope.row.status === 'PAUSED'" size="small" text type="success" @click="resumeTask(scope.row)">
                <el-icon><CaretRight /></el-icon>
                {{ $t('uiAutomation.scheduledTask.actions.resume') }}
              </el-button>
              <el-button size="small" text type="danger" :aria-label="$t('uiAutomation.scheduledTask.actions.delete')" @click="deleteTask(scope.row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-container">
        <el-pagination
          v-model:current-page="pagination.current"
          v-model:page-size="pagination.size"
          :total="pagination.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadTasks"
          @current-change="loadTasks"
        />
      </div>
    </div>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      :title="editingTask ? $t('uiAutomation.scheduledTask.editTask') : $t('uiAutomation.scheduledTask.createTask')"
      width="800px"
      :close-on-click-modal="false"
      @close="resetTaskForm"
    >
      <el-form :model="taskForm" label-width="120px">
        <el-form-item :label="$t('uiAutomation.scheduledTask.taskName')" required>
          <el-input v-model="taskForm.name" :placeholder="$t('uiAutomation.scheduledTask.taskNamePlaceholder')" />
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.taskDesc')">
          <el-input v-model="taskForm.description" type="textarea" :placeholder="$t('uiAutomation.scheduledTask.taskDescPlaceholder')" />
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.relatedProject')" required>
          <el-select v-model="taskForm.project" :placeholder="$t('uiAutomation.scheduledTask.selectProject')" @change="onProjectChange">
            <el-option
              v-for="project in projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.taskType')" required>
          <el-radio-group v-model="taskForm.task_type" @change="onTaskTypeChange">
            <el-radio value="TEST_SUITE">{{ $t('uiAutomation.scheduledTask.taskTypes.testSuite') }}</el-radio>
            <el-radio value="TEST_CASE">{{ $t('uiAutomation.scheduledTask.taskTypes.testCase') }}</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- 根据任务类型显示不同配置 - 移到任务类型下面 -->
        <el-form-item v-if="taskForm.task_type === 'TEST_SUITE'" :label="$t('uiAutomation.scheduledTask.testSuite')" required>
          <el-select
            v-model="taskForm.test_suite"
            filterable
            fit-input-width
            popper-class="scheduled-suite-popper"
            :placeholder="$t('uiAutomation.scheduledTask.selectSuite')"
            style="width: 100%"
          >
            <el-option
              v-for="suite in testSuites"
              :key="suite.id"
              :label="suite.name"
              :value="suite.id"
            >
              <el-tooltip :content="suite.name" :disabled="overflowSuiteId !== suite.id" :show-after="300" placement="top">
                <span class="scheduled-suite-name" @mouseenter="onSuiteNameEnter($event, suite.id)">{{ suite.name }}</span>
              </el-tooltip>
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item v-if="taskForm.task_type === 'TEST_CASE'" :label="$t('uiAutomation.scheduledTask.testCase')" required>
          <!-- 目录级联勾选所有后代用例，表单只保存用例 ID，不保存目录节点。 -->
          <el-tree-select
            v-model="selectedTestCaseKeys"
            :data="testCaseTree"
            :props="{ label: 'name', children: 'children' }"
            node-key="key"
            multiple
            show-checkbox
            filterable
            clearable
            collapse-tags
            collapse-tags-tooltip
            fit-input-width
            popper-class="scheduled-case-tree-popper"
            :loading="projectDataLoading"
            :disabled="!taskForm.project || projectDataLoading"
            :placeholder="$t('uiAutomation.scheduledTask.selectTestCase')"
            style="width: 100%"
          >
            <template #default="{ data }">
              <el-tooltip :content="data.name" :disabled="overflowCaseKey !== data.key" :show-after="300" placement="top">
                <span class="scheduled-case-tree-name" @mouseenter="onCaseNameEnter($event, data.key)">{{ data.name }}</span>
              </el-tooltip>
            </template>
          </el-tree-select>
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.triggerType')" required>
          <el-radio-group v-model="taskForm.trigger_type">
            <el-radio value="CRON">{{ $t('uiAutomation.scheduledTask.triggerTypes.cron') }}</el-radio>
            <el-radio value="INTERVAL">{{ $t('uiAutomation.scheduledTask.triggerTypes.interval') }}</el-radio>
            <el-radio value="ONCE">{{ $t('uiAutomation.scheduledTask.triggerTypes.once') }}</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- 根据触发器类型显示不同配置 -->
        <el-form-item v-if="taskForm.trigger_type === 'CRON'" :label="$t('uiAutomation.scheduledTask.cronExpression')" required>
          <el-input v-model="taskForm.cron_expression" :placeholder="$t('uiAutomation.scheduledTask.cronPlaceholder')" />
          <div class="cron-help">
            <el-tooltip raw-content placement="top">
              <template #content>
                <div style="line-height: 1.6; text-align: left;">
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.format') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.minute') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.hour') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.day') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.month') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.week') }}</div>
                  <div style="margin-top: 8px;">{{ $t('uiAutomation.scheduledTask.cronHelp.examples') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.everyDay') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.everyHour') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.everyMonday') }}</div>
                  <div>{{ $t('uiAutomation.scheduledTask.cronHelp.everyMonth') }}</div>
                </div>
              </template>
              <span style="cursor: pointer; color: #409EFF;">{{ $t('uiAutomation.scheduledTask.cronHelpLink') }}</span>
            </el-tooltip>
          </div>
        </el-form-item>

        <el-form-item v-if="taskForm.trigger_type === 'INTERVAL'" :label="$t('uiAutomation.scheduledTask.intervalTime')" required>
          <el-input-number v-model="taskForm.interval_seconds" :min="60" :step="60" />
          <span class="unit">{{ $t('uiAutomation.scheduledTask.intervalUnit') }}</span>
        </el-form-item>

        <el-form-item v-if="taskForm.trigger_type === 'ONCE'" :label="$t('uiAutomation.scheduledTask.executeTime')" required>
          <el-date-picker
            v-model="taskForm.execute_at"
            type="datetime"
            :disabled-date="disabledExecuteDate"
            :disabled-hours="disabledExecuteHours"
            :disabled-minutes="disabledExecuteMinutes"
            :disabled-seconds="disabledExecuteSeconds"
            :placeholder="$t('uiAutomation.scheduledTask.selectExecuteTime')"
            @visible-change="onExecuteTimeVisibleChange"
          />
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.executionEngine')" required>
          <el-radio-group v-model="taskForm.engine">
            <el-radio value="playwright">Playwright</el-radio>
            <el-radio value="selenium">Selenium</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.browserType')" required>
          <el-select v-model="taskForm.browser" :placeholder="$t('uiAutomation.scheduledTask.selectBrowser')">
            <el-option label="Chrome" value="chrome" />
            <el-option label="Firefox" value="firefox" />
            <el-option label="Edge" value="edge" />
          </el-select>
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.runMode')">
          <el-checkbox v-model="taskForm.headless">{{ $t('uiAutomation.scheduledTask.headlessMode') }}</el-checkbox>
        </el-form-item>

        <!-- 定时任务只在 Playwright 执行时允许选择初始登录态。 -->
        <el-form-item label="登录态">
          <el-select v-model="taskForm.storage_state" clearable placeholder="无登录态" :disabled="taskForm.engine !== 'playwright'" style="width: 260px">
            <el-option v-for="state in storageStates" :key="state.id" :label="state.name" :value="state.id">
              <template #default>
                <span class="storage-state-option-name">{{ state.name }}</span>
                <span class="storage-state-option-note">{{ state.note || '' }}</span>
              </template>
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item :label="$t('uiAutomation.scheduledTask.notificationSettings')">
          <el-checkbox v-model="taskForm.notify_on_success">{{ $t('uiAutomation.scheduledTask.notifyOnSuccess') }}</el-checkbox>
          <el-checkbox v-model="taskForm.notify_on_failure">{{ $t('uiAutomation.scheduledTask.notifyOnFailure') }}</el-checkbox>
        </el-form-item>

        <el-form-item v-if="taskForm.notify_on_success || taskForm.notify_on_failure" :label="$t('uiAutomation.scheduledTask.notificationType')">
          <el-select v-model="taskForm.notification_type" :placeholder="$t('uiAutomation.scheduledTask.selectNotificationType')">
            <el-option :label="$t('uiAutomation.scheduledTask.notificationTypes.email')" value="email" />
            <el-option :label="$t('uiAutomation.scheduledTask.notificationTypes.webhook')" value="webhook" />
            <el-option :label="$t('uiAutomation.scheduledTask.notificationTypes.both')" value="both" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="(taskForm.notify_on_success || taskForm.notify_on_failure) && (taskForm.notification_type === 'email' || taskForm.notification_type === 'both')" :label="$t('uiAutomation.scheduledTask.notifyEmails')">
          <UserSelect v-model="taskForm.notify_emails" multiple :options="users" value-key="email" label-key="display_name" :placeholder="$t('uiAutomation.scheduledTask.selectNotifyEmails')" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateDialog = false">{{ $t('uiAutomation.common.cancel') }}</el-button>
        <el-button type="primary" @click="submitTaskForm" :loading="submitting">
          {{ editingTask ? $t('uiAutomation.messages.success.update') : $t('uiAutomation.messages.success.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Edit, VideoPlay, Clock, VideoPause, CaretRight, Delete } from '@element-plus/icons-vue'
import UserSelect from '@/components/UserSelect.vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { fetchAllPages } from '@/utils/api'
import { getTestCaseTreeNodes } from '@/utils/ui-automation'
import {
  getScheduledTasks,
  createScheduledTask,
  updateScheduledTask,
  deleteScheduledTask,
  runScheduledTask,
  pauseScheduledTask,
  resumeScheduledTask,
  getUiProjects,
  getTestSuites,
  getTestCaseTree,
  getUiUsers,
  getStorageStates
} from '@/api/ui_automation.js'

const { t, locale } = useI18n()
const router = useRouter()

// 数据状态
const tasks = ref([])
const projects = ref([])
const testSuites = ref([])
const testCaseTree = ref([])
const projectDataLoading = ref(false)
const overflowCaseKey = ref('')
const overflowSuiteId = ref(null)
let projectDataRequestId = 0
const storageStates = ref([])
const users = ref([])
const loading = ref(false)
const submitting = ref(false)
const showCreateDialog = ref(false)
const editingTask = ref(null)
const executeTimeNow = ref(new Date())
let executeTimeTimer = null

// 面板打开期间刷新时钟，避免停留后仍可选择已经过去的时间。
const onExecuteTimeVisibleChange = (visible) => {
  clearInterval(executeTimeTimer)
  executeTimeTimer = null
  executeTimeNow.value = new Date()
  if (visible) {
    executeTimeTimer = setInterval(() => { executeTimeNow.value = new Date() }, 1000)
  }
}
onBeforeUnmount(() => clearInterval(executeTimeTimer))

// 筛选条件
const filters = reactive({
  task_type: '',
  trigger_type: '',
  status: ''
})

// 分页配置
const pagination = reactive({
  current: 1,
  size: 10,
  total: 0
})

// 表单数据
const taskForm = reactive({
  name: '',
  description: '',
  project: '',
  task_type: 'TEST_SUITE',
  trigger_type: 'CRON',
  cron_expression: '0 0 * * *',
  interval_seconds: 3600,
  execute_at: '',
  test_suite: '',
  test_cases: [],
  storage_state: null,
  engine: 'playwright',
  browser: 'chrome',
  headless: true,
  notify_on_success: false,
  notify_on_failure: false,
  notification_type: '',
  notify_emails: []
})

const disabledExecuteDate = (date) => {
  const today = new Date(executeTimeNow.value)
  today.setHours(0, 0, 0, 0)
  return date.getTime() < today.getTime()
}

// comparingDate 是时间面板正在编辑的日期，优先于尚未确认的表单值。
const getExecuteDate = (comparingDate) => {
  const date = new Date(comparingDate?.toDate() || taskForm.execute_at || executeTimeNow.value)
  date.setHours(0, 0, 0, 0)
  return date.getTime()
}
const disabledTimeValues = (count) => Array.from({ length: count }, (_, index) => index)
const disabledExecuteHours = (_role, comparingDate) => {
  const now = executeTimeNow.value
  const today = new Date(now).setHours(0, 0, 0, 0)
  const selectedDay = getExecuteDate(comparingDate)
  return disabledTimeValues(selectedDay < today ? 24 : selectedDay === today ? now.getHours() : 0)
}
const disabledExecuteMinutes = (hour, _role, comparingDate) => {
  const now = executeTimeNow.value
  const today = new Date(now).setHours(0, 0, 0, 0)
  const selectedDay = getExecuteDate(comparingDate)
  if (selectedDay < today || (selectedDay === today && hour < now.getHours())) return disabledTimeValues(60)
  return disabledTimeValues(selectedDay === today && hour === now.getHours() ? now.getMinutes() : 0)
}
const disabledExecuteSeconds = (hour, minute, _role, comparingDate) => {
  const now = executeTimeNow.value
  const today = new Date(now).setHours(0, 0, 0, 0)
  const selectedDay = getExecuteDate(comparingDate)
  if (selectedDay < today || (selectedDay === today &&
      (hour < now.getHours() || (hour === now.getHours() && minute < now.getMinutes())))) {
    return disabledTimeValues(60)
  }
  // 选择器精确到秒，当前秒在确认时已过，因此从下一秒起允许选择。
  return disabledTimeValues(selectedDay === today && hour === now.getHours() && minute === now.getMinutes()
    ? now.getSeconds() + 1 : 0)
}

// 树使用带类型前缀的 key，表单和接口始终保存真实用例 ID，兼容已有任务回显。
const selectedTestCaseKeys = computed({
  get: () => taskForm.test_cases.map(id => `case:${id}`),
  set: keys => {
    const casesByKey = new Map(getTestCaseTreeNodes(testCaseTree.value, 'testCase')
      .map(testCase => [testCase.key, testCase.id]))
    taskForm.test_cases = keys.filter(key => casesByKey.has(key)).map(key => casesByKey.get(key))
  }
})

// 只对实际被截断的节点启用提示，目录和用例共用相同的名称展示规则。
const onCaseNameEnter = (event, key) => {
  const label = event.currentTarget
  overflowCaseKey.value = label.scrollWidth > label.clientWidth ? key : ''
}

// 套件沿用相同的省略规则，仅对实际截断的名称显示完整提示。
const onSuiteNameEnter = (event, id) => {
  const label = event.currentTarget
  overflowSuiteId.value = label.scrollWidth > label.clientWidth ? id : null
}

// 获取触发器类型文本
const getTriggerTypeText = (type) => {
  const typeMap = {
    'CRON': t('uiAutomation.scheduledTask.triggerTypes.cronShort'),
    'INTERVAL': t('uiAutomation.scheduledTask.triggerTypes.intervalShort'),
    'ONCE': t('uiAutomation.scheduledTask.triggerTypes.onceShort')
  }
  return typeMap[type] || type
}

// 获取状态文本
const getStatusText = (status) => {
  const statusMap = {
    'ACTIVE': t('uiAutomation.scheduledTask.statusTypes.active'),
    'PAUSED': t('uiAutomation.scheduledTask.statusTypes.paused'),
    'COMPLETED': t('uiAutomation.scheduledTask.statusTypes.completedShort'),
    'FAILED': t('uiAutomation.scheduledTask.statusTypes.failed')
  }
  return statusMap[status] || status
}

// 获取通知类型文本
const getNotificationTypeText = (type) => {
  const typeMap = {
    'email': t('uiAutomation.scheduledTask.notificationTypes.email'),
    'webhook': t('uiAutomation.scheduledTask.notificationTypes.webhook'),
    'both': t('uiAutomation.scheduledTask.notificationTypes.both')
  }
  return typeMap[type] || type
}

// 生命周期
onMounted(() => {
  loadTasks()
  loadProjects()
  loadUsers()
})

// 加载任务列表
const loadTasks = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.current,
      page_size: pagination.size,
      ...filters
    }
    const response = await getScheduledTasks(params)
    tasks.value = response.data.results
    pagination.total = response.data.count
  } catch (error) {
    ElMessage.error(t('uiAutomation.scheduledTask.messages.loadFailed'))
  } finally {
    loading.value = false
  }
}

// 加载项目列表
const loadProjects = async () => {
  try {
    const response = await getUiProjects()
    projects.value = response.data.results
  } catch (error) {
    console.error('Load projects failed:', error)
  }
}

// 加载用户列表
const loadUsers = async () => {
  try {
    const response = await getUiUsers()
    // 处理分页数据结构
    const usersData = response.data.results || response.data
    users.value = usersData.map(user => ({
      ...user,
      display_name: user.first_name ? `${user.first_name}（${user.email}）` : `${user.username}（${user.email}）`
    }))
  } catch (error) {
    console.error('Load users failed:', error)
  }
}

// 套件保留分页加载，用例直接读取完整轻量树；旧请求不能覆盖其他项目的选项。
const loadProjectData = async (projectId) => {
  const requestId = ++projectDataRequestId
  testSuites.value = []
  testCaseTree.value = []
  storageStates.value = []
  projectDataLoading.value = Boolean(projectId)
  if (!projectId) return
  try {
    const [suites, treeResponse, statesResponse] = await Promise.all([
      fetchAllPages(getTestSuites, { project: projectId }),
      getTestCaseTree({ project: projectId }),
      getStorageStates({ project: projectId, page_size: 200 })
    ])
    if (requestId !== projectDataRequestId || taskForm.project !== projectId) return
    testSuites.value = suites
    testCaseTree.value = treeResponse.data
    storageStates.value = statesResponse.data.results || statesResponse.data
  } catch (error) {
    if (requestId !== projectDataRequestId) return
    console.error('Load project data failed:', error)
    ElMessage.error(t('uiAutomation.suite.messages.loadCasesFailed'))
  } finally {
    if (requestId === projectDataRequestId) projectDataLoading.value = false
  }
}

// 用户切换项目时清除旧目标；编辑已有任务仅加载选项，保留原有选择。
const onProjectChange = async (projectId) => {
  taskForm.test_suite = ''
  taskForm.test_cases = []
  taskForm.storage_state = null
  await loadProjectData(projectId)
}

// 任务类型变化
const onTaskTypeChange = () => {
  taskForm.test_suite = ''
  taskForm.test_cases = []
}

// 新建按钮点击
const handleCreateClick = () => {
  editingTask.value = null
  resetTaskForm()
  showCreateDialog.value = true
}

// 重置表单
const resetTaskForm = () => {
  projectDataRequestId += 1
  projectDataLoading.value = false
  testSuites.value = []
  testCaseTree.value = []
  storageStates.value = []
  Object.assign(taskForm, {
    name: '',
    description: '',
    project: '',
    task_type: 'TEST_SUITE',
    trigger_type: 'CRON',
    cron_expression: '0 0 * * *',
    interval_seconds: 3600,
    execute_at: '',
    test_suite: '',
    test_cases: [],
    storage_state: null,
    engine: 'playwright',
    browser: 'chrome',
    headless: true,
    notify_on_success: false,
    notify_on_failure: false,
    notification_type: '',
    notify_emails: []
  })
}

// 重置筛选
const resetFilters = () => {
  Object.assign(filters, {
    task_type: '',
    trigger_type: '',
    status: ''
  })
  loadTasks()
}

// 提交任务表单
const submitTaskForm = async () => {
  // 同时拦截手动输入和选择后停留过久导致的过期时间。
  if (taskForm.trigger_type === 'ONCE') {
    const executeAt = taskForm.execute_at ? new Date(taskForm.execute_at).getTime() : NaN
    if (!Number.isFinite(executeAt) || executeAt <= Date.now()) {
      ElMessage.warning(t('uiAutomation.scheduledTask.rules.executeTimeFuture'))
      return
    }
  }
  submitting.value = true
  try {
    const submitData = {
      name: taskForm.name,
      description: taskForm.description,
      project: taskForm.project,
      task_type: taskForm.task_type,
      trigger_type: taskForm.trigger_type,
      engine: taskForm.engine,
      browser: taskForm.browser,
      headless: taskForm.headless,
      storage_state: taskForm.engine === 'playwright' ? taskForm.storage_state : null,
      notify_on_success: taskForm.notify_on_success,
      notify_on_failure: taskForm.notify_on_failure
    }

    // 添加通知相关字段
    if (taskForm.notify_on_success || taskForm.notify_on_failure) {
      if (taskForm.notification_type) {
        submitData.notification_type = taskForm.notification_type
      }
      if (taskForm.notify_emails && taskForm.notify_emails.length > 0) {
        submitData.notify_emails = taskForm.notify_emails
      }
    }

    // 根据触发器类型添加对应字段
    if (taskForm.trigger_type === 'CRON') {
      submitData.cron_expression = taskForm.cron_expression
    } else if (taskForm.trigger_type === 'INTERVAL') {
      submitData.interval_seconds = taskForm.interval_seconds
    } else if (taskForm.trigger_type === 'ONCE') {
      submitData.execute_at = taskForm.execute_at
    }

    // 根据任务类型添加对应字段
    if (taskForm.task_type === 'TEST_SUITE') {
      submitData.test_suite = taskForm.test_suite
    } else if (taskForm.task_type === 'TEST_CASE') {
      submitData.test_cases = taskForm.test_cases
    }

    if (editingTask.value) {
      await updateScheduledTask(editingTask.value.id, submitData)
      ElMessage.success(t('uiAutomation.scheduledTask.messages.updateSuccess'))
    } else {
      await createScheduledTask(submitData)
      ElMessage.success(t('uiAutomation.scheduledTask.messages.createSuccess'))
    }
    showCreateDialog.value = false
    loadTasks()
  } catch (error) {
    console.error('Task operation failed:', error)
    ElMessage.error(error.response?.data?.error ||
                   error.response?.data?.detail ||
                   (editingTask.value ? t('uiAutomation.scheduledTask.messages.updateFailed') : t('uiAutomation.scheduledTask.messages.createFailed')))
  } finally {
    submitting.value = false
  }
}

// 任务类型决定目标页面，使用任务 ID 筛选，避免同套件的其他运行混入历史。
const viewTaskExecutions = (task) => {
  router.push({
    name: task.task_type === 'TEST_SUITE' ? 'UiReports' : 'UiExecutions',
    query: { project: task.project, scheduled_task: task.id, task_name: task.name }
  })
}

// 立即执行任务
const runTaskNow = async (task) => {
  try {
    task.running = true
    await runScheduledTask(task.id)
    ElMessage.success(t('uiAutomation.scheduledTask.messages.runSuccess'))
    setTimeout(() => {
      loadTasks()
    }, 2000)
  } catch (error) {
    ElMessage.error(t('uiAutomation.scheduledTask.messages.runFailed'))
  } finally {
    task.running = false
  }
}

// 格式化日期时间
const formatDateTime = (dateString) => {
  if (!dateString) return '-'
  const date = new Date(dateString)
  const localeStr = locale.value === 'en' ? 'en-US' : 'zh-CN'
  return date.toLocaleString(localeStr, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23'
  }).replace(/\//g, '-')
}

// 获取通知类型标签类型
const getNotificationTypeTagType = (typeDisplay) => {
  const typeMap = {
    '邮箱通知': '',
    'Email Notification': '',
    'Webhook机器人': 'primary',
    'Webhook Robot': 'primary',
    '两者都发送': 'warning',
    'Both': 'warning'
  }
  return typeMap[typeDisplay] || 'info'
}

// 编辑任务
const editTask = async (task) => {
  resetTaskForm()
  editingTask.value = task
  Object.assign(taskForm, {
    name: task.name,
    description: task.description,
    project: task.project,
    task_type: task.task_type,
    trigger_type: task.trigger_type,
    cron_expression: task.cron_expression,
    interval_seconds: task.interval_seconds,
    execute_at: task.execute_at,
    test_suite: task.test_suite || '',
    test_cases: [...(task.test_cases || [])],
    storage_state: task.storage_state || null,
    engine: task.engine || 'playwright',
    browser: task.browser || 'chrome',
    headless: task.headless ?? true,
    notify_on_success: task.notify_on_success || false,
    notify_on_failure: task.notify_on_failure || false,
    notification_type: task.notification_type || '',
    notify_emails: task.notify_emails || []
  })

  // 加载项目相关数据
  if (task.project) {
    await loadProjectData(task.project)
  }

  if (editingTask.value?.id !== task.id || taskForm.project !== task.project) return
  showCreateDialog.value = true
}

// 暂停任务
const pauseTask = async (task) => {
  try {
    await pauseScheduledTask(task.id)
    ElMessage.success(t('uiAutomation.scheduledTask.messages.pauseSuccess'))
    loadTasks()
  } catch (error) {
    console.error('Pause task failed:', error)
    ElMessage.error(t('uiAutomation.scheduledTask.messages.pauseFailed'))
  }
}

// 恢复任务
const resumeTask = async (task) => {
  try {
    await resumeScheduledTask(task.id)
    ElMessage.success(t('uiAutomation.scheduledTask.messages.resumeSuccess'))
    loadTasks()
  } catch (error) {
    ElMessage.error(t('uiAutomation.scheduledTask.messages.resumeFailed'))
  }
}

// 删除任务
const deleteTask = async (task) => {
  try {
    await ElMessageBox.confirm(t('uiAutomation.scheduledTask.messages.deleteConfirm'), t('uiAutomation.scheduledTask.messages.deleteConfirmTitle'), {
      confirmButtonText: t('uiAutomation.common.confirm'),
      cancelButtonText: t('uiAutomation.common.cancel'),
      type: 'warning'
    })
    await deleteScheduledTask(task.id)
    ElMessage.success(t('uiAutomation.scheduledTask.messages.deleteSuccess'))
    loadTasks()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(t('uiAutomation.scheduledTask.messages.deleteFailed'))
    }
  }
}
</script>

<style scoped lang="scss">
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
  background: white;
  padding: 10px 20px;
  border-radius: 4px;
}

/* 跟随全局响应式断点 */
.page-container .page-header .page-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: #303133;

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

.title-group {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.card-container {
  background-color: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.filter-bar {
  margin-bottom: 10px;
}

.pagination-container {
  margin-top: 10px;
  display: flex;
  justify-content: flex-end;
}

:deep(.action-col .cell) {
  white-space: nowrap;
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: nowrap;
  gap: 0;
  white-space: nowrap;
}

.row-actions :deep(.el-button) {
  margin: 0;
  padding: 0 3px;
}

.cron-help {
  margin-top: 8px;
  font-size: 12px;
}

.unit {
  margin-left: 8px;
  color: #606266;
}
.storage-state-option-note {
  display: inline-block;
  width: 50%;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-state-option-name {
  display: inline-block;
  width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

<style lang="scss">
// 套件面板与输入框同宽，长名称不撑开选项。
.scheduled-suite-popper {
  .el-select-dropdown__wrap {
    overflow-x: hidden;
  }

  .scheduled-suite-name {
    display: block;
    width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

// 下拉面板挂载在 body，通过专用类约束宽度和节点收缩，避免长名称撑开面板。
.scheduled-case-tree-popper {
  .el-select-dropdown__wrap {
    overflow-x: hidden;
  }

  .el-tree-node__content {
    min-width: 0;
    overflow: hidden;
  }

  .el-select-dropdown__item {
    min-width: 0;
    overflow: hidden;
  }

  .scheduled-case-tree-name {
    display: block;
    width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
