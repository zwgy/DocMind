import assert from 'node:assert/strict'
import test from 'node:test'

import { appendRunChunk, appendRunChunkSegment, normalizeChatMessage } from '../src/utils/chat-message.ts'

test('appendRunChunk merges text, reasoning and tool calls into assistant message', () => {
  const message = normalizeChatMessage({ id: 'm1', type: 'ai', content: '' })

  appendRunChunk(message, { type: 'text', content: '正文', reasoningContent: '推理' })
  appendRunChunk(message, {
    type: 'tool_call',
    toolCallId: 'tool-1',
    name: 'search_docs',
    args: { q: '来文' }
  })
  appendRunChunk(message, { type: 'tool_result', toolCallId: 'tool-1', content: '命中结果' })

  assert.equal(message.content, '正文')
  assert.equal(message.reasoningContent, '推理')
  assert.deepEqual(message.toolCalls, [
    {
      id: 'tool-1',
      name: 'search_docs',
      args: { q: '来文' },
      result: '命中结果',
      status: 'done'
    }
  ])
})

test('appendRunChunk ignores unmatched tool results instead of creating anonymous tools', () => {
  const message = normalizeChatMessage({ id: 'm1', type: 'ai', content: '' })

  appendRunChunk(message, { type: 'tool_result', toolCallId: 'missing', content: 'late result' })

  assert.deepEqual(message.toolCalls, [])
})

test('appendRunChunkSegment keeps streaming text and tool calls in event order', () => {
  const messages = [normalizeChatMessage({ id: 'a1', type: 'ai', content: '' })]
  let current = messages[0]
  let nextId = 2
  const createSegment = () => normalizeChatMessage({ id: `a${nextId++}`, type: 'ai', content: '' })

  current = appendRunChunkSegment(messages, current, { type: 'text', content: '我先看看。' }, createSegment)
  current = appendRunChunkSegment(
    messages,
    current,
    { type: 'tool_call', toolCallId: 'tool-1', name: 'list_kbs', args: {} },
    createSegment
  )
  appendRunChunkSegment(messages, current, { type: 'tool_result', toolCallId: 'tool-1', content: 'test1' }, createSegment)
  appendRunChunkSegment(messages, current, { type: 'text', content: '目前有 1 个知识库。' }, createSegment)

  assert.deepEqual(
    messages.map((message) => ({
      id: message.id,
      content: message.content,
      tools: message.toolCalls?.map((tool) => `${tool.name}:${tool.status}`)
    })),
    [
      { id: 'a1', content: '我先看看。', tools: [] },
      { id: 'a2', content: '', tools: ['list_kbs:done'] },
      { id: 'a3', content: '目前有 1 个知识库。', tools: [] }
    ]
  )
})

test('normalizeChatMessage keeps image, attachments, model and error metadata', () => {
  const message = normalizeChatMessage({
    id: 'h1',
    type: 'human',
    content: '看图',
    image_content: 'base64',
    response_metadata: { model_name: 'qwen' },
    extra_metadata: {
      attachments: [{ file_name: 'demo.pdf' }],
      error_type: 'interrupted',
      error_message: '用户停止'
    }
  })

  assert.equal(message.role, 'user')
  assert.equal(message.imageContent, 'base64')
  assert.deepEqual(message.attachments, [{ file_name: 'demo.pdf' }])
  assert.equal(message.modelName, 'qwen')
  assert.equal(message.errorType, 'interrupted')
  assert.equal(message.errorMessage, '用户停止')
})
