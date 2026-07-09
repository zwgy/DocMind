import { normalizeChatMessage } from '../utils/chat-message.ts'
import { apiUrl } from './api-url.ts'
import type {
  ChatMessage,
  ChatThread,
  ExtractionResult,
  IframeContextPayload,
  IncomingPageFile,
  PageContent,
  RunStreamChunk
} from '../types'

const DEFAULT_AGENT_ID = 'default-chatbot'

type RequestOptions = {
  token?: string
}

type CreateConversationOptions = RequestOptions & {
  agentId?: string
  title?: string
  conversationScopeKey?: string
}

type RunEventHandlers = {
  onRunStart?: (runId: string, requestId: string) => void
  onChunk?: (chunk: RunStreamChunk) => void
  onText?: (text: string) => void
  onTool?: (text: string) => void
  onError?: (message: string) => void
  onDone?: () => void
}

type ChatContextInput = {
  text: string
  includePage: boolean
  includeFile: boolean
  pageContent?: PageContent
  selectedFile?: IncomingPageFile | null
  extractionResult?: ExtractionResult | null
  selectedPageFiles?: IncomingPageFile[]
  extractionResults?: Record<string, ExtractionResult>
}

type SendMessagePayload = ChatContextInput &
  RequestOptions & {
    threadId: string
    agentId?: string
    modelSpec?: string
    attachmentNames?: string[]
    attachments?: Record<string, unknown>[]
    imageContent?: string | null
    signal?: AbortSignal
  }

function authHeaders(token?: string, json = true): Record<string, string> {
  const headers: Record<string, string> = {}
  if (json) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

async function parseResponse<T>(response: Response, fallbackMessage: string): Promise<T> {
  if (response.ok) return response.json() as Promise<T>
  let message = fallbackMessage
  try {
    const data = await response.json()
    message = data.detail || data.message || message
  } catch {
    // 后端偶发非 JSON 错误时，保留 HTTP 语义即可；这里不做二次包装，避免遮住真实状态码。
  }
  throw new Error(message)
}

function summarizeExtraction(result?: ExtractionResult | null) {
  if (!result) return ''
  if (result.matchStatus !== 'matched' || result.extractionStatus !== 'ready') return ''
  const lines: string[] = [`匹配状态：${result.matchStatus}`, `抽取状态：${result.extractionStatus}`]
  const categories = Object.entries(result.categories || {})
    .filter(([, value]) => value?.matched)
    .map(([key, value]) => `${key}：命中${value.evidence ? `，依据：${value.evidence}` : ''}`)
  if (categories.length) lines.push(`分类：${categories.join('；')}`)
  const quotes = (result.items || [])
    .map((item) => item.source_quote || '')
    .filter(Boolean)
    .slice(0, 5)
  if (quotes.length) lines.push(`原文依据：${quotes.join('；')}`)
  return lines.join('\n')
}

export function buildChatQuery(input: ChatContextInput) {
  return input.text.trim()
  /*
  const parts = [`用户问题：${input.text.trim()}`]
  if (input.includePage && input.pageContent) {
    // 当前后端只保证消费 query；把页面摘要拼进去，才能让“问网页”在第一版真实可用。
    const pageLines = [
      input.pageContent.title ? `页面标题：${input.pageContent.title}` : '',
      input.pageContent.url ? `页面地址：${input.pageContent.url}` : '',
      compactText(input.pageContent.text || input.pageContent.html)
    ].filter(Boolean)
    if (pageLines.length) parts.push(`页面上下文：\n${pageLines.join('\n')}`)
  }
  if (input.includeFile && input.selectedFile) {
    const fileLines = [`附件：${input.selectedFile.name}`]
    if (input.selectedFile.sourceKey) fileLines.push(`来源编号：${input.selectedFile.sourceKey}`)
    const extraction = summarizeExtraction(input.extractionResult)
    if (extraction) fileLines.push(extraction)
    parts.push(`文件上下文：\n${fileLines.join('\n')}`)
  }
  return parts.join('\n\n')
  */
}

export function buildIframeContext(input: ChatContextInput): IframeContextPayload {
  const context: IframeContextPayload = { files: [] }
  if (input.includePage && input.pageContent) context.page = input.pageContent
  if (!input.includeFile) return context

  const files = input.selectedPageFiles?.length
    ? input.selectedPageFiles
    : input.selectedFile
      ? [input.selectedFile]
      : []
  for (const file of files) {
    const result = input.extractionResults?.[file.id] || (file.id === input.selectedFile?.id ? input.extractionResult : null)
    const summary = summarizeExtraction(result)
    context.files.push({
      ...file,
      matchStatus: result?.matchStatus,
      extractionStatus: result?.extractionStatus,
      fileStatus: result?.fileStatus,
      hasParsedMarkdown: result?.hasParsedMarkdown,
      kbId: result?.kbId,
      fileId: result?.fileId,
      runId: result?.runId,
      summary: summary || undefined,
      summaryTruncated: Boolean(summary && summary.length >= 1200),
      structuredResult: result?.structuredResult
    })
  }
  return context
}

function extractTextDelta(payload: Record<string, unknown>) {
  const chunk = payload.chunk as Record<string, unknown> | undefined
  const streamEvent =
    (payload.stream_event as Record<string, unknown> | undefined) ||
    (chunk?.stream_event as Record<string, unknown> | undefined)
  const candidates = [
    streamEvent?.content,
    streamEvent?.delta,
    (chunk?.message as Record<string, unknown> | undefined)?.content,
    chunk?.content,
    payload.response
  ]
  const value = candidates.find((item) => typeof item === 'string' && item)
  return typeof value === 'string' ? value : ''
}

function emitChunk(handlers: RunEventHandlers, chunk: RunStreamChunk) {
  handlers.onChunk?.(chunk)
  if (chunk.type === 'text' && chunk.content) handlers.onText?.(chunk.content)
  if (chunk.type === 'tool_call' && chunk.name) handlers.onTool?.(`工具调用：${chunk.name}`)
  if (chunk.type === 'error') handlers.onError?.(chunk.message)
  if (chunk.type === 'done') handlers.onDone?.()
}

function streamEventChunk(streamEvent: Record<string, unknown>): RunStreamChunk | null {
  const eventType = String(streamEvent.type || '')
  const messageId = typeof streamEvent.message_id === 'string' ? streamEvent.message_id : undefined
  if (eventType === 'message_delta') {
    return {
      type: 'text',
      messageId,
      content: String(streamEvent.content || streamEvent.delta || ''),
      reasoningContent: String(streamEvent.reasoning_content || streamEvent.additional_reasoning_content || '')
    }
  }
  if (eventType === 'tool_call' || eventType === 'tool_call_delta') {
    return {
      type: 'tool_call',
      messageId,
      toolCallId: typeof streamEvent.tool_call_id === 'string' ? streamEvent.tool_call_id : undefined,
      name: typeof streamEvent.name === 'string' ? streamEvent.name : undefined,
      args: streamEvent.args || streamEvent.args_delta
    }
  }
  return null
}

function toolResultChunk(payload: Record<string, unknown>): RunStreamChunk | null {
  const event = payload.event as Record<string, unknown> | undefined
  const data = event?.data as Record<string, unknown> | undefined
  if (event?.method !== 'tools' || data?.event !== 'tool-finished') return null
  const output = data.output as Record<string, unknown> | undefined
  return {
    type: 'tool_result',
    toolCallId: String(output?.tool_call_id || data.tool_call_id || output?.id || ''),
    content: output?.content ?? output
  }
}

function extractToolEvent(payload: Record<string, unknown>) {
  const chunk = payload.chunk as Record<string, unknown> | undefined
  const event = (payload.event as Record<string, unknown> | undefined) || (chunk?.event as Record<string, unknown>)
  const name = event?.name || event?.tool_name || event?.type
  return typeof name === 'string' && name ? `工具调用：${name}` : ''
}

function handleRunPayload(payload: Record<string, unknown>, handlers: RunEventHandlers) {
  const body = (payload.payload as Record<string, unknown> | undefined) || payload
  const items = Array.isArray(body.items) ? body.items : []
  for (const item of items) {
    if (item && typeof item === 'object') {
      handleRunPayload(item as Record<string, unknown>, handlers)
    }
  }
  const chunk = body.chunk && typeof body.chunk === 'object' ? (body.chunk as Record<string, unknown>) : null
  const current = chunk || body
  const status = String(current.status || body.status || '')
  const streamEvent =
    (current.stream_event as Record<string, unknown> | undefined) ||
    ((current.chunk as Record<string, unknown> | undefined)?.stream_event as Record<string, unknown> | undefined)
  const semanticChunk = streamEvent ? streamEventChunk(streamEvent) : null
  const toolResult = toolResultChunk(current)
  if (semanticChunk) {
    emitChunk(handlers, semanticChunk)
  } else if (toolResult) {
    emitChunk(handlers, toolResult)
  } else {
    const text = extractTextDelta(current)
    if (text) emitChunk(handlers, { type: 'text', content: text })
    const tool = extractToolEvent(current)
    if (tool) handlers.onTool?.(tool)
  }
  if (status === 'error') {
    emitChunk(handlers, {
      type: 'error',
      message: String(current.error_message || current.message || '对话失败'),
      errorType: typeof current.error_type === 'string' ? current.error_type : undefined
    })
  }
  if (status === 'interrupted') {
    emitChunk(handlers, { type: 'error', message: String(current.message || '回答生成已中断'), errorType: 'interrupted' })
  }
  if (status === 'finished' || status === 'completed' || status === 'cancelled') emitChunk(handlers, { type: 'done' })
}

function handleSseBlock(block: string, handlers: RunEventHandlers) {
  const eventType = block
    .split(/\r?\n/)
    .find((line) => line.startsWith('event:'))
    ?.slice(6)
    .trim()
  const dataLine = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n')
  if (!dataLine) return
  let payload: Record<string, unknown>
  try {
    payload = JSON.parse(dataLine)
  } catch {
    handlers.onError?.('流式响应解析失败')
    return
  }
  handleRunPayload(payload, handlers)
  if (eventType === 'end') handlers.onDone?.()
}

export async function readRunEventStream(response: Response, handlers: RunEventHandlers = {}) {
  if (!response.ok || !response.body) {
    throw new Error(`流式响应失败：${response.status}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() || ''
      blocks.forEach((block) => handleSseBlock(block, handlers))
    }
    if (buffer.trim()) handleSseBlock(buffer, handlers)
  } finally {
    reader.releaseLock()
  }
}

export async function listConversations(token?: string, agentId?: string, conversationScopeKey?: string): Promise<ChatThread[]> {
  const params = new URLSearchParams({ limit: '50', offset: '0' })
  if (agentId) params.set('agent_id', agentId)
  if (conversationScopeKey) params.set('conversation_scope_key', conversationScopeKey)
  const response = await fetch(apiUrl(`/api/chat/threads?${params.toString()}`), {
    headers: authHeaders(token, false)
  })
  return parseResponse<ChatThread[]>(response, '获取对话列表失败')
}

export async function createConversation(options: CreateConversationOptions = {}): Promise<ChatThread> {
  const metadata: Record<string, unknown> = { source: 'chat-iframe' }
  if (options.conversationScopeKey) metadata.conversation_scope_key = options.conversationScopeKey
  const response = await fetch(apiUrl('/api/chat/thread'), {
    method: 'POST',
    headers: authHeaders(options.token),
    body: JSON.stringify({
      agent_id: options.agentId || DEFAULT_AGENT_ID,
      title: options.title || '来文咨询',
      metadata
    })
  })
  return parseResponse<ChatThread>(response, '创建对话失败')
}

export async function listMessages(threadId: string, token?: string): Promise<ChatMessage[]> {
  const response = await fetch(apiUrl(`/api/chat/thread/${encodeURIComponent(threadId)}/history`), {
    headers: authHeaders(token, false)
  })
  const data = await parseResponse<{ history?: Record<string, unknown>[] }>(response, '获取聊天记录失败')
  return (data.history || []).map(normalizeChatMessage)
}

export async function sendMessageStream(payload: SendMessagePayload, handlers: RunEventHandlers = {}) {
  const requestId = crypto.randomUUID()
  const query = buildChatQuery(payload)
  const iframeContext = buildIframeContext(payload)
  const response = await fetch(apiUrl('/api/agent/runs'), {
    method: 'POST',
    headers: authHeaders(payload.token),
    signal: payload.signal,
    body: JSON.stringify({
      query,
      agent_id: payload.agentId || DEFAULT_AGENT_ID,
      thread_id: payload.threadId,
      model_spec: payload.modelSpec || null,
      image_content: payload.imageContent || null,
      meta: {
        request_id: requestId,
        source: 'chat-iframe',
        attachment_names: payload.attachmentNames || [],
        attachments: payload.attachments || [],
        iframe_context: iframeContext,
        page_content: payload.includePage ? payload.pageContent || null : null,
        selected_file: payload.includeFile ? payload.selectedFile || null : null,
        extraction_result: payload.includeFile ? payload.extractionResult || null : null
      }
    })
  })
  const run = await parseResponse<{ id?: string; run_id?: string; stream_url?: string }>(response, '发送消息失败')
  const runId = run.id || run.run_id
  if (!runId) throw new Error('发送消息失败：缺少运行任务 ID')
  handlers.onRunStart?.(runId, requestId)
  const streamResponse = await fetch(apiUrl(`/api/agent/runs/${encodeURIComponent(runId)}/events?verbose=false`), {
    headers: authHeaders(payload.token, false),
    signal: payload.signal
  })
  await readRunEventStream(streamResponse, handlers)
  return { runId, requestId }
}

export async function cancelRun(runId: string, token?: string) {
  const response = await fetch(apiUrl(`/api/agent/runs/${encodeURIComponent(runId)}/cancel`), {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({})
  })
  return parseResponse<Record<string, unknown>>(response, '停止回答失败')
}

export async function updateConversation(
  threadId: string,
  payload: { title?: string; isPinned?: boolean },
  token?: string
) {
  const response = await fetch(apiUrl(`/api/chat/thread/${encodeURIComponent(threadId)}`), {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify({ title: payload.title, is_pinned: payload.isPinned })
  })
  return parseResponse<ChatThread>(response, '更新对话失败')
}

export async function deleteConversation(threadId: string, token?: string) {
  const response = await fetch(apiUrl(`/api/chat/thread/${encodeURIComponent(threadId)}`), {
    method: 'DELETE',
    headers: authHeaders(token, false)
  })
  return parseResponse<Record<string, unknown>>(response, '删除对话失败')
}

export async function submitMessageFeedback(
  messageId: string,
  rating: 'like' | 'dislike',
  reason: string | null,
  token?: string
) {
  const response = await fetch(apiUrl(`/api/chat/message/${encodeURIComponent(messageId)}/feedback`), {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ rating, reason })
  })
  return parseResponse<Record<string, unknown>>(response, '提交反馈失败')
}

export async function uploadImage(file: File, token?: string) {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(apiUrl('/api/chat/image/upload'), {
    method: 'POST',
    headers: authHeaders(token, false),
    body
  })
  return parseResponse<{ image_content?: string } & Record<string, unknown>>(response, '上传图片失败')
}

export async function getThreadAttachments(threadId: string, token?: string) {
  const response = await fetch(apiUrl(`/api/chat/thread/${encodeURIComponent(threadId)}/attachments`), {
    headers: authHeaders(token, false)
  })
  return parseResponse<Record<string, unknown>>(response, '获取附件失败')
}

export async function uploadAttachment(file: File, token?: string) {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(apiUrl('/api/chat/attachments/tmp'), {
    method: 'POST',
    headers: authHeaders(token, false),
    body
  })
  return parseResponse<Record<string, unknown>>(response, '上传附件失败')
}

export async function confirmThreadAttachments(
  threadId: string,
  attachments: Record<string, unknown>[],
  token?: string
) {
  const response = await fetch(apiUrl(`/api/chat/thread/${encodeURIComponent(threadId)}/attachments/confirm`), {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ attachments })
  })
  return parseResponse<Record<string, unknown>>(response, '确认附件失败')
}
