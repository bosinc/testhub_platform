<template>
  <el-select
    v-bind="$attrs"
    :model-value="modelValue"
    :filterable="filterable"
    @update:model-value="onUpdate"
  >
    <el-option
      v-for="user in options"
      :key="user.id"
      :label="optionLabel(user)"
      :value="user[valueKey]"
    >
      <div class="user-select-option">
        <span class="user-select-option__name">{{ optionLabel(user) }}</span>
        <span class="user-select-option__email">{{ user.email || '-' }}</span>
      </div>
    </el-option>
  </el-select>
</template>

<script setup>
import { computed } from 'vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: [String, Number, Array], default: null },
  options: { type: Array, default: () => [] },
  valueKey: { type: String, default: 'id' },
  labelKey: { type: String, default: 'username' },
  filterable: { type: Boolean, default: true }
})

const emit = defineEmits(['update:modelValue'])

const options = computed(() => props.options || [])

const optionLabel = (user) => user?.[props.labelKey] || user?.username || user?.name || user?.email || ''

const onUpdate = (value) => emit('update:modelValue', value)
</script>

<style scoped>
.user-select-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  width: 100%;
}

.user-select-option__name {
  overflow: hidden;
  color: var(--el-text-color-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-select-option__email {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
