import { apiUrl } from './api-url'

async function request(path: string, token?: string, options: RequestInit = {}) {
  const response = await fetch(apiUrl(path), {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers }
  })
  if (response.ok) return response.json()
  const payload = await response.json().catch(() => ({}))
  throw new Error(payload.detail || payload.message || `请求失败：${response.status}`)
}

export const inboxApi = {
  unreadCount: (token?: string) => request('/api/inbox/unread-count', token),
  list: (category: 'notification' | 'task', token?: string) => request(`/api/inbox/${category === 'task' ? 'tasks' : 'notifications'}?limit=20`, token),
  markRead: (category: 'notification' | 'task', id: string, token?: string) => request(`/api/inbox/${category === 'task' ? `tasks/${encodeURIComponent(id)}` : `notifications/${encodeURIComponent(id)}`}/read`, token, { method: 'POST', body: '{}' })
}
