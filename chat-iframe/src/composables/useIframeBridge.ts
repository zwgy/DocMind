import { onMounted, onUnmounted } from 'vue'
import { setApiBaseUrl } from '@/apis/api-url'
import { useIframeContextStore } from '@/stores/iframe-context'
import type {
  FileIngestStage,
  FileIngestStatePayload,
  IframeConfig,
  IncomingPageFile,
  PageContent,
  ParentMessage,
  WindowState
} from '@/types'
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
  let fileIngestSequence = 0
  const fileIngestRequests = new Map<
    string,
    {
      resolve: () => void
      reject: (error: Error) => void
      onState: (stage: FileIngestStage, error?: string) => void
    }
  >()

  function rejectFileIngestRequests(message: string) {
    fileIngestRequests.forEach(({ reject }) => reject(new Error(message)))
    fileIngestRequests.clear()
  }

  function send(type: string, payload?: unknown) {
    if (!context.isEmbedded) return
    // 完成握手后使用锁定的父页面 origin，避免把运行期消息广播给其他嵌入方。
    window.parent.postMessage({ type, payload, timestamp: Date.now() }, parentOrigin || '*')
  }

  function handleMessage(event: MessageEvent) {
    const message = (event.data || {}) as ParentMessage
    if (!message.type || !isTrustedParentMessage(event, message)) return
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
      case 'PAGE_FILES_UPDATED': {
        // 两个消息名来自不同接入版本，统一入口能避免附件状态在兼容路径上分叉。
        const nextFiles = (message.payload as IncomingPageFile[] | undefined) || []
        context.setFiles(nextFiles)
        break
      }
      case 'FILE_INGEST_STATE': {
        const payload = message.payload as FileIngestStatePayload | undefined
        const request = payload?.requestId ? fileIngestRequests.get(payload.requestId) : null
        if (!payload || !request) break
        request.onState(payload.stage, payload.error)
        if (payload.stage === 'completed') {
          fileIngestRequests.delete(payload.requestId)
          request.resolve()
        } else if (payload.stage === 'failed') {
          fileIngestRequests.delete(payload.requestId)
          request.reject(new Error(payload.error || '附件准备失败'))
        }
        break
      }
      case 'WINDOW_STATE':
        context.setWindowState(
          ((message.payload as { state?: WindowState } | undefined)?.state ||
            'normal') as WindowState
        )
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
    rejectFileIngestRequests('附件准备已取消')
  })

  function requestFileIngest(
    files: IncomingPageFile[],
    onState: (stage: FileIngestStage, error?: string) => void
  ) {
    if (!context.isEmbedded || !context.config.parentFileIngest) {
      return Promise.reject(new Error('父页面 SDK 不支持附件同步'))
    }
    const sourceFileIds = [...new Set(files.map((file) => file.source_file_id).filter(Boolean))]
    if (!sourceFileIds.length) return Promise.reject(new Error('附件不能为空'))
    const requestId = `file-ingest-${Date.now()}-${++fileIngestSequence}`
    return new Promise<void>((resolve, reject) => {
      fileIngestRequests.set(requestId, { resolve, reject, onState })
      send('FILE_INGEST_REQUEST', {
        requestId,
        source_file_ids: sourceFileIds
      })
    })
  }

  return {
    notifyMinimize: () => send('MINIMIZE'),
    notifyMaximize: () => send('MAXIMIZE'),
    notifyRestore: () => send('RESTORE'),
    notifyClose: () => send('CLOSE'),
    notifyWindowDragStart: (payload: {
      clientX: number
      clientY: number
      screenX: number
      screenY: number
      pointerId?: number
    }) => send('WINDOW_DRAG_START', payload),
    notifyWindowDragMove: (payload: {
      clientX: number
      clientY: number
      screenX: number
      screenY: number
      pointerId?: number
    }) => send('WINDOW_DRAG_MOVE', payload),
    notifyWindowDragEnd: () => send('WINDOW_DRAG_END'),
    notifyConversationCreated: (payload: { conversationId: string }) =>
      send('CONVERSATION_CREATED', payload),
    notifyMessageSent: (payload: { conversationId: string; messageId?: string }) =>
      send('MESSAGE_SENT', payload),
    notifyUnreadCountChanged: (totalUnreadCount: number) =>
      send('UNREAD_COUNT_CHANGED', { total_unread_count: totalUnreadCount }),
    requestFileIngest
  }
}
