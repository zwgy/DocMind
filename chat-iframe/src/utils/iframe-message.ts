import type { ParentMessage } from '@/types'

export function createTrustedParentMessageGuard(parent: MessageEventSource | null) {
  let source: MessageEventSource | null = null
  let origin = ''

  return (event: Pick<MessageEvent, 'source' | 'origin'>, message: ParentMessage) => {
    if (event.source !== parent) return false
    if (!source) {
      if (message.type !== 'INIT_CONFIG') return false
      source = event.source
      origin = event.origin
      return true
    }
    return event.source === source && event.origin === origin && message.type !== 'INIT_CONFIG'
  }
}
