import type { ExtractionQueryResponse, IncomingPageFile } from '@/types'

export async function queryIncomingDocumentExtractions(
  files: IncomingPageFile[],
  token?: string
): Promise<ExtractionQueryResponse> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch('/api/incoming-documents/extractions/query', {
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
