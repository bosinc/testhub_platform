// UI 自动化前端公共方法集中在本文件：动作文案、元素树/用例树读取、用例变量校验、远程画面窗口。
// 远程画面依赖接口层和 Pinia store，改为调用时按需加载，使本文件可被 Node 直接导入做单元测试。

/* ------------------------------ 用例变量 ------------------------------ */

const variableTypes = ['string', 'number', 'boolean', 'array', 'object']

export const loadUiVariables = (variables = []) => variables.map(variable => ({
  ...variable,
  // 非字符串值在表单中转成 JSON 文本，供用户编辑后再按类型还原。
  _originalValue: variable.value,
  value: typeof variable.value === 'string' ? variable.value : JSON.stringify(variable.value)
}))

export const formatUiVariables = (variables = []) => {
  const names = new Set()
  return variables.map((variable, index) => {
    const { name, type, description = '' } = variable
    if (typeof name !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || !variableTypes.includes(type)) {
      throw new Error(`Variable ${index + 1}: invalid name or type`)
    }
    const key = name
    if (names.has(key)) throw new Error(`Duplicate variable: ${key}`)
    names.add(key)
    let value = variable.value
    if (type === 'string' && value === 'null' && variable._originalValue === null) value = null
    const expression = typeof value === 'string' && /^(\{\{[\s\S]*\}\}|\$\{[\s\S]*\})$/.test(value)
    if (type !== 'string' && !expression) {
      try {
        value = JSON.parse(value)
      } catch {
        throw new Error(`${key}: invalid ${type} value`)
      }
      const valid = value === null || (
        type === 'number' ? typeof value === 'number' && Number.isFinite(value)
          : type === 'boolean' ? typeof value === 'boolean'
            : type === 'array' ? Array.isArray(value)
              : typeof value === 'object' && !Array.isArray(value)
      )
      if (!valid) throw new Error(`${key}: value must be ${type}`)
    }
    return { name, type, value, description }
  })
}

/* ------------------------------ 动作文案 ------------------------------ */

// UI 自动化动作名称统一映射。
// 执行日志（用例执行结果、执行记录详情、测试报告用例详情）里显示的动作名，与步骤编辑器的下拉选项共用同一套文案键。
const ACTION_LABEL_KEYS = {
  click: 'actionClick',
  fill: 'actionFill',
  press: 'actionPress',
  select: 'actionSelect',
  check: 'actionCheck',
  uncheck: 'actionUncheck',
  gettext: 'actionGetText',
  waitfor: 'actionWaitFor',
  hover: 'actionHover',
  scroll: 'actionScroll',
  screenshot: 'actionScreenshot',
  assert: 'actionAssert',
  wait: 'actionWait',
  switchtab: 'actionSwitchTab',
  navigate: 'actionNavigate',
  setvariable: 'actionSetVariable',
  storagestate: 'uiAutomation.storageState.label'
}

// 后端历史数据里可能出现 STORAGE_STATE / GETTEXT 这类大写、下划线写法，统一归一化
const normalizeActionType = actionType => String(actionType).toLowerCase().replace(/[\s_-]/g, '')

/**
 * 取动作类型的中文/英文名称。
 * @param {(key: string) => string} t vue-i18n 的 t 方法
 * @param {string} actionType 后端返回的 action_type
 * @returns {string} 已翻译的动作名；未知类型回退为原始值
 */
export const getUiActionLabel = (t, actionType) => {
  if (!actionType) return ''
  const raw = String(actionType)
  const labelKey = ACTION_LABEL_KEYS[normalizeActionType(raw)]
  if (!labelKey) return raw
  const fullKey = labelKey.startsWith('uiAutomation.') ? labelKey : `uiAutomation.testCase.${labelKey}`
  const label = t(fullKey)
  // 文案缺失时 vue-i18n 会原样返回 key，此时回退原始值，避免把 key 显示出来
  return label === fullKey ? raw : label
}

/* ------------------------------ 元素树 ------------------------------ */

// 从服务端树读取元素或真实页面选项，不重新组装层级，也不将虚拟分组用于写入。
export const getElementLibraryNodes = (tree, type) => {
  const nodes = []
  const pending = [...tree].reverse()
  while (pending.length) {
    const node = pending.pop()
    if (node.type === type && !node.synthetic) nodes.push(node)
    const children = node.children || []
    for (let index = children.length - 1; index >= 0; index -= 1) pending.push(children[index])
  }
  return nodes
}

// 先补齐步骤已关联的元素选项，再更新步骤的 ID，避免列表分页遗漏导致选择器匹配失败。
export const mergeStepElementOptions = (elements, steps) => {
  const byId = new Map(elements.map(element => [String(element.id), element]))
  steps.forEach(step => {
    const element = step.target_element
    const selectedId = step.element_id || element?.id
    if (element?.id && String(element.id) === String(selectedId) && !byId.has(String(element.id))) {
      byId.set(String(element.id), element)
    }
  })
  return [...byId.values()]
}

export const createElementLibraryLoader = getTree => {
  // 仅合并当前组件内同项目的在途请求；完成后释放，录制导入后仍可读取最新数据。
  const pendingByProject = new Map()
  return projectId => {
    if (!projectId) return Promise.resolve([])
    const key = String(projectId)
    if (pendingByProject.has(key)) return pendingByProject.get(key)

    const pending = Promise.resolve().then(() => getTree({ project: projectId }))
      .then(({ data }) => data)
      .finally(() => pendingByProject.delete(key))
    pendingByProject.set(key, pending)
    return pending
  }
}

/* ------------------------------ 用例树 ------------------------------ */

/** 从服务端树中读取指定类型的节点，供目录选项和用例查找复用，不重新拼树。 */
export function getTestCaseTreeNodes(tree, type) {
  const nodes = []
  const pending = [...tree].reverse()
  while (pending.length) {
    const node = pending.pop()
    if (node.type === type) nodes.push(node)
    const children = node.children || []
    for (let index = children.length - 1; index >= 0; index -= 1) pending.push(children[index])
  }
  return nodes
}

/** 只按用例名称过滤，保留目录层级和空目录，不修改原树或已加载的编辑详情。 */
export function filterTestCaseTree(tree, keyword, predicate = null) {
  const query = keyword.trim().toLocaleLowerCase()
  if (!query && !predicate) return tree
  return tree.flatMap(node => {
    if (node.type === 'folder') {
      const children = filterTestCaseTree(node.children || [], query, predicate)
      // 有筛选条件时隐藏没有命中用例的目录，避免目录仍全部显示造成筛选未生效的错觉。
      return children.length ? [{ ...node, children }] : []
    }
    const matchesKeyword = node.name.toLocaleLowerCase().includes(query)
    return matchesKeyword && (!predicate || predicate(node)) ? [node] : []
  })
}

/* ------------------------------ 远程画面 ------------------------------ */

let remoteDesktopEnabled = false
let capabilityLoaded = false
let capabilityRequest = null
let capabilityGeneration = 0

export const resetUiRemoteCapabilities = () => {
  // Invalidate pending responses as well as the cached capability on logout.
  capabilityGeneration += 1
  remoteDesktopEnabled = false
  capabilityLoaded = false
  capabilityRequest = null
}

export const loadUiRemoteCapabilities = () => {
  if (capabilityRequest) return capabilityRequest
  if (capabilityLoaded) return Promise.resolve(remoteDesktopEnabled)

  // Explicitly preload after authentication; importing this module must not send requests.
  const generation = capabilityGeneration
  capabilityRequest = Promise.all([import('@/api/ui_automation'), import('@/stores/user')])
    .then(([{ getUiRemoteCapabilities }, { useUserStore }]) => {
      const userStore = useUserStore()
      if (generation !== capabilityGeneration) return false
      if (!userStore.isAuthenticated) {
        resetUiRemoteCapabilities()
        return false
      }
      return getUiRemoteCapabilities().then(response => {
        if (generation !== capabilityGeneration || !userStore.isAuthenticated) return false
        remoteDesktopEnabled = Boolean(response.data.enabled)
        capabilityLoaded = true
        return remoteDesktopEnabled
      })
    })
    // Optional capability failures disable the popup and can be retried on the next visit.
    .catch(() => false)
    .finally(() => {
      if (generation === capabilityGeneration) capabilityRequest = null
    })
  return capabilityRequest
}

export const reserveUiRemoteWindow = message => {
  // 保持同步执行，window.open 必须在用户点击的同一任务里调用，否则会被浏览器拦截。
  // 退出登录时 resetUiRemoteCapabilities 会关闭该开关。
  if (!remoteDesktopEnabled) return null

  const target = window.open('about:blank', '_blank')
  if (!target) return null

  try {
    const popupDocument = target.document
    popupDocument.open()
    popupDocument.write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>html,body{width:100%;height:100%;margin:0}body{display:grid;place-items:center;background:#f5f7fa;color:#303133;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.loading{display:flex;align-items:center;gap:12px}.spinner{width:18px;height:18px;border:2px solid #dcdfe6;border-top-color:#409eff;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}</style></head><body></body></html>')
    popupDocument.close()
    popupDocument.title = message
    const loading = popupDocument.createElement('main')
    loading.className = 'loading'
    loading.setAttribute('aria-live', 'polite')
    const spinner = popupDocument.createElement('span')
    spinner.className = 'spinner'
    spinner.setAttribute('aria-hidden', 'true')
    const label = popupDocument.createElement('span')
    label.textContent = message
    loading.append(spinner, label)
    popupDocument.body.replaceChildren(loading)
    return target
  } catch {
    target.close()
    return null
  }
}

export const openUiRemoteSession = (router, sessionId, reservedWindow = null) => {
  const route = { name: 'UiRemoteDesktop', params: { sessionId } }
  const url = router.resolve(route).href
  const target = reservedWindow && !reservedWindow.closed ? reservedWindow : window.open(url, '_blank')

  if (!target) {
    router.push({ ...route, query: { fallback: '1' } })
    return null
  }

  if (reservedWindow && !reservedWindow.closed) target.location.href = url
  target.focus()
  return target
}

export const closeReservedUiRemoteWindow = target => {
  if (target && !target.closed) target.close()
}
