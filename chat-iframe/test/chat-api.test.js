import assert from 'node:assert/strict'
import test from 'node:test'

import { buildChatQuery, createConversation, listConversations, readRunEventStream } from '../src/apis/chat.ts'
import { listChatModels } from '../src/apis/models.ts'

test('createConversation posts the default agent thread with bearer token', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ id: 'thread-1', title: '来文咨询' })
  }

  const thread = await createConversation({ token: 'token-1' })

  assert.equal(thread.id, 'thread-1')
  assert.equal(calls[0].url, '/api/chat/thread')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    agent_id: 'default-chatbot',
    title: '来文咨询',
    metadata: { source: 'chat-iframe' }
  })
})

test('listConversations filters by configured agent id', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json([])
  }

  await listConversations('token-1', 'agent-iframe')

  assert.equal(calls[0].url, '/api/chat/threads?limit=50&offset=0&agent_id=agent-iframe')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
})

test('buildChatQuery carries enabled page and file context in the query text', () => {
  const query = buildChatQuery({
    text: '这份文件有什么风险？',
    includePage: true,
    includeFile: true,
    pageContent: {
      title: '收文详情',
      url: 'https://oa.example.test/doc/1',
      text: '页面正文'
    },
    selectedFile: { id: 'f1', name: '来文.docx', sourceKey: 'S001' },
    extractionResult: {
      matchStatus: 'matched',
      extractionStatus: 'ready',
      categories: { risk: { matched: true, evidence: '风险依据' } },
      items: [{ item_id: 'i1', item_type: 'risk', source_quote: '现场作业监护需加强' }]
    }
  })

  assert.match(query, /用户问题：这份文件有什么风险？/)
  assert.match(query, /页面标题：收文详情/)
  assert.match(query, /附件：来文.docx/)
  assert.match(query, /risk：命中/)
  assert.match(query, /现场作业监护需加强/)
})

test('readRunEventStream emits text deltas from compact run SSE events', async () => {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          [
            'event: stream_event',
            'data: {"status":"stream_event","chunk":{"stream_event":{"type":"message_delta","content":"你好"}}}',
            '',
            'event: finished',
            'data: {"status":"finished"}',
            '',
            ''
          ].join('\n')
        )
      )
      controller.close()
    }
  })
  const response = new Response(stream)
  const deltas = []
  let finished = false

  await readRunEventStream(response, {
    onText: (text) => deltas.push(text),
    onDone: () => {
      finished = true
    }
  })

  assert.deepEqual(deltas, ['你好'])
  assert.equal(finished, true)
})

test('readRunEventStream emits text from backend compact payload items', async () => {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          [
            'event: messages',
            'data: {"payload":{"items":[{"status":"streaming","stream_event":{"type":"message_delta","content":"阶段八"}}]}}',
            '',
            'event: end',
            'data: {"payload":{"status":"completed"}}',
            '',
            ''
          ].join('\n')
        )
      )
      controller.close()
    }
  })
  const deltas = []

  await readRunEventStream(new Response(stream), {
    onText: (text) => deltas.push(text)
  })

  assert.deepEqual(deltas, ['阶段八'])
})

test('listChatModels adapts backend grouped model response', async () => {
  globalThis.fetch = async () =>
    Response.json({
      success: true,
      data: {
        local: {
          provider_id: 'local',
          provider_display_name: '本地模型',
          models: [{ spec: 'local:qwen', model_id: 'qwen', display_name: 'Qwen' }]
        }
      }
    })

  const models = await listChatModels('token-1')

  assert.deepEqual(models, [
    {
      value: 'local:qwen',
      label: 'Qwen',
      provider: '本地模型',
      model_id: 'qwen'
    }
  ])
})
