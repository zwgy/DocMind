import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')

function sseBlock(id, event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\nid: ${id}\n\n`
}

test('active run resumes into its own thread runtime', async () => {
  setActivePinia(createPinia())
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    if (url === '/api/agent/thread/thread-a/active_run') return Response.json({ run: { id: 'run-a', status: 'running' } })
    if (url === '/api/chat/thread/thread-a/state') {
      return Response.json({ agent_state: { todos: [{ content: '恢复任务', status: 'in_progress' }] } })
    }
    if (url === '/api/agent/runs/run-a') return Response.json({ run: { status: 'running' } })
    if (url.startsWith('/api/agent/runs/run-a/events')) {
      return new Response(
        sseBlock('1-0', 'messages', { payload: { items: [{ status: 'running', stream_event: { type: 'message_delta', content: 'resumed' } }] } }) +
          sseBlock('2-0', 'end', { payload: { status: 'completed' } }),
        { status: 200, headers: { 'content-type': 'text/event-stream' } }
      )
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-b'
  chat.ensureRuntime('thread-a')

  await chat.resumeActiveRun('thread-a', 'token-1')

  const runtime = chat.threadRuntimes['thread-a']
  assert.equal(runtime.messages.length, 1)
  assert.equal(runtime.messages[0].content, 'resumed')
  assert.equal(runtime.isStreaming, false)
  assert.equal(runtime.activeRunId, '')
  assert.deepEqual(runtime.agentState?.todos, [{ content: '恢复任务', status: 'in_progress' }])
  assert.equal(chat.messages.length, 0, '当前线程 B 不应显示 A 的恢复内容')
  assert.equal(calls.some((call) => call.url === '/api/chat/thread'), false)
})
