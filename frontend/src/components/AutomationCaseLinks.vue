<template>
  <div class="automation-case-links">
    <div
      v-for="link in links"
      :key="link.id"
      class="automation-linked-item"
    >
      <el-tooltip
        :content="getAutomationLinkTooltip(link)"
        placement="top"
      >
        <el-button
          link
          class="automation-link-icon"
          :disabled="!link.target_exists"
          :aria-label="getAutomationLinkTooltip(link)"
          @click="link.target_exists && router.push(link.detail_url)"
        >
          <el-icon :class="typeIconClass(link.type)">
            <Monitor v-if="link.type === 'ui_test_case'" />
            <Link v-else />
          </el-icon>
        </el-button>
      </el-tooltip>

      <el-button
        v-if="allowUnlink"
        link
        type="danger"
        size="small"
        class="automation-unlink-button"
        :aria-label="$t('testcase.unlinkAutomation')"
        @click="removeLink(link)"
      >
        <el-icon><Close /></el-icon>
      </el-button>
    </div>

    <el-tooltip
      v-if="!testCaseId"
      :content="$t('testcase.saveBeforeLinkingAutomation')"
      placement="top"
    >
      <el-button link type="primary" disabled :aria-label="$t('testcase.automation')">
        <el-icon><Plus /></el-icon>
      </el-button>
    </el-tooltip>

    <el-popover
      v-else-if="availableTypes.length"
      v-model:visible="automationMenuVisible"
      trigger="click"
      placement="bottom-start"
      :width="300"
      :teleported="true"
      popper-class="automation-case-menu-popper"
      @show="createMenuExpanded = false"
    >
      <template #reference>
        <el-button link type="primary" :aria-label="$t('testcase.automation')">
          <el-icon><Plus /></el-icon>
        </el-button>
      </template>

      <div class="automation-case-menu">
        <div class="automation-menu-heading">{{ $t('testcase.automation') }}</div>
        <div class="automation-menu-section">
          <el-button class="automation-menu-item" plain @click="createMenuExpanded = !createMenuExpanded">
            <span>{{ $t('testcase.createAutomation') }}</span>
            <el-icon :class="['automation-menu-expand-icon', { 'is-expanded': createMenuExpanded }]">
              <ArrowDown />
            </el-icon>
          </el-button>
          <div v-if="createMenuExpanded" class="automation-menu-subitems">
            <el-button
              v-for="type in availableTypes"
              :key="`create-${type.value}`"
              class="automation-menu-item"
              plain
              @click="selectCreateType(type.value)"
            >
              <el-icon :class="`automation-menu-icon automation-menu-icon-${type.value}`">
                <Monitor v-if="type.value === 'ui_test_case'" />
                <Link v-else />
              </el-icon>
              <span>{{ $t(type.labelKey) }}</span>
            </el-button>
          </div>
          <el-button class="automation-menu-item" plain @click="selectLinkAction">
            <span>{{ $t('testcase.linkAutomation') }}</span>
          </el-button>
        </div>
      </div>
    </el-popover>

    <el-dialog
      v-model="linkDialogVisible"
      :title="$t('testcase.linkAutomationTitle')"
      width="min(760px, calc(100vw - 32px))"
      append-to-body
      destroy-on-close
      @closed="resetLinkDialog"
    >
      <div class="automation-dialog-filters">
        <el-select v-model="automationType" style="width: 220px" @change="loadOptions">
          <el-option
            v-for="type in availableTypes"
            :key="type.value"
            :label="$t(type.labelKey)"
            :value="type.value"
          />
        </el-select>
        <el-input
          v-model="automationSearch"
          clearable
          :placeholder="$t('testcase.searchAutomationCases')"
        />
      </div>
      <el-tree
        ref="automationTreeRef"
        v-loading="optionsLoading"
        :data="optionTree"
        :props="{ label: 'name', children: 'children' }"
        :filter-node-method="filterAutomationNode"
        node-key="tree_key"
        highlight-current
        :default-expand-all="false"
        :empty-text="$t('testcase.noAutomationCases')"
        class="automation-options-tree"
        @node-click="selectTreeNode"
      >
        <template #default="{ data }">
          <span class="automation-tree-node">
            <el-icon><Folder v-if="data.type === 'folder'" /><Document v-else /></el-icon>
            <span>{{ data.name }}</span>
          </span>
        </template>
      </el-tree>
      <template #footer>
        <el-button @click="linkDialogVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="linkSaving" :disabled="!selectedOption" @click="createLink">
          {{ $t('testcase.confirmLinkAutomation') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, Close, Document, Folder, Link, Monitor, Plus } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import {
  createTestCaseAutomationLink,
  deleteTestCaseAutomationLink,
  getTestCaseAutomationLinks,
  getTestCaseAutomationOptions
} from '@/api/testcases'

const props = defineProps({
  testCaseId: { type: [Number, String], default: null },
  projectId: { type: [Number, String], default: null },
  links: { type: Array, default: () => [] },
  allowUnlink: { type: Boolean, default: false },
  loadLinksOnMount: { type: Boolean, default: false }
})

const emit = defineEmits(['update:links'])
const { t } = useI18n()
const router = useRouter()
const automationTypes = [
  { value: 'ui_test_case', labelKey: 'testcase.uiAutomationCase', path: '/ui-automation/test-cases' },
  { value: 'api_scenario_case', labelKey: 'testcase.apiScenarioCase', path: '/api-testing/automation' }
]
const linkedTypes = computed(() => new Set(props.links.map(link => link.type)))
const availableTypes = computed(() => automationTypes.filter(type => !linkedTypes.value.has(type.value)))
const automationMenuVisible = ref(false)
const createMenuExpanded = ref(false)
const linkDialogVisible = ref(false)
const automationType = ref('')
const automationSearch = ref('')
const automationOptions = ref([])
const automationFolders = ref([])
const automationTreeRef = ref(null)
const optionsLoading = ref(false)
const selectedOption = ref(null)
const linkSaving = ref(false)

const optionTree = computed(() => {
  const roots = []
  const folders = new Map(automationFolders.value.map(folder => {
    const treeKey = `folder:${folder.project_id}:${folder.id}`
    return [treeKey, { ...folder, tree_key: treeKey, type: 'folder', children: [] }]
  }))

  automationFolders.value.forEach(folder => {
    const node = folders.get(`folder:${folder.project_id}:${folder.id}`)
    const parent = folder.parent_id
      ? folders.get(`folder:${folder.project_id}:${folder.parent_id}`)
      : null
    ;(parent ? parent.children : roots).push(node)
  })

  automationOptions.value.forEach(option => {
    const node = { ...option, tree_key: `case:${option.project_id}:${option.id}`, type: 'case' }
    const parent = option.folder_id
      ? folders.get(`folder:${option.project_id}:${option.folder_id}`)
      : null
    ;(parent ? parent.children : roots).push(node)
  })

  return roots
})

const loadLinks = async () => {
  if (!props.testCaseId) return
  try {
    const { data } = await getTestCaseAutomationLinks(props.testCaseId)
    emit('update:links', data)
  } catch {
    ElMessage.error(t('testcase.loadAutomationCasesFailed'))
  }
}

watch(
  () => [props.testCaseId, props.loadLinksOnMount],
  ([testCaseId, shouldLoad]) => {
    if (testCaseId && shouldLoad) loadLinks()
  },
  { immediate: true }
)
onActivated(() => {
  if (props.loadLinksOnMount && props.testCaseId) loadLinks()
})

const refreshLinksOnFocus = () => {
  if (document.visibilityState === 'visible' && props.loadLinksOnMount && props.testCaseId) loadLinks()
}

onMounted(() => window.addEventListener('focus', refreshLinksOnFocus))
onUnmounted(() => window.removeEventListener('focus', refreshLinksOnFocus))

const openLinkDialog = async () => {
  automationType.value = availableTypes.value[0]?.value || ''
  automationSearch.value = ''
  selectedOption.value = null
  linkDialogVisible.value = true
  if (automationType.value) await loadOptions()
}

const selectCreateType = type => {
  automationMenuVisible.value = false
  navigateToCreate(type)
}

const selectLinkAction = async () => {
  automationMenuVisible.value = false
  await openLinkDialog()
}

const typeIconClass = type => type === 'ui_test_case'
  ? 'automation-type-icon-ui'
  : 'automation-type-icon-api'

const getAutomationLinkTooltip = link => {
  if (!link.target_exists) return t('testcase.unavailableAutomationTarget')
  const type = automationTypes.find(item => item.value === link.type)
  return t('testcase.automationLinkTooltip', {
    type: type ? t(type.labelKey) : link.type,
    name: link.target_name
  })
}

const loadOptions = async () => {
  if (!props.testCaseId || !automationType.value) return
  optionsLoading.value = true
  selectedOption.value = null
  automationOptions.value = []
  automationFolders.value = []
  try {
    let page = 1
    let hasNext = true
    while (hasNext) {
      const { data } = await getTestCaseAutomationOptions(props.testCaseId, {
        type: automationType.value,
        page,
        page_size: 100
      })
      automationOptions.value.push(...(data.results || []))
      if (page === 1) automationFolders.value = data.folders || []
      hasNext = Boolean(data.next)
      page += 1
    }
  } catch (error) {
    automationOptions.value = []
    automationFolders.value = []
    ElMessage.error(error.response?.data?.detail || t('testcase.loadAutomationCasesFailed'))
  } finally {
    optionsLoading.value = false
    await nextTick()
    await filterOptions()
  }
}

const filterAutomationNode = (query, data) => {
  if (!query) return true
  const normalizedQuery = query.toLowerCase()
  const matchesCase = node => node.type === 'case'
    ? String(node.name || '').toLowerCase().includes(normalizedQuery)
    : (node.children || []).some(matchesCase)
  return matchesCase(data)
}

const filterOptions = async () => {
  const tree = automationTreeRef.value
  if (!tree) return

  const query = automationSearch.value.trim()
  tree.filter(query)
  await nextTick()

  const hasMatchingCase = node => node.type === 'case'
    ? String(node.name || '').toLowerCase().includes(query.toLowerCase())
    : (node.children || []).some(hasMatchingCase)

  automationFolders.value.forEach(folder => {
    const treeKey = `folder:${folder.project_id}:${folder.id}`
    const treeNode = tree.getNode(treeKey)
    if (!treeNode) return
    if (query && hasMatchingCase(treeNode.data)) treeNode.expand()
    else treeNode.collapse()
  })
}

watch(automationSearch, filterOptions)

const selectTreeNode = data => {
  if (data.type === 'case') {
    selectedOption.value = data
    return
  }
  nextTick(() => automationTreeRef.value?.setCurrentKey(selectedOption.value?.tree_key || null))
}

const createLink = async () => {
  if (!selectedOption.value) return
  linkSaving.value = true
  try {
    const { data } = await createTestCaseAutomationLink(props.testCaseId, {
      type: automationType.value,
      object_id: selectedOption.value.id
    })
    emit('update:links', [...props.links, data])
    linkDialogVisible.value = false
    ElMessage.success(t('testcase.automationLinked'))
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.response?.data?.object_id || t('testcase.linkAutomationFailed'))
  } finally {
    linkSaving.value = false
  }
}

const removeLink = async link => {
  try {
    await deleteTestCaseAutomationLink(props.testCaseId, link.id)
    emit('update:links', props.links.filter(item => item.id !== link.id))
    ElMessage.success(t('testcase.automationUnlinked'))
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || t('testcase.unlinkAutomationFailed'))
  }
}

const navigateToCreate = type => {
  const target = automationTypes.find(item => item.value === type)
  if (!target || !props.testCaseId) return
  const targetRoute = router.resolve({
    path: target.path,
    query: {
      automation_for_test_case_id: props.testCaseId,
      workspace_project_id: props.projectId
    }
  })
  window.open(targetRoute.href, '_blank', 'noopener,noreferrer')
}

const resetLinkDialog = () => {
  automationOptions.value = []
  automationFolders.value = []
  selectedOption.value = null
}
</script>

<style scoped>
.automation-case-links {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  white-space: nowrap;
}

.automation-linked-item {
  display: inline-flex;
  align-items: center;
  gap: 0;
}

.automation-link-icon {
  padding: 3px;
  font-size: 16px;
}

.automation-unlink-button {
  width: 14px;
  height: 16px;
  margin-left: -2px;
  padding: 0;
  font-size: 11px;
}

.automation-case-menu {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.automation-menu-heading {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.automation-menu-section {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.automation-menu-subitems {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-left: 12px;
}

.automation-menu-item.el-button {
  display: flex;
  justify-content: flex-start;
  width: 100%;
  height: 32px;
  margin: 0;
  padding: 0 10px;
  border-color: var(--el-border-color-lighter);
  color: var(--el-text-color-primary);
  background: var(--el-bg-color);
}

.automation-menu-item.el-button:hover {
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}

.automation-menu-icon {
  margin-right: 8px;
  font-size: 16px;
}

.automation-menu-icon-ui_test_case {
  color: #e6a23c;
}

.automation-menu-icon-api_scenario_case {
  color: #67c23a;
}

.automation-type-icon-ui {
  color: #e6a23c;
}

.automation-type-icon-api {
  color: #67c23a;
}

.automation-menu-expand-icon {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  transition: transform 0.2s;
}

.automation-menu-expand-icon.is-expanded {
  transform: rotate(180deg);
}

.automation-dialog-filters {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.automation-options-tree {
  height: 360px;
  overflow-y: auto;
}

.automation-tree-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
</style>
