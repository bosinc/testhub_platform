<template>
  <div class="condition-fields">
    <el-input
      :model-value="modelValue.left"
      class="condition-left"
      :placeholder="$t('apiTesting.scenarioEditor.leftPlaceholder')"
      @update:model-value="update({ left: $event })"
    />
    <div class="condition-operator-row">
      <el-select
        :model-value="modelValue.operator"
        class="condition-operator"
        @update:model-value="changeOperator"
      >
        <el-option v-for="item in operatorOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select
        v-if="!isUnary"
        :model-value="modelValue.right_type"
        class="condition-type"
        :disabled="lockedRightType"
        @update:model-value="changeRightType"
      >
        <el-option v-for="item in rightTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
    </div>
    <template v-if="!isUnary">
      <el-input-number
        v-if="modelValue.right_type === 'number'"
        :model-value="modelValue.right"
        class="condition-right"
        :controls="false"
        @update:model-value="update({ right: $event })"
      />
      <el-select
        v-else-if="modelValue.right_type === 'boolean'"
        :model-value="modelValue.right"
        class="condition-right"
        @update:model-value="update({ right: $event })"
      >
        <el-option :label="'true'" :value="true" />
        <el-option :label="'false'" :value="false" />
      </el-select>
      <el-input
        v-else
        :model-value="modelValue.right"
        class="condition-right"
        :placeholder="$t('apiTesting.scenarioEditor.rightPlaceholder')"
        @update:model-value="update({ right: $event })"
      />
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({ modelValue: { type: Object, required: true } })
const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()

const UNARY = ['exists', 'not_exists', 'is_empty', 'not_empty']
const NUMBER_ONLY = ['gt', 'gte', 'lt', 'lte']

const operatorOptions = computed(() => [
  { value: 'eq', label: t('apiTesting.scenarioEditor.opEq') },
  { value: 'ne', label: t('apiTesting.scenarioEditor.opNe') },
  { value: 'gt', label: t('apiTesting.scenarioEditor.opGt') },
  { value: 'gte', label: t('apiTesting.scenarioEditor.opGte') },
  { value: 'lt', label: t('apiTesting.scenarioEditor.opLt') },
  { value: 'lte', label: t('apiTesting.scenarioEditor.opLte') },
  { value: 'contains', label: t('apiTesting.scenarioEditor.opContains') },
  { value: 'not_contains', label: t('apiTesting.scenarioEditor.opNotContains') },
  { value: 'exists', label: t('apiTesting.scenarioEditor.opExists') },
  { value: 'not_exists', label: t('apiTesting.scenarioEditor.opNotExists') },
  { value: 'is_empty', label: t('apiTesting.scenarioEditor.opIsEmpty') },
  { value: 'not_empty', label: t('apiTesting.scenarioEditor.opNotEmpty') }
])

const isUnary = computed(() => UNARY.includes(props.modelValue.operator))
const lockedRightType = computed(() => NUMBER_ONLY.includes(props.modelValue.operator)
  || props.modelValue.operator === 'contains'
  || props.modelValue.operator === 'not_contains')
const rightTypeOptions = computed(() => {
  const all = [
    { value: 'string', label: t('apiTesting.scenarioEditor.rightString') },
    { value: 'number', label: t('apiTesting.scenarioEditor.rightNumber') },
    { value: 'boolean', label: t('apiTesting.scenarioEditor.rightBoolean') },
    { value: 'null', label: t('apiTesting.scenarioEditor.rightNull') }
  ]
  if (NUMBER_ONLY.includes(props.modelValue.operator)) return all.filter(item => item.value === 'number')
  if (['contains', 'not_contains'].includes(props.modelValue.operator)) return all.filter(item => item.value === 'string')
  return all
})

function update(patch) {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}

function defaultValue(type) {
  if (type === 'number') return 0
  if (type === 'boolean') return true
  if (type === 'null') return null
  return ''
}

function changeOperator(operator) {
  if (UNARY.includes(operator)) {
    update({ operator, right: null, right_type: 'null' })
    return
  }
  let rightType = props.modelValue.right_type
  if (NUMBER_ONLY.includes(operator)) rightType = 'number'
  if (['contains', 'not_contains'].includes(operator)) rightType = 'string'
  if (!rightType || rightType === 'null') rightType = 'string'
  const right = props.modelValue.right_type === rightType
    ? props.modelValue.right
    : defaultValue(rightType)
  update({ operator, right_type: rightType, right })
}

function changeRightType(rightType) {
  update({ right_type: rightType, right: defaultValue(rightType) })
}
</script>

<style scoped>
/* 左值、运算符、右值各自独占一行，与场景编辑器的竖向表单保持一致。 */
.condition-fields { display: flex; flex-direction: column; gap: 10px; }
.condition-operator-row { display: flex; align-items: center; gap: 8px; }
.condition-left, .condition-right { width: 100%; }
.condition-operator { flex: 1; min-width: 0; }
.condition-type { width: 110px; flex: none; }
</style>
