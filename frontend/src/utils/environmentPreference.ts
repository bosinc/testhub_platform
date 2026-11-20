/**
 * API 测试模块的环境选择记忆。
 *
 * 用户在任意页面选择环境后写入 localStorage，其他页面的环境选择器
 * 在环境列表加载完成后读取并自动选中同一个环境。
 */

const STORAGE_KEY = 'api-testing:selected-environment-id'

export function getRememberedEnvironmentId() {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === null || value === '' ? null : value
  } catch {
    // 隐私模式或禁用存储时，退化为不记忆
    return null
  }
}

export function rememberEnvironment(value) {
  try {
    if (value === null || value === undefined || value === '') {
      localStorage.removeItem(STORAGE_KEY)
    } else {
      localStorage.setItem(STORAGE_KEY, String(value))
    }
  } catch {
    // 存储不可用时只影响记忆功能
  }
}

/** 在已加载的环境列表里找到记忆的环境；找不到返回 null（不覆盖记忆）。 */
export function findRememberedEnvironment(environments) {
  const remembered = getRememberedEnvironmentId()
  if (!remembered) return null
  return (environments || []).find(env => String(env.id) === String(remembered)) || null
}
