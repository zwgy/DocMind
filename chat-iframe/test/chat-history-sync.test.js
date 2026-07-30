import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')

test('selectThread restores persisted token usage with message history', async () => {
  setActivePinia(createPinia())
  globalThis.fetch = async (url) => {
    if (url === '/api/chat/thread/thread-history/history') {
      return Response.json({ history: [{ id: 'answer', type: 'ai', content: 'answer' }] })
    }
    if (url === '/api/chat/thread/thread-history/state') {
      return Response.json({ agent_state: { token_usage: { prompt_tokens: 1200, prompt_budget: 102400 } } })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  await chat.selectThread('thread-history', 'token-1')

  assert.equal(chat.messages[0].content, 'answer')
  assert.deepEqual(chat.agentState?.token_usage, { prompt_tokens: 1200, prompt_budget: 102400 })
})

test('selectThread hides internal ask_user_question resume messages', async () => {
  setActivePinia(createPinia())
  globalThis.fetch = async (url) => {
    if (url === '/api/chat/thread/thread-resume/history') {
      return Response.json({
        history: [
          { id: 'question', type: 'human', content: '请选择输出格式' },
          {
            id: 'resume',
            type: 'human',
            content: '{"format":"excel"}',
            message_type: 'resume'
          },
          {
            id: 'legacy-resume',
            type: 'human',
            content: 'reject',
            extra_metadata: { source: 'ask_user_question_resume' }
          },
          { id: 'answer', type: 'ai', content: '已选择 Excel' }
        ]
      })
    }
    if (url === '/api/chat/thread/thread-resume/state') return Response.json({})
    return Response.json({})
  }

  const chat = useChatStore()
  await chat.selectThread('thread-resume', 'token-1')

  assert.deepEqual(
    chat.messages.map((message) => message.id),
    ['question', 'answer']
  )
})

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
            extra_metadata: { request_id: requestId }
          }
        ]
      })
    }
    if (url === '/api/chat/thread/thread-1/state') {
      return Response.json({ agent_state: { token_usage: { prompt_tokens: 1200, prompt_budget: 102400 } } })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-1'
  chat.modelOptions = [{ value: 'model-qwen', label: 'Qwen3.6' }]
  chat.selectedModelSpec = 'model-qwen'
  chat.ensureRuntime('thread-1')

  const result = await chat.send({ text: 'question', files: [], imageFile: null }, 'token-1')

  assert.equal(result?.messageId, 'server-user')
  assert.deepEqual(chat.messages.map((message) => message.id), ['server-user', 'server-assistant'])
  assert.equal(chat.messages[1].content, 'official answer')
  assert.equal(chat.messages[1].modelName, 'Qwen3.6')
  assert.deepEqual(chat.agentState?.token_usage, { prompt_tokens: 1200, prompt_budget: 102400 })
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

test('terminal run attaches artifacts from final state before delayed history catches up', async () => {
  setActivePinia(createPinia())
  let requestId = ''
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') {
      requestId = JSON.parse(options.body).meta.request_id
      return Response.json({ id: 'run-artifact' })
    }
    if (url.startsWith('/api/agent/runs/run-artifact/events')) {
      return new Response(
        'event: messages\ndata: {"payload":{"items":[{"status":"running","stream_event":{"type":"message_delta","content":"artifact answer"}}]}}\n\nevent: end\ndata: {"payload":{"status":"completed"}}\n\n'
      )
    }
    if (url === '/api/chat/thread/thread-artifact/history') {
      return Response.json({
        history: [
          {
            id: 'server-user',
            type: 'human',
            content: 'question',
            extra_metadata: { request_id: requestId }
          },
          {
            id: 'server-assistant',
            type: 'ai',
            content: 'artifact answer',
            extra_metadata: { request_id: requestId }
          }
        ]
      })
    }
    if (url === '/api/chat/thread/thread-artifact/state') {
      return Response.json({
        agent_state: {
          artifacts: ['/home/gem/user-data/outputs/mindmap.svg']
        }
      })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-artifact'
  chat.ensureRuntime('thread-artifact')

  await chat.send({ text: 'question' }, 'token-1')

  assert.deepEqual(chat.messages[1].artifacts, [
    { path: '/home/gem/user-data/outputs/mindmap.svg', name: 'mindmap.svg' }
  ])
})
