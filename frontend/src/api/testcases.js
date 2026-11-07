import request from '@/utils/api'

// 获取正式测试用例关联的自动化用例。
export function getTestCaseAutomationLinks(testCaseId) {
  return request({
    url: `/testcases/${testCaseId}/automation-links/`,
    method: 'get'
  })
}

// 获取可关联的自动化用例及其目录。
export function getTestCaseAutomationOptions(testCaseId, params) {
  return request({
    url: `/testcases/${testCaseId}/automation-options/`,
    method: 'get',
    params
  })
}

// 将自动化用例关联到正式测试用例。
export function createTestCaseAutomationLink(testCaseId, data) {
  return request({
    url: `/testcases/${testCaseId}/automation-links/`,
    method: 'post',
    data
  })
}

// 移除正式测试用例上的自动化用例关联。
export function deleteTestCaseAutomationLink(testCaseId, linkId) {
  return request({
    url: `/testcases/${testCaseId}/automation-links/${linkId}/`,
    method: 'delete'
  })
}

// 删除单条测试用例。
export function deleteTestCase(id) {
  return request({
    url: `/testcases/${id}/`,
    method: 'delete'
  })
}

// 批量删除测试用例（后端单次上限 5000 个 id）。
export function bulkDeleteTestCases(testcaseIds) {
  return request({
    url: '/testcases/bulk-delete/',
    method: 'post',
    data: { testcase_ids: testcaseIds }
  })
}

// 删除测试用例模块。
export function deleteTestCaseModule(moduleId) {
  return request({
    url: `/testcases/modules/${moduleId}/`,
    method: 'delete'
  })
}
