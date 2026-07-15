import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')

test('terminal run replaces its optimistic turn with server history ids', async () => {
  setActivePinia(createPinia())
  let requestId = ''
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') {
      requestId = JSON.parse(options.body).meta.request_id
      return Response.json({ id: 'run-1' })
    }
    if (url.startsWith('/api/agent/runs/run-1/events')) {
      return new Response('event: end\ndata: {"payload":{"status":"completed"}}\nid: 2-0\n\n')
    }
    if (url === '/api/chat/thread/thread-1/history') {
      return Response.json({
        history: [
          { id: 'server-user', type: 'human', content: 'question', extra_metadata: { request_id: requestId } },
          {
            id: 'server-assistant',
            type: 'ai',
            content: 'official answer',
            extra_metadata: { request_id: requestId, response_metadata: { model_name: 'Qwen' } }
          }
        ]
      })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-1'
  chat.ensureRuntime('thread-1')

  const result = await chat.send({ text: 'question', files: [], imageFile: null }, 'token-1')

  assert.equal(result?.messageId, 'server-user')
  assert.deepEqual(chat.messages.map((message) => message.id), ['server-user', 'server-assistant'])
  assert.equal(chat.messages[1].content, 'official answer')
  assert.equal(chat.messages[1].modelName, 'Qwen')
})

test('terminal run keeps a complete streamed answer until delayed history catches up', async () => {
  setActivePinia(createPinia())
  let requestId = ''
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') {
      requestId = JSON.parse(options.body).meta.request_id
      return Response.json({ id: 'run-2' })
    }
    if (url.startsWith('/api/agent/runs/run-2/events')) {
      return new Response(
        'event: messages\ndata: {"payload":{"items":[{"status":"running","stream_event":{"type":"message_delta","content":"complete streamed answer"}}]}}\n\nevent: end\ndata: {"payload":{"status":"completed"}}\n\n'
      )
    }
    if (url === '/api/chat/thread/thread-2/history') {
      return Response.json({
        history: [
          { id: 'server-user', type: 'human', content: 'question', extra_metadata: { request_id: requestId } },
          { id: 'server-assistant', type: 'ai', content: 'partial', extra_metadata: { request_id: requestId } }
        ]
      })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-2'
  chat.modelOptions = [{ value: 'model-qwen', label: 'Qwen3.6' }]
  chat.selectedModelSpec = 'model-qwen'
  chat.ensureRuntime('thread-2')

  await chat.send({ text: 'question' }, 'token-1')

  assert.equal(chat.messages[1].content, 'complete streamed answer')
  assert.equal(chat.messages[1].modelName, 'Qwen3.6')
})
