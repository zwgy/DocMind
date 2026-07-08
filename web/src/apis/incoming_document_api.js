import { apiAdminGet, apiAdminPost } from './base'

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

  importToKnowledge: async (incomingId, payload) => {
    return apiAdminPost(`/api/incoming-documents/${incomingId}/knowledge-import`, payload)
  }
}
