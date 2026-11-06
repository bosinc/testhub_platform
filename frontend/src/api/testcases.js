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
