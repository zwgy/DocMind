/**
 * 旧版浏览器或嵌入式 WebView 可能提供 crypto 但不支持 randomUUID；
 * 本地请求和消息仍需要唯一标识，不能因此中断聊天。
 */
export function createClientId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `client-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}
