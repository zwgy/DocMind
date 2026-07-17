import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildIframeContext,
  buildChatQuery,
  cancelRun,
  createConversation,
  deleteConversation,
  listConversations,
  readRunEventStream,
  sendMessageStream,
  submitMessageFeedback,
  updateConversation,
  uploadImage
} from '../src/apis/chat.ts'
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

  await listConversations('token-1', 'agent-iframe', 'oa:contract:001')

  assert.equal(calls[0].url, '/api/chat/threads?limit=50&offset=0&agent_id=agent-iframe&conversation_scope_key=oa%3Acontract%3A001')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
})

test('createConversation stores iframe conversation scope in metadata', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ id: 'thread-1', title: 'scope thread' })
  }

  await createConversation({ token: 'token-1', agentId: 'agent-iframe', conversationScopeKey: 'oa:contract:001' })

  assert.deepEqual(JSON.parse(calls[0].options.body).metadata, {
    source: 'chat-iframe',
    conversation_scope_key: 'oa:contract:001'
  })
})

test.skip('buildChatQuery carries enabled page and file context in the query text', () => {
  const query = buildChatQuery({
    text: '这份文件有什么风险？',
    includePage: true,
    includeFile: true,
    pageContent: {
      title: '收文详情',
      url: 'https://oa.example.test/doc/1',
      text: '页面正文'
    },
    selectedFile: { name: '来文.docx', source_file_id: 'S001' },
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

test.skip('buildChatQuery omits file context when askFile is disabled', () => {
  const query = buildChatQuery({
    text: '只看页面',
    includePage: false,
    includeFile: false,
    selectedFile: { name: '来文.docx', source_file_id: 'S001' },
    extractionResult: {
      matchStatus: 'matched',
      extractionStatus: 'ready',
      categories: { risk: { matched: true, evidence: '风险依据' } },
      items: [{ item_id: 'i1', item_type: 'risk', source_quote: '现场作业监护需加强' }]
    }
  })

  assert.match(query, /用户问题：只看页面/)
  assert.doesNotMatch(query, /文件上下文/)
  assert.doesNotMatch(query, /现场作业监护需加强/)
})

test.skip('buildChatQuery keeps file identity but omits unavailable extraction details', () => {
  const query = buildChatQuery({
    text: '这份文件有摘要吗？',
    includePage: false,
    includeFile: true,
    selectedFile: { name: '来文.docx', source_file_id: 'S001' },
    extractionResult: {
      matchStatus: 'not_found',
      extractionStatus: 'not_found',
      reason: 'not found',
      categories: { risk: { matched: true, evidence: '不应出现' } },
      items: [{ item_id: 'i1', item_type: 'risk', source_quote: '不应注入' }]
    }
  })

  assert.match(query, /附件：来文.docx/)
  assert.doesNotMatch(query, /不应出现/)
  assert.doesNotMatch(query, /不应注入/)
})

test('buildChatQuery keeps the user query clean', () => {
  const query = buildChatQuery({
    text: 'Summarize this contract',
    includePage: true,
    includeFile: true,
    pageContent: { title: 'Detail page', text: 'Page body' },
    selectedFile: { name: 'contract.docx', source_file_id: 'S001' },
    extractionResult: {
      matchStatus: 'matched',
      extractionStatus: 'ready',
      categories: { risk: { matched: true, evidence: 'Risk evidence' } },
      items: [{ item_id: 'i1', item_type: 'risk', source_quote: 'Source quote' }]
    }
  })

  assert.equal(query, 'Summarize this contract')
})

test('buildIframeContext carries enabled page and all selected files', () => {
  const context = buildIframeContext({
    text: 'Summarize',
    includePage: true,
    includeFile: true,
    pageContent: { title: 'Detail page', url: 'https://oa.example.test/doc/1', text: 'Page body' },
    selectedPageFiles: [
      { name: 'a.docx', source_file_id: 'S001', source_url: 'https://oa.example.test/a.docx' },
      { name: 'b.pdf', source_file_id: 'S002', source_url: 'https://oa.example.test/b.pdf' }
    ],
    extractionResults: {
      S001: {
        incomingFileId: 'S001',
        matchStatus: 'matched',
        extractionStatus: 'ready',
        fileStatus: 'parsed',
        hasParsedMarkdown: true,
        kbId: 'kb1',
        fileId: 'file1',
        categories: { risk: { matched: true, evidence: 'Risk evidence' } },
        items: [{ item_id: 'i1', item_type: 'risk', source_quote: 'Source quote' }]
      },
      S002: {
        incomingFileId: 'S002',
        matchStatus: 'pending_sync',
        extractionStatus: 'not_found'
      }
    }
  })

  assert.equal(context.page.title, 'Detail page')
  assert.equal(context.files.length, 2)
  assert.equal(context.files[0].kbId, 'kb1')
  assert.equal(context.files[0].summary.includes('risk'), true)
  assert.equal(context.files[1].matchStatus, 'pending_sync')
})

test('buildIframeContext omits disabled page and files', () => {
  const context = buildIframeContext({
    text: 'Summarize',
    includePage: false,
    includeFile: false,
    pageContent: { title: 'Detail page', text: 'Page body' },
    selectedPageFiles: [{ name: 'a.docx', source_file_id: 'S001' }]
  })

  assert.equal(context.page, undefined)
  assert.deepEqual(context.files, [])
})

test('buildIframeContext uses backend summary when extraction items are empty', () => {
  const context = buildIframeContext({
    text: 'Summarize',
    includePage: false,
    includeFile: true,
    selectedPageFiles: [{ name: 'a.docx', source_file_id: 'S001' }],
    extractionResults: {
      S001: {
        matchStatus: 'matched',
        extractionStatus: 'ready',
        summary: 'Full document summary',
        items: []
      }
    }
  })

  assert.equal(context.files[0].summary, 'Full document summary')
})

test('buildIframeContext keeps structured extraction details with backend summary', () => {
  const context = buildIframeContext({
    text: 'Summarize',
    includePage: false,
    includeFile: true,
    selectedPageFiles: [{ name: 'a.docx', source_file_id: 'S001' }],
    extractionResults: {
      S001: {
        matchStatus: 'matched',
        extractionStatus: 'ready',
        summary: 'Full document summary',
        categories: { risk: { matched: true, evidence: 'Risk evidence' } },
        items: [{ item_id: 'i1', item_type: 'risk', source_quote: 'Source quote' }]
      }
    }
  })

  assert.match(context.files[0].summary, /Full document summary/)
  assert.match(context.files[0].summary, /Source quote/)
  assert.deepEqual(context.files[0].items, [{ item_id: 'i1', item_type: 'risk', source_quote: 'Source quote' }])
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

test('readRunEventStream emits structured reasoning and tool call chunks', async () => {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          [
            'event: messages',
            'data: {"payload":{"items":[{"status":"loading","stream_event":{"type":"message_delta","message_id":"m1","content":"回答","reasoning_content":"推理"}},{"status":"loading","stream_event":{"type":"tool_call","message_id":"m1","tool_call_id":"tool-1","name":"search_docs","args":{"q":"来文"}}},{"status":"stream_event","event":{"method":"tools","data":{"event":"tool-finished","tool_call_id":"tool-1","output":{"content":"命中"}}}}]}}',
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
  const chunks = []

  await readRunEventStream(new Response(stream), {
    onChunk: (chunk) => chunks.push(chunk)
  })

  assert.deepEqual(chunks, [
    { type: 'text', messageId: 'm1', content: '回答', reasoningContent: '推理' },
    { type: 'tool_call', messageId: 'm1', toolCallId: 'tool-1', name: 'search_docs', args: { q: '来文' } },
    { type: 'tool_result', toolCallId: 'tool-1', content: '命中' },
    { type: 'done' }
  ])
})

test('readRunEventStream matches tool results by tool_call_id before output id', async () => {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          [
            'event: messages',
            'data: {"payload":{"items":[{"status":"loading","stream_event":{"type":"tool_call","message_id":"m1","tool_call_id":"call-1","name":"search_file","args":{"kb_id":"kb1"}}},{"status":"stream_event","event":{"method":"tools","data":{"event":"tool-finished","tool_call_id":"call-1","output":{"id":"tool-message-1","tool_call_id":"call-1","content":"done"}}}}]}}',
            '',
            ''
          ].join('\n')
        )
      )
      controller.close()
    }
  })
  const chunks = []

  await readRunEventStream(new Response(stream), {
    onChunk: (chunk) => chunks.push(chunk)
  })

  assert.deepEqual(chunks, [
    { type: 'tool_call', messageId: 'm1', toolCallId: 'call-1', name: 'search_file', args: { kb_id: 'kb1' } },
    { type: 'tool_result', toolCallId: 'call-1', content: 'done' }
  ])
})

test('sendMessageStream posts image content and attachment metadata with attachment IDs', async () => {
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    if (url === '/api/agent/runs') return Response.json({ run_id: 'run-1' })
    return new Response('event: end\ndata: {"payload":{"status":"completed"}}\n\n')
  }

  await sendMessageStream({
    text: '识别图片',
    threadId: 'thread-1',
    token: 'token-1',
    includePage: false,
    includeFile: false,
    imageContent: 'base64-image',
    attachments: [{ file_id: 'file-1', file_name: 'demo.pdf' }]
  })

  const body = JSON.parse(calls[0].options.body)
  assert.equal(body.image_content, 'base64-image')
  assert.deepEqual(body.meta.attachments, [{ file_id: 'file-1', file_name: 'demo.pdf' }])
  assert.deepEqual(body.meta.attachment_file_ids, ['file-1'])
})

test('sendMessageStream posts iframe context separately from the query', async () => {
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    if (url === '/api/agent/runs') return Response.json({ run_id: 'run-1' })
    return new Response('event: end\ndata: {"payload":{"status":"completed"}}\n\n')
  }

  await sendMessageStream({
    text: 'Summarize',
    threadId: 'thread-1',
    includePage: true,
    includeFile: true,
    pageContent: { title: 'Page', text: 'Page body' },
    selectedPageFiles: [{ name: 'a.docx', source_file_id: 'S001' }],
    extractionResults: {
      S001: {
        matchStatus: 'matched',
        extractionStatus: 'not_found',
        hasParsedMarkdown: true,
        kbId: 'kb1',
        fileId: 'file1'
      }
    }
  })

  const body = JSON.parse(calls[0].options.body)
  assert.equal(body.query, 'Summarize')
  assert.equal(body.meta.iframe_context.page.title, 'Page')
  assert.equal(body.meta.iframe_context.files[0].fileId, 'file1')
})

test('sendMessageStream keeps historical conversations usable without crypto.randomUUID', async () => {
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    if (url === '/api/agent/runs') return Response.json({ run_id: 'run-1' })
    return new Response('event: end\ndata: {"payload":{"status":"completed"}}\n\n')
  }
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {} })

  try {
    await sendMessageStream({ text: 'continue', threadId: 'thread-1', includePage: false, includeFile: false })
  } finally {
    if (cryptoDescriptor) Object.defineProperty(globalThis, 'crypto', cryptoDescriptor)
    else delete globalThis.crypto
  }

  const body = JSON.parse(calls[0].options.body)
  assert.match(body.meta.request_id, /^client-\d+-[a-z0-9]+$/)
})

test('buildIframeContext keeps all selected files without changing the query', () => {
  const input = {
    text: '只看附件风险',
    includeFile: true,
    selectedPageFiles: [
      { name: '合同.docx', source_file_id: 'S001' },
      { name: '报价.pdf', source_file_id: 'S002' }
    ],
    extractionResults: {
      S001: {
        matchStatus: 'matched',
        extractionStatus: 'ready',
        fileStatus: 'parsed',
        hasParsedMarkdown: true,
        kbId: 'kb1',
        fileId: 'file1',
        categories: { risk: { matched: true, evidence: '超期' } },
        schemaIds: ['risk_item'],
        items: [{ source_quote: '付款超期' }]
      },
      S002: {
        matchStatus: 'not_found',
        extractionStatus: 'not_found'
      }
    }
  }

  const context = buildIframeContext(input)

  assert.equal(buildChatQuery(input), '只看附件风险')
  assert.equal(context.files.length, 2)
  assert.equal(context.files[0].fileId, 'file1')
  assert.match(context.files[0].summary, /付款超期/)
  assert.deepEqual(context.files[0].categories, { risk: { matched: true, evidence: '超期' } })
  assert.deepEqual(context.files[0].schemaIds, ['risk_item'])
  assert.deepEqual(context.files[0].items, [{ source_quote: '付款超期' }])
  assert.equal(context.files[1].name, '报价.pdf')
  assert.equal(context.files[1].summary, undefined)
})

test('chat management APIs use web-compatible endpoints', async () => {
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    return Response.json({ ok: true })
  }

  await updateConversation('thread-1', { title: '新标题', isPinned: true }, 'token-1')
  await deleteConversation('thread-1', 'token-1')
  await cancelRun('run-1', 'token-1')
  await submitMessageFeedback('message-1', 'like', null, 'token-1')

  assert.deepEqual(
    calls.map((call) => [call.url, call.options.method, call.options.headers.Authorization]),
    [
      ['/api/chat/thread/thread-1', 'PUT', 'Bearer token-1'],
      ['/api/chat/thread/thread-1', 'DELETE', 'Bearer token-1'],
      ['/api/agent/runs/run-1/cancel', 'POST', 'Bearer token-1'],
      ['/api/chat/message/message-1/feedback', 'POST', 'Bearer token-1']
    ]
  )
  assert.deepEqual(JSON.parse(calls[0].options.body), { title: '新标题', is_pinned: true })
})

test('uploadImage posts image multipart payload', async () => {
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    return Response.json({ success: true, image_content: 'base64' })
  }

  const file = new File(['x'], 'demo.png', { type: 'image/png' })
  const result = await uploadImage(file, 'token-1')

  assert.equal(result.image_content, 'base64')
  assert.equal(calls[0].url, '/api/chat/image/upload')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.ok(calls[0].options.body instanceof FormData)
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
