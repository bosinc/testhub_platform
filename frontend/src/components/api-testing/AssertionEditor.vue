<template>
  <div class="assertions-editor" :class="{ compact }">
    <div class="assertions-header">
      <el-button type="primary" size="small" @click="addAssertion">
        <el-icon><Plus /></el-icon>
        {{ $t('apiTesting.interface.addAssertion') }}
      </el-button>
    </div>

    <div class="assertions-list">
      <div v-for="(assertion, index) in assertions" :key="index" class="assertion-item">
        <div class="assertion-header">
          <el-input
            v-model="assertion.name"
            :placeholder="$t('apiTesting.interface.assertionName')"
            class="assertion-name"
          />
          <el-button link type="danger" @click="removeAssertion(index)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>

        <div class="assertion-config">
          <div class="assertion-config-row">
            <el-select
              v-model="assertion.type"
              class="assertion-type"
              :placeholder="$t('apiTesting.interface.selectAssertionType')"

              @change="resetAssertion(assertion)"
            >
              <el-option :label="$t('apiTesting.interface.assertionTypes.statusCode')" value="status_code" />
              <el-option :label="$t('apiTesting.interface.assertionTypes.responseTime')" value="response_time" />
              <el-option :label="$t('apiTesting.interface.assertionTypes.contains')" value="contains" />
              <el-option :label="$t('apiTesting.interface.assertionTypes.jsonPath')" value="json_path" />
              <el-option :label="$t('apiTesting.interface.assertionTypes.header')" value="header" />
              <el-option :label="$t('apiTesting.interface.assertionTypes.equals')" value="equals" />
            </el-select>
            <el-select
              v-if="assertion.type === 'json_path'"
              v-model="assertion.operator"
              class="assertion-operator"

              @change="resetOperator(assertion)"
            >
              <el-option
                v-for="option in operatorOptions"
                :key="option.value"
                :label="$t(option.label)"
                :value="option.value"
              />
            </el-select>
          </div>

          <div v-if="assertion.type" class="assertion-params">
            <template v-if="assertion.type === 'status_code' && isCollectionAssertion(assertion)">
              <el-input :model-value="JSON.stringify(assertion.expected)" readonly />
              <span class="assertion-hint">
                {{ $t('apiTesting.interface.collectionAssertionReadOnly') }}
              </span>
            </template>
            <el-input-number
              v-else-if="assertion.type === 'status_code'"
              v-model="assertion.expected"
              :min="100"
              :max="599"
             
              :placeholder="$t('apiTesting.interface.expectedStatusCode')"
            />
            <el-input-number
              v-else-if="assertion.type === 'response_time'"
              v-model="assertion.expected"
              :min="1"
             
              :placeholder="$t('apiTesting.interface.maxResponseTime')"
            />
            <template v-else-if="assertion.type === 'json_path'">
              <el-input
                v-model="assertion.json_path"
                :placeholder="$t('apiTesting.interface.jsonPathExample')"
               
                class="assertion-input"
              />
              <template v-if="isCollectionAssertion(assertion)">
                <el-input :model-value="JSON.stringify(assertion.expected)" readonly />
                <span class="assertion-hint">
                  {{ $t('apiTesting.interface.collectionAssertionReadOnly') }}
                </span>
              </template>
              <AssertionValueInput
                v-else-if="operatorNeedsValue(assertion)"
                v-model="assertion.expected"
                placeholder="apiTesting.interface.expectedValue"
                :show-helpers="showHelpers"
                :assertion="assertion"
                field="expected"
                :index="index"
                @data-factory="emitHelper('data-factory', $event)"
                @variable-helper="emitHelper('variable-helper', $event)"
              />
              <span v-else class="assertion-hint">
                {{ $t('apiTesting.interface.operatorNoValue') }}
              </span>
            </template>
            <template v-else-if="assertion.type === 'header'">
              <el-input
                v-model="assertion.header_name"
                :placeholder="$t('apiTesting.interface.headerNameLabel')"
               
                class="assertion-input"
              />
              <AssertionValueInput
                v-model="assertion.expected_value"
                placeholder="apiTesting.interface.expectedValue"
                :show-helpers="showHelpers"
                :assertion="assertion"
                field="expected_value"
                :index="index"
                @data-factory="emitHelper('data-factory', $event)"
                @variable-helper="emitHelper('variable-helper', $event)"
              />
            </template>
            <AssertionValueInput
              v-else
              v-model="assertion.expected"
              :placeholder="valuePlaceholder(assertion.type)"
              :show-helpers="showHelpers"
              :assertion="assertion"
              field="expected"
              :index="index"
              @data-factory="emitHelper('data-factory', $event)"
              @variable-helper="emitHelper('variable-helper', $event)"
            />
          </div>
        </div>
      </div>

      <div v-if="assertions.length === 0" class="no-assertions">
        <p>{{ $t('apiTesting.interface.noAssertions') }}</p>
        <el-button type="primary" size="small" @click="addAssertion">
          <el-icon><Plus /></el-icon>
          {{ $t('apiTesting.interface.addFirstAssertion') }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Delete, Plus } from '@element-plus/icons-vue'
import AssertionValueInput from './AssertionValueInput.vue'
import {
  createAssertion,
  normalizeAssertions,
  resetAssertionFields
} from '@/utils/assertions'

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => []
  },
  showHelpers: {
    type: Boolean,
    default: false
  },
  compact: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'data-factory', 'variable-helper'])
const { t } = useI18n()
const assertions = computed({
  get: () => props.modelValue || [],
  set: value => emit('update:modelValue', value)
})

const addAssertion = () => {
  emit('update:modelValue', [
    ...normalizeAssertions(assertions.value),
    createAssertion(`${t('apiTesting.interface.assertion')}${assertions.value.length + 1}`)
  ])
}

const removeAssertion = (index) => {
  emit('update:modelValue', assertions.value.filter((_, itemIndex) => itemIndex !== index))
}

const resetAssertion = assertion => resetAssertionFields(assertion)
const emitHelper = (eventName, payload) => emit(eventName, payload)

// JSONPath 断言的操作符：语义与源测试框架的 expect() DSL 一一对应。
// 说明：one_of（多值集合）仅支持通过 API 写入，界面不提供，避免出现半成品输入控件。
const operatorOptions = [
  { value: 'equals', label: 'apiTesting.interface.assertionOperators.equals' },
  { value: 'not_equals', label: 'apiTesting.interface.assertionOperators.notEquals' },
  { value: 'exists', label: 'apiTesting.interface.assertionOperators.exists' },
  { value: 'not_exists', label: 'apiTesting.interface.assertionOperators.notExists' },
  { value: 'is_not_empty', label: 'apiTesting.interface.assertionOperators.isNotEmpty' },
  { value: 'is_empty', label: 'apiTesting.interface.assertionOperators.isEmpty' },
  { value: 'is_string', label: 'apiTesting.interface.assertionOperators.isString' },
  { value: 'is_number', label: 'apiTesting.interface.assertionOperators.isNumber' },
  { value: 'is_boolean', label: 'apiTesting.interface.assertionOperators.isBoolean' },
  { value: 'is_array', label: 'apiTesting.interface.assertionOperators.isArray' },
  { value: 'is_object', label: 'apiTesting.interface.assertionOperators.isObject' },
  { value: 'includes', label: 'apiTesting.interface.assertionOperators.includes' },
  { value: 'not_includes', label: 'apiTesting.interface.assertionOperators.notIncludes' },
  { value: 'contains', label: 'apiTesting.interface.assertionOperators.contains' },
  { value: 'not_contains', label: 'apiTesting.interface.assertionOperators.notContains' },
  { value: 'at_least', label: 'apiTesting.interface.assertionOperators.atLeast' },
  { value: 'greater_than', label: 'apiTesting.interface.assertionOperators.greaterThan' },
  { value: 'less_than', label: 'apiTesting.interface.assertionOperators.lessThan' },
  { value: 'close_to', label: 'apiTesting.interface.assertionOperators.closeTo' },
  { value: 'length', label: 'apiTesting.interface.assertionOperators.length' },
  { value: 'matches', label: 'apiTesting.interface.assertionOperators.matches' }
]

// 这些操作符只判定取值本身，不需要期望值
const NO_VALUE_OPERATORS = new Set([
  'exists', 'not_exists',
  'is_not_empty', 'is_empty',
  'is_string', 'is_number', 'is_boolean', 'is_array', 'is_object'
])

const isCollectionAssertion = assertion => (
  String(assertion.operator || '').trim().toLowerCase() === 'one_of' ||
  (assertion.type === 'status_code' && Array.isArray(assertion.expected))
)

const operatorNeedsValue = assertion => !NO_VALUE_OPERATORS.has(assertion.operator || 'equals')

const resetOperator = assertion => {
  if (!operatorNeedsValue(assertion)) assertion.expected = ''
}

const valuePlaceholder = type => (
  type === 'contains'
    ? 'apiTesting.interface.expectedContains'
    : 'apiTesting.interface.expectedMatch'
)
</script>

<style scoped>
/* 与 KeyValueEditor 保持一致的紧凑分区：8px 内边距与间距。 */
.assertions-editor {
  background: white;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.assertions-editor.compact {
  border: 0;
  background: transparent;
}

.assertions-editor.compact .assertions-list {
  max-height: 55vh;
  overflow-y: auto;
  padding-right: 6px;
}

.assertions-header {
  display: flex;
  align-items: center;
  padding: 8px;
  border-bottom: 1px solid #e4e7ed;
  background: #fafbfc;
}

.assertions-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
}

.assertion-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
  background: white;
  border: 1px solid #e9ecef;
  border-radius: 4px;
}

.assertion-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.assertion-name {
  flex: 1;
  min-width: 0;
}

.assertion-config {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.assertion-config-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.assertion-type,
.assertion-operator {
  flex: 0 0 200px;
  width: 200px;
}

.assertion-params {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.assertion-hint {
  display: block;
  font-size: 12px;
  color: #909399;
}

.no-assertions {
  padding: 8px;
  text-align: center;
  color: #909399;
}
</style>

