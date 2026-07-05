<script setup lang="ts">
import type { ChatMessage } from '@/types'

withDefaults(
  defineProps<{
    messages?: ChatMessage[]
    loading?: boolean
  }>(),
  { messages: () => [], loading: false }
)
</script>

<template>
  <section class="chat-messages">
    <p v-if="loading" class="empty">正在加载聊天记录...</p>
    <div v-else-if="!messages.length" class="chat-welcome">
      <strong>可以直接提问</strong>
      <span>默认会带上当前页面和选中文档的结构化结果。</span>
    </div>
    <article
      v-for="message in messages"
      :key="message.id"
      class="chat-message"
      :class="[message.role, message.status]"
    >
      <div class="message-role">{{ message.role === 'user' ? '我' : message.role === 'tool' ? '工具' : '助手' }}</div>
      <div class="message-content">
        <p v-if="message.content">{{ message.content }}</p>
        <p v-else class="muted">正在思考...</p>
        <ul v-if="message.toolEvents?.length" class="tool-events">
          <li v-for="event in message.toolEvents" :key="event">{{ event }}</li>
        </ul>
      </div>
    </article>
  </section>
</template>
