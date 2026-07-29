import assert from 'node:assert/strict'
import test from 'node:test'

import {
  executionProcessShouldExpand,
  groupMessageDisplayItems
} from '../src/utils/message-display.ts'

test('completed tool turn groups every intermediate segment and keeps the final answer separate', () => {
  const items = groupMessageDisplayItems([
    { id: 'user', role: 'user', content: '分析数据' },
    { id: 'stage-1', role: 'assistant', content: '先读取来文。' },
    {
      id: 'tool-1',
      role: 'assistant',
      content: '',
      toolCalls: [{ id: 'a', name: 'read_file', status: 'done' }]
    },
    { id: 'stage-2', role: 'assistant', content: '继续生成图表。' },
    {
      id: 'tool-2',
      role: 'assistant',
      content: '',
      toolCalls: [{ id: 'b', name: 'render_data_chart', status: 'done' }]
    },
    { id: 'answer', role: 'assistant', content: '最终分析结论。', status: 'done' }
  ])

  assert.deepEqual(items.map((item) => item.type), [
    'message',
    'execution-process',
    'message'
  ])
  assert.deepEqual(
    items[1].messages.map((message) => message.id),
    ['stage-1', 'tool-1', 'stage-2', 'tool-2']
  )
  assert.equal(items[1].hasFinalAnswer, true)
  assert.equal(items[2].message.id, 'answer')
})

test('active tool turn keeps all model text inside the execution process', () => {
  const items = groupMessageDisplayItems(
    [
      { id: 'user', role: 'user', content: '分析数据' },
      { id: 'stage', role: 'assistant', content: '正在检查数据。', status: 'streaming' },
      {
        id: 'tool',
        role: 'assistant',
        content: '',
        status: 'streaming',
        toolCalls: [{ id: 'a', name: 'query_kb', status: 'running' }]
      },
      { id: 'latest', role: 'assistant', content: '正在整理。', status: 'streaming' }
    ],
    { streaming: true }
  )

  assert.deepEqual(items.map((item) => item.type), ['message', 'execution-process'])
  assert.deepEqual(
    items[1].messages.map((message) => message.id),
    ['stage', 'tool', 'latest']
  )
  assert.equal(items[1].isActive, true)
  assert.equal(items[1].hasFinalAnswer, false)
})

test('failed tool turn does not promote partial model text to a final answer', () => {
  const items = groupMessageDisplayItems([
    { id: 'user', role: 'user', content: '分析数据' },
    { id: 'stage', role: 'assistant', content: '正在生成图表。', status: 'done' },
    {
      id: 'tool',
      role: 'assistant',
      content: '',
      status: 'error',
      toolCalls: [{ id: 'a', name: 'render_data_chart', status: 'error' }]
    }
  ])

  assert.deepEqual(items.map((item) => item.type), ['message', 'execution-process'])
  assert.equal(items[1].hasFinalAnswer, false)
})

test('recovered final answer stays visible while the failed execution process remains separate', () => {
  const items = groupMessageDisplayItems([
    { id: 'user', role: 'user', content: '分析数据' },
    {
      id: 'tool',
      role: 'assistant',
      content: '',
      status: 'done',
      toolCalls: [{ id: 'a', name: 'render_data_chart', status: 'error' }]
    },
    {
      id: 'answer',
      role: 'assistant',
      content: '图表生成失败，以下是文字分析。',
      status: 'done'
    }
  ])

  assert.deepEqual(items.map((item) => item.type), [
    'message',
    'execution-process',
    'message'
  ])
  assert.equal(items[1].hasFinalAnswer, true)
  assert.equal(items[2].message.id, 'answer')
})

test('tool-only completed turn stays expanded when no final answer exists', () => {
  const items = groupMessageDisplayItems([
    { id: 'user', role: 'user', content: '读取文件' },
    {
      id: 'tool',
      role: 'assistant',
      content: '',
      status: 'done',
      toolCalls: [{ id: 'a', name: 'read_file', status: 'done' }]
    }
  ])

  assert.deepEqual(items.map((item) => item.type), ['message', 'execution-process'])
  assert.equal(items[1].hasFinalAnswer, false)
})

test('model preamble carrying a tool call is not treated as a final answer', () => {
  const items = groupMessageDisplayItems([
    { id: 'user', role: 'user', content: '生成图表' },
    {
      id: 'tool-preamble',
      role: 'assistant',
      content: '我先生成图表。',
      status: 'done',
      toolCalls: [{ id: 'a', name: 'render_data_chart', status: 'done' }]
    }
  ])

  assert.deepEqual(items.map((item) => item.type), ['message', 'execution-process'])
  assert.equal(items[1].hasFinalAnswer, false)
  assert.equal(items[1].messages[0].id, 'tool-preamble')
})

test('direct answer without execution steps keeps the original message display', () => {
  const items = groupMessageDisplayItems([
    { id: 'user', role: 'user', content: '你好' },
    { id: 'answer', role: 'assistant', content: '你好，有什么可以帮你？', status: 'done' }
  ])

  assert.deepEqual(items.map((item) => item.type), ['message', 'message'])
  assert.equal(items[1].message.id, 'answer')
})

test('user messages keep execution processes inside their own turn', () => {
  const items = groupMessageDisplayItems([
    { id: 'user-1', role: 'user', content: '第一问' },
    {
      id: 'tool-1',
      role: 'assistant',
      content: '',
      toolCalls: [{ id: 'a', name: 'read_file', status: 'done' }]
    },
    { id: 'answer-1', role: 'assistant', content: '第一答', status: 'done' },
    { id: 'user-2', role: 'user', content: '第二问' },
    {
      id: 'tool-2',
      role: 'assistant',
      content: '',
      toolCalls: [{ id: 'b', name: 'query_kb', status: 'running' }]
    }
  ])

  assert.deepEqual(items.map((item) => item.type), [
    'message',
    'execution-process',
    'message',
    'message',
    'execution-process'
  ])
  assert.equal(items[1].messages[0].id, 'tool-1')
  assert.equal(items[4].messages[0].id, 'tool-2')
})

test('execution process expands for active, failed, or missing-final states only', () => {
  assert.equal(executionProcessShouldExpand(true, false, false), true)
  assert.equal(executionProcessShouldExpand(false, true, true), true)
  assert.equal(executionProcessShouldExpand(false, false, false), true)
  assert.equal(executionProcessShouldExpand(false, false, true), false)
})
