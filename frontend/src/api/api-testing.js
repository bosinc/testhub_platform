import request from '@/utils/api'

// 仪表盘相关API
export function getDashboardStats() {
  return request({
    url: '/api-testing/dashboard/stats/',
    method: 'get'
  })
}

// 获取定时任务列表
export function getScheduledTasks(params) {
  return request({
    url: '/api-testing/scheduled-tasks/',
    method: 'get',
    params
  })
}

// 创建定时任务
export function createScheduledTask(data) {
  return request({
    url: '/api-testing/scheduled-tasks/',
    method: 'post',
    data
  })
}

// 更新定时任务
export function updateScheduledTask(id, data) {
  return request({
    url: `/api-testing/scheduled-tasks/${id}/`,
    method: 'patch',
    data
  })
}

// 删除定时任务
export function deleteScheduledTask(id) {
  return request({
    url: `/api-testing/scheduled-tasks/${id}/`,
    method: 'delete'
  })
}

// 立即执行定时任务
export function runScheduledTask(id) {
  return request({
    url: `/api-testing/scheduled-tasks/${id}/run_now/`,
    method: 'post'
  })
}

// 暂停定时任务
export function pauseScheduledTask(id) {
  return request({
    url: `/api-testing/scheduled-tasks/${id}/pause/`,
    method: 'post'
  })
}

// 激活定时任务
export function activateScheduledTask(id) {
  return request({
    url: `/api-testing/scheduled-tasks/${id}/activate/`,
    method: 'post'
  })
}

// 获取执行日志
export function getExecutionLogs(taskId, params = {}) {
  return request({
    url: `/api-testing/scheduled-tasks/${taskId}/execution_logs/`,
    method: 'get',
    params
  })
}

// 获取场景用例列表
export function getScenarioCases(params) {
  return request({
    url: '/api-testing/scenario-cases/',
    method: 'get',
    params
  })
}

// 获取单个接口场景用例详情。
export function getScenarioCaseDetail(id) {
  return request({
    url: `/api-testing/scenario-cases/${id}/`,
    method: 'get'
  })
}

// 创建接口场景用例。
export function createScenarioCase(data) {
  return request({
    url: '/api-testing/scenario-cases/',
    method: 'post',
    data
  })
}

// 完整更新接口场景用例。
export function updateScenarioCase(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/`,
    method: 'put',
    data
  })
}

// 部分更新接口场景用例。
export function patchScenarioCase(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/`,
    method: 'patch',
    data
  })
}

// 删除接口场景用例。
export function deleteScenarioCase(id) {
  return request({
    url: `/api-testing/scenario-cases/${id}/`,
    method: 'delete'
  })
}

// 获取项目下的全部场景用例，自动读取分页结果。
export async function getAllScenarioCases(params = {}) {
  return getAllPages('/api-testing/scenario-cases/', params)
}

// 获取项目下的全部场景用例文件夹，自动读取分页结果。
export async function getScenarioCaseFolders(params = {}) {
  return getAllPages('/api-testing/scenario-case-folders/', params)
}

// 获取项目下的全部接口集合及请求，供场景用例树使用。
export async function getAllApiCollections(params = {}) {
  return getAllPages('/api-testing/collections/', params)
}

export async function getAllApiRequests(params = {}) {
  return getAllPages('/api-testing/requests/', params)
}

// 完整复制场景用例（步骤、变量、执行配置）。
export function duplicateScenarioCase(id, data = {}) {
  return request({
    url: `/api-testing/scenario-cases/${id}/duplicate/`,
    method: 'post',
    data
  })
}

// 保存场景用例中的单个步骤，不影响场景其他步骤与场景级配置。
export function saveScenarioStep(id, stepId, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/steps/${stepId}/`,
    method: 'put',
    data
  })
}

// 创建场景用例文件夹。
export function createScenarioCaseFolder(data) {
  return request({
    url: '/api-testing/scenario-case-folders/',
    method: 'post',
    data
  })
}

// 更新场景用例文件夹。
export function updateScenarioCaseFolder(id, data) {
  return request({
    url: `/api-testing/scenario-case-folders/${id}/`,
    method: 'patch',
    data
  })
}

// 删除场景用例文件夹。
export function deleteScenarioCaseFolder(id) {
  return request({
    url: `/api-testing/scenario-case-folders/${id}/`,
    method: 'delete'
  })
}

async function getAllPages(url, params = {}) {
  const results = []
  let page = 1
  while (true) {
    const { data } = await request({
      url,
      method: 'get',
      params: { ...params, page, page_size: 1500 }
    })
    if (Array.isArray(data)) return data
    results.push(...(data.results || []))
    if (!data.next) return results
    page += 1
  }
}

// 获取测试套件列表。定时任务和测试套件管理使用此接口。
export function getTestSuites(params) {
  return request({
    url: '/api-testing/test-suites/',
    method: 'get',
    params
  })
}

// 获取API请求列表
export function getApiRequests(params) {
  return request({
    url: '/api-testing/requests/',
    method: 'get',
    params
  })
}

// 获取环境列表
export function getEnvironments(params) {
  return request({
    url: '/api-testing/environments/',
    method: 'get',
    params
  })
}

// 获取项目列表
export function getApiProjects(params) {
  return request({
    url: '/api-testing/projects/',
    method: 'get',
    params
  })
}

// 获取集合列表
export function getApiCollections(params) {
  return request({
    url: '/api-testing/collections/',
    method: 'get',
    params
  })
}

// 执行场景用例
export function executeScenarioCase(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/execute/`,
    method: 'post',
    data
  })
}

// 获取场景用例的执行历史。
export function getScenarioCaseExecutions(params) {
  return request({
    url: '/api-testing/test-executions/',
    method: 'get',
    params
  })
}

// 删除场景执行记录。
export function deleteScenarioExecution(id) {
  return request({
    url: `/api-testing/test-executions/${id}/`,
    method: 'delete'
  })
}

// 批量删除场景执行记录。
export function batchDeleteScenarioExecutions(ids) {
  return request({
    url: '/api-testing/test-executions/batch-delete/',
    method: 'post',
    data: { ids }
  })
}

// 获取接口测试套件执行报告列表。
export function getApiSuiteExecutions(params) {
  return request({
    url: '/api-testing/suite-executions/',
    method: 'get',
    params
  })
}

// 获取单条接口测试套件执行报告。
export function getApiSuiteExecution(id) {
  return request({
    url: `/api-testing/suite-executions/${id}/`,
    method: 'get'
  })
}

// 删除接口测试套件执行报告。
export function deleteApiSuiteExecution(id) {
  return request({
    url: `/api-testing/suite-executions/${id}/`,
    method: 'delete'
  })
}

// 批量删除接口测试套件执行报告。
export function batchDeleteApiSuiteExecutions(ids) {
  return request({
    url: '/api-testing/suite-executions/batch-delete/',
    method: 'post',
    data: { ids }
  })
}

// 执行测试套件
export function executeTestSuite(id, data) {
  return request({
    url: `/api-testing/test-suites/${id}/execute/`,
    method: 'post',
    data
  })
}

// 执行API请求
export function executeApiRequest(id, data) {
  return request({
    url: `/api-testing/api-requests/${id}/execute/`,
    method: 'post',
    data
  })
}

// 获取执行结果
export function getExecutionResult(id) {
  return request({
    url: `/api-testing/executions/${id}/`,
    method: 'get'
  })
}

// 读取单个接口请求（用于导入为场景步骤）
export function getApiRequest(id) {
  return request({
    url: `/api-testing/requests/${id}/`,
    method: 'get'
  })
}

// 读取场景编排定义（步骤树 + 场景变量 + 执行配置）
export function getScenarioDefinition(id) {
  return request({
    url: `/api-testing/scenario-cases/${id}/definition/`,
    method: 'get'
  })
}

// 原子保存整棵步骤树
export function saveScenarioDefinition(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/definition/`,
    method: 'put',
    data
  })
}

// 仅校验编排定义，不落库
export function validateScenarioDefinition(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/validate/`,
    method: 'post',
    data
  })
}

// 步骤类型能力（可编辑名称、是否容器、允许来源等）
export function getStepCapabilities() {
  return request({
    url: '/api-testing/scenario-cases/step-capabilities/',
    method: 'get'
  })
}

// 调试单个步骤的请求草稿（不保存），使用场景环境与初始变量
export function debugScenarioStep(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/debug-step/`,
    method: 'post',
    data
  })
}

// 从单接口用例导入一个 HTTP 步骤
export function importApiRequestStep(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/import-request/`,
    method: 'post',
    data
  })
}

// 从其他场景用例深拷贝步骤
export function importScenarioSteps(id, data) {
  return request({
    url: `/api-testing/scenario-cases/${id}/import-scenario/`,
    method: 'post',
    data
  })
}

// 增量读取步骤执行结果
export function getExecutionSteps(id, params = {}) {
  return request({
    url: `/api-testing/executions/${id}/steps/`,
    method: 'get',
    params
  })
}

// 请求取消执行
export function cancelExecution(id) {
  return request({
    url: `/api-testing/executions/${id}/cancel/`,
    method: 'post'
  })
}

// 获取请求历史
export function getRequestHistory(params) {
  return request({
    url: '/api-testing/histories/',
    method: 'get',
    params
  })
}

// 删除请求历史
export function deleteRequestHistory(id) {
  return request({
    url: `/api-testing/histories/${id}/`,
    method: 'delete'
  })
}

// 批量删除请求历史
export function batchDeleteRequestHistory(ids) {
  return request({
    url: '/api-testing/histories/batch-delete/',
    method: 'post',
    data: { ids }
  })
}

// 获取用户列表
export function getUsers(params) {
  return request({
    url: '/api-testing/users/',
    method: 'get',
    params
  })
}
// 获取操作日志
export function getOperationLogs(params) {
  return request({
    url: '/api-testing/operation-logs/',
    method: 'get',
    params
  })
}
