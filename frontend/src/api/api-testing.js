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

// 向场景用例添加接口请求。
export function addRequestsToScenarioCase(id, requestIds) {
  return request({
    url: `/api-testing/scenario-cases/${id}/add-requests/`,
    method: 'post',
    data: { request_ids: requestIds }
  })
}

// 更新场景用例中的接口请求启用状态。
export function updateScenarioCaseRequest(id, data) {
  return request({
    url: `/api-testing/scenario-case-requests/${id}/`,
    method: 'put',
    data
  })
}

// 更新场景用例中的接口请求断言与提取器配置。
export function patchScenarioCaseRequest(id, data) {
  return request({
    url: `/api-testing/scenario-case-requests/${id}/`,
    method: 'patch',
    data
  })
}

// 调整场景用例中接口请求的执行顺序。
export function moveScenarioCaseRequest(id, direction) {
  return request({
    url: `/api-testing/scenario-case-requests/${id}/move/`,
    method: 'post',
    data: { direction }
  })
}

// 从场景用例中移除接口请求。
export function deleteScenarioCaseRequest(id) {
  return request({
    url: `/api-testing/scenario-case-requests/${id}/`,
    method: 'delete'
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
