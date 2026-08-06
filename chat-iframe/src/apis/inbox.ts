import { apiUrl } from './api-url'

export type InboxCategory = 'notification' | 'task'

export type NotificationInboxItem = {
  id: string
  title: string
  content: string
  is_read: boolean
}

export type TaskInboxItem = {
  job: { id: string; name: string }
  latest_update: { content: string } | null
  unread_update_count: number
}

export type InboxItem = NotificationInboxItem | TaskInboxItem

export type InboxListResponse = {
  items: InboxItem[]
  next_cursor: string | null
}

export type InboxUnreadCounts = {
  notification_unread_count: number
  task_unread_count: number
  total_unread_count: number
}

type MarkReadResponse = { marked_count: number }

async function request<T>(path: string, token?: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers }
  })
  if (response.ok) return response.json() as Promise<T>
  const payload: { detail?: string; message?: string } = await response.json().catch(() => ({}))
  throw new Error(payload.detail || payload.message || `请求失败：${response.status}`)
}

export const inboxApi = {
  unreadCount: (token?: string) => request<InboxUnreadCounts>('/api/inbox/unread-count', token),
  list: (category: InboxCategory, token?: string, cursor?: string) => request<InboxListResponse>(`/api/inbox/${category === 'task' ? 'tasks' : 'notifications'}?${new URLSearchParams({ limit: '20', ...(cursor ? { cursor } : {}) })}`, token),
  markRead: (category: InboxCategory, id: string, token?: string) => request<MarkReadResponse>(`/api/inbox/${category === 'task' ? `tasks/${encodeURIComponent(id)}` : `notifications/${encodeURIComponent(id)}`}/read`, token, { method: 'POST', body: '{}' }),
  markAllRead: (category: InboxCategory, token?: string) => request<MarkReadResponse>('/api/inbox/read-all', token, { method: 'POST', body: JSON.stringify({ category }) })
}
