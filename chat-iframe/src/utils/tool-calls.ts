import type { ChatMessage, ChatToolCall } from '../types'

const HIDDEN_TOOLS = new Set(['present_artifacts'])

type LooseRecord = Record<string, unknown>

// 运行事件只携带工具机器名，无法获得后端注册表中的 display_name；
// 在 iframe 端为常用内置工具提供稳定中文名称，避免把 Execute 等实现细节暴露给普通用户。
const TOOL_DISPLAY_NAMES: Record<string, string> = {
  ask_user_question: '向用户提问',
  edit_file: '编辑文件',
  execute: '执行命令',
  find_kb_document: '查找知识库文档',
  get_incoming_document_statistics: '统计来文',
  get_mindmap: '获取思维导图',
  glob: '查找文件',
  grep: '搜索文件内容',
  list_kbs: '列出知识库',
  ls: '列出目录',
  open_kb_document: '打开知识库文档',
  present_artifacts: '展示交付物',
  query_kb: '检索知识库',
  read_file: '读取文件',
  read_incoming_document: '读取来文',
  render_data_chart: '生成数据图表',
  render_flowchart: '生成流程图',
  render_mindmap: '生成思维导图',
  search_file: '搜索文件',
  search_incoming_documents: '查询来文',
  tavily_search: '网页搜索',
  write_file: '写入文件'
}

function asRecord(value: unknown): LooseRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as LooseRecord) : {}
}

export function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) return value
  try {
    return JSON.parse(trimmed)
  } catch {
    return value
  }
}

export function getToolArgs(tool: Pick<ChatToolCall, 'args'> | unknown): LooseRecord {
  const item = asRecord(tool)
  const fn = asRecord(item.function)
  return asRecord(parseMaybeJson(item.args ?? fn.arguments))
}

export function getToolResult(tool: Pick<ChatToolCall, 'result'> | unknown): unknown {
  const item = asRecord(tool)
  const result = parseMaybeJson(item.result ?? item.tool_call_result)
  const resultRecord = asRecord(result)
  return resultRecord.content === undefined ? result : parseMaybeJson(resultRecord.content)
}

function normalizeStatus(tool: LooseRecord): ChatToolCall['status'] {
  if (tool.status === 'error' || tool.status === 'failed') return 'error'
  if (tool.status === 'done' || tool.status === 'success' || tool.tool_call_result || tool.result) return 'done'
  return 'running'
}

export function normalizeToolCalls(value: unknown): ChatToolCall[] {
  if (!Array.isArray(value)) return []

  return value
    .map((tool, index) => {
      const item = asRecord(tool)
      const fn = asRecord(item.function)
      const name = String(item.name || fn.name || 'tool')
      return {
        id: String(item.id || item.tool_call_id || index),
        name,
        args: item.args ?? fn.arguments,
        result: item.result ?? item.tool_call_result,
        status: normalizeStatus(item)
      }
    })
    .filter((tool) => !HIDDEN_TOOLS.has(tool.name))
}

export function displayToolName(name = 'tool') {
  return TOOL_DISPLAY_NAMES[name] || name.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

export function skillName(tool: ChatToolCall): string {
  const path = String(getToolArgs(tool).file_path || '')
  if (!path.endsWith('SKILL.md')) return ''
  const parts = path.replace(/\\/g, '/').split('/')
  return parts.length >= 2 ? parts[parts.length - 2] : ''
}

export function getToolCallLabel(tool: ChatToolCall): string {
  const skill = skillName(tool)
  if (skill) return `激活 Skill：${skill}`
  return displayToolName(tool.name)
}

export function getToolStatusLabel(tool: ChatToolCall): string {
  if (tool.status === 'error') return '失败'
  if (tool.status === 'done') return '已完成'
  return '进行中'
}

export function isToolRunning(tool: Pick<ChatToolCall, 'status'>): boolean {
  return tool.status === 'running'
}

export function formatJson(value: unknown): string {
  const parsed = parseMaybeJson(value)
  if (parsed && typeof parsed === 'object') return JSON.stringify(parsed, null, 2)
  return String(parsed ?? '')
}

export function listKbsItems(tool: ChatToolCall) {
  const data = getToolResult(tool)
  if (!Array.isArray(data)) return []
  return data
    .map((item) => asRecord(item))
    .filter((item) => Object.keys(item).length)
    .map((item) => ({
      id: String(item.kb_id || item.id || item.name || ''),
      name: String(item.name || '未命名知识库'),
      description: String(item.description || '暂无描述')
    }))
}

export function resolveKbDisplayName(tool: ChatToolCall, toolCalls: ChatToolCall[] = []): string {
  const args = getToolArgs(tool)
  const kbId = String(args.kb_id || '')
  const explicitName = String(args.kb_name || '')
  if (explicitName) return explicitName
  if (!kbId) return ''

  for (const candidate of toolCalls) {
    if (candidate.name !== 'list_kbs') continue
    const match = listKbsItems(candidate).find((item) => item.id === kbId)
    if (match?.name) return match.name
  }

  return kbId
}

export function getToolKbDescription(tool: ChatToolCall, toolCalls: ChatToolCall[] = []): string {
  const args = getToolArgs(tool)
  const kbName = resolveKbDisplayName(tool, toolCalls)
  const fallback = String(args.kb_name || args.knowledge_base || '')
  const value = kbName || fallback
  return value ? `知识库: ${value}` : ''
}

export type QueryKbResult = {
  chunks: LooseRecord[]
  entities: LooseRecord[]
  relationships: LooseRecord[]
  references: LooseRecord[]
}

export function parseQueryKbResult(value: unknown): QueryKbResult {
  const payload = asRecord(parseMaybeJson(value))
  const chunks = Array.isArray(payload.results)
    ? payload.results
    : Array.isArray(payload.chunks)
      ? payload.chunks
      : []

  return {
    chunks: chunks.map((item) => asRecord(item)).filter((item) => Boolean(item.content)),
    entities: Array.isArray(payload.entities) ? payload.entities.map((item) => asRecord(item)) : [],
    relationships: Array.isArray(payload.relationships)
      ? payload.relationships.map((item) => asRecord(item))
      : [],
    references: Array.isArray(payload.references) ? payload.references.map((item) => asRecord(item)) : []
  }
}

export type ChatSources = {
  knowledgeChunks: Record<string, unknown>[]
  webSources: Array<{ title: string; url: string; content: string; score: number | null }>
}

export function extractFinalAnswerSources(messages: ChatMessage[], messageId: string): ChatSources {
  const answerIndex = messages.findIndex((message) => message.id === messageId)
  if (answerIndex < 0) return { knowledgeChunks: [], webSources: [] }

  let turnStart = answerIndex
  while (turnStart > 0 && messages[turnStart - 1].role !== 'user') turnStart -= 1
  const toolCalls = messages.slice(turnStart, answerIndex + 1).flatMap((message) => message.toolCalls || [])
  const knowledgeChunks: Record<string, unknown>[] = []
  const webSources: ChatSources['webSources'] = []
  const knowledgeKeys = new Set<string>()
  const webUrls = new Set<string>()

  for (const tool of toolCalls) {
    if (tool.name === 'query_kb') {
      for (const chunk of parseQueryKbResult(getToolResult(tool)).chunks) {
        const metadata = asRecord(chunk.metadata)
        const key = `${chunk.file_id || metadata.file_id || ''}:${chunk.id || chunk.chunk_id || ''}:${chunk.content || ''}`
        if (!knowledgeKeys.has(key)) {
          knowledgeKeys.add(key)
          knowledgeChunks.push(chunk)
        }
      }
    }

    if (!tool.name.toLowerCase().includes('tavily_search')) continue
    const payload = asRecord(getToolResult(tool))
    for (const result of Array.isArray(payload.results) ? payload.results : []) {
      const item = asRecord(result)
      const url = String(item.url || '').trim()
      if (!url || webUrls.has(url)) continue
      webUrls.add(url)
      webSources.push({
        title: String(item.title || url).trim(),
        url,
        content: String(item.content || ''),
        score: typeof item.score === 'number' ? item.score : null
      })
    }
  }
  return { knowledgeChunks, webSources }
}

export type KbChunkGroup = {
  filename: string
  kbId: string
  fileId: string
  chunks: LooseRecord[]
}

export function groupKbChunksByFile(chunks: unknown[]): KbChunkGroup[] {
  const groups = new Map<string, KbChunkGroup>()

  for (const chunk of chunks) {
    const item = asRecord(chunk)
    const metadata = asRecord(item.metadata)
    const filename = String(
      metadata.source ||
        metadata.file_name ||
        metadata.filename ||
        metadata.title ||
        item.file_name ||
        item.filename ||
        item.file_id ||
        item.kb_id ||
        '未知来源'
    )

    if (!groups.has(filename)) {
      groups.set(filename, {
        filename,
        kbId: String(item.kb_id || metadata.kb_id || ''),
        fileId: String(item.file_id || metadata.file_id || ''),
        chunks: []
      })
    }
    groups.get(filename)?.chunks.push({ ...item, metadata: { ...metadata, source: filename } })
  }

  return Array.from(groups.values()).sort((a, b) => a.filename.localeCompare(b.filename))
}
