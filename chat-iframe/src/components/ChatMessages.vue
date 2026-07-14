<script setup lang="ts">
import { Check, Copy, FileText, Image as ImageIcon } from 'lucide-vue-next'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import MarkdownPreview from '@/components/MarkdownPreview.vue'
import MessageRefs from '@/components/MessageRefs.vue'
import ToolCallsPanel from '@/components/ToolCallsPanel.vue'
import type { ChatMessage, ExtractionItem } from '@/types'
import {
  displayExtractionDataEntries,
  extractionClassificationText,
  extractionItemTypeText,
  extractionSummaryText
} from '@/utils/context-summary'
import { groupMessageDisplayItems } from '@/utils/message-display'
import { extractFinalAnswerSources } from '@/utils/tool-calls'

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
const copiedUserMessageId = ref('')
const imagePreview = ref({ src: '', alt: '' })
const displayItems = computed(() => groupMessageDisplayItems(props.messages))
const showGeneratingStatus = computed(() => props.streaming && props.messages.some((message) => message.role === 'user'))
const lastAssistantMessageId = computed(() => {
  for (let index = props.messages.length - 1; index >= 0; index -= 1) {
    const message = props.messages[index]
    if (message.role === 'assistant') return message.id
  }
  return ''
})

type ContextSummaryItemGroup = {
  itemType: string
  label: string
  items: ExtractionItem[]
}

function imageSrc(content?: string) {
  if (!content) return ''
  if (content.startsWith('data:') || content.startsWith('blob:')) return content
  return `data:image/jpeg;base64,${content}`
}

function formatFileSize(size?: number) {
  if (!Number.isFinite(size) || !size) return ''
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024).toFixed(1)} KB`
}

function attachmentStatus(status?: string) {
  if (status === 'uploading') return '上传中'
  if (status === 'error') return '上传失败'
  if (status === 'parsed') return '已解析'
  return '已上传'
}

function isImageAttachment(type?: string) {
  return Boolean(type?.startsWith('image/'))
}

async function copyUserMessage(message: ChatMessage) {
  await navigator.clipboard?.writeText(message.content)
  copiedUserMessageId.value = message.id
  window.setTimeout(() => {
    if (copiedUserMessageId.value === message.id) copiedUserMessageId.value = ''
  }, 1500)
}

function openImagePreview(content?: string) {
  const src = imageSrc(content)
  if (!src) return
  imagePreview.value = { src, alt: '用户上传图片' }
  window.addEventListener('keydown', closeImagePreviewOnEscape)
}

function closeImagePreview() {
  imagePreview.value = { src: '', alt: '' }
  window.removeEventListener('keydown', closeImagePreviewOnEscape)
}

function closeImagePreviewOnEscape(event: KeyboardEvent) {
  if (event.key === 'Escape') closeImagePreview()
}

function displayValue(value: unknown) {
  return Array.isArray(value) ? value.join('、') : String(value ?? '')
}

function contextSummaryItemGroups(message: ChatMessage): ContextSummaryItemGroup[] {
  const summary = message.contextSummary
  const groups = new Map<string, ContextSummaryItemGroup>()
  for (const item of summary?.items || []) {
    const itemType = item.item_type || 'unknown'
    if (!groups.has(itemType)) {
      groups.set(itemType, {
        itemType,
        label: extractionItemTypeText(itemType, summary?.result),
        items: []
      })
    }
    groups.get(itemType)?.items.push(item)
  }
  const schemaIds = summary?.result?.schemaIds || []
  return [
    ...schemaIds.filter((itemType) => groups.has(itemType)).map((itemType) => groups.get(itemType)!),
    ...Array.from(groups.values()).filter((group) => !schemaIds.includes(group.itemType))
  ]
}

function hasSummaryDetails(message: ChatMessage) {
  const summary = message.contextSummary
  return Boolean(summary?.matchedCategories.length || summary?.items.length || extractionSummaryText(summary?.result))
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

function assistantSources(message: ChatMessage) {
  return extractFinalAnswerSources(props.messages, message.id)
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
onUnmounted(closeImagePreview)
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
            <span>{{ item.message.contextSummary.file.name }}</span>
            <span v-if="extractionClassificationText(item.message.contextSummary.result)" class="classification-badge">
              {{ extractionClassificationText(item.message.contextSummary.result) }}
            </span>
          </p>
          <div v-if="!hasSummaryDetails(item.message)" class="context-summary-empty">
            <strong>{{ item.message.contextSummary.statusText }}</strong>
            <p>{{ summaryEmptyText(item.message) }}</p>
          </div>
          <section v-if="hasSummaryDetails(item.message)" class="context-summary-section">
            <article v-if="extractionSummaryText(item.message.contextSummary.result)" class="item-row">
              <strong>摘要</strong>
              <blockquote>{{ extractionSummaryText(item.message.contextSummary.result) }}</blockquote>
            </article>
            <p v-if="!item.message.contextSummary.items.length && !extractionSummaryText(item.message.contextSummary.result)" class="muted">暂无结构化明细</p>
            <details
              v-for="group in contextSummaryItemGroups(item.message)"
              :key="group.itemType"
              class="context-summary-group"
              open
            >
              <summary>{{ group.label }}（{{ group.items.length }}）</summary>
              <article v-for="(summaryItem, index) in group.items" :key="summaryItem.item_id" class="item-row">
                <strong>{{ group.label }} {{ index + 1 }}</strong>
                <dl v-if="displayExtractionDataEntries(summaryItem.data, summaryItem.item_type, item.message.contextSummary.result).length">
                  <template v-for="[key, value] in displayExtractionDataEntries(summaryItem.data, summaryItem.item_type, item.message.contextSummary.result)" :key="key">
                    <dt>{{ key }}</dt>
                    <dd>{{ displayValue(value) }}</dd>
                  </template>
                </dl>
                <blockquote v-if="summaryItem.source_quote">{{ summaryItem.source_quote }}</blockquote>
              </article>
            </details>
          </section>
        </div>
      </template>

      <template v-else>
        <div v-if="item.message.role !== 'assistant' && item.message.role !== 'user'" class="message-role">
          {{ item.message.role === 'tool' ? '工具' : '系统' }}
        </div>
        <div class="message-content">
          <button
            v-if="item.message.imageContent"
            type="button"
            class="message-image-button"
            title="查看大图"
            @click="openImagePreview(item.message.imageContent)"
          >
            <img class="message-image" :src="imageSrc(item.message.imageContent)" alt="用户上传图片" />
          </button>
          <div v-if="item.message.attachments?.length" class="message-attachments">
            <article v-for="attachment in item.message.attachments" :key="String(attachment.file_id || attachment.file_name || attachment.name)" class="message-attachment">
              <ImageIcon v-if="isImageAttachment(attachment.file_type)" :size="16" />
              <FileText v-else :size="16" />
              <div>
                <strong :title="String(attachment.file_name || attachment.name || '')">{{ attachment.file_name || attachment.name }}</strong>
                <small>{{ [formatFileSize(attachment.file_size), attachmentStatus(attachment.status)].filter(Boolean).join(' · ') }}</small>
              </div>
            </article>
          </div>
          <button v-if="item.message.role === 'user' && item.message.content" type="button" class="user-message-copy" title="复制消息" @click="copyUserMessage(item.message)">
            <Check v-if="copiedUserMessageId === item.message.id" :size="13" />
            <Copy v-else :size="13" />
          </button>
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
            :sources="assistantSources(item.message)"
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
    <Teleport to="body">
      <div v-if="imagePreview.src" class="message-image-preview-overlay" role="dialog" aria-modal="true" aria-label="图片预览" @click="closeImagePreview">
        <button type="button" class="message-image-preview-close" title="关闭" @click.stop="closeImagePreview">×</button>
        <img :src="imagePreview.src" :alt="imagePreview.alt" class="message-image-preview-img" @click.stop />
      </div>
    </Teleport>
    </template>
  </section>
</template>
