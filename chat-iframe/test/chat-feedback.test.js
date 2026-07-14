import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { listMessages } = await import('../src/apis/chat.ts')
const { useChatStore } = await import('../src/stores/chat.ts')

test('history feedback is normalized and a second click does not submit again', async () => {
  let feedbackPosts = 0
  globalThis.fetch = async (url) => {
    if (url === '/api/chat/thread/thread-1/history') {
      return Response.json({
        history: [{ id: '42', type: 'ai', content: 'answer', feedback: { rating: 'dislike', reason: 'not specific' } }]
      })
    }
    if (url === '/api/chat/message/43/feedback') {
      feedbackPosts += 1
      return Response.json({ rating: 'like', reason: null })
    }
    return Response.json({})
  }

  const history = await listMessages('thread-1', 'token-1')
  assert.deepEqual(history[0].feedback, { rating: 'dislike', reason: 'not specific' })

  setActivePinia(createPinia())
  const chat = useChatStore()
  chat.ensureRuntime().messages = [{ id: '43', role: 'assistant', content: 'new answer', status: 'done' }]
  const first = chat.feedback({ messageId: '43', rating: 'like', reason: null }, 'token-1')
  const second = chat.feedback({ messageId: '43', rating: 'like', reason: null }, 'token-1')
  await Promise.all([first, second])

  assert.equal(feedbackPosts, 1)
  assert.deepEqual(chat.messages[0].feedback, { rating: 'like', reason: null })
})

test('a concurrent retry creates one optimistic user message', async () => {
  let runPosts = 0
  globalThis.fetch = async (url) => {
    if (url === '/api/agent/runs') {
      runPosts += 1
      return Response.json({ id: 'run-1' })
    }
    if (url.startsWith('/api/agent/runs/run-1/events')) {
      return new Response('event: end\ndata: {"payload":{"status":"completed"}}\n\n')
    }
    if (url === '/api/chat/thread/thread-1/history') return Response.json({ history: [] })
    return Response.json({})
  }

  setActivePinia(createPinia())
  const chat = useChatStore()
  chat.currentThreadId = 'thread-1'
  const runtime = chat.ensureRuntime('thread-1')
  runtime.lastUserMessageForRetry = { text: 'try again', files: [], imageFile: null }

  const first = chat.retry('token-1')
  const second = await chat.retry('token-1')
  await first

  assert.equal(second, null)
  assert.equal(runPosts, 1)
  assert.equal(chat.messages.filter((message) => message.role === 'user').length, 1)
})
