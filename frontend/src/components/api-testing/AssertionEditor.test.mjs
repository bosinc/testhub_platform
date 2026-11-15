// 运行：node --test frontend/src/components/api-testing/AssertionEditor.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, defineComponent, h, nextTick, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { ElInput, ElInputNumber, ID_INJECTION_KEY } from 'element-plus'
import { normalizeAssertions, stripLegacyAssertionValue } from '../../utils/assertions.js'
import zhCn from '../../locales/lang/zh-cn/api-testing.js'
import en from '../../locales/lang/en/api-testing.js'

// 在静态依赖以 SSR 环境加载后，仅提供输入 watch 和数字控件卸载需要的 document。
globalThis.document = {
  activeElement: null,
  addEventListener() {},
  removeEventListener() {}
}

// 编译真实 SFC，不落地临时文件；data URL 内的依赖使用绝对模块 URL。
async function compileComponent(filename, replacements = {}) {
  const url = new URL(filename, import.meta.url)
  const { descriptor, errors } = parse(await readFile(url, 'utf8'), { filename: url.pathname })
  assert.deepEqual(errors, [])
  const { content } = compileScript(descriptor, { id: filename, inlineTemplate: true })
  const code = content.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => (
    `from ${quote}${replacements[specifier] || import.meta.resolve(specifier)}${quote}`
  ))
  return `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
}

const valueInputUrl = await compileComponent('./AssertionValueInput.vue')
const { default: AssertionValueInput } = await import(valueInputUrl)
const editorUrl = await compileComponent('./AssertionEditor.vue', {
  './AssertionValueInput.vue': valueInputUrl,
  '@/utils/assertions': new URL('../../utils/assertions.js', import.meta.url).href
})
const { default: AssertionEditor } = await import(editorUrl)

function memoryNode(tag, text = '') {
  return {
    tag, text, props: {}, children: [], parent: null, value: '',
    setAttribute(key, value) { this.props[key] = value },
    removeAttribute(key) { delete this.props[key] },
    addEventListener() {},
    removeEventListener() {},
    setSelectionRange() {}
  }
}

const renderer = createRenderer({
  createElement: memoryNode,
  createText: text => memoryNode('#text', text),
  createComment: text => memoryNode('#comment', text),
  setText: (node, text) => { node.text = text },
  setElementText: (node, text) => { node.text = text; node.children = [] },
  parentNode: node => node.parent,
  nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  patchProp: (node, key, previous, value) => {
    node.props[key] = value
    if (key === 'value') node.value = value
  },
  insert(node, parent, anchor = null) {
    if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
    const index = anchor ? parent.children.indexOf(anchor) : parent.children.length
    parent.children.splice(index, 0, node)
    node.parent = parent
  },
  remove(node) {
    if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
    node.parent = null
  }
})

// 下拉框、按钮不涉及数值转换；输入框与数字控件使用真实 Element Plus 实现。
const Select = defineComponent({
  props: ['modelValue'],
  emits: ['update:modelValue', 'change'],
  setup(props, { attrs, emit, slots }) {
    return () => h('select', {
      ...attrs,
      value: props.modelValue,
      onChange(event) {
        emit('update:modelValue', event.target.value)
        emit('change', event.target.value)
      }
    }, slots.default?.())
  }
})
const Option = defineComponent({
  props: ['label', 'value'],
  setup: props => () => h('option', { value: props.value }, props.label)
})
const Button = (_, { attrs, slots }) => h('button', attrs, slots.default?.())
const Icon = (_, { slots }) => h('span', slots.default?.())
const Tooltip = (_, { slots }) => slots.default?.()

const descendants = (node, predicate) => [
  ...(predicate(node) ? [node] : []),
  ...node.children.flatMap(child => descendants(child, predicate))
]
const hasClass = (node, name) => String(node.props.class || '').split(/\s+/).includes(name)
const rows = root => descendants(root, node => hasClass(node, 'assertion-item'))
const inputs = root => descendants(root, node => node.tag === 'input')

function componentVNodes(vnode, type) {
  if (!vnode) return []
  return [
    ...(vnode.type === type ? [vnode] : []),
    ...(vnode.component
      ? componentVNodes(vnode.component.subTree, type)
      : Array.isArray(vnode.children)
        ? vnode.children.flatMap(child => componentVNodes(child, type))
        : [])
  ]
}

async function mount(t, assertions, { compact = false, locale = 'zh-cn', component } = {}) {
  const model = ref(structuredClone(assertions))
  const warnings = []
  const root = memoryNode('root')
  const app = renderer.createApp({
    setup: () => () => component
      ? component(model)
      : h(AssertionEditor, {
        modelValue: model.value,
        compact,
        'onUpdate:modelValue': value => { model.value = value }
      })
  })
  app.config.warnHandler = message => warnings.push(message)
  app.provide(ID_INJECTION_KEY, { prefix: 1, current: 0 })
  app.use(createI18n({
    legacy: false,
    locale,
    missingWarn: false,
    fallbackWarn: false,
    messages: { 'zh-cn': { apiTesting: zhCn }, en: { apiTesting: en } }
  }))
  for (const [name, implementation] of Object.entries({
    ElInput, ElInputNumber, ElSelect: Select, ElOption: Option, ElButton: Button, ElIcon: Icon,
    ElTooltip: Tooltip
  })) app.component(name, implementation)
  app.mount(root)
  t.after(() => app.unmount())
  await nextTick()
  await nextTick()
  return {
    root, model, warnings,
    components: type => componentVNodes(root._vnode, type),
    // 对应编辑页归一化及保存时去掉旧 value 字段，再做请求 JSON 序列化。
    save: () => JSON.parse(JSON.stringify(normalizeAssertions(model.value).map(stripLegacyAssertionValue)))
  }
}

async function inputValue(input, value) {
  input.value = value
  const inputEvent = new Event('input')
  Object.defineProperty(inputEvent, 'target', { value: input })
  await input.props.onInput(inputEvent)
  await nextTick()
  const changeEvent = new Event('change')
  Object.defineProperty(changeEvent, 'target', { value: input })
  await input.props.onChange?.(changeEvent)
  await nextTick()
}

async function selectValue(select, value) {
  select.props.onChange({ target: { value } })
  await nextTick()
}

function deleteButton(row) {
  return descendants(row, node => node.tag === 'button')[0]
}

// 哨兵测试确保测试环境实际执行了引发原故障的数字控件挂载逻辑。
test('真实 ElInputNumber 双向绑定数组时，挂载会发出 null', async t => {
  const mounted = await mount(t, [200, 201], {
    component: model => h(ElInputNumber, {
      modelValue: model.value,
      min: 100,
      max: 599,
      'onUpdate:modelValue': value => { model.value = value }
    })
  })
  assert.equal(mounted.components(ElInputNumber).length, 1)
  assert.equal(mounted.model.value, null)
})

const collections = [
  { name: '显式状态码集合', type: 'status_code', operator: 'one_of', expected: [200, 201] },
  { name: '大小写空白操作符', type: 'status_code', operator: ' ONE_OF ', expected: [200, 201] },
  { name: '隐式状态码集合', type: 'status_code', expected: [200, 201] },
  { name: 'equals 状态码数组', type: 'status_code', operator: 'equals', expected: [200, 201] },
  { name: '空状态码集合', type: 'status_code', expected: [] },
  { name: '仅操作符判定集合', type: 'status_code', operator: ' One_Of ', expected: 200 },
  { name: 'JSONPath 集合', type: 'json_path', operator: 'one_of', json_path: '$.data', expected: ['ok', 200, { ready: true }] },
  { name: 'JSONPath 空白操作符', type: 'json_path', operator: ' ONE_OF ', json_path: '$.data', expected: ['ok', 'ready'] }
]

for (const compact of [false, true]) {
  for (const collection of collections) {
    test(`compact=${compact} ${collection.name}：打开保存、改名、增删其他断言均保留期望值`, async t => {
      const original = [{ name: '普通状态码', type: 'status_code', expected: 200 }, collection]
      const mounted = await mount(t, original, { compact })
      const { root, model, save, components } = mounted
      assert.equal(hasClass(root.children[0], 'compact'), compact)
      assert.equal(components(ElInputNumber).length, 1, '集合行不得挂载数字控件')
      assert.equal(components(AssertionValueInput).length, 0, 'JSONPath 集合不得挂载值编辑器')
      assert.deepEqual(model.value, original, '打开不改变原始配置')
      assert.deepEqual(save(), original, '无修改保存保留完整配置')

      const readOnlyInput = inputs(rows(root)[1]).find(input => input.props.readonly)
      assert.ok(readOnlyInput, '必须显示只读输入框')
      assert.equal(readOnlyInput.value, JSON.stringify(collection.expected))
      assert.ok(descendants(rows(root)[1], node => node.text === zhCn.interface.collectionAssertionReadOnly).length)
      // 即使输入框发出更新事件，也没有双向绑定修改集合。
      await inputValue(readOnlyInput, 'null')
      assert.deepEqual(save(), original)

      await inputValue(inputs(rows(root)[1])[0], '修改后的名称')
      const renamed = { ...collection, name: '修改后的名称' }
      assert.deepEqual(save(), [original[0], renamed])

      const addButton = descendants(root, node => node.tag === 'button')[0]
      addButton.props.onClick()
      await nextTick()
      assert.equal(model.value.length, 3)
      assert.deepEqual(save()[1], renamed)
      assert.equal(components(ElInputNumber).length, 2)

      // 删除前面的普通断言，覆盖 index key 复用时集合移动到第一行的情况。
      deleteButton(rows(root)[0]).props.onClick()
      await nextTick()
      assert.deepEqual(save()[0], renamed)
      deleteButton(rows(root)[1]).props.onClick()
      await nextTick()
      assert.deepEqual(save(), [renamed])
      assert.equal(components(ElInputNumber).length, 0)
      assert.deepEqual(mounted.warnings, [])
    })
  }

  test(`compact=${compact} 普通状态码仍通过真实数字控件编辑`, async t => {
    const mounted = await mount(t, [{ name: '状态码', type: 'status_code', expected: 200 }], { compact })
    assert.equal(mounted.components(ElInputNumber).length, 1)
    const numericInput = inputs(mounted.root).find(input => input.props.role === 'spinbutton')
    assert.ok(numericInput)
    assert.equal(numericInput.value, '200')
    await inputValue(numericInput, '204')
    assert.equal(numericInput.value, '204')
    assert.equal(mounted.model.value[0].expected, 204)
    assert.equal(mounted.save()[0].expected, 204)
    assert.deepEqual(mounted.warnings, [])
  })

  for (const type of ['status_code', 'response_time', 'contains', 'header', 'equals', 'json_path']) {
    test(`compact=${compact} 显式切换为 ${type} 清理旧 one_of 操作符`, async t => {
      const mounted = await mount(t, [type === 'json_path' ? collections[1] : collections[7]], { compact })
      await selectValue(descendants(mounted.root, node => node.tag === 'select')[0], type)
      const assertion = mounted.model.value[0]
      if (type === 'json_path') {
        assert.equal(assertion.operator, 'equals')
        assert.equal(assertion.json_path, '')
        assert.equal(assertion.expected, '')
        assert.equal(mounted.components(AssertionValueInput).length, 1)
      } else {
        assert.equal(Object.hasOwn(assertion, 'operator'), false)
        if (type === 'status_code') {
          assert.equal(assertion.expected, 200)
          assert.equal(mounted.components(ElInputNumber).length, 1)
        } else if (type === 'response_time') {
          assert.equal(assertion.expected, 1000)
        } else if (type === 'header') {
          assert.equal(assertion.header_name, '')
          assert.equal(assertion.expected_value, '')
        } else {
          assert.equal(assertion.expected, '')
        }
      }
      assert.deepEqual(mounted.warnings, [])
    })
  }

  test(`compact=${compact} 从 JSONPath 集合切换状态码，再切回 JSONPath 初始化 equals`, async t => {
    const mounted = await mount(t, [collections[6]], { compact })
    const typeSelect = () => descendants(mounted.root, node => node.tag === 'select')[0]
    await selectValue(typeSelect(), 'status_code')
    assert.equal(Object.hasOwn(mounted.model.value[0], 'operator'), false)
    assert.equal(mounted.model.value[0].expected, 200)
    await selectValue(typeSelect(), 'json_path')
    assert.equal(mounted.model.value[0].operator, 'equals')
    assert.equal(mounted.model.value[0].expected, '')
    assert.deepEqual(mounted.warnings, [])
  })
}

test('集合只读提示在英文语言下显示 API-only 说明', async t => {
  const mounted = await mount(t, [collections[0]], { locale: 'en' })
  assert.match(en.interface.collectionAssertionReadOnly, /API/)
  assert.ok(descendants(mounted.root, node => node.text === en.interface.collectionAssertionReadOnly).length)
  assert.deepEqual(mounted.save(), [collections[0]])
})
