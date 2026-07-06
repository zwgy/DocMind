import type { ExtractionQueryResponse, IncomingPageFile } from '@/types'
import { apiUrl } from './api-url.ts'

function mockExtractionItem(file: IncomingPageFile, mode: string, index: number) {
  const incomingFileId = file.id || file.sourceKey || file.sourceUrl || file.name
  const shouldReady = mode === 'ready' || (mode === 'mixed' && index === 0)
  if (!shouldReady) {
    return {
      incomingFileId,
      name: file.name,
      matchStatus: 'not_found',
      extractionStatus: 'not_found'
    }
  }
  return {
    incomingFileId,
    name: file.name,
    matchStatus: 'matched',
    kbId: 'kb_mock_docmind',
    fileId: `file_mock_${index + 1}`,
    extractionStatus: 'ready',
    runId: `ber_mock_${index + 1}`,
    reason: 'source_key matched',
    categories: {
      业务需求: {
        matched: true,
        evidence: '5.5 章节要求从生产系统附件 DOM 中识别文件名、大小、下载地址和 sourceKey。'
      },
      集成风险: {
        matched: true,
        evidence: 'iframe 与父页面跨 origin 时不能共享 localStorage，需要父页面显式注入 token。'
      }
    },
    items: [
      {
        item_id: 'mock-item-1',
        item_type: '文档摘要',
        data: {
          核心内容: '本章节定义 chat-iframe 在外部系统中的附件识别、页面上下文注入和端到端调试方式。',
          验收重点: '小助手应自动识别页面附件，展示结构化摘要，并在用户提问时携带当前文档上下文。'
        },
        source_quote: '父页面脚本采集附件 DOM 后，将文件列表发送给 iframe。'
      },
      {
        item_id: 'mock-item-2',
        item_type: '关键约束',
        data: {
          认证: 'Bearer Token 注入',
          上下文: '页面正文 + 选中文档结构化摘要'
        },
        source_quote: 'iframe 和主站是不同 origin，浏览器不会共享 localStorage。'
      }
    ]
  }
}

function mockExtractionResponse(files: IncomingPageFile[]): ExtractionQueryResponse | null {
  if (typeof window === 'undefined') return null
  const mode = new URLSearchParams(window.location.search).get('mockExtraction') || ''
  if (!mode || mode === 'off') return null
  return { items: files.map((file, index) => mockExtractionItem(file, mode, index)) }
}

export async function queryIncomingDocumentExtractions(
  files: IncomingPageFile[],
  token?: string
): Promise<ExtractionQueryResponse> {
  const mock = mockExtractionResponse(files)
  if (mock) return mock
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(apiUrl('/api/incoming-documents/extractions/query'), {
    method: 'POST',
    headers,
    body: JSON.stringify({ files })
  })
  if (!response.ok) {
    let message = `查询失败：${response.status}`
    try {
      const data = await response.json()
      message = data.detail || data.message || message
    } catch {
      // 后端非 JSON 错误保持 HTTP 状态，避免把解析错误暴露给用户。
    }
    throw new Error(message)
  }
  return response.json()
}
