<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownPreview from '@/components/MarkdownPreview.vue'
import MessageRefs from '@/components/MessageRefs.vue'
import ToolCallsPanel from '@/components/ToolCallsPanel.vue'
import type { ChatMessage } from '@/types'

const props = withDefaults(
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
const lastAssistantMessageId = computed(() => {
  for (let index = props.messages.length - 1; index >= 0; index -= 1) {
    const message = props.messages[index]
    if (message.role === 'assistant') return message.id
  }
  return ''
})

function imageSrc(content?: string) {
  if (!content) return ''
  if (content.startsWith('data:') || content.startsWith('blob:')) return content
  return `data:image/jpeg;base64,${content}`
}

function displayValue(value: unknown) {
  return Array.isArray(value) ? value.join('、') : String(value ?? '')
}

function hasSummaryDetails(message: ChatMessage) {
  const summary = message.contextSummary
  return Boolean(summary?.items.length)
}

function isSummaryReady(message: ChatMessage) {
  const result = message.contextSummary?.result
  return result?.matchStatus === 'matched' && result.extractionStatus === 'ready'
}

function contextSummaryTone(message: ChatMessage) {
  if (message.contextSummary?.error || message.contextSummary?.result?.extractionStatus === 'failed') return 'error'
  if (message.contextSummary?.loading || message.contextSummary?.result?.extractionStatus === 'running') return 'loading'
  if (isSummaryReady(message)) return 'ready'
  return 'unavailable'
}

function summaryEmptyText(message: ChatMessage) {
  const summary = message.contextSummary
  if (summary?.loading) return '正在查询当前附件的结构化摘要。'
  if (summary?.error) return '结构化摘要查询失败，可刷新页面或稍后重试。'
  if (!summary?.result) return '等待后端返回当前附件的结构化摘要。'
  if (summary.result.matchStatus === 'pending_sync') return '当前附件还没有同步到 docMind 知识库，暂时无法展示结构化摘要。'
  if (summary.result.matchStatus === 'not_found') return '未在 docMind 中匹配到当前附件，暂时没有可展示的结构化摘要。'
  if (summary.result.matchStatus === 'multiple') return '匹配到多个同名或相近附件，需要后端进一步消歧后才能展示结构化摘要。'
  if (summary.result.extractionStatus === 'running') return '结构化抽取任务正在运行，完成后会展示分类和明细。'
  if (summary.result.extractionStatus === 'failed') return summary.result.reason || '结构化抽取失败，暂时没有可展示的摘要。'
  return '暂无结构化摘要明细。'
}

function hasToolCalls(message: ChatMessage) {
  return Boolean(message.toolCalls?.length)
}

function showThinkingPlaceholder(message: ChatMessage) {
  return !message.content && !message.reasoningContent && !hasToolCalls(message)
}

function showAssistantRefs(message: ChatMessage) {
  return message.role === 'assistant' && message.status === 'done' && message.id === lastAssistantMessageId.value
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
      <template v-if="message.type === 'context_summary' && message.contextSummary">
        <div class="context-summary-card" :class="[contextSummaryTone(message), { unavailable: !hasSummaryDetails(message) }]">
          <div class="context-summary-header">
            <div>
              <h2>文档摘要</h2>
            </div>
          </div>
          <p class="context-summary-file" :title="message.contextSummary.file.name">
            {{ message.contextSummary.file.name }}
          </p>
          <div v-if="!hasSummaryDetails(message)" class="context-summary-empty">
            <strong>{{ message.contextSummary.statusText }}</strong>
            <p>{{ summaryEmptyText(message) }}</p>
          </div>
          <section v-if="hasSummaryDetails(message)" class="context-summary-section">
            <p v-if="!message.contextSummary.items.length" class="muted">暂无结构化明细</p>
            <article v-for="item in message.contextSummary.items.slice(0, 3)" :key="item.item_id" class="item-row">
              <strong>{{ item.item_type }}</strong>
              <dl v-if="item.data && Object.keys(item.data).length">
                <template v-for="[key, value] in Object.entries(item.data)" :key="key">
                  <dt>{{ key }}</dt>
                  <dd>{{ displayValue(value) }}</dd>
                </template>
              </dl>
              <blockquote v-if="item.source_quote">{{ item.source_quote }}</blockquote>
            </article>
          </section>
        </div>
      </template>
      <template v-else>
        <div v-if="message.role !== 'assistant' && message.role !== 'user'" class="message-role">
          {{ message.role === 'tool' ? '工具' : '系统' }}
        </div>
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
        <p v-else-if="showThinkingPlaceholder(message)" class="muted">正在思考...</p>
        <p v-if="message.errorMessage" class="error-hint">{{ message.errorMessage }}</p>
        <ToolCallsPanel :tool-calls="message.toolCalls || []" />
        <MessageRefs
          v-if="showAssistantRefs(message)"
          :message="message"
          @retry="$emit('retry')"
          @feedback="$emit('feedback', $event)"
        />
      </div>
      </template>
    </article>
  </section>
</template>
