import type { ChatMessage, ChatToolCall } from '../types'

export type MessageDisplayItem =
  | { type: 'message'; key: string; message: ChatMessage }
  | { type: 'tool-group'; key: string; toolCalls: ChatToolCall[] }

function hasVisibleAssistantBody(message: ChatMessage) {
  return Boolean(message.content || message.reasoningContent || message.errorMessage)
}

export function groupMessageDisplayItems(messages: ChatMessage[] = []): MessageDisplayItem[] {
  const items: MessageDisplayItem[] = []
  let pending: Extract<MessageDisplayItem, { type: 'tool-group' }> | null = null

  const flush = () => {
    if (pending) items.push(pending)
    pending = null
  }

  messages.forEach((message, index) => {
    if (message.role !== 'assistant') {
      flush()
      items.push({ type: 'message', key: message.id || `message-${index}`, message })
      return
    }

    if (hasVisibleAssistantBody(message)) {
      flush()
      items.push({ type: 'message', key: message.id || `message-${index}`, message })
    }

    if (!message.toolCalls?.length) return

    // 工具调用跨消息合并，历史消息和流式消息都走同一个摘要面板。
    if (!pending) {
      pending = { type: 'tool-group', key: `tool-group-${message.id || index}`, toolCalls: [] }
    }
    pending.toolCalls.push(...message.toolCalls)
  })

  flush()
  return items
}
