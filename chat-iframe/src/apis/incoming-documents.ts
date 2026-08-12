import type { ExtractionQueryResponse, IncomingPageFile } from '@/types'
import { apiUrl } from './api-url.ts'

function mockExtractionItem(file: IncomingPageFile, mode: string, index: number) {
  const incomingFileId = file.source_file_id
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
    reason: 'source_file_id matched',
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

export async function ingestIncomingDocument(
  files: IncomingPageFile[],
  token?: string,
  options: { source_system?: string } = {}
): Promise<Record<string, unknown>> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  const first = files[0]
  if (!first) throw new Error('附件不能为空')
  const sourceDocId = first.source_doc_id
  const sourceSystem = first.source_system || options.source_system || 'production'
  if (!sourceDocId) throw new Error('附件缺少 source_doc_id')
  if (
    files.some(
      (file) =>
        file.source_doc_id !== sourceDocId ||
        (file.source_system || options.source_system || 'production') !== sourceSystem
    )
  ) {
    throw new Error('一次只能同步同一份来文的附件')
  }
  if (files.some((file) => !file.source_url)) throw new Error('附件缺少下载地址')
  const documentMetadata = first.document_metadata || {
    source_doc_id: sourceDocId,
    document_number: first.document_number,
    title: first.title,
    incoming_type: first.incoming_type,
    source_unit: first.source_unit,
    incoming_date: first.incoming_date
  }
  headers['Content-Type'] = 'application/json'
  const response = await fetch(apiUrl('/api/incoming-documents/ingest'), {
    method: 'POST',
    headers,
    body: JSON.stringify({
      source_system: sourceSystem,
      document_metadata: { ...documentMetadata, source_doc_id: sourceDocId },
      files: files.map((file) => ({
        source_file_id: file.source_file_id,
        filename: file.name,
        source_url: file.source_url,
        is_main_file: file.is_main_file === true
      }))
    })
  })
  return parseApiResponse<Record<string, unknown>>(response, `入库失败：${response.status}`)
}
