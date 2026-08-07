<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Bell, Bot, Clock3, Maximize2, Menu, Minimize2, Minus, X } from 'lucide-vue-next'
import { ingestIncomingDocument, queryIncomingDocumentExtractions } from '@/apis/incoming-documents'
import { inboxApi } from '@/apis/inbox'
import type { InboxUnreadCounts, NotificationInboxItem, TaskInboxItem } from '@/apis/inbox'
import ChatInput from '@/components/ChatInput.vue'
import ChatMessages from '@/components/ChatMessages.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import RunInterruptCard from '@/components/RunInterruptCard.vue'
import ScheduledCenterDrawer from '@/components/ScheduledCenterDrawer.vue'
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
  notifyWindowDragStart,
  notifyUnreadCountChanged
} = useIframeBridge()
const chat = useChatStore()
const loading = ref(false)
const error = ref('')
const results = ref<Record<string, ExtractionResult>>({})
const selectedPageFiles = ref<IncomingPageFile[]>([])
const showSidebar = ref(false)
const showScheduledCenter = ref(false)
const unreadCounts = ref<InboxUnreadCounts>({
  notification_unread_count: 0,
  task_unread_count: 0,
  total_unread_count: 0
})
type TickerItem = {
  key: string
  id: string
  category: 'notification' | 'task'
  content: string
  createdAt: string | null
}
const TICKER_ITEM_LIMIT = 5
const TICKER_SPEED_PX_PER_SECOND = 40
const tickerItems = ref<TickerItem[]>([])
const tickerViewport = ref<HTMLElement | null>(null)
const tickerGroup = ref<HTMLElement | null>(null)
const tickerCycleKey = ref(0)
const tickerDistance = ref(0)
const tickerDuration = ref('12s')
const tickerIsOverflowing = ref(false)
const tickerMotionReduced = ref(false)
const inboxNavigation = ref<{ key: number; category: 'notification' | 'task' } | null>(null)
const draggingWindow = ref(false)
const historyScrollRequest = ref(0)
const ingestingFileIds = new Set<string>()
let extractionRefreshTimer: ReturnType<typeof setTimeout> | null = null
let inboxRefreshTimer: number | null = null
let inboxRefreshPromise: Promise<void> | null = null
let pendingTickerItems: TickerItem[] | null = null
let tickerResizeObserver: ResizeObserver | null = null
let tickerMotionQuery: MediaQueryList | null = null

const selectedFile = computed(() => context.selectedFile)
const currentTokenUsage = computed(() => {
  const usage = chat.agentState?.token_usage
  return usage && typeof usage === 'object' && !Array.isArray(usage)
    ? (usage as Record<string, unknown>)
    : null
})

function openSidebar() {
  showSidebar.value = true
  // 重新打开抽屉时保留已加载的分页，否则首屏刷新会移除后续页中的当前会话，导致无法定位。
  if (chat.threads.length) return

  void chat.refreshThreads(
    context.config.token,
    context.config.agentId,
    context.config.conversationScopeKey
  )
}

function displayCount(value: number) {
  return value > 99 ? '99+' : String(value)
}

function applyUnreadCounts(counts: InboxUnreadCounts) {
  unreadCounts.value = {
    notification_unread_count: Number(counts?.notification_unread_count || 0),
    task_unread_count: Number(counts?.task_unread_count || 0),
    total_unread_count: Number(counts?.total_unread_count || 0)
  }
  notifyUnreadCountChanged(unreadCounts.value.total_unread_count)
}

function resetInboxSnapshot() {
  pendingTickerItems = null
  tickerItems.value = []
  tickerCycleKey.value += 1
  tickerIsOverflowing.value = false
  applyUnreadCounts({
    notification_unread_count: 0,
    task_unread_count: 0,
    total_unread_count: 0
  })
}

function tickerItemsSignature(items: TickerItem[]) {
  return items
    .map((item) => `${item.key}\u0000${item.category}\u0000${item.content}`)
    .join('\u0001')
}

function measureTickerTrack() {
  const viewportWidth = tickerViewport.value?.clientWidth || 0
  const groupWidth = tickerGroup.value?.scrollWidth || 0
  tickerDistance.value = groupWidth
  tickerDuration.value = `${Math.max(groupWidth / TICKER_SPEED_PX_PER_SECOND, 12).toFixed(2)}s`
  tickerIsOverflowing.value = groupWidth > viewportWidth + 1
}

function observeTickerTrack() {
  tickerResizeObserver?.disconnect()
  if (tickerViewport.value) tickerResizeObserver?.observe(tickerViewport.value)
  if (tickerGroup.value) tickerResizeObserver?.observe(tickerGroup.value)
  measureTickerTrack()
}

function applyTickerItems(items: TickerItem[]) {
  pendingTickerItems = null
  tickerItems.value = items
  tickerCycleKey.value += 1
  tickerIsOverflowing.value = false
  void nextTick(observeTickerTrack)
}

function queueTickerItems(items: TickerItem[]) {
  if (tickerItemsSignature(items) === tickerItemsSignature(tickerItems.value)) {
    pendingTickerItems = null
    return
  }
  if (
    !tickerItems.value.length ||
    !items.length ||
    !tickerIsOverflowing.value ||
    tickerMotionReduced.value
  ) {
    applyTickerItems(items)
    return
  }
  // 轮询结果先进入下一批，等当前轨道完整跑完一圈再替换，避免正文阅读到一半突然跳动。
  pendingTickerItems = items
}

function commitPendingTickerItems() {
  if (pendingTickerItems) applyTickerItems(pendingTickerItems)
}

function handleTickerMotionPreference(event: MediaQueryListEvent) {
  tickerMotionReduced.value = event.matches
  if (event.matches) commitPendingTickerItems()
  void nextTick(observeTickerTrack)
}

async function loadInboxSnapshot() {
  const token = context.config.token
  if (!token) return
  try {
    const counts = await inboxApi.unreadCount(token)
    const [notificationPage, taskPage] = await Promise.all([
      counts.notification_unread_count
        ? inboxApi.list('notification', token)
        : Promise.resolve({ items: [], next_cursor: null }),
      counts.task_unread_count
        ? inboxApi.list('task', token)
        : Promise.resolve({ items: [], next_cursor: null })
    ])
    // 认证切换期间到达的旧请求结果不能覆盖新用户的收件箱状态。
    if (context.config.token !== token) return
    const notifications = notificationPage.items
      .filter((item): item is NotificationInboxItem => 'id' in item && !item.is_read)
      .map((item) => ({
        key: `notification:${item.id}`,
        id: item.id,
        category: 'notification' as const,
        content: item.content,
        createdAt: item.created_at
      }))
    const tasks = taskPage.items
      .filter((item): item is TaskInboxItem => 'job' in item && item.unread_update_count > 0)
      .map((item) => ({
        key: `task:${item.job.id}`,
        id: item.job.id,
        category: 'task' as const,
        content: item.latest_update?.content || '任务状态已更新',
        createdAt: item.latest_update?.created_at || item.sort_at
      }))
    const nextTickerItems = [...notifications, ...tasks]
      .sort((left, right) => Date.parse(right.createdAt || '') - Date.parse(left.createdAt || ''))
      .slice(0, TICKER_ITEM_LIMIT)
      .reverse()
    queueTickerItems(nextTickerItems)
    applyUnreadCounts(counts)
  } catch {
    // 轮询失败时保留上一次状态，避免网络抖动把未读提示错误清零。
  }
}

async function refreshInboxSnapshot(force = false) {
  if (inboxRefreshPromise) {
    await inboxRefreshPromise
    if (!force) return
  }
  inboxRefreshPromise = loadInboxSnapshot()
  try {
    await inboxRefreshPromise
  } finally {
    inboxRefreshPromise = null
  }
}

function openScheduledCenter(category?: 'notification' | 'task') {
  if (category) {
    inboxNavigation.value = { key: (inboxNavigation.value?.key || 0) + 1, category }
  }
  showScheduledCenter.value = true
  void refreshInboxSnapshot()
}

async function openTickerItem(item: TickerItem) {
  openScheduledCenter(item.category)
  if (!context.config.token) return
  try {
    await inboxApi.markRead(item.category, item.id, context.config.token)
    applyTickerItems(tickerItems.value.filter((candidate) => candidate.key !== item.key))
    await refreshInboxSnapshot(true)
    inboxNavigation.value = {
      key: (inboxNavigation.value?.key || 0) + 1,
      category: item.category
    }
  } catch (err) {
    chat.error = err instanceof Error ? err.message : '标记通知已读失败'
  }
}

function handleUnreadChanged(counts: InboxUnreadCounts) {
  applyUnreadCounts(counts)
  void refreshInboxSnapshot(true)
}

function cacheExtractionResults(files: IncomingPageFile[], items: ExtractionResult[] = []) {
  const next = { ...results.value }
  for (const [index, file] of files.entries()) {
    const item =
      items.find((candidate) => {
        const incomingId = candidate.incomingFileId || candidate.name
        return incomingId === file.source_file_id
      }) || (items.length === 1 ? items[0] : items[index])
    if (item) next[file.source_file_id] = item
  }
  results.value = next
}

function refreshContextSummaries(options: { loading?: boolean; error?: string } = {}) {
  const selectedFiles = selectedPageFiles.value.length
    ? selectedPageFiles.value
    : selectedFile.value
      ? [selectedFile.value]
      : []
  chat.setContextSummaries(
    selectedFiles.map((file) => ({
      file,
      result: results.value[file.source_file_id] || null,
      ...options
    }))
  )
}

function updateSelectedPageFiles(files: IncomingPageFile[]) {
  selectedPageFiles.value = files
  // 多选摘要必须与发送给模型的附件集一致，避免最后点击的副附件覆盖主附件摘要。
  refreshContextSummaries()
  if (files.length) void refreshExtraction(filesForSelectedDocuments(files))
}

function filesForSelectedDocuments(selectedFiles: IncomingPageFile[]) {
  const documentKey = (file: IncomingPageFile) =>
    file.source_function_id && file.source_doc_id
      ? `${file.source_system || 'production'}\u0000${file.source_function_id}\u0000${file.source_doc_id}`
      : ''
  const selectedKeys = new Set(selectedFiles.map(documentKey).filter(Boolean))
  const candidates = [
    ...selectedFiles,
    ...context.files.filter((file) => selectedKeys.has(documentKey(file)))
  ]
  return [...new Map(candidates.map((file) => [file.source_file_id, file])).values()]
}

async function refreshExtraction(
  queryFiles: IncomingPageFile[] = selectedFile.value ? [selectedFile.value] : [],
  syncPending = false
) {
  if (extractionRefreshTimer) {
    clearTimeout(extractionRefreshTimer)
    extractionRefreshTimer = null
  }
  const file = selectedFile.value
  if (!file) {
    refreshContextSummaries()
    return false
  }
  if (!queryFiles.length) return false
  if (context.config.authError) {
    refreshContextSummaries({ error: context.config.authError })
    return false
  }
  if (!context.config.token) {
    // 父页面可能先响应附件列表、后完成换票；这里等待 token 到达，避免无凭证请求把摘要卡片打成 401。
    refreshContextSummaries()
    return false
  }
  loading.value = true
  error.value = ''
  refreshContextSummaries({ loading: true })
  try {
    let response = await queryIncomingDocumentExtractions(queryFiles, context.config.token)
    cacheExtractionResults(queryFiles, response.items || [])
    const pendingCandidates = syncPending
      ? queryFiles.filter(
          (file) => results.value[file.source_file_id]?.matchStatus === 'pending_sync'
        )
      : []
    const missingSourceFiles = pendingCandidates.filter((file) => !file.source_url)
    if (missingSourceFiles.length) {
      throw new Error(
        `以下附件缺少下载地址，无法形成完整来文：${missingSourceFiles.map((file) => file.name).join('、')}`
      )
    }
    const pendingFiles = pendingCandidates.filter(
      (file) => !ingestingFileIds.has(file.source_file_id)
    )
    if (pendingFiles.length) {
      pendingFiles.forEach((file) => ingestingFileIds.add(file.source_file_id))
      try {
        await ingestIncomingDocument(pendingFiles, context.config.token)
      } finally {
        pendingFiles.forEach((file) => ingestingFileIds.delete(file.source_file_id))
      }
      response = await queryIncomingDocumentExtractions(queryFiles, context.config.token)
      cacheExtractionResults(queryFiles, response.items || [])
    }
    if (
      queryFiles.some(
        (candidate) => candidate.source_file_id === selectedFile.value?.source_file_id
      )
    )
      refreshContextSummaries()
    return true
  } catch (err) {
    error.value = err instanceof Error ? err.message : '查询失败'
    if (
      queryFiles.some(
        (candidate) => candidate.source_file_id === selectedFile.value?.source_file_id
      )
    )
      refreshContextSummaries({ error: error.value })
    return false
  } finally {
    loading.value = false
    const currentFile = selectedFile.value
    const currentResult = currentFile ? results.value[currentFile.source_file_id] : null
    if (
      currentFile &&
      currentResult?.matchStatus === 'matched' &&
      !['ready', 'failed'].includes(currentResult.extractionStatus)
    ) {
      extractionRefreshTimer = setTimeout(() => void refreshExtraction(), 2000)
    }
  }
}

async function createChat() {
  try {
    const thread = await chat.newConversation(
      context.config.token,
      context.config.agentId,
      context.config.conversationScopeKey
    )
    showSidebar.value = false
    notifyConversationCreated({ conversationId: thread.id })
  } catch (err) {
    // 嵌入页可能先于父页面 token 注入完成，显式落到聊天错误态比未处理异常更利于定位接入问题。
    chat.error = err instanceof Error ? err.message : '创建会话失败'
  }
}

async function selectThread(threadId: string) {
  await chat.selectThread(threadId, context.config.token)
  // 会话 ID 会先于异步历史写入组件；完成加载后显式通知消息区定位，
  // 避免依赖流式状态或消息数组更新时机来猜测是否需要滚动。
  historyScrollRequest.value += 1
  showSidebar.value = false
}

async function submitInterrupt(answer: unknown) {
  try {
    await chat.submitInterrupt(
      chat.currentThreadId,
      answer,
      context.config.token,
      context.config.agentId
    )
  } catch (err) {
    chat.error = err instanceof Error ? err.message : '恢复运行失败'
  }
}

async function submitFeedback(event: {
  messageId: string
  rating: 'like' | 'dislike'
  reason: string | null
}) {
  try {
    await chat.feedback(event, context.config.token)
  } catch (err) {
    chat.error = err instanceof Error ? err.message : '提交反馈失败'
  }
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
  restoreUploadDraft: (retry: { files: boolean; image: boolean; message: string }) => void
}) {
  const selectedPageFiles = payload.selectedPageFiles || []
  if (
    selectedPageFiles.length &&
    !(await refreshExtraction(filesForSelectedDocuments(selectedPageFiles), true))
  ) {
    payload.restoreUploadDraft({
      files: false,
      image: false,
      message: error.value || '来文附件尚未准备完成，请稍后重试'
    })
    return
  }
  const selectedContextFile = selectedPageFiles[0] || null
  const selectedContextResult = selectedContextFile
    ? results.value[selectedContextFile.source_file_id] || null
    : null
  const result = await chat.send(
    {
      text: payload.text,
      files: payload.files,
      imageFile: payload.imageFile,
      pageContent: context.pageContent,
      selectedFile: selectedContextFile,
      extractionResult: selectedContextResult,
      selectedPageFiles,
      extractionResults: results.value
    },
    context.config.token,
    context.config.agentId,
    context.config.conversationScopeKey
  )
  if (result && 'retryUpload' in result) payload.restoreUploadDraft(result.retryUpload)
  else if (result)
    notifyMessageSent({ conversationId: result.threadId, messageId: result.messageId })
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
  () => context.config.token,
  () => resetInboxSnapshot()
)

watch(
  () => selectedFile.value?.source_file_id,
  () => {
    refreshContextSummaries()
    void refreshExtraction()
  },
  { immediate: true }
)

watch(
  [
    () => context.config.token,
    () => context.config.agentId,
    () => context.config.conversationScopeKey,
    () => context.config.authError
  ],
  () => {
    if (context.config.authError) {
      chat.error = context.config.authError
      if (selectedFile.value) {
        refreshContextSummaries({ error: context.config.authError })
      }
      return
    }
    if (!context.config.token) return
    void refreshInboxSnapshot(true)
    void chat
      .bootstrap(context.config.token, context.config.agentId, context.config.conversationScopeKey)
      .then(() => {
        historyScrollRequest.value += 1
      })
    void refreshExtraction()
  },
  { immediate: true }
)

watch(
  () => context.windowState,
  (state, previousState) => {
    if (state !== 'normal' && state !== 'maximized') return
    if (previousState !== 'minimized' && previousState !== 'closed') return
    // 父页面在隐藏悬浮窗期间无法完成有效滚动；恢复尺寸后再等一帧通知消息区，
    // 保证首次展开已有长会话时以最后一条消息作为起点。
    requestAnimationFrame(() => {
      historyScrollRequest.value += 1
    })
    void refreshInboxSnapshot()
  },
  { flush: 'post' }
)

onMounted(() => {
  if (!context.files.length) refreshExtraction()
  document.addEventListener('visibilitychange', resumeVisiblePage)
  window.addEventListener('focus', refreshVisibleInbox)
  resumeVisibleThread()
  inboxRefreshTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible' && context.config.token) void refreshInboxSnapshot()
  }, 30000)
  tickerResizeObserver = new ResizeObserver(measureTickerTrack)
  tickerMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  tickerMotionReduced.value = tickerMotionQuery.matches
  tickerMotionQuery.addEventListener('change', handleTickerMotionPreference)
  void nextTick(observeTickerTrack)
})

onUnmounted(() => {
  endWindowDrag()
  if (extractionRefreshTimer) clearTimeout(extractionRefreshTimer)
  if (inboxRefreshTimer) clearInterval(inboxRefreshTimer)
  tickerResizeObserver?.disconnect()
  tickerMotionQuery?.removeEventListener('change', handleTickerMotionPreference)
  document.removeEventListener('visibilitychange', resumeVisiblePage)
  window.removeEventListener('focus', refreshVisibleInbox)
})

function refreshVisibleInbox() {
  if (document.visibilityState === 'visible' && context.config.token) void refreshInboxSnapshot()
}

function resumeVisiblePage() {
  resumeVisibleThread()
  refreshVisibleInbox()
}
</script>

<template>
  <main class="chat-shell">
    <header class="chat-header" @pointerdown="startWindowDrag">
      <button type="button" class="header-icon-button" title="对话列表" @click="openSidebar">
        <Menu :size="17" />
      </button>
      <h1 class="chat-title">AI智能助手</h1>
      <nav class="window-actions" aria-label="窗口控制">
        <button
          type="button"
          title="定时中心"
          :aria-label="
            unreadCounts.total_unread_count
              ? `定时中心，${unreadCounts.total_unread_count} 条未读`
              : '定时中心'
          "
          class="timing-center-button"
          @click="openScheduledCenter()"
        >
          <Clock3 :size="16" />
          <span v-if="unreadCounts.total_unread_count" class="timing-count">{{
            displayCount(unreadCounts.total_unread_count)
          }}</span>
        </button>
        <button
          v-if="context.windowState === 'normal'"
          type="button"
          title="最小化到悬浮按钮"
          @click="notifyMinimize"
        >
          <Minus :size="16" />
        </button>
        <button
          v-if="context.windowState === 'normal'"
          type="button"
          title="最大化"
          @click="notifyMaximize"
        >
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
      <button
        v-if="showSidebar"
        type="button"
        class="sidebar-overlay"
        aria-label="关闭对话列表"
        @click="showSidebar = false"
      ></button>
    </Transition>
    <ScheduledCenterDrawer
      :open="showScheduledCenter"
      :token="context.config.token"
      :unread-counts="unreadCounts"
      :inbox-navigation="inboxNavigation"
      @close="showScheduledCenter = false"
      @unread-changed="handleUnreadChanged"
    />
    <Transition name="sidebar-slide">
      <aside v-if="showSidebar" class="conversation-drawer">
        <ChatSidebar
          :threads="chat.threads"
          :current-thread-id="chat.currentThreadId"
          :loading="chat.isLoading"
          :has-more="chat.hasMoreThreads"
          :loading-more="chat.isLoadingMoreThreads"
          @new="createChat"
          @close="showSidebar = false"
          @refresh="
            chat.refreshThreads(
              context.config.token,
              context.config.agentId,
              context.config.conversationScopeKey
            )
          "
          @load-more="
            chat.loadMoreThreads(
              context.config.token,
              context.config.agentId,
              context.config.conversationScopeKey
            )
          "
          @select="selectThread"
          @rename="
            (event) =>
              event.title &&
              chat.renameConversation(event.threadId, event.title, context.config.token)
          "
          @delete="(threadId) => chat.removeConversation(threadId, context.config.token)"
          @pin="
            (threadId) =>
              chat.togglePinConversation(
                threadId,
                context.config.token,
                context.config.agentId,
                context.config.conversationScopeKey
              )
          "
        />
      </aside>
    </Transition>

    <section class="chat-body">
      <section v-if="tickerItems.length" class="notification-ticker" aria-label="未读消息轮播">
        <div ref="tickerViewport" class="ticker-marquee">
          <div
            :key="tickerCycleKey"
            class="ticker-track"
            :class="{ 'is-moving': tickerIsOverflowing && !tickerMotionReduced }"
            :style="{
              '--ticker-distance': `${tickerDistance}px`,
              '--ticker-duration': tickerDuration
            }"
            @animationiteration="commitPendingTickerItems"
          >
            <div ref="tickerGroup" class="ticker-group">
              <button
                v-for="item in tickerItems"
                :key="item.key"
                type="button"
                class="ticker-item"
                :class="item.category"
                :aria-label="item.content"
                @click="openTickerItem(item)"
              >
                <Bell v-if="item.category === 'notification'" class="ticker-item-icon" :size="13" />
                <Bot v-else class="ticker-item-icon" :size="13" />
                <span class="ticker-content">{{ item.content }}</span>
              </button>
            </div>
            <div
              v-if="tickerIsOverflowing && !tickerMotionReduced"
              class="ticker-group"
              aria-hidden="true"
            >
              <button
                v-for="item in tickerItems"
                :key="`duplicate:${item.key}`"
                type="button"
                tabindex="-1"
                class="ticker-item"
                :class="item.category"
                @click="openTickerItem(item)"
              >
                <Bell v-if="item.category === 'notification'" class="ticker-item-icon" :size="13" />
                <Bot v-else class="ticker-item-icon" :size="13" />
                <span class="ticker-content">{{ item.content }}</span>
              </button>
            </div>
          </div>
        </div>
      </section>
      <section class="workbench">
        <ChatMessages
          :messages="chat.displayMessages"
          :loading="chat.isLoading"
          :streaming="chat.isStreaming"
          :compacting="chat.isCompacting"
          :show-run-progress="chat.showRunTodos"
          :agent-state="chat.agentState"
          :thread-id="chat.currentThreadId"
          :token="context.config.token"
          :history-scroll-request="historyScrollRequest"
          @feedback="submitFeedback"
        />
        <RunInterruptCard
          v-if="chat.pendingInterrupt"
          :interrupt="chat.pendingInterrupt"
          :disabled="chat.isSending"
          @submit="submitInterrupt"
          @cancel="submitInterrupt('reject')"
        />
        <ChatInput
          v-else
          :disabled="chat.isSending || Boolean(context.config.authError) || !context.config.token"
          :streaming="chat.isStreaming"
          :ask-page="chat.askPage"
          :ask-file="chat.askFile"
          :models="chat.modelOptions"
          :selected-model-spec="chat.selectedModelSpec"
          :token-usage="currentTokenUsage"
          :page-files="context.files"
          :selected-page-source-file-id="context.selectedSourceFileId"
          @update:ask-page="chat.askPage = $event"
          @update:ask-file="chat.askFile = $event"
          @update:selected-model-spec="chat.setSelectedModelSpec($event)"
          @update:selected-page-source-file-id="context.selectFile($event)"
          @update:selected-page-files="updateSelectedPageFiles"
          @submit="sendChat"
          @stop="chat.stop(context.config.token)"
        />
      </section>
    </section>
  </main>
</template>
