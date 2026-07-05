import type { ChatMessage, ChatThread, ExtractionResult, IncomingPageFile, PageContent } from '../types'

const DEFAULT_AGENT_ID = 'default-chatbot'

type RequestOptions = {
  token?: string
}

type CreateConversationOptions = RequestOptions & {
  agentId?: string
  title?: string
}

type RunEventHandlers = {
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
}

type SendMessagePayload = ChatContextInput &
  RequestOptions & {
    threadId: string
    agentId?: string
    modelSpec?: string
    attachmentNames?: string[]
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

function compactText(value?: string, limit = 1200) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit)}...` : text
}

function summarizeExtraction(result?: ExtractionResult | null) {
  if (!result) return ''
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
}

function normalizeHistoryItem(item: Record<string, unknown>): ChatMessage {
  const type = String(item.type || item.role || 'assistant')
  const role = type === 'human' ? 'user' : type === 'ai' ? 'assistant' : type
  return {
    id: String(item.id || crypto.randomUUID()),
    role: role === 'user' || role === 'assistant' || role === 'tool' || role === 'system' ? role : 'assistant',
    content: String(item.content || ''),
    status: item.error_message ? 'error' : 'done',
    createdAt: typeof item.created_at === 'string' ? item.created_at : undefined
  }
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
  const text = extractTextDelta(current)
  if (text) handlers.onText?.(text)
  const tool = extractToolEvent(current)
  if (tool) handlers.onTool?.(tool)
  if (status === 'error') handlers.onError?.(String(current.error_message || current.message || '对话失败'))
  if (status === 'finished' || status === 'completed') handlers.onDone?.()
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

export async function listConversations(token?: string, agentId?: string): Promise<ChatThread[]> {
  const params = new URLSearchParams({ limit: '50', offset: '0' })
  if (agentId) params.set('agent_id', agentId)
  const response = await fetch(`/api/chat/threads?${params.toString()}`, {
    headers: authHeaders(token, false)
  })
  return parseResponse<ChatThread[]>(response, '获取对话列表失败')
}

export async function createConversation(options: CreateConversationOptions = {}): Promise<ChatThread> {
  const response = await fetch('/api/chat/thread', {
    method: 'POST',
    headers: authHeaders(options.token),
    body: JSON.stringify({
      agent_id: options.agentId || DEFAULT_AGENT_ID,
      title: options.title || '来文咨询',
      metadata: { source: 'chat-iframe' }
    })
  })
  return parseResponse<ChatThread>(response, '创建对话失败')
}

export async function listMessages(threadId: string, token?: string): Promise<ChatMessage[]> {
  const response = await fetch(`/api/chat/thread/${encodeURIComponent(threadId)}/history`, {
    headers: authHeaders(token, false)
  })
  const data = await parseResponse<{ history?: Record<string, unknown>[] }>(response, '获取聊天记录失败')
  return (data.history || []).map(normalizeHistoryItem)
}

export async function sendMessageStream(payload: SendMessagePayload, handlers: RunEventHandlers = {}) {
  const requestId = crypto.randomUUID()
  const query = buildChatQuery(payload)
  const response = await fetch('/api/agent/runs', {
    method: 'POST',
    headers: authHeaders(payload.token),
    signal: payload.signal,
    body: JSON.stringify({
      query,
      agent_id: payload.agentId || DEFAULT_AGENT_ID,
      thread_id: payload.threadId,
      model_spec: payload.modelSpec || null,
      meta: {
        request_id: requestId,
        source: 'chat-iframe',
        attachment_names: payload.attachmentNames || [],
        page_content: payload.includePage ? payload.pageContent || null : null,
        selected_file: payload.includeFile ? payload.selectedFile || null : null,
        extraction_result: payload.includeFile ? payload.extractionResult || null : null
      }
    })
  })
  const run = await parseResponse<{ id?: string; run_id?: string; stream_url?: string }>(response, '发送消息失败')
  const runId = run.id || run.run_id
  if (!runId) throw new Error('发送消息失败：缺少运行任务 ID')
  const streamResponse = await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/events?verbose=false`, {
    headers: authHeaders(payload.token, false),
    signal: payload.signal
  })
  await readRunEventStream(streamResponse, handlers)
  return { runId, requestId }
}

export async function uploadAttachment(file: File, token?: string) {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch('/api/chat/attachments/tmp', {
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
  const response = await fetch(`/api/chat/thread/${encodeURIComponent(threadId)}/attachments/confirm`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ attachments })
  })
  return parseResponse<Record<string, unknown>>(response, '确认附件失败')
}
