import { apiGet, apiPatch, apiPost } from './base'

function queryString(params = {}) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  }
  return query.toString()
}

export const scheduledJobApi = {
  list(params = {}) {
    const query = queryString(params)
    return apiGet(`/api/scheduled-jobs${query ? `?${query}` : ''}`)
  },
  get(jobId) {
    return apiGet(`/api/scheduled-jobs/${encodeURIComponent(jobId)}`)
  },
  preview(payload) {
    return apiPost('/api/scheduled-jobs/schedule-preview', payload)
  },
  changeStatus(jobId, payload) {
    return apiPost(`/api/scheduled-jobs/${encodeURIComponent(jobId)}/status`, payload)
  },
  update(jobId, payload) {
    return apiPatch(`/api/scheduled-jobs/${encodeURIComponent(jobId)}`, payload)
  },
  runs(jobId, params = {}) {
    const query = queryString(params)
    return apiGet(`/api/scheduled-jobs/${encodeURIComponent(jobId)}/runs${query ? `?${query}` : ''}`)
  }
}
