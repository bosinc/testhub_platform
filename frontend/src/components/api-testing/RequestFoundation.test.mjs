// Run: node --test frontend/src/components/api-testing/RequestFoundation.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, parse } from '@vue/compiler-sfc'
import { transform } from 'esbuild'
import { createRenderer, defineComponent, h, nextTick, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { ElInput, ElInputNumber, ID_INJECTION_KEY } from 'element-plus'
import { BODY_TYPES, REQUEST_FIELDS, canonicalRows, cloneDraft, createRequestDraft, curlModelToDraft, previewRequestUrl, serializeRequestDraft, validateBaseUrl } from '../../utils/requestDraft.js'
import zh from '../../locales/lang/zh-cn/api-testing.js'
import en from '../../locales/lang/en/api-testing.js'

globalThis.document = { activeElement: null, addEventListener() {}, removeEventListener() {} }
const moduleUrl = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
const uiMessages = []
const monacoInstances = []
globalThis.__foundationMessages = uiMessages
globalThis.__foundationMonaco = monacoInstances
const messageModule = moduleUrl(`export const ElMessage = Object.fromEntries(['success','error','warning'].map(type => [type, message => globalThis.__foundationMessages.push({type,message})])); export const ElMessageBox = { confirm: async () => {}, alert: async () => {} }`)
const apiModule = moduleUrl(`export default Object.fromEntries(['get','post','put','patch','delete','request'].map(method => [method, (...args) => globalThis.__foundationApi(method, ...args)]))`)
const monacoModule = moduleUrl(`export const editor = { create(host, options) { const instance = { host, value: options.value, options, actions: [], disposed: false, modelDisposed: false, getValue() { return this.value }, setValue(value) { this.value = value }, addAction(action) { this.actions.push(action) }, getModel() { return { dispose: () => { this.modelDisposed = true }, getValueInRange: () => 'selected' } }, dispose() { this.disposed = true } }; globalThis.__foundationMonaco.push(instance); return instance } }`)
const emptyModule = moduleUrl('export default { render: () => null }')
const replacements = {
  'element-plus': messageModule,
  '@/utils/api': apiModule,
  '@/components/DataFactorySelector.vue': emptyModule,
  '@/components/ResizableSplitPane.vue': moduleUrl(`import {h} from 'vue'; export default { setup: (_, {slots}) => () => h('div', [slots.first?.(), slots.second?.()]) }`.replace("'vue'", JSON.stringify(import.meta.resolve('vue')))),
  '@/api/data-factory': moduleUrl('export const getVariableFunctions = async () => ({data: []})'),
  '@/utils/projectSelection': moduleUrl('export const useRememberedProject = () => {}; export const getPreferredProjectId = (projects, fallback) => fallback'),
  '@/utils/assertions': new URL('../../utils/assertions.js', import.meta.url).href,
  '@/utils/requestDraft': new URL('../../utils/requestDraft.js', import.meta.url).href,
  'monaco-editor/esm/vs/editor/editor.api': monacoModule
}
const cache = new Map()
async function compileFile(relative, expose = '') {
  const url = new URL(relative, import.meta.url)
  if (cache.has(url.href)) return cache.get(url.href)
  let code = await readFile(url, 'utf8')
  if (url.pathname.endsWith('.vue')) {
    if (expose) code = code.replace('</script>', `\ndefineExpose({ ${expose} })\n</script>`)
    const { descriptor, errors } = parse(code, { filename: url.pathname })
    assert.deepEqual(errors, [])
    code = compileScript(descriptor, { id: url.pathname, inlineTemplate: true }).content
  } else if (url.pathname.endsWith('.ts')) code = (await transform(code, { loader: 'ts', format: 'esm' })).code
  code = code.replace(/^import ['"]monaco-editor\/[^'"]+['"];?$/gm, '')
  const imports = [...code.matchAll(/from (['"])([^'"]+)\1/g)]
  for (const match of imports) {
    const specifier = match[2]
    let target = replacements[specifier]
    if (!target && (specifier.startsWith('.') || specifier.startsWith('@/'))) {
      const file = specifier.startsWith('@/') ? new URL(`../../${specifier.slice(2)}`, import.meta.url) : new URL(specifier, url)
      if (!/\.(vue|js|ts)$/.test(file.pathname)) file.pathname += '.ts'
      target = /\.(vue|ts)$/.test(file.pathname) ? await compileFile(file.href) : file.href
    }
    target ||= import.meta.resolve(specifier)
    code = code.replace(match[0], `from ${JSON.stringify(target)}`)
  }
  const result = moduleUrl(code)
  cache.set(url.href, result)
  return result
}
const { default: RequestEditor } = await import(await compileFile('./RequestEditor.vue'))
const { default: ResponseViewer } = await import(await compileFile('./ResponseViewer.vue'))
const { default: KeyValueEditor } = await import(await compileFile('../../views/api-testing/components/KeyValueEditor.vue'))
const { default: AssertionEditor } = await import(await compileFile('./AssertionEditor.vue'))
const { default: VariableExtractorEditor } = await import(await compileFile('./VariableExtractorEditor.vue'))
const { RequestModelParser } = await import(await compileFile('../../utils/requestModel.ts'))
const { CodeGenerator } = await import(await compileFile('../../utils/codeGenerator.ts'))
const { default: InterfaceManagement } = await import(await compileFile('../../views/api-testing/InterfaceManagement.vue', 'selectRequest, requestDraft, selectedRequest, selectedEnvironment, environments, selectedProject, sendRequest, saveRequest, getBackendPreview, toggleWebSocketConnection, disconnectWebSocket, websocketConnectionStatus, websocketMessageType, websocketMessageContent, websocketBinaryFile, sendWebSocketMessage, createEmptyRequest, curlPreview, applyCurlPreview'))
const { default: EnvironmentManagement } = await import(await compileFile('../../views/api-testing/EnvironmentManagement.vue', 'form, editEnvironment, openCreateDialog, resetForm, duplicateEnvironment, submitForm'))

function memoryNode(tag, text = '') {
  return { tag, text, props: {}, style: {}, children: [], parent: null, value: '',
    setAttribute(key, value) { this.props[key] = value }, removeAttribute(key) { delete this.props[key] },
    addEventListener() {}, removeEventListener() {}, setSelectionRange() {}, focus() {} }
}
const renderer = createRenderer({
  createElement: memoryNode, createText: text => memoryNode('#text', text), createComment: text => memoryNode('#comment', text),
  setText: (node, text) => { node.text = text }, setElementText: (node, text) => { node.text = text; node.children = [] },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  patchProp: (node, key, previous, value) => { node.props[key] = value; if (key === 'value') node.value = value },
  insert(node, parent, anchor = null) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, node); node.parent = parent },
  remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = null }
})
const ModelControl = defineComponent({
  props: ['modelValue'], emits: ['update:modelValue', 'change'],
  setup(props, { attrs, emit, slots }) {
    return () => h('select', { ...attrs, value: props.modelValue, onChange: event => { emit('update:modelValue', event.target.value); emit('change', event.target.value) } }, slots.default?.())
  }
})
const Option = defineComponent({ props: ['label', 'value'], setup: props => () => h('option', { value: props.value }, props.label) })
const Pass = (_, { attrs, slots }) => h('div', attrs, slots.default?.())
const Button = (_, { attrs, slots }) => h('button', attrs, slots.default?.())
const Hidden = () => h('div')
const Dialog = defineComponent({ props: ['modelValue'], setup: (props, { slots }) => () => props.modelValue ? h('div', [slots.default?.(), slots.footer?.()]) : null })
const Form = defineComponent({ setup(_, { slots, expose }) { expose({ validate: async () => true, resetFields() {} }); return () => h('form', slots.default?.()) } })
const descendants = (node, predicate) => [...(predicate(node) ? [node] : []), ...node.children.flatMap(child => descendants(child, predicate))]
function vnodes(vnode, type) {
  if (!vnode) return []
  return [...(vnode.type === type ? [vnode] : []), ...(vnode.component ? vnodes(vnode.component.subTree, type) : Array.isArray(vnode.children) ? vnode.children.flatMap(child => vnodes(child, type)) : [])]
}
async function flush() { await nextTick(); await nextTick(); await nextTick() }
async function mount(t, render, locale = 'zh-cn') {
  const root = memoryNode('root')
  const app = renderer.createApp({ setup: () => render })
  const warnings = []
  app.config.warnHandler = message => warnings.push(message)
  app.provide(ID_INJECTION_KEY, { prefix: 1, current: 0 })
  app.use(createI18n({ legacy: false, locale, missingWarn: false, fallbackWarn: false, messages: { 'zh-cn': { apiTesting: zh }, en: { apiTesting: en } } }))
  for (const [name, implementation] of Object.entries({
    ElInput, ElInputNumber, ElSelect: ModelControl, ElOption: Option, ElSwitch: ModelControl, ElRadioGroup: ModelControl,
    ElRadio: Pass, ElRadioButton: Pass, ElCheckbox: ModelControl, ElCheckboxGroup: Pass,
    ElButton: Button, ElButtonGroup: Pass, ElIcon: Pass, ElTooltip: Pass, ElTabs: Pass, ElTabPane: Pass,
    ElEmpty: Hidden, ElUpload: Pass, ElDialog: Dialog, ElForm: Form, ElFormItem: Pass, ElText: Pass,
    ElTree: Hidden, ElTable: Hidden, ElTableColumn: Hidden, ElTag: Pass, ElDropdown: Pass, ElDropdownMenu: Pass, ElDropdownItem: Pass
  })) app.component(name, implementation)
  app.directive('loading', {})
  app.mount(root)
  t.after(() => app.unmount())
  await flush()
  return { root, warnings, app, components: type => vnodes(root._vnode, type) }
}
const row = (key, value, enabled = true, type = 'text') => ({ key, value, enabled, type, description: `description:${key}` })
const fullDraft = () => createRequestDraft({
  method: 'POST', url: '/users/{id}', headers: [row('X-A', 'one'), row('X-A', 'two', false)],
  params: [row('tag', 'one'), row('tag', 'two'), row('ignored', false, false, 'boolean'), row('count', 0, true, 'number')],
  path_params: [row('id', 'a/b'), row('unused', 0, false, 'number')], body: { type: 'json', data: { valid: false, count: 0 } },
  auth: { type: 'basic', username: 'user', password: 'p:a:ss' }, pre_request_script: 'before()', post_request_script: 'after()',
  assertions: [{ name: 'codes', type: 'status_code', operator: 'one_of', expected: [200, 201] }],
  extractors: [{ name: 'token', type: 'json_path', source: 'body', expression: '$.token', enabled: false }],
  settings: { timeout: 12, follow_redirects: false, verify_ssl: true }
})
const environment = { id: 9, base_url: 'https://test.example.com/api/v1' }

test('完整DTO保存与发送保留有序条目、禁用项、重复Query、类型及全部字段', async () => {
  const original = fullDraft()
  const output = await serializeRequestDraft(original)
  assert.deepEqual(output, original)
  assert.deepEqual(Object.keys(output), REQUEST_FIELDS)
  output.params[0].value = 'changed'
  assert.equal(original.params[0].value, 'one')
  assert.throws(() => createRequestDraft({ params: { tag: 'one' } }), /rowsInvalid/)
  assert.equal(canonicalRows([row('null', null, false, 'json')])[0].value, null)
})
for (const type of BODY_TYPES) test(`body ${type}使用canonical结构且不按HTTP方法丢失`, async () => {
  const file = { filename: 'data.bin', content_base64: 'AAH/', content_type: 'application/octet-stream' }
  const data = type === 'json' ? false : type === 'raw' ? 'raw text' : type === 'binary' ? file : [row('x', 0, false, 'number')]
  const draft = createRequestDraft({ method: 'GET', body: { type, data } })
  assert.deepEqual((await serializeRequestDraft(draft)).body, { type, data })
})
test('表单与binary文件只序列化字节，不发送JSFile、本机路径或丢失禁用项', async () => {
  const file = new Blob([new Uint8Array([0, 1, 255])], { type: 'application/test' })
  Object.defineProperty(file, 'name', { value: 'sample.bin' })
  const expected = { filename: 'sample.bin', content_base64: 'AAH/', content_type: 'application/test' }
  const output = await serializeRequestDraft(createRequestDraft({ body: { type: 'form-data', data: [row('upload', file, false, 'file')] } }))
  assert.deepEqual(output.body.data[0].value, expected)
  assert.deepEqual(JSON.parse(JSON.stringify(output)).body.data[0].value, expected)
  const binary = await serializeRequestDraft(createRequestDraft({ body: { type: 'binary', data: file } }))
  assert.deepEqual(binary.body.data, expected)
  await assert.rejects(serializeRequestDraft(createRequestDraft({ body: { type: 'form-data', data: [row('upload', '/Users/private/file', true, 'file')] } })), /fileRequired/)
  await assert.rejects(serializeRequestDraft(createRequestDraft({ body: { type: 'json', data: '{invalid' } })), /jsonInvalid/)
})
test('URL参考预览保留环境基础路径、编码Path、重复Query及WS映射', () => {
  const draft = fullDraft()
  assert.equal(previewRequestUrl(draft, environment), 'https://test.example.com/api/v1/users/a%2Fb?tag=one&tag=two&count=0')
  draft.request_type = 'WEBSOCKET'
  assert.match(previewRequestUrl(draft, environment), /^wss:\/\/test.example.com\/api\/v1/)
  for (const url of ['https://other.test/x', '//other.test', '/../x', '/%2e%2e/x', '/%', '/x?q=1']) assert.equal(previewRequestUrl({ ...draft, url }, environment), '')
  for (const base of ['', 'ftp://test.test', 'https://user:pass@test.test', 'https://test.test?q=1', 'https://test.test#x', 'https://{{host}}']) assert.equal(validateBaseUrl(base), false)
  assert.equal(validateBaseUrl('', true), true)
})
test('真实cURL导入保留相对path、Path/Query同名条目和host建议，不覆盖环境', async () => {
  const model = await RequestModelParser.parseCurl("curl 'https://test.example.com/v1/users/{id}?id=q&tag=a&tag=b' -H 'Content-Type: application/json' --data '{\"valid\":false}'")
  const draft = curlModelToDraft(model)
  assert.equal(draft.url, '/v1/users/{id}')
  assert.deepEqual(draft.params.map(item => [item.key, item.value]), [['id', 'q'], ['tag', 'a'], ['tag', 'b']])
  assert.equal(draft.path_params[0].key, 'id')
  assert.equal(model.baseURL, 'https://test.example.com')
  assert.deepEqual((await serializeRequestDraft(draft)).body.data, { valid: false })
  assert.equal(environment.base_url, 'https://test.example.com/api/v1')
})
test('cURL和代码生成共用完整Body/认证及shell引号转义', async () => {
  const model = { method: 'POST', baseURL: 'https://test.example.com', path: '/x', query: [row('tag', 'a'), row('tag', 'b')], headers: [row('X-A', "it's")], body: { mode: 'urlencoded', urlencoded: [row('x', 'a&b'), row('x', 'second')] }, auth: { type: 'bearer', token: 'token' }, timeout: 1000 }
  const curl = RequestModelParser.toCurl(model)
  assert.match(curl, /tag=a&tag=b/)
  assert.match(curl, /--data-urlencode 'x=a&b'/)
  assert.match(curl, /--data-urlencode 'x=second'/)
  assert.ok(curl.includes("it'\\''s"))
  assert.match(curl, /Authorization: Bearer token/)
  assert.equal(await CodeGenerator.generateCode(model, 'curl'), curl)
  assert.match(await CodeGenerator.generateCode(model, 'python'), /requests/)
})

test('RequestEditor真实嵌套编辑只emit新DTO，不修改prop，并保留集合断言', async t => {
  const original = fullDraft()
  const model = ref(cloneDraft(original))
  const events = []
  const mounted = await mount(t, () => h(RequestEditor, { modelValue: model.value, environment, 'onUpdate:modelValue': value => { events.push(value); model.value = value } }))
  assert.equal(events.length, 0, '打开不得改变草稿')
  const query = mounted.components(KeyValueEditor)[0]
  const nextRows = canonicalRows([...original.params, row('new', true, false, 'boolean')])
  query.props['onUpdate:modelValue'](nextRows)
  await flush()
  assert.deepEqual(model.value.params, nextRows)
  assert.notEqual(events[0], original)
  assert.deepEqual(original, fullDraft())
  const assertions = mounted.components(AssertionEditor)[0]
  assertions.props.modelValue[0].name = 'renamed'
  await flush()
  assert.equal(model.value.assertions[0].name, 'renamed')
  assert.deepEqual(model.value.assertions[0].expected, [200, 201])
  assert.equal(original.assertions[0].name, 'codes')
  const extractor = mounted.components(VariableExtractorEditor)[0]
  extractor.props.modelValue[0].expression = '$.updated'
  await flush()
  assert.equal(model.value.extractors[0].expression, '$.updated')
  assert.equal(original.extractors[0].expression, '$.token')
  assert.deepEqual(mounted.warnings, [])
})
test('RequestEditor实际Auth/Settings/脚本/JSON控件更新完整DTO，Path不挪走同名Query', async t => {
  const model = ref(fullDraft())
  const mounted = await mount(t, () => h(RequestEditor, { modelValue: model.value, 'onUpdate:modelValue': value => { model.value = value } }))
  mounted.components(ElInput).find(node => node.props.modelValue === 'p:a:ss').props['onUpdate:modelValue']('new:password')
  await flush()
  mounted.components(ElInputNumber)[0].props['onUpdate:modelValue'](25)
  mounted.components(ElInput).find(node => node.props.modelValue === 'before()').props['onUpdate:modelValue']('newBefore()')
  await flush()
  mounted.components(ElInput).find(node => node.props.modelValue === '/users/{id}').props['onUpdate:modelValue']('/users/{id}/{extra}')
  await flush()
  assert.equal(model.value.auth.password, 'new:password')
  assert.equal(model.value.settings.timeout, 25)
  assert.equal(model.value.pre_request_script, 'newBefore()')
  assert.deepEqual(model.value.params, fullDraft().params)
  assert.deepEqual(model.value.path_params.at(-1), { key: 'extra', value: '', enabled: true, type: 'text', description: '' })
  assert.deepEqual((await serializeRequestDraft(model.value)).assertions[0].expected, [200, 201])
})
test('RequestEditor异步文件读取在切换草稿或卸载后不能emit旧数据', async t => {
  const model = ref(createRequestDraft({ url: '/first', body: { type: 'binary', data: null } }))
  const emitted = []
  const mounted = await mount(t, () => h(RequestEditor, { modelValue: model.value, 'onUpdate:modelValue': value => emitted.push(value) }))
  let resolveFile
  const upload = descendants(mounted.root, node => node.tag === 'div' && node.props['on-change'] && node.props['auto-upload'] === false)[0]
  assert.ok(upload)
  const pending = upload.props['on-change']({ raw: { name: 'old.bin', type: 'application/octet-stream', arrayBuffer: () => new Promise(resolve => { resolveFile = resolve }) } })
  model.value = createRequestDraft({ url: '/second' })
  await flush()
  mounted.app.unmount()
  resolveFile(new Uint8Array([1, 2]).buffer)
  await pending
  assert.deepEqual(emitted, [])
  assert.equal(model.value.url, '/second')
})
test('RequestEditor切换请求不串Body/Auth/脚本/设置并限制协议选择', async t => {
  const model = ref(fullDraft())
  const editor = ref(null)
  const mounted = await mount(t, () => h(RequestEditor, { ref: editor, modelValue: model.value, allowedProtocols: ['HTTP'], 'onUpdate:modelValue': value => { model.value = value } }))
  editor.value.insertText('pre_request_script', ' appended')
  await flush()
  assert.equal(model.value.pre_request_script, 'before() appended')
  model.value = createRequestDraft({ url: '/second' })
  await flush()
  assert.deepEqual(model.value.auth, { type: 'none' })
  assert.deepEqual(model.value.settings, {})
  assert.equal(model.value.body.data, '')
  editor.value.insertText('rawBody', 'second body')
  await flush()
  assert.equal(model.value.body.data, 'second body')
  assert.equal(model.value.pre_request_script, '')
  assert.equal(descendants(mounted.root, node => String(node.props.class).includes('protocol-select')).length, 0)
})
test('RequestEditor send/WS/辅助按钮只发事件，busy正确传递', async t => {
  const model = ref(fullDraft())
  const status = ref('disconnected')
  const events = []
  const mounted = await mount(t, () => h(RequestEditor, { modelValue: model.value, busy: true, wsStatus: status.value,
    onSend: () => events.push('send'), onWsConnect: () => events.push('connect'), onWsDisconnect: () => events.push('disconnect'),
    onWsSend: () => events.push('wsSend'),
    onDataFactory: value => events.push(value), 'onUpdate:modelValue': value => { model.value = value } }, { 'ws-message': ({ send }) => h('button', { onClick: send }, 'ws-message-send') }))
  let buttons = descendants(mounted.root, node => node.tag === 'button')
  const send = buttons.find(node => node.props.loading)
  assert.ok(send)
  send.props.onClick()
  assert.deepEqual(events, ['send'])
  const helper = buttons.find(node => descendants(node, child => child.text === zh.interface.referDataFactory).length)
  helper.props.onClick()
  assert.deepEqual(events[1], { field: 'rawBody' })
  model.value = { ...model.value, request_type: 'WEBSOCKET' }
  await flush()
  buttons = descendants(mounted.root, node => node.tag === 'button')
  buttons.find(node => node.props.disabled === true && node.props.loading === false).props.onClick()
  status.value = 'connected'
  await flush()
  descendants(mounted.root, node => node.tag === 'button').find(node => node.props.disabled === true && node.props.loading === false).props.onClick()
  assert.deepEqual(events.slice(-2), ['connect', 'disconnect'])
  descendants(mounted.root, node => node.tag === 'button' && descendants(node, child => child.text === 'ws-message-send').length)[0].props.onClick()
  assert.equal(events.at(-1), 'wsSend')
})
test('KeyValueEditor上传真实字节并保留禁用、重复、零值和类型', async t => {
  const model = ref([row('x', 0, false, 'number'), row('x', false, true, 'boolean'), row('file', '', true, 'file')])
  const mounted = await mount(t, () => h(KeyValueEditor, { modelValue: model.value, showFile: true, 'onUpdate:modelValue': value => { model.value = value } }))
  const upload = descendants(mounted.root, node => node.props.onChange && node.tag === 'div').find(node => node.props['auto-upload'] === false)
  assert.ok(upload)
  const file = new Blob(['abc'], { type: 'text/plain' })
  Object.defineProperty(file, 'name', { value: 'file.txt' })
  await upload.props.onChange({ raw: file, name: 'file.txt' })
  await flush()
  assert.deepEqual(model.value[0], row('x', 0, false, 'number'))
  assert.equal(model.value[1].value, false)
  assert.deepEqual(model.value[2].value, { filename: 'file.txt', content_base64: 'YWJj', content_type: 'text/plain' })
})

test('ResponseViewer迁出Monaco、格式化、JSONPath和复制，支持false及卸载释放', async t => {
  const response = ref({ status_code: 200, response_time: 1.5, response_data: { body: '{"a.b":[false,0]}' }, request_data: { method: 'GET', url: 'https://test.example.com/x', body: false, headers: {} }, assertions_results: [{ name: 'codes', passed: true, expected: [200,201], actual: 200 }] })
  const original = cloneDraft(response.value)
  const copied = []
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async value => copied.push(value) } } })
  const mounted = await mount(t, () => h(ResponseViewer, { response: response.value }))
  const instance = monacoInstances.at(-1)
  assert.equal(instance.options.readOnly, true)
  assert.equal(instance.actions.length, 2)
  const buttons = descendants(mounted.root, node => node.tag === 'button')
  buttons[0].props.onClick()
  await flush()
  assert.equal(instance.value, JSON.stringify({ 'a.b': [false, 0] }, null, 2))
  await instance.actions[0].run({ getSelection: () => null, getPosition: () => ({ lineNumber: 3 }) })
  await instance.actions[1].run({ getPosition: () => ({ lineNumber: 3 }) })
  assert.deepEqual(copied, ['false', '$["a.b"][0]'])
  buttons[2].props.onClick()
  await flush()
  const input = mounted.components(ElInput)[0]
  input.props['onUpdate:modelValue']('$["a.b"][1]')
  input.props.onInput()
  await flush()
  assert.ok(descendants(mounted.root, node => node.text === '0').length)
  assert.deepEqual(response.value, original, '格式化不得修改响应prop')
  response.value = null
  await flush()
  assert.equal(instance.disposed, true)
  assert.equal(instance.modelDisposed, true)
  response.value = { response_data: { json: false } }
  await flush()
  assert.equal(monacoInstances.at(-1).value, 'false')
  mounted.app.unmount()
  assert.equal(monacoInstances.at(-1).disposed, true)
})

async function mountPage(t) {
  const calls = []
  globalThis.__foundationApi = async (method, url, data) => {
    calls.push({ method, url, data: cloneDraft(data) })
    if (method === 'get') return { data: [] }
    if (url.endsWith('/preview-url/')) return { data: { url: 'wss://confirmed.example.com/api/socket?tag=a&tag=b' } }
    if (url.endsWith('/execute/')) return { data: { status_code: 200, request_data: data, response_data: { json: false } } }
    return { data: { ...data, id: 101 } }
  }
  const page = ref(null)
  const mounted = await mount(t, () => h(InterfaceManagement, { ref: page }))
  await flush()
  page.value.selectedProject = 7
  page.value.environments = [environment]
  return { ...mounted, page: page.value, calls }
}
test('实际页面save/execute/preview发送完整canonical DTO，缺环境不send', async t => {
  const { page, calls, components } = await mountPage(t)
  page.selectRequest({ ...fullDraft(), name: 'test', id: 1, collection: 2 })
  await flush()
  assert.equal(components(RequestEditor).length, 1)
  await page.sendRequest()
  assert.equal(calls.filter(call => call.url.endsWith('/execute/')).length, 0)
  page.selectedEnvironment = 9
  assert.equal(await page.saveRequest(), true)
  await page.sendRequest()
  await page.getBackendPreview()
  const save = calls.find(call => call.method === 'put')
  const execute = calls.find(call => call.url.endsWith('/execute/'))
  const preview = calls.find(call => call.url.endsWith('/preview-url/'))
  for (const field of REQUEST_FIELDS) { assert.deepEqual(save.data[field], fullDraft()[field]); assert.deepEqual(execute.data[field], fullDraft()[field]); assert.deepEqual(preview.data[field], fullDraft()[field]) }
  assert.equal(execute.data.environment_id, 9)
  assert.equal(preview.data.environment_id, 9)
  await flush()
  assert.equal(components(ResponseViewer).length, 1)
})
test('WebSocket必须保存后backend预览才连接，JSON stringify、binary和卸载close', async t => {
  const sockets = []
  globalThis.WebSocket = class { constructor(url) { this.url = url; this.sent = []; this.closed = false; sockets.push(this) } send(value) { this.sent.push(value) } close() { this.closed = true } }
  const { page, calls, app } = await mountPage(t)
  page.selectRequest({ ...fullDraft(), request_type: 'WEBSOCKET', id: null })
  page.selectedEnvironment = 9
  await page.toggleWebSocketConnection()
  assert.equal(sockets.length, 0)
  assert.equal(calls.filter(call => call.url.endsWith('/preview-url/')).length, 0)
  page.selectedRequest.id = 5
  await page.toggleWebSocketConnection()
  assert.equal(sockets[0].url, 'wss://confirmed.example.com/api/socket?tag=a&tag=b')
  sockets[0].onopen()
  page.websocketMessageType = 'json'
  page.websocketMessageContent = '{"valid":false}'
  page.sendWebSocketMessage()
  assert.equal(sockets[0].sent[0], '{"valid":false}')
  page.websocketMessageType = 'binary'
  const file = new Blob(['binary'])
  page.websocketBinaryFile = file
  page.sendWebSocketMessage()
  assert.equal(sockets[0].sent[1], file)
  app.unmount()
  assert.equal(sockets[0].closed, true)
})
test('延迟保存或WS预览不能污染切换后的请求', async t => {
  const { page } = await mountPage(t)
  page.selectRequest({ ...fullDraft(), id: 1, name: 'first' })
  page.selectedEnvironment = 9
  let resolveSave
  globalThis.__foundationApi = async () => new Promise(resolve => { resolveSave = resolve })
  const save = page.saveRequest()
  await flush()
  page.selectRequest({ ...createRequestDraft({ url: '/second' }), name: 'second', id: 2 })
  resolveSave({ data: { ...fullDraft(), id: 1 } })
  await save
  assert.equal(page.selectedRequest.id, 2)
  assert.equal(page.requestDraft.url, '/second')
})
test('WS后端预览失败或切换请求时不创建浏览器连接', async t => {
  const sockets = []
  globalThis.WebSocket = class { constructor(url) { sockets.push(url) } close() {} }
  const { page } = await mountPage(t)
  page.selectRequest({ ...fullDraft(), id: 1, request_type: 'WEBSOCKET' })
  page.selectedEnvironment = 9
  await flush()
  let resolvePreview
  globalThis.__foundationApi = async () => new Promise(resolve => { resolvePreview = resolve })
  const connecting = page.toggleWebSocketConnection()
  await flush()
  page.selectRequest({ ...createRequestDraft({ url: '/second' }), id: 2 })
  resolvePreview({ data: { url: 'wss://confirmed.example.com/socket' } })
  await connecting
  assert.deepEqual(sockets, [])
  page.selectRequest({ ...fullDraft(), id: 3, request_type: 'WEBSOCKET' })
  await flush()
  globalThis.__foundationApi = async () => { throw new Error('backend rejected') }
  await page.toggleWebSocketConnection()
  assert.deepEqual(sockets, [])
  assert.equal(page.websocketConnectionStatus, 'disconnected')
})
test('英文编辑器显示后端确认和执行环境提示', async t => {
  const mounted = await mount(t, () => h(RequestEditor, { modelValue: fullDraft() }), 'en')
  assert.ok(descendants(mounted.root, node => node.text === en.requestEditor.previewHint).length)
  assert.ok(descendants(mounted.root, node => node.text === en.requestEditor.environmentRequired).length)
})
test('环境base_url在编辑、保存、复制、重置、新建全路径同步', async t => {
  const calls = []
  globalThis.__foundationApi = async (method, url, data) => { calls.push({ method, url, data: cloneDraft(data) }); return { data: method === 'get' ? [] : data } }
  const page = ref(null)
  await mount(t, () => h(EnvironmentManagement, { ref: page }))
  page.value.editEnvironment({ ...environment, name: 'environment', scope: 'GLOBAL', project: null, variables: { token: 'abc' } })
  await flush()
  assert.equal(page.value.form.base_url, environment.base_url)
  page.value.form.base_url = 'https://edited.example.com/v2'
  await page.value.submitForm()
  assert.equal(calls.find(call => call.method === 'put').data.base_url, 'https://edited.example.com/v2')
  await page.value.duplicateEnvironment({ ...environment, name: 'copy', scope: 'GLOBAL', variables: {} })
  assert.equal(calls.find(call => call.method === 'post').data.base_url, environment.base_url)
  page.value.resetForm()
  assert.equal(page.value.form.base_url, '')
  assert.equal(page.value.form.variables[0].key, '')
  page.value.form.base_url = 'https://temporary.example.com'
  page.value.openCreateDialog()
  assert.equal(page.value.form.base_url, '')
})
