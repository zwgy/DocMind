import { defineStore } from 'pinia'
import type { IframeConfig, IncomingPageFile, PageContent, WindowState } from '@/types'

type IframeContextState = {
  config: IframeConfig
  pageContent: PageContent
  files: IncomingPageFile[]
  selectedFileId: string
  windowState: WindowState
  isEmbedded: boolean
}

function normalizeFiles(files: IncomingPageFile[] = [], selectedIds: string[] = [], config: IframeConfig = {}): IncomingPageFile[] {
  const selected = new Set(selectedIds)
  const normalized = (files || []).map((file) => {
    const sourceUrl = file.source_url || ''
    const sourceFileId = file.source_file_id
    const id = file.id || sourceFileId || sourceUrl || file.name
    return {
      ...file,
      id,
      source_url: sourceUrl,
      source_system: file.source_system || config.source_system || undefined,
      source_function_id: file.source_function_id || config.function_id || undefined,
      source_doc_id: file.source_doc_id || config.business_id || undefined,
      selected: Boolean(file.selected || selected.has(id) || (sourceFileId && selected.has(sourceFileId)))
    }
  })
  return normalized
}

export const useIframeContextStore = defineStore('iframe-context', {
  state: (): IframeContextState => ({
    config: {},
    pageContent: {},
    files: [],
    selectedFileId: '',
    windowState: 'normal',
    isEmbedded: false
  }),
  getters: {
    selectedFile(state) {
      return state.selectedFileId ? state.files.find((file) => file.id === state.selectedFileId) || null : null
    }
  },
  actions: {
    setConfig(config?: IframeConfig) {
      this.config = config || {}
      if (this.files.length) this.setFiles(this.files)
    },
    setPageContent(content?: PageContent) {
      this.pageContent = content || {}
    },
    setFiles(files?: IncomingPageFile[]) {
      this.files = normalizeFiles(files, this.config.selectedFileIds || [], this.config)
      const preferred = this.files.find((file) => file.selected) || this.files[0]
      this.selectedFileId = preferred?.id || ''
    },
    selectFile(fileId: string) {
      this.selectedFileId = fileId
    },
    setWindowState(state: WindowState) {
      this.windowState = state
    }
  }
})
