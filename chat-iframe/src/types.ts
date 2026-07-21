export type WindowState = 'minimized' | 'normal' | 'maximized' | 'closed'

export type PageContent = {
  title?: string
  url?: string
  html?: string
  text?: string
}

export type IncomingPageFile = {
  source_file_id: string
  name: string
  size_text?: string
  size_bytes?: number
  source_url?: string
  source_function_id?: string
  source_doc_id?: string
  source_system?: string
  document_metadata?: Record<string, unknown>
  is_main_file?: boolean
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

export type AdditionalClassification = {
  classification: string
  confidence: number
  evidence: string
}

export type ExtractionItem = {
  item_id: string
  item_type: string
  data?: Record<string, unknown>
  source_quote?: string | null
  evidence?: Array<{
    source_file_id?: string
    incoming_file_id?: string
    file_name?: string
    quote?: string | null
    source_location?: string | null
  }>
}

export type IncomingDocumentFileResult = {
  sourceFileId: string
  filename: string
  isMainFile?: boolean
  status?: string
  hasParsedMarkdown?: boolean
}

export type ExtractionDisplay = {
  classificationLabel?: string | null
  categoryLabels?: Record<string, string>
  schemaLabels?: Record<string, string>
  fieldLabels?: Record<string, Record<string, string>>
}

export type ExtractionResult = {
  incomingId?: string
  incomingFileId?: string
  name?: string
  source_system?: string
  document_number?: string
  title?: string
  incoming_type?: string
  source_unit?: string
  incoming_date?: string
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
  aiClassificationEvidence?: string | null
  additionalClassifications?: AdditionalClassification[]
  categories?: Record<string, ExtractionCategory>
  items?: ExtractionItem[]
  schemaIds?: string[]
  summary?: string | null
  structuredResult?: Record<string, unknown> | null
  files?: IncomingDocumentFileResult[]
  display?: ExtractionDisplay
}

export type IframeContextFile = IncomingPageFile & {
  documentTitle?: string
  incomingId?: string
  matchStatus?: string
  extractionStatus?: string
  fileStatus?: string
  hasParsedMarkdown?: boolean
  kbId?: string
  fileId?: string
  runId?: string | null
  summary?: string
  summaryTruncated?: boolean
  classification?: string | null
  aiClassificationEvidence?: string | null
  categories?: Record<string, ExtractionCategory>
  additionalClassifications?: AdditionalClassification[]
  items?: ExtractionItem[]
  schemaIds?: string[]
  display?: ExtractionDisplay
  documentFiles?: IncomingDocumentFileResult[]
  selectedFiles?: IframeContextFile[]
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
  attachments: Array<{ file: IncomingPageFile; result: ExtractionResult | null }>
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
