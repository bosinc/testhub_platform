<template>
  <el-dialog
    v-model="visible"
    :title="t('uiAutomation.testCase.screenshotPreview')"
    width="80%"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :modal="true"
    :destroy-on-close="false"
    append-to-body
  >
    <div v-if="screenshot" class="screenshot-preview">
      <div class="preview-info">
        <h4>{{ screenshot.description || fallbackTitle }}</h4>
        <span v-if="screenshot.timestamp" class="preview-time">
          {{ t('uiAutomation.testCase.screenshotTime') }}: {{ formatTime(screenshot.timestamp) }}
        </span>
      </div>
      <div class="preview-image">
        <img :src="screenshot.url" :alt="screenshot.description || fallbackTitle" />
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  screenshot: { type: Object, default: null },
  fallbackTitle: { type: String, default: '' }
})

const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const formatTime = (timestamp) => {
  if (!timestamp) return ''
  return new Date(timestamp).toLocaleString()
}
</script>

<style scoped>
.screenshot-preview {
  display: flex;
  flex-direction: column;
}

.preview-info {
  margin-bottom: 20px;
  padding: 15px;
  background: #f5f7fa;
  border-radius: 6px;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px 12px;
}

.preview-info h4 {
  margin: 0;
  font-size: 16px;
  color: #333;
}

.preview-info .preview-time {
  font-size: 14px;
  color: #666;
  white-space: nowrap;
}

.preview-image {
  display: flex;
  justify-content: center;
  align-items: center;
  background: #f5f5f5;
  border-radius: 8px;
  padding: 20px;
  max-height: 70vh;
  overflow: auto;
}

.preview-image img {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}
</style>
