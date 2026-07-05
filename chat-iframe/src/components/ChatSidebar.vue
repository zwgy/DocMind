<script setup lang="ts">
import { MessageSquarePlus, Pin, PinOff, Pencil, Trash2 } from 'lucide-vue-next'
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
    <button type="button" class="new-chat-button" @click="$emit('new')">
      <MessageSquarePlus :size="16" />
      <span>新聊天</span>
    </button>
    <div class="section-title">对话</div>
    <p v-if="loading" class="empty">加载中...</p>
    <p v-else-if="!threads.length" class="empty">暂无对话</p>
    <div
      v-for="thread in threads"
      :key="thread.id"
      class="thread-option"
      :class="{ active: thread.id === currentThreadId }"
    >
      <button type="button" class="thread-title" @click="$emit('select', thread.id)">
        <Pin v-if="thread.is_pinned" :size="13" />
        {{ thread.title || '来文咨询' }}
      </button>
      <span class="thread-actions">
        <button
          type="button"
          title="重命名"
          @click.stop="renameThread(thread)"
        >
          <Pencil :size="13" />
        </button>
        <button type="button" :title="thread.is_pinned ? '取消置顶' : '置顶'" @click.stop="$emit('pin', thread.id)">
          <PinOff v-if="thread.is_pinned" :size="13" />
          <Pin v-else :size="13" />
        </button>
        <button
          type="button"
          title="删除"
          @click.stop="deleteThread(thread.id)"
        >
          <Trash2 :size="13" />
        </button>
      </span>
    </div>
  </section>
</template>
