import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')

function sseBlock(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

test('switching threads keeps streamed chunks in their original thread', async () => {
  setActivePinia(createPinia())
  let streamController
  let streamReadyResolve
  const streamReady = new Promise((resolve) => {
    streamReadyResolve = resolve
  })
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') return Response.json({ id: 'run-thread-a' })
    if (url.startsWith('/api/agent/runs/run-thread-a/events')) {
      const stream = new ReadableStream({
        start(controller) {
          streamController = controller
          streamReadyResolve()
        }
      })
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
    if (url === '/api/chat/thread/thread-b/history') {
      return Response.json({
        history: [{ id: 'thread-b-history', type: 'human', content: 'B history', created_at: '2026-07-14T00:00:00Z' }]
      })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.threads = [
    { id: 'thread-a', title: 'Thread A' },
    { id: 'thread-b', title: 'Thread B' }
  ]
  chat.currentThreadId = 'thread-a'
  chat.ensureRuntime('thread-a')

  const sending = chat.send({ text: 'Question from A', files: [], imageFile: null }, 'token-1')
  await streamReady
  await chat.selectThread('thread-b', 'token-1')

  streamController.enqueue(
    new TextEncoder().encode(
      sseBlock('chunk', { chunk: { status: 'running', stream_event: { type: 'message_delta', content: 'Answer from A' } } }) +
        sseBlock('end', { run_id: 'run-thread-a' })
    )
  )
  streamController.close()
  await sending

  assert.equal(chat.currentThreadId, 'thread-b')
  assert.equal(chat.messages.length, 1)
  assert.equal(chat.messages[0].content, 'B history')
  assert.equal(chat.threadRuntimes['thread-a'].messages.length, 2)
  assert.equal(chat.threadRuntimes['thread-a'].messages[1].content.includes('Answer from A'), true)
})
