import assert from 'node:assert/strict'
import test from 'node:test'

import { groupMessageDisplayItems } from '../src/utils/message-display.ts'

test('groupMessageDisplayItems merges adjacent tool-only assistant messages', () => {
  const items = groupMessageDisplayItems([
    { id: 'tool-1', role: 'assistant', content: '', toolCalls: [{ id: 'a', name: 'read_file' }] },
    { id: 'tool-2', role: 'assistant', content: '', toolCalls: [{ id: 'b', name: 'list_kbs' }] },
    { id: 'answer', role: 'assistant', content: 'done', toolCalls: [] }
  ])

  assert.equal(items.length, 2)
  assert.equal(items[0].type, 'tool-group')
  assert.deepEqual(
    items[0].toolCalls.map((tool) => tool.name),
    ['read_file', 'list_kbs']
  )
  assert.equal(items[1].type, 'message')
  assert.equal(items[1].message.id, 'answer')
})

test('groupMessageDisplayItems keeps user messages between tool groups', () => {
  const items = groupMessageDisplayItems([
    { id: 'tool-1', role: 'assistant', content: '', toolCalls: [{ id: 'a', name: 'read_file' }] },
    { id: 'user-1', role: 'user', content: 'next' },
    { id: 'tool-2', role: 'assistant', content: '', toolCalls: [{ id: 'b', name: 'query_kb' }] }
  ])

  assert.equal(items.length, 3)
  assert.equal(items[0].type, 'tool-group')
  assert.equal(items[1].type, 'message')
  assert.equal(items[2].type, 'tool-group')
})

test('groupMessageDisplayItems splits visible assistant body from tool calls', () => {
  const items = groupMessageDisplayItems([
    {
      id: 'answer',
      role: 'assistant',
      content: '先查一下资料',
      toolCalls: [{ id: 'a', name: 'query_kb' }]
    },
    { id: 'tool-only', role: 'assistant', content: '', toolCalls: [{ id: 'b', name: 'read_file' }] }
  ])

  assert.equal(items.length, 2)
  assert.equal(items[0].type, 'message')
  assert.equal(items[0].message.id, 'answer')
  assert.equal(items[1].type, 'tool-group')
  assert.deepEqual(
    items[1].toolCalls.map((tool) => tool.name),
    ['query_kb', 'read_file']
  )
})
