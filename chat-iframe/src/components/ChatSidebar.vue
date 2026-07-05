<script setup lang="ts">
import { MessageSquarePlus } from 'lucide-vue-next'
import type { ChatThread } from '@/types'

withDefaults(
  defineProps<{
    threads?: ChatThread[]
    currentThreadId?: string
    loading?: boolean
  }>(),
  { threads: () => [], currentThreadId: '', loading: false }
)

defineEmits<{
  new: []
  select: [threadId: string]
}>()
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
    <button
      v-for="thread in threads"
      :key="thread.id"
      type="button"
      class="thread-option"
      :class="{ active: thread.id === currentThreadId }"
      @click="$emit('select', thread.id)"
    >
      {{ thread.title || '来文咨询' }}
    </button>
  </section>
</template>
