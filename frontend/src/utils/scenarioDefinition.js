/**
 * Scenario step-tree helpers.
 *
 * The backend keeps the definition as a flat node list with `parent` and
 * `position`; the editor rebuilds the tree for rendering and always submits a
 * normalized flat list. Keep all structural rules here so components stay thin.
 */

const STEP_REFERENCE = /(?<![\w])steps\.([A-Za-z0-9_-]+)/g

export const CONTAINER_TYPES = ['group', 'condition', 'else', 'for', 'foreach']

export const STEP_TYPE_META = {
  http: { label: 'HTTP 请求', icon: 'Link', container: false },
  group: { label: '分组', icon: 'Folder', container: true },
  condition: { label: 'If', icon: 'BottomLeft', container: true },
  else: { label: 'Else', icon: 'BottomRight', container: true },
  foreach: { label: 'ForEach 循环', icon: 'Refresh', container: true },
  for: { label: 'For 循环', icon: 'RefreshRight', container: true },
  break_if: { label: 'Break If', icon: 'Switch', container: false },
  script: { label: '脚本', icon: 'Document', container: false },
  wait: { label: '等待', icon: 'Timer', container: false },
  scenario_ref: { label: '引用场景', icon: 'Files', container: false }
}

export function createId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, char => {
    const random = (Math.random() * 16) | 0
    const value = char === 'x' ? random : (random & 0x3) | 0x8
    return value.toString(16)
  })
}

export function isContainer(type) {
  return CONTAINER_TYPES.includes(type)
}

export function createNode(type, { parent = null, config = {}, name, enabled = true, sourceType = null, sourceId = null } = {}) {
  return {
    id: createId(),
    parent,
    position: 0,
    type,
    name: name ?? type,
    enabled,
    config,
    source_type: sourceType,
    source_id: sourceId
  }
}

export function childrenOf(steps, parentId) {
  return steps
    .filter(step => (step.parent ?? null) === (parentId ?? null))
    .sort((a, b) => (a.position - b.position) || a.id.localeCompare(b.id))
}

export function buildTree(steps) {
  const map = new Map()
  for (const step of steps) map.set(step.id, { ...step, children: [] })
  const roots = []
  for (const step of steps) {
    const node = map.get(step.id)
    const parent = step.parent ? map.get(step.parent) : null
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  const sort = nodes => {
    nodes.sort((a, b) => (a.position - b.position) || a.id.localeCompare(b.id))
    nodes.forEach(node => sort(node.children))
  }
  sort(roots)
  return roots
}

export function normalizePositions(steps) {
  const groups = new Map()
  for (const step of steps) {
    const key = step.parent ?? '__root__'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(step)
  }
  for (const group of groups.values()) {
    group.sort((a, b) => (a.position - b.position) || a.id.localeCompare(b.id))
    group.forEach((step, index) => { step.position = index })
  }
  return steps
}

export function subtreeIds(steps, rootId) {
  const ids = new Set([rootId])
  const queue = [rootId]
  while (queue.length) {
    const current = queue.shift()
    for (const step of steps) {
      if (step.parent === current && !ids.has(step.id)) {
        ids.add(step.id)
        queue.push(step.id)
      }
    }
  }
  return ids
}

export function pairedElse(steps, conditionId) {
  return steps.find(step => step.type === 'else' && step.config?.if_step_id === conditionId) || null
}

export function pairedCondition(steps, elseStep) {
  const id = elseStep?.config?.if_step_id
  return id ? steps.find(step => step.id === id) || null : null
}

/** Delete a node and its subtree; deleting an If also removes its paired Else. */
export function deleteNode(steps, id) {
  const node = steps.find(step => step.id === id)
  if (!node) return steps
  const ids = subtreeIds(steps, id)
  if (node.type === 'condition') {
    const elseNode = pairedElse(steps, id)
    if (elseNode) for (const child of subtreeIds(steps, elseNode.id)) ids.add(child)
  } else if (node.type === 'else') {
    const condition = pairedCondition(steps, node)
    if (condition) {
      // Removing the Else keeps the If; only the Else subtree goes away.
      ids.delete(condition.id)
    }
  }
  return normalizePositions(steps.filter(step => !ids.has(step.id)))
}

export function rewriteStepReferences(value, idMap) {
  if (typeof value === 'string') {
    return value.replace(STEP_REFERENCE, (match, oldId) => (idMap.has(oldId) ? `steps.${idMap.get(oldId)}` : match))
  }
  if (Array.isArray(value)) return value.map(item => rewriteStepReferences(item, idMap))
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, rewriteStepReferences(item, idMap)]))
  }
  return value
}

/** Deep-copy a subtree (If carries its Else) and insert it after the original. */
export function cloneNode(steps, id) {
  const node = steps.find(step => step.id === id)
  if (!node) return { steps, rootId: null }
  const roots = [node]
  if (node.type === 'condition') {
    const elseNode = pairedElse(steps, id)
    if (elseNode) roots.push(elseNode)
  } else if (node.type === 'else') {
    const condition = pairedCondition(steps, node)
    if (condition) roots.unshift(condition)
  }
  const included = new Set()
  for (const root of roots) for (const child of subtreeIds(steps, root.id)) included.add(child)
  const originals = steps.filter(step => included.has(step.id))
  const idMap = new Map(originals.map(step => [step.id, createId()]))
  const clones = originals.map(step => {
    const clone = JSON.parse(JSON.stringify(step))
    clone.id = idMap.get(step.id)
    clone.parent = step.parent ? idMap.get(step.parent) ?? step.parent : null
    clone.config = rewriteStepReferences(clone.config, idMap)
    if (clone.type === 'else') clone.config.if_step_id = idMap.get(clone.config.if_step_id)
    return clone
  })
  // Insert after the original block so both stay adjacent in the same parent.
  const lastPosition = Math.max(...originals.map(step => step.position))
  clones.forEach((clone, index) => { clone.position = lastPosition + 1 + index })
  return { steps: normalizePositions([...steps, ...clones]), rootId: idMap.get(node.id) }
}

/** Insert a node after a sibling (same parent) or append to a container. */
export function insertAfter(steps, node, siblingId) {
  const sibling = steps.find(step => step.id === siblingId)
  if (!sibling) return normalizePositions([...steps, { ...node, parent: null, position: steps.length }])
  node.parent = sibling.parent ?? null
  const siblings = childrenOf(steps, node.parent)
  const index = siblings.findIndex(step => step.id === siblingId) + 1
  node.position = index
  siblings.splice(index, 0, node)
  siblings.forEach((step, position) => { step.position = position })
  return normalizePositions([...steps, node])
}

export function appendChild(steps, node, parentId) {
  node.parent = parentId
  node.position = childrenOf(steps, parentId).length
  return normalizePositions([...steps, node])
}

function moveRoots(steps, node) {
  const first = node.type === 'else' ? pairedCondition(steps, node) : node
  if (!first) return []
  const last = first.type === 'condition' ? pairedElse(steps, first.id) : null
  return last ? [first, last] : [first]
}

export function siblingMoveTarget(siblings, id, direction) {
  const node = siblings.find(step => step.id === id)
  if (!node) return null
  const roots = moveRoots(siblings, node)
  if (!roots.length) return null
  const up = direction === 'up'
  const boundary = up ? roots[0] : roots[roots.length - 1]
  const index = siblings.indexOf(boundary) + (up ? -1 : 1)
  return siblings[index]?.id ?? null
}

/** Resolve the exact insertion edge without splitting an If/Else pair. */
export function resolveNodeDrop(steps, id, targetId, mode) {
  const node = steps.find(step => step.id === id)
  const target = steps.find(step => step.id === targetId)
  if (!node || !target || !['inside', 'before', 'after'].includes(mode)) return null
  const roots = moveRoots(steps, node)
  const targetRoots = moveRoots(steps, target)
  if (!roots.length || !targetRoots.length || (mode === 'inside' && !isContainer(target.type))) return null
  const anchor = mode === 'inside' ? target : targetRoots[mode === 'before' ? 0 : targetRoots.length - 1]
  const destinationParent = mode === 'inside' ? anchor.id : (anchor.parent ?? null)
  // Break If 是循环体专属步骤，不能拖出循环，也不能放入普通容器。
  if (node.type === 'break_if') {
    const parent = steps.find(step => step.id === destinationParent)
    if (!parent || !['for', 'foreach'].includes(parent.type)) return null
  }
  const rootIds = new Set(roots.map(step => step.id))
  const byId = new Map(steps.map(step => [step.id, step]))
  // A child may target its parent's outer edge, but a block may not target its own children.
  for (let current = anchor; current; current = byId.get(current.parent)) {
    if (rootIds.has(current.id)) return null
  }
  return { anchor, roots, rootIds }
}

/** Reorder the moved roots, leaving their descendant links and branch order intact. */
export function moveNode(steps, id, targetId, mode = 'inside') {
  const drop = resolveNodeDrop(steps, id, targetId, mode)
  if (!drop) return steps
  const { anchor, roots, rootIds } = drop
  const targetParent = mode === 'inside' ? anchor.id : (anchor.parent ?? null)
  const oldParent = roots[0].parent ?? null
  const parent = targetParent
  const siblings = childrenOf(steps, parent).filter(step => !rootIds.has(step.id))
  const index = mode === 'inside' ? siblings.length : siblings.indexOf(anchor) + (mode === 'after' ? 1 : 0)
  siblings.splice(index, 0, ...roots)

  const updates = new Map()
  const updateSiblings = (items, parentId) => items.forEach((step, position) => {
    if ((step.parent ?? null) !== parentId || step.position !== position) {
      updates.set(step.id, { ...step, parent: parentId, position })
    }
  })
  updateSiblings(siblings, parent)
  if (oldParent !== parent) {
    updateSiblings(childrenOf(steps, oldParent).filter(step => !rootIds.has(step.id)), oldParent)
  }
  return updates.size ? steps.map(step => updates.get(step.id) ?? step) : steps
}

/** Ensure a condition has exactly one Else right after it, creating it if needed. */
export function ensureElse(steps, conditionId) {
  const condition = steps.find(step => step.id === conditionId)
  if (!condition || condition.type !== 'condition') return { steps, elseId: null }
  const existing = pairedElse(steps, conditionId)
  if (existing) return { steps, elseId: existing.id }
  const elseNode = createNode('else', { config: { if_step_id: conditionId }, enabled: condition.enabled })
  return { steps: insertAfter(steps, elseNode, conditionId), elseId: elseNode.id }
}

/** Flat nodes ordered depth-first, matching the backend's load_definition order. */
export function flattenTree(steps) {
  const ordered = []
  const walk = parentId => {
    for (const step of childrenOf(steps, parentId)) {
      ordered.push(step)
      walk(step.id)
    }
  }
  walk(null)
  return ordered
}
