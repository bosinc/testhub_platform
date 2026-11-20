<template>
  <div class="assertion-value-input">
    <el-input v-model="value" :placeholder="$t(placeholder)">
      <!-- 辅助按钮收进输入框尾部，控件外框与其他输入框同宽。 -->
      <template v-if="showHelpers" #append>
        <el-button
          class="data-factory-btn"
          :icon="MagicStick"
          :title="$t('apiTesting.interface.referDataFactory')"
          @click="$emit('data-factory', { assertion, field, index })"
        />
        <el-tooltip
          :content="$t('apiTesting.interface.insertDynamicVariable')"
          placement="top"
        >
          <el-button
            class="variable-helper-btn"
            @click="$emit('variable-helper', { assertion, field, index })"
          >
            <el-icon><MagicStick /></el-icon>
          </el-button>
        </el-tooltip>
      </template>
    </el-input>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { MagicStick } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: { default: '' },
  assertion: { type: Object, required: true },
  field: { type: String, required: true },
  index: { type: Number, required: true },
  placeholder: { type: String, required: true },
  showHelpers: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue', 'data-factory', 'variable-helper'])
const value = computed({
  get: () => props.modelValue,
  set: nextValue => emit('update:modelValue', nextValue)
})
</script>

<style scoped>
.assertion-value-input {
  width: 100%;
}

.assertion-value-input :deep(.el-input-group__append) {
  padding: 0;
  gap: 0;
}

/* 按钮铺满尾部区域，末位按钮保留输入框右侧圆角。 */
.assertion-value-input :deep(.el-input-group__append .el-button) {
  flex: none;
  align-self: stretch;
  width: 30px;
  height: auto;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
}

.assertion-value-input :deep(.el-input-group__append .el-button:last-child) {
  border-radius: 0 var(--el-input-border-radius) var(--el-input-border-radius) 0;
}

/* 与 Params（KeyValueEditor）中的辅助按钮同色。 */
.assertion-value-input :deep(.el-button.data-factory-btn) {
  background-color: #409eff;
  border-color: #409eff;
  color: white;
}

.assertion-value-input :deep(.el-button.data-factory-btn:hover) {
  background-color: #66b1ff;
  border-color: #66b1ff;
}

.assertion-value-input :deep(.el-button.variable-helper-btn) {
  background-color: #67c23a;
  border-color: #67c23a;
  color: white;
}

.assertion-value-input :deep(.el-button.variable-helper-btn:hover) {
  background-color: #5daf34;
  border-color: #5daf34;
}
</style>
