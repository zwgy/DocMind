import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getToolCallLabel,
  groupKbChunksByFile,
  listKbsItems,
  normalizeToolCalls,
  parseQueryKbResult,
  resolveKbDisplayName
} from '../src/utils/tool-calls.ts'

test('normalizeToolCalls keeps supported historical and streaming shapes', () => {
  const calls = normalizeToolCalls([
    {
      id: 'a',
      function: { name: 'query_kb', arguments: '{"query_text":"业绩"}' },
      tool_call_result: { content: '{"results":[{"content":"片段","metadata":{"source":"a.pdf"}}]}' }
    },
    { id: 'b', name: 'present_artifacts' },
    { id: 'c', name: 'read_file', args: { file_path: '/tmp/SKILL.md' }, status: 'running' }
  ])

  assert.equal(calls.length, 2)
  assert.equal(calls[0].name, 'query_kb')
  assert.equal(calls[0].status, 'done')
  assert.equal(calls[1].status, 'running')
})

test('parseQueryKbResult accepts results, chunks and graph fields', () => {
  const result = parseQueryKbResult({
    results: [{ content: 'chunk', metadata: { source: 'report.pdf' }, score: 0.58 }],
    entities: [{ name: 'GlobalFinance' }],
    relationships: [{ src_id: 'A', tgt_id: 'B' }],
    references: [{ url: 'https://example.test' }]
  })

  assert.equal(result.chunks.length, 1)
  assert.equal(result.entities.length, 1)
  assert.equal(result.relationships.length, 1)
  assert.equal(result.references.length, 1)
})

test('groupKbChunksByFile groups chunks by source filename', () => {
  const groups = groupKbChunksByFile([
    { content: 'one', metadata: { source: 'a.pdf' }, score: 0.58 },
    { content: 'two', metadata: { file_name: 'a.pdf' }, score: 0.39 },
    { content: 'three', filename: 'b.pdf' }
  ])

  assert.equal(groups.length, 2)
  assert.equal(groups[0].filename, 'a.pdf')
  assert.equal(groups[0].chunks.length, 2)
  assert.equal(groups[1].filename, 'b.pdf')
})

test('listKbsItems unwraps tool result content and labels skill files', () => {
  const kbs = listKbsItems({
    id: 'list',
    name: 'list_kbs',
    result: { content: '[{"kb_id":"1","name":"test1","description":"desc"}]' }
  })

  assert.equal(kbs.length, 1)
  assert.equal(kbs[0].name, 'test1')
  assert.equal(
    getToolCallLabel({ id: 'read', name: 'read_file', args: { file_path: '/agents/knowledge-base/SKILL.md' } }),
    'Skill'
  )
})

test('resolveKbDisplayName prefers list_kbs names over raw kb ids', () => {
  const tools = [
    {
      id: 'list',
      name: 'list_kbs',
      result: { content: '[{"kb_id":"kb_r7gbu3094n","name":"test1"}]' }
    },
    {
      id: 'query',
      name: 'query_kb',
      args: { kb_id: 'kb_r7gbu3094n', query_text: 'GlobalFinance Corp 2024 业绩' }
    }
  ]

  assert.equal(resolveKbDisplayName(tools[1], tools), 'test1')
  assert.equal(resolveKbDisplayName(tools[1], [tools[1]]), 'kb_r7gbu3094n')
})
