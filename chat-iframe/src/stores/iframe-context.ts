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

function normalizeFiles(files: IncomingPageFile[] = [], selectedIds: string[] = []): IncomingPageFile[] {
  const selected = new Set(selectedIds)
  const normalized = (files || []).map((file) => {
    const sourceUrl = file.sourceUrl || file.url || ''
    const id = file.id || file.sourceKey || sourceUrl || file.name
    return {
      ...file,
      id,
      sourceUrl,
      url: sourceUrl || file.url,
      selected: Boolean(file.selected || selected.has(id) || (file.sourceKey && selected.has(file.sourceKey)))
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
      return state.files.find((file) => file.id === state.selectedFileId) || state.files[0] || null
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
      this.files = normalizeFiles(files, this.config.selectedFileIds || [])
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
