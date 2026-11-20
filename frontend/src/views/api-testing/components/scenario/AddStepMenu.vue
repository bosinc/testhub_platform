<template>
  <div class="add-step-menu">
    <div class="menu-group">
      <div class="menu-group-title">{{ $t('apiTesting.scenarioEditor.groupRequest') }}</div>
      <button
        v-for="item in requestItems"
        :key="item.key"
        class="menu-row"
        type="button"
        @click="choose(item.key)"
      >
        <span class="menu-icon" :class="item.tone">
          <el-icon><component :is="item.icon" /></el-icon>
        </span>
        <span class="menu-label">{{ $t(item.label) }}</span>
      </button>
    </div>

    <div class="menu-group">
      <div class="menu-group-title">{{ $t('apiTesting.scenarioEditor.groupOther') }}</div>
      <div class="menu-grid">
        <button
          v-for="item in otherItems"
          :key="item.key"
          class="menu-card"
          type="button"
          @click="choose(item.key)"
        >
          <span class="menu-icon" :class="item.tone">
            <el-icon><component :is="item.icon" /></el-icon>
          </span>
          <span class="menu-label">{{ $t(item.label) }}</span>
        </button>
      </div>
    </div>

    <div class="menu-group">
      <div class="menu-group-title">{{ $t('apiTesting.scenarioEditor.groupScenario') }}</div>
      <button
        v-for="item in scenarioItems"
        :key="item.key"
        class="menu-row"
        type="button"
        @click="choose(item.key)"
      >
        <span class="menu-icon" :class="item.tone">
          <el-icon><component :is="item.icon" /></el-icon>
        </span>
        <span class="menu-label">{{ $t(item.label) }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { Link, Document, MagicStick, Folder, BottomLeft, Refresh, RefreshRight, Timer, Files, Download } from '@element-plus/icons-vue'

const emit = defineEmits(['select'])

// 菜单既被底部“添加步骤”使用，也被步骤行内的“+”浮层使用，
// 统一用 select 事件上报被点击的步骤类型。
function choose(key) {
  emit('select', key)
}
// 方案 5.1：不提供“从接口导入”“从接口调试记录导入”和“数据库操作”。
const requestItems = [
  { key: 'import-request', icon: Document, tone: 'warning', label: 'apiTesting.scenarioEditor.addFromApiRequest' },
  { key: 'http', icon: Link, tone: 'primary', label: 'apiTesting.scenarioEditor.addHttp' },
  { key: 'curl', icon: MagicStick, tone: 'info', label: 'apiTesting.scenarioEditor.addFromCurl' }
]

const otherItems = [
  { key: 'group', icon: Folder, tone: 'success', label: 'apiTesting.scenarioEditor.addGroup' },
  { key: 'condition', icon: BottomLeft, tone: 'danger', label: 'apiTesting.scenarioEditor.addCondition' },
  { key: 'foreach', icon: Refresh, tone: 'warning', label: 'apiTesting.scenarioEditor.addForEach' },
  { key: 'for', icon: RefreshRight, tone: 'warning', label: 'apiTesting.scenarioEditor.addFor' },
  { key: 'script', icon: Document, tone: 'info', label: 'apiTesting.scenarioEditor.addScript' },
  { key: 'wait', icon: Timer, tone: 'primary', label: 'apiTesting.scenarioEditor.addWait' }
]

const scenarioItems = [
  { key: 'import-scenario', icon: Download, tone: 'success', label: 'apiTesting.scenarioEditor.addFromScenario' },
  { key: 'scenario-ref', icon: Files, tone: 'primary', label: 'apiTesting.scenarioEditor.addScenarioRef' }
]

</script>

<style scoped>
/* 宽度跟随浮层本身，避免固定宽度撑破 popper 的内边距。 */
.add-step-menu { width: 100%; }
.menu-group + .menu-group { margin-top: 14px; }
.menu-group-title { font-size: 12px; color: #909399; margin-bottom: 8px; }
.menu-row, .menu-card {
  display: flex; align-items: center; gap: 8px; width: 100%;
  border: 1px solid #ebeef5; background: #fff; border-radius: 8px;
  padding: 8px 10px; cursor: pointer; color: #303133; font-size: 13px;
  transition: border-color .15s, background .15s, color .15s; text-align: left;
}
.menu-row + .menu-row { margin-top: 6px; }
.menu-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.menu-row:hover, .menu-card:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.menu-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 6px; font-size: 13px; flex: none;
}
.menu-icon.primary { color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.menu-icon.success { color: #67c23a; background: #f0f9eb; }
.menu-icon.warning { color: #e6a23c; background: #fdf6ec; }
.menu-icon.danger { color: #f56c6c; background: #fef0f0; }
.menu-icon.info { color: #909399; background: #f4f4f5; }
.menu-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
