let apiBaseUrl = ''

export function setApiBaseUrl(value?: string) {
  apiBaseUrl = String(value || '').trim().replace(/\/+$/, '')
}

export function apiUrl(path: string) {
  if (/^https?:\/\//i.test(path) || !apiBaseUrl) return path
  return `${apiBaseUrl}${path.startsWith('/') ? '' : '/'}${path}`
}
