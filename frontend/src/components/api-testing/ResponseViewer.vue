<template>
  <div v-if="response" class="response-section" :class="{ 'is-fill-height': fillHeight }">
    <el-tabs v-model="resultActiveTab" class="result-tabs">
      <el-tab-pane :label="$t('apiTesting.interface.response')" name="response">
        <el-tabs v-model="responseActiveTab" class="nested-tabs">
          <el-tab-pane label="Body" name="body">
            <div class="response-body">
              <div class="response-actions"><el-button-group>
                <el-button @click="formatResponse">{{ $t('apiTesting.interface.format') }}</el-button>
                <el-button @click="copyResponse">{{ $t('apiTesting.interface.copy') }}</el-button>
                <el-button @click="showJsonPathExtractor = !showJsonPathExtractor">{{ $t('apiTesting.interface.jsonPathExtract') }}</el-button>
              </el-button-group></div>
              <div v-if="showJsonPathExtractor" class="jsonpath-extractor">
                <el-input v-model="jsonPathExpression" :placeholder="$t('apiTesting.interface.jsonPathExample')" @input="evaluateJsonPath"><template #append><el-button @click="copyJsonPathResult">{{ $t('apiTesting.interface.copyResult') }}</el-button></template></el-input>
                <div v-if="jsonPathResult !== null" class="jsonpath-result"><strong>{{ $t('apiTesting.interface.extractResult') }}</strong><pre>{{ jsonPathResult }}</pre></div>
              </div>
              <div ref="responseEditorHost" class="response-content"></div>
            </div>
          </el-tab-pane>
          <el-tab-pane label="Headers" name="headers"><div class="response-headers"><div v-for="(value, key) in (response.response_data?.headers || {})" :key="key" class="header-row"><strong>{{ key }}:</strong> {{ value }}</div></div></el-tab-pane>
          <el-tab-pane
            v-if="response.assertions_results?.length && !hideAssertions"
            :label="$t('apiTesting.interface.assertionResults')"
            name="assertions"
          >
            <div class="assertions-results">
              <div v-for="(result, index) in response.assertions_results" :key="index" class="assertion-result-item" :class="result.passed ? 'passed' : 'failed'">
                <div class="assertion-result-header"><el-tag :type="result.passed ? 'success' : 'danger'">{{ $t(result.passed ? 'apiTesting.interface.passed' : 'apiTesting.interface.failed') }}</el-tag><span>{{ result.name }}</span></div>
                <div class="assertion-result-details"><div class="result-row">{{ $t('apiTesting.interface.expected') }} {{ formatValue(result.expected) }}</div><div class="result-row">{{ $t('apiTesting.interface.actual') }} {{ formatValue(result.actual) }}</div><div v-if="result.error" class="result-row error">{{ $t('apiTesting.interface.error') }} {{ result.error }}</div></div>
              </div>
            </div>
          </el-tab-pane>
          <el-tab-pane v-if="response.extraction_results?.length" :label="$t('apiTesting.interface.extractionResults')" name="extractors"><div v-for="(result, index) in response.extraction_results" :key="index" class="header-row">{{ result.name }} <el-tag :type="result.success ? 'success' : 'danger'">{{ result.success ? formatValue(result.value) : result.error }}</el-tag></div></el-tab-pane>
        </el-tabs>
      </el-tab-pane>
      <el-tab-pane :label="$t('apiTesting.interface.requestInfo')" name="request">
        <div class="header-row request-line">
          <span class="method-tag" :class="(response.request_data?.method || 'GET').toLowerCase()">
            {{ response.request_data?.method || 'GET' }}
          </span>
          <span class="request-url">{{ response.request_data?.url }}</span>
        </div>
        <el-tabs v-model="requestActiveTab" class="nested-tabs">
          <el-tab-pane label="Body" name="body"><pre v-if="hasRequestBodyData" class="request-content">{{ formattedRequestBody }}</pre><div v-else class="empty-tip">{{ $t('apiTesting.interface.noRequestBody') }}</div></el-tab-pane>
          <el-tab-pane label="Headers" name="headers"><div v-if="Object.keys(response.request_data?.headers || {}).length" class="response-headers"><div v-for="(value, key) in (response.request_data?.headers || {})" :key="key" class="header-row"><strong>{{ key }}:</strong> {{ value }}</div></div><div v-else class="empty-tip">{{ $t('apiTesting.interface.noRequestHeaders') }}</div></el-tab-pane>
        </el-tabs>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
// 按需引入 Monaco 的基础样式、图标字体和折叠贡献模块，确保折叠按钮正常显示。
import 'monaco-editor/esm/vs/editor/browser/widget/codeEditor/editor.css'
import 'monaco-editor/esm/vs/base/browser/ui/codicons/codiconStyles'
import 'monaco-editor/esm/vs/editor/contrib/folding/browser/folding'
import 'monaco-editor/esm/vs/language/json/monaco.contribution'
import 'monaco-editor/esm/vs/editor/contrib/contextmenu/browser/contextmenu'
import 'monaco-editor/esm/vs/editor/contrib/clipboard/browser/clipboard'

const props = defineProps({
  response: { type: Object, default: null },
  hideAssertions: { type: Boolean, default: false },
  // 详情抽屉需要让编辑器占满断言区域之外的剩余高度。
  fillHeight: { type: Boolean, default: false }
})
const { hideAssertions } = props
const { t } = useI18n()
const resultActiveTab = ref('response')
const responseActiveTab = ref('body')
const requestActiveTab = ref('body')
const responseEditorHost = ref(null)
const showJsonPathExtractor = ref(false)
const jsonPathExpression = ref('')
const jsonPathResult = ref(null)
const formattedBody = ref(null)
let responseEditor = null
let unmounted = false

function unwrapRequestBody(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return body
  // 历史调试接口使用 mode/raw，场景执行接口使用 type/data；详情面板统一展示实际内容。
  const requestTypes = new Set(['none', 'json', 'raw', 'x-www-form-urlencoded', 'form-data', 'binary'])
  if (body.type === 'none') return null
  if (requestTypes.has(body.type) && Object.hasOwn(body, 'data')) return body.data
  if (body.mode === 'raw') return body.raw ?? body.data
  if (body.mode === 'json') return body.json ?? body.raw ?? body.data
  if (body.mode === 'formdata') return body.formdata ?? body.data
  if (body.mode === 'urlencoded') return body.urlencoded ?? body.data
  return body
}

const formatValue = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2)

function formatRequestBody(body) {
  if (typeof body !== 'string') return formatValue(body) ?? ''
  const text = body.trim()
  if (!text) return body
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return body }
}

function formatResponseBody(body) {
  if (body == null) return ''
  if (typeof body !== 'string') return formatValue(body) ?? ''
  const text = body.trim()
  if (!text) return body
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return body }
}

const responseBody = computed(() => {
  const data = props.response?.response_data
  if (!data) return ''
  if (formattedBody.value !== null) return formattedBody.value
  if (Object.hasOwn(data, 'json') && data.json != null) return formatResponseBody(data.json)
  return formatResponseBody(data.body)
})
const hasRequestBodyData = computed(() => {
  const body = unwrapRequestBody(props.response?.request_data?.body)
  return body != null && body !== '' && (typeof body !== 'object' || Object.keys(body).length > 0)
})
const formattedRequestBody = computed(() => formatRequestBody(
  unwrapRequestBody(props.response?.request_data?.body)
))

const appendJsonPathKey = (parent, key) => /^[A-Za-z_$][\w$]*$/.test(key) ? `${parent}.${key}` : `${parent}[${JSON.stringify(key)}]`
// Monaco line actions use the pretty-printed JSON tree, including quoted property names.
function buildJsonPathByLine(content) {
  const linePaths = new Map()
  try { JSON.parse(content) } catch { return linePaths }
  const stack = []
  content.split('\n').forEach((line, index) => {
    const text = line.trim()
    if (!text) return
    if (/^[}\]]/.test(text)) { linePaths.set(index + 1, stack.at(-1)?.path || '$'); stack.pop(); return }
    const match = text.match(/^"((?:\\.|[^"\\])*)"\s*:\s?(.*)$/)
    const parent = stack.at(-1)
    let path = '$'
    let value = text
    if (match) { path = appendJsonPathKey(parent?.path || '$', JSON.parse(`"${match[1]}"`)); value = match[2] }
    else if (parent?.type === 'array') { path = `${parent.path}[${parent.index++}]` }
    linePaths.set(index + 1, path)
    if (/^[{\[]/.test(value)) stack.push({ type: value.startsWith('[') ? 'array' : 'object', path, index: 0 })
  })
  return linePaths
}
function jsonPathValue(json, expression) {
  if (!expression.startsWith('$')) throw new Error('JSONPath')
  let value = json
  let remaining = expression.slice(1)
  while (remaining) {
    const match = remaining.match(/^(?:\.([A-Za-z_$][\w$]*)|\[(\d+)\]|\[("(?:\\.|[^"\\])*")\])/)
    if (!match) throw new Error('JSONPath')
    const key = match[1] ?? (match[2] === undefined ? JSON.parse(match[3]) : Number(match[2]))
    if (value == null || !Object.hasOwn(Object(value), key)) throw new Error('JSONPath')
    value = value[key]
    remaining = remaining.slice(match[0].length)
  }
  return value
}
const responseJsonPathForLine = line => buildJsonPathByLine(responseEditor?.getValue() ?? responseBody.value).get(line) || ''
function responseValueForLine(line) {
  try { return formatValue(jsonPathValue(JSON.parse(responseBody.value), responseJsonPathForLine(line))) ?? null } catch { return null }
}
async function copyTextToClipboard(text, successMessage = t('apiTesting.interface.clipboardCopied')) {
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
    else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      try { textarea.select(); if (!document.execCommand('copy')) throw new Error('copy') } finally { textarea.remove() }
    }
    ElMessage.success(successMessage)
  } catch { ElMessage.error(t('apiTesting.interface.copyFailed')) }
}
function createResponseEditor() {
  if (!responseEditorHost.value || responseEditor || unmounted) return
  responseEditor = monaco.editor.create(responseEditorHost.value, {
    value: responseBody.value, language: 'json', theme: 'vs', readOnly: true, automaticLayout: true,
    minimap: { enabled: false }, folding: true, showFoldingControls: 'mouseover', lineNumbers: 'on', scrollBeyondLastLine: false,
    wordWrap: 'on', wrappingStrategy: 'advanced', scrollbar: { horizontal: 'hidden', horizontalScrollbarSize: 0 },
    // 关闭括号对颜色化，使 JSON 的括号保持原始灰黑色显示。
    bracketPairColorization: { enabled: false },
    contextmenu: true, fontSize: 13, lineHeight: 20, padding: { top: 12, bottom: 12 }
  })
  responseEditor.addAction({ id: 'copy-response-value', label: t('apiTesting.interface.copyValue'), contextMenuGroupId: 'navigation', contextMenuOrder: -2,
    run: async editor => {
      const selection = editor.getSelection()
      const value = selection && !selection.isEmpty() ? editor.getModel()?.getValueInRange(selection) : responseValueForLine(editor.getPosition()?.lineNumber || 1)
      if (value == null) { ElMessage.warning(t('apiTesting.interface.valueUnavailable')); return }
      await copyTextToClipboard(value, t('apiTesting.interface.valueCopied'))
    } })
  responseEditor.addAction({ id: 'copy-response-json-path', label: t('apiTesting.interface.copyJsonPath'), contextMenuGroupId: 'navigation', contextMenuOrder: -1,
    run: async editor => {
      const path = responseJsonPathForLine(editor.getPosition()?.lineNumber || 1)
      if (!path) { ElMessage.warning(t('apiTesting.interface.jsonPathUnavailable')); return }
      await copyTextToClipboard(path, t('apiTesting.interface.jsonPathCopied'))
    } })
}
function disposeResponseEditor() { responseEditor?.getModel()?.dispose(); responseEditor?.dispose(); responseEditor = null }
watch(() => props.response, () => {
  formattedBody.value = null
  jsonPathResult.value = null
  jsonPathExpression.value = ''
  showJsonPathExtractor.value = false
}, { flush: 'sync' })
watch([responseBody, responseEditorHost], async () => {
  await nextTick()
  if (unmounted) return
  if (!props.response || !responseEditorHost.value) { disposeResponseEditor(); return }
  createResponseEditor()
  if (responseEditor.getValue() !== responseBody.value) responseEditor.setValue(responseBody.value)
}, { immediate: true })
function formatResponse() {
  try { formattedBody.value = JSON.stringify(JSON.parse(responseBody.value), null, 2); ElMessage.success(t('apiTesting.interface.formatSucceeded')) }
  catch { ElMessage.error(t('apiTesting.interface.formatFailed')) }
}
const copyResponse = () => copyTextToClipboard(responseEditor?.getValue() ?? responseBody.value)
function evaluateJsonPath() {
  if (!jsonPathExpression.value) { jsonPathResult.value = null; return }
  try { jsonPathResult.value = JSON.stringify(jsonPathValue(JSON.parse(responseBody.value), jsonPathExpression.value), null, 2) }
  catch { jsonPathResult.value = t('apiTesting.interface.jsonPathError') }
}
const copyJsonPathResult = () => { if (jsonPathResult.value !== null) return copyTextToClipboard(jsonPathResult.value) }
onBeforeUnmount(() => { unmounted = true; disposeResponseEditor() })
</script>

<style scoped>
.response-section { min-width: 0; }
.response-section.is-fill-height { display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0; }
.assertion-result-header { display: flex; align-items: center; gap: 12px; }
/* 两层 tab 与标题之间保持紧凑，避免大块空白 */
.result-tabs :deep(.el-tabs__header) { margin: 0 0 6px; }
.result-tabs :deep(.el-tabs__item) { height: 32px; line-height: 32px; }
.nested-tabs :deep(.el-tabs__header) { margin: 0 0 8px; }
.nested-tabs :deep(.el-tabs__item) { height: 30px; line-height: 30px; }
.response-content { height: 400px; min-height: 240px; border: 1px solid #ebeef5; }
.response-section.is-fill-height .result-tabs,
.response-section.is-fill-height .nested-tabs,
.response-section.is-fill-height .response-body { display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0; }
.response-section.is-fill-height :deep(.result-tabs > .el-tabs__content),
.response-section.is-fill-height :deep(.nested-tabs > .el-tabs__content) { display: flex; flex: 1 1 auto; min-height: 0; overflow: hidden; }
.response-section.is-fill-height :deep(.result-tabs > .el-tabs__content > .el-tab-pane),
.response-section.is-fill-height :deep(.nested-tabs > .el-tabs__content > .el-tab-pane) { display: flex; flex: 1 1 auto; flex-direction: column; width: 100%; height: 100%; min-height: 0; }
.response-section.is-fill-height .response-content { flex: 1 1 auto; width: 100%; height: auto; min-height: 0; }
.response-actions { margin-bottom: 12px; }
.jsonpath-extractor { margin-bottom: 12px; }
.jsonpath-result, .request-content { white-space: pre-wrap; overflow-wrap: anywhere; }
.header-row { padding: 8px 0; overflow-wrap: anywhere; }
/* 请求方法与接口树保持同一套标签样式 */
.request-line { display: flex; align-items: center; gap: 8px; }
.method-tag {
  font-size: 10px; padding: 2px 8px; border-radius: 10px; color: white;
  font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px;
  display: inline-block; min-width: 40px; text-align: center; flex-shrink: 0;
}
.method-tag.get { background-color: #61affe; }
.method-tag.post { background-color: #49cc90; }
.method-tag.put { background-color: #fca130; }
.method-tag.delete { background-color: #f93e3e; }
.method-tag.patch { background-color: #50e3c2; }
.method-tag.head { background-color: #9013fe; }
.method-tag.options { background-color: #0ebeff; }
.method-tag.connect { background-color: #7f8c8d; }
.assertion-result-item { padding: 12px; margin-bottom: 12px; border-left: 3px solid #f56c6c; background: #fef0f0; }
.assertion-result-item.passed { border-color: #67c23a; background: #f0f9eb; }
.result-row { margin-top: 8px; white-space: pre-wrap; }
.error { color: #f56c6c; }
.empty-tip { color: #909399; padding: 12px 0; }
</style>
