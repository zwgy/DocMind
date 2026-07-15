import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')

function completedRunEvents() {
  return new Response('event: end\ndata: {"payload":{"status":"completed"}}\n\n')
}

test('per-thread model selection is restored from history and used by the next run', async () => {
  setActivePinia(createPinia())
  let sentModelSpec = ''
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/chat/thread/thread-a/history') {
      return Response.json({
        history: [{ id: 'a-user', type: 'human', content: 'A', extra_metadata: { model_spec: 'model-a' } }]
      })
    }
    if (url === '/api/chat/thread/thread-b/history') {
      return Response.json({
        history: [{ id: 'b-user', type: 'human', content: 'B', extra_metadata: { model_spec: 'model-b' } }]
      })
    }
    if (url === '/api/agent/runs' && options.method === 'POST') {
      sentModelSpec = JSON.parse(options.body).model_spec
      return Response.json({ id: 'run-a' })
    }
    if (url.startsWith('/api/agent/runs/run-a/events')) return completedRunEvents()
    return Response.json({})
  }

  const chat = useChatStore()
  chat.selectedModelSpec = 'model-default'
  chat.threads = [
    { id: 'thread-a', title: 'A' },
    { id: 'thread-b', title: 'B' }
  ]

  await chat.selectThread('thread-a', 'token-1')
  assert.equal(chat.selectedModelSpec, 'model-a')
  chat.setSelectedModelSpec('model-a-custom')
  await chat.selectThread('thread-b', 'token-1')
  assert.equal(chat.selectedModelSpec, 'model-b')
  await chat.selectThread('thread-a', 'token-1')
  assert.equal(chat.selectedModelSpec, 'model-a-custom')

  await chat.send({ text: 'continue A' }, 'token-1')
  assert.equal(sentModelSpec, 'model-a-custom')
})

test('first successful reply generates a fast-model title without replacing a manual title', async () => {
  setActivePinia(createPinia())
  const titleCalls = []
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') return Response.json({ id: 'run-title' })
    if (url.startsWith('/api/agent/runs/run-title/events')) return completedRunEvents()
    if (url === '/api/chat/thread/thread-title/history') return Response.json({ history: [] })
    if (url === '/api/chat/call') {
      titleCalls.push(JSON.parse(options.body))
      return Response.json({ response: '自动标题' })
    }
    if (url === '/api/chat/thread/thread-title' && options.method === 'PUT') {
      return Response.json({ id: 'thread-title', title: '自动标题' })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-title'
  chat.threads = [{ id: 'thread-title', title: '来文咨询' }]
  chat.ensureRuntime('thread-title')

  await chat.send({ text: '请总结这份合同' }, 'token-1')
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.deepEqual(titleCalls, [
    {
      query: '根据以下对话内容生成一个简短的标题（最多20个字符，中英文均可），不要包含 markdown 标记：\n\n请总结这份合同',
      meta: { use_fast_model: true }
    }
  ])
  assert.equal(chat.threads[0].title, '自动标题')

  chat.manuallyRenamedThreads['thread-title'] = true
  chat.threads[0].title = '人工标题'
  await chat.autoGenerateTitle('thread-title', '不应覆盖', 'token-1')
  assert.equal(titleCalls.length, 1)
})

test('title falls back to the first question when the fast model is unavailable', async () => {
  setActivePinia(createPinia())
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/chat/call') return Response.json({ detail: 'unavailable' }, { status: 503 })
    if (url === '/api/chat/thread/thread-title' && options.method === 'PUT') {
      return Response.json({ id: 'thread-title', title: 'first question for title' })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.threads = [{ id: 'thread-title', title: '来文咨询' }]

  await chat.autoGenerateTitle('thread-title', 'first question for title', 'token-1')

  assert.equal(chat.threads[0].title, 'first question for title')
})

test('stale sidebar data does not reset an auto-generated title', async () => {
  setActivePinia(createPinia())
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/chat/call') return Response.json({ response: '自动标题' })
    if (url === '/api/chat/thread/thread-title' && options.method === 'PUT') {
      return Response.json({ id: 'thread-title', title: '来文咨询' })
    }
    if (String(url).startsWith('/api/chat/threads?')) {
      return Response.json([{ id: 'thread-title', title: '来文咨询' }])
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.threads = [{ id: 'thread-title', title: '来文咨询' }]

  await chat.autoGenerateTitle('thread-title', 'first question', 'token-1')
  await chat.refreshThreads('token-1')

  assert.equal(chat.threads[0].title, '自动标题')
})
