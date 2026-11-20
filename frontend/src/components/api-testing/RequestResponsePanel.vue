<template>
  <div class="request-response-panel">
    <RequestEditor
      ref="editorRef"
      v-bind="$attrs"
      :model-value="modelValue"
      :environment="environment"
      @update:model-value="emit('update:modelValue', $event)"
    >
      <template v-for="(_, name) in $slots" #[name]="slotProps">
        <slot :name="name" v-bind="slotProps || {}" />
      </template>
    </RequestEditor>
    <ResponseViewer :response="response" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import RequestEditor from './RequestEditor.vue'
import ResponseViewer from './ResponseViewer.vue'

defineOptions({ inheritAttrs: false })

defineProps({
  modelValue: { type: Object, required: true },
  environment: { type: Object, default: null },
  response: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue'])
const editorRef = ref(null)

defineExpose({
  // 供页面把数据工厂/变量片段插入当前草稿字段
  insertText: (...args) => editorRef.value?.insertText?.(...args)
})
</script>

<style scoped>
.request-response-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
  flex: 1;
  min-height: 0;
  min-width: 0;
}
</style>
