import type { ChatMessage, ExtractionResult, IncomingPageFile } from '@/types'

type SummaryInput = {
  file: IncomingPageFile | null
  result: ExtractionResult | null
  loading?: boolean
  error?: string
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
      not_found: '暂无抽取结果',
      failed: '抽取失败'
    }[input.result.extractionStatus] || input.result.extractionStatus
  )
}

export function matchedExtractionCategories(result?: ExtractionResult | null) {
  return Object.entries(result?.categories || {})
    .filter(([, value]) => value?.matched)
    .map(([name, value]) => ({ name, evidence: value.evidence }))
}

function summaryContent(input: SummaryInput) {
  const status = extractionStatusText(input)
  const lines = [`### 文档结构化摘要`, `附件：${input.file?.name || '未选择附件'}`, `状态：${status}`]
  const categories = matchedExtractionCategories(input.result)
  if (categories.length) lines.push(`分类：${categories.map((item) => item.name).join('、')}`)
  const quotes = (input.result?.items || [])
    .map((item) => item.source_quote || '')
    .filter(Boolean)
    .slice(0, 3)
  if (quotes.length) lines.push(`原文依据：${quotes.join('；')}`)
  if (input.result?.reason) lines.push(`说明：${input.result.reason}`)
  return lines.join('\n')
}

export function buildContextSummaryMessage(input: SummaryInput): ChatMessage | null {
  if (!input.file) return null
  const matchedCategories = matchedExtractionCategories(input.result)
  // 摘要卡片是当前页面上下文，不写入后端历史；固定 id 便于切换附件时前端稳定替换。
  return {
    id: 'context-summary',
    role: 'system',
    type: 'context_summary',
    content: summaryContent(input),
    status: 'done',
    contextSummary: {
      file: input.file,
      result: input.result,
      loading: input.loading,
      error: input.error,
      statusText: extractionStatusText(input),
      matchedCategories,
      items: input.result?.items || []
    }
  }
}

