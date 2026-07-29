<script setup lang="ts">
import {
  Check,
  Circle,
  CircleCheck,
  Copy,
  Download,
  Eye,
  FileText,
  Image as ImageIcon,
  LoaderCircle,
  X
} from 'lucide-vue-next'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import MarkdownPreview from '@/components/MarkdownPreview.vue'
import MessageRefs from '@/components/MessageRefs.vue'
import ExecutionProcessPanel from '@/components/ExecutionProcessPanel.vue'
import { fetchThreadArtifact } from '@/apis/chat'
import type { ChatArtifact, ChatMessage, ExtractionItem, IncomingPageFile } from '@/types'
import {
  displayExtractionDataEntries,
  extractionClassificationText,
  extractionItemTypeText,
  extractionSummaryText
} from '@/utils/context-summary'
import { groupMessageDisplayItems } from '@/utils/message-display'
import { extractFinalAnswerSources } from '@/utils/tool-calls'
import { copyToClipboard } from '@/utils/clipboard'

const props = withDefaults(
  defineProps<{
    messages?: ChatMessage[]
    loading?: boolean
    streaming?: boolean
    compacting?: boolean
    showRunProgress?: boolean
    agentState?: Record<string, unknown> | null
    threadId?: string
    token?: string
    historyScrollRequest?: number
  }>(),
  {
    messages: () => [],
    loading: false,
    streaming: false,
    compacting: false,
    showRunProgress: false,
    agentState: null,
    threadId: '',
    token: '',
    historyScrollRequest: 0
  }
)

defineEmits<{
  feedback: [payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }]
}>()

const openReasoning = ref<Record<string, boolean>>({})
const messagesEl = ref<HTMLElement | null>(null)
const copiedUserMessageId = ref('')
const imagePreview = ref({ src: '', alt: '' })
const artifactPreview = ref<{
  name: string
  kind: 'image' | 'pdf' | 'text'
  url?: string
  text?: string
} | null>(null)
const artifactBusyPath = ref('')
const artifactError = ref('')
const inlineSvgUrls = ref<Record<string, string>>({})
const displayItems = computed(() =>
  groupMessageDisplayItems(props.messages, { streaming: props.streaming })
)
const showGeneratingStatus = computed(
  () => props.streaming && props.messages.some((message) => message.role === 'user')
)
const runTodos = computed(() => {
  const todos = props.agentState?.todos
  if (!Array.isArray(todos)) return []
  return todos
    .map((todo, index) => {
      const item = todo && typeof todo === 'object' ? (todo as Record<string, unknown>) : {}
      const status = String(item.status || 'pending').toLowerCase()
      return {
        id: String(item.id || index),
        content: String(item.content || item.title || item.task || '').trim(),
        status:
          status === 'completed' || status === 'done'
            ? 'done'
            : status === 'in_progress' || status === 'running'
              ? 'running'
              : 'pending'
      }
    })
    .filter((todo) => todo.content)
})
const completedTodoCount = computed(
  () => runTodos.value.filter((todo) => todo.status === 'done').length
)
const showRunProgress = computed(() => props.streaming && props.showRunProgress && runTodos.value.length > 0)
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
  return size >= 1024 * 1024
    ? `${(size / 1024 / 1024).toFixed(1)} MB`
    : `${(size / 1024).toFixed(1)} KB`
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
  if (!(await copyToClipboard(message.content))) return
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

function artifactKind(artifact: ChatArtifact): 'image' | 'pdf' | 'text' | null {
  // 交付物 path 可能是虚拟目录或工具回传值；界面类型必须以用户可见文件名为准，读取仍使用 path。
  const extension = (artifact.name || artifact.path).split('.').pop()?.toLowerCase() || ''
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(extension)) return 'image'
  if (extension === 'pdf') return 'pdf'
  if (['txt', 'md', 'json', 'csv', 'yaml', 'yml', 'xml', 'html', 'log'].includes(extension))
    return 'text'
  return null
}

function isInlineSvgArtifact(artifact: ChatArtifact) {
  return artifactKind(artifact) === 'image' && artifact.name.toLowerCase().endsWith('.svg')
}

function inlineSvgKey(artifact: ChatArtifact) {
  return `${props.threadId}\u0000${artifact.path}`
}

function inlineSvgUrl(artifact: ChatArtifact) {
  return inlineSvgUrls.value[inlineSvgKey(artifact)] || ''
}

function clearInlineSvgUrls() {
  Object.values(inlineSvgUrls.value).forEach((url) => URL.revokeObjectURL(url))
  inlineSvgUrls.value = {}
}

async function preloadRecentInlineSvgs() {
  const threadId = props.threadId
  if (!threadId || !props.token) return
  const artifacts = props.messages
    .flatMap((message) => message.artifacts || [])
    .filter(isInlineSvgArtifact)
    .slice(-3)

  for (const artifact of artifacts) {
    const key = `${threadId}\u0000${artifact.path}`
    if (inlineSvgUrls.value[key]) continue
    try {
      const response = await fetchThreadArtifact(threadId, artifact.path, props.token)
      const url = URL.createObjectURL(await response.blob())
      if (props.threadId !== threadId) {
        URL.revokeObjectURL(url)
        return
      }
      inlineSvgUrls.value = { ...inlineSvgUrls.value, [key]: url }
    } catch {
      // 内联预览失败不应影响交付物下载或现有的弹层预览能力。
    }
  }
}

function closeArtifactPreview() {
  if (artifactPreview.value?.url) URL.revokeObjectURL(artifactPreview.value.url)
  artifactPreview.value = null
}

async function previewArtifact(artifact: ChatArtifact) {
  const kind = artifactKind(artifact)
  if (!kind || !props.threadId || artifactBusyPath.value) return
  artifactBusyPath.value = artifact.path
  artifactError.value = ''
  try {
    const response = await fetchThreadArtifact(props.threadId, artifact.path, props.token)
    if (kind === 'text') {
      artifactPreview.value = { name: artifact.name, kind, text: await response.text() }
    } else {
      artifactPreview.value = {
        name: artifact.name,
        kind,
        url: URL.createObjectURL(await response.blob())
      }
    }
  } catch (error) {
    artifactError.value = error instanceof Error ? error.message : '交付物预览失败'
  } finally {
    artifactBusyPath.value = ''
  }
}

async function downloadArtifact(artifact: ChatArtifact) {
  if (!props.threadId || artifactBusyPath.value) return
  artifactBusyPath.value = artifact.path
  artifactError.value = ''
  try {
    const response = await fetchThreadArtifact(props.threadId, artifact.path, props.token, true)
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = artifact.name
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) {
    artifactError.value = error instanceof Error ? error.message : '交付物下载失败'
  } finally {
    artifactBusyPath.value = ''
  }
}

function displayValue(value: unknown) {
  return Array.isArray(value) ? value.join('、') : String(value ?? '')
}

function contextSummaryMetadata(file: IncomingPageFile) {
  // 标签按阅读优先级排列，时间固定收尾，来源和文号暂不在小助手展示。
  return [
    ['incoming-type', '来文类型', file.incoming_type || '无'],
    ['source-unit', '发文单位', file.source_unit || '无'],
    ['incoming-date', '时间', file.incoming_date || '无']
  ]
}

function contextSummaryItemGroups(message: ChatMessage): ContextSummaryItemGroup[] {
  const summary = message.contextSummary
  if (!summary?.file.is_main_file) return []
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
    ...schemaIds
      .filter((itemType) => groups.has(itemType))
      .map((itemType) => groups.get(itemType)!),
    ...Array.from(groups.values()).filter((group) => !schemaIds.includes(group.itemType))
  ]
}

function hasSummaryDetails(message: ChatMessage) {
  const summary = message.contextSummary
  return Boolean(
    summary?.matchedCategories.length ||
    summary?.items.length ||
    extractionSummaryText(summary?.result) ||
    summary?.attachments.some((attachment) => extractionSummaryText(attachment.result))
  )
}

function supplementaryAttachments(message: ChatMessage) {
  const summary = message.contextSummary
  return (summary?.attachments || []).filter(
    (attachment) => attachment.file.source_file_id !== summary?.file.source_file_id
  )
}

function isSummaryReady(message: ChatMessage) {
  const result = message.contextSummary?.result
  return result?.matchStatus === 'matched' && result.extractionStatus === 'ready'
}

function contextSummaryTone(message: ChatMessage) {
  if (
    message.contextSummary?.error ||
    message.contextSummary?.result?.extractionStatus === 'failed'
  )
    return 'error'
  if (
    message.contextSummary?.loading ||
    message.contextSummary?.result?.extractionStatus === 'running'
  )
    return 'loading'
  if (isSummaryReady(message)) return 'ready'
  return 'unavailable'
}

function summaryEmptyText(message: ChatMessage) {
  const summary = message.contextSummary
  if (summary?.loading) return '正在查询当前附件的结构化摘要。'
  if (summary?.error) return '结构化摘要查询失败，可刷新页面或稍后重试。'
  if (!summary?.result) return '等待后端返回当前附件的结构化摘要。'
  if (summary.result.matchStatus === 'pending_sync')
    return '当前附件还没有同步到 docMind 知识库，暂时无法展示结构化摘要。'
  if (summary.result.matchStatus === 'not_found')
    return '未在 docMind 中匹配到当前附件，暂时没有可展示的结构化摘要。'
  if (summary.result.matchStatus === 'multiple')
    return '匹配到多个同名或相近附件，需要后端进一步消歧后才能展示结构化摘要。'
  if (summary.result.extractionStatus === 'running')
    return '结构化抽取任务正在运行，完成后会展示分类和明细。'
  if (summary.result.extractionStatus === 'failed')
    return summary.result.reason || '结构化抽取失败，暂时没有可展示的摘要。'
  return '暂无结构化摘要明细。'
}

function showThinkingPlaceholder(message: ChatMessage) {
  return !props.streaming && !message.content && !message.reasoningContent
}

function showAssistantRefs(message: ChatMessage) {
  return (
    message.role === 'assistant' &&
    message.status === 'done' &&
    message.id === lastAssistantMessageId.value
  )
}

function assistantSources(message: ChatMessage) {
  return extractFinalAnswerSources(props.messages, message.id)
}

async function scrollToBottom(behavior: 'auto' | 'smooth') {
  await nextTick()
  requestAnimationFrame(() => {
    const el = messagesEl.value
    if (el) el.scrollTo({ top: el.scrollHeight, behavior })
  })
}

function scrollStreamingToBottom() {
  if (props.streaming) void scrollToBottom('smooth')
}

watch([displayItems, showGeneratingStatus, showRunProgress, () => props.compacting], scrollStreamingToBottom, {
  flush: 'post'
})
// 历史加载完成与流式增量是两种不同的滚动语义：切换会话必须立即落到底部，
// 不能复用 streaming 判断，否则已完成会话会停留在列表顶部。
watch(
  () => props.historyScrollRequest,
  () => void scrollToBottom('auto'),
  { flush: 'post' }
)
watch(() => props.threadId, clearInlineSvgUrls, { immediate: true })
// 交付物会在运行结束后补挂到最终消息；直接跟踪实际驱动卡片渲染的显示项，避免原始消息数组的更新时机错过预加载。
watch(
  [displayItems, () => props.threadId, () => props.token],
  () => void preloadRecentInlineSvgs(),
  { deep: true, immediate: true }
)
onUnmounted(() => {
  closeImagePreview()
  closeArtifactPreview()
  clearInlineSvgUrls()
})
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
        :class="
          item.type === 'execution-process'
            ? ['assistant', 'execution-process-message']
            : [item.message.role, item.message.status]
        "
      >
        <template v-if="item.type === 'execution-process'">
          <div class="message-content">
            <ExecutionProcessPanel
              :messages="item.messages"
              :is-active="item.isActive"
              :has-final-answer="item.hasFinalAnswer"
            />
          </div>
        </template>

        <template
          v-else-if="item.message.type === 'context_summary' && item.message.contextSummary"
        >
          <div
            class="context-summary-card"
            :class="[
              contextSummaryTone(item.message),
              { unavailable: !hasSummaryDetails(item.message) }
            ]"
          >
            <p class="context-summary-document-title">
              {{ item.message.contextSummary.file.title || '文档摘要' }}
            </p>
            <div class="context-summary-metadata">
              <span
                v-if="extractionClassificationText(item.message.contextSummary.result)"
                class="classification-badge"
              >
                {{ extractionClassificationText(item.message.contextSummary.result) }}
              </span>
              <template
                v-for="[kind, label, value] in contextSummaryMetadata(
                  item.message.contextSummary.file
                )"
                :key="kind"
              >
                <span class="context-summary-meta" :class="kind">
                  <span class="context-summary-meta-label">{{ label }}</span>
                  <span>{{ value }}</span>
                </span>
              </template>
            </div>
            <div v-if="!hasSummaryDetails(item.message)" class="context-summary-empty">
              <strong>{{ item.message.contextSummary.statusText }}</strong>
              <p>{{ summaryEmptyText(item.message) }}</p>
            </div>
            <section v-if="hasSummaryDetails(item.message)" class="context-summary-section">
              <article class="item-row context-summary-attachment">
                <p class="context-summary-file" :title="item.message.contextSummary.file.name">
                  <span>{{ item.message.contextSummary.file.name }}</span>
                </p>
                <strong>摘要</strong>
                <blockquote v-if="extractionSummaryText(item.message.contextSummary.result)">
                  {{ extractionSummaryText(item.message.contextSummary.result) }}
                </blockquote>
                <p v-else class="muted">暂无摘要</p>
                <details
                  v-for="group in contextSummaryItemGroups(item.message)"
                  :key="group.itemType"
                  class="context-summary-group"
                >
                  <summary>{{ group.label }}（{{ group.items.length }}）</summary>
                  <article
                    v-for="(summaryItem, index) in group.items"
                    :key="summaryItem.item_id"
                    class="item-row"
                  >
                    <strong>{{ group.label }} {{ index + 1 }}</strong>
                    <dl
                      v-if="
                        displayExtractionDataEntries(
                          summaryItem.data,
                          summaryItem.item_type,
                          item.message.contextSummary.result
                        ).length
                      "
                    >
                      <template
                        v-for="[key, value] in displayExtractionDataEntries(
                          summaryItem.data,
                          summaryItem.item_type,
                          item.message.contextSummary.result
                        )"
                        :key="key"
                      >
                        <dt>{{ key }}</dt>
                        <dd>{{ displayValue(value) }}</dd>
                      </template>
                    </dl>
                    <blockquote v-if="summaryItem.source_quote">
                      {{ summaryItem.source_quote }}
                    </blockquote>
                  </article>
                </details>
              </article>
              <article
                v-for="attachment in supplementaryAttachments(item.message)"
                :key="attachment.file.source_file_id"
                class="item-row context-summary-attachment"
              >
                <p class="context-summary-file" :title="attachment.file.name">
                  <span>{{ attachment.file.name }}</span>
                </p>
                <strong>摘要</strong>
                <blockquote v-if="extractionSummaryText(attachment.result)">
                  {{ extractionSummaryText(attachment.result) }}
                </blockquote>
                <p v-else class="muted">暂无摘要</p>
              </article>
            </section>
          </div>
        </template>

        <template v-else>
          <div
            v-if="item.message.role !== 'assistant' && item.message.role !== 'user'"
            class="message-role"
          >
            {{ item.message.role === 'tool' ? '工具' : '系统' }}
          </div>
          <button
            v-if="item.message.role === 'user' && item.message.content"
            type="button"
            class="user-message-copy"
            title="复制消息"
            @click="copyUserMessage(item.message)"
          >
            <Check v-if="copiedUserMessageId === item.message.id" :size="13" />
            <Copy v-else :size="13" />
          </button>
          <div class="message-content">
            <button
              v-if="item.message.imageContent"
              type="button"
              class="message-image-button"
              title="查看大图"
              @click="openImagePreview(item.message.imageContent)"
            >
              <img
                class="message-image"
                :src="imageSrc(item.message.imageContent)"
                alt="用户上传图片"
              />
            </button>
            <div v-if="item.message.attachments?.length" class="message-attachments">
              <article
                v-for="attachment in item.message.attachments"
                :key="String(attachment.file_id || attachment.file_name || attachment.name)"
                class="message-attachment"
              >
                <ImageIcon v-if="isImageAttachment(attachment.file_type)" :size="16" />
                <FileText v-else :size="16" />
                <div>
                  <strong :title="String(attachment.file_name || attachment.name || '')">{{
                    attachment.file_name || attachment.name
                  }}</strong>
                  <small>{{
                    [formatFileSize(attachment.file_size), attachmentStatus(attachment.status)]
                      .filter(Boolean)
                      .join(' · ')
                  }}</small>
                </div>
              </article>
            </div>
            <details
              v-if="item.message.reasoningContent"
              class="reasoning-box"
              :open="openReasoning[item.message.id]"
            >
              <summary
                @click.prevent="openReasoning[item.message.id] = !openReasoning[item.message.id]"
              >
                {{ item.message.status === 'streaming' ? '正在思考...' : '推理过程' }}
              </summary>
              <p>{{ item.message.reasoningContent }}</p>
            </details>
            <MarkdownPreview v-if="item.message.content" :content="item.message.content" />
            <p v-else-if="showThinkingPlaceholder(item.message)" class="muted">正在思考...</p>
            <p v-if="item.message.errorMessage" class="error-hint">
              {{ item.message.errorMessage }}
            </p>
            <section v-if="item.message.artifacts?.length" class="message-artifacts">
              <header>
                <strong>本轮交付物（{{ item.message.artifacts.length }}）</strong>
              </header>
              <article v-for="artifact in item.message.artifacts" :key="artifact.path">
                <div class="artifact-row">
                  <FileText :size="16" />
                  <span :title="artifact.name">{{ artifact.name }}</span>
                  <button
                    v-if="artifactKind(artifact)"
                    type="button"
                    :disabled="Boolean(artifactBusyPath)"
                    title="预览交付物"
                    @click="previewArtifact(artifact)"
                  >
                    <Eye :size="14" />
                  </button>
                  <button
                    type="button"
                    :disabled="Boolean(artifactBusyPath)"
                    title="下载交付物"
                    @click="downloadArtifact(artifact)"
                  >
                    <Download :size="14" />
                  </button>
                </div>
                <button
                  v-if="inlineSvgUrl(artifact)"
                  type="button"
                  class="artifact-inline-preview"
                  title="打开完整预览"
                  :disabled="Boolean(artifactBusyPath)"
                  @click="previewArtifact(artifact)"
                >
                  <img
                    class="artifact-inline-svg"
                    :src="inlineSvgUrl(artifact)"
                    :alt="artifact.name"
                  />
                </button>
              </article>
              <p v-if="artifactError" class="error-hint">{{ artifactError }}</p>
            </section>
            <MessageRefs
              v-if="showAssistantRefs(item.message)"
              :message="item.message"
              :sources="assistantSources(item.message)"
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
          <span class="generating-text">{{
            compacting ? '正在压缩历史对话...' : '正在生成回复...'
          }}</span>
        </div>
      </div>
      <section v-if="showRunProgress" class="run-progress-card" aria-live="polite">
        <header>
          <LoaderCircle :size="15" class="run-progress-spinner" />
          <strong>本轮进度</strong>
          <span>已完成 {{ completedTodoCount }}/{{ runTodos.length }}</span>
        </header>
        <ol>
          <li v-for="todo in runTodos" :key="todo.id" :class="`is-${todo.status}`">
            <CircleCheck v-if="todo.status === 'done'" :size="15" />
            <LoaderCircle
              v-else-if="todo.status === 'running'"
              :size="15"
              class="run-progress-spinner"
            />
            <Circle v-else :size="15" />
            <span>{{ todo.content }}</span>
          </li>
        </ol>
      </section>
      <Teleport to="body">
        <div
          v-if="imagePreview.src"
          class="message-image-preview-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="图片预览"
          @click="closeImagePreview"
        >
          <button
            type="button"
            class="message-image-preview-close"
            title="关闭"
            @click.stop="closeImagePreview"
          >
            ×
          </button>
          <img
            :src="imagePreview.src"
            :alt="imagePreview.alt"
            class="message-image-preview-img"
            @click.stop
          />
        </div>
      </Teleport>
      <Teleport to="body">
        <div
          v-if="artifactPreview"
          class="artifact-preview-overlay"
          role="dialog"
          aria-modal="true"
          :aria-label="`${artifactPreview.name} 预览`"
          @click="closeArtifactPreview"
        >
          <section
            class="artifact-preview-dialog"
            :class="{ 'is-image': artifactPreview.kind === 'image' }"
            @click.stop
          >
            <header>
              <strong>{{ artifactPreview.name }}</strong
              ><button type="button" title="关闭预览" @click="closeArtifactPreview">
                <X :size="17" />
              </button>
            </header>
            <div v-if="artifactPreview.kind === 'image'" class="artifact-preview-image-viewport">
              <img :src="artifactPreview.url" :alt="artifactPreview.name" />
            </div>
            <iframe
              v-else-if="artifactPreview.kind === 'pdf'"
              :src="artifactPreview.url"
              :title="artifactPreview.name"
            />
            <pre v-else>{{ artifactPreview.text }}</pre>
          </section>
        </div>
      </Teleport>
    </template>
  </section>
</template>

<style scoped>
.context-summary-document-title {
  margin: 0;
  color: var(--gray-800);
  font-size: 15px;
  font-weight: 600;
  line-height: 1.45;
}
</style>
