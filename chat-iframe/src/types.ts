export type WindowState = 'minimized' | 'normal' | 'maximized' | 'closed'

export type PageContent = {
  title?: string
  url?: string
  html?: string
  text?: string
}

export type IncomingPageFile = {
  id: string
  name: string
  sizeText?: string
  sizeBytes?: number
  url?: string
  sourceUrl?: string
  sourceKey?: string
  onclick?: string
  type?: 'document' | 'image' | 'unknown'
  selected?: boolean
}

export type IframeConfig = {
  user?: string
  token?: string
  agentId?: string
  includePageContent?: boolean
  includeFiles?: boolean
  selectedFileIds?: string[]
  originAllowlist?: string[]
}

export type ParentMessage =
  | { type: 'INIT_CONFIG'; payload?: IframeConfig }
  | { type: 'PAGE_CONTENT'; payload?: PageContent }
  | { type: 'FILE_LIST'; payload?: IncomingPageFile[] }
  | { type: 'PAGE_FILES_UPDATED'; payload?: IncomingPageFile[] }
  | { type: 'WINDOW_STATE'; payload?: { state?: WindowState } }
  | { type: string; payload?: unknown }

export type ExtractionCategory = {
  matched?: boolean
  evidence?: string | null
}

export type ExtractionItem = {
  item_id: string
  item_type: string
  data?: Record<string, unknown>
  source_quote?: string | null
}

export type ExtractionResult = {
  incomingFileId?: string
  name?: string
  matchStatus: 'matched' | 'multiple' | 'pending_sync' | 'not_found' | string
  extractionStatus: 'ready' | 'running' | 'not_found' | 'failed' | string
  reason?: string
  runId?: string | null
  kbId?: string
  fileId?: string
  categories?: Record<string, ExtractionCategory>
  items?: ExtractionItem[]
}

export type ExtractionQueryResponse = {
  items?: ExtractionResult[]
}

export type ChatThread = {
  id: string
  agent_id?: string
  title?: string | null
  is_pinned?: boolean
  created_at?: string
  updated_at?: string
  metadata?: Record<string, unknown>
}

export type ChatMessageRole = 'user' | 'assistant' | 'system' | 'tool'

export type ChatToolCall = {
  id: string
  name: string
  args?: unknown
  result?: unknown
  status?: 'running' | 'done' | 'error'
}

export type ChatAttachmentPreview = {
  file_id?: string
  file_name?: string
  name?: string
  file_size?: number
  file_type?: string
  [key: string]: unknown
}

export type ChatMessage = {
  id: string
  role: ChatMessageRole
  type?: string
  content: string
  status?: 'sending' | 'streaming' | 'done' | 'error' | 'stopped'
  toolEvents?: string[]
  toolCalls?: ChatToolCall[]
  reasoningContent?: string
  imageContent?: string
  attachments?: ChatAttachmentPreview[]
  errorType?: string
  errorMessage?: string
  modelName?: string
  createdAt?: string
  raw?: Record<string, unknown>
}

export type ModelOption = {
  label: string
  value: string
  provider?: string
  model_id?: string
}

export type RunStreamChunk =
  | { type: 'text'; messageId?: string; content: string; reasoningContent?: string }
  | { type: 'tool_call'; messageId?: string; toolCallId?: string; name?: string; args?: unknown }
  | { type: 'tool_result'; toolCallId?: string; content?: unknown; status?: 'done' | 'error' }
  | { type: 'error'; message: string; errorType?: string }
  | { type: 'done' }
