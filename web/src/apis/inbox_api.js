import { apiGet, apiPost } from './base'

function queryString(params = {}) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  }
  return query.toString()
}

export const inboxApi = {
  list(category, params = {}) {
    const query = queryString(params)
    return apiGet(
      `/api/inbox/${category === 'task' ? 'tasks' : 'notifications'}${query ? `?${query}` : ''}`
    )
  },
  unreadCount() {
    return apiGet('/api/inbox/unread-count')
  },
  markRead(category, id) {
    const path =
      category === 'task'
        ? `/api/inbox/tasks/${encodeURIComponent(id)}/read`
        : `/api/inbox/notifications/${encodeURIComponent(id)}/read`
    return apiPost(path, {})
  },
  markRunRead(jobId, runId) {
    return apiPost(
      `/api/inbox/tasks/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}/read`,
      {}
    )
  },
  markAllRead(category) {
    return apiPost('/api/inbox/read-all', { category })
  }
}
