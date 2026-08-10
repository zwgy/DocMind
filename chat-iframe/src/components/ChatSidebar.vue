<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArrowLeft,
  Clock3,
  MessageSquare,
  MessageSquarePlus,
  MoreVertical,
  Pin,
  PinOff,
  Pencil,
  RefreshCcw,
  Trash2
} from 'lucide-vue-next'
import type { ChatThread } from '@/types'

const props = withDefaults(
  defineProps<{
    threads?: ChatThread[]
    currentThreadId?: string
    loading?: boolean
    hasMore?: boolean
    loadingMore?: boolean
  }>(),
  { threads: () => [], currentThreadId: '', loading: false, hasMore: false, loadingMore: false }
)

const emit = defineEmits<{
  new: []
  close: []
  refresh: []
  select: [threadId: string]
  rename: [payload: { threadId: string; title: string }]
  delete: [threadId: string]
  pin: [threadId: string]
  loadMore: []
}>()

const openActionsThreadId = ref('')
const sidebarContent = ref<HTMLElement | null>(null)
const pendingDeleteThreadId = ref('')
const pendingRenameThreadId = ref('')
const renameDraft = ref('')
const titleTooltip = ref<{
  threadId: string
  title: string
  updatedAt: string
  top: number
  left: number
  width: number
} | null>(null)
let titleTooltipTimer: number | undefined

function getThreadTitle(thread: ChatThread) {
  return thread.title || '来文咨询'
}

function formatThreadUpdatedAt(value?: string) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  })
    .format(date)
    .replaceAll('/', '-')
}

function hideTitleTooltip(threadId = '') {
  window.clearTimeout(titleTooltipTimer)
  titleTooltipTimer = undefined
  if (!threadId || titleTooltip.value?.threadId === threadId) titleTooltip.value = null
}

function showTitleTooltip(thread: ChatThread, event: MouseEvent | FocusEvent, delay: number) {
  hideTitleTooltip()
  const trigger = event.currentTarget as HTMLElement
  const title = trigger.querySelector<HTMLElement>('.thread-title-text')
  if (!title || title.scrollWidth <= title.clientWidth) return

  // 抽屉会裁剪内部溢出内容，因此浮层挂到 iframe body，并按实际剩余空间选择右侧或视口内展示。
  titleTooltipTimer = window.setTimeout(() => {
    const rect = trigger.getBoundingClientRect()
    const maxWidth = Math.min(360, window.innerWidth - 24)
    const availableRight = window.innerWidth - rect.right - 8
    const placeRight = availableRight >= Math.min(220, maxWidth)
    titleTooltip.value = {
      threadId: thread.id,
      title: getThreadTitle(thread),
      updatedAt: formatThreadUpdatedAt(thread.updated_at),
      top: Math.max(8, Math.min(rect.top, window.innerHeight - 220)),
      left: placeRight ? rect.right + 8 : 12,
      width: placeRight ? Math.min(maxWidth, availableRight) : maxWidth
    }
  }, delay)
}

function selectThread(threadId: string) {
  hideTitleTooltip()
  openActionsThreadId.value = ''
  emit('select', threadId)
}

function toggleThreadActions(threadId: string) {
  hideTitleTooltip()
  openActionsThreadId.value = openActionsThreadId.value === threadId ? '' : threadId
}

function requestRenameThread(thread: ChatThread) {
  hideTitleTooltip()
  openActionsThreadId.value = ''
  pendingRenameThreadId.value = thread.id
  renameDraft.value = thread.title || '来文咨询'
}

function confirmRenameThread() {
  const threadId = pendingRenameThreadId.value
  const title = renameDraft.value.trim()
  pendingRenameThreadId.value = ''
  renameDraft.value = ''
  if (threadId && title) emit('rename', { threadId, title })
}

function togglePinThread(thread: ChatThread) {
  hideTitleTooltip()
  openActionsThreadId.value = ''
  emit('pin', thread.id)
}

function requestDeleteThread(threadId: string) {
  hideTitleTooltip()
  openActionsThreadId.value = ''
  pendingDeleteThreadId.value = threadId
}

function confirmDeleteThread() {
  const threadId = pendingDeleteThreadId.value
  pendingDeleteThreadId.value = ''
  if (threadId) emit('delete', threadId)
}

function closeDialog() {
  pendingDeleteThreadId.value = ''
  pendingRenameThreadId.value = ''
  renameDraft.value = ''
}

async function revealCurrentThread() {
  if (props.loading || !props.currentThreadId) return

  await nextTick()
  const list = sidebarContent.value
  const activeThread = list?.querySelector<HTMLElement>('.thread-option.active')
  if (!list || !activeThread) return

  // 抽屉每次打开都会重新挂载；保留列表原生首行间距，让当前会话稳定成为顶部第一条可见项。
  const listRect = list.getBoundingClientRect()
  const threadRect = activeThread.getBoundingClientRect()
  const listStyle = window.getComputedStyle(list)
  const threadStyle = window.getComputedStyle(activeThread)
  const topGap = Number.parseFloat(listStyle.paddingTop) + Number.parseFloat(threadStyle.marginTop)
  list.scrollTop += threadRect.top - listRect.top - topGap
}

onMounted(revealCurrentThread)
watch(
  [() => props.loading, () => props.currentThreadId],
  ([loading]) => {
    if (!loading) revealCurrentThread()
  },
  { flush: 'post' }
)
onBeforeUnmount(() => hideTitleTooltip())
</script>

<template>
  <section class="chat-sidebar">
    <header class="sidebar-header">
      <button type="button" class="sidebar-header-btn back" title="返回" @click="$emit('close')">
        <ArrowLeft :size="22" />
      </button>
      <h2>历史对话</h2>
      <div class="sidebar-header-actions">
        <button type="button" class="sidebar-header-btn" title="刷新列表" @click="$emit('refresh')">
          <RefreshCcw :size="17" />
        </button>
        <button
          type="button"
          class="sidebar-header-btn primary"
          title="新建对话"
          @click="$emit('new')"
        >
          <MessageSquarePlus :size="18" />
        </button>
      </div>
    </header>

    <div ref="sidebarContent" class="sidebar-content" @scroll="hideTitleTooltip()">
      <div v-if="loading" class="sidebar-state">
        <span class="loading-spinner"></span>
        <span>加载中...</span>
      </div>
      <div v-else-if="!threads.length" class="sidebar-state">
        <MessageSquare :size="48" />
        <span>暂无历史对话</span>
      </div>
      <template v-else>
        <div
          v-for="thread in threads"
          :key="thread.id"
          class="thread-option"
          :class="{
            active: thread.id === currentThreadId,
            'actions-open': openActionsThreadId === thread.id
          }"
        >
          <span class="thread-icon">
            <Clock3 v-if="thread.thread_kind === 'scheduled_run'" :size="16" />
            <MessageSquare v-else :size="16" />
          </span>
          <button
            type="button"
            class="thread-title"
            :aria-describedby="
              titleTooltip?.threadId === thread.id ? `thread-title-tooltip-${thread.id}` : undefined
            "
            @mouseenter="showTitleTooltip(thread, $event, 350)"
            @mouseleave="hideTitleTooltip(thread.id)"
            @focus="showTitleTooltip(thread, $event, 0)"
            @blur="hideTitleTooltip(thread.id)"
            @click="selectThread(thread.id)"
          >
            <Pin v-if="thread.is_pinned" :size="13" />
            <span class="thread-title-text">{{ getThreadTitle(thread) }}</span>
          </button>
          <span class="thread-actions">
            <template v-if="openActionsThreadId === thread.id">
              <button type="button" title="重命名" @click.stop="requestRenameThread(thread)">
                <Pencil :size="15" />
              </button>
              <button
                type="button"
                :title="thread.is_pinned ? '取消置顶' : '置顶'"
                @click.stop="togglePinThread(thread)"
              >
                <PinOff v-if="thread.is_pinned" :size="15" />
                <Pin v-else :size="15" />
              </button>
              <button
                v-if="thread.thread_kind !== 'scheduled_run'"
                type="button"
                title="删除"
                @click.stop="requestDeleteThread(thread.id)"
              >
                <Trash2 :size="15" />
              </button>
            </template>
            <button
              v-else
              type="button"
              title="更多操作"
              @click.stop="toggleThreadActions(thread.id)"
            >
              <MoreVertical :size="16" />
            </button>
          </span>
        </div>
        <button
          v-if="hasMore"
          type="button"
          class="sidebar-load-more"
          :disabled="loadingMore"
          @click="$emit('loadMore')"
        >
          {{ loadingMore ? '加载中...' : '加载更多' }}
        </button>
      </template>
    </div>

    <Teleport to="body">
      <div
        v-if="titleTooltip"
        :id="`thread-title-tooltip-${titleTooltip.threadId}`"
        class="thread-title-tooltip"
        role="tooltip"
        :style="{
          top: `${titleTooltip.top}px`,
          left: `${titleTooltip.left}px`,
          width: `${titleTooltip.width}px`
        }"
      >
        <div class="thread-title-tooltip-name">{{ titleTooltip.title }}</div>
        <div class="thread-title-tooltip-time">更新于 {{ titleTooltip.updatedAt }}</div>
      </div>
    </Teleport>

    <div
      v-if="pendingDeleteThreadId || pendingRenameThreadId"
      class="sidebar-confirm-mask"
      @click.self="closeDialog"
    >
      <article v-if="pendingRenameThreadId" class="sidebar-confirm">
        <h3>重命名对话</h3>
        <input
          v-model="renameDraft"
          class="sidebar-confirm-input"
          type="text"
          autocomplete="off"
          autofocus
          @keydown.enter="confirmRenameThread"
          @keydown.esc="closeDialog"
        />
        <footer>
          <button type="button" class="secondary" @click="closeDialog">取消</button>
          <button
            type="button"
            class="primary"
            :disabled="!renameDraft.trim()"
            @click="confirmRenameThread"
          >
            保存
          </button>
        </footer>
      </article>

      <article v-else class="sidebar-confirm">
        <h3>删除对话？</h3>
        <p>删除后无法恢复，请确认是否继续。</p>
        <footer>
          <button type="button" class="secondary" @click="closeDialog">取消</button>
          <button type="button" class="danger" @click="confirmDeleteThread">删除</button>
        </footer>
      </article>
    </div>
  </section>
</template>
