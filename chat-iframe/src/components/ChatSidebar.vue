<script setup lang="ts">
import { ArrowLeft, MessageSquare, MessageSquarePlus, Pin, PinOff, Pencil, RefreshCcw, Trash2 } from 'lucide-vue-next'
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

function renameThread(thread: ChatThread) {
  const title = window.prompt('对话标题', thread.title || '来文咨询')?.trim()
  if (title) emit('rename', { threadId: thread.id, title })
}

function deleteThread(threadId: string) {
  if (window.confirm('确认删除这个对话？')) emit('delete', threadId)
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
          :class="{ active: thread.id === currentThreadId }"
        >
          <span class="thread-icon">
            <MessageSquare :size="16" />
          </span>
          <button type="button" class="thread-title" @click="$emit('select', thread.id)">
            <Pin v-if="thread.is_pinned" :size="14" />
            {{ thread.title || '来文咨询' }}
          </button>
          <span class="thread-actions">
            <button type="button" title="重命名" @click.stop="renameThread(thread)">
              <Pencil :size="15" />
            </button>
            <button type="button" :title="thread.is_pinned ? '取消置顶' : '置顶'" @click.stop="$emit('pin', thread.id)">
              <PinOff v-if="thread.is_pinned" :size="15" />
              <Pin v-else :size="15" />
            </button>
            <button type="button" title="删除" @click.stop="deleteThread(thread.id)">
              <Trash2 :size="15" />
            </button>
          </span>
        </div>
      </template>
    </div>
  </section>
</template>
