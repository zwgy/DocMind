<script setup lang="ts">
import { ref } from 'vue'
import MarkdownPreview from '@/components/MarkdownPreview.vue'
import MessageRefs from '@/components/MessageRefs.vue'
import ToolCallsPanel from '@/components/ToolCallsPanel.vue'
import type { ChatMessage } from '@/types'

withDefaults(
  defineProps<{
    messages?: ChatMessage[]
    loading?: boolean
  }>(),
  { messages: () => [], loading: false }
)

defineEmits<{
  retry: []
  feedback: [payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }]
}>()

const openReasoning = ref<Record<string, boolean>>({})

function imageSrc(content?: string) {
  if (!content) return ''
  if (content.startsWith('data:') || content.startsWith('blob:')) return content
  return `data:image/jpeg;base64,${content}`
}
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
        <img v-if="message.imageContent" class="message-image" :src="imageSrc(message.imageContent)" alt="用户上传图片" />
        <div v-if="message.attachments?.length" class="message-attachments">
          <span v-for="attachment in message.attachments" :key="String(attachment.file_id || attachment.file_name || attachment.name)">
            {{ attachment.file_name || attachment.name }}
          </span>
        </div>
        <details v-if="message.reasoningContent" class="reasoning-box" :open="openReasoning[message.id]">
          <summary @click.prevent="openReasoning[message.id] = !openReasoning[message.id]">
            {{ message.status === 'streaming' ? '正在思考...' : '推理过程' }}
          </summary>
          <p>{{ message.reasoningContent }}</p>
        </details>
        <MarkdownPreview v-if="message.content" :content="message.content" />
        <p v-else class="muted">正在思考...</p>
        <p v-if="message.errorMessage" class="error-hint">{{ message.errorMessage }}</p>
        <ToolCallsPanel :tool-calls="message.toolCalls || []" />
        <MessageRefs
          v-if="message.role === 'assistant' && message.status === 'done'"
          :message="message"
          @retry="$emit('retry')"
          @feedback="$emit('feedback', $event)"
        />
        <ul v-if="message.toolEvents?.length" class="tool-events">
          <li v-for="event in message.toolEvents" :key="event">{{ event }}</li>
        </ul>
      </div>
    </article>
  </section>
</template>
