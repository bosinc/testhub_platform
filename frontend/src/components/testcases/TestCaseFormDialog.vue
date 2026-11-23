<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="min(1440px, 96vw)"
    align-center
    append-to-body
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
          <div v-if="hasNavigation" class="tc-case-navigation">
            <el-button
              class="tc-case-navigation__button"
              :icon="ArrowUp"
              :disabled="currentCaseIndex <= 0"
              :aria-label="$t('testcase.previousCase')"
              @click="navigateCase(-1)" />
            <span class="tc-case-navigation__count">{{ currentCaseIndex + 1 }} / {{ navigationCases.length }}</span>
            <el-button
              class="tc-case-navigation__button"
              :icon="ArrowDown"
              :disabled="currentCaseIndex >= navigationCases.length - 1"
              :aria-label="$t('testcase.nextCase')"
              @click="navigateCase(1)" />
          </div>
          <el-button @click="close">{{ $t('common.cancel') }}</el-button>
          <el-button type="primary" :loading="busy" @click="handleSave">
            {{ isEdit ? $t('common.save') : $t('testcase.createCase') }}
          </el-button>
        </div>
      </div>
    </template>

    <TestCaseFormPanel
      v-if="panelMounted"
      ref="panelRef"
      :test-case-id="testCaseId"
      :project-id="projectId"
      :module-id="moduleId"
      :initial-modules="initialModules"
      :initial-modules-project-id="initialModulesProjectId"
      :initial-automation-links="initialAutomationLinks"
      @busy-change="busy = $event"
      @saved="handleSaved"
      @load-error="close"
    />
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import TestCaseFormPanel from '@/components/testcases/TestCaseFormPanel.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 为空表示新建用例
  testCaseId: { type: [Number, String], default: null },
  // 新建时的默认归属项目
  projectId: { type: [Number, String], default: null },
  // 新建时的默认模块，由用例列表当前选中的模块传入。
  moduleId: { type: [Number, String], default: null },
  // 列表页已加载的模块树，优先用于编辑表单，避免重复请求。
  initialModules: { type: Array, default: () => [] },
  initialModulesProjectId: { type: [Number, String], default: null },
  // 编辑详情页传入当前列表快照，用于弹窗顶部前后切换。
  navigationCases: { type: Array, default: () => [] },
  currentCaseIndex: { type: Number, default: -1 },
  initialAutomationLinks: { type: Array, default: null }
})
const emit = defineEmits(['update:modelValue', 'saved', 'navigate'])

const { t } = useI18n()
const panelRef = ref()
const busy = ref(false)
const panelMounted = ref(false)

const isEdit = computed(() => props.testCaseId !== null && props.testCaseId !== undefined && props.testCaseId !== '')
const hasNavigation = computed(() => isEdit.value && props.currentCaseIndex >= 0 && props.navigationCases.length > 0)
// 新建复用「新建用例」文案；编辑显示 ID 前缀和用例编号。
const dialogTitle = computed(() => (isEdit.value ? `ID ${props.testCaseId}` : t('testcase.newCase')))

const close = () => emit('update:modelValue', false)
const handleVisibility = visible => emit('update:modelValue', visible)
const handleClosed = () => { busy.value = false }

const handleSave = async () => {
  const ok = await panelRef.value?.submit()
  // 带导航的列表编辑保存后保持弹窗打开，便于继续切换和编辑其他用例。
  if (ok && !hasNavigation.value) close()
}

const handleSaved = (detail, mode) => emit('saved', detail, mode)
const navigateCase = offset => emit('navigate', offset)

// 首次打开后保留表单实例，关闭再打开或切换用例时只更新数据，避免整块编辑器闪烁重建。
watch(() => props.modelValue, visible => {
  if (visible) panelMounted.value = true
})
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
  font-size: 18px;
  font-weight: 600;
}

.tc-dialog-toolbar {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  justify-content: flex-end;
  gap: 8px;
}

.tc-case-navigation {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-right: 4px;
}

.tc-case-navigation__button {
  width: 34px;
  height: 34px;
  padding: 8px;
}

.tc-case-navigation__count {
  min-width: 52px;
  color: var(--el-text-color-secondary);
  text-align: center;
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
