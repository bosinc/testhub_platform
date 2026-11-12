/**
 * 测试用例步骤工具：与后端 apps/testcases/steps.py 保持同一套 `#` 约定。
 * 一行 Excel 对应一条用例，「操作步骤」「预期结果」两列用行首 `#` 标记步骤，
 * 两列按 `#` 出现顺序一一对齐；步骤多于预期时补空，预期多于步骤时多余的忽略。
 *
 * 分隔只看行首：行中间的 `#`（如 URL 的 `#/login`）属于正文，不会切段；
 * 一个行首 `#` 到下一个行首 `#` 之间的所有行都属于同一条步骤。
 * 正文某行本身以 `#` 开头时导出成 `\#`、以 `\` 开头时导出成 `\\`，导入时原样还原。
 */
export const STEP_SEPARATOR = '#'
export const ESCAPE_CHAR = '\\'
export const ESCAPED_SEPARATOR = ESCAPE_CHAR + STEP_SEPARATOR
export const ESCAPED_ESCAPE = ESCAPE_CHAR + ESCAPE_CHAR

const BR_PATTERN = /<br\s*\/?>/gi

// 统一换行：`<br>` / `\r\n` / `\r` 一律归一化为 `\n`，否则按行首判定会失效。
function toLines(text) {
  return String(text ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(BR_PATTERN, '\n')
}

// 行首是转义前缀（`\\` 或 `\#`）时，该行是正文而不是分隔符。
function isEscapedLine(line) {
  return line.startsWith(ESCAPED_ESCAPE) || line.startsWith(ESCAPED_SEPARATOR)
}

// 还原行首转义：`\\` → `\`，`\#` → `#`。
function unescapeLine(line) {
  return isEscapedLine(line) ? line.slice(1) : line
}

// 转义行首：`#` → `\#`，`\` → `\\`；其余原样。与后端 _escape_line 一致。
function escapeLine(line) {
  return line.startsWith(ESCAPE_CHAR) || line.startsWith(STEP_SEPARATOR)
    ? ESCAPE_CHAR + line
    : line
}

export function splitStepSegments(text) {
  if (text === null || text === undefined) return []
  const normalized = toLines(text)
  if (!normalized.trim()) return []

  // 只去行尾空白，保留行首缩进 —— 缩进是判断“是不是分隔符”的依据。
  const lines = normalized.split('\n').map(line => line.replace(/\s+$/, ''))
  // 没有任何一行以 `#` 开头：整块内容就是一条步骤，保留内部换行。
  if (!lines.some(line => line.startsWith(STEP_SEPARATOR))) {
    return [normalized.trim()]
  }

  const segments = []
  let current = null
  for (const line of lines) {
    if (isEscapedLine(line)) {
      const body = unescapeLine(line)
      current = current === null ? body : `${current}\n${body}`
    } else if (line.startsWith(STEP_SEPARATOR)) {
      if (current !== null) segments.push(current.trim())
      current = line.slice(1)
    } else if (current !== null) {
      current = current ? `${current}\n${line}` : line
    }
  }
  if (current !== null) segments.push(current.trim())
  return segments
}

export function parseStepPairs(stepsText, expectedText) {
  const stepSegments = splitStepSegments(stepsText)
  const expectedSegments = splitStepSegments(expectedText)
  // 以步骤条数为准：多余的预期直接忽略（不生成空操作步骤，否则编辑页存不了）。
  return stepSegments.map((action, index) => ({
    action,
    expected: expectedSegments[index] || ''
  }))
}

function formatSegment(value) {
  const lines = toLines(value).split('\n')
  const parts = [`${STEP_SEPARATOR}${lines[0]}`]
  // 续行按行首转义：`#` → `\#`、`\` → `\\`，否则再导入会被当成分隔符或被误还原。
  lines.slice(1).forEach(line => parts.push(escapeLine(line)))
  return parts.join('\n')
}

export function formatStepPairs(steps = []) {
  const rows = Array.isArray(steps) ? steps : []
  // 不追加序号，且对续行行首转义，保证「导出 → 再导入」内容不变。
  return {
    stepsText: rows.map(item => formatSegment(item.action)).join('\n'),
    expectedText: rows.map(item => formatSegment(item.expected)).join('\n')
  }
}

/**
 * 清洗步骤数组：保留 id（用于按主键增量保存），sort_order 按当前顺序重排。
 * 单条步骤的操作描述与预期结果都允许为空（只填一侧也是合法步骤），
 * 只有「操作描述与预期结果都为空」的占位行才丢弃。
 */
export function normalizeSteps(steps) {
  if (!Array.isArray(steps)) return []
  const rows = steps
    .map(item => {
      const row = {
        action: String(item?.action ?? '').trim(),
        expected: String(item?.expected ?? '').trim()
      }
      if (item?.id) row.id = item.id
      return row
    })
    .filter(item => item.action || item.expected)
  return rows.map((row, index) => ({ ...row, sort_order: index }))
}
