<template>
  <div ref="containerRef" class="resizable-split-pane" :class="{ 'resize-second': resizeTarget === 'second' }">
    <div class="split-pane-first" :style="firstPaneStyle">
      <slot name="first" />
    </div>

    <div
      class="split-pane-divider"
      :class="{ 'is-dragging': dragging }"
      role="separator"
      aria-orientation="vertical"
      :aria-valuemin="minSize"
      :aria-valuemax="effectiveMaxSize"
      :aria-valuenow="width"
      tabindex="0"
      @pointerdown="startDragging"
      @keydown.left.prevent="adjustWidth(-keyboardStep)"
      @keydown.right.prevent="adjustWidth(keyboardStep)"
      @keydown.home.prevent="setWidth(minSize)"
      @keydown.end.prevent="setWidth(effectiveMaxSize)"
    >
      <span class="split-pane-divider-handle" aria-hidden="true" />
    </div>

    <div class="split-pane-second" :style="secondPaneStyle">
      <slot name="second" />
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: Number,
    default: 300
  },
  minSize: {
    type: Number,
    default: 220
  },
  maxSize: {
    type: Number,
    default: 560
  },
  // 按容器宽度的比例放宽上限（0~1），maxSize 作为保底值；传 0 则只按固定的 maxSize 限制
  maxRatio: {
    type: Number,
    default: 0.4
  },
  secondMinSize: {
    type: Number,
    default: 320
  },
  firstMinSize: {
    type: Number,
    default: 320
  },
  resizeTarget: {
    type: String,
    default: 'first',
    validator: (value) => ['first', 'second'].includes(value)
  },
  storageKey: {
    type: String,
    default: ''
  },
  keyboardStep: {
    type: Number,
    default: 16
  }
})

const emit = defineEmits(['update:modelValue', 'resize-start', 'resize', 'resize-end'])

const containerRef = ref(null)
const containerWidth = ref(0)
let resizeObserver
const width = ref(props.modelValue)
const dragging = ref(false)
const dragStartX = ref(0)
const dragStartWidth = ref(0)

const effectiveMaxSize = computed(() => {
  const availableWidth = containerWidth.value || Number.POSITIVE_INFINITY
  const otherMinSize = props.resizeTarget === 'first' ? props.secondMinSize : props.firstMinSize
  const ratioMaxSize = props.maxRatio > 0 && containerWidth.value
    ? Math.round(containerWidth.value * props.maxRatio)
    : 0
  const maxSize = Math.max(props.maxSize, ratioMaxSize)
  return Math.max(props.minSize, Math.min(maxSize, availableWidth - otherMinSize))
})

const clampWidth = (value) => Math.round(Math.max(props.minSize, Math.min(effectiveMaxSize.value, value)))

const setWidth = (value, persist = true) => {
  const nextWidth = clampWidth(value)
  width.value = nextWidth
  emit('update:modelValue', nextWidth)
  emit('resize', nextWidth)
  if (persist && props.storageKey) {
    localStorage.setItem(props.storageKey, String(nextWidth))
  }
}

const adjustWidth = (delta) => setWidth(width.value + delta)

const startDragging = (event) => {
  if (event.button !== 0) return
  dragging.value = true
  dragStartX.value = event.clientX
  dragStartWidth.value = width.value
  event.currentTarget.setPointerCapture?.(event.pointerId)
  document.body.classList.add('is-resizing-split-pane')
  emit('resize-start', width.value)
}

const handlePointerMove = (event) => {
  if (!dragging.value) return
  const containerRect = containerRef.value?.getBoundingClientRect()
  if (!containerRect) return
  const pointerOffset = event.clientX - containerRect.left
  const nextWidth = props.resizeTarget === 'first'
    ? dragStartWidth.value + event.clientX - dragStartX.value
    : containerRect.width - pointerOffset
  setWidth(nextWidth)
}

const stopDragging = () => {
  if (!dragging.value) return
  dragging.value = false
  document.body.classList.remove('is-resizing-split-pane')
  emit('resize-end', width.value)
}

const restoreWidth = () => {
  const savedWidth = props.storageKey && Number(localStorage.getItem(props.storageKey))
  if (Number.isFinite(savedWidth) && savedWidth > 0) {
    width.value = clampWidth(savedWidth)
  } else {
    width.value = clampWidth(props.modelValue)
  }
}

watch(() => props.modelValue, (value) => {
  if (!dragging.value && value !== width.value) width.value = clampWidth(value)
})

watch(effectiveMaxSize, () => {
  if (width.value > effectiveMaxSize.value) setWidth(width.value, false)
})

onMounted(() => {
  // 先量出容器宽度再恢复，否则按比例拖宽后保存的宽度会被截回 maxSize
  containerWidth.value = containerRef.value?.clientWidth || 0
  restoreWidth()
  resizeObserver = new ResizeObserver(([entry]) => {
    const measuredFirstTime = !containerWidth.value && entry.contentRect.width > 0
    containerWidth.value = entry.contentRect.width
    // 挂载时容器不可见（宽度为 0）的情况，首次量到宽度后再恢复一次
    if (measuredFirstTime && !dragging.value) restoreWidth()
  })
  if (containerRef.value) resizeObserver.observe(containerRef.value)
  window.addEventListener('pointermove', handlePointerMove)
  window.addEventListener('pointerup', stopDragging)
  window.addEventListener('pointercancel', stopDragging)
})

onBeforeUnmount(() => {
  stopDragging()
  resizeObserver?.disconnect()
  window.removeEventListener('pointermove', handlePointerMove)
  window.removeEventListener('pointerup', stopDragging)
  window.removeEventListener('pointercancel', stopDragging)
})

const firstPaneStyle = computed(() => props.resizeTarget === 'first' ? { width: `${width.value}px` } : {})
const secondPaneStyle = computed(() => props.resizeTarget === 'second' ? { width: `${width.value}px` } : {})
</script>

<style scoped>
.resizable-split-pane {
  display: flex;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.split-pane-first,
.split-pane-second {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.split-pane-first > :deep(*),
.split-pane-second > :deep(*) {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}

.split-pane-first {
  flex: 0 0 auto;
}

.split-pane-second {
  flex: 1 1 auto;
}

.resize-second .split-pane-first {
  flex: 1 1 auto;
}

.resize-second .split-pane-second {
  flex: 0 0 auto;
}

.split-pane-divider {
  position: relative;
  z-index: 2;
  flex: 0 0 8px;
  display: flex;
  align-items: stretch;
  justify-content: center;
  cursor: ew-resize;
  touch-action: none;
  user-select: none;
}

.split-pane-divider-handle {
  width: 1px;
  background: #e4e7ed;
  transition: width 0.15s, background-color 0.15s;
}

.split-pane-divider:hover .split-pane-divider-handle,
.split-pane-divider.is-dragging .split-pane-divider-handle {
  width: 2px;
  background: #409eff;
}

:global(body.is-resizing-split-pane) {
  cursor: ew-resize;
  user-select: none;
}
</style>
