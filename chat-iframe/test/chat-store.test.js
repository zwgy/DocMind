import assert from 'node:assert/strict'
import test from 'node:test'

// store 依赖 Pinia；mock 掉持久化与远端副作用，验证 send() 在没有会话时
// 不会把乐观消息清掉。
const { setActivePinia, createPinia } = await import('pinia')

// 必须放在 import store 之前，否则 setup store 拿不到 pinia 实例。
setActivePinia(createPinia())

const { useChatStore } = await import('../src/stores/chat.ts')

function sseBlock(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

test('send() 在没有会话时保留乐观消息并产出 assistant 回复', async () => {
  const calls = []
  const events = [
    sseBlock('start', { run_id: 'run-1' }),
    sseBlock('chunk', {
      chunk: { status: 'running', stream_event: { type: 'message_delta', content: '你好' } }
    }),
    sseBlock('end', { run_id: 'run-1' })
  ]
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method, body: options.body })
    if (url === '/api/chat/thread' && options.method === 'POST') {
      return Response.json({ id: 'thread-new', title: '来文咨询' })
    }
    if (url.startsWith('/api/agent/runs/') && url.includes('/events')) {
      const stream = new ReadableStream({
        start(controller) {
          for (const block of events) controller.enqueue(new TextEncoder().encode(block))
          controller.close()
        }
      })
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
    if (url === '/api/agent/runs' && options.method === 'POST') {
      return Response.json({ id: 'run-1' })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  assert.equal(chat.currentThreadId, '')
  assert.equal(chat.messages.length, 0)

  const result = await chat.send(
    { text: '你好', files: [], imageFile: null },
    'token-1',
    'agent-iframe',
    'oa:contract:001'
  )

  // 关键断言：send 完成后，乐观消息必须留在主区。
  assert.equal(result?.threadId, 'thread-new')
  assert.equal(chat.currentThreadId, 'thread-new')
  assert.equal(chat.messages.length, 2, 'user + assistant 都不能丢')
  assert.equal(chat.messages[0].role, 'user')
  assert.equal(chat.messages[0].content, '你好')
  assert.equal(chat.messages[1].role, 'assistant')
  assert.equal(chat.messages[1].content.includes('你好'), true)
  assert.equal(chat.messages[1].status, 'done')
})

test('newConversation 仍会清空消息（用户主动开新会话）', async () => {
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/chat/thread' && options.method === 'POST') {
      return Response.json({ id: 'thread-fresh', title: '来文咨询' })
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-old'
  chat.messages = [
    {
      id: 'm1',
      role: 'user',
      content: '历史消息',
      status: 'done',
      toolEvents: [],
      createdAt: '2024-01-01'
    }
  ]

  await chat.newConversation('token-1', 'agent-iframe', 'oa:contract:001')

  assert.equal(chat.currentThreadId, 'thread-fresh')
  assert.equal(chat.messages.length, 0, '主动开新会话要清空历史')
})
