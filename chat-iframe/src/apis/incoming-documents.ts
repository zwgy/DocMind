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
    fileStatus: 'parsed',
    hasParsedMarkdown: true,
    extractionStatus: 'ready',
    runId: `ber_mock_${index + 1}`,
    reason: 'source_key matched',
    categories: {
      risk: {
        matched: true,
        evidence: 'mock evidence'
      }
    },
    items: [
      {
        item_id: 'mock-item-1',
        item_type: 'summary',
        data: { summary: 'mock summary' },
        source_quote: 'mock source quote'
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

async function parseApiResponse<T>(response: Response, fallbackMessage: string): Promise<T> {
  if (response.ok) return response.json() as Promise<T>
  let message = fallbackMessage
  try {
    const data = await response.json()
    message = data.detail || data.message || message
  } catch {
    // Keep the HTTP status when the backend response is not JSON.
  }
  throw new Error(message)
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
  return parseApiResponse<ExtractionQueryResponse>(response, `查询失败：${response.status}`)
}

export async function ingestIncomingDocument(file: IncomingPageFile, token?: string): Promise<Record<string, unknown>> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(apiUrl('/api/incoming-documents/ingest'), {
    method: 'POST',
    headers,
    body: JSON.stringify({
      sourceUrl: file.sourceUrl || file.url,
      sourceKey: file.sourceKey || file.id,
      filename: file.name
    })
  })
  return parseApiResponse<Record<string, unknown>>(response, `入库失败：${response.status}`)
}
