<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="min(1440px, 96vw)"
    align-center
    append-to-body
    destroy-on-close
    :close-on-click-modal="false"
    class="testcase-form-dialog"
    @update:model-value="handleVisibility"
    @closed="handleClosed"
  >
    <template #header>
      <div class="tc-dialog-header">
        <div class="tc-dialog-heading">
          <span class="tc-dialog-title">{{ dialogTitle }}</span>
        </div>
        <div class="tc-dialog-toolbar">
          <el-button @click="close">{{ $t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="busy" @click="handleSave">
            {{ isEdit ? $t('common.save') : $t('testcase.createCase') }}
          </el-button>
        </div>
      </div>
    </template>

    <TestCaseFormPanel
      v-if="modelValue"
      ref="panelRef"
      :test-case-id="testCaseId"
      :project-id="projectId"
      @busy-change="busy = $event"
      @saved="handleSaved"
      @load-error="close"
    />
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import TestCaseFormPanel from '@/components/TestCaseFormPanel.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 为空表示新建用例
  testCaseId: { type: [Number, String], default: null },
  // 新建时的默认归属项目
  projectId: { type: [Number, String], default: null }
})
const emit = defineEmits(['update:modelValue', 'saved'])

const { t } = useI18n()
const panelRef = ref()
const busy = ref(false)

const isEdit = computed(() => props.testCaseId !== null && props.testCaseId !== undefined && props.testCaseId !== '')
// 新建复用「新建用例」文案；编辑只显示纯用例 ID
const dialogTitle = computed(() => (isEdit.value ? String(props.testCaseId) : t('testcase.newCase')))

const close = () => emit('update:modelValue', false)
const handleVisibility = visible => emit('update:modelValue', visible)
const handleClosed = () => { busy.value = false }

const handleSave = async () => {
  const ok = await panelRef.value?.submit()
  if (ok) close()
}

const handleSaved = (detail, mode) => emit('saved', detail, mode)
</script>

<style lang="scss" scoped>
/* 标题栏结构与「用例执行详情弹窗」的 .case-dialog-header 保持一致 */
.tc-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  width: 100%;
}

.tc-dialog-heading,
.tc-dialog-toolbar {
  min-width: 0;
}

.tc-dialog-heading {
  display: flex;
  align-items: center;
  gap: 14px;
}

.tc-dialog-title {
  flex: 0 0 auto;
  /* 与左栏用例标题输入框的字号保持一致 */
  font-size: 20px;
  font-weight: 600;
}

.tc-dialog-toolbar {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  justify-content: flex-end;
  gap: 8px;
}
</style>

<style lang="scss">
/* 尺寸与既有的「用例执行详情弹窗」保持一致：宽 min(1440px, 96vw) */
.testcase-form-dialog {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 10px);
}

.testcase-form-dialog .el-dialog__header {
  flex: 0 0 auto;
}

/* 关闭按钮完全沿用「用例执行详情弹窗」：不做定位覆盖，使用 Element Plus 默认的
   top:0 / right:0 + .show-close 预留的右侧空间，按钮与 X 的间距自然一致 */

.testcase-form-dialog .el-dialog__body {
  min-height: 0;
  /* 弹窗整体不滚动，只由左侧内容区独立滚动 */
  overflow: hidden;
}
</style>
