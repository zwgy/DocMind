import type { ChatMessage, ExtractionResult, IncomingPageFile } from '@/types'

type SummaryInput = {
  file: IncomingPageFile | null
  result: ExtractionResult | null
  loading?: boolean
  error?: string
}

function contextSummaryFile(file: IncomingPageFile, result: ExtractionResult | null): IncomingPageFile {
  // 宿主页的附件名才是当前选择对象；来文标题仅补充元数据，不能覆盖副附件名称。
  return {
    ...file,
    name: file.name,
    source_system: result?.source_system || file.source_system,
    document_number: result?.document_number || file.document_number,
    title: result?.title || file.title,
    incoming_type: result?.incoming_type || file.incoming_type,
    source_unit: result?.source_unit || file.source_unit,
    incoming_date: result?.incoming_date || file.incoming_date
  }
}

export function extractionStatusText(input: SummaryInput) {
  if (input.loading) return '查询中'
  if (input.error) return input.error
  if (!input.file) return '未选择附件'
  if (!input.result) return '等待查询'
  if (input.result.matchStatus !== 'matched') {
    return (
      {
        pending_sync: '待同步入库',
        not_found: '未匹配到来文',
        multiple: '匹配到多个文档'
      }[input.result.matchStatus] || input.result.matchStatus
    )
  }
  return (
    {
      ready: '已生成结构化结果',
      running: '抽取中',
      uploaded: '等待处理',
      parsing: '解析中',
      extracting: '抽取中',
      not_found: '暂无抽取结果',
      failed: '抽取失败'
    }[input.result.extractionStatus] || input.result.extractionStatus
  )
}

export function matchedExtractionCategories(result?: ExtractionResult | null) {
  return Object.entries(result?.categories || {})
    .filter(([, value]) => value?.matched)
    .map(([name, value]) => ({ name: result?.display?.categoryLabels?.[name] || name, evidence: value.evidence }))
}

export function extractionSummaryText(result?: ExtractionResult | null) {
  // 后端 summary 与结构化 items 是两类结果；保留 summary 可避免用户看到“已生成”但无内容的误导状态。
  return String(result?.summary || '').trim()
}

export function extractionClassificationText(result?: ExtractionResult | null) {
  return String(result?.display?.classificationLabel || result?.classification || '').trim()
}

export function extractionItemTypeText(type?: string | null, result?: ExtractionResult | null) {
  const normalized = String(type || '').trim()
  return result?.display?.schemaLabels?.[normalized] || normalized || '结构化结果'
}

export function displayExtractionDataEntries(
  data?: Record<string, unknown> | null,
  itemType?: string | null,
  result?: ExtractionResult | null
) {
  const fieldLabels = result?.display?.fieldLabels?.[String(itemType || '')] || {}
  return Object.entries(data || {})
    .filter(([key, value]) => key !== 'source_quote' && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => [fieldLabels[key] || key, value] as [string, unknown])
}

function summaryContent(input: SummaryInput) {
  const status = extractionStatusText(input)
  const lines = [`### 文档结构化摘要`, `来文：${input.file?.name || '未选择来文'}`, `状态：${status}`]
  const categories = matchedExtractionCategories(input.result)
  if (categories.length) lines.push(`分类：${categories.map((item) => item.name).join('、')}`)
  const summary = extractionSummaryText(input.result)
  if (summary) lines.push(`摘要：${summary}`)
  const quotes = (input.result?.items || [])
    .map((item) => item.source_quote || '')
    .filter(Boolean)
    .slice(0, 3)
  if (quotes.length) lines.push(`原文依据：${quotes.join('；')}`)
  if (input.result?.reason) lines.push(`说明：${input.result.reason}`)
  return lines.join('\n')
}

export function buildContextSummaryMessage(input: SummaryInput, id = 'context-summary'): ChatMessage | null {
  if (!input.file) return null
  const file = contextSummaryFile(input.file, input.result)
  const matchedCategories = matchedExtractionCategories(input.result)
  // 摘要卡片是当前页面上下文，不写入后端历史；调用方按附件提供稳定 id，便于整体替换。
  return {
    id,
    role: 'system',
    type: 'context_summary',
    content: summaryContent({ ...input, file }),
    status: 'done',
    contextSummary: {
      file,
      result: input.result,
      loading: input.loading,
      error: input.error,
      statusText: extractionStatusText(input),
      matchedCategories,
      items: input.result?.items || []
    }
  }
}
