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
  size_text?: string
  size_bytes?: number
  source_url?: string
  source_file_id?: string
  source_function_id?: string
  source_doc_id?: string
  source_system?: string
  document_number?: string
  title?: string
  incoming_type?: string
  source_unit?: string
  incoming_date?: string
  onclick?: string
  selected?: boolean
}

export type IframeConfig = {
  token?: string
  apiBaseUrl?: string
  agentId?: string
  conversationScopeKey?: string
  authError?: string
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

export type ExtractionDisplay = {
  classificationLabel?: string | null
  categoryLabels?: Record<string, string>
  schemaLabels?: Record<string, string>
  fieldLabels?: Record<string, Record<string, string>>
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
  fileStatus?: string
  hasParsedMarkdown?: boolean
  taskId?: string | null
  classification?: string | null
  categories?: Record<string, ExtractionCategory>
  items?: ExtractionItem[]
  schemaIds?: string[]
  summary?: string | null
  structuredResult?: Record<string, unknown> | null
  display?: ExtractionDisplay
}

export type IframeContextFile = IncomingPageFile & {
  matchStatus?: string
  extractionStatus?: string
  fileStatus?: string
  hasParsedMarkdown?: boolean
  kbId?: string
  fileId?: string
  runId?: string | null
  summary?: string
  summaryTruncated?: boolean
  categories?: Record<string, ExtractionCategory>
  items?: ExtractionItem[]
  schemaIds?: string[]
  display?: ExtractionDisplay
}

export type IframeContextPayload = {
  page?: PageContent
  files: IframeContextFile[]
}

export type ContextSummaryPayload = {
  file: IncomingPageFile
  result: ExtractionResult | null
  loading?: boolean
  error?: string
  statusText: string
  matchedCategories: Array<{ name: string; evidence?: string | null }>
  items: ExtractionItem[]
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

export type ChatMessageFeedback = {
  rating: 'like' | 'dislike'
  reason: string | null
}

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
  status?: string
  [key: string]: unknown
}

export type ChatArtifact = {
  path: string
  name: string
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
  artifacts?: ChatArtifact[]
  errorType?: string
  errorMessage?: string
  modelName?: string
  feedback?: ChatMessageFeedback
  feedbackSubmitting?: boolean
  contextSummary?: ContextSummaryPayload
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
