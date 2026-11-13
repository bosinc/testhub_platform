/**
 * API 断言的共享编辑方法。
 * 单接口和测试套件使用同一套字段初始化、类型切换及旧数据兼容规则。
 */
export const normalizeAssertion = (assertion = {}) => {
  const { value, ...normalized } = assertion
  const result = {
    ...normalized,
    expected: assertion.expected ?? value ?? (
      assertion.type === 'status_code' ? 200 :
        assertion.type === 'response_time' ? 1000 : ''
    )
  }
  // JSONPath 断言的操作符缺省为 equals：与历史数据（无 operator 字段）行为一致
  if (result.type === 'json_path' && !result.operator) result.operator = 'equals'
  return result
}

export const normalizeAssertions = (assertions = []) => (
  Array.isArray(assertions) ? assertions.map(normalizeAssertion) : []
)

export const createAssertion = (name) => ({
  name,
  type: 'status_code',
  expected: 200
})

export const resetAssertionFields = (assertion) => {
  if (!assertion) return assertion
  delete assertion.operator
  if (assertion.type === 'status_code') {
    assertion.expected = 200
  } else if (assertion.type === 'response_time') {
    assertion.expected = 1000
  } else if (assertion.type === 'contains' || assertion.type === 'equals') {
    assertion.expected = ''
  } else if (assertion.type === 'json_path') {
    assertion.json_path = ''
    assertion.expected = ''
    assertion.operator = 'equals'
  } else if (assertion.type === 'header') {
    assertion.header_name = ''
    assertion.expected_value = ''
  }
  return assertion
}

export const stripLegacyAssertionValue = (assertion) => {
  const { value, ...normalized } = assertion || {}
  return normalized
}

