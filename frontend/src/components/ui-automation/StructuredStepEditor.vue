<template>
  <div class="structured-step-editor">
    <div v-if="showToolbar" class="steps-toolbar">
      <span>{{ title || t('uiAutomation.stepEditor.title') }} ({{ modelValue.length }})</span>
      <el-button size="default" @click="addStep">{{ t('uiAutomation.testCase.addStep') }}</el-button>
    </div>
    <draggable v-model="localSteps" item-key="id" handle=".drag-handle" @change="emitSteps">
      <template #item="{ element, index }">
        <section class="step-row" :class="{ expanded: element.expanded }">
          <header class="step-header">
            <el-icon class="drag-handle"><Rank /></el-icon>
            <span class="step-number">{{ index + 1 }}</span>
            <!-- 步骤描述放在摘要行，折叠后仍可查看和编辑。 -->
            <el-input v-model="element.description" class="description-field" size="default" :placeholder="t('uiAutomation.stepEditor.description')" @input="emitSteps" />
            <el-button size="default" text @click="element.expanded = !element.expanded; emitSteps()" :aria-label="t(element.expanded ? 'uiAutomation.stepEditor.collapseStep' : 'uiAutomation.stepEditor.expandStep')">
              <el-icon><ArrowUp v-if="element.expanded" /><ArrowDown v-else /></el-icon>
            </el-button>
            <el-button size="default" text type="danger" @click="removeStep(index)">{{ t('uiAutomation.common.delete') }}</el-button>
          </header>
          <div v-if="element.expanded" class="step-content">
          <!-- 动作、页面及元素选择统一排列在摘要下方。 -->
          <div class="step-field action-row" :class="{ 'inline-input-action-row': ['wait', 'switchTab', 'navigate'].includes(element.action_type), 'storage-state-action-row': element.action_type === 'storageState' }">
            <el-select v-model="element.action_type" class="action-selector" size="default" :placeholder="t('uiAutomation.testCase.selectAction')" fit-input-width popper-class="action-selector-popper" @change="onActionChange(element)">
              <el-option v-for="action in actions" :key="action.value" :label="action.label" :value="action.value" />
            </el-select>
            <!-- 固定等待不需要元素选择，时间直接跟随动作显示。 -->
            <el-input-number v-if="element.action_type === 'wait'" v-model="element.wait_time" :min="100" :max="30000" :step="100" size="default" :aria-label="t('uiAutomation.testCase.waitTime')" @change="emitSteps" />
            <!-- 切换标签页和跳转 URL 的输入及辅助按钮与动作同行。 -->
            <template v-if="['switchTab', 'navigate'].includes(element.action_type)">
              <el-input v-model="element.input_value" size="default" :placeholder="t(element.action_type === 'navigate' ? 'uiAutomation.stepEditor.url' : 'uiAutomation.stepEditor.inputValue')" @input="emitSteps" />
              <el-button v-if="helperActions" class="data-factory-button" type="primary" size="default" :icon="MagicStick" :title="t('uiAutomation.stepEditor.selectFactoryData')" :aria-label="t('uiAutomation.stepEditor.selectFactoryData')" @click="emit('helper', { step: element, field: 'input_value' })" />
              <el-button v-if="helperActions && element.action_type === 'navigate'" class="variable-helper-button" size="default" :title="t('uiAutomation.testCase.insertVariable')" :aria-label="t('uiAutomation.testCase.insertVariable')" @click="emit('variable', { step: element, field: 'input_value' })"><el-icon><MagicStick /></el-icon></el-button>
            </template>
            <!-- 登录态操作紧随动作选择器，保存方式与目标仍放在下一行。 -->
            <el-select v-if="element.action_type === 'storageState'" v-model="element.action_params.operation" size="default" :placeholder="t('uiAutomation.stepEditor.operation')" @change="emitSteps">
              <el-option :label="t('uiAutomation.common.save')" value="save" /><el-option :label="t('uiAutomation.stepEditor.switchStorageState')" value="switch" /><el-option :label="t('uiAutomation.stepEditor.clearStorageState')" value="clear" />
            </el-select>
            <el-select v-if="needsElement(element) && pages.length" v-model="element.element_page" class="page-selector" size="default" clearable :placeholder="t('uiAutomation.stepEditor.page')" @change="onPageChange(element)">
              <el-option v-for="page in pages" :key="page.id" :label="page.name" :value="page.name" />
            </el-select>
            <el-select
              v-if="needsElement(element)"
              v-model="element.element_id"
              class="element-selector"
              size="default"
              filterable
              :remote="!elementsReady"
              fit-input-width
              popper-class="element-selector-popper"
              clearable
              :loading="elementsLoading"
              :placeholder="t('uiAutomation.testCase.selectElement')"
              :remote-method="keyword => emit('search-elements', { step: element, keyword })"
              @visible-change="visible => onElementSelectorVisible(element, visible)"
              @change="onElementChange(element)"
            >
              <el-option v-for="item in getElementsForStep(element)" :key="item.id" :label="elementOptionLabel(item)" :value="item.id" />
            </el-select>
          </div>
          <div v-if="needsElement(element)" class="step-field locator-field">
            <el-select v-model="element.locator_strategy" size="default" :placeholder="t('uiAutomation.stepEditor.locatorStrategy')" :disabled="hasElement(element)" @change="onLocatorChange(element)">
              <el-option v-for="strategy in strategies" :key="strategy" :label="strategy" :value="strategy" />
            </el-select>
            <el-input v-model="element.locator_value" size="default" :placeholder="t('uiAutomation.stepEditor.locator')" :disabled="hasElement(element)" @input="emitSteps" />
            <el-input v-if="element.locator_strategy === 'role'" v-model="element.locator_name" size="default" :placeholder="t('uiAutomation.stepEditor.optionalName')" :disabled="hasElement(element)" @input="onLocatorNameInput(element)" />
          </div>
          <div v-if="element.action_type === 'setVariable'" class="step-field variable-set-field">
            <el-input v-model="element.input_value" size="default" :placeholder="t('uiAutomation.stepEditor.variableName')" @input="emitSteps" />
            <el-input v-model="element.assert_value" size="default" :placeholder="t('uiAutomation.stepEditor.variableValue')" @input="emitSteps" />
          </div>
          <!-- 登录态步骤不绑定页面元素，只编辑保存、切换、清除及其参数。 -->
          <div v-else-if="element.action_type === 'storageState' && element.action_params.operation !== 'clear'" class="storage-state-fields">
            <!-- 保存方式独占下一行，右侧只显示当前方式需要的目标控件。 -->
            <div v-if="element.action_params.operation === 'save'" class="step-field storage-state-save-field">
              <el-radio-group v-model="element.action_params.mode" size="default" @change="onStorageStateSaveChange(element)">
                <el-radio-button value="overwrite">{{ t('uiAutomation.stepEditor.overwrite') }}</el-radio-button><el-radio-button value="save_as">{{ t('uiAutomation.stepEditor.saveAs') }}</el-radio-button>
              </el-radio-group>
              <el-select v-if="element.action_params.mode === 'overwrite'" v-model="element.action_params.storage_state_id" size="default" :placeholder="t('uiAutomation.stepEditor.selectStorageState')" @change="onStorageStateSaveChange(element)">
                <el-option v-for="state in storageStates" :key="state.id" :label="state.name" :value="state.id" />
              </el-select>
              <el-input v-else v-model="element.action_params.name" size="default" :placeholder="t('uiAutomation.stepEditor.storageStateName')" @input="emitSteps" />
            </div>
            <el-select v-else-if="element.action_params.operation === 'switch'" v-model="element.action_params.storage_state_id" size="default" :placeholder="t('uiAutomation.stepEditor.selectStorageState')" @change="emitSteps">
              <el-option v-for="state in storageStates" :key="state.id" :label="state.name" :value="state.id" />
            </el-select>
          </div>
          <div v-else-if="element.action_type === 'getText'" class="step-field">
            <el-input v-model="element.assert_value" size="default" :placeholder="t('uiAutomation.stepEditor.saveToVariable')" @input="emitSteps" />
          </div>
          <div v-else-if="needsInput(element.action_type)" class="step-field">
            <el-input v-model="element.input_value" size="default" :placeholder="t(element.action_type === 'navigate' ? 'uiAutomation.stepEditor.url' : 'uiAutomation.stepEditor.inputValue')" @input="emitSteps" />
            <el-button v-if="helperActions" class="data-factory-button" type="primary" size="default" :icon="MagicStick" :title="t('uiAutomation.stepEditor.selectFactoryData')" :aria-label="t('uiAutomation.stepEditor.selectFactoryData')" @click="emit('helper', { step: element, field: 'input_value' })" />
            <el-button v-if="helperActions && element.action_type !== 'switchTab'" class="variable-helper-button" size="default" :title="t('uiAutomation.testCase.insertVariable')" :aria-label="t('uiAutomation.testCase.insertVariable')" @click="emit('variable', { step: element, field: 'input_value' })"><el-icon><MagicStick /></el-icon></el-button>
          </div>
          <div v-if="element.action_type === 'waitFor'" class="step-field">
            <el-input-number v-model="element.wait_time" :min="100" :max="30000" :step="100" size="default" @change="emitSteps" />
          </div>
          <div v-if="element.action_type === 'assert'" class="step-field assertion-field">
            <el-select v-model="element.assert_type" size="default" @change="emitSteps">
              <el-option :label="t('uiAutomation.testCase.assertTextContains')" value="textContains" /><el-option :label="t('uiAutomation.testCase.assertTextEquals')" value="textEquals" />
              <el-option :label="t('uiAutomation.testCase.assertIsVisible')" value="isVisible" /><el-option :label="t('uiAutomation.testCase.assertExists')" value="exists" />
              <el-option :label="t('uiAutomation.testCase.assertIsNotVisible')" value="isNotVisible" /><el-option :label="t('uiAutomation.testCase.assertHasAttribute')" value="hasAttribute" />
              <el-option :label="t('uiAutomation.testCase.assertUrlContains')" value="urlContains" />
            </el-select>
            <!-- 不可见断言无需期望值；属性断言提示属性存在与精确值两种写法。 -->
            <el-input v-if="element.assert_type !== 'isNotVisible'" v-model="element.assert_value" size="default" :placeholder="t(element.assert_type === 'hasAttribute' ? 'uiAutomation.stepEditor.attributeValue' : 'uiAutomation.stepEditor.assertValue')" @input="emitSteps" />
            <el-button v-if="helperActions && element.assert_type !== 'isNotVisible'" class="data-factory-button" type="primary" size="default" :icon="MagicStick" :title="t('uiAutomation.stepEditor.selectFactoryData')" :aria-label="t('uiAutomation.stepEditor.selectFactoryData')" @click="emit('helper', { step: element, field: 'assert_value' })" />
            <el-button v-if="helperActions && element.assert_type !== 'isNotVisible'" class="variable-helper-button" size="default" :title="t('uiAutomation.testCase.insertVariable')" :aria-label="t('uiAutomation.testCase.insertVariable')" @click="emit('variable', { step: element, field: 'assert_value' })"><el-icon><MagicStick /></el-icon></el-button>
          </div>
          </div>
        </section>
      </template>
    </draggable>
    <el-empty v-if="!modelValue.length" :description="t('uiAutomation.stepEditor.empty')" :image-size="48" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import draggable from 'vuedraggable'
import { Rank, MagicStick, ArrowDown, ArrowUp } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  elements: { type: Array, default: () => [] },
  pages: { type: Array, default: () => [] },
  storageStates: { type: Array, default: () => [] },
  elementsLoading: { type: Boolean, default: false },
  elementsReady: { type: Boolean, default: false },
  title: { type: String, default: '' },
  helperActions: { type: Boolean, default: false },
  showToolbar: { type: Boolean, default: true }
})
const emit = defineEmits(['update:modelValue', 'change', 'helper', 'variable', 'search-elements', 'open-element-selector'])
// 与后端 LocatorStrategy 名称保持一致，保证步骤编辑和代码生成使用同一套值。
const strategies = ['role', 'label', 'placeholder', 'text', 'test-id', 'title', 'alt-text', 'css', 'xpath']
const { t } = useI18n()
// 动作标识保持不变，仅展示文案随当前语言即时更新。
const actionTypes = ['click', 'fill', 'press', 'select', 'check', 'uncheck', 'hover', 'getText', 'waitFor', 'scroll', 'screenshot', 'assert', 'wait', 'switchTab', 'navigate', 'setVariable', 'storageState']
const actions = computed(() => actionTypes.map(value => ({
  value,
  // 登录态复用管理模块的统一文案，避免维护重复翻译键。
  label: t(value === 'storageState' ? 'uiAutomation.storageState.label' : `uiAutomation.testCase.action${value[0].toUpperCase()}${value.slice(1)}`)
})))
// 使用 computed 代理 v-model，让拖拽排序和字段修改统一向父组件同步。
const localSteps = computed({
  get: () => props.modelValue,
  set: value => { emit('update:modelValue', value); emit('change', value) }
})
const emitSteps = () => { emit('update:modelValue', props.modelValue); emit('change', props.modelValue) }
const addStep = () => localSteps.value = [...localSteps.value, {
  id: `new-${Date.now()}-${Math.random()}`, action_type: 'click', element_id: '', element_page: '', locator_strategy: 'role', locator_value: '',
  locator_name: '', locator_options: {}, locator_options_json: '{}', input_value: '', action_params: {}, wait_time: 1000, assert_type: 'textContains', assert_value: '', description: ''
}]
const removeStep = index => { localSteps.value = localSteps.value.filter((_, i) => i !== index) }
const needsInput = action => ['fill', 'press', 'select'].includes(action)
const needsElement = step => !['wait', 'switchTab', 'screenshot', 'navigate', 'setVariable', 'storageState'].includes(step.action_type) && !(step.action_type === 'assert' && step.assert_type === 'urlContains')
const hasElement = step => Boolean(step.element_id)
const locatorOptionsText = options => JSON.stringify(options || {})
// 关闭的选择器只保留已选项，避免每个步骤都创建整份元素库的选项组件。
const activeElementStep = ref(null)
const elementsById = computed(() => new Map(props.elements.map(item => [String(item.id), item])))
const elementsByPage = computed(() => {
  const groups = new Map()
  props.elements.forEach(item => {
    if (!groups.has(item.page)) groups.set(item.page, [])
    groups.get(item.page).push(item)
  })
  return groups
})
// 按 ID 从实际候选项中查找已选元素，标签由 el-select 匹配 el-option 后生成。
const selectedElement = step => elementsById.value.get(String(step.element_id))
const getElementsForStep = step => {
  const selected = selectedElement(step)
  if (activeElementStep.value !== step.id) return selected ? [selected] : []
  const elements = step.element_page ? elementsByPage.value.get(step.element_page) || [] : props.elements
  return selected && !elements.includes(selected) ? [selected, ...elements] : elements
}
const onElementSelectorVisible = (step, visible) => {
  if (visible) {
    activeElementStep.value = step.id
    emit('open-element-selector', step)
  } else if (activeElementStep.value === step.id) {
    activeElementStep.value = null
  }
}
const elementOptionLabel = item => {
  const strategy = item.locator_strategy?.name || item.locator_strategy || ''
  return `${item.name} (${strategy ? `${strategy}=` : ''}${item.locator_value})`
}
const onPageChange = step => {
  const selected = selectedElement(step)
  if (selected && selected.page !== step.element_page) {
    step.element_id = ''
    step.locator_strategy = 'role'
    step.locator_value = ''
    step.locator_name = ''
    step.locator_options = {}
    step.locator_options_json = '{}'
  }
  emitSteps()
}
const onActionChange = step => {
  if (!['fill', 'press', 'select', 'navigate', 'switchTab', 'setVariable'].includes(step.action_type)) step.input_value = ''
  if (!['wait', 'waitFor'].includes(step.action_type)) step.wait_time = 1000
  if (step.action_type !== 'assert') { step.assert_type = 'textContains'; step.assert_value = '' }
  if (!needsElement(step)) { step.element_id = ''; step.element_page = '' }
  if (step.action_type === 'storageState') step.action_params = { operation: 'save', mode: 'save_as', name: '', storage_state_id: null }
  emitSteps()
}
// 覆盖时沿用目标登录态名称，兼容保存校验并避免隐藏的旧名称重命名目标。
const onStorageStateSaveChange = step => {
  if (step.action_params.mode === 'overwrite') {
    const selected = props.storageStates.find(state => state.id === step.action_params.storage_state_id)
    step.action_params.name = selected?.name || ''
  }
  emitSteps()
}
const onLocatorChange = step => {
  if (!['role', 'label', 'placeholder'].includes(step.locator_strategy)) {
    step.locator_name = ''
    if (step.locator_options?.name) {
      const { name, ...options } = step.locator_options
      step.locator_options = options
      step.locator_options_json = locatorOptionsText(options)
    }
  }
  if (!hasElement(step)) {
    step.locator_options = {}
    step.locator_options_json = '{}'
  }
  emitSteps()
}
const onLocatorNameInput = step => {
  const options = { ...(step.locator_options || {}) }
  if (step.locator_name) options.name = step.locator_name
  else delete options.name
  step.locator_options = options
  step.locator_options_json = locatorOptionsText(options)
  emitSteps()
}
// 选择元素后回填其页面、定位器及可读描述，避免用户重复录入。
const onElementChange = step => {
  const selected = selectedElement(step)
  if (selected) {
    step.locator_value = selected.locator_value
    // 紧凑列表返回策略字符串，解析结果仍返回带 name 的对象，两种格式统一回填。
    const strategy = selected.locator_strategy?.name || selected.locator_strategy
    step.locator_strategy = strategy?.toLowerCase() || step.locator_strategy
    step.locator_options = { ...(selected.locator_options || {}) }
    step.locator_options_json = locatorOptionsText(step.locator_options)
    step.locator_name = step.locator_options.name || ''
    step.element_page = selected.page || ''
    // 只为尚未填写的描述生成当前语言文案，切换语言不改写已保存的描述。
    step.description ||= t('uiAutomation.stepEditor.elementDescription', {
      action: actions.value.find(item => item.value === step.action_type)?.label || '', name: selected.name
    })
  } else {
    step.locator_strategy = 'role'
    step.locator_value = ''
    step.locator_name = ''
    step.locator_options = {}
    step.locator_options_json = '{}'
  }
  emitSteps()
}
</script>

<style scoped>
.structured-step-editor { box-sizing: border-box; width: 100%; min-width: 0; height: 100%; overflow: auto; padding: 10px; container: step-editor / inline-size; }
.steps-toolbar, .step-header, .step-field { display: flex; align-items: center; gap: 8px; min-width: 0; }
.steps-toolbar { flex-wrap: wrap; justify-content: space-between; margin-bottom: 10px; font-weight: 600; }
.step-row { min-width: 0; border: 1px solid var(--el-border-color); border-radius: 4px; padding: 9px; margin-bottom: 8px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; }
.step-row.expanded { border-color: var(--el-color-primary-light-5); }
.step-header { flex-wrap: wrap; }
/* 输入类控件按剩余空间分配宽度，允许收缩，避免组件默认的 100% 宽度挤出按钮。 */
.step-field > .el-select, .step-field > .el-input, .step-field > .el-input-number {
  flex: 1 1 0;
  width: 0;
  min-width: 0;
}
/* 动作、页面、元素按比例分配整行；最小宽度只用于保证文案可读，空间不足时换行。 */
.action-row { flex-wrap: wrap; }
.action-row > .action-selector { flex: 2 1 22%; min-width: min(180px, 100%); }
.action-row > .page-selector { flex: 2 1 20%; min-width: min(120px, 100%); }
.action-row > .element-selector { flex: 5 1 50%; min-width: min(220px, 100%); }
/* 等待、标签页和 URL 动作与参数按 1:2 分配空间，窄容器下也保持同一行。 */
.inline-input-action-row { flex-wrap: nowrap; }
.inline-input-action-row > .action-selector { flex: 1 1 0; min-width: 0; }
.inline-input-action-row > .el-input, .inline-input-action-row > .el-input-number { flex: 2 1 0; }
/* 登录态动作与操作平分可用宽度，父容器收窄时仍保持同行。 */
.storage-state-action-row { flex-wrap: nowrap; }
.storage-state-action-row > .el-select { flex: 1 1 0; min-width: 0; }
.element-selector :deep(.el-select__selected-item),
.element-selector :deep(.el-select__selected-item span) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 弹层跟随选择器宽度，长定位器保持省略显示。 */
:deep(.element-selector-popper .el-select-dropdown__item) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.step-content, .storage-state-fields { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; min-width: 0; }
.storage-state-save-field { flex-wrap: wrap; }
.storage-state-save-field > .el-radio-group { flex: 0 0 auto; flex-wrap: nowrap; }
.storage-state-save-field > .el-input, .storage-state-save-field > .el-select { min-width: min(120px, 100%); }
.variable-set-field :deep(.el-input__inner) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
/* 断言值和定位器占更多空间，辅助按钮独立占位，不随输入框压缩或重叠。 */
.assertion-field > .el-input, .locator-field > .el-input:nth-child(2) { flex-grow: 2; }
.step-field > .el-button { flex: 0 0 32px; width: 32px; margin: 0; padding: 0; }
.step-field > .data-factory-button { background-color: #409eff; border-color: #409eff; color: white; }
.step-field > .data-factory-button:hover { background-color: #66b1ff; border-color: #66b1ff; }
.step-field > .variable-helper-button { background-color: #67c23a; border-color: #67c23a; color: white; }
.step-field > .variable-helper-button:hover { background-color: #5daf34; border-color: #5daf34; }
/* 描述占满摘要行剩余空间，保持操作按钮可见。 */
.step-header > .description-field { flex: 1 1 30%; min-width: min(120px, 100%); }
.step-header > .el-button { flex: 0 0 auto; margin: 0; }
.drag-handle { cursor: grab; color: var(--el-text-color-secondary); }
.step-number { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; flex: 0 0 24px; border-radius: 50%; background: #409eff; color: #fff; font-weight: 600; }
/* 以组件所在容器而非浏览器宽度响应，兼容侧栏拖动和脚本编辑器分栏。 */
@container step-editor (max-width: 480px) {
  .action-row > .element-selector { flex-basis: 100%; }
  .assertion-field, .locator-field { flex-wrap: wrap; }
  .assertion-field > .el-select, .locator-field > .el-select { flex-basis: 100%; }
}
</style>
