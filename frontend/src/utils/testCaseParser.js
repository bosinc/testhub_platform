/**
 * AI 生成测试用例内容解析工具
 *
 * 任务详情页（TaskDetail）与生成记录列表页（GeneratedTestCaseList）共用同一套解析逻辑，
 * 保证“用例列表条数”与“用例条数”两处口径一致。
 *
 * 支持的输入格式：
 * 1. Markdown 表格（默认提示词产出格式，表头为 Case ID / Title / Preconditions / Steps /
 *    Expected Results / Priority / Case Type，同时兼容中文表头）；
 * 2. 结构化文本（“测试用例1: xxx / 前置条件: xxx / 测试步骤: xxx ...”）。
 *
 * 列数少于表头的残缺行（模型命中 max_tokens 被截断）会被跳过，与后端
 * `_parse_test_cases_content` 的处理口径保持一致。
 */

// 表格分隔行，如 |---|---|、|---|:--:|
const SEPARATOR_RE = /^\|?[\s:\-|]+\|?$/

// 标准表头词表：用于判断首行到底是表头还是数据行，避免把数据行误当表头
const HEADER_WORDS_RE = /^(用例\s*id|用例编号|编号|序号|测试目标|测试场景|场景|标题|名称|前置条件|前置|前提条件|操作步骤|测试步骤|步骤|预期结果|预期|期望结果|优先级|测试类型|关联需求|case\s*id|test\s*case\s*id|case\s*no|id|title|scenario|objective|description|preconditions?|pre-?conditions?|prerequisite|steps?|test\s*steps?|expected\s*results?|expected|results?|priority|case\s*type|type)$/

// 无表头时的默认列顺序
const DEFAULT_COLUMNS = ['caseId', 'scenario', 'precondition', 'steps', 'expected', 'priority', null]

/**
 * 按 | 拆分表格行，保留空单元格，避免某列为空导致后续列错位
 */
export function splitTableRow(line) {
  let text = (line || '').trim()
  if (text.startsWith('|')) text = text.slice(1)
  if (text.endsWith('|')) text = text.slice(0, -1)
  return text.split('|').map(cell => cell.trim())
}

/**
 * 是否为表格分隔行
 */
export function isTableSeparator(line) {
  const text = (line || '').trim()
  return Boolean(text) && text.includes('-') && SEPARATOR_RE.test(text)
}

/**
 * 表头 -> 字段映射，同时兼容中文表头与英文表头
 */
export function resolveField(header) {
  const h = (header || '').trim().toLowerCase()
  if (!h) return null
  if (/优先级|priority/.test(h)) return 'priority'
  // 预期结果需先于“步骤”判断，避免被其它关键字误吞
  if (/预期|期望|expected/.test(h)) return 'expected'
  if (/前置|前提|precondition|prerequisite/.test(h)) return 'precondition'
  if (/步骤|step/.test(h)) return 'steps'
  if (/用例\s*id|用例编号|编号|序号|case\s*id|caseid|^id$|case\s*no/.test(h)) return 'caseId'
  if (/标题|场景|测试目标|名称|title|scenario|objective|summary|description/.test(h)) return 'scenario'
  return null
}

/**
 * 解析测试用例内容为数组
 * @param {string} content 用例内容（Markdown 表格或结构化文本）
 * @returns {Array<Object>} 用例数组，字段：caseId / scenario / precondition / steps / expected / priority
 */
export function parseTestCases(content) {
  if (!content) return []

  // 去除 markdown 加粗标记，保留纯净文本
  const cleanContent = content.replace(/\*\*([^*]+)\*\*/g, '$1')
  const rawLines = cleanContent.split('\n')
  const lines = rawLines.filter(line => line.trim())
  const testCases = []

  // 按连续行切分为多个表格块（内容里可能同时有“分析/覆盖矩阵”等表格）
  const tableBlocks = []
  let currentBlock = []
  for (const line of rawLines) {
    const trimmedLine = line.trim()
    if (!trimmedLine) {
      // 空行是表格块的分界
      if (currentBlock.length > 0) {
        tableBlocks.push(currentBlock)
        currentBlock = []
      }
      continue
    }
    if (trimmedLine.includes('|')) {
      // 分隔行属于表格的一部分，只跳过不采集，不能打断当前表格块
      if (isTableSeparator(trimmedLine)) continue
      const cells = splitTableRow(trimmedLine)
      if (cells.length > 1) {
        currentBlock.push(cells)
        continue
      }
    }
    if (currentBlock.length > 0) {
      tableBlocks.push(currentBlock)
      currentBlock = []
    }
  }
  if (currentBlock.length > 0) tableBlocks.push(currentBlock)

  // 取行数最多的表格块作为用例表，避免把非用例表格当成用例表解析
  const tableData = tableBlocks.reduce(
    (best, block) => (block.length > best.length ? block : best),
    []
  )

  if (tableData.length > 1) {
    const cleanBrTags = text => (text ? text.replace(/<br\s*\/?>/gi, '\n') : '')

    const headerCells = tableData[0]
    const headerWordCount = headerCells.filter(
      cell => HEADER_WORDS_RE.test((cell || '').trim().toLowerCase())
    ).length
    const hasHeaderRow = headerWordCount >= 2

    const fieldList = hasHeaderRow ? headerCells.map(resolveField) : DEFAULT_COLUMNS
    const dataRows = hasHeaderRow ? tableData.slice(1) : tableData
    // 期望列数：有表头时以表头为准，否则以数据行最大列数为准
    const expectedColumns = hasHeaderRow
      ? headerCells.length
      : Math.max(...dataRows.map(row => row.length))

    for (const row of dataRows) {
      // 跳过残缺行（模型输出被截断），与后端解析口径一致
      if (row.length < expectedColumns) continue

      const testCase = {}
      fieldList.forEach((field, index) => {
        if (!field) return
        testCase[field] = cleanBrTags(row[index] || '')
      })

      if (testCase.scenario || testCase.caseId || testCase.steps || testCase.expected) {
        // 步骤为空时用场景兜底
        if (!testCase.steps && testCase.scenario) {
          testCase.steps = testCase.scenario
        }
        if (!testCase.priority) {
          testCase.priority = 'P2'
        }
        testCases.push(testCase)
      }
    }

    if (testCases.length > 0) {
      return testCases
    }
  }

  // 结构化文本格式解析（非表格格式兜底）
  let currentTestCase = {}
  let caseNumber = 1

  for (const line of lines) {
    if (line.includes('测试用例') || line.includes('Test Case') ||
        line.match(/^(\d+\.|\*|\-|\d+、)/)) {

      if (Object.keys(currentTestCase).length > 0) {
        testCases.push(currentTestCase)
        caseNumber++
      }

      currentTestCase = {
        caseId: `TC${String(caseNumber).padStart(3, '0')}`,
        scenario: line.replace(/^(\d+\.|\*|\-|\d+、)\s*/, '').replace(/测试用例\d*[:：]?\s*/, '').replace(/Test Case\s*\d*[:：]?\s*/i, ''),
        precondition: '',
        steps: '',
        expected: '',
        priority: 'P2'
      }
    } else if (line.includes('前置条件') || line.includes('前提')) {
      currentTestCase.precondition = line.replace(/.*?[:：]\s*/, '')
    } else if (line.includes('测试步骤') || line.includes('操作步骤') || line.includes('步骤')) {
      currentTestCase.steps = line.replace(/.*?[:：]\s*/, '')
    } else if (line.includes('预期结果') || line.includes('Expected')) {
      currentTestCase.expected = line.replace(/.*?[:：]\s*/, '')
    } else if (line.includes('优先级')) {
      currentTestCase.priority = line.replace(/.*?[:：]\s*/, '')
    }
  }

  if (Object.keys(currentTestCase).length > 0) {
    testCases.push(currentTestCase)
  }

  return testCases
}

/**
 * 统计测试用例条数（与解析结果严格一致）
 */
export function countTestCases(content) {
  return parseTestCases(content).length
}
