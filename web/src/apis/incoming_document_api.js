import { apiAdminGet, apiAdminPost, apiAdminPut } from './base'

const buildQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  })
  return query.toString()
}

export const incomingDocumentApi = {
  list: async (params = {}) => {
    const query = buildQuery(params)
    return apiAdminGet(`/api/incoming-documents${query ? `?${query}` : ''}`)
  },

  detail: async (incomingId) => {
    return apiAdminGet(`/api/incoming-documents/${incomingId}`)
  },

  getOriginalFile: async (incomingId, sourceFileId) => {
    const query = sourceFileId ? `?source_file_id=${encodeURIComponent(sourceFileId)}` : ''
    return apiAdminGet(`/api/incoming-documents/${incomingId}/file/original${query}`, {}, 'blob')
  },

  getMarkdown: async (incomingId, sourceFileId) => {
    return apiAdminGet(
      `/api/incoming-documents/${incomingId}/file/markdown?source_file_id=${encodeURIComponent(sourceFileId)}`
    )
  },

  importToKnowledge: async (incomingId, payload) => {
    return apiAdminPost(`/api/incoming-documents/${incomingId}/knowledge-import`, payload)
  },

  retry: async (incomingId) => {
    return apiAdminPost(`/api/incoming-documents/${incomingId}/retry`, {})
  },

  options: async () => apiAdminGet('/api/incoming-documents/options'),

  correctClassification: async (incomingId, classification) => {
    return apiAdminPut(`/api/incoming-documents/${incomingId}/classification`, { classification })
  },

  confirm: async (incomingId) => {
    return apiAdminPost(`/api/incoming-documents/${incomingId}/confirm`, {})
  }
}
