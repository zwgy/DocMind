import { defineStore } from 'pinia'
import type { IframeConfig, IncomingPageFile, PageContent, WindowState } from '@/types'

type IframeContextState = {
  config: IframeConfig
  pageContent: PageContent
  files: IncomingPageFile[]
  selectedSourceFileId: string
  windowState: WindowState
  windowStateInitialized: boolean
  isEmbedded: boolean
}

function normalizeFiles(files: IncomingPageFile[] = []): IncomingPageFile[] {
  const normalized = (files || []).map((file) => {
    const sourceUrl = file.source_url || ''
    const sourceFileId = file.source_file_id.trim()
    if (!sourceFileId) throw new Error('附件缺少 source_file_id')
    return {
      ...file,
      source_file_id: sourceFileId,
      source_url: sourceUrl,
      selected: Boolean(file.selected)
    }
  })
  return normalized
}

export const useIframeContextStore = defineStore('iframe-context', {
  state: (): IframeContextState => ({
    config: {},
    pageContent: {},
    files: [],
    selectedSourceFileId: '',
    windowState: 'normal',
    windowStateInitialized: false,
    isEmbedded: false
  }),
  getters: {
    selectedFile(state) {
      return state.selectedSourceFileId ? state.files.find((file) => file.source_file_id === state.selectedSourceFileId) || null : null
    }
  },
  actions: {
    setConfig(config?: IframeConfig) {
      this.config = config || {}
    },
    setPageContent(content?: PageContent) {
      this.pageContent = content || {}
    },
    setFiles(files?: IncomingPageFile[]) {
      this.files = normalizeFiles(files)
      const preferred = this.files.find((file) => file.selected) || this.files[0]
      this.selectedSourceFileId = preferred?.source_file_id || ''
    },
    selectFile(sourceFileId: string) {
      this.selectedSourceFileId = sourceFileId
    },
    setWindowState(state: WindowState) {
      this.windowState = state
      this.windowStateInitialized = true
    }
  }
})
