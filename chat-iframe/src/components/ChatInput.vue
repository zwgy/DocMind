<script setup lang="ts">
import {
  CheckSquare,
  ChevronDown,
  FileText,
  Gauge,
  Globe2,
  Image,
  Paperclip,
  Search,
  SendHorizontal,
  Square,
  Square as SquareIcon,
  X
} from 'lucide-vue-next'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { IncomingPageFile, ModelOption } from '@/types'
import {
  ATTACHMENT_LIMIT_TEXT,
  IMAGE_ACCEPT,
  IMAGE_LIMIT_TEXT,
  attachmentValidationError,
  imageValidationError
} from '@/utils/attachment-limits'
import { autosizeTextarea } from '@/utils/textarea-autosize'

const props = withDefaults(
  defineProps<{
    disabled?: boolean
    streaming?: boolean
    askPage?: boolean
    askFile?: boolean
    models?: ModelOption[]
    selectedModelSpec?: string
    pageFiles?: IncomingPageFile[]
    selectedPageFileId?: string
    tokenUsage?: Record<string, unknown> | null
  }>(),
  {
    disabled: false,
    streaming: false,
    askPage: true,
    askFile: true,
    models: () => [],
    selectedModelSpec: '',
    pageFiles: () => [],
    selectedPageFileId: '',
    tokenUsage: null
  }
)

const emit = defineEmits<{
  submit: [
    payload: {
      text: string
      files: File[]
      imageFile?: File | null
      selectedPageFiles: IncomingPageFile[]
      restoreUploadDraft: (retry: { files: boolean; image: boolean; message: string }) => void
    }
  ]
  stop: []
  'update:askPage': [value: boolean]
  'update:askFile': [value: boolean]
  'update:selectedModelSpec': [value: string]
  'update:selectedPageFileId': [value: string]
}>()

const text = ref('')
const files = ref<File[]>([])
const imageFile = ref<File | null>(null)
const draftAttachmentFiles = ref<File[]>([])
const selectedPageFileIds = ref<Set<string>>(new Set())
const showFileMenu = ref(false)
const showModelMenu = ref(false)
const showAttachmentMenu = ref(false)
const showAttachmentModal = ref(false)
const showContextUsage = ref(false)
const dragActive = ref(false)
const attachmentError = ref('')
const modelSearch = ref('')
const fileMenuRef = ref<HTMLElement | null>(null)
const modelMenuRef = ref<HTMLElement | null>(null)
const attachmentMenuRef = ref<HTMLElement | null>(null)
const contextUsageRef = ref<HTMLElement | null>(null)
const imageInputRef = ref<HTMLInputElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const hasPageFiles = computed(() => props.pageFiles.length > 0)
const selectedPageFiles = computed(() => {
  const selected = props.pageFiles.filter((file) => selectedPageFileIds.value.has(file.id))
  if (!props.selectedPageFileId) return selected
  // 当前上下文附件要排在第一位，保证顶部摘要和发送给模型的文件上下文一致。
  return selected.sort((a, b) => Number(b.id === props.selectedPageFileId) - Number(a.id === props.selectedPageFileId))
})
const selectedModelLabel = computed(() => {
  return props.models.find((model) => model.value === props.selectedModelSpec)?.label || props.selectedModelSpec || '默认模型'
})
const filteredModelGroups = computed(() => {
  const keyword = modelSearch.value.trim().toLowerCase()
  const groups = new Map<string, ModelOption[]>()
  for (const model of props.models) {
    const haystack = `${model.label} ${model.value} ${model.provider || ''}`.toLowerCase()
    if (keyword && !haystack.includes(keyword)) continue
    const provider = model.provider || '模型'
    groups.set(provider, [...(groups.get(provider) || []), model])
  }
  return [...groups.entries()].map(([provider, models]) => ({ provider, models }))
})
const fileButtonText = computed(() => {
  if (!hasPageFiles.value || !selectedPageFiles.value.length) return '问文件'
  return `问文件(${selectedPageFiles.value.length})`
})
function tokenNumber(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

const TOKEN_COUNT_K_UNIT = 1024

function formatTokenCount(value: number) {
  if (value < TOKEN_COUNT_K_UNIT) return String(Math.round(value))
  const digits = value >= TOKEN_COUNT_K_UNIT * 10 ? 1 : 2
  return `${(value / TOKEN_COUNT_K_UNIT).toFixed(digits).replace(/\.0+$/, '')}k`
}

const contextUsage = computed(() => {
  const usage = props.tokenUsage
  if (!usage) return null
  const used = tokenNumber(usage.llm_input_tokens)
  if (used === null) return null
  const summaryTrigger = tokenNumber(usage.summary_trigger_tokens)
  const contextWindow = tokenNumber(usage.context_window)
  const limit = summaryTrigger || contextWindow || Math.max(used, 1)
  const summaryTokens = usage.summary_active ? tokenNumber(usage.summary_message_tokens) || 0 : 0
  const llmMessageTokens = tokenNumber(usage.llm_messages_tokens) || 0
  const messageCount = Math.max((tokenNumber(usage.llm_message_count) || 0) - (usage.summary_active ? 1 : 0), 0)
  const segments = [
    { key: 'messages', label: '消息', value: Math.max(llmMessageTokens - summaryTokens, 0), messageCount },
    { key: 'summary', label: '摘要', value: summaryTokens, messageCount: 0 },
    { key: 'system', label: '系统', value: tokenNumber(usage.system_tokens) || 0, messageCount: 0 },
    { key: 'tools', label: `工具 (${tokenNumber(usage.tool_count) || 0})`, value: tokenNumber(usage.tools_tokens) || 0, messageCount: 0 }
  ].filter((segment) => segment.value > 0)
  const accounted = segments.reduce((total, segment) => total + segment.value, 0)
  if (used > accounted) segments.push({ key: 'other', label: '其他', value: used - accounted, messageCount: 0 })
  let available = limit
  return {
    used,
    limit,
    percent: Math.min(Math.round((used / limit) * 100), 100),
    limitLabel: summaryTrigger ? '摘要阈值' : contextWindow ? '模型窗口' : '本次输入',
    remaining: summaryTrigger || contextWindow ? Math.max(limit - used, 0) : null,
    segments: segments
      .map((segment) => {
        const value = Math.min(segment.value, Math.max(available, 0))
        available -= value
        return { ...segment, value, percent: `${Math.min((value / limit) * 100, 100)}%` }
      })
      .filter((segment) => segment.value > 0)
  }
})

watch(
  () => props.pageFiles.map((file) => `${file.id}:${file.selected ? '1' : '0'}`).join('|'),
  () => {
    const currentIds = new Set(props.pageFiles.map((file) => file.id))
    const next = new Set([...selectedPageFileIds.value].filter((id) => currentIds.has(id)))
    const selected = props.pageFiles.filter((file) => file.selected).map((file) => file.id)
    if (!next.size && selected.length) selected.forEach((id) => next.add(id))
    selectedPageFileIds.value = next
    emit('update:askFile', next.size > 0)
  },
  { immediate: true }
)

function syncAskFile() {
  emit('update:askFile', selectedPageFileIds.value.size > 0)
}

function resizeTextarea() {
  // 输入区要让历史消息区尽量可见，所以只随内容增长到上限，超过后交给 textarea 自己滚动。
  nextTick(() => autosizeTextarea(textareaRef.value))
}

function submit() {
  const content = text.value.trim()
  if (!content) return
  const fileError = attachmentValidationError(files.value)
  const imageError = imageValidationError(imageFile.value)
  if (fileError || imageError) {
    attachmentError.value = fileError || imageError
    return
  }
  const draft = { text: content, files: [...files.value], imageFile: imageFile.value }
  emit('submit', {
    text: content,
    files: draft.files,
    imageFile: draft.imageFile,
    selectedPageFiles: selectedPageFiles.value,
    restoreUploadDraft: (retry) => {
      text.value = draft.text
      if (retry.files) files.value = draft.files
      if (retry.image) imageFile.value = draft.imageFile
      attachmentError.value = retry.message
      resizeTextarea()
    }
  })
  text.value = ''
  files.value = []
  imageFile.value = null
  attachmentError.value = ''
  resizeTextarea()
}

function appendDraftFiles(fileList?: FileList | File[]) {
  const incoming = Array.from(fileList || [])
  const existing = new Set(draftAttachmentFiles.value.map((file) => `${file.name}:${file.size}:${file.lastModified}`))
  const next = incoming.filter((file) => !existing.has(`${file.name}:${file.size}:${file.lastModified}`))
  const error = attachmentValidationError(next, draftAttachmentFiles.value.length)
  if (error) {
    attachmentError.value = error
    return
  }
  draftAttachmentFiles.value = [...draftAttachmentFiles.value, ...next]
  attachmentError.value = ''
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  appendDraftFiles(input.files || undefined)
  // 同一个文件二次选择也要触发 change，否则用户替换附件时会觉得按钮失灵。
  input.value = ''
}

function onImageChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = Array.from(input.files || [])[0] || null
  const error = imageValidationError(file)
  if (error) attachmentError.value = error
  else if (file) {
    imageFile.value = file
    attachmentError.value = ''
  }
  input.value = ''
}

function removeFile(index: number) {
  files.value = files.value.filter((_, itemIndex) => itemIndex !== index)
}

function removeDraftFile(index: number) {
  draftAttachmentFiles.value = draftAttachmentFiles.value.filter((_, itemIndex) => itemIndex !== index)
}

function formatFileSize(size: number) {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024).toFixed(1)} KB`
}

function emitAskPage(event: Event) {
  emit('update:askPage', (event.target as HTMLInputElement).checked)
}

function selectModel(value: string) {
  emit('update:selectedModelSpec', value)
  showModelMenu.value = false
}

function toggleModelMenu() {
  if (!props.models.length || props.disabled) return
  showModelMenu.value = !showModelMenu.value
}

function togglePageFile(fileId: string) {
  const next = new Set(selectedPageFileIds.value)
  const isCurrent = props.selectedPageFileId === fileId
  const isRemoving = next.has(fileId) && isCurrent
  if (isRemoving) next.delete(fileId)
  else next.add(fileId)
  selectedPageFileIds.value = next
  syncAskFile()
  // “问文件”选择决定当前文档上下文卡片；已选附件再次点击时先切换当前摘要，再点击当前项才取消。
  emit('update:selectedPageFileId', isRemoving ? [...next][0] || '' : fileId)
}

function toggleFileMenu() {
  if (!hasPageFiles.value || props.disabled) return
  showFileMenu.value = !showFileMenu.value
}

function openAttachmentModal() {
  showAttachmentMenu.value = false
  draftAttachmentFiles.value = [...files.value]
  showAttachmentModal.value = true
}

function confirmAttachmentModal() {
  files.value = [...draftAttachmentFiles.value]
  showAttachmentModal.value = false
}

function closeAttachmentModal() {
  showAttachmentModal.value = false
  draftAttachmentFiles.value = []
}

function triggerImageInput() {
  showAttachmentMenu.value = false
  imageInputRef.value?.click()
}

function onDropFiles(event: DragEvent) {
  dragActive.value = false
  appendDraftFiles(event.dataTransfer?.files || undefined)
}

function handleOutsideClick(event: MouseEvent) {
  if (fileMenuRef.value && !fileMenuRef.value.contains(event.target as Node)) showFileMenu.value = false
  if (modelMenuRef.value && !modelMenuRef.value.contains(event.target as Node)) showModelMenu.value = false
  if (attachmentMenuRef.value && !attachmentMenuRef.value.contains(event.target as Node)) showAttachmentMenu.value = false
  if (contextUsageRef.value && !contextUsageRef.value.contains(event.target as Node)) showContextUsage.value = false
}

onMounted(() => {
  document.addEventListener('click', handleOutsideClick)
  resizeTextarea()
})

onUnmounted(() => {
  document.removeEventListener('click', handleOutsideClick)
})

watch(text, resizeTextarea)
</script>

<template>
  <form class="chat-input" @submit.prevent="submit">
    <div class="input-toolbar">
      <label class="context-chip" :class="{ active: askPage }" title="携带当前页面内容">
        <input type="checkbox" :checked="askPage" @change="emitAskPage" />
        <Globe2 :size="15" />
        <span>问网页</span>
      </label>
      <div ref="fileMenuRef" class="file-context-menu">
        <button
          type="button"
          class="context-chip"
          :class="{ active: selectedPageFiles.length > 0 }"
          :disabled="!hasPageFiles || disabled"
          title="选择要询问的页面附件"
          @click="toggleFileMenu"
        >
          <FileText :size="15" />
          <span>{{ fileButtonText }}</span>
          <ChevronDown :size="14" :class="{ open: showFileMenu }" />
        </button>
        <div v-if="showFileMenu" class="page-file-popover">
          <div class="popover-title">选择询问附件</div>
          <button
            v-for="file in pageFiles"
            :key="file.id"
            type="button"
            class="page-file-row"
            :class="{ active: selectedPageFileIds.has(file.id) }"
            @click="togglePageFile(file.id)"
          >
            <CheckSquare v-if="selectedPageFileIds.has(file.id)" :size="16" />
            <SquareIcon v-else :size="16" />
            <span :title="file.name">{{ file.name }}</span>
            <small>{{ file.size_text || file.source_file_id || '文档' }}</small>
          </button>
          <p v-if="!pageFiles.length" class="popover-empty">当前页面没有可询问附件</p>
        </div>
      </div>
    </div>

    <div v-if="files.length || imageFile" class="attached-files">
      <span v-if="imageFile">
        图片：{{ imageFile.name }}
        <button type="button" title="移除图片" @click="imageFile = null"><X :size="12" /></button>
      </span>
      <span v-for="(file, index) in files" :key="`${file.name}-${index}`">
        {{ file.name }}
        <button type="button" title="移除附件" @click="removeFile(index)"><X :size="12" /></button>
      </span>
    </div>
    <p v-if="attachmentError" class="input-attachment-error" role="alert">{{ attachmentError }}</p>

    <div class="input-row">
      <textarea
        ref="textareaRef"
        v-model="text"
        rows="1"
        placeholder="输入问题..."
        :disabled="disabled"
        @keydown.enter.exact.prevent="submit"
      />
      <div class="input-footer">
        <div class="input-tools">
          <div ref="attachmentMenuRef" class="attachment-menu-wrapper">
            <button
              type="button"
              class="tool-button"
              :class="{ active: showAttachmentMenu }"
              title="添加内容"
              @click="showAttachmentMenu = !showAttachmentMenu"
            >
              <Paperclip :size="18" />
            </button>
            <div v-if="showAttachmentMenu" class="attachment-options-menu">
              <button
                type="button"
                class="attachment-option"
                :data-tooltip="ATTACHMENT_LIMIT_TEXT"
                @click="openAttachmentModal"
              >
                <FileText :size="15" />
                <span>添加附件</span>
              </button>
              <button
                type="button"
                class="attachment-option"
                :data-tooltip="IMAGE_LIMIT_TEXT"
                @click="triggerImageInput"
              >
                <Image :size="15" />
                <span>上传图片</span>
              </button>
            </div>
            <input class="hidden-file-input" type="file" multiple @change="onFileChange" />
            <input ref="imageInputRef" class="hidden-file-input" type="file" :accept="IMAGE_ACCEPT" @change="onImageChange" />
          </div>
        </div>
        <div class="send-tools">
          <div v-if="contextUsage" ref="contextUsageRef" class="context-usage-wrapper">
            <button
              type="button"
              class="context-usage-button"
              :class="{ warning: contextUsage.percent >= 80 }"
              :aria-expanded="showContextUsage"
              title="查看本次模型调用的上下文用量"
              @click="showContextUsage = !showContextUsage"
            >
              <Gauge :size="16" />
            </button>
            <section v-if="showContextUsage" class="context-usage-popover" aria-label="上下文用量">
              <strong>上下文用量</strong>
              <span>{{ formatTokenCount(contextUsage.used) }} / {{ formatTokenCount(contextUsage.limit) }} Token（{{ contextUsage.percent }}%）</span>
              <div class="context-usage-bar" aria-label="上下文 Token 构成">
                <i v-for="segment in contextUsage.segments" :key="segment.key" :class="`is-${segment.key}`" :style="{ width: segment.percent }"></i>
              </div>
              <div class="context-usage-legend">
                <span v-for="segment in contextUsage.segments" :key="segment.key"><i :class="`is-${segment.key}`"></i>{{ segment.label }}<template v-if="segment.messageCount"> ({{ segment.messageCount }})</template> {{ formatTokenCount(segment.value) }}</span>
              </div>
              <small>{{ contextUsage.limitLabel }}{{ contextUsage.remaining === null ? '' : ` · 剩余 ${formatTokenCount(contextUsage.remaining)}` }}</small>
            </section>
          </div>
          <div ref="modelMenuRef" class="model-menu-wrapper">
            <button
              type="button"
              class="model-trigger"
              :disabled="!models.length || disabled"
              title="选择模型"
              @click="toggleModelMenu"
            >
              <span>{{ selectedModelLabel }}</span>
              <ChevronDown :size="14" :class="{ open: showModelMenu }" />
            </button>
            <div v-if="showModelMenu" class="model-popover">
              <label class="model-search">
                <Search :size="14" />
                <input v-model="modelSearch" type="text" placeholder="搜索模型" @keydown.stop />
              </label>
              <div v-for="group in filteredModelGroups" :key="group.provider" class="model-group">
                <div class="model-provider">{{ group.provider }}</div>
                <button
                  v-for="model in group.models"
                  :key="model.value"
                  type="button"
                  class="model-option"
                  :class="{ active: model.value === selectedModelSpec }"
                  @click="selectModel(model.value)"
                >
                  {{ model.label }}
                </button>
              </div>
              <p v-if="!filteredModelGroups.length" class="popover-empty">没有匹配模型</p>
            </div>
          </div>
          <button v-if="streaming" class="send-button" type="button" title="停止" @click="$emit('stop')">
            <Square :size="16" />
          </button>
          <button v-else class="send-button" type="submit" :disabled="disabled || !text.trim()" title="发送">
            <SendHorizontal :size="18" />
          </button>
        </div>
      </div>
    </div>

    <div v-if="showAttachmentModal" class="attachment-modal-mask" @click.self="closeAttachmentModal">
      <section class="attachment-modal" role="dialog" aria-modal="true" aria-labelledby="attachmentModalTitle">
        <header class="attachment-modal-header">
          <h2 id="attachmentModalTitle">添加附件</h2>
          <button type="button" title="关闭" @click="closeAttachmentModal">
            <X :size="18" />
          </button>
        </header>
        <label
          class="attachment-dropzone"
          :class="{ active: dragActive }"
          @dragover.prevent="dragActive = true"
          @dragleave.prevent="dragActive = false"
          @drop.prevent="onDropFiles"
        >
          <input class="dropzone-file-input" type="file" multiple @change="onFileChange" />
          <strong>点击或拖拽文件到此处上传</strong>
          <span>{{ ATTACHMENT_LIMIT_TEXT }}；PDF 和图片可选解析为 Markdown。</span>
        </label>
        <div v-if="draftAttachmentFiles.length" class="attachment-draft-list">
          <div v-for="(file, index) in draftAttachmentFiles" :key="`${file.name}-${file.size}-${index}`" class="attachment-draft-item">
            <div class="attachment-draft-icon">
              <FileText :size="17" />
            </div>
            <div class="attachment-draft-body">
              <strong :title="file.name">{{ file.name }}</strong>
              <div class="attachment-draft-meta">
                <span>已上传</span>
                <small>{{ formatFileSize(file.size) }}</small>
              </div>
            </div>
            <button type="button" title="移除" @click="removeDraftFile(index)">
              <X :size="14" />
            </button>
          </div>
        </div>
        <footer class="attachment-modal-footer">
          <button type="button" class="secondary" @click="closeAttachmentModal">取消</button>
          <button type="button" :disabled="!draftAttachmentFiles.length" @click="confirmAttachmentModal">添加附件</button>
        </footer>
      </section>
    </div>
  </form>
</template>
