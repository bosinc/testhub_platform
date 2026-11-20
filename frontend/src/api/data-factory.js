import request from '@/utils/api'

// 获取工具分类
export function getCategories() {
  return request({
    url: '/data-factory/categories/',
    method: 'get'
  })
}

// 执行工具
export function executeTool(data) {
  return request({
    url: '/data-factory/',
    method: 'post',
    data
  })
}

// 获取历史记录
export function getHistory(params) {
  return request({
    url: '/data-factory/',
    method: 'get',
    params
  })
}

// 获取统计信息
export function getStatistics() {
  return request({
    url: '/data-factory/statistics/',
    method: 'get'
  })
}

// 删除记录
export function deleteRecord(id) {
  return request({
    url: `/data-factory/${id}/`,
    method: 'delete'
  })
}

// 批量生成
export function batchGenerate(data) {
  return request({
    url: '/data-factory/batch_generate/',
    method: 'post',
    data
  })
}

// 获取变量函数列表（用于变量助手）
let variableFunctionsRequest = null

export function getVariableFunctions() {
  // 场景详情页会同时挂载多个 KeyValueEditor，共用同一个变量函数请求，避免重复调用接口。
  if (!variableFunctionsRequest) {
    variableFunctionsRequest = request({
      url: '/data-factory/variable_functions/',
      method: 'get'
    }).catch(error => {
      // 请求失败时清除缓存，后续重新进入页面仍可正常重试。
      variableFunctionsRequest = null
      throw error
    })
  }

  return variableFunctionsRequest
}
