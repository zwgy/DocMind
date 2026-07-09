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

function normalizeFiles(files: IncomingPageFile[] = [], selectedIds: string[] = [], sourceSystem = ''): IncomingPageFile[] {
  const selected = new Set(selectedIds)
  const normalized = (files || []).map((file) => {
    const sourceUrl = file.sourceUrl || file.url || ''
    const sourceFileId = file.source_file_id || file.sourceFileId || file.sourceKey
    const id = file.id || sourceFileId || sourceUrl || file.name
    return {
      ...file,
      id,
      sourceUrl,
      url: sourceUrl || file.url,
      sourceSystem: file.sourceSystem || file.source_system || sourceSystem || undefined,
      selected: Boolean(file.selected || selected.has(id) || (sourceFileId && selected.has(sourceFileId)))
    }
  })
  if (normalized.length && !normalized.some((file) => file.selected)) {
    // 页面打开后要自动查询，默认首个附件能保持零点击闭环。
    normalized[0].selected = true
  }
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
      this.files = normalizeFiles(files, this.config.selectedFileIds || [], this.config.source_system || '')
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
