import { defineStore } from 'pinia'
import {
  confirmThreadAttachments,
  createConversation,
  listConversations,
  listMessages,
  sendMessageStream,
  uploadAttachment
} from '@/apis/chat'
import { listChatModels } from '@/apis/models'
import type { ChatMessage, ChatThread, ExtractionResult, IncomingPageFile, ModelOption, PageContent } from '@/types'

type SendOptions = {
  text: string
  files?: File[]
  pageContent?: PageContent
  selectedFile?: IncomingPageFile | null
  extractionResult?: ExtractionResult | null
}

type ChatState = {
  threads: ChatThread[]
  currentThreadId: string
  messages: ChatMessage[]
  modelOptions: ModelOption[]
  selectedModelSpec: string
  askPage: boolean
  askFile: boolean
  isSending: boolean
  isLoading: boolean
  error: string
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
    modelOptions: [],
    selectedModelSpec: '',
    askPage: true,
    askFile: true,
    isSending: false,
    isLoading: false,
    error: ''
  }),
  getters: {
    currentThread(state) {
      return state.threads.find((thread) => thread.id === state.currentThreadId) || null
    }
  },
  actions: {
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
    async newConversation(token?: string, agentId?: string) {
      const thread = await createConversation({ token, agentId })
      this.threads = [thread, ...this.threads.filter((item) => item.id !== thread.id)]
      this.currentThreadId = thread.id
      this.messages = []
      return thread
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
      return uploaded.map((item) => String(item.file_name || '')).filter(Boolean)
    },
    async send(options: SendOptions, token?: string, agentId?: string) {
      const text = options.text.trim()
      if (!text || this.isSending) return null
      this.error = ''
      this.isSending = true
      const userMessage: ChatMessage = {
        id: messageId('user'),
        role: 'user',
        content: text,
        status: 'done',
        createdAt: new Date().toISOString()
      }
      const assistantMessage: ChatMessage = {
        id: messageId('assistant'),
        role: 'assistant',
        content: '',
        status: 'streaming',
        toolEvents: [],
        createdAt: new Date().toISOString()
      }
      this.messages = [...this.messages, userMessage, assistantMessage]
      try {
        const threadId = await this.ensureThread(token, agentId)
        const attachmentNames = await this.attachFiles(threadId, options.files || [], token)
        await sendMessageStream(
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
            attachmentNames
          },
          {
            onText: (delta) => {
              assistantMessage.content += delta
              this.messages = [...this.messages]
            },
            onTool: (event) => {
              assistantMessage.toolEvents = [...(assistantMessage.toolEvents || []), event]
              this.messages = [...this.messages]
            },
            onError: (message) => {
              assistantMessage.status = 'error'
              this.error = message
            },
            onDone: () => {
              assistantMessage.status = 'done'
            }
          }
        )
        if (!assistantMessage.content && assistantMessage.status !== 'error') {
          assistantMessage.content = '已完成。'
        }
        assistantMessage.status = assistantMessage.status === 'error' ? 'error' : 'done'
        this.messages = [...this.messages]
        return { threadId, messageId: userMessage.id }
      } catch (error) {
        const message = error instanceof Error ? error.message : '发送失败'
        assistantMessage.status = 'error'
        assistantMessage.content = message
        this.error = message
        this.messages = [...this.messages]
        return null
      } finally {
        this.isSending = false
      }
    }
  }
})
