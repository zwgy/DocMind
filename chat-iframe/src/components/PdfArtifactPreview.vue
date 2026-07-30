<script setup lang="ts">
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-vue-next'
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask
} from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

GlobalWorkerOptions.workerSrc = pdfWorkerUrl

const props = defineProps<{
  src: string
}>()

const viewportEl = ref<HTMLElement | null>(null)
const pageNumbers = ref<number[]>([])
const loading = ref(true)
const errorMessage = ref('')
const zoom = ref(1)
const baseScale = ref(1)
const canvases = new Map<number, HTMLCanvasElement>()

let documentTask: PDFDocumentLoadingTask | null = null
let pdfDocument: PDFDocumentProxy | null = null
let renderTasks: RenderTask[] = []
let renderGeneration = 0
let resizeObserver: ResizeObserver | null = null
let resizeTimer: number | null = null
let mounted = false

const zoomPercent = computed(() => `${Math.round(zoom.value * 100)}%`)
const canZoomOut = computed(() => zoom.value > 0.5)
const canZoomIn = computed(() => zoom.value < 2)

function bindCanvas(pageNumber: number, element: Element | null) {
  if (element instanceof HTMLCanvasElement) canvases.set(pageNumber, element)
  else canvases.delete(pageNumber)
}

function cancelRendering() {
  renderGeneration += 1
  for (const task of renderTasks) task.cancel()
  renderTasks = []
}

async function destroyDocument() {
  cancelRendering()
  const activeTask = documentTask
  const activeDocument = pdfDocument
  documentTask = null
  pdfDocument = null
  if (activeTask) await activeTask.destroy()
  else await activeDocument?.destroy()
}

async function fitToViewport() {
  if (!pdfDocument || !viewportEl.value) return
  const firstPage = await pdfDocument.getPage(1)
  const naturalViewport = firstPage.getViewport({ scale: 1 })
  const availableWidth = Math.max(220, viewportEl.value.clientWidth - 32)
  baseScale.value = Math.min(1.5, Math.max(0.35, availableWidth / naturalViewport.width))
}

async function renderPages() {
  if (!pdfDocument) return
  cancelRendering()
  const generation = renderGeneration
  errorMessage.value = ''

  try {
    for (const pageNumber of pageNumbers.value) {
      if (generation !== renderGeneration) return
      const canvas = canvases.get(pageNumber)
      if (!canvas) continue

      const page = await pdfDocument.getPage(pageNumber)
      const viewport = page.getViewport({ scale: baseScale.value * zoom.value })
      const outputScale = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.floor(viewport.width * outputScale)
      canvas.height = Math.floor(viewport.height * outputScale)
      canvas.style.width = `${Math.floor(viewport.width)}px`
      canvas.style.height = `${Math.floor(viewport.height)}px`

      const task = page.render({
        canvas,
        viewport,
        transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0]
      })
      renderTasks.push(task)
      await task.promise
    }
  } catch (error) {
    if (generation !== renderGeneration) return
    errorMessage.value = error instanceof Error ? error.message : 'PDF 预览渲染失败'
  } finally {
    if (generation === renderGeneration) loading.value = false
  }
}

async function loadPdf() {
  loading.value = true
  errorMessage.value = ''
  zoom.value = 1
  pageNumbers.value = []
  canvases.clear()
  await destroyDocument()

  try {
    documentTask = getDocument({ url: props.src })
    pdfDocument = await documentTask.promise
    pageNumbers.value = Array.from({ length: pdfDocument.numPages }, (_, index) => index + 1)
    await nextTick()
    await fitToViewport()
    await renderPages()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'PDF 预览加载失败'
    loading.value = false
  }
}

function changeZoom(step: number) {
  zoom.value = Math.min(2, Math.max(0.5, Number((zoom.value + step).toFixed(2))))
  void renderPages()
}

function resetZoom() {
  zoom.value = 1
  void renderPages()
}

function scheduleResizeRender() {
  if (!pdfDocument) return
  if (resizeTimer) window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(async () => {
    await fitToViewport()
    await renderPages()
  }, 120)
}

watch(
  () => props.src,
  () => {
    if (mounted) void loadPdf()
  }
)

onMounted(() => {
  mounted = true
  if (viewportEl.value) {
    resizeObserver = new ResizeObserver(scheduleResizeRender)
    resizeObserver.observe(viewportEl.value)
  }
  void loadPdf()
})

onUnmounted(() => {
  mounted = false
  resizeObserver?.disconnect()
  if (resizeTimer) window.clearTimeout(resizeTimer)
  void destroyDocument()
})
</script>

<template>
  <section class="pdf-artifact-preview">
    <header class="pdf-artifact-toolbar">
      <span>{{ pageNumbers.length }} 页</span>
      <div>
        <button
          type="button"
          title="缩小"
          :disabled="!canZoomOut"
          @click="changeZoom(-0.1)"
        >
          <ZoomOut :size="16" />
        </button>
        <span class="pdf-artifact-zoom">{{ zoomPercent }}</span>
        <button type="button" title="放大" :disabled="!canZoomIn" @click="changeZoom(0.1)">
          <ZoomIn :size="16" />
        </button>
        <button type="button" title="适合窗口" @click="resetZoom">
          <Maximize2 :size="16" />
        </button>
      </div>
    </header>

    <div ref="viewportEl" class="pdf-artifact-viewport">
      <p v-if="loading" class="pdf-artifact-status">正在加载预览...</p>
      <p v-else-if="errorMessage" class="pdf-artifact-status is-error">{{ errorMessage }}</p>
      <canvas
        v-for="pageNumber in pageNumbers"
        v-show="!errorMessage"
        :key="pageNumber"
        :ref="(element) => bindCanvas(pageNumber, element as Element | null)"
        :aria-label="`第 ${pageNumber} 页`"
      />
    </div>
  </section>
</template>

<style scoped>
.pdf-artifact-preview {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  background: var(--gray-100);
}

.pdf-artifact-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 42px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--gray-200);
  color: var(--gray-700);
  background: var(--gray-0);
  font-size: 12px;
}

.pdf-artifact-toolbar > div {
  display: flex;
  align-items: center;
  gap: 3px;
}

.pdf-artifact-toolbar button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 6px;
  color: var(--gray-700);
  background: transparent;
  cursor: pointer;
}

.pdf-artifact-toolbar button:hover,
.pdf-artifact-toolbar button:focus-visible {
  color: var(--gray-900);
  background: var(--gray-100);
}

.pdf-artifact-toolbar button:disabled {
  opacity: 0.4;
  cursor: default;
}

.pdf-artifact-zoom {
  width: 46px;
  color: var(--gray-700);
  text-align: center;
}

.pdf-artifact-viewport {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  min-width: 0;
  min-height: 0;
  padding: 16px;
  overflow: auto;
}

.pdf-artifact-viewport canvas {
  display: block;
  flex: 0 0 auto;
  max-width: none;
  background: var(--gray-0);
  box-shadow: 0 2px 10px rgb(15 23 42 / 16%);
}

.pdf-artifact-status {
  margin: auto;
  color: var(--gray-600);
  font-size: 13px;
}

.pdf-artifact-status.is-error {
  color: var(--color-error-700);
}
</style>
