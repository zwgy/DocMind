<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Maximize2, Menu, Minimize2, Minus, X } from 'lucide-vue-next'
import { ingestIncomingDocument, queryIncomingDocumentExtractions } from '@/apis/incoming-documents'
import ChatInput from '@/components/ChatInput.vue'
import ChatMessages from '@/components/ChatMessages.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import { useIframeBridge } from '@/composables/useIframeBridge'
import { useChatStore } from '@/stores/chat'
import { useIframeContextStore } from '@/stores/iframe-context'
import type { ExtractionResult, IncomingPageFile } from '@/types'

const context = useIframeContextStore()
const {
  notifyClose,
  notifyConversationCreated,
  notifyMaximize,
  notifyMessageSent,
  notifyMinimize,
  notifyRestore,
  notifyWindowDragEnd,
  notifyWindowDragMove,
  notifyWindowDragStart
} = useIframeBridge()
const chat = useChatStore()
const loading = ref(false)
const error = ref('')
const results = ref<Record<string, ExtractionResult>>({})
const showSidebar = ref(false)
const draggingWindow = ref(false)
const ingestingFileIds = new Set<string>()

const selectedFile = computed(() => context.selectedFile)

function openSidebar() {
  showSidebar.value = true
  void chat.refreshThreads(context.config.token, context.config.agentId, context.config.conversationScopeKey)
}

function cacheExtractionResults(files: IncomingPageFile[], items: ExtractionResult[] = []) {
  const next = { ...results.value }
  for (const [index, file] of files.entries()) {
    const item =
      items.find((candidate) => {
        const incomingId = candidate.incomingFileId || candidate.name
        return incomingId === file.id || incomingId === file.source_file_id || incomingId === file.source_url || incomingId === file.name
      }) || items[index]
    if (item) next[file.id] = item
  }
  results.value = next
}

async function refreshExtraction() {
  const file = selectedFile.value
  if (!file) {
    chat.setContextSummary({ file: null, result: null })
    return
  }
  if (context.config.authError) {
    chat.setContextSummary({ file, result: results.value[file.id] || null, error: context.config.authError })
    return
  }
  if (!context.config.token) {
    // 父页面可能先响应附件列表、后完成换票；这里等待 token 到达，避免无凭证请求把摘要卡片打成 401。
    chat.setContextSummary({ file, result: results.value[file.id] || null })
    return
  }
  const queryFiles = context.files.length ? context.files : [file]
  loading.value = true
  error.value = ''
  chat.setContextSummary({ file, result: results.value[file.id] || null, loading: true })
  try {
    let response = await queryIncomingDocumentExtractions(queryFiles, context.config.token)
    cacheExtractionResults(queryFiles, response.items || [])
    const pendingFiles = queryFiles.filter((file) => {
      const result = results.value[file.id]
      return result?.matchStatus === 'pending_sync' && file.source_url && !ingestingFileIds.has(file.id)
    })
    if (pendingFiles.length) {
      pendingFiles.forEach((file) => ingestingFileIds.add(file.id))
      await Promise.all(
        pendingFiles.map((file) =>
          ingestIncomingDocument(file, context.config.token, { source_system: context.config.source_system }).catch(() => null)
        )
      )
      response = await queryIncomingDocumentExtractions(queryFiles, context.config.token)
      cacheExtractionResults(queryFiles, response.items || [])
    }
    if (selectedFile.value?.id === file.id) chat.setContextSummary({ file, result: results.value[file.id] || null, loading: false })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '查询失败'
    if (selectedFile.value?.id === file.id) chat.setContextSummary({ file, result: results.value[file.id] || null, error: error.value })
  } finally {
    loading.value = false
  }
}

async function createChat() {
  try {
    const thread = await chat.newConversation(context.config.token, context.config.agentId, context.config.conversationScopeKey)
    showSidebar.value = false
    notifyConversationCreated({ conversationId: thread.id })
  } catch (err) {
    // 嵌入页可能先于父页面 token 注入完成，显式落到聊天错误态比未处理异常更利于定位接入问题。
    chat.error = err instanceof Error ? err.message : '创建会话失败'
  }
}

async function selectThread(threadId: string) {
  await chat.selectThread(threadId, context.config.token)
  showSidebar.value = false
}

function resumeVisibleThread() {
  if (document.visibilityState !== 'visible' || !context.config.token) return
  void chat.resumeActiveRun(chat.currentThreadId, context.config.token)
}

async function sendChat(payload: {
  text: string
  files: File[]
  imageFile?: File | null
  selectedPageFiles?: IncomingPageFile[]
}) {
  const selectedContextFile = payload.selectedPageFiles?.[0] || null
  const selectedContextResult = selectedContextFile ? results.value[selectedContextFile.id] || null : null
  const result = await chat.send(
    {
      text: payload.text,
      files: payload.files,
      imageFile: payload.imageFile,
      pageContent: context.pageContent,
      selectedFile: selectedContextFile,
      extractionResult: selectedContextResult,
      selectedPageFiles: payload.selectedPageFiles || [],
      extractionResults: results.value
    },
    context.config.token,
    context.config.agentId,
    context.config.conversationScopeKey
  )
  if (result) notifyMessageSent({ conversationId: result.threadId, messageId: result.messageId })
}

function windowDragPayload(event: PointerEvent) {
  return {
    clientX: event.clientX,
    clientY: event.clientY,
    screenX: event.screenX,
    screenY: event.screenY,
    pointerId: event.pointerId
  }
}

function moveWindowDrag(event: PointerEvent) {
  if (!draggingWindow.value) return
  notifyWindowDragMove(windowDragPayload(event))
}

function endWindowDrag() {
  if (!draggingWindow.value) return
  draggingWindow.value = false
  notifyWindowDragEnd()
  window.removeEventListener('pointermove', moveWindowDrag)
  window.removeEventListener('pointerup', endWindowDrag)
  window.removeEventListener('pointercancel', endWindowDrag)
}

function startWindowDrag(event: PointerEvent) {
  if (context.windowState !== 'normal') return
  const target = event.target as HTMLElement | null
  // 顶栏承担拖动热区，但窗口按钮必须保持点击语义，避免拖动和控制操作互相抢事件。
  if (target?.closest('button, a, input, textarea, select')) return
  draggingWindow.value = true
  notifyWindowDragStart(windowDragPayload(event))
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
  window.addEventListener('pointermove', moveWindowDrag)
  window.addEventListener('pointerup', endWindowDrag)
  window.addEventListener('pointercancel', endWindowDrag)
  event.preventDefault()
}

watch(
  () => selectedFile.value?.id,
  () => {
    chat.setContextSummary({
      file: selectedFile.value,
      result: selectedFile.value ? results.value[selectedFile.value.id] || null : null
    })
    void refreshExtraction()
  },
  { immediate: true }
)

watch(
  [() => context.config.token, () => context.config.agentId, () => context.config.conversationScopeKey, () => context.config.authError],
  () => {
    if (context.config.authError) {
      chat.error = context.config.authError
      if (selectedFile.value) {
        chat.setContextSummary({
          file: selectedFile.value,
          result: results.value[selectedFile.value.id] || null,
          error: context.config.authError
        })
      }
      return
    }
    if (!context.config.token) return
    void chat.bootstrap(context.config.token, context.config.agentId, context.config.conversationScopeKey)
    void refreshExtraction()
  },
  { immediate: true }
)

onMounted(() => {
  if (!context.files.length) refreshExtraction()
  document.addEventListener('visibilitychange', resumeVisibleThread)
  resumeVisibleThread()
})

onUnmounted(() => {
  endWindowDrag()
  document.removeEventListener('visibilitychange', resumeVisibleThread)
})
</script>

<template>
  <main class="chat-shell">
    <header class="chat-header" @pointerdown="startWindowDrag">
      <button type="button" class="header-icon-button" title="对话列表" @click="openSidebar">
        <Menu :size="17" />
      </button>
      <h1 class="chat-title">AI智能助手</h1>
      <nav class="window-actions" aria-label="窗口控制">
        <button v-if="context.windowState === 'normal'" type="button" title="最小化到悬浮按钮" @click="notifyMinimize">
          <Minus :size="16" />
        </button>
        <button v-if="context.windowState === 'normal'" type="button" title="最大化" @click="notifyMaximize">
          <Maximize2 :size="15" />
        </button>
        <button v-else type="button" title="还原窗口" @click="notifyRestore">
          <Minimize2 :size="15" />
        </button>
        <button type="button" title="关闭" @click="notifyClose">
          <X :size="16" />
        </button>
      </nav>
    </header>

    <Transition name="sidebar-fade">
      <button v-if="showSidebar" type="button" class="sidebar-overlay" aria-label="关闭对话列表" @click="showSidebar = false"></button>
    </Transition>
    <Transition name="sidebar-slide">
      <aside v-if="showSidebar" class="conversation-drawer">
        <ChatSidebar
          :threads="chat.threads"
          :current-thread-id="chat.currentThreadId"
          :loading="chat.isLoading"
          @new="createChat"
          @close="showSidebar = false"
          @refresh="chat.refreshThreads(context.config.token, context.config.agentId, context.config.conversationScopeKey)"
          @select="selectThread"
          @rename="(event) => event.title && chat.renameConversation(event.threadId, event.title, context.config.token)"
          @delete="(threadId) => chat.removeConversation(threadId, context.config.token)"
          @pin="(threadId) => chat.togglePinConversation(threadId, context.config.token)"
        />
      </aside>
    </Transition>

    <section class="chat-body">
      <section class="workbench">
        <ChatMessages
          :messages="chat.displayMessages"
          :loading="chat.isLoading"
          :streaming="chat.isStreaming"
          @retry="chat.retry(context.config.token, context.config.agentId, context.config.conversationScopeKey)"
          @feedback="(event) => chat.feedback(event, context.config.token)"
        />
        <ChatInput
          :disabled="chat.isSending || Boolean(context.config.authError) || !context.config.token"
          :streaming="chat.isStreaming"
          :ask-page="chat.askPage"
          :ask-file="chat.askFile"
          :models="chat.modelOptions"
          :selected-model-spec="chat.selectedModelSpec"
          :page-files="context.files"
          :selected-page-file-id="context.selectedFileId"
          @update:ask-page="chat.askPage = $event"
          @update:ask-file="chat.askFile = $event"
          @update:selected-model-spec="chat.selectedModelSpec = $event"
          @update:selected-page-file-id="context.selectFile($event)"
          @submit="sendChat"
          @stop="chat.stop(context.config.token)"
        />
      </section>
    </section>
  </main>
</template>
