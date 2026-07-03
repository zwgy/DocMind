<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Maximize2, Minimize2, RotateCcw, X } from 'lucide-vue-next'
import { queryIncomingDocumentExtractions } from '@/apis/incoming-documents'
import IncomingDocumentPanel from '@/components/IncomingDocumentPanel.vue'
import PageFileSelector from '@/components/PageFileSelector.vue'
import { useIframeBridge } from '@/composables/useIframeBridge'
import { useIframeContextStore } from '@/stores/iframe-context'
import type { ExtractionResult } from '@/types'

const context = useIframeContextStore()
const { notifyClose, notifyMaximize, notifyMinimize, notifyRestore } = useIframeBridge()
const loading = ref(false)
const error = ref('')
const results = ref<Record<string, ExtractionResult>>({})

const selectedFile = computed(() => context.selectedFile)
const selectedResult = computed(() =>
  selectedFile.value ? results.value[selectedFile.value.id] || null : null
)

async function refreshExtraction() {
  if (!selectedFile.value) return
  loading.value = true
  error.value = ''
  try {
    const response = await queryIncomingDocumentExtractions([selectedFile.value], context.config.token)
    const item = response.items?.[0] || null
    if (item) {
      results.value = { ...results.value, [selectedFile.value.id]: item }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '查询失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => selectedFile.value?.id,
  () => refreshExtraction(),
  { immediate: true }
)

onMounted(() => {
  if (!context.files.length) refreshExtraction()
})
</script>

<template>
  <main class="chat-shell">
    <header class="chat-header">
      <div>
        <strong>docMind 文档助手</strong>
        <span>{{ context.pageContent.title || '来文结构化结果' }}</span>
      </div>
      <nav class="window-actions" aria-label="窗口控制">
        <button type="button" title="最小化" @click="notifyMinimize">
          <Minimize2 :size="16" />
        </button>
        <button type="button" title="恢复" @click="notifyRestore">
          <RotateCcw :size="16" />
        </button>
        <button type="button" title="最大化" @click="notifyMaximize">
          <Maximize2 :size="16" />
        </button>
        <button type="button" title="关闭" @click="notifyClose">
          <X :size="16" />
        </button>
      </nav>
    </header>

    <section class="chat-body">
      <PageFileSelector
        :files="context.files"
        :selected-file-id="context.selectedFileId"
        @select="context.selectFile"
      />
      <IncomingDocumentPanel
        :file="selectedFile"
        :result="selectedResult"
        :loading="loading"
        :error="error"
        @refresh="refreshExtraction"
      />
    </section>
  </main>
</template>
