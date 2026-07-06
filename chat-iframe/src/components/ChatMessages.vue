<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import MarkdownPreview from '@/components/MarkdownPreview.vue'
import MessageRefs from '@/components/MessageRefs.vue'
import ToolCallsPanel from '@/components/ToolCallsPanel.vue'
import type { ChatMessage } from '@/types'
import { groupMessageDisplayItems } from '@/utils/message-display'

const props = withDefaults(
  defineProps<{
    messages?: ChatMessage[]
    loading?: boolean
    streaming?: boolean
  }>(),
  { messages: () => [], loading: false, streaming: false }
)

defineEmits<{
  retry: []
  feedback: [payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }]
}>()

const openReasoning = ref<Record<string, boolean>>({})
const messagesEl = ref<HTMLElement | null>(null)
const displayItems = computed(() => groupMessageDisplayItems(props.messages))
const showGeneratingStatus = computed(() => props.streaming && props.messages.some((message) => message.role === 'user'))
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

function hasRunningToolCalls(toolCalls: Array<{ status?: string }> = []) {
  return toolCalls.some((tool) => tool.status === 'running')
}

function showThinkingPlaceholder(message: ChatMessage) {
  return !props.streaming && !message.content && !message.reasoningContent && !hasToolCalls(message)
}

function showAssistantRefs(message: ChatMessage) {
  return message.role === 'assistant' && message.status === 'done' && message.id === lastAssistantMessageId.value
}

async function scrollToBottom() {
  if (!props.streaming) return
  await nextTick()
  requestAnimationFrame(() => {
    const el = messagesEl.value
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  })
}

watch([displayItems, showGeneratingStatus], scrollToBottom, { flush: 'post', deep: true })
</script>

<template>
  <section ref="messagesEl" class="chat-messages">
    <p v-if="loading" class="empty">正在加载聊天记录...</p>
    <div v-else-if="!messages.length" class="chat-welcome">
      <strong>可以直接提问</strong>
      <span>默认会带上当前页面和选中文档的结构化结果。</span>
    </div>
    <template v-else>
    <article
      v-for="item in displayItems"
      :key="item.key"
      class="chat-message"
      :class="item.type === 'tool-group' ? ['assistant', 'tool-group-message'] : [item.message.role, item.message.status]"
    >
      <template v-if="item.type === 'tool-group'">
        <div class="message-content">
          <ToolCallsPanel :tool-calls="item.toolCalls" :is-active="hasRunningToolCalls(item.toolCalls)" />
        </div>
      </template>

      <template v-else-if="item.message.type === 'context_summary' && item.message.contextSummary">
        <div class="context-summary-card" :class="[contextSummaryTone(item.message), { unavailable: !hasSummaryDetails(item.message) }]">
          <div class="context-summary-header">
            <div>
              <h2>文档摘要</h2>
            </div>
          </div>
          <p class="context-summary-file" :title="item.message.contextSummary.file.name">
            {{ item.message.contextSummary.file.name }}
          </p>
          <div v-if="!hasSummaryDetails(item.message)" class="context-summary-empty">
            <strong>{{ item.message.contextSummary.statusText }}</strong>
            <p>{{ summaryEmptyText(item.message) }}</p>
          </div>
          <section v-if="hasSummaryDetails(item.message)" class="context-summary-section">
            <p v-if="!item.message.contextSummary.items.length" class="muted">暂无结构化明细</p>
            <article v-for="summaryItem in item.message.contextSummary.items.slice(0, 3)" :key="summaryItem.item_id" class="item-row">
              <strong>{{ summaryItem.item_type }}</strong>
              <dl v-if="summaryItem.data && Object.keys(summaryItem.data).length">
                <template v-for="[key, value] in Object.entries(summaryItem.data)" :key="key">
                  <dt>{{ key }}</dt>
                  <dd>{{ displayValue(value) }}</dd>
                </template>
              </dl>
              <blockquote v-if="summaryItem.source_quote">{{ summaryItem.source_quote }}</blockquote>
            </article>
          </section>
        </div>
      </template>

      <template v-else>
        <div v-if="item.message.role !== 'assistant' && item.message.role !== 'user'" class="message-role">
          {{ item.message.role === 'tool' ? '工具' : '系统' }}
        </div>
        <div class="message-content">
          <img v-if="item.message.imageContent" class="message-image" :src="imageSrc(item.message.imageContent)" alt="用户上传图片" />
          <div v-if="item.message.attachments?.length" class="message-attachments">
            <span v-for="attachment in item.message.attachments" :key="String(attachment.file_id || attachment.file_name || attachment.name)">
              {{ attachment.file_name || attachment.name }}
            </span>
          </div>
          <details v-if="item.message.reasoningContent" class="reasoning-box" :open="openReasoning[item.message.id]">
            <summary @click.prevent="openReasoning[item.message.id] = !openReasoning[item.message.id]">
              {{ item.message.status === 'streaming' ? '正在思考...' : '推理过程' }}
            </summary>
            <p>{{ item.message.reasoningContent }}</p>
          </details>
          <MarkdownPreview v-if="item.message.content" :content="item.message.content" />
          <p v-else-if="showThinkingPlaceholder(item.message)" class="muted">正在思考...</p>
          <p v-if="item.message.errorMessage" class="error-hint">{{ item.message.errorMessage }}</p>
          <MessageRefs
            v-if="showAssistantRefs(item.message)"
            :message="item.message"
            @retry="$emit('retry')"
            @feedback="$emit('feedback', $event)"
          />
        </div>
      </template>
    </article>
    <div v-if="showGeneratingStatus" class="generating-status" aria-live="polite">
      <div class="generating-indicator">
        <div class="loading-dots" aria-hidden="true">
          <div></div>
          <div></div>
          <div></div>
        </div>
        <span class="generating-text">正在生成回复...</span>
      </div>
    </div>
    </template>
  </section>
</template>
