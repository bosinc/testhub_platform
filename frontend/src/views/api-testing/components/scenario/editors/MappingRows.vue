<template>
  <div class="mapping-rows">
    <div class="mapping-header">
      <span class="mapping-title">{{ title }}</span>
      <el-button size="small" link type="primary" :icon="Plus" @click="addRow">
        {{ $t('apiTesting.scenarioEditor.addMapping') }}
      </el-button>
    </div>
    <div v-if="hint" class="mapping-hint">{{ hint }}</div>
    <div v-for="(row, index) in rows" :key="index" class="mapping-row">
      <el-input v-model="row.key" :placeholder="$t('apiTesting.scenarioEditor.mappingKey')" @change="commit" />
      <span class="mapping-equals">=</span>
      <el-input v-model="row.value" :placeholder="$t('apiTesting.scenarioEditor.mappingValue')" @change="commit" />
      <el-button link type="danger" :icon="Delete" @click="removeRow(index)" />
    </div>
    <div v-if="!rows.length" class="mapping-empty">{{ $t('apiTesting.noData') }}</div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Plus, Delete } from '@element-plus/icons-vue'

const props = defineProps({
  title: { type: String, default: '' },
  hint: { type: String, default: '' },
  modelValue: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['update:modelValue'])
const rows = ref([])
let syncing = false

watch(() => props.modelValue, value => {
  if (syncing) return
  rows.value = Object.entries(value || {}).map(([key, item]) => ({ key, value: typeof item === 'string' ? item : JSON.stringify(item) }))
}, { immediate: true, deep: true })

function commit() {
  const result = {}
  for (const row of rows.value) {
    const key = String(row.key || '').trim()
    if (key) result[key] = row.value
  }
  syncing = true
  emit('update:modelValue', result)
  Promise.resolve().then(() => { syncing = false })
}

function addRow() {
  rows.value.push({ key: '', value: '' })
}

function removeRow(index) {
  rows.value.splice(index, 1)
  commit()
}
</script>

<style scoped>
.mapping-rows { margin-top: 18px; }
.mapping-header { display: flex; align-items: center; justify-content: space-between; }
.mapping-title { font-size: 13px; font-weight: 600; color: #303133; }
.mapping-hint { font-size: 12px; color: #909399; margin: 4px 0 8px; }
.mapping-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.mapping-equals { color: #909399; }
.mapping-empty { font-size: 12px; color: #c0c4cc; padding: 4px 0; }
</style>
