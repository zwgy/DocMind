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

export type ChatMessage = {
  id: string
  role: ChatMessageRole
  content: string
  status?: 'sending' | 'streaming' | 'done' | 'error'
  toolEvents?: string[]
  createdAt?: string
}

export type ModelOption = {
  label: string
  value: string
  provider?: string
  model_id?: string
}
