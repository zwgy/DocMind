import type { ChatToolCall } from '../types'

const HIDDEN_TOOLS = new Set(['present_artifacts'])

type LooseRecord = Record<string, unknown>

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
  return name.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

export function skillName(tool: ChatToolCall): string {
  const path = String(getToolArgs(tool).file_path || '')
  if (!path.endsWith('SKILL.md')) return ''
  const parts = path.replace(/\\/g, '/').split('/')
  return parts.length >= 2 ? parts[parts.length - 2] : ''
}

export function getToolCallLabel(tool: ChatToolCall): string {
  if (skillName(tool)) return 'Skill'
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
