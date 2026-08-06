import { apiAdminGet, apiAdminPost, apiPatch } from './base'

function queryString(params = {}) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  return query.toString()
}

export const scheduledJobCandidateApi = {
  list(params = {}) {
    const query = queryString(params)
    return apiAdminGet(`/api/scheduled-job-candidates${query ? `?${query}` : ''}`)
  },
  update(id, payload) {
    return apiPatch(`/api/scheduled-job-candidates/${encodeURIComponent(id)}`, payload)
  },
  enable(id, version) {
    return apiAdminPost(`/api/scheduled-job-candidates/${encodeURIComponent(id)}/enable`, { version })
  },
  reject(id, version, reason) {
    return apiAdminPost(`/api/scheduled-job-candidates/${encodeURIComponent(id)}/reject`, { version, reason })
  }
}
