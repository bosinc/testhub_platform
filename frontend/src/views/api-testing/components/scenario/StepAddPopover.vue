<template>
  <div class="step-add-popover">
    <!-- 支持子步骤的步骤先选择插入位置，悬停后在二级浮层中展示添加菜单。 -->
    <template v-if="choosing">
      <el-popover
        v-for="item in choices"
        :key="item.mode"
        trigger="hover"
        placement="right-start"
        :width="340"
        :show-after="80"
        :hide-after="120"
        :offset="8"
        :popper-options="submenuPopperOptions"
        popper-class="step-add-submenu-popper"
      >
        <template #reference>
          <button type="button" class="choice-row">
            <span>{{ $t(item.label) }}</span>
            <el-icon><ArrowRight /></el-icon>
          </button>
        </template>
        <AddStepMenu @select="key => pick(key, item.mode)" />
      </el-popover>
    </template>
    <template v-else>
      <AddStepMenu @select="pick" />
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import AddStepMenu from './AddStepMenu.vue'

const props = defineProps({ canHaveChildren: { type: Boolean, default: false } })
const emit = defineEmits(['add', 'close'])

// 支持子步骤时展示插入位置选择页，否则直接展示步骤类型菜单。
const choosing = computed(() => props.canHaveChildren)

// 二级菜单优先向右展开，空间不足时由 Popper 翻转到左侧或上下方。
const submenuPopperOptions = {
  modifiers: [
    {
      name: 'flip',
      options: {
        fallbackPlacements: ['left-start', 'right-end', 'left-end', 'bottom-start', 'top-start']
      }
    },
    {
      name: 'preventOverflow',
      options: { padding: 12, altAxis: true }
    }
  ]
}

const choices = [
  { mode: 'after', label: 'apiTesting.scenarioEditor.addSiblingStep' },
  { mode: 'inside', label: 'apiTesting.scenarioEditor.addChildStep' }
]

function pick(key, selectedMode = 'after') {
  // 叶子步骤（不支持子步骤）只能添加相邻步骤。
  emit('add', { key, mode: props.canHaveChildren ? selectedMode : 'after' })
  emit('close')
}
</script>

<style scoped>
/* 宽度跟随一级 popper 内容区，避免固定宽度撑破浮层。 */
.step-add-popover { width: 100%; max-height: min(70vh, calc(100vh - 24px)); overflow-y: auto; }
.choice-row {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  width: 100%; padding: 9px 12px; border: none; border-radius: 8px;
  background: transparent; color: #303133; font-size: 14px; cursor: pointer;
  text-align: left; box-sizing: border-box;
}
.choice-row:hover { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }

/* Teleported 的二级 popper 不在当前组件 DOM 下，需要用全局选择器限制其高度。 */
:global(.step-add-submenu-popper .step-add-popover) {
  max-height: min(70vh, calc(100vh - 24px));
}
</style>
