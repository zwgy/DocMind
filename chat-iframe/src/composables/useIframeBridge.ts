import { onMounted, onUnmounted } from 'vue'
import { setApiBaseUrl } from '@/apis/api-url'
import { useIframeContextStore } from '@/stores/iframe-context'
import type { IframeConfig, IncomingPageFile, PageContent, ParentMessage, WindowState } from '@/types'
import { createTrustedParentMessageGuard } from '@/utils/iframe-message'

function isEmbedded() {
  try {
    return window.self !== window.top
  } catch {
    return true
  }
}

export function useIframeBridge() {
  const context = useIframeContextStore()
  const isTrustedParentMessage = createTrustedParentMessageGuard(window.parent)
  let parentOrigin = ''

  function send(type: string, payload?: unknown) {
    if (!context.isEmbedded) return
    // 完成握手后使用锁定的父页面 origin，避免把运行期消息广播给其他嵌入方。
    window.parent.postMessage({ type, payload, timestamp: Date.now() }, parentOrigin || '*')
  }

  function handleMessage(event: MessageEvent) {
    const message = (event.data || {}) as ParentMessage
    if (!message.type || !isTrustedParentMessage(event, message)) return
    const allowlist = context.config.originAllowlist || []
    if (allowlist.length && !allowlist.includes(event.origin)) return
    switch (message.type) {
      case 'INIT_CONFIG':
        parentOrigin = event.origin
        context.setConfig(message.payload as IframeConfig | undefined)
        setApiBaseUrl((message.payload as IframeConfig | undefined)?.apiBaseUrl)
        break
      case 'PAGE_CONTENT':
        context.setPageContent(message.payload as PageContent | undefined)
        break
      case 'FILE_LIST':
      case 'PAGE_FILES_UPDATED':
        // 两个消息名来自不同接入版本，统一入口能避免附件状态在兼容路径上分叉。
        context.setFiles(message.payload as IncomingPageFile[] | undefined)
        break
      case 'WINDOW_STATE':
        context.setWindowState(((message.payload as { state?: WindowState } | undefined)?.state || 'normal') as WindowState)
        break
      default:
        break
    }
  }

  onMounted(() => {
    context.isEmbedded = isEmbedded()
    window.addEventListener('message', handleMessage)
    if (context.isEmbedded) {
      send('IFRAME_READY')
      send('REQUEST_PAGE_CONTENT')
      send('REQUEST_FILE_LIST')
    }
  })

  onUnmounted(() => {
    window.removeEventListener('message', handleMessage)
  })

  return {
    notifyMinimize: () => send('MINIMIZE'),
    notifyMaximize: () => send('MAXIMIZE'),
    notifyRestore: () => send('RESTORE'),
    notifyClose: () => send('CLOSE'),
    notifyWindowDragStart: (payload: { clientX: number; clientY: number; screenX: number; screenY: number; pointerId?: number }) =>
      send('WINDOW_DRAG_START', payload),
    notifyWindowDragMove: (payload: { clientX: number; clientY: number; screenX: number; screenY: number; pointerId?: number }) =>
      send('WINDOW_DRAG_MOVE', payload),
    notifyWindowDragEnd: () => send('WINDOW_DRAG_END'),
    notifyConversationCreated: (payload: { conversationId: string }) => send('CONVERSATION_CREATED', payload),
    notifyMessageSent: (payload: { conversationId: string; messageId?: string }) => send('MESSAGE_SENT', payload)
  }
}
