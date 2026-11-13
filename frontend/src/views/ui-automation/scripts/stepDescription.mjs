const actionKey = action => `uiAutomation.testCase.action${action[0].toUpperCase()}${action.slice(1)}`

// 只本地化解析器明确标记的默认描述，代码注释和用户编辑内容保持原文。
export const localizeGeneratedStepDescription = (step, t) => {
  if (!step.description_generated) return step.description

  const action = step.action_type
  const locator = step.action_params?.locator
  if (locator) {
    const options = locator.options || {}
    const name = options.name || options.label || options.placeholder || locator.value || ''
    const label = t(actionKey(action))
    if (name) return t('uiAutomation.stepEditor.elementDescription', { action: label, name })
    return label
  }

  if (action === 'wait') {
    return t('uiAutomation.stepEditor.generatedWaitDescription', { duration: step.wait_time ?? 1000 })
  }
  if (action === 'navigate' && step.input_value) {
    return t('uiAutomation.stepEditor.generatedNavigateDescription', { url: step.input_value })
  }
  if (action === 'switchTab' && step.input_value !== '') {
    return t('uiAutomation.stepEditor.generatedSwitchTabDescription', { tab: step.input_value })
  }
  if (action === 'press' && step.input_value) {
    return t('uiAutomation.stepEditor.generatedPressDescription', {
      action: t(actionKey(action)), key: step.input_value,
    })
  }
  if (action === 'storageState') return t('uiAutomation.stepEditor.generatedStorageStateDescription')
  return t(actionKey(action))
}
