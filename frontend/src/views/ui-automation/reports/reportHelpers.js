// UI 自动化测试报告：列表页与详情页共用的展示方法。

// 执行状态标签配色
export const getReportStatusType = (status) => {
  const typeMap = {
    PENDING: 'info',
    RUNNING: 'warning',
    SUCCESS: 'success',
    FAILED: 'danger',
    ABORTED: 'info'
  }
  return typeMap[status] || 'info'
}

// 执行状态文案（需要 i18n 实例）
export const getReportStatusText = (t, status) => {
  const keyMap = {
    PENDING: 'uiAutomation.report.statusPending',
    RUNNING: 'uiAutomation.report.statusRunning',
    SUCCESS: 'uiAutomation.report.statusSuccess',
    FAILED: 'uiAutomation.report.statusFailed',
    ABORTED: 'uiAutomation.report.statusAborted'
  }
  const key = keyMap[status]
  return key ? t(key) : status
}

export const getReportEngineText = (engine) => {
  const engineMap = {
    playwright: 'Playwright',
    selenium: 'Selenium'
  }
  return engineMap[engine] || engine || 'Playwright'
}

export const getReportBrowserText = (browser) => {
  const browserMap = {
    chrome: 'Chrome',
    firefox: 'Firefox',
    safari: 'Safari',
    edge: 'Edge'
  }
  return browserMap[browser] || browser || 'Chrome'
}

export const formatReportDate = (dateString) => {
  if (!dateString) return '-'
  return new Date(dateString).toLocaleString()
}

export const formatReportDuration = (t, seconds) => {
  const value = Number(seconds) || 0
  if (value <= 0) return `0${t('uiAutomation.report.seconds')}`
  if (value < 60) return `${value.toFixed(1)}${t('uiAutomation.report.seconds')}`
  const minutes = Math.floor(value / 60)
  const secs = (value % 60).toFixed(0)
  return `${minutes}${t('uiAutomation.report.minutes')}${secs}${t('uiAutomation.report.seconds')}`
}
