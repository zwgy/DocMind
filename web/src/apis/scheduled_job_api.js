import { apiDelete, apiGet, apiPatch, apiPost } from './base'

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
  listIncoming(params = {}) {
    const query = queryString(params)
    return apiGet(`/api/scheduled-jobs/incoming${query ? `?${query}` : ''}`)
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
  changeIncomingStatus(jobId, payload) {
    return apiPost(`/api/scheduled-jobs/incoming/${encodeURIComponent(jobId)}/status`, payload)
  },
  update(jobId, payload) {
    return apiPatch(`/api/scheduled-jobs/${encodeURIComponent(jobId)}`, payload)
  },
  runs(jobId, params = {}) {
    const query = queryString(params)
    return apiGet(`/api/scheduled-jobs/${encodeURIComponent(jobId)}/runs${query ? `?${query}` : ''}`)
  },
  incomingRuns(jobId, params = {}) {
    const query = queryString(params)
    return apiGet(`/api/scheduled-jobs/incoming/${encodeURIComponent(jobId)}/runs${query ? `?${query}` : ''}`)
  },
  remove(job) {
    const prefix = job.source_type === 'incoming' ? '/api/scheduled-jobs/incoming' : '/api/scheduled-jobs'
    const query = job.source_type === 'personal' ? `?version=${encodeURIComponent(job.version)}` : ''
    return apiDelete(`${prefix}/${encodeURIComponent(job.id)}${query}`)
  }
}
