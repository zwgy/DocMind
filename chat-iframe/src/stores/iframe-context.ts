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

function normalizeFiles(files: IncomingPageFile[] = []): IncomingPageFile[] {
  const normalized = (files || []).map((file) => {
    const sourceUrl = file.source_url || ''
    const sourceFileId = file.source_file_id
    const id = file.id || sourceFileId || sourceUrl || file.name
    return {
      ...file,
      id,
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
    },
    setPageContent(content?: PageContent) {
      this.pageContent = content || {}
    },
    setFiles(files?: IncomingPageFile[]) {
      this.files = normalizeFiles(files)
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
