import { defineStore } from 'pinia'
import {
  cancelRun,
  confirmThreadAttachments,
  createResumeRun,
  createConversation,
  deleteConversation,
  generateConversationTitle,
  getRun,
  getThreadActiveRun,
  listConversations,
  listMessages,
  sendMessageStream,
  submitMessageFeedback,
  streamRunEvents,
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
  selectedPageFiles?: IncomingPageFile[]
  extractionResults?: Record<string, ExtractionResult>
}

type PendingInterrupt = {
  status: string
  questions: Record<string, unknown>[]
  parentRunId: string
}

type ThreadRuntime = {
  messages: ChatMessage[]
  isSending: boolean
  isStreaming: boolean
  activeRunId: string
  lastEventSeq: string
  abortController: AbortController | null
  lastUserMessageForRetry: SendOptions | null
  pendingInterrupt: PendingInterrupt | null
  agentState: Record<string, unknown> | null
}

type ChatState = {
  threads: ChatThread[]
  currentThreadId: string
  threadRuntimes: Record<string, ThreadRuntime>
  threadOffset: number
  hasMoreThreads: boolean
  isLoadingMoreThreads: boolean
  contextSummaryMessage: ChatMessage | null
  modelOptions: ModelOption[]
  selectedModelSpec: string
  modelSpecsByThread: Record<string, string>
  manuallyRenamedThreads: Record<string, boolean>
  askPage: boolean
  askFile: boolean
  isLoading: boolean
  error: string
}

const DRAFT_THREAD_KEY = '__draft__'
const DEFAULT_THREAD_TITLE = '来文咨询'
const THREAD_PAGE_SIZE = 50

function createThreadRuntime(): ThreadRuntime {
  return {
    messages: [],
    isSending: false,
    isStreaming: false,
    activeRunId: '',
    lastEventSeq: '0-0',
    abortController: null,
    lastUserMessageForRetry: null,
    pendingInterrupt: null,
    agentState: null
  }
}

function messageId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function waitForStreamReconnect() {
  return new Promise((resolve) => setTimeout(resolve, 500))
}

function requestIdFromMessage(message: ChatMessage) {
  const raw = message.raw || {}
  const extra = raw.extra_metadata as Record<string, unknown> | undefined
  const requestId = extra?.request_id || raw.request_id
  return typeof requestId === 'string' ? requestId : ''
}

function modelSpecFromHistory(messages: ChatMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'user') continue
    const extraMetadata = message.raw?.extra_metadata as Record<string, unknown> | undefined
    const modelSpec = extraMetadata?.model_spec
    if (typeof modelSpec === 'string' && modelSpec) return modelSpec
  }
  return ''
}

function nonPinnedThreadCount(threads: ChatThread[]) {
  return threads.filter((thread) => !thread.is_pinned).length
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
    threadRuntimes: {},
    threadOffset: 0,
    hasMoreThreads: false,
    isLoadingMoreThreads: false,
    contextSummaryMessage: null,
    modelOptions: [],
    selectedModelSpec: '',
    modelSpecsByThread: {},
    manuallyRenamedThreads: {},
    askPage: true,
    askFile: true,
    isLoading: false,
    error: ''
  }),
  getters: {
    currentThread(state) {
      return state.threads.find((thread) => thread.id === state.currentThreadId) || null
    },
    messages(state) {
      return state.threadRuntimes[state.currentThreadId || DRAFT_THREAD_KEY]?.messages || []
    },
    isSending(state) {
      return state.threadRuntimes[state.currentThreadId || DRAFT_THREAD_KEY]?.isSending || false
    },
    isStreaming(state) {
      return state.threadRuntimes[state.currentThreadId || DRAFT_THREAD_KEY]?.isStreaming || false
    },
    pendingInterrupt(state) {
      return state.threadRuntimes[state.currentThreadId || DRAFT_THREAD_KEY]?.pendingInterrupt || null
    },
    displayMessages(state) {
      const messages = state.threadRuntimes[state.currentThreadId || DRAFT_THREAD_KEY]?.messages || []
      return state.contextSummaryMessage ? [state.contextSummaryMessage, ...messages] : messages
    }
  },
  actions: {
    ensureRuntime(threadId?: string) {
      const runtimeKey = threadId || this.currentThreadId || DRAFT_THREAD_KEY
      if (!this.threadRuntimes[runtimeKey]) this.threadRuntimes[runtimeKey] = createThreadRuntime()
      return this.threadRuntimes[runtimeKey]
    },
    setSelectedModelSpec(modelSpec: string) {
      if (!modelSpec) return
      const threadId = this.currentThreadId || DRAFT_THREAD_KEY
      // 模型选择属于会话草稿，避免切换侧栏后把其他会话的下一轮请求改成当前模型。
      this.modelSpecsByThread[threadId] = modelSpec
      this.selectedModelSpec = modelSpec
    },
    restoreThreadModelSpec(threadId: string, messages: ChatMessage[]) {
      if (this.modelSpecsByThread[threadId]) return
      const modelSpec = modelSpecFromHistory(messages)
      if (modelSpec) this.modelSpecsByThread[threadId] = modelSpec
    },
    consumeRunStatus(runtime: ThreadRuntime, chunk: Record<string, unknown>) {
      const status = String(chunk.status || '')
      if (status === 'agent_state' && chunk.agent_state && typeof chunk.agent_state === 'object') {
        runtime.agentState = chunk.agent_state as Record<string, unknown>
        return
      }
      if (status !== 'ask_user_question_required' && status !== 'human_approval_required') return

      const interruptInfo = chunk.interrupt_info as Record<string, unknown> | undefined
      const questions = Array.isArray(chunk.questions)
        ? chunk.questions
        : Array.isArray(interruptInfo?.questions)
          ? interruptInfo.questions
          : []
      if (!questions.length) return
      runtime.pendingInterrupt = {
        status,
        questions: questions.filter((question): question is Record<string, unknown> => Boolean(question && typeof question === 'object')),
        parentRunId: String(chunk.run_id || chunk.parent_run_id || runtime.activeRunId)
      }
      runtime.isSending = false
      runtime.isStreaming = false
    },
    setContextSummary(input: { file: IncomingPageFile | null; result: ExtractionResult | null; loading?: boolean; error?: string }) {
      this.contextSummaryMessage = buildContextSummaryMessage(input)
    },
    async bootstrap(token?: string, agentId?: string, conversationScopeKey?: string) {
      this.isLoading = true
      this.error = ''
      try {
        const [threads, models] = await Promise.all([
          listConversations(token, agentId, conversationScopeKey, 0, THREAD_PAGE_SIZE),
          listChatModels(token).catch(() => [])
        ])
        this.threads = threads
        this.threadOffset = nonPinnedThreadCount(threads)
        this.hasMoreThreads = this.threadOffset === THREAD_PAGE_SIZE
        this.modelOptions = models
        if (!this.selectedModelSpec && models[0]) this.selectedModelSpec = models[0].value
        if (threads[0]) await this.selectThread(threads[0].id, token)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '初始化聊天失败'
      } finally {
        this.isLoading = false
      }
    },
    async refreshThreads(token?: string, agentId?: string, conversationScopeKey?: string) {
      // 打开侧边栏只刷新列表，避免把当前会话悄悄切到第一条。
      this.isLoading = true
      try {
        const threads = await listConversations(token, agentId, conversationScopeKey, 0, THREAD_PAGE_SIZE)
        this.threads = threads
        this.threadOffset = nonPinnedThreadCount(threads)
        this.hasMoreThreads = this.threadOffset === THREAD_PAGE_SIZE
      } catch (error) {
        this.error = error instanceof Error ? error.message : '刷新对话列表失败'
      } finally {
        this.isLoading = false
      }
    },
    async loadMoreThreads(token?: string, agentId?: string, conversationScopeKey?: string) {
      if (!this.hasMoreThreads || this.isLoadingMoreThreads) return
      this.isLoadingMoreThreads = true
      try {
        const page = await listConversations(token, agentId, conversationScopeKey, this.threadOffset, THREAD_PAGE_SIZE)
        const seen = new Set(this.threads.map((thread) => thread.id))
        this.threads = [...this.threads, ...page.filter((thread) => !seen.has(thread.id))]
        const loaded = nonPinnedThreadCount(page)
        this.threadOffset += loaded
        this.hasMoreThreads = loaded === THREAD_PAGE_SIZE
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载更多对话失败'
      } finally {
        this.isLoadingMoreThreads = false
      }
    },
    async newConversation(token?: string, agentId?: string, conversationScopeKey?: string) {
      const thread = await this.createThread(token, agentId, conversationScopeKey)
      // 仅在用户主动"新建会话"时清空消息；send() 走 ensureThread 复用同一创建路径，
      // 那里的乐观消息不能在这里被抹掉，否则首条提问与回复都会在主区消失。
      this.threadRuntimes[thread.id] = createThreadRuntime()
      return thread
    },
    async createThread(token?: string, agentId?: string, conversationScopeKey?: string) {
      const thread = await createConversation({ token, agentId, conversationScopeKey })
      this.threads = [thread, ...this.threads.filter((item) => item.id !== thread.id)]
      if (!thread.is_pinned) this.threadOffset += 1
      const draftRuntime = this.threadRuntimes[DRAFT_THREAD_KEY]
      const draftModelSpec = this.modelSpecsByThread[DRAFT_THREAD_KEY] || this.selectedModelSpec
      if (draftModelSpec) this.modelSpecsByThread[thread.id] = draftModelSpec
      delete this.modelSpecsByThread[DRAFT_THREAD_KEY]
      if (draftRuntime) {
        // 首次发送先写入草稿容器，拿到真实会话 ID 后原样迁移，避免创建请求期间丢失乐观消息。
        this.threadRuntimes[thread.id] = draftRuntime
        delete this.threadRuntimes[DRAFT_THREAD_KEY]
      } else {
        this.ensureRuntime(thread.id)
      }
      // 创建首轮会话期间用户可能已切到其他线程；此时只完成草稿迁移，不能把可见会话强行切回旧 run。
      if (!draftRuntime || !this.currentThreadId) this.currentThreadId = thread.id
      return thread
    },
    async renameConversation(threadId: string, title: string, token?: string) {
      this.manuallyRenamedThreads[threadId] = true
      const thread = await updateConversation(threadId, { title }, token)
      this.threads = this.threads.map((item) => (item.id === threadId ? { ...item, ...thread } : item))
    },
    async togglePinConversation(threadId: string, token?: string, agentId?: string, conversationScopeKey?: string) {
      const thread = this.threads.find((item) => item.id === threadId)
      if (!thread) return
      const nextPinned = !thread.is_pinned
      await updateConversation(threadId, { isPinned: nextPinned }, token)
      await this.refreshThreads(token, agentId, conversationScopeKey)
    },
    async removeConversation(threadId: string, token?: string) {
      const thread = this.threads.find((item) => item.id === threadId)
      await deleteConversation(threadId, token)
      this.threads = this.threads.filter((item) => item.id !== threadId)
      if (thread && !thread.is_pinned) this.threadOffset = Math.max(0, this.threadOffset - 1)
      delete this.threadRuntimes[threadId]
      if (this.currentThreadId === threadId) {
        this.currentThreadId = this.threads[0]?.id || ''
        if (this.currentThreadId) this.ensureRuntime(this.currentThreadId).messages = await listMessages(this.currentThreadId, token)
      }
    },
    async selectThread(threadId: string, token?: string) {
      if (!threadId) return
      this.currentThreadId = threadId
      const runtime = this.ensureRuntime(threadId)
      runtime.messages = await listMessages(threadId, token)
      this.restoreThreadModelSpec(threadId, runtime.messages)
      this.selectedModelSpec = this.modelSpecsByThread[threadId] || this.selectedModelSpec
      void this.resumeActiveRun(threadId, token)
    },
    async autoGenerateTitle(threadId: string, query: string, token?: string) {
      try {
        const currentThread = this.threads.find((item) => item.id === threadId)
        if (this.manuallyRenamedThreads[threadId] || currentThread?.title !== DEFAULT_THREAD_TITLE) return
        const generatedTitle = await generateConversationTitle(query, token)
        const title = generatedTitle.slice(0, 30).replace(/\s+/g, ' ').trim()
        const thread = this.threads.find((item) => item.id === threadId)
        if (!title || this.manuallyRenamedThreads[threadId] || thread?.title !== DEFAULT_THREAD_TITLE) return
        const updated = await updateConversation(threadId, { title }, token)
        this.threads = this.threads.map((item) => (item.id === threadId ? { ...item, ...updated } : item))
      } catch {
        // 标题仅是辅助体验，快速模型不可用时不能影响正式问答结果。
      }
    },
    async syncThreadHistory(threadId: string, token?: string, requestId = '') {
      const runtime = this.ensureRuntime(threadId)
      const history = await listMessages(threadId, token)
      if (!requestId) {
        if (history.length) runtime.messages = history
        return history
      }

      const persistedTurn = history.filter((message) => requestIdFromMessage(message) === requestId)
      const start = runtime.messages.findIndex((message) => requestIdFromMessage(message) === requestId)
      if (!persistedTurn.length || start < 0) return []

      const nextUser = runtime.messages.findIndex((message, index) => index > start && message.role === 'user')
      runtime.messages = [
        ...runtime.messages.slice(0, start),
        ...persistedTurn,
        ...runtime.messages.slice(nextUser < 0 ? runtime.messages.length : nextUser)
      ]
      return persistedTurn
    },
    async submitInterrupt(threadId: string, answer: unknown, token?: string, agentId?: string) {
      const runtime = this.ensureRuntime(threadId)
      const pending = runtime.pendingInterrupt
      if (!pending?.parentRunId) return null
      runtime.pendingInterrupt = null
      try {
        const result = await createResumeRun({ threadId, agentId, parentRunId: pending.parentRunId, answer, token })
        await this.resumeActiveRun(threadId, token)
        return result
      } catch (error) {
        runtime.pendingInterrupt = pending
        throw error
      }
    },
    async resumeActiveRun(threadId: string, token?: string) {
      if (!threadId) return
      const runtime = this.ensureRuntime(threadId)
      if (runtime.abortController) return
      const run = (await getThreadActiveRun(threadId, token)).run
      if (!run?.id) return

      if (runtime.activeRunId !== run.id) runtime.lastEventSeq = '0-0'
      runtime.activeRunId = run.id
      runtime.isSending = true
      runtime.isStreaming = true
      const controller = new AbortController()
      runtime.abortController = controller
      let reachedTerminalEvent = false
      let assistantMessage = [...runtime.messages].reverse().find((message) => message.role === 'assistant' && message.status === 'streaming')
      if (!assistantMessage) {
        assistantMessage = {
          id: messageId('assistant'),
          role: 'assistant',
          content: '',
          status: 'streaming',
          toolEvents: [],
          createdAt: new Date().toISOString()
        }
        runtime.messages = [...runtime.messages, assistantMessage]
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
      const streamHandlers = {
        onEventId: (eventId: string) => {
          runtime.lastEventSeq = eventId
        },
        onStatus: (chunk: Record<string, unknown>) => {
          this.consumeRunStatus(runtime, chunk)
        },
        onChunk: (chunk: RunStreamChunk) => {
          if (chunk.type === 'done') {
            reachedTerminalEvent = true
            assistantMessages.forEach((message) => {
              if (message.status === 'streaming') message.status = 'done'
            })
          } else if (chunk.type === 'error') {
            assistantMessage!.status = 'error'
            assistantMessage!.errorType = chunk.errorType
            assistantMessage!.errorMessage = chunk.message
            if (!assistantMessage!.content) assistantMessage!.content = chunk.message
          } else {
            assistantMessage = appendRunChunkSegment(runtime.messages, assistantMessage!, chunk, createAssistantSegment)
          }
          runtime.messages = [...runtime.messages]
        },
        onTool: (event: string) => {
          assistantMessage!.toolEvents = [...(assistantMessage!.toolEvents || []), event]
          runtime.messages = [...runtime.messages]
        },
        onError: (message: string) => {
          assistantMessage!.status = 'error'
          if (!assistantMessage!.content) assistantMessage!.content = message
          this.error = message
        },
        onDone: () => {
          reachedTerminalEvent = true
          assistantMessages.forEach((message) => {
            if (message.status === 'streaming') message.status = 'done'
          })
        }
      }
      try {
        while (!reachedTerminalEvent && !controller.signal.aborted) {
          const status = (await getRun(run.id, token)).run?.status
          if (status === 'completed') break
          if (status === 'failed' || status === 'cancelled') throw new Error(`运行已${status === 'failed' ? '失败' : '取消'}`)
          const resumed = await streamRunEvents(run.id, token, runtime.lastEventSeq, streamHandlers, controller.signal)
          reachedTerminalEvent = reachedTerminalEvent || resumed.reachedTerminalEvent
          if (!reachedTerminalEvent) await waitForStreamReconnect()
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          const message = error instanceof Error ? error.message : '恢复运行失败'
          assistantMessage.status = 'error'
          assistantMessage.content = assistantMessage.content || message
          this.error = message
          runtime.messages = [...runtime.messages]
        }
      } finally {
        if (runtime.abortController === controller) {
          runtime.isSending = false
          runtime.isStreaming = false
          runtime.activeRunId = ''
          runtime.abortController = null
        }
        if (reachedTerminalEvent && !controller.signal.aborted) await this.syncThreadHistory(threadId, token).catch(() => null)
      }
    },
    async ensureThread(token?: string, agentId?: string, conversationScopeKey?: string) {
      if (this.currentThreadId) return this.currentThreadId
      const thread = await this.createThread(token, agentId, conversationScopeKey)
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
      const runtime = this.ensureRuntime()
      runtime.abortController?.abort()
      if (runtime.activeRunId) await cancelRun(runtime.activeRunId, token).catch(() => null)
      const last = [...runtime.messages].reverse().find((message) => message.role === 'assistant')
      if (last && last.status === 'streaming') {
        last.status = 'stopped'
        last.errorType = 'interrupted'
        last.errorMessage = '回答生成已中断'
      }
      runtime.isSending = false
      runtime.isStreaming = false
      runtime.activeRunId = ''
      runtime.lastEventSeq = '0-0'
      runtime.abortController = null
      runtime.messages = [...runtime.messages]
    },
    async retry(token?: string, agentId?: string, conversationScopeKey?: string) {
      const runtime = this.ensureRuntime()
      if (!runtime.lastUserMessageForRetry) return null
      return this.send(runtime.lastUserMessageForRetry, token, agentId, conversationScopeKey)
    },
    async feedback(payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }, token?: string) {
      const runtime = this.ensureRuntime()
      const message = runtime.messages.find((item) => item.id === payload.messageId)
      if (!message || message.feedback || message.feedbackSubmitting) return

      // 提交中的本地状态先落下，避免同一条正式消息被快速重复点击。
      message.feedbackSubmitting = true
      runtime.messages = [...runtime.messages]
      try {
        await submitMessageFeedback(payload.messageId, payload.rating, payload.reason, token)
        message.feedback = { rating: payload.rating, reason: payload.reason }
      } finally {
        message.feedbackSubmitting = false
        runtime.messages = [...runtime.messages]
      }
    },
    async send(options: SendOptions, token?: string, agentId?: string, conversationScopeKey?: string) {
      const text = options.text.trim()
      const runtime = this.ensureRuntime()
      if (!text || runtime.isSending) return null
      const isFirstTurn = !runtime.messages.some((message) => message.role === 'user')
      const selectedModelSpec = this.modelSpecsByThread[this.currentThreadId || DRAFT_THREAD_KEY] || this.selectedModelSpec
      this.error = ''
      runtime.isSending = true
      runtime.isStreaming = true
      runtime.lastUserMessageForRetry = { ...options, files: options.files || [], imageFile: options.imageFile || null }
      runtime.lastEventSeq = '0-0'
      const controller = new AbortController()
      runtime.abortController = controller
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
        assistantMessage = appendRunChunkSegment(runtime.messages, assistantMessage, chunk, createAssistantSegment)
        runtime.messages = [...runtime.messages]
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
      runtime.messages = [...runtime.messages, userMessage, assistantMessage]
      let reachedTerminalEvent = false
      const streamHandlers = {
        onRunStart: (runId: string) => {
          runtime.activeRunId = runId
          runtime.lastEventSeq = '0-0'
        },
        onEventId: (eventId: string) => {
          runtime.lastEventSeq = eventId
        },
        onStatus: (chunk: Record<string, unknown>) => {
          this.consumeRunStatus(runtime, chunk)
        },
        onChunk: (chunk: RunStreamChunk) => {
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
          runtime.messages = [...runtime.messages]
        },
        onTool: (event: string) => {
          assistantMessage.toolEvents = [...(assistantMessage.toolEvents || []), event]
          runtime.messages = [...runtime.messages]
        },
        onError: (message: string) => {
          flushTextQueue()
          assistantMessage.status = 'error'
          this.error = message
        },
        onDone: () => {
          reachedTerminalEvent = true
          flushTextQueue()
          assistantMessages.forEach((message) => {
            if (message.status === 'streaming') message.status = 'done'
          })
        }
      }
      try {
        const threadId = await this.ensureThread(token, agentId, conversationScopeKey)
        if (selectedModelSpec) this.modelSpecsByThread[threadId] = selectedModelSpec
        const uploadedAttachments = await this.attachFiles(threadId, options.files || [], token)
        const imageContent = options.imageFile ? (await uploadImage(options.imageFile, token)).image_content || null : null
        let runId = ''
        let requestId = ''
        try {
          const result = await sendMessageStream(
            {
              text,
              token,
              threadId,
              agentId,
              modelSpec: selectedModelSpec,
              includePage: this.askPage,
              includeFile: this.askFile,
              pageContent: options.pageContent,
              selectedFile: options.selectedFile,
              extractionResult: options.extractionResult,
              selectedPageFiles: options.selectedPageFiles,
              extractionResults: options.extractionResults,
              attachmentNames: uploadedAttachments.map((item) => String(item.file_name || '')).filter(Boolean),
              attachments: uploadedAttachments,
              imageContent,
              signal: controller.signal
            },
            streamHandlers
          )
          runId = result.runId
          requestId = result.requestId
          userMessage.raw = { request_id: requestId }
        } catch (error) {
          if (!runtime.activeRunId || controller.signal.aborted) throw error
          runId = runtime.activeRunId
        }
        while (!reachedTerminalEvent && !controller.signal.aborted) {
          const status = (await getRun(runId, token)).run?.status
          if (status === 'completed') break
          if (status === 'failed' || status === 'cancelled') throw new Error(`运行已${status === 'failed' ? '失败' : '取消'}`)
          try {
            const resumed = await streamRunEvents(runId, token, runtime.lastEventSeq, streamHandlers, controller.signal)
            reachedTerminalEvent = reachedTerminalEvent || resumed.reachedTerminalEvent
          } catch {
            // SSE 连接可能在 worker 仍运行时瞬断；保留 cursor 后短暂等待再检查 run，不能直接把回答判成失败。
            await waitForStreamReconnect()
          }
        }
        flushTextQueue()
        assistantMessages.forEach((message) => {
          if (message.status === 'streaming') message.status = 'done'
        })
        if (!assistantMessages.some((message) => message.content) && assistantMessage.status !== 'error') {
          assistantMessage.content = '已完成。'
        }
        assistantMessage.status = assistantMessage.status === 'error' ? 'error' : 'done'
        runtime.messages = [...runtime.messages]
        const persistedTurn = await this.syncThreadHistory(threadId, token, requestId).catch(() => [])
        const persistedUserMessage = persistedTurn.find((message) => message.role === 'user')
        if (isFirstTurn && assistantMessages.every((message) => message.status === 'done')) {
          void this.autoGenerateTitle(threadId, text, token)
        }
        return { threadId, messageId: persistedUserMessage?.id || userMessage.id }
      } catch (error) {
        if (controller.signal.aborted) {
          clearTextTimer()
          textQueue = []
          assistantMessages.forEach((message) => {
            if (message.status === 'streaming') message.status = 'stopped'
          })
          assistantMessage.errorType = 'interrupted'
          assistantMessage.errorMessage = '回答生成已中断'
          runtime.messages = [...runtime.messages]
          return null
        }
        flushTextQueue()
        const message = error instanceof Error ? error.message : '发送失败'
        assistantMessage.status = 'error'
        assistantMessage.content = message
        this.error = message
        runtime.messages = [...runtime.messages]
        return null
      } finally {
        runtime.isSending = false
        runtime.isStreaming = false
        runtime.activeRunId = ''
        runtime.abortController = null
      }
    }
  }
})
