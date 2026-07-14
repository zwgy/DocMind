import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')
const { extractFinalAnswerSources } = await import('../src/utils/tool-calls.ts')
const { normalizeChatMessage } = await import('../src/utils/chat-message.ts')

test('loads thread pages without duplicating pinned threads or changing the current thread', async () => {
  setActivePinia(createPinia())
  const offsets = []
  const pinned = { id: 'pinned', title: 'Pinned', is_pinned: true }
  const page = (prefix) => Array.from({ length: 50 }, (_, index) => ({ id: `${prefix}-${index}`, title: `${prefix}-${index}` }))
  globalThis.fetch = async (url) => {
    const offset = Number(new URL(`https://test${url}`).searchParams.get('offset'))
    offsets.push(offset)
    if (offset === 0) return Response.json([pinned, ...page('first')])
    if (offset === 50) return Response.json([pinned, ...page('second')])
    return Response.json([pinned])
  }

  const chat = useChatStore()
  chat.currentThreadId = 'current-thread'
  await chat.refreshThreads('token-1', 'agent-1', 'oa:contract:001')
  await chat.loadMoreThreads('token-1', 'agent-1', 'oa:contract:001')
  await chat.loadMoreThreads('token-1', 'agent-1', 'oa:contract:001')

  assert.deepEqual(offsets, [0, 50, 100])
  assert.equal(chat.threads.length, 101)
  assert.equal(chat.threads.filter((thread) => thread.id === 'pinned').length, 1)
  assert.equal(chat.currentThreadId, 'current-thread')
  assert.equal(chat.hasMoreThreads, false)
})

test('final-answer sources include only the current turn and preserve model metadata', () => {
  const finalAnswer = normalizeChatMessage({
    id: 'final-answer',
    type: 'ai',
    content: '最终回答',
    extra_metadata: { response_metadata: { model_name: 'Qwen3.6' } }
  })
  const messages = [
    { id: 'old-user', role: 'user', content: '旧问题' },
    {
      id: 'old-tool',
      role: 'assistant',
      content: '',
      toolCalls: [{ id: 'old-kb', name: 'query_kb', result: { content: JSON.stringify({ results: [{ content: '旧片段' }] }) } }]
    },
    { id: 'current-user', role: 'user', content: '当前问题' },
    {
      id: 'current-tools',
      role: 'assistant',
      content: '',
      toolCalls: [
        { id: 'kb', name: 'query_kb', result: { content: JSON.stringify({ results: [{ content: '当前片段', file_id: 'file-1' }] }) } },
        {
          id: 'web',
          name: 'tavily_search',
          result: { content: JSON.stringify({ results: [{ title: '网页来源', url: 'https://example.com', content: '网页摘要' }] }) }
        }
      ]
    },
    finalAnswer
  ]

  const sources = extractFinalAnswerSources(messages, finalAnswer.id)
  assert.equal(finalAnswer.modelName, 'Qwen3.6')
  assert.deepEqual(sources.knowledgeChunks.map((chunk) => chunk.content), ['当前片段'])
  assert.deepEqual(sources.webSources.map((source) => source.url), ['https://example.com'])
})
