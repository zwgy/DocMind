import { apiUrl } from './api-url.ts'

export type ScheduledJobView = 'ongoing' | 'paused' | 'history'
export type ScheduledJobStatus = 'active' | 'paused' | 'completed' | 'cancelled'
export type ScheduledJobActionType = 'notification' | 'agent'

export type ScheduledJob = {
  id: string
  name: string
  source_type: string
  schedule_kind: 'at' | 'interval' | 'cron'
  run_at: string | null
  anchor_at: string | null
  interval_seconds: number | null
  cron_expression: string | null
  timezone: string
  next_run_at: string | null
  action_type: ScheduledJobActionType
  action_data: Record<string, unknown>
  status: ScheduledJobStatus
  version: number
  last_run_at: string | null
}

type ScheduledJobList = { items: ScheduledJob[]; next_cursor: string | null }

async function request<T>(path: string, token?: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers
    }
  })
  if (response.ok) return response.json() as Promise<T>
  const payload: { detail?: string; message?: string } = await response.json().catch(() => ({}))
  throw new Error(payload.detail || payload.message || `请求失败：${response.status}`)
}

function query(params: Record<string, string | number | undefined>) {
  return new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined).map(([key, value]) => [key, String(value)])
  ).toString()
}

export const scheduledJobApi = {
  list(view: ScheduledJobView, token?: string, cursor?: string) {
    return request<ScheduledJobList>(
      `/api/scheduled-jobs?${query({ view, limit: 20, cursor })}`,
      token
    )
  },
  changeStatus(
    jobId: string,
    payload: { action: 'pause' | 'resume' | 'cancel'; version: number },
    token?: string
  ) {
    return request<{ job: ScheduledJob }>(
      `/api/scheduled-jobs/${encodeURIComponent(jobId)}/status`,
      token,
      { method: 'POST', body: JSON.stringify(payload) }
    )
  },
  update(jobId: string, payload: Record<string, unknown>, token?: string) {
    return request<{ job: ScheduledJob }>(
      `/api/scheduled-jobs/${encodeURIComponent(jobId)}`,
      token,
      { method: 'PATCH', body: JSON.stringify(payload) }
    )
  },
  remove(jobId: string, version: number, token?: string) {
    return request<{ deleted_id: string }>(
      `/api/scheduled-jobs/${encodeURIComponent(jobId)}?version=${encodeURIComponent(version)}`,
      token,
      { method: 'DELETE' }
    )
  }
}
