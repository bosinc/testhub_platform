<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="page-title">{{ $t('testcase.detail') }}</h1>
      <div>
        <el-button @click="$router.back()">{{ $t('common.back') }}</el-button>
        <el-button type="primary" @click="editTestCase">{{ $t('common.edit') }}</el-button>
      </div>
    </div>

    <div class="card-container" v-if="testcase">
      <el-descriptions :column="2" border>
        <el-descriptions-item :label="$t('testcase.caseTitle')" :span="2">{{ testcase.title }}</el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.priority')">
          <el-tag :class="`priority-tag ${testcase.priority}`">{{ getPriorityText(testcase.priority) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.testType')">{{ getTypeText(testcase.test_type) }}</el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.project')">{{ testcase.project?.name || $t('testcase.noProject') }}</el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.relatedVersions')" :span="2">
          <div v-if="testcase.versions && testcase.versions.length > 0" class="version-tags">
            <el-tag
              v-for="version in testcase.versions"
              :key="version.id"
              size="small"
              :type="version.is_baseline ? 'warning' : 'info'"
              class="version-tag"
            >
              {{ version.name }}
            </el-tag>
          </div>
          <span v-else class="no-version">{{ $t('testcase.noVersion') }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.author')">{{ testcase.author?.username }}</el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.createdAt')" :span="2">{{ formatDate(testcase.created_at) }}</el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.caseDescription')" :span="2">{{ testcase.description || $t('testcase.noDescription') }}</el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.preconditions')" :span="2">
          <div class="text-content" v-html="testcase.preconditions || $t('testcase.none')"></div>
        </el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.steps')" :span="2">
          <div class="text-content" v-html="testcase.steps || $t('testcase.none')"></div>
        </el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.expectedResult')" :span="2">
          <div class="text-content" v-html="testcase.expected_result || $t('testcase.none')"></div>
        </el-descriptions-item>
        <el-descriptions-item :label="$t('testcase.notes')" :span="2">
          <div v-if="safeNotes" class="notes-content" v-html="safeNotes" @mousedown.capture="previewNoteImage" @click="previewNoteImage" @dblclick="previewNoteImage"></div>
          <span v-else>{{ $t('testcase.none') }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <el-dialog v-model="imagePreviewVisible" title="图片预览" width="80%" append-to-body>
      <img v-if="previewImageUrl" class="image-preview" :src="previewImageUrl" alt="备注图片预览" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '@/utils/api'
import dayjs from 'dayjs'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const testcase = ref(null)
const imagePreviewVisible = ref(false)
const previewImageUrl = ref('')

// 详情页只渲染备注编辑器支持的标签和内嵌图片，避免直接渲染任意 HTML。
const safeNotes = computed(() => {
  const parsed = new DOMParser().parseFromString(testcase.value?.notes || '', 'text/html')
  const allowedTags = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'UL', 'OL', 'LI', 'P', 'H2', 'H3', 'BR', 'FONT', 'IMG'])
  const cleanNode = (node) => {
    for (const child of [...node.childNodes]) {
      if (child.nodeType !== Node.ELEMENT_NODE) {
        if (child.nodeType !== Node.TEXT_NODE) child.remove()
        continue
      }
      if (!allowedTags.has(child.tagName)) {
        cleanNode(child)
        child.replaceWith(...child.childNodes)
        continue
      }
      [...child.attributes].forEach(attribute => {
        const isImageSource = child.tagName === 'IMG' && attribute.name === 'src' && (
          /^data:image\/(png|jpeg|gif|webp);base64,/i.test(attribute.value) || /^\/media\//.test(attribute.value)
        )
        const isFontSize = child.tagName === 'FONT' && attribute.name === 'size' && /^[1-7]$/.test(attribute.value)
        if (!isImageSource && !isFontSize) child.removeAttribute(attribute.name)
      })
      if (child.tagName === 'IMG' && !child.hasAttribute('src')) child.remove()
      else cleanNode(child)
    }
  }
  cleanNode(parsed.body)
  return parsed.body.innerHTML
})

const previewNoteImage = (event) => {
  const image = event.target?.closest?.('img')
  if (!image) return
  previewImageUrl.value = image.src
  imagePreviewVisible.value = true
}

const fetchTestCase = async () => {
  try {
    const response = await api.get(`/testcases/${route.params.id}/`)
    testcase.value = response.data
  } catch (error) {
    ElMessage.error(t('testcase.fetchDetailFailed'))
  }
}

const editTestCase = () => {
  router.push(`/ai-generation/testcases/${route.params.id}/edit`)
}

const getPriorityText = (priority) => {
  const textMap = {
    low: t('testcase.low'),
    medium: t('testcase.medium'),
    high: t('testcase.high'),
    critical: t('testcase.critical')
  }
  return textMap[priority] || priority
}

const getTypeText = (type) => {
  const textMap = {
    functional: t('testcase.functional'),
    integration: t('testcase.integration'),
    api: t('testcase.api'),
    ui: t('testcase.ui'),
    performance: t('testcase.performance'),
    security: t('testcase.security')
  }
  return textMap[type] || '-'
}

const formatDate = (dateString) => {
  return dayjs(dateString).format('YYYY-MM-DD HH:mm')
}

onMounted(() => {
  fetchTestCase()
})
</script>

<style lang="scss" scoped>
.priority-tag {
  &.low { color: #67c23a; }
  &.medium { color: #e6a23c; }
  &.high { color: #f56c6c; }
  &.critical { color: #f56c6c; font-weight: bold; }
}

.version-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  
  .version-tag {
    margin: 0;
  }
}

.no-version {
  color: #909399;
  font-size: 14px;
  font-style: italic;
}

/* 操作步骤 / 预期结果 / 前置条件：库里存的是纯文本 + 换行符（导入的数据也可能含 <br>），
   必须保留换行，否则多行内容会被折叠成一行 */
.text-content {
  white-space: pre-wrap;
  line-height: 1.6;
  color: #303133;
  font-family: inherit;
  word-break: break-word;
}

.notes-content {
  line-height: 1.6;
  color: #303133;
  word-break: break-word;

  :deep(img) {
    display: block;
    max-width: min(480px, 100%);
    max-height: 320px;
    height: auto;
    object-fit: contain;
    margin: 8px 0;
    cursor: zoom-in;
  }
}

.image-preview {
  display: block;
  max-width: 100%;
  max-height: 75vh;
  margin: 0 auto;
  object-fit: contain;
}
</style>
