import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')

function sseBlock(id, event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\nid: ${id}\n\n`
}

test('stream reconnects from the last SSE id without replaying visible text', async () => {
  setActivePinia(createPinia())
  const eventRequests = []
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') return Response.json({ id: 'run-1' })
    if (url === '/api/agent/runs/run-1') return Response.json({ run: { status: 'running' } })
    if (url.startsWith('/api/agent/runs/run-1/events')) {
      eventRequests.push(options.headers)
      const body =
        eventRequests.length === 1
          ? sseBlock('1-0', 'messages', { payload: { items: [{ status: 'running', stream_event: { type: 'message_delta', content: 'first' } }] } })
          : sseBlock('2-0', 'messages', { payload: { items: [{ status: 'running', stream_event: { type: 'message_delta', content: ' second' } }] } }) +
            sseBlock('3-0', 'end', { payload: { status: 'completed' } })
      return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-1'
  chat.ensureRuntime('thread-1')

  await chat.send({ text: 'question', files: [], imageFile: null }, 'token-1')

  assert.equal(eventRequests.length, 2)
  assert.equal(eventRequests[0]['Last-Event-ID'], undefined)
  assert.equal(eventRequests[1]['Last-Event-ID'], '1-0')
  assert.equal(chat.messages[1].content, 'first second')
})
