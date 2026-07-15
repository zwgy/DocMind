import type { ChatArtifact, ChatMessage, ChatMessageRole, RunStreamChunk } from '../types'
import { normalizeToolCalls } from './tool-calls.ts'

function roleFromType(type: string): ChatMessageRole {
  if (type === 'human') return 'user'
  if (type === 'ai') return 'assistant'
  if (type === 'tool' || type === 'system' || type === 'user' || type === 'assistant') return type
  return 'assistant'
}

function textContent(value: unknown) {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    const textPart = value.find((item) => item && typeof item === 'object' && item.type === 'text')
    return typeof textPart?.text === 'string' ? textPart.text : ''
  }
  return ''
}

function parseReasoning(content: string, explicit?: unknown) {
  const direct = typeof explicit === 'string' ? explicit : ''
  if (direct) return { content, reasoningContent: direct }
  const match = content.match(/<think>(.*?)<\/think>|<think>(.*?)$/s)
  if (!match) return { content, reasoningContent: '' }
  return {
    content: content.replace(match[0], '').trim(),
    reasoningContent: (match[1] || match[2] || '').trim()
  }
}

function presentedArtifacts(value: unknown): ChatArtifact[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  return value
    .filter((path): path is string => typeof path === 'string' && Boolean(path.trim()))
    .map((path) => path.trim())
    .filter((path) => {
      if (seen.has(path)) return false
      seen.add(path)
      return true
    })
    .map((path) => ({ path, name: path.split('/').filter(Boolean).pop() || '未命名交付物' }))
}

export function normalizeChatMessage(item: Record<string, unknown>): ChatMessage {
  const type = String(item.type || item.role || 'assistant')
  const extra =
    item.extra_metadata && typeof item.extra_metadata === 'object'
      ? (item.extra_metadata as Record<string, unknown>)
      : {}
  const additional =
    item.additional_kwargs && typeof item.additional_kwargs === 'object'
      ? (item.additional_kwargs as Record<string, unknown>)
      : {}
  const parsed = parseReasoning(textContent(item.content), additional.reasoning_content)
  const errorMessage = String(item.error_message || extra.error_message || '')
  const errorType = String(item.error_type || extra.error_type || '')
  const responseMetadata =
    item.response_metadata && typeof item.response_metadata === 'object'
      ? (item.response_metadata as Record<string, unknown>)
      : extra.response_metadata && typeof extra.response_metadata === 'object'
        ? (extra.response_metadata as Record<string, unknown>)
        : {}
  const feedback = item.feedback && typeof item.feedback === 'object' ? (item.feedback as Record<string, unknown>) : null

  return {
    id: String(item.id || crypto.randomUUID()),
    role: roleFromType(type),
    type,
    content: parsed.content,
    status: errorMessage || errorType ? 'error' : 'done',
    reasoningContent: parsed.reasoningContent,
    toolCalls: normalizeToolCalls(item.tool_calls),
    imageContent: typeof item.image_content === 'string' ? item.image_content : undefined,
    attachments: Array.isArray(extra.attachments) ? extra.attachments : [],
    artifacts: presentedArtifacts(extra.presented_artifacts),
    errorType: errorType || undefined,
    errorMessage: errorMessage || undefined,
    modelName: typeof responseMetadata.model_name === 'string' ? responseMetadata.model_name : undefined,
    feedback:
      feedback?.rating === 'like' || feedback?.rating === 'dislike'
        ? { rating: feedback.rating, reason: typeof feedback.reason === 'string' ? feedback.reason : null }
        : undefined,
    createdAt: typeof item.created_at === 'string' ? item.created_at : undefined,
    raw: item
  }
}

export function appendRunChunk(message: ChatMessage, chunk: RunStreamChunk) {
  if (chunk.type === 'text') {
    message.content += chunk.content
    if (chunk.reasoningContent) message.reasoningContent = `${message.reasoningContent || ''}${chunk.reasoningContent}`
    return
  }
  if (chunk.type === 'tool_call') {
    const id = String(chunk.toolCallId || chunk.name || message.toolCalls?.length || 'tool')
    const calls = message.toolCalls || []
    const existing = calls.find((tool) => tool.id === id)
    if (existing) {
      existing.name = chunk.name || existing.name
      existing.args = chunk.args ?? existing.args
      existing.status = 'running'
    } else {
      calls.push({ id, name: chunk.name || 'tool', args: chunk.args, status: 'running' })
    }
    message.toolCalls = calls
    return
  }
  if (chunk.type === 'tool_result') {
    const id = String(chunk.toolCallId || '')
    if (!id) return
    const calls = message.toolCalls || []
    const existing = calls.find((tool) => tool.id === id)
    if (existing) {
      existing.result = chunk.content
      existing.status = chunk.status || 'done'
    }
    message.toolCalls = calls
  }
}

function hasAssistantBody(message: ChatMessage) {
  return Boolean(message.content || message.reasoningContent || message.errorMessage)
}

function findToolSegment(messages: ChatMessage[], toolCallId?: string) {
  if (!toolCallId) return null
  return (
    messages.find((message) => message.role === 'assistant' && message.toolCalls?.some((tool) => tool.id === toolCallId)) || null
  )
}

export function appendRunChunkSegment(
  messages: ChatMessage[],
  current: ChatMessage,
  chunk: RunStreamChunk,
  createSegment: () => ChatMessage
) {
  // 流式事件本身有顺序，按段落落消息才能避免工具调用被最终正文挤到末尾。
  if (chunk.type === 'tool_result') {
    const target = findToolSegment(messages, chunk.toolCallId)
    if (target) {
      appendRunChunk(target, chunk)
      return current
    }
  }

  const needsNewTextSegment = chunk.type === 'text' && current.toolCalls?.length && !hasAssistantBody(current)
  const needsNewToolSegment = chunk.type === 'tool_call' && hasAssistantBody(current)
  if (needsNewTextSegment || needsNewToolSegment) {
    current = createSegment()
    messages.push(current)
  }

  appendRunChunk(current, chunk)
  return current
}
