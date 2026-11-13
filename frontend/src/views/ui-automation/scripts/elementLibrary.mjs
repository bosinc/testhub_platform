// 先按分组建立索引，避免每个页面都重新遍历全部元素；保留接口的节点顺序。
export const buildElementLibraryTree = (groups, elements) => {
  const elementsByGroup = new Map()
  elements.forEach(element => {
    const groupKey = String(element.group_id)
    if (!elementsByGroup.has(groupKey)) elementsByGroup.set(groupKey, [])
    elementsByGroup.get(groupKey).push({
      ...element,
      type: 'element',
      treeKey: `element-${element.id}`,
    })
  })

  const buildGroups = pages => pages.map(group => ({
    ...group,
    type: 'page',
    // 分组和元素来自不同表，不能直接共用数字 ID 作为树的唯一键。
    treeKey: `group-${group.id}`,
    children: [
      ...buildGroups(group.children || []),
      ...(elementsByGroup.get(String(group.id)) || []),
    ],
  }))
  return buildGroups(groups)
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

export const createElementLibraryLoader = (getGroups, getElements) => {
  // 仅合并当前组件内同项目的在途请求；完成后释放，录制导入后仍可读取最新数据。
  const pendingByProject = new Map()
  return projectId => {
    if (!projectId) return Promise.resolve([])
    const key = String(projectId)
    if (pendingByProject.has(key)) return pendingByProject.get(key)

    const pending = Promise.allSettled([
      getGroups({ project: projectId }),
      getElements({ project: projectId }),
    ]).then(results => {
      // 等两项都结束后再清理在途状态，避免失败重试又重复发出尚未完成的请求。
      const failed = results.find(result => result.status === 'rejected')
      if (failed) throw failed.reason
      const [groups, elements] = results.map(result => result.value.data)
      return buildElementLibraryTree(groups || [], elements?.results || elements || [])
    }).finally(() => pendingByProject.delete(key))
    pendingByProject.set(key, pending)
    return pending
  }
}
