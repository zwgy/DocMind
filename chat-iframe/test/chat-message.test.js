import assert from 'node:assert/strict'
import test from 'node:test'

import { appendRunChunk, normalizeChatMessage } from '../src/utils/chat-message.ts'

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
