import { defineStore } from 'pinia'
import {
  cancelRun,
  confirmThreadAttachments,
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  sendMessageStream,
  submitMessageFeedback,
  updateConversation,
  uploadImage,
  uploadAttachment
} from '@/apis/chat'
import { listChatModels } from '@/apis/models'
import { appendRunChunkSegment } from '@/utils/chat-message'
import { buildContextSummaryMessage } from '@/utils/context-summary'
import { splitStreamingText } from '@/utils/streaming-text'
import type { ChatMessage, ChatThread, ExtractionResult, IncomingPageFile, ModelOption, PageContent, RunStreamChunk } from '@/types'

type SendOptions = {
  text: string
  files?: File[]
  imageFile?: File | null
  pageContent?: PageContent
  selectedFile?: IncomingPageFile | null
  extractionResult?: ExtractionResult | null
}

type ChatState = {
  threads: ChatThread[]
  currentThreadId: string
  messages: ChatMessage[]
  contextSummaryMessage: ChatMessage | null
  modelOptions: ModelOption[]
  selectedModelSpec: string
  askPage: boolean
  askFile: boolean
  isSending: boolean
  isStreaming: boolean
  isLoading: boolean
  error: string
  activeRunId: string
  abortController: AbortController | null
  lastUserMessageForRetry: SendOptions | null
}

function messageId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function tmpAttachmentPayload(uploaded: Record<string, unknown>) {
  return {
    file_name: uploaded.file_name,
    file_type: uploaded.file_type,
    bucket_name: uploaded.bucket_name,
    object_name: uploaded.object_name,
    parsed_object_name: uploaded.parsed_object_name || null,
    truncated: Boolean(uploaded.truncated)
  }
}

export const useChatStore = defineStore('chat', {
  state: (): ChatState => ({
    threads: [],
    currentThreadId: '',
    messages: [],
    contextSummaryMessage: null,
    modelOptions: [],
    selectedModelSpec: '',
    askPage: true,
    askFile: true,
    isSending: false,
    isStreaming: false,
    isLoading: false,
    error: '',
    activeRunId: '',
    abortController: null,
    lastUserMessageForRetry: null
  }),
  getters: {
    currentThread(state) {
      return state.threads.find((thread) => thread.id === state.currentThreadId) || null
    },
    displayMessages(state) {
      return state.contextSummaryMessage ? [state.contextSummaryMessage, ...state.messages] : state.messages
    }
  },
  actions: {
    setContextSummary(input: { file: IncomingPageFile | null; result: ExtractionResult | null; loading?: boolean; error?: string }) {
      this.contextSummaryMessage = buildContextSummaryMessage(input)
    },
    async bootstrap(token?: string, agentId?: string) {
      this.isLoading = true
      this.error = ''
      try {
        const [threads, models] = await Promise.all([
          listConversations(token, agentId),
          listChatModels(token).catch(() => [])
        ])
        this.threads = threads
        this.modelOptions = models
        if (!this.selectedModelSpec && models[0]) this.selectedModelSpec = models[0].value
        if (threads[0]) await this.selectThread(threads[0].id, token)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '初始化聊天失败'
      } finally {
        this.isLoading = false
      }
    },
    async refreshThreads(token?: string, agentId?: string) {
      // 打开侧边栏只刷新列表，避免把当前会话悄悄切到第一条。
      this.isLoading = true
      try {
        this.threads = await listConversations(token, agentId)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '刷新对话列表失败'
      } finally {
        this.isLoading = false
      }
    },
    async newConversation(token?: string, agentId?: string) {
      const thread = await createConversation({ token, agentId })
      this.threads = [thread, ...this.threads.filter((item) => item.id !== thread.id)]
      this.currentThreadId = thread.id
      this.messages = []
      return thread
    },
    async renameConversation(threadId: string, title: string, token?: string) {
      const thread = await updateConversation(threadId, { title }, token)
      this.threads = this.threads.map((item) => (item.id === threadId ? { ...item, ...thread } : item))
    },
    async togglePinConversation(threadId: string, token?: string) {
      const thread = this.threads.find((item) => item.id === threadId)
      if (!thread) return
      const nextPinned = !thread.is_pinned
      await updateConversation(threadId, { isPinned: nextPinned }, token)
      thread.is_pinned = nextPinned
      this.threads = [...this.threads].sort((a, b) => Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned)))
    },
    async removeConversation(threadId: string, token?: string) {
      await deleteConversation(threadId, token)
      this.threads = this.threads.filter((item) => item.id !== threadId)
      if (this.currentThreadId === threadId) {
        this.currentThreadId = this.threads[0]?.id || ''
        this.messages = this.currentThreadId ? await listMessages(this.currentThreadId, token) : []
      }
    },
    async selectThread(threadId: string, token?: string) {
      if (!threadId) return
      this.currentThreadId = threadId
      this.messages = await listMessages(threadId, token)
    },
    async ensureThread(token?: string, agentId?: string) {
      if (this.currentThreadId) return this.currentThreadId
      const thread = await this.newConversation(token, agentId)
      return thread.id
    },
    async attachFiles(threadId: string, files: File[] = [], token?: string) {
      if (!files.length) return []
      // 复用主站已有附件链路，避免 iframe 自己维护一套上传目录和权限模型。
      const uploaded = await Promise.all(files.map((file) => uploadAttachment(file, token)))
      await confirmThreadAttachments(threadId, uploaded.map(tmpAttachmentPayload), token)
      return uploaded
    },
    async stop(token?: string) {
      this.abortController?.abort()
      if (this.activeRunId) await cancelRun(this.activeRunId, token).catch(() => null)
      const last = [...this.messages].reverse().find((message) => message.role === 'assistant')
      if (last && last.status === 'streaming') {
        last.status = 'stopped'
        last.errorType = 'interrupted'
        last.errorMessage = '回答生成已中断'
      }
      this.isSending = false
      this.isStreaming = false
      this.activeRunId = ''
      this.abortController = null
      this.messages = [...this.messages]
    },
    async retry(token?: string, agentId?: string) {
      if (!this.lastUserMessageForRetry) return null
      return this.send(this.lastUserMessageForRetry, token, agentId)
    },
    async feedback(payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }, token?: string) {
      await submitMessageFeedback(payload.messageId, payload.rating, payload.reason, token)
    },
    async send(options: SendOptions, token?: string, agentId?: string) {
      const text = options.text.trim()
      if (!text || this.isSending) return null
      this.error = ''
      this.isSending = true
      this.isStreaming = true
      this.lastUserMessageForRetry = { ...options, files: options.files || [], imageFile: options.imageFile || null }
      const controller = new AbortController()
      this.abortController = controller
      const userMessage: ChatMessage = {
        id: messageId('user'),
        role: 'user',
        content: text,
        status: 'done',
        imageContent: options.imageFile ? URL.createObjectURL(options.imageFile) : undefined,
        attachments: (options.files || []).map((file) => ({ file_name: file.name, file_size: file.size, file_type: file.type })),
        createdAt: new Date().toISOString()
      }
      let assistantMessage: ChatMessage = {
        id: messageId('assistant'),
        role: 'assistant',
        content: '',
        status: 'streaming',
        toolEvents: [],
        createdAt: new Date().toISOString()
      }
      const assistantMessages = [assistantMessage]
      const createAssistantSegment = () => {
        const segment: ChatMessage = {
          id: messageId('assistant'),
          role: 'assistant',
          content: '',
          status: 'streaming',
          toolEvents: [],
          createdAt: new Date().toISOString()
        }
        assistantMessages.push(segment)
        return segment
      }
      let textQueue: RunStreamChunk[] = []
      let textTimer: ReturnType<typeof setInterval> | null = null
      const clearTextTimer = () => {
        if (textTimer) clearInterval(textTimer)
        textTimer = null
      }
      const appendVisibleChunk = (chunk: RunStreamChunk) => {
        assistantMessage = appendRunChunkSegment(this.messages, assistantMessage, chunk, createAssistantSegment)
        this.messages = [...this.messages]
      }
      const drainTextQueue = () => {
        const chunk = textQueue.shift()
        if (chunk) appendVisibleChunk(chunk)
        if (!textQueue.length) clearTextTimer()
      }
      const flushTextQueue = () => {
        clearTextTimer()
        while (textQueue.length) appendVisibleChunk(textQueue.shift()!)
      }
      const enqueueTextChunk = (chunk: RunStreamChunk) => {
        if (chunk.type !== 'text' || !chunk.content || chunk.reasoningContent) {
          appendVisibleChunk(chunk)
          return
        }
        // 主站有完整 smoother；iframe 只做正文小步输出，避免维护另一套复杂状态机。
        textQueue.push(...splitStreamingText(chunk.content, 5).map((content) => ({ ...chunk, content })))
        if (!textTimer) textTimer = setInterval(drainTextQueue, 28)
      }
      this.messages = [...this.messages, userMessage, assistantMessage]
      try {
        const threadId = await this.ensureThread(token, agentId)
        const uploadedAttachments = await this.attachFiles(threadId, options.files || [], token)
        const imageContent = options.imageFile ? (await uploadImage(options.imageFile, token)).image_content || null : null
        const result = await sendMessageStream(
          {
            text,
            token,
            threadId,
            agentId,
            modelSpec: this.selectedModelSpec,
            includePage: this.askPage,
            includeFile: this.askFile,
            pageContent: options.pageContent,
            selectedFile: options.selectedFile,
            extractionResult: options.extractionResult,
            attachmentNames: uploadedAttachments.map((item) => String(item.file_name || '')).filter(Boolean),
            attachments: uploadedAttachments,
            imageContent,
            signal: controller.signal
          },
          {
            onRunStart: (runId) => {
              this.activeRunId = runId
            },
            onChunk: (chunk) => {
              if (chunk.type === 'done') {
                flushTextQueue()
                assistantMessages.forEach((message) => {
                  if (message.status === 'streaming') message.status = 'done'
                })
              } else if (chunk.type === 'error') {
                flushTextQueue()
                assistantMessage.status = 'error'
                assistantMessage.errorType = chunk.errorType
                assistantMessage.errorMessage = chunk.message
                if (!assistantMessage.content) assistantMessage.content = chunk.message
              } else if (chunk.type === 'text') {
                enqueueTextChunk(chunk)
              } else {
                flushTextQueue()
                appendVisibleChunk(chunk)
              }
              this.messages = [...this.messages]
            },
            onTool: (event) => {
              assistantMessage.toolEvents = [...(assistantMessage.toolEvents || []), event]
              this.messages = [...this.messages]
            },
            onError: (message) => {
              flushTextQueue()
              assistantMessage.status = 'error'
              this.error = message
            },
            onDone: () => {
              flushTextQueue()
              assistantMessages.forEach((message) => {
                if (message.status === 'streaming') message.status = 'done'
              })
            }
          }
        )
        this.activeRunId = result.runId
        flushTextQueue()
        assistantMessages.forEach((message) => {
          if (message.status === 'streaming') message.status = 'done'
        })
        if (!assistantMessages.some((message) => message.content) && assistantMessage.status !== 'error') {
          assistantMessage.content = '已完成。'
        }
        assistantMessage.status = assistantMessage.status === 'error' ? 'error' : 'done'
        this.messages = [...this.messages]
        return { threadId, messageId: userMessage.id }
      } catch (error) {
        if (controller.signal.aborted) {
          clearTextTimer()
          textQueue = []
          assistantMessages.forEach((message) => {
            if (message.status === 'streaming') message.status = 'stopped'
          })
          assistantMessage.errorType = 'interrupted'
          assistantMessage.errorMessage = '回答生成已中断'
          this.messages = [...this.messages]
          return null
        }
        flushTextQueue()
        const message = error instanceof Error ? error.message : '发送失败'
        assistantMessage.status = 'error'
        assistantMessage.content = message
        this.error = message
        this.messages = [...this.messages]
        return null
      } finally {
        this.isSending = false
        this.isStreaming = false
        this.activeRunId = ''
        this.abortController = null
      }
    }
  }
})
