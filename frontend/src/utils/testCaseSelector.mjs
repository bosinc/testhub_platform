// 先按项目、模块累计用例数量，避免为每个目录节点重复扫描完整候选集。
export const buildTestCaseLibraryTree = (projects, modulesByProject, testcaseList, noModuleLabel) => {
  const countsByProject = new Map()
  for (const testcase of testcaseList) {
    const projectId = String(testcase.project_id)
    if (!countsByProject.has(projectId)) {
      countsByProject.set(projectId, { total: 0, modules: new Map() })
    }
    const counts = countsByProject.get(projectId)
    const moduleId = testcase.module_id == null ? '' : String(testcase.module_id)
    counts.total += 1
    counts.modules.set(moduleId, (counts.modules.get(moduleId) || 0) + 1)
  }

  const buildModules = (modules, projectId, counts) => modules.map(module => {
    const children = buildModules(module.children || [], projectId, counts)
    return {
      id: `module-${projectId}-${module.id}`,
      type: 'module',
      projectId,
      label: module.name,
      count: (counts.get(String(module.id)) || 0) + children.reduce((total, child) => total + child.count, 0),
      moduleIds: [module.id, ...children.flatMap(child => child.moduleIds)],
      children
    }
  })

  return projects.map(project => {
    const counts = countsByProject.get(String(project.id)) || { total: 0, modules: new Map() }
    return {
      id: `project-${project.id}`,
      type: 'project',
      projectId: project.id,
      label: project.name,
      count: counts.total,
      children: [
        ...buildModules(modulesByProject[project.id] || [], project.id, counts.modules),
        {
          id: `unassigned-${project.id}`,
          type: 'unassigned',
          projectId: project.id,
          label: noModuleLabel,
          count: counts.modules.get('') || 0,
          children: []
        }
      ]
    }
  })
}

// 只替换当前页的选择；未展示的页、筛选隐藏的用例和已废弃用例都继续保留。
export const updatePageSelection = (selectedIds, pageRows, selectedRows) => {
  const pageIds = new Set(pageRows.map(row => row.id))
  return [...new Set([
    ...selectedIds.filter(id => !pageIds.has(id)),
    ...selectedRows.map(row => row.id)
  ])]
}
