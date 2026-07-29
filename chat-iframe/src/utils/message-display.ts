import type { ChatMessage } from '../types'

export type MessageDisplayItem =
  | { type: 'message'; key: string; message: ChatMessage }
  | {
      type: 'execution-process'
      key: string
      messages: ChatMessage[]
      isActive: boolean
      hasFinalAnswer: boolean
    }

type RawDisplayItem =
  | { type: 'message'; key: string; message: ChatMessage }
  | { type: 'assistant-turn'; key: string; messages: ChatMessage[] }

function hasVisibleAssistantBody(message: ChatMessage) {
  return Boolean(message.content || message.reasoningContent || message.errorMessage)
}

function hasAssistantProcess(message: ChatMessage) {
  return Boolean(
    hasVisibleAssistantBody(message) ||
      message.toolCalls?.length ||
      message.toolEvents?.length
  )
}

function finalAnswerMessage(messages: ChatMessage[]) {
  const lastFailureIndex = messages.findLastIndex(
    (message) =>
      message.status === 'error' ||
      message.status === 'stopped' ||
      Boolean(message.errorMessage) ||
      message.toolCalls?.some((tool) => tool.status === 'error')
  )
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    // 工具失败之前的阶段性说明不能在失败发生后被提升为最终回答。
    if (index <= lastFailureIndex) return undefined
    const message = messages[index]
    // 带工具调用的模型文本通常是调用前说明，不应误判成最终回答。
    if (
      message.content.trim() &&
      !message.toolCalls?.length &&
      message.status !== 'error' &&
      message.status !== 'stopped' &&
      !message.errorMessage
    )
      return message
  }
  return undefined
}

function buildAssistantTurnItems(
  item: Extract<RawDisplayItem, { type: 'assistant-turn' }>,
  isActive: boolean
): MessageDisplayItem[] {
  const visibleMessages = item.messages.filter(hasAssistantProcess)
  if (!visibleMessages.length) return []

  const bodyCount = visibleMessages.filter(hasVisibleAssistantBody).length
  const hasExecutionEvidence = visibleMessages.some(
    (message) => message.toolCalls?.length || message.toolEvents?.length
  ) || bodyCount > 1

  // 普通单段回答保持原有展示；只有出现工具或多段模型回复时才进入“执行过程”。
  if (!hasExecutionEvidence) {
    return visibleMessages.map((message) => ({
      type: 'message',
      key: message.id,
      message
    }))
  }

  const finalMessage = !isActive ? finalAnswerMessage(visibleMessages) : undefined
  const processMessages = visibleMessages.filter((message) => message !== finalMessage)
  const result: MessageDisplayItem[] = []

  if (processMessages.length) {
    result.push({
      type: 'execution-process',
      key: `execution-process-${item.key}`,
      messages: processMessages,
      isActive,
      hasFinalAnswer: Boolean(finalMessage)
    })
  }

  if (finalMessage) {
    result.push({
      type: 'message',
      key: finalMessage.id,
      message: finalMessage
    })
  }

  return result
}

export function executionProcessShouldExpand(
  isActive: boolean,
  hasFailure: boolean,
  hasFinalAnswer: boolean
) {
  return isActive || hasFailure || !hasFinalAnswer
}

export function groupMessageDisplayItems(
  messages: ChatMessage[] = [],
  options: { streaming?: boolean } = {}
): MessageDisplayItem[] {
  const rawItems: RawDisplayItem[] = []
  let assistantMessages: ChatMessage[] = []
  let assistantKey = ''

  const flushAssistantTurn = () => {
    if (!assistantMessages.length) return
    rawItems.push({
      type: 'assistant-turn',
      key: assistantKey || assistantMessages[0].id,
      messages: assistantMessages
    })
    assistantMessages = []
    assistantKey = ''
  }

  messages.forEach((message, index) => {
    if (message.role === 'assistant') {
      if (!assistantMessages.length) assistantKey = message.id || `assistant-${index}`
      assistantMessages.push(message)
      return
    }

    flushAssistantTurn()
    rawItems.push({ type: 'message', key: message.id || `message-${index}`, message })
  })
  flushAssistantTurn()

  const lastAssistantTurnIndex = rawItems.findLastIndex((item) => item.type === 'assistant-turn')
  return rawItems.flatMap((item, index) =>
    item.type === 'assistant-turn'
      ? buildAssistantTurnItems(
          item,
          Boolean(options.streaming && index === lastAssistantTurnIndex)
        )
      : [item]
  )
}
