<script setup lang="ts">
import { ref } from 'vue'
import {
  ArrowLeft,
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

withDefaults(
  defineProps<{
    threads?: ChatThread[]
    currentThreadId?: string
    loading?: boolean
  }>(),
  { threads: () => [], currentThreadId: '', loading: false }
)

const emit = defineEmits<{
  new: []
  close: []
  refresh: []
  select: [threadId: string]
  rename: [payload: { threadId: string; title: string }]
  delete: [threadId: string]
  pin: [threadId: string]
}>()

const openActionsThreadId = ref('')
const pendingDeleteThreadId = ref('')
const pendingRenameThreadId = ref('')
const renameDraft = ref('')

function selectThread(threadId: string) {
  openActionsThreadId.value = ''
  emit('select', threadId)
}

function toggleThreadActions(threadId: string) {
  openActionsThreadId.value = openActionsThreadId.value === threadId ? '' : threadId
}

function requestRenameThread(thread: ChatThread) {
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
  openActionsThreadId.value = ''
  emit('pin', thread.id)
}

function requestDeleteThread(threadId: string) {
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
        <button type="button" class="sidebar-header-btn primary" title="新建对话" @click="$emit('new')">
          <MessageSquarePlus :size="18" />
        </button>
      </div>
    </header>

    <div class="sidebar-content">
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
          :class="{ active: thread.id === currentThreadId, 'actions-open': openActionsThreadId === thread.id }"
        >
          <span class="thread-icon">
            <MessageSquare :size="16" />
          </span>
          <button type="button" class="thread-title" @click="selectThread(thread.id)">
            <Pin v-if="thread.is_pinned" :size="13" />
            <span>{{ thread.title || '来文咨询' }}</span>
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
              <button type="button" title="删除" @click.stop="requestDeleteThread(thread.id)">
                <Trash2 :size="15" />
              </button>
            </template>
            <button v-else type="button" title="更多操作" @click.stop="toggleThreadActions(thread.id)">
              <MoreVertical :size="16" />
            </button>
          </span>
        </div>
      </template>
    </div>

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
          <button type="button" class="primary" :disabled="!renameDraft.trim()" @click="confirmRenameThread">
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
